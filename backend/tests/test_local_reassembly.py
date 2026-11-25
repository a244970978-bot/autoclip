"""No user media; verifiable tones and synthetic editorial inputs."""
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.local_captions_app import create_caption_app
from backend.services.local_captions import CaptionError, atomic, digest, srt_body
from backend.services.local_reassembly import analyze, build_candidate, windows
from backend.services.reassembly_runtime import detect_silence
from backend.tests.test_local_captions import workspace, wait_job  # noqa: F401
from backend.utils.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path

WORDS = [
    '先从一个经历讲起。', '发现复盘从反馈开始。', '咳，题外话，嗓子有点干。',
    '我们把反馈写在同一张表上。', '最快的成长来自每周复盘。', '拿团队沟通举个例子。',
    '每周听取参与者的反馈。', '只有具备完整记录才可比较，不能拿个例下结论。',
]

@pytest.fixture
def cues():
    return [{'id': f'c{i+1:05}', 'start': .3+i*3.6, 'end': 2.95+i*3.6, 'text': txt, 'original_text': txt}
            for i, txt in enumerate(WORDS)]

def proposal(cues):
    ids = [x['id'] for x in cues]
    return {'title': '用反馈做复盘', 'topic': [ids[0], ids[-1]], 'hook': [ids[4], ids[4]],
            'drop': [[ids[2], ids[2]]]}

def test_hook_first_once_and_closing_retained(cues):
    plan = build_candidate(proposal(cues), cues, [(2.95,3.9)], 30)
    assert plan['mode'] == 'hook_first'
    assert [x['first'] for x in plan['scenes']][0] == cues[4]['id']
    assert plan['scenes'][0]['role'] == 'hook'
    assert plan['scenes'][-1]['last'] == cues[-1]['id']
    assert plan['dropped'][0]['first'] == cues[2]['id']
    assert len(plan['removed_silence']) == 1
    gap = plan['removed_silence'][0]
    assert 2.95 < gap['start'] < gap['end'] < 3.9
    covered = [c['id'] for s in plan['scenes'] for c in cues if s['start'] <= c['start'] < s['end']]
    assert covered == [cues[i]['id'] for i in [4,0,1,3,5,6,7]]

def test_transcript_gap_alone_does_not_cut(cues):
    predicted = build_candidate(proposal(cues), cues, [], 30)
    assert not predicted['removed_silence']

def test_padding_does_not_escape_requested_scope(cues):
    scope = (cues[0]['start'], cues[-1]['end'])
    p = analyze(cues, scope, 30, [], lambda *_: {'chapters': [proposal(cues)]})['chapters'][0]
    assert all(scope[0] <= s['start'] < s['end'] <= scope[1] for s in p['scenes'])

@pytest.mark.parametrize('suffix', ['只有持久记录才有用', '不能跳过记录', '这样才能知道原因'])
def test_context_guard_falls_back_to_original_order(cues, suffix):
    cues[4]['text'] = suffix
    p = build_candidate(proposal(cues), cues, [], 30)
    assert p['mode'] == 'source_order' and 'hook_context' in p['warnings']

def test_protects_limitations_even_when_model_marks_them_as_noise(cues):
    cues[2]['text'] = '只有新项目适用，不能用于归档。'
    plan = build_candidate(proposal(cues), cues, [], 30)
    assert plan['dropped'] == [] and 'drop_context' in plan['warnings']
    assert any(s['start'] <= cues[2]['start'] and cues[2]['end'] <= s['end'] for s in plan['scenes'])

def test_overlaps_do_not_get_cut(cues):
    cues[3]['end'] = cues[4]['start'] + .1
    p = build_candidate(proposal(cues), cues, [], 30)
    assert p['mode'] == 'source_order' and p['warnings'] == ['continuous_fallback']
    assert len(p['scenes']) == 1

def test_invented_ids_and_timestamp_are_never_renderable(cues):
    p = proposal(cues)
    p['topic'] = [.2, 28.5]
    with pytest.raises(ValueError):
        build_candidate(p, cues, [], 30)
    p = proposal(cues)
    p['hook'] = ['invented','invented']
    assert build_candidate(p, cues, [], 30)['mode'] == 'source_order'

def test_scope_jobs_do_not_mislabel_failed_model_or_partial_scan(cues):
    def fail(*_):
        raise ValueError('invalid response')
    with pytest.raises(CaptionError, match='analysis_failed'):
        analyze(cues, (0,30), 30, [], fail)
    with pytest.raises(CaptionError, match='analysis_no_cues'):
        analyze(cues, (29,30), 30, [], lambda *_: None)
    timelines = cues + [{**c, 'id': 'other'+c['id'], 'start': c['start']+180, 'end': c['end']+180} for c in cues]
    assert len(list(windows(timelines))) == 2
    report = analyze(timelines, (0,210), 210, [], lambda _, inp: fail() if inp['core_first'].startswith('other')
                     else {'chapters':[proposal(cues),proposal(cues)]})
    assert report['failed_windows'] == 1 and len(report['chapters']) == 1 and report['needs_review']

@pytest.fixture
def material(tmp_path, cues):
    video, srt = tmp_path/'test.mp4', tmp_path/'test.srt'
    # Every cue has its own frequency. The gaps are actual low-level audio, not only subtitle gaps.
    freq = [320+70*i for i in range(len(cues))]
    signal = '+'.join(f'between(t,{c["start"]:.3f},{c["end"]:.3f})*0.12*sin(2*PI*{f}*t)' for c,f in zip(cues,freq))
    boxes = ("drawbox=x=0:y=0:w=36:h=36:c=red:t=fill:enable='between(t,14.7,17.35)',"
             "drawbox=x=0:y=0:w=36:h=36:c=lime:t=fill:enable='between(t,0.3,2.95)'")
    subprocess.run([get_ffmpeg_path(), '-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=30',
        '-f','lavfi','-i', f"aevalsrc='{signal}':s=16000:d=30", '-vf', boxes, '-c:v','libx264','-preset','veryfast','-pix_fmt',
        'yuv420p','-c:a','aac','-movflags','+faststart',str(video)], check=True, capture_output=True, timeout=35)
    srt.write_text(srt_body(cues), encoding='utf-8')
    return video, srt, freq

def test_scoped_silence_is_on_absolute_source_clock(material):
    video, _, _ = material
    pauses = detect_silence(video, (6.0, 15.0), subprocess.run)
    assert len(pauses) >= 2
    assert any(6.53 < a < 6.77 and 7.36 < b < 7.65 for a,b in pauses)
    assert all(6 <= a < b <= 15 for a,b in pauses)

def test_reviewed_reassembly_exports_actual_audio_and_subtitles(workspace, material, cues, monkeypatch):
    video, srt, freq = material
    monkeypatch.setenv('AUTOCLIP_FFMPEG_PATH', get_ffmpeg_path())
    monkeypatch.setenv('AUTOCLIP_FFPROBE_PATH', get_ffprobe_path())
    with video.open('rb') as stream:
        project = workspace.import_stream(stream, 'fixture.mp4', subtitles=srt, expected_sha=digest(video))
    cue_rows = project['cues']
    p = build_candidate(proposal(cue_rows), cue_rows, detect_silence(video, (0,30), subprocess.run), 30)
    assert p['mode'] == 'hook_first' and p['dropped'] and p['removed_silence']
    pid = project['id']
    draft = workspace.plan(pid, project['revision'], {'scenes': [{k:s[k] for k in ('id','start','end')} for s in p['scenes']]})
    with pytest.raises(CaptionError, match='approval_required'):
        workspace.start_job(pid, 'render', approved=False, plan_hash=draft['plan_hash'])
    job = workspace.start_job(pid, 'render', approved=True, plan_hash=draft['plan_hash'])
    assert wait_job(workspace, pid, job['id'])['status'] == 'completed'
    out = workspace.download(pid, job['id'], 'video')
    samples = subprocess.check_output([get_ffmpeg_path(),'-v','error','-i',str(out),'-map','0:a:0','-ac','1','-ar','16000','-f','f32le','-'],timeout=30)
    x = np.frombuffer(samples,dtype='<f4')
    def peak(t):
        pcm = x[int(t*16000):int((t+.5)*16000)]
        return np.argmax(np.abs(np.fft.rfft(pcm)))*16000/len(pcm)
    plan = json.loads(workspace.download(pid, job['id'], 'plan').read_text())
    subs = workspace.download(pid, job['id'], 'subtitles').read_text()
    assert abs(peak(.4)-freq[4]) < 5
    assert abs(peak(3.5)-freq[0]) < 5
    def color_at(t):
        raw = subprocess.check_output([get_ffmpeg_path(),'-v','error','-ss',str(t),'-i',str(out),
            '-vf','crop=2:2:8:8,format=rgb24','-frames:v','1','-f','rawvideo','-'],timeout=25)
        return np.frombuffer(raw,dtype=np.uint8).reshape(-1,3).mean(axis=0)
    assert color_at(.4)[0] > 180 and color_at(.4)[1] < 65
    assert color_at(3.5)[1] > 180 and color_at(3.5)[0] < 65
    assert WORDS[2] not in subs and subs.index(WORDS[4]) < subs.index(WORDS[0]) < subs.index(WORDS[-1])
    assert len(plan['draft']['scenes']) >= 3
    assert digest(workspace.path(pid)/'raw/input.mp4') == digest(video)

def test_http_analysis_job_and_subtitle_revision_boundary(workspace, material):
    video, srt, _ = material
    with video.open('rb') as stream:
        project = workspace.import_stream(stream, 'fixture.mp4', subtitles=srt)
    origin = 'http://127.0.0.1:19784'
    headers = {'Origin': origin, 'X-Autoclip-Local': '1'}
    base = '/api/projects/'+project['id']
    with TestClient(create_caption_app(workspace), base_url=origin, client=('127.0.0.1',4321)) as c:
        assert c.post(base+'/analysis',json={'start':2,'end':3},headers=headers).status_code == 422
        assert c.post(base+'/analysis',json={},headers={'Origin':'https://example.invalid',
                                                       'X-Autoclip-Local':'1'}).status_code == 403
        response = c.post(base+'/analysis',json={},headers=headers)
        assert response.status_code == 200
        jid = response.json()['id']
        # No model is prepared in unit test env; this is never silently treated as an empty success.
        assert wait_job(workspace, project['id'], jid)['error'] == 'analysis_model_unavailable'
        assert c.get(base+'/analysis').json()['analysis'] is None
        data = workspace.load(project['id'])
        data['jobs'][-1].update(status='completed',subtitle_sha256=data['subtitle_sha256'])
        folder = workspace.path(project['id'])/'metadata'/('job-'+jid)
        atomic(folder/'chapters.json',{'chapters':[]})
        workspace.put(data)
        assert c.get(base+'/analysis').json()['analysis']['job_id'] == jid
        assert c.post(base+'/captions',json={'revision':1,'edits':[{'id':'c00001','text':'修改'}]},headers=headers).is_success
        assert c.get(base+'/analysis').json()['analysis'] is None
