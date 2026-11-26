"""Only synthetic cue strings and generated waveforms; never reads user media."""

import subprocess
import sys
from types import ModuleType

import numpy as np
import pytest

from backend.services import editorial_audio
from backend.utils.ffmpeg_utils import get_ffmpeg_path


def _cue(number, text, start, end, **extra):
    return {'id': f'c{number}', 'text': text, 'start': start, 'end': end, **extra}


def test_short_independent_fillers_only():
    cues = [
        _cue(0, '呃', 0, .20),  # lack of context at beginning
        _cue(1, '这一轮先看原始记录。', .24, 1.2),
        _cue(2, '呃，', 1.27, 1.48),
        _cue(3, '把两个指标记下来。', 1.58, 3.0),
        _cue(4, '然后', 3.18, 3.38),
        _cue(5, '看看', 3.5, 4.0),  # lexical reduplication, not a stutter
        _cue(6, '嗯', 4.12, 4.38),  # may be a response, never auto-omit
        _cue(7, '这样的安排是否可行？', 4.5, 6.0),
        _cue(8, '呃', 6.12, 6.45),  # turn/response context
        _cue(9, '这里用另一种办法。', 6.6, 8.0),
        _cue(10, '额。', 8.08, 8.35, original_text='这就是额度。'),  # edited
        _cue(11, '计算两次的平均值。', 8.45, 9.5),
        _cue(12, '呃', 9.5, 9.73),  # contiguous with the preceding cue
        _cue(13, '这一段得先核实。', 9.8, 10.8),
        _cue(14, '呃', 11.0, 11.3),  # lack of context at end
    ]
    assert editorial_audio.optional_filler_ids(cues) == ['c2']


def test_speech_and_energy_are_independent_protection():
    # A quiet speech span is vetoed by VAD; a tone without VAD is rejected by energy.
    audio = np.zeros((16000, 2), dtype=np.float32)
    audio[4000:7200, 0] = .001
    audio[4000:7200, 1] = .001
    audio[10300:11000, 1] = .1
    classifier = lambda chan: [{'start': 3700, 'end': 7500}] if chan[4300] else []
    pauses = list(editorial_audio._quiet_frames(np, audio, classifier))
    assert pauses == [(.74, .96)]


def test_bundled_vad_initialization_disables_onnx_telemetry(monkeypatch):
    import os

    calls = []
    onnx = ModuleType('onnxruntime')
    onnx.disable_telemetry_events = lambda: calls.append(os.environ.get('ORT_DISABLE_TELEMETRY'))
    whisper = ModuleType('faster_whisper')
    vad = ModuleType('faster_whisper.vad')
    vad.VadOptions = lambda **kwargs: kwargs
    vad.get_speech_timestamps = lambda *args, **kwargs: []
    vad.get_vad_model = lambda: calls.append('graph loaded')
    for name, mod in ((onnx.__name__, onnx), (whisper.__name__, whisper), (vad.__name__, vad)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setenv('ORT_DISABLE_TELEMETRY', '0')
    assert editorial_audio._detector() is not None
    assert calls == ['1', 'graph loaded']


@pytest.fixture
def opposite_phase_video(tmp_path):
    source = tmp_path / 'do-not-downmix.mp4'
    tone = '.15*sin(2*PI*487*t)*(1-between(t,4,4.60))'
    subprocess.run([get_ffmpeg_path(), '-nostdin', '-v', 'error',
                    '-f', 'lavfi', '-i', 'color=black:size=160x90:rate=25:duration=8',
                    '-f', 'lavfi', '-i', f"aevalsrc='{tone}|-({tone})':s=16000:d=8",
                    '-c:v', 'libx264', '-c:a', 'aac', str(source)],
                   capture_output=True, check=True, timeout=20)
    return source


def test_scoped_pause_keeps_clock_and_multichannel_audio(opposite_phase_video, monkeypatch):
    monkeypatch.setattr(editorial_audio, '_detector', lambda: (np, lambda _: []))
    # Put a chunk edge within the pause; overlapping context must neither lose
    # the pause nor interpret opposing stereo waveforms as silence.
    monkeypatch.setattr(editorial_audio, '_CHUNK_SECONDS', 2)
    pauses = editorial_audio.measure_pauses(opposite_phase_video, (2.3, 7), subprocess.run)
    assert len(pauses) == 1
    a, b = pauses[0]
    assert 4.0 < a < 4.15 < 4.40 < b < 4.60


def test_unavailable_vad_uses_prior_long_silence_only(opposite_phase_video, monkeypatch):
    monkeypatch.setattr(editorial_audio, '_detector', lambda: None)
    # The generated file's actual gap is shorter than the 0.62s fallback threshold.
    assert editorial_audio.measure_pauses(opposite_phase_video, (2.3, 7), subprocess.run) == []


def test_decode_failure_does_not_silently_replace_result(monkeypatch):
    monkeypatch.setattr(editorial_audio, '_detector', lambda: (np, lambda _: []))

    def run(command, **kwargs):
        if '-show_entries' in command:
            return subprocess.CompletedProcess(command, 0, '2\n', '')
        raise subprocess.CalledProcessError(1, command, output=b'')

    with pytest.raises(subprocess.CalledProcessError):
        editorial_audio.measure_pauses('placeholder.mp4', (0, 10), run)


def test_selected_passages_only_share_one_probe_and_do_not_decode_gaps(monkeypatch):
    calls = []
    monkeypatch.setattr(editorial_audio, '_detector', lambda: (np, lambda _: []))

    def run(command, **kwargs):
        if '-show_entries' in command:
            calls.append('probe')
            return subprocess.CompletedProcess(command, 0, '2\n', '')
        start = float(command[command.index('-ss') + 1])
        length = float(command[command.index('-t') + 1])
        calls.append((start, length))
        return subprocess.CompletedProcess(command, 0, bytes(int(length * 16000 * 2 * 2)), b'')

    pauses = editorial_audio.measure_candidate_pauses(
        'placeholder.mp4', [(6, 7), (2, 5), (1, 3), (7, 8)], run)
    assert calls == ['probe', (1., 4.), (6., 2.)]
    assert pauses == [(1.04, 4.96), (6.04, 7.96)]


def test_invalid_or_empty_selected_ranges_do_not_open_media(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('no audio should be initialized or read')

    monkeypatch.setattr(editorial_audio, '_detector', unexpected)
    assert editorial_audio.measure_candidate_pauses('placeholder.mp4', [], unexpected) == []
    for ranges in [[(0, 3), (4, float('nan'))], [(-1, 2)], [(3, 2)], [(0, 0)], [(1, 3, 4)], ['12'], [(False, 4)]]:
        with pytest.raises(ValueError):
            editorial_audio.measure_candidate_pauses('placeholder.mp4', ranges, unexpected)


def test_selected_pause_stays_on_source_clock_with_delayed_audio_stream(tmp_path, monkeypatch):
    source = tmp_path / 'delayed-audio.mp4'
    tone = '.15*sin(2*PI*487*t)*(1-between(t,2.5,3.3))'
    subprocess.run([get_ffmpeg_path(), '-nostdin', '-v', 'error',
                    '-f', 'lavfi', '-i', 'color=black:size=160x90:rate=25:duration=7',
                    '-itsoffset', '1.25', '-f', 'lavfi', '-i', f"aevalsrc='{tone}|-({tone})':s=16000:d=5",
                    '-c:v', 'libx264', '-c:a', 'aac', str(source)],
                   capture_output=True, check=True, timeout=20)
    monkeypatch.setattr(editorial_audio, '_detector', lambda: (np, lambda _: []))
    monkeypatch.setattr(editorial_audio, '_CHUNK_SECONDS', 1)

    # The source is voiced in the first selected segment. Only the second has
    # measured quiet; its stream-relative 2.5–3.3s gap is at 3.75–4.55s in video.
    pauses = editorial_audio.measure_candidate_pauses(source, [(1.65, 2.6), (3.35, 5.2)], subprocess.run)
    assert len(pauses) == 1
    a, b = pauses[0]
    assert 3.75 < a < 3.92 < 4.39 < b < 4.55
