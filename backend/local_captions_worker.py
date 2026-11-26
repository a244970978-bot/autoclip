"""Killable, offline-only worker. Raw output never leaves the local job folder."""
from pathlib import Path
import json
import os
import signal
import sys
import threading
import time

from backend.services.local_captions import CaptionError, atomic, digest, srt_body


def main():
    # Whisper's bundled VAD and the pause detector both use local ONNX Runtime.
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    task_path = Path(sys.argv[1]).resolve()
    parent = int(sys.argv[2])
    started = time.monotonic()
    task = json.loads(task_path.read_text())
    data, job = task['project'], task['job']
    result = {'status': 'failed', 'error': 'worker_failed'}

    def no_connections(event, args):
        if event == 'socket.connect' and not (job['kind'] == 'analyze' and
                isinstance(args[1], str) and args[1] == os.environ.get('AUTOCLIP_MODEL_SOCKET')):
            raise PermissionError('This worker is offline; no cloud fallback or downloads')
    sys.addaudithook(no_connections)

    def stopped(*_):
        raise CaptionError('cancelled')
    signal.signal(signal.SIGTERM, stopped)

    def parent_watch():
        while True:
            if os.getppid() != parent:
                from backend.core import project_cancellation
                project_cancellation.stop(data['id'])
                os._exit(2)
            time.sleep(.5)
    threading.Thread(target=parent_watch, daemon=True).start()
    root = Path(os.environ['AUTOCLIP_DATA_DIR']).resolve()
    project = root / 'projects' / data['id']
    video, subtitle = project / 'raw/input.mp4', project / 'raw/input.srt'
    try:
        if digest(video) != data['source_sha256']:
            raise CaptionError('source_changed')
        if job['kind'] == 'transcribe':
            from backend.services.whisper_model_manager import get_model_manager
            model_path = get_model_manager().get_local_model_path('small')
            if model_path is None:
                raise CaptionError('model_unavailable')
            pins = json.loads((Path(__file__).parent / 'assets/local_captions/runtime-pins.json').read_text())
            if not all((model_path / name).is_file() and digest(model_path / name) == expected
                       for name, expected in pins['whisper_files'].items()):
                raise CaptionError('model_changed')
            from backend.utils.speech_recognizer import generate_subtitle_for_video
            from backend.utils.transcription_progress import reporting
            with reporting(lambda value: atomic(task_path.parent / 'progress.json', {'fraction': value})):
                generate_subtitle_for_video(video, output_path=subtitle, method='whisper_local', model='small',
                                           language='zh', enable_fallback=False, enable_speaker_diarization=False)
        elif job['kind'] == 'analyze':
            if digest(subtitle) != data['subtitle_sha256']:
                raise CaptionError('subtitles_changed')
            from backend.core import project_cancellation
            from backend.services.editorial_audio import measure_candidate_pauses
            from backend.services.editorial_selection import select_edits
            from backend.services.reassembly_runtime import local_model
            def progress(done, total, phase):
                atomic(task_path.parent / 'progress.json', {'done': done, 'total': total, 'phase': phase})
            def measure(ranges):
                with project_cancellation.bind(data['id']):
                    return measure_candidate_pauses(video, ranges, project_cancellation.run)
            with local_model(task_path.parent / 'model-runtime.log') as call:
                chapters = select_edits(data['cues'], task['scope'], data['info']['duration'], call, measure, progress)
            if digest(subtitle) != data['subtitle_sha256']:
                raise CaptionError('subtitles_changed')
            atomic(task_path.parent / 'chapters.json', chapters)
        else:
            if digest(subtitle) != data['subtitle_sha256']:
                raise CaptionError('subtitles_changed')
            from backend.services.studio.models import Draft
            from backend.services.studio.render import render_draft
            from backend.core import project_cancellation
            with project_cancellation.bind(data['id']):
                render_draft(data['id'], video, Draft.model_validate(data['draft']), job['id'],
                             lambda value: atomic(task_path.parent / 'progress.json', {'fraction': value / 100}),
                             brand_outro=False)
            if digest(subtitle) != data['subtitle_sha256']:
                raise CaptionError('subtitles_changed')
            # Turning off burned captions does not discard the independently exportable SRT.
            (task_path.parent / 'captions.srt').write_text(srt_body(task['track']['output']), encoding='utf-8')
            atomic(task_path.parent / 'plan.json', {'plan_hash': data['plan_hash'], 'revision': data['revision'],
                   'source_sha256': data['source_sha256'], 'subtitle_sha256': data['subtitle_sha256'],
                   'draft': data['draft'], 'timeline': task['track'], 'approval': 'explicit_local_export_request'})
        if digest(video) != data['source_sha256']:
            raise CaptionError('source_changed')
        result = {'status': 'completed', 'seconds': round(time.monotonic() - started, 3)}
    except BaseException as error:
        result = {'status': 'failed', 'error': error.code if isinstance(error, CaptionError) else 'worker_failed',
                  'error_type': type(error).__name__, 'seconds': round(time.monotonic() - started, 3)}
    finally:
        atomic(task_path.parent / 'result.json', result)
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
