"""Self-generated media only: no real transcripts, credentials or cloud providers."""
import io
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from backend.services.local_captions import CaptionError, CaptionWorkspace, caption_track, digest
from backend.local_captions_app import ASSETS, create_caption_app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOCLIP_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('AUTOCLIP_APP_DIR', str(tmp_path))
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'test.db'))
    work = CaptionWorkspace(tmp_path)
    yield work
    work.close()


@pytest.fixture(scope='module')
def fixture_media(tmp_path_factory):
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    path = tmp_path_factory.mktemp('local-caption-fixture')
    video = path / 'source.mp4'
    subprocess.run([get_ffmpeg_path(), '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x180:rate=17:duration=25', '-f', 'lavfi', '-i',
                    'sine=frequency=400:sample_rate=48000:duration=25', '-c:v', 'libx264',
                    '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-y', str(video)],
                   check=True, capture_output=True, timeout=30)
    srt = path / 'source.srt'
    srt.write_text('1\n00:00:00,000 --> 00:00:05,000\n第一段测试\n\n2\n00:00:05,000 --> 00:00:10,000\n第二段测试\n\n3\n00:00:10,000 --> 00:00:15,000\n第三段测试\n', encoding='utf-8')
    return video, srt


@pytest.fixture
def project(workspace, fixture_media):
    video, srt = fixture_media
    with video.open('rb') as f:
        return workspace.import_stream(f, 'self-made.mp4', subtitles=srt, expected_sha=digest(video))


def plan(workspace, data, subtitles=True, aspect='original'):
    return workspace.plan(data['id'], data['revision'], {'aspect': aspect, 'subtitles': subtitles,
        'subtitle_style': 'clean', 'scenes': [{'id': 'third', 'start': 10, 'end': 12},
        {'id': 'first', 'start': 0, 'end': 2}, {'id': 'second', 'start': 5, 'end': 7}]})


def wait_job(workspace, pid, jid):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        job = next(j for j in workspace.load(pid)['jobs'] if j['id'] == jid)
        if job['status'] not in ('queued', 'running'):
            return job
        time.sleep(.05)
    raise AssertionError('bounded local job did not finish')


def test_caption_preview_and_export_mapping_share_real_source_timestamps(workspace, project):
    data = plan(workspace, project)
    track = caption_track(data['cues'], data['draft'], data['info'])
    assert track['enabled'] is True
    assert [(c['start'], c['end'], c['text']) for c in track['output']] == [
        (0, 2, '第三段测试'), (2, 4, '第一段测试'), (4, 6, '第二段测试')]
    assert [c['start'] for c in track['source']] == [10, 0, 5]
    assert track['duration'] == 6


def test_corrections_preserve_original_times_and_revision_history(workspace, project):
    before = workspace.path(project['id']) / 'raw/asr-original.srt'
    original = digest(before)
    data = workspace.edit_captions(project['id'], 1, [{'id': 'c00001', 'text': '第一段已校对'}])
    cue = data['cues'][0]
    assert cue['start'] == 0 and cue['end'] == 5
    assert cue['original_text'] == '第一段测试' and cue['text'] == '第一段已校对'
    assert digest(before) == original
    for version in [1, 2]:
        assert (before.parent.parent / f'metadata/transcript-{version:06}.json').is_file()
    with pytest.raises(CaptionError, match='revision_conflict'):
        workspace.edit_captions(project['id'], 1, [])


def test_edits_invalidate_exact_approval(workspace, project):
    saved = plan(workspace, project)
    workspace.edit_captions(saved['id'], saved['revision'], [{'id': 'c00001', 'text': '修改后的字幕'}])
    with pytest.raises(CaptionError, match='approval_required'):
        workspace.start_job(saved['id'], 'render', plan_hash=saved['plan_hash'], approved=True)


@pytest.mark.parametrize('a,b', [(-1, 1), (1, 1), (2, 1), (0, 100), (float('nan'), 2), (0, float('inf'))])
def test_bad_intervals_rejected_before_render(workspace, project, a, b):
    with pytest.raises(ValueError):
        workspace.plan(project['id'], 1, {'scenes': [{'id': 's1', 'start': a, 'end': b}]})
    assert workspace.load(project['id'])['jobs'] == []


@pytest.mark.parametrize('edit', [{'id': 'missing', 'text': '错误'}, {'id': 'c00001', 'text': ''},
                                {'id': 'c00001', 'text': 'a\n\nb'}, {'text': 'missing id'}])
def test_invalid_edits_do_not_mutate_original(workspace, project, edit):
    before = workspace.load(project['id'])
    with pytest.raises(CaptionError):
        workspace.edit_captions(project['id'], 1, [edit])
    assert workspace.load(project['id']) == before


def test_render_needs_explicit_approval(workspace, project):
    data = plan(workspace, project)
    for approved, sha in [(False, data['plan_hash']), (True, 'f' * 64), (True, None)]:
        with pytest.raises(CaptionError, match='approval_required'):
            workspace.start_job(data['id'], 'render', approved=approved, plan_hash=sha)


@pytest.mark.parametrize('subtitles', [True, False])
@pytest.mark.parametrize('aspect', ['original', 'portrait'])
def test_actual_studio_render_downloads_video_srt_plan_and_preserves_source(workspace, project, subtitles, aspect):
    data = plan(workspace, project, subtitles, aspect)
    source = workspace.path(data['id']) / 'raw/input.mp4'
    before = digest(source)
    job = workspace.start_job(data['id'], 'render', plan_hash=data['plan_hash'], approved=True)
    result = wait_job(workspace, data['id'], job['id'])
    assert result['status'] == 'completed', result
    video = workspace.download(data['id'], job['id'], 'video')
    assert video.stat().st_size > 1000
    from backend.services.publish_export import _probe
    info = _probe(video)
    assert (info['width'], info['height']) == ((1080, 1920) if aspect == 'portrait' else (320, 180))
    srt = workspace.download(data['id'], job['id'], 'subtitles').read_text()
    assert '第三段测试' in srt and srt.index('第三段测试') < srt.index('第一段测试')
    report = json.loads(workspace.download(data['id'], job['id'], 'plan').read_text())
    assert report['plan_hash'] == data['plan_hash'] and report['draft']['subtitles'] == subtitles
    assert digest(source) == before


def test_changed_source_cannot_be_mislabeled_success(workspace, project):
    data = plan(workspace, project)
    source = workspace.path(data['id']) / 'raw/input.mp4'
    with source.open('ab') as f:
        f.write(b'changed')
    job = workspace.start_job(data['id'], 'render', plan_hash=data['plan_hash'], approved=True)
    assert wait_job(workspace, data['id'], job['id'])['status'] == 'failed'
    with pytest.raises(CaptionError):
        workspace.download(data['id'], job['id'], 'video')


def test_cancel_owned_worker_is_not_success_and_keeps_source(workspace, project, monkeypatch):
    data = plan(workspace, project)
    popen = subprocess.Popen
    def slow_worker(cmd, **kw):
        return popen([sys.executable, '-c', 'import time;time.sleep(60)'], **kw)
    monkeypatch.setattr('backend.services.local_captions.subprocess.Popen', slow_worker)
    job = workspace.start_job(data['id'], 'render', plan_hash=data['plan_hash'], approved=True)
    deadline = time.monotonic() + 5
    while workspace.active['process'] is None:
        assert time.monotonic() < deadline
        time.sleep(.01)
    proc = workspace.active['process']
    workspace.cancel(data['id'], job['id'])
    assert wait_job(workspace, data['id'], job['id'])['status'] == 'cancelled'
    assert proc.poll() is not None
    assert (workspace.path(data['id']) / 'raw/input.mp4').is_file()


def test_insufficient_disk_rolls_back_import(workspace, fixture_media, monkeypatch):
    monkeypatch.setattr(workspace, 'space', lambda _: (_ for _ in ()).throw(CaptionError('disk_full')))
    with pytest.raises(CaptionError, match='disk_full'), fixture_media[0].open('rb') as f:
        workspace.import_stream(f, 'input.mp4', subtitles=fixture_media[1])
    assert workspace.listing() == []


def test_invalid_media_rolls_back_import(workspace):
    with pytest.raises(Exception):
        workspace.import_stream(io.BytesIO(b'not an mp4'), 'broken.mp4')
    assert workspace.listing() == []


def test_missing_local_model_fails_without_success_or_download(workspace, fixture_media):
    with fixture_media[0].open('rb') as stream:
        data = workspace.import_stream(stream, 'synthetic.mp4')
    job = data['jobs'][0]
    result = wait_job(workspace, data['id'], job['id'])
    assert result['status'] == 'failed' and result['error'] == 'model_unavailable'
    assert not workspace.load(data['id'])['cues']


def test_restart_marks_unfinished_job_failed_and_retires_dispatch(workspace, project):
    data = workspace.load(project['id'])
    jid = 'a' * 32
    data['jobs'].append({'id': jid, 'status': 'running', 'kind': 'render'})
    workspace.put(data)
    marker = workspace.path(data['id']) / 'metadata' / ('job-' + jid) / 'dispatch.ready'
    marker.parent.mkdir()
    marker.touch()
    other = CaptionWorkspace(workspace.root)
    try:
        assert other.load(data['id'])['jobs'][0]['status'] == 'failed'
        assert not marker.exists() and marker.with_suffix('.interrupted').is_file()
    finally:
        other.close()


def test_enospc_on_actual_copy_cleans_partial_project(workspace, fixture_media, monkeypatch):
    original_open = Path.open
    class DiskFull:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def write(self, _block): raise OSError(28, 'injected ENOSPC')
    def open_file(path, mode='r', *args, **kwargs):
        if path.name == 'input.mp4' and mode == 'xb':
            return DiskFull()
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_file)
    before = digest(fixture_media[0])
    with fixture_media[0].open('rb') as stream, pytest.raises(OSError) as caught:
        workspace.import_stream(stream, 'source.mp4', subtitles=fixture_media[1])
    assert caught.value.errno == 28
    assert workspace.listing() == [] and digest(fixture_media[0]) == before


def test_local_http_boundary_ranges_and_assets(workspace, project):
    origin = 'http://127.0.0.1:19784'
    with TestClient(create_caption_app(workspace), base_url=origin, client=('127.0.0.1', 1234)) as client:
        page = client.get('/')
        assert page.status_code == 200 and "connect-src 'self'" in page.headers['content-security-policy']
        assert client.get('/assets/font.ttf').status_code == 200
        assert client.get('/api/projects', headers={'Host': 'evil.example'}).status_code == 403
        assert client.get('/api/projects', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        assert client.post(f"/api/projects/{project['id']}/plan", json={}).status_code == 403
        assert client.post(f"/api/projects/{project['id']}/plan", json={}, headers={
            'Origin': 'https://evil.example', 'X-Autoclip-Local': '1'}).status_code == 403
        media = client.get(f"/api/projects/{project['id']}/source", headers={'Range': 'bytes=0-99'})
        assert media.status_code == 206 and len(media.content) == 100
        assert client.get('/api/projects/not-a-real-project').status_code == 404
        body = {'revision': 1, 'scenes': [{'id': 's1', 'start': 0, 'end': 2}]}
        saved = client.post(f"/api/projects/{project['id']}/plan", json=body,
                            headers={'Origin': origin, 'X-Autoclip-Local': '1'})
        assert saved.status_code == 200 and saved.json()['draft']['subtitles'] is True


def test_all_existing_locales_have_same_caption_keys_and_assets_are_local():
    locales = json.loads((ASSETS / 'locales.json').read_text())
    expected = {p.stem for p in (ASSETS.parents[2] / 'frontend/src/i18n/locales').resolve().glob('*.json')}
    assert set(locales) == expected and len(locales) == 13
    assert all(set(messages) == set(locales['zh']) and all(messages.values()) for messages in locales.values())
    for name in ['index.html', 'style.css', 'app.js']:
        source = (ASSETS / name).read_text()
        assert 'https://' not in source and 'http://' not in source
        assert 'innerHTML' not in source


def test_one_click_chains_existing_transcription_without_browser_and_never_renders(workspace, fixture_media):
    # Exercise the real scheduler and private dispatch with synthetic broker results.
    workspace.profile = Path('fixture-broker')
    origin = 'http://127.0.0.1:19784'
    headers = {'Origin': origin, 'X-AutoClip-Local': '1'}
    with TestClient(create_caption_app(workspace), base_url=origin, client=('127.0.0.1', 1234)) as client:
        with fixture_media[0].open('rb') as stream:
            data = workspace.import_stream(stream, 'synthetic.mp4')
        pid = data['id']
        transcribe, = data['jobs']
        route = f'/api/projects/{pid}/one-click'
        assert client.post(route).status_code == 403
        for _ in range(2):
            response = client.post(route, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()['job']['id'] == transcribe['id']
            assert response.json()['job']['one_click'] is True
        source = workspace.path(pid)
        (source / 'raw/input.srt').write_bytes(fixture_media[1].read_bytes())
        tp = source / 'metadata' / ('job-' + transcribe['id'])
        (tp / 'result.json').write_text('{"status":"completed","seconds":1}')
        # No HTTP poll, click, or request drives the transition.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            fresh = workspace.load(pid)
            if len(fresh['jobs']) > 1:
                break
            time.sleep(.03)
        first, analysis = fresh['jobs']
        assert first['status'] == 'completed' and first['one_click_next'] == analysis['id']
        assert analysis['kind'] == 'analyze' and analysis['one_click'] is True
        ap = source / 'metadata' / ('job-' + analysis['id'])
        assert json.loads((ap / 'task.json').read_text())['scope'] == [0, fresh['info']['duration']]
        assert client.post(route, headers=headers).json()['job']['id'] == analysis['id']
        assert fresh['plan_hash'] is None and fresh['cues']
        assert digest(source / 'raw/input.mp4') == digest(fixture_media[0])
        assert not list(source.glob('output/**/*.mp4'))
        # The page can still know only the ASR ID at the instant of handoff.
        workspace.cancel(pid, transcribe['id'])
        (ap / 'result.json').write_text('{"status":"completed","seconds":1}')
        assert wait_job(workspace, pid, analysis['id'])['status'] == 'cancelled'


@pytest.mark.parametrize('asr_state', ['failed', 'cancelled'])
def test_one_click_does_not_analyze_if_local_asr_fails_or_is_cancelled(workspace, fixture_media, asr_state):
    workspace.profile = Path('fixture-broker')
    with fixture_media[0].open('rb') as stream:
        data = workspace.import_stream(stream, 'synthetic.mp4')
    pid = data['id']
    job = workspace.one_click(pid)
    assert job['one_click'] is True
    if asr_state == 'cancelled':
        workspace.cancel(pid, job['id'])
    path = workspace.path(pid) / 'metadata' / ('job-' + job['id'])
    (path / 'result.json').write_text('{"status":"failed","error":"asr_failed"}')
    assert wait_job(workspace, pid, job['id'])['status'] == asr_state
    first, = workspace.load(pid)['jobs']
    assert not first.get('one_click_next')
    assert not list(workspace.path(pid).glob('output/**/*.mp4'))


def test_one_click_candidates_are_exact_caption_bound_and_read_only(workspace, project):
    pid = project['id']
    saved = plan(workspace, project, subtitles=False)
    workspace.profile = Path('fixture-broker')
    origin = 'http://127.0.0.1:19784'
    headers = {'Origin': origin, 'X-AutoClip-Local': '1'}
    with TestClient(create_caption_app(workspace), base_url=origin, client=('127.0.0.1', 1234)) as client:
        base = f'/api/projects/{pid}'
        response = client.post(base + '/one-click', headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()['job']
        assert job['kind'] == 'analyze' and job['one_click'] is True
        chapter = {
            'id': 'T01', 'mode': 'hook_first', 'editorial_review': 'passed',
            'editorial_checks': {'outcome': 'hook_accepted', 'checks': dict.fromkeys(
                ['flow', 'meaning', 'opening', 'ending'], True)},
            'scenes': [{'id': 's1', 'start': 5, 'end': 7, 'role': 'hook'},
                       {'id': 's2', 'start': 0, 'end': 2, 'role': 'body'},
                       {'id': 's3', 'start': 10, 'end': 12, 'role': 'close'}],
        }
        report = {'analysis_version': 'editorial_v1', 'recommended': 'T01', 'chapters': [chapter,
                  {**chapter, 'id': 'T02', 'mode': 'source_order'},
                  {**chapter, 'id': 'T03', 'editorial_review': 'failed'},
                  {**chapter, 'id': 'T04', 'editorial_checks': {'outcome': 'hook_accepted', 'checks': {'flow': True}}}]}
        path = workspace.path(pid) / 'metadata' / ('job-' + job['id'])
        (path / 'chapters.json').write_text(json.dumps(report))
        (path / 'result.json').write_text('{"status":"completed","seconds":1}')
        assert wait_job(workspace, pid, job['id'])['status'] == 'completed'
        analysis = base + '/analysis'
        assert client.get(analysis, params={'job_id': '0'*32}).json()['analysis'] is None
        assert client.get(analysis, params={'job_id': job['id']}).json()['analysis']['previewable'] == ['T01']
        candidate = f'{analysis}/{job["id"]}/candidates/'
        for cid in ('T02', 'T03', 'T04', 'T05'):
            assert client.get(candidate + cid + '/preview').status_code >= 400
        res = client.get(candidate + 'T01/preview')
        assert res.status_code == 200, res.text
        preview = res.json()
        assert preview['draft']['subtitles'] and preview['track']['enabled']
        assert [s['start'] for s in preview['draft']['scenes']] == [5, 0, 10]
        assert [(s['start'], s['end'], s['text']) for s in preview['track']['output']] == [
            (0, 2, '第二段测试'), (2, 4, '第一段测试'), (4, 6, '第三段测试')]
        fresh = workspace.load(pid)
        assert (fresh['draft'], fresh['plan_hash'], fresh['revision']) == (
            saved['draft'], saved['plan_hash'], saved['revision'])
        assert all(j['kind'] != 'render' for j in fresh['jobs'])
        assert client.post(base + '/export', json={'plan_hash': fresh['plan_hash']}, headers=headers).status_code == 409
        workspace.edit_captions(pid, fresh['revision'], [{'id': fresh['cues'][0]['id'], 'text': '校订测试'}])
        assert client.get(candidate + 'T01/preview').status_code >= 400
