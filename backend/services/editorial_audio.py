"""Measure removable pauses on the original source clock, without remote services."""
from __future__ import annotations

import math
import os
import re

_RATE = 16_000
_FRAME = 320  # 20 ms
_CHUNK_SECONDS = 60
_CONTEXT_SECONDS = 1
_GUARD_FRAMES = 2
_MIN_SECONDS = .20
_FILLER = re.compile(r'[呃额]{1,2}[，,。.]?\Z')
_NEIGHBOUR_WORD = re.compile(r'[\u3400-\u9fffA-Za-z0-9]')


def _detector():
    """Use the Silero graph bundled in the already prepared faster-whisper wheel."""
    # Disable telemetry before ONNX Runtime imports/initializes its environment,
    # and disable its event API as well if another module imported it first.
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    try:
        import numpy as np
        import onnxruntime
        from faster_whisper.vad import VadOptions, get_speech_timestamps, get_vad_model

        onnxruntime.disable_telemetry_events()
        get_vad_model()  # force an offline CPU session before opening media
    except (ImportError, OSError, RuntimeError):
        return None
    except Exception as error:
        if type(error).__module__.startswith('onnxruntime.'):
            return None  # optional local graph could not be loaded
        raise
    options = VadOptions(threshold=.33, min_speech_duration_ms=65,
                         min_silence_duration_ms=185, speech_pad_ms=115)

    def speech(channel):
        return get_speech_timestamps(channel, options, sampling_rate=_RATE)

    return np, speech


def _chunks(start, end):
    while start < end:
        edge = min(end, start + _CHUNK_SECONDS)
        yield start, edge
        start = edge


def _quiet_frames(np, audio, speech):
    """A candidate must be both VAD-negative and quiet on every source channel."""
    n = len(audio) // _FRAME
    if n == 0:
        return
    signal = audio[:n*_FRAME].reshape(n, _FRAME, audio.shape[1])
    peak = np.max(np.abs(signal), axis=(1, 2))
    rms = np.sqrt(np.mean(signal * signal, axis=1)).max(axis=1)
    quiet = (peak <= .038) & (rms <= .009)
    for channel in audio.T:
        for span in speech(channel.copy()):
            left = max(0, span['start'] // _FRAME)
            right = min(n, math.ceil(span['end'] / _FRAME))
            quiet[left:right] = False
    boundaries = np.flatnonzero(np.diff(quiet, prepend=False, append=False))
    for a, b in zip(boundaries[::2], boundaries[1::2]):
        a, b = int(a) + _GUARD_FRAMES, int(b) - _GUARD_FRAMES
        if (b-a) * _FRAME / _RATE >= _MIN_SECONDS:
            yield a * _FRAME / _RATE, b * _FRAME / _RATE


def _append_inside_core(intervals, ranges, core_start, core_end):
    for start, end in ranges:
        start, end = round(max(core_start, start), 6), round(min(core_end, end), 6)
        if end <= start:
            continue
        if intervals and start <= intervals[-1][1]:
            intervals[-1] = intervals[-1][0], max(intervals[-1][1], end)
        else:
            intervals.append((start, end))


def measure_pauses(video, scope, run):
    """Return sorted, padded source intervals; callers still intersect cue gaps.

    Decode the same first audio stream as the caption workspace. Do not downmix
    before measuring (opposite-phase stereo could otherwise look like silence).
    Decode bounded chunks with context across boundaries. With no local Silero
    runtime only the previous, longer FFmpeg silence test is used. Decode or
    cancellation errors are not turned into a different analysis or provider.
    """
    start, end = map(float, scope)
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError('invalid audio analysis scope')
    detector = _detector()
    ranges = []
    if detector is None:
        from backend.services.reassembly_runtime import detect_silence

        for a, b in _chunks(start, end):
            lo, hi = max(start, a-_CONTEXT_SECONDS), min(end, b+_CONTEXT_SECONDS)
            silence = detect_silence(video, (lo, hi), run)
            _append_inside_core(ranges, ((x+.16, y-.16) for x, y in silence), a, b)
    else:
        from backend.utils.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path

        np, speech = detector
        probe = run([get_ffprobe_path(), '-v', 'error', '-protocol_whitelist', 'file,pipe',
                     '-select_streams', 'a:0', '-show_entries', 'stream=channels',
                     '-of', 'default=noprint_wrappers=1:nokey=1', str(video)],
                    capture_output=True, check=True, text=True, timeout=20)
        channels = int(probe.stdout.strip() or 0)
        if not 1 <= channels <= 8:
            return []  # no automatically removable audio on unsupported channel layouts
        for a, b in _chunks(start, end):
            lo, hi = max(start, a-_CONTEXT_SECONDS), min(end, b+_CONTEXT_SECONDS)
            pcm = run([get_ffmpeg_path(), '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                       '-ss', f'{lo:.6f}', '-i', str(video), '-t', f'{hi-lo:.6f}', '-map', '0:a:0',
                       '-vn', '-sn', '-dn', '-af', f'aresample={_RATE}:async=1:first_pts=0',
                       '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'],
                      capture_output=True, check=True, timeout=90)
            audio = np.frombuffer(pcm.stdout, dtype='<i2').reshape(-1, channels).astype(np.float32) / 32768.
            quiet = ((lo+x, lo+y) for x, y in _quiet_frames(np, audio, speech))
            _append_inside_core(ranges, quiet, a, b)
    return [(a, b) for a, b in ranges if round(b-a, 6) >= _MIN_SECONDS]


def optional_filler_ids(cues):
    """Suggest only independent hesitation-only cues; never alter captions.

    A model/human may find repetitions in a sentence, but cue timing does not
    locate those individual words. Protect lexical reduplication (看看), replies
    (嗯、对), connectors (然后), and user-edited values by deciding none of them
    with a regex. An editorial plan still validates topic boundaries and flow.
    """
    ids = []
    for left, middle, right in zip(cues, cues[1:], cues[2:]):
        text = middle['text'].strip()
        if not _FILLER.fullmatch(text) or middle.get('original_text', middle['text']).strip() != text:
            continue
        left_text, right_text = left['text'].strip(), right['text'].strip()
        if (not all(_NEIGHBOUR_WORD.search(t) and not _FILLER.fullmatch(t)
                    for t in (left_text, right_text))
                or left_text.endswith(('?', '？'))
                or ('额' in text and not left_text.endswith(('.', '。', '!', '！')))):
            continue
        if (0 < left['end']-left['start'] and 0 < right['end']-right['start']
                and .10 <= middle['end']-middle['start'] <= .65
                and .025 <= middle['start']-left['end'] <= .9
                and .025 <= right['start']-middle['end'] <= .9):
            ids.append(middle['id'])
    return ids
