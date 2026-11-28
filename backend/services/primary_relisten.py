"""Caption audition for one recommended edit. Never changes the cut or source cues."""
import json
from subprocess import SubprocessError

from backend.core.project_cancellation import JobStopped, ProjectDeleted
from backend.services.local_captions import CaptionError, atomic


def _binding(data, jid, cid):
    return {'source': data['source_sha256'], 'captions': data['subtitle_sha256'],
            'job': jid, 'clip': cid, 'version': 'primary_relisten_1'}


def write_notes(jobdir, data, jid, chapter, video, run, *, decode=None):
    if decode is None:
        from backend.services.local_caption_relisten import relisten as decode
    rows = data['cues']
    positions = {cue['id']: i for i, cue in enumerate(rows)}
    retained = {rows[i]['id'] for scene in chapter['scenes']
                for i in range(positions[scene['first']], positions[scene['last']] + 1)}
    try:
        notes = decode(video, rows[positions[chapter['first']]:positions[chapter['last']] + 1], run)
    except (JobStopped, ProjectDeleted, CaptionError):
        raise
    except (OSError, ValueError, RuntimeError, ImportError, SubprocessError):
        notes = None
    kept = [note for note in notes or [] if note['cue'] in retained][:300]
    atomic(jobdir / 'primary-relisten.json', {
        **_binding(data, jid, chapter['id']), 'complete': notes is not None, 'notes': kept})


def safe_notes(jobdir, data, jid, cid):
    path = jobdir / 'primary-relisten.json'
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
            return []
        item = json.loads(path.read_text())
        if (not isinstance(item, dict) or item.get('complete') is not True
                or any(item.get(k) != v for k, v in _binding(data, jid, cid).items())
                or not isinstance(item.get('notes'), list)):
            return []
        by_id = {c['id']: c for c in data['cues']}
        out = []
        for note in item['notes'][:300]:
            cue = by_id.get(note['cue'])
            if (cue and note.get('basis') in ('listen', 'two_local_decodes')
                    and note.get('start') == cue['start'] and note.get('end') == cue['end']
                    and all(isinstance(note.get(k), str) and len(note[k]) <= 3000 for k in ('before', 'after'))):
                out.append({k: note[k] for k in ('cue', 'start', 'end', 'before', 'after', 'basis')})
        return out
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        return []
