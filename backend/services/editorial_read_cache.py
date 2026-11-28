"""Find an exact prior full reading in this project's private analysis receipts."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import stat


READING_CONTRACT = 'full_read_v1'
_LOOKBACK_JOBS = 24
_LIMITS = {'task.json': 4 * 1024 * 1024, 'result.json': 4096, 'chapters.json': 2 * 1024 * 1024}
_ID = re.compile(r'[a-f0-9]{32}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_COUNTS = ('selected_cues', 'read_cues', 'segments_total', 'segments_read')
_NOTE = ('first', 'last', 'start', 'end', 'anchor', 'summary')


def _matches(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _same_scope(value, expected):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(_number(n) for n in value) and tuple(value) == expected)


@contextmanager
def _dir(name, parent=None):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _json(parent, name):
    """Use fixed relative filenames and reject symlinks, FIFOs and oversized data."""
    limit = _LIMITS[name]
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        body = source.read(limit + 1)
        if len(body) > limit:
            return None
        value = json.loads(body)
        return value if isinstance(value, dict) else None


def _read_fields(report):
    reading = report.get('reading')
    if not isinstance(reading, dict):
        return None
    if 'contract' in reading:
        if reading['contract'] != READING_CONTRACT:
            return None
    elif report.get('analysis_version') != 'editorial_v2' or not (
            isinstance(report.get('selection'), dict) and report['selection'].get('rubric_version') == 'spread_v1'):
        # The unmarked v2 format from this workspace used precisely this reading
        # protocol. Do not infer equivalent reading from older scoring reports.
        return None
    coverage, notes, summary = (reading.get(k) for k in ('coverage', 'sections', 'summary'))
    if (not isinstance(coverage, dict) or not isinstance(notes, list)
            or not isinstance(summary, str) or not summary.strip()):
        return None
    counts = {k: coverage.get(k) for k in _COUNTS}
    if (not all(type(n) is int and n > 0 for n in counts.values())
            or not counts['selected_cues'] == counts['read_cues']
            or not counts['segments_read'] == counts['segments_total'] == len(notes)):
        return None
    if not all(isinstance(n, dict) and all(k in n for k in _NOTE)
               and _number(n['start']) and _number(n['end']) for n in notes):
        return None
    # Carry over only reading notes. Every original core and citation is checked
    # again by the selector; none of the old screens, scores or edits is reused.
    return {'contract': READING_CONTRACT, 'sections': [{k: n[k] for k in _NOTE} for n in notes],
            'summary': summary, 'coverage': counts}


def _bound_task(task, pid, jid, source_hash, subtitle_hash, expected):
    project = task.get('project')
    job = task.get('job')
    info = project.get('info') if isinstance(project, dict) else None
    return (isinstance(info, dict) and _number(info.get('duration')) and info['duration'] == expected[1]
            and project.get('id') == pid and project.get('source_sha256') == source_hash
            and project.get('subtitle_sha256') == subtitle_hash
            and isinstance(job, dict) and job.get('kind') == 'analyze' and job.get('id') == jid
            and _same_scope(task.get('scope'), expected))


def find_prior_reading(project, data, current_id, scope):
    """Read at most the latest bounded same-project receipts for an exact full scope.

    The project snapshot is captured before enqueueing the current analysis.
    Cache rejection/absence is not a failure: the selector reads the full source.
    """
    if os.open not in os.supports_dir_fd or not all(
            hasattr(os, flag) for flag in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')):
        return None
    if not isinstance(data, dict):
        return None
    project = Path(project)
    pid, jobs = data.get('id'), data.get('jobs')
    source_hash, subtitle_hash = data.get('source_sha256'), data.get('subtitle_sha256')
    info = data.get('info')
    duration = info.get('duration') if isinstance(info, dict) else None
    expected = (0, duration)
    if (not _matches(pid, _ID) or not _matches(current_id, _ID) or not isinstance(jobs, list)
            or project.name != pid or project.parent.name != 'projects' or not _number(duration)
            or duration <= 0 or not _same_scope(scope, expected)
            or not _matches(source_hash, _HASH) or not _matches(subtitle_hash, _HASH)):
        return None

    try:
        # Open one directory at a time without following symlinks, including
        # the projects / project-id / metadata boundaries above each receipt.
        with (_dir(project.parent.parent) as root_fd, _dir('projects', root_fd) as projects_fd,
              _dir(pid, projects_fd) as project_fd, _dir('metadata', project_fd) as meta_fd):
            history = jobs[-_LOOKBACK_JOBS:]
            # Defensive when called with a later project snapshot: a future
            # job must not be considered a predecessor of the current one.
            for i, job in enumerate(history):
                if isinstance(job, dict) and job.get('id') == current_id:
                    history = history[:i]
                    break
            for job in reversed(history):
                if (not isinstance(job, dict) or job.get('kind') != 'analyze'
                        or job.get('status') != 'completed' or job.get('subtitle_sha256') != subtitle_hash):
                    continue
                jid = job.get('id')
                if not _matches(jid, _ID) or jid == current_id:
                    continue
                try:
                    with _dir('job-' + jid, meta_fd) as job_fd:
                        result = _json(job_fd, 'result.json')
                        if result is None or result.get('status') != 'completed':
                            continue
                        report = _json(job_fd, 'chapters.json')
                        if report is None or not _same_scope(report.get('scope'), expected):
                            continue
                        reading = _read_fields(report)
                        if reading is None:
                            continue
                        old_task = _json(job_fd, 'task.json')
                        if old_task is not None and _bound_task(old_task, pid, jid, source_hash, subtitle_hash, expected):
                            return reading
                except (OSError, UnicodeError, ValueError, RecursionError):
                    continue  # an unavailable, unsafe or malformed receipt is not reusable
    except OSError:
        pass  # no navigable private project history
    return None
