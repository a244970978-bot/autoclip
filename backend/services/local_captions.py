"""Small local caption workspace; reuses AutoClip's ASR, timeline and Studio renderer.

No cloud providers, downloads, diagnostics queue or legacy web application are started here.
Persistent media and transcripts belong in an ignored, isolated data directory.
"""
from __future__ import annotations

import errno
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
            return self._enqueue(self.load(pid), kind, plan_hash=plan_hash, approved=approved, scope=scope)

    def one_click(self, pid):
        """Prepare selectable previews, never a saved draft, approval or render."""
        with self.lock:
            if self.closing:
                raise CaptionError('busy')
            data = self.load(pid)
            if self.active is not None and self.active['pid'] == pid:
                job = next(j for j in data['jobs'] if j['id'] == self.active['job'])
                if job['status'] in ('queued', 'running') and not self.active['cancel'].is_set():
                    if job.get('one_click') is True:
                        return job
                    if job['kind'] == 'transcribe' and not data['cues']:
                        # The worker needs no new inputs. _run reloads this unconditional
                        # follow-on intent when it accepts the completed transcription.
                        job['one_click'] = True
                        self.put(data)
                        return job
            self.idle()
            return self._enqueue(data, 'analyze' if data['cues'] else 'transcribe', one_click=True)

    def polish_latest(self, pid):
        with self.lock:
            self.idle()
            data = self.load(pid)
            job, report = self._analysis_result(data, None)
            if not job or report.get('analysis_version') != 'editorial_v2' or not report.get('reading', {}).get('coverage', {}).get('complete'):
                raise CaptionError('revision_conflict')
            initial = [c for c in report['chapters'] if self._preview_draft(data, report, c) is not None]
            if not initial or report.get('polish_version'):
                raise CaptionError('revision_conflict')
            return self._enqueue(data, 'analyze', one_click=True, scope=report['scope'],
                                 polish_input=dict(report, chapters=initial))

    def _enqueue(self, data, kind, *, plan_hash=None, approved=False, scope=None,
                 one_click=False, previous=None, polish_input=None):
        """Queue with self.lock held. A continuation and its link are persisted together."""
        self.idle()
        pid = data['id']
        if kind == 'render' and (not approved or not plan_hash or plan_hash != data.get('plan_hash')):
            raise CaptionError('approval_required')
        if kind == 'render' and data['status'] != 'ready':
            raise CaptionError('invalid_subtitles')
        if kind == 'transcribe' and data['cues']:
            raise CaptionError('revision_conflict')
        if kind == 'analyze':
            if data['status'] != 'ready' or not data['cues']:
                raise CaptionError('analysis_no_cues')
            duration = data['info']['duration']
            start, end = scope if scope is not None else (None, None)
            a, b = 0 if start is None else start, duration if end is None else end
            if not 0 <= a < b <= duration or not 18 <= b-a <= 3*3600:
                raise CaptionError('analysis_range')
            scope = (a, b)
        if kind not in ('render', 'transcribe', 'analyze'):
            raise CaptionError('invalid_job')
        track = caption_track(data['cues'], data['draft'], data['info']) if kind == 'render' else None
        self.space(max(256 * 1024**2, int(track['duration'] * 8 * 1024**2)) if track else 512 * 1024**2)
        job = {'id': uuid.uuid4().hex, 'kind': kind, 'status': 'queued', 'created': time.time()}
        if one_click:
            job['one_click'] = True
        path = self.path(pid) / 'metadata' / ('job-' + job['id'])
        path.mkdir(parents=True)
        try:
            atomic(path / 'task.json', {'project': data, 'job': job, 'track': track, 'scope': scope, 'polish_input': polish_input})
            data['jobs'].append(job)
            if previous is not None:
                previous['one_click_next'] = job['id']
            self.put(data)
        except Exception:
            if data['jobs'] and data['jobs'][-1] is job:
                data['jobs'].pop()
            if previous is not None:
                previous.pop('one_click_next', None)
            shutil.rmtree(path, ignore_errors=True)
            raise
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

    def _continue_one_click(self, data, job):
        # _run still holds self.lock; nothing can insert other work or edit this
        # project's just-transcribed cues between transcription and analysis.
        self.active = None
        try:
            self._enqueue(data, 'analyze', one_click=True, previous=job)
        except (CaptionError, OSError, RuntimeError) as error:
            if isinstance(error, CaptionError):
                code = error.code
            elif isinstance(error, OSError):
                code = 'disk_full' if error.errno == errno.ENOSPC else 'io_failed'
            else:
                code = 'interrupted'
            # A scheduling failure is not a transcription failure. Keep the cues.
            job['one_click_error'] = code
            self.put(data)

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
                if job['kind'] == 'transcribe' and job.get('one_click'):
                    self._continue_one_click(data, job)
                else:
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
            if not a or a['pid'] != pid:
                raise CaptionError('not_running')
            if a['job'] != jid:
                # A click made just before the ASR → analysis handoff applies
                # only to the exact continuation, never to another attempt.
                jobs = self.load(pid)['jobs']
                if not any(j['id'] == jid and j.get('one_click_next') == a['job'] for j in jobs):
                    raise CaptionError('not_running')
            a['cancel'].set()
            (self.path(pid) / 'metadata' / ('job-' + a['job']) / 'cancel').touch()
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
        with self.lock:
            data = self.load(pid)
            match, report = self._analysis_result(data, jid)
            if match and (match.get('one_click') or report.get('analysis_version') == 'editorial_v2'):
                choices = [c for c in report.get('chapters', []) if self._preview_draft(data, report, c) is not None]
                if report.get('analysis_version') == 'editorial_v1':
                    choices.sort(key=lambda c: (c['id'] == report.get('recommended'),
                                                c.get('appeal') if type(c.get('appeal')) is int else 0), reverse=True)
                report['previewable'] = [c['id'] for c in choices]
            return report

    def _analysis_result(self, data, jid):
        match = next((j for j in reversed(data['jobs']) if j['kind'] == 'analyze'
                      and j['status'] == 'completed' and (jid is None or j['id'] == jid)
                      and j.get('subtitle_sha256') and j['subtitle_sha256'] == data.get('subtitle_sha256')), None)
        if not match:
            return None, None
        path = self.path(data['id']) / 'metadata' / ('job-' + match['id']) / 'chapters.json'
        if path.is_symlink():
            return None, None
        try:
            report = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            return None, None
        if not isinstance(report, dict) or not isinstance(report.get('chapters'), list):
            return None, None
        return match, {**report, 'job_id': match['id']}

    @staticmethod
    def _preview_draft(data, report, chapter):
        from backend.services.editorial_recut import CHECK_KEYS
        from backend.services.editorial_selection import EVIDENCE
        from backend.services.local_reassembly import _span
        from backend.services.studio.models import Draft
        from backend.services.studio.intelligence import validate_scenes

        version = report.get('analysis_version')
        if (version not in ('editorial_v1', 'editorial_v2') or not isinstance(chapter, dict)
                or not isinstance(chapter.get('id'), str)
                or chapter.get('editorial_review') != 'passed'):
            return None
        if report.get('polish_version') and chapter.get('second_pass', {}).get('passed') is not True:
            return None
        moved = chapter.get('mode') == 'hook_first'
        if not moved and (version != 'editorial_v2' or chapter.get('mode') != 'source_order'):
            return None
        editorial = chapter.get('editorial_checks')
        checks = editorial.get('checks') if isinstance(editorial, dict) else None
        if (not isinstance(checks, dict)
                or editorial.get('outcome') != ('hook_accepted' if moved else 'body_accepted')
                or set(checks) != set(CHECK_KEYS) or not all(value is True for value in checks.values())):
            return None
        try:
            if version == 'editorial_v2':
                coverage = report['reading']['coverage']
                lo, hi = report['scope']
                actual_count = sum(lo <= cue['start'] and cue['end'] <= hi for cue in data['cues'])
                if (coverage['complete'] is not True or editorial.get('clean_body_confirmed') is not True
                        or not 0 <= lo < hi <= data['info']['duration']
                        or not 0 < actual_count == coverage['selected_cues'] == coverage['read_cues'] == coverage['screened_cues']
                        or not 0 < coverage['segments_total'] == coverage['segments_read'] == coverage['segments_screened']):
                    return None
            pieces = chapter['scenes']
            roles = [s['role'] for s in pieces]
            first_body = next((i for i, role in enumerate(roles) if role != 'hook'), 0)
            if (bool(first_body) != moved or roles[-1] != 'close'
                    or any(role not in ('body', 'close') for role in roles[first_body:])):
                return None
            scenes = [{'id': s['id'], 'start': s['start'], 'end': s['end'],
                       **{k:s[k] for k in ('punch','focus_x','focus_y') if k in s},
                       'label': 'smart-' + ('body' if s['role'] == 'close' and i < len(pieces) - 1 else s['role'])}
                      for i, s in enumerate(pieces)]
            d = Draft(id='captions', title='AutoClip', scenes=scenes, subtitles=True,
                      aspect=data['draft']['aspect'], subtitle_style=data['draft']['subtitle_style'])
            duration = data['info']['duration']
            validate_scenes(d.scenes, duration)
            timeline = sorted(d.scenes, key=lambda s: s.start)
            if ((moved and (d.scenes[0].start <= d.scenes[first_body].start
                            or d.scenes[first_body - 1].start >= d.scenes[-1].start))
                    or any(a.start >= b.start for i, (a, b) in enumerate(zip(d.scenes, d.scenes[1:]), 1)
                           if i != first_body)
                    or any(s.end > duration for s in timeline)
                    or len({s.id for s in timeline}) != len(timeline)):
                return None
            for a, b in zip(timeline, timeline[1:]):
                # Workers preserve up to 80/100 ms of padding on either side.
                # That padding may meet in a short source-cue gap, not in speech.
                if a.end > b.start and (a.end - b.start > .180001 or any(
                        c['start'] < a.end and c['end'] > b.start for c in data['cues'])):
                    return None
            if version == 'editorial_v2':
                evidence = chapter['selection']['evidence']
                if set(evidence) != set(EVIDENCE):
                    return None
                subtitle = data['cues']
                ids = {c['id']: i for i, c in enumerate(subtitle)}
                spans = {key: _span([v['first'], v['last']], ids) for key, v in evidence.items()}
                claim, support, closing = (spans[k] for k in EVIDENCE)
                if (not (claim[1] < support[0] or support[1] < claim[0])
                        or closing[0] <= max(claim[1], support[1])):
                    return None
                for key, item in evidence.items():
                    start, end = spans[key]
                    cited = subtitle[start:end+1]
                    if (item['start'] != cited[0]['start'] or item['end'] != cited[-1]['end']
                            or item['quote'] != ' '.join(c['text'] for c in cited)
                            or any(not any(s.start <= c['start'] + .000001 and s.end >= c['end'] - .000001
                                           for s in timeline) for c in cited)):
                        return None
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        return d.model_dump()

    def candidate_preview(self, pid, jid, cid):
        with self.lock:
            data = self.load(pid)
            _, report = self._analysis_result(data, jid)
            if report is not None:
                chapter = next((c for c in report['chapters'] if isinstance(c, dict) and c.get('id') == cid), None)
                draft = self._preview_draft(data, report, chapter)
                if draft is not None:
                    return {'draft': draft, 'track': caption_track(data['cues'], draft, data['info']),
                            'revision': data['revision'], 'job_id': jid, 'chapter_id': cid}
            raise CaptionError('not_found')

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
