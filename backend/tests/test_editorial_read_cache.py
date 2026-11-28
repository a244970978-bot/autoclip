"""Private-receipt cache contracts with generated metadata and cue text only."""
from contextlib import contextmanager
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from backend.services import editorial_read_cache as cache
from backend.services.editorial_selection import select_edits


PID, PREVIOUS, CURRENT = ('1'*32, '2'*32, '3'*32)
MEDIA_BYTES, SRT_BYTES = b'isolated source identity fixture', b'isolated subtitle identity fixture'
SOURCE, SUBTITLES = (hashlib.sha256(payload).hexdigest() for payload in (MEDIA_BYTES, SRT_BYTES))
SCOPE = (0, 30)
SAFE_READER = os.open in os.supports_dir_fd and all(
    hasattr(os, flag) for flag in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK'))


def _write(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')


@pytest.fixture
def receipts(tmp_path):
    project = tmp_path / 'workspace' / 'projects' / PID
    cues = [{'id': f'c{i+1:05}', 'start': i*7+.2, 'end': i*7+5,
             'text': '合成字幕。', 'original_text': '合成字幕。'} for i in range(4)]
    data = {'id': PID, 'source_sha256': SOURCE, 'subtitle_sha256': SUBTITLES,
            'info': {'duration': SCOPE[1]}, 'cues': cues, 'jobs': []}

    def add(jid=PREVIOUS, *, version='editorial_v2', contract=None):
        previous = {'id': jid, 'kind': 'analyze', 'status': 'completed', 'subtitle_sha256': SUBTITLES}
        snap = {'job': {'id': jid, 'kind': 'analyze'}, 'project': data, 'scope': SCOPE}
        note = {'start': cues[0]['start'], 'end': cues[-1]['end'],
                'first': cues[0]['id'], 'last': cues[-1]['id'],
                'anchor': [cues[0]['id'], cues[0]['id']], 'summary': '这是一条合成阅读笔记。',
                'old_edit': 'must not inherit'}
        # A complete reading can be reused even if later screening failed.
        coverage = {'selected_cues': 4, 'read_cues': 4, 'segments_total': 1, 'segments_read': 1,
                    'complete': False, 'screened_cues': 0}
        reading = {'coverage': coverage, 'sections': [note], 'summary': '合成全文主线。'}
        if contract is not None:
            reading['contract'] = contract
        report = {'analysis_version': version, 'scope': SCOPE, 'reading': reading,
                  'selection': {'rubric_version': 'spread_v1'},
                  'chapters': [{'decision': 'obsolete'}], 'recommended': 'T01'}
        folder = project / 'metadata' / ('job-' + jid)
        folder.mkdir(parents=True)
        _write(folder/'task.json', snap)
        _write(folder/'result.json', {'status': 'completed'})
        _write(folder/'chapters.json', report)
        data['jobs'].append(previous)
        return folder

    return project, data, add


def test_reuses_grounded_reading_but_rescreens_original_cues(receipts):
    project, data, add = receipts
    add(contract=cache.READING_CONTRACT)
    reading = cache.find_prior_reading(project, data, CURRENT, SCOPE)
    if not SAFE_READER:
        assert reading is None
        return
    assert set(reading) == {'contract', 'summary', 'sections', 'coverage'}
    assert 'old_edit' not in reading['sections'][0]
    assert set(reading['coverage']) == {'selected_cues', 'read_cues', 'segments_total', 'segments_read'}
    stages = []

    def call(_system, payload, **_kwargs):
        stages.append(payload['stage'])
        assert payload['stage'] == 'screen'
        assert payload['whole_summary'] == reading['summary']
        assert len(payload['rows']) == len(data['cues'])
        return {'chapters': []}

    def measure(_ranges):
        pytest.fail('no chosen passages to measure')

    report = select_edits(data['cues'], SCOPE, SCOPE[1], call, measure, prior_reading=reading)
    assert report['reading']['reused'] is True and report['reading']['coverage']['complete'] is True
    assert stages == ['screen'] and report['chapters'] == []


def test_legacy_v2_compatibility_does_not_weaken_anchor_validation(receipts):
    project, data, add = receipts
    folder = add()
    reading = cache.find_prior_reading(project, data, CURRENT, SCOPE)
    if not SAFE_READER:
        assert reading is None
        return
    assert reading['contract'] == cache.READING_CONTRACT
    path = folder / 'chapters.json'
    report = json.loads(path.read_text())
    report['reading']['sections'][0]['anchor'] = ['never-an-original-caption']*2
    _write(path, report)
    source = cache.find_prior_reading(project, data, CURRENT, SCOPE)
    stages = []

    def call(_system, payload, **_kwargs):
        stage = payload['stage']
        stages.append(stage)
        if stage == 'read':
            return {'note': '重新核对原字幕。', 'anchor': [payload['core_first']]*2}
        if stage == 'synthesis':
            return {'summary': '重新概括。'}
        assert stage == 'screen'
        return {'chapters': []}

    updated = select_edits(data['cues'], SCOPE, SCOPE[1], call, lambda _: [], prior_reading=source)
    assert stages == ['read', 'synthesis', 'screen'] and updated['reading']['reused'] is False


@pytest.mark.parametrize('case', [
    'source', 'subtitle', 'historic_subtitle', 'scope', 'report_scope', 'duration', 'other_project',
    'different_job', 'other_kind', 'unchecked_job', 'unchecked_result', 'incomplete', 'boolean_count',
    'v1', 'versioned_other_contract', 'missing_report', 'corrupt_result',
])
def test_ineligible_receipts_always_choose_fresh_reading(receipts, case):
    project, data, add = receipts
    folder = add(version='editorial_v1' if case == 'v1' else 'editorial_v2',
                 contract='other-contract' if case == 'versioned_other_contract' else None)
    if case in ('source', 'subtitle', 'scope', 'duration', 'other_project', 'different_job', 'other_kind'):
        path = folder/'task.json'
        task = json.loads(path.read_text())
        if case == 'source':
            task['project']['source_sha256'] = 'c'*64
        elif case == 'subtitle':
            task['project']['subtitle_sha256'] = 'c'*64
        elif case == 'scope':
            task['scope'] = [0, 25]
        elif case == 'duration':
            task['project']['info']['duration'] = 35
        elif case == 'other_project':
            task['project']['id'] = '4'*32
        elif case == 'different_job':
            task['job']['id'] = '4'*32
        elif case == 'other_kind':
            task['job']['kind'] = 'transcribe'
        _write(path, task)
    elif case in ('historic_subtitle', 'unchecked_job'):
        data['jobs'][0].update({'subtitle_sha256': 'c'*64} if case == 'historic_subtitle' else {'status': 'failed'})
    elif case in ('unchecked_result', 'corrupt_result'):
        path = folder/'result.json'
        if case == 'unchecked_result':
            _write(path, {'status': 'cancelled'})
        else:
            path.write_text('{incomplete!', encoding='utf-8')
    elif case == 'missing_report':
        (folder/'chapters.json').unlink()
    elif case in ('report_scope', 'incomplete', 'boolean_count'):
        path = folder/'chapters.json'
        report = json.loads(path.read_text())
        if case == 'report_scope':
            report['scope'] = [0, 25]
        elif case == 'incomplete':
            report['reading']['coverage']['read_cues'] = 3
        else:
            report['reading']['coverage']['segments_read'] = True
        _write(path, report)
    assert cache.find_prior_reading(project, data, CURRENT, SCOPE) is None


@pytest.mark.parametrize('part', ['root', 'projects', 'project', 'metadata', 'job', 'file'])
def test_never_follow_symlinks_at_any_receipt_boundary(receipts, part):
    project, data, add = receipts
    folder = add()
    if not SAFE_READER:
        assert cache.find_prior_reading(project, data, CURRENT, SCOPE) is None
        return
    target = {'root': project.parent.parent, 'projects': project.parent, 'project': project,
              'metadata': folder.parent, 'job': folder, 'file': folder/'chapters.json'}[part]
    displaced = target.with_name(target.name + '-elsewhere')
    target.rename(displaced)
    target.symlink_to(displaced, target_is_directory=part != 'file')
    assert cache.find_prior_reading(project, data, CURRENT, SCOPE) is None


def test_current_future_unlisted_and_out_of_bounds_jobs_are_excluded(receipts, monkeypatch):
    project, data, add = receipts
    add()
    valid = data['jobs'].pop()
    assert cache.find_prior_reading(project, data, CURRENT, SCOPE) is None
    data['jobs'].extend([{'id': CURRENT}, valid])
    assert cache.find_prior_reading(project, data, CURRENT, SCOPE) is None
    data['jobs'] = [valid]
    assert cache.find_prior_reading(project, data, CURRENT, (1, 30)) is None
    monkeypatch.setattr(cache, '_LOOKBACK_JOBS', 2)
    data['jobs'].extend([{'id': '4'*32}, {'id': '5'*32}])
    assert cache.find_prior_reading(project, data, CURRENT, SCOPE) is None


def test_receipt_read_size_is_capped(receipts, monkeypatch):
    project, data, add = receipts
    add()
    monkeypatch.setitem(cache._LIMITS, 'chapters.json', 4)
    assert cache.find_prior_reading(project, data, CURRENT, SCOPE) is None


def test_corruption_of_latest_job_does_not_hide_valid_previous_receipt(receipts):
    project, data, add = receipts
    add()
    latest = add('4'*32)
    (latest/'chapters.json').write_text('{incomplete!', encoding='utf-8')
    reading = cache.find_prior_reading(project, data, CURRENT, SCOPE)
    if SAFE_READER:
        assert reading['contract'] == cache.READING_CONTRACT
    else:
        assert reading is None


def test_analysis_worker_reuses_reading_and_screens_via_grounded_units(receipts, monkeypatch):
    from backend import local_captions_worker as worker
    from backend.services import editorial_audio, reassembly_runtime

    project, data, add = receipts
    add()
    raw = project/'raw'
    raw.mkdir()
    (raw/'input.mp4').write_bytes(MEDIA_BYTES)
    (raw/'input.srt').write_bytes(SRT_BYTES)
    current = project/'metadata'/('job-' + CURRENT)
    current.mkdir()
    task = current/'task.json'
    _write(task, {'project': data, 'scope': SCOPE, 'job': {'id': CURRENT, 'kind': 'analyze'}})
    monkeypatch.setenv('AUTOCLIP_DATA_DIR', str(project.parent.parent))
    monkeypatch.setenv('ORT_DISABLE_TELEMETRY', '1')
    monkeypatch.setattr(worker, 'sys', SimpleNamespace(argv=['worker', str(task), '1'],
                                                        addaudithook=lambda _: None))
    monkeypatch.setattr(worker, 'threading', SimpleNamespace(
        Thread=lambda **_: SimpleNamespace(start=lambda: None)))
    monkeypatch.setattr(worker, 'signal', SimpleNamespace(SIGTERM=15, signal=lambda *_: None))
    stages = []

    def call(_instruction, payload, **_kwargs):
        stages.append(payload['stage'])
        if payload['stage'] == 'read':
            return {'note': '重新核对原字幕。', 'anchor': [payload['core_first']]*2}
        if payload['stage'] == 'synthesis':
            return {'summary': '重新概括。'}
        assert payload['stage'] == 'screen' and len(payload['units']) == len(data['cues'])
        assert 'rows' not in payload  # the worker's model may only choose grounded units
        return {'chapters': []}

    @contextmanager
    def fake_model(_path):
        yield call

    monkeypatch.setattr(reassembly_runtime, 'local_model', fake_model)

    def unexpected(*_args):
        pytest.fail('audio must not be measured without selected passages')

    monkeypatch.setattr(editorial_audio, 'measure_candidate_pauses', unexpected)
    assert worker.main() == 0
    assert json.loads((current/'result.json').read_text())['status'] == 'completed'
    report = json.loads((current/'chapters.json').read_text())
    assert report['reading']['reused'] is SAFE_READER and report['chapters'] == []
    assert stages == (['screen'] if SAFE_READER else ['read', 'synthesis', 'screen'])
