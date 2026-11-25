"""Small local caption workspace; reuses AutoClip's ASR, timeline and Studio renderer.

No cloud providers, downloads, diagnostics queue or legacy web application are started here.
Persistent media and transcripts belong in an ignored, isolated data directory.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pysrt


class CaptionError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def srt_body(cues: list[dict]) -> str:
    def clock(t):
        return str(pysrt.SubRipTime(milliseconds=round(t * 1000)))
    return '\n'.join(f"{i}\n{clock(c['start'])} --> {clock(c['end'])}\n{c['text']}\n"
                     for i, c in enumerate(cues, 1))


def parse_cues(path: Path, duration: float) -> list[dict]:
    cues = pysrt.open(str(path), encoding='utf-8')
    result, last = [], 0.0
    for n, cue in enumerate(cues):
        a, b = cue.start.ordinal / 1000, cue.end.ordinal / 1000
        if not (0 <= a < b <= duration and a >= last and cue.text.strip()):
            raise CaptionError('invalid_subtitles')
        result.append({'id': f'c{n + 1:05}', 'start': a, 'end': b,
                       'text': cue.text, 'original_text': cue.text})
        last = a
    if not result:
        raise CaptionError('empty_subtitles')
    return result


def caption_track(cues, draft: dict, source_info: dict) -> dict:
    """Use the same pagination/clipping and whole-frame offsets as the burned export."""
    from backend.services.publish_export import slice_srt, subtitle_line_limit
    from backend.services.studio.models import Draft
    from backend.services.studio.audio import scene_duration
    from backend.services.studio.packaging_render import timeline
    d = Draft.model_validate(draft)
    width, height = (1080, 1920) if d.aspect == 'portrait' else (source_info['width'], source_info['height'])
    entries = [{'start_time': str(pysrt.SubRipTime(milliseconds=round(c['start'] * 1000))),
                'end_time': str(pysrt.SubRipTime(milliseconds=round(c['end'] * 1000))), 'text': c['text']}
               for c in cues]
    output, source = [], []
    for i, ((start, end, offset), scene) in enumerate(zip(timeline(d.scenes), d.scenes)):
        length = scene_duration(scene)
        body = slice_srt(entries, start, end, subtitle_line_limit(width, height, d.subtitle_style))
        for cue in pysrt.from_string(body):
            a, b = cue.start.ordinal / 1000, min(cue.end.ordinal / 1000, length)
            if a >= b:
                continue
            source.append({'scene': i, 'start': start + a, 'end': start + b, 'text': cue.text})
            output.append({'scene': i, 'start': offset + a, 'end': offset + b, 'text': cue.text})
    return {'source': source, 'output': output, 'duration': sum(scene_duration(s) for s in d.scenes),
            'enabled': d.subtitles, 'width': width, 'height': height}


class CaptionWorkspace:
    def __init__(self, root: Path, *, worker_profile: Path | None = None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        from backend.services.local_runner import configure_environment
        configure_environment(self.root)
        atomic(self.root / 'privacy.json', {'analytics': False, 'crash_reports': False})
        atomic(self.root / 'diagnostics/consent.json', {'diagnostics': False, 'media': False,
               'full_original': False, 'epoch': 1, 'notice_seen': True})
        self.projects = self.root / 'projects'
        self.projects.mkdir(exist_ok=True)
        self.profile = worker_profile
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-captions')
        self.active = None
        self.closing = False
        for path in self.projects.glob('*/caption-project.json'):
            if path.parent.is_symlink():
                continue
            data = json.loads(path.read_text())
            for job in data.get('jobs', []):
                if job['status'] in ('queued', 'running'):
                    job.update(status='failed', error='interrupted')
                    suffix = 'ai-ready' if job['kind'] == 'analyze' else 'ready'
                    ready = path.parent / 'metadata' / ('job-' + job['id']) / ('dispatch.' + suffix)
                    if ready.exists():
                        ready.rename(ready.with_suffix('.interrupted'))
            atomic(path, data)

    def path(self, pid: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{32}', pid):
            raise CaptionError('not_found')
        path = self.projects / pid
        if not path.is_dir() or path.is_symlink():
            raise CaptionError('not_found')
        return path

    def load(self, pid):
        return json.loads((self.path(pid) / 'caption-project.json').read_text())

    def put(self, data):
        atomic(self.path(data['id']) / 'caption-project.json', data)

    def listing(self):
        return [{'id': p.parent.name, 'name': d['name'], 'status': d['status']}
                for p in sorted(self.projects.glob('*/caption-project.json')) if not p.parent.is_symlink()
                for d in [json.loads(p.read_text())]]

    def idle(self):
        if self.closing or self.active is not None:
            raise CaptionError('busy')

    def space(self, needed):
        if shutil.disk_usage(self.root).free < needed + 256 * 1024 * 1024:
            raise CaptionError('disk_full')

    def import_stream(self, stream, name: str, *, subtitles: Path | None = None,
                      expected_sha: str | None = None):
        from backend.services.publish_export import _probe
        from backend.services.studio.audio import has_audio
        from backend.utils.ffmpeg_utils import get_ffprobe_path
        with self.lock:
            self.idle()
            if Path(name).suffix.lower() != '.mp4':
                raise CaptionError('unsupported_format')
            pid = uuid.uuid4().hex
            path = self.projects / pid
            (path / 'raw').mkdir(parents=True)
            video = path / 'raw/input.mp4'
            try:
                size = 0
                with video.open('xb') as output:
                    while block := stream.read(2 * 1024 * 1024):
                        size += len(block)
                        if size > 8 * 1024**3:
                            raise CaptionError('file_too_large')
                        self.space(len(block))
                        output.write(block)
                sha = digest(video)
                if expected_sha and sha != expected_sha:
                    raise CaptionError('source_changed')
                info = _probe(video)
                if not (math.isfinite(info.get('duration', 0)) and info['duration'] > 0
                        and info.get('width', 0) > 0 and info.get('height', 0) > 0):
                    raise CaptionError('invalid_media')
                if not has_audio(video):
                    raise CaptionError('no_audio')
                result = subprocess.run([get_ffprobe_path(), '-v', 'error', '-show_entries',
                                         'stream=codec_type,codec_name', '-of', 'json', str(video)],
                                        capture_output=True, check=True, timeout=30)
                codecs = json.loads(result.stdout)['streams']
                if any(s['codec_name'] != ('h264' if s['codec_type'] == 'video' else 'aac')
                       for s in codecs if s['codec_type'] in ('audio', 'video')):
                    raise CaptionError('unsupported_format')
                from backend.services.studio.models import Draft, Scene
                draft = Draft(id='captions', title='AutoClip', subtitles=True, original_audio=True,
                              language='source', aspect='original', layout='fit',
                              scenes=[Scene(id='s1', start=0, end=min(info['duration'], 30))]).model_dump()
                data = {'id': pid, 'name': Path(name).name[:160], 'status': 'imported', 'info': info,
                        'source_sha256': sha, 'revision': 1, 'cues': [], 'draft': draft,
                        'plan_hash': None, 'jobs': []}
                self.put(data)
                if subtitles is not None:
                    self.finish_transcript(data, subtitles)
                else:
                    self.start_job(pid, 'transcribe')
                return self.load(pid)
            except subprocess.SubprocessError:
                shutil.rmtree(path, ignore_errors=True)
                raise CaptionError('invalid_media') from None
            except BaseException:
                shutil.rmtree(path, ignore_errors=True)
                raise

    def finish_transcript(self, data, source):
        cues = parse_cues(source, data['info']['duration'])
        path = self.path(data['id'])
        original = path / 'raw/asr-original.srt'
        if not original.exists():
            shutil.copyfile(source, original)
        data.update(cues=cues, status='ready', word_timing='not_claimed', original_srt_sha256=digest(original))
        # The initial suggestion must not mechanically end halfway through a sentence.
        complete = [c['end'] for c in cues if c['end'] <= 30]
        data['draft']['scenes'] = [{'id': 's1', 'label': 'AutoClip', 'start': 0,
                                    'end': max(complete) if complete else cues[0]['end']}]
        self.save_captions(data)
        self.put(data)

    def save_captions(self, data):
        path = self.path(data['id'])
        atomic(path / f"metadata/transcript-{data['revision']:06}.json", {
            'revision': data['revision'], 'cues': data['cues'], 'time_basis': 'original_sentence_timestamps'})
        target = path / 'raw/input.srt'
        tmp = target.with_suffix('.srt.tmp')
        tmp.write_text(srt_body(data['cues']), encoding='utf-8')
        os.replace(tmp, target)
        data['subtitle_sha256'] = digest(target)
        data['plan_hash'] = None

    def edit_captions(self, pid, revision, edits):
        with self.lock:
            self.idle()
            data = self.load(pid)
            if data['revision'] != revision or data['status'] != 'ready':
                raise CaptionError('revision_conflict')
            found = {c['id']: c for c in data['cues']}
            for edit in edits:
                if set(edit) != {'id', 'text'}:
                    raise CaptionError('invalid_subtitles')
                text = edit['text'].strip()
                if edit['id'] not in found or not text or len(text) > 2000 or '\x00' in text or '\n\n' in text or '\r' in text:
                    raise CaptionError('invalid_subtitles')
                found[edit['id']]['text'] = text
            data['revision'] += 1
            self.save_captions(data)
            self.put(data)
            return data

    def plan(self, pid, revision, draft):
        from backend.services.studio.models import Draft
        from backend.services.studio.intelligence import validate_scenes
        with self.lock:
            self.idle()
            data = self.load(pid)
            if data['revision'] != revision or data['status'] != 'ready':
                raise CaptionError('revision_conflict')
            # This entry never translates, generates narration/frames, or activates templates.
            d = Draft(id='captions', title='AutoClip', hook='', language='source', layout='fit',
                      original_audio=True, **draft)
            validate_scenes(d.scenes, data['info']['duration'])
            data['draft'] = d.model_dump()
            data['revision'] += 1
            payload = {'source_sha256': data['source_sha256'], 'subtitle_sha256': data['subtitle_sha256'],
                       'revision': data['revision'], 'draft': data['draft']}
            data['plan_hash'] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                         separators=(',', ':')).encode()).hexdigest()
            self.put(data)
            return data

    def start_job(self, pid, kind, *, plan_hash=None, approved=False, scope=None):
        with self.lock:
            self.idle()
            data = self.load(pid)
            if kind == 'render' and (not approved or not plan_hash or plan_hash != data.get('plan_hash')):
                raise CaptionError('approval_required')
            if kind == 'render' and data['status'] != 'ready':
                raise CaptionError('invalid_subtitles')
            if kind == 'analyze':
                if data['status'] != 'ready' or not data['cues']:
                    raise CaptionError('analysis_no_cues')
                duration = data['info']['duration']
                a, b = (0 if scope[0] is None else scope[0], duration if scope[1] is None else scope[1])
                if not 0 <= a < b <= duration or not 18 <= b-a <= 3*3600:
                    raise CaptionError('analysis_range')
                scope = (a,b)
            if kind not in ('render', 'transcribe', 'analyze'):
                raise CaptionError('invalid_job')
            track = caption_track(data['cues'], data['draft'], data['info']) if kind == 'render' else None
            self.space(max(256 * 1024**2, int(track['duration'] * 8 * 1024**2)) if track else 512 * 1024**2)
            job = {'id': uuid.uuid4().hex, 'kind': kind, 'status': 'queued', 'created': time.time()}
            path = self.path(pid) / 'metadata' / ('job-' + job['id'])
            path.mkdir(parents=True)
            atomic(path / 'task.json', {'project': data, 'job': job, 'track': track, 'scope': scope})
            data['jobs'].append(job)
            self.put(data)
            active = {'pid': pid, 'job': job['id'], 'process': None, 'cancel': threading.Event()}
            self.active = active
            try:
                self.pool.submit(self._run, active, path)
            except BaseException:
                self.active = None
                job.update(status='failed', error='interrupted')
                self.put(data)
                raise
            return job

    def _run(self, active, path):
        pid, jid, proc = active['pid'], active['job'], None
        try:
            with self.lock:
                data = self.load(pid)
                job = next(j for j in data['jobs'] if j['id'] == jid)
                job['status'] = 'running'
                self.put(data)
                if active['cancel'].is_set():
                    raise CaptionError('cancelled')
                cmd = [sys.executable, '-B', '-m', 'backend.local_captions_worker', str(path / 'task.json'), str(os.getpid())]
                if self.profile:
                    # macOS refuses sandbox_apply from an already sandboxed parent.
                    # A separately launched deny-network broker consumes this local file queue.
                    (path / ('dispatch.ai-ready' if job['kind'] == 'analyze' else 'dispatch.ready')).touch(exist_ok=False)
                else:
                    with (path / 'worker.stdout').open('wb') as out, (path / 'worker.stderr').open('wb') as err:
                        proc = subprocess.Popen(cmd, stdout=out, stderr=err, start_new_session=True)
                        active['process'] = proc
            if self.profile:
                deadline = time.monotonic() + 4 * 3600
                claim_deadline = time.monotonic() + 25
                while not (path / 'result.json').exists():
                    if active['cancel'].is_set():
                        (path / 'cancel').touch()
                    if (time.monotonic() > claim_deadline and not (path / 'dispatch.claimed').exists()) or time.monotonic() > deadline:
                        (path / 'cancel').touch()
                        raise CaptionError('worker_unavailable')
                    time.sleep(.1)
                code = 0
            else:
                code = proc.wait(timeout=4 * 3600)
            with self.lock:
                data = self.load(pid)
                job = next(j for j in data['jobs'] if j['id'] == jid)
                result = json.loads((path / 'result.json').read_text()) if (path / 'result.json').exists() else {}
                if active['cancel'].is_set():
                    raise CaptionError('cancelled')
                if code or result.get('status') != 'completed':
                    raise CaptionError(result.get('error', 'worker_failed'))
                if job['kind'] == 'transcribe':
                    self.finish_transcript(data, self.path(pid) / 'raw/input.srt')
                if job['kind'] == 'analyze':
                    job['subtitle_sha256'] = data['subtitle_sha256']
                job.update(status='completed', seconds=result['seconds'])
                self.put(data)
        except BaseException as error:
            if proc is not None and proc.poll() is None:
                self._kill(proc)
            with self.lock:
                data = self.load(pid)
                job = next(j for j in data['jobs'] if j['id'] == jid)
                job.update(status='cancelled' if active['cancel'].is_set() else 'failed',
                           error=error.code if isinstance(error, CaptionError) else 'worker_failed')
                (self.path(pid) / 'output/studio' / (jid + '.mp4')).unlink(missing_ok=True)
                self.put(data)
        finally:
            with self.lock:
                if self.active is active:
                    self.active = None

    @staticmethod
    def _kill(proc):
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            except ProcessLookupError:
                pass

    def cancel(self, pid, jid):
        with self.lock:
            a = self.active
            if not a or (a['pid'], a['job']) != (pid, jid):
                raise CaptionError('not_running')
            a['cancel'].set()
            (self.path(pid) / 'metadata' / ('job-' + jid) / 'cancel').touch()
            proc = a['process']
        if proc:
            self._kill(proc)

    def close(self):
        with self.lock:
            self.closing = True
            a = self.active
            if a:
                self.cancel(a['pid'], a['job'])
        self.pool.shutdown(wait=True, cancel_futures=False)

    def analysis(self, pid, jid=None):
        data = self.load(pid)
        match = next((j for j in reversed(data['jobs']) if j['kind'] == 'analyze'
                      and j['status'] == 'completed' and (jid is None or j['id'] == jid)
                      and j.get('subtitle_sha256') == data.get('subtitle_sha256')), None)
        if not match:
            return None
        path = self.path(pid) / 'metadata' / ('job-' + match['id']) / 'chapters.json'
        return {'job_id': match['id'], **json.loads(path.read_text())}

    def download(self, pid, jid, kind):
        data = self.load(pid)
        job = next((j for j in data['jobs'] if j['id'] == jid), None)
        if not job or job['status'] != 'completed' or job['kind'] != 'render':
            raise CaptionError('not_found')
        base = self.path(pid)
        files = {'video': base / 'output/studio' / (jid + '.mp4'),
                 'subtitles': base / 'metadata' / ('job-' + jid) / 'captions.srt',
                 'plan': base / 'metadata' / ('job-' + jid) / 'plan.json'}
        path = files.get(kind)
        if not path or not path.is_file() or path.is_symlink():
            raise CaptionError('not_found')
        return path
