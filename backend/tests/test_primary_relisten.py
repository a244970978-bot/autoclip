"""The default cut can flag a disputed word without altering captions or framing."""
import json


def example():
    data = dict(source_sha256='video', subtitle_sha256='subs', cues=[
        dict(id='c1', text='original', start=0, end=2),
        dict(id='c2', text='removed', start=2, end=4)])
    chapter = dict(id='T01', first='c1', last='c2', scenes=[dict(first='c1', last='c1', start=0, end=2)])
    return data, chapter


def test_only_retained_cues_and_exact_source_are_delivered(tmp_path):
    from backend.services.primary_relisten import safe_notes, write_notes
    data, chapter = example()
    def decode(video, cues, run):
        return [dict(cue=v['id'], before=v['text'], after='alternative', start=v['start'], end=v['end'],
                     basis='two_local_decodes') for v in cues]
    write_notes(tmp_path, data, 'job', chapter, None, None, decode=decode)
    rows = safe_notes(tmp_path, data, 'job', 'T01')
    assert [n['cue'] for n in rows] == ['c1']
    assert data['cues'][0]['text'] == 'original'
    assert not safe_notes(tmp_path, data, 'wrong', 'T01')
    assert not safe_notes(tmp_path, data, 'job', 'T02')
    assert not safe_notes(tmp_path, dict(data, subtitle_sha256='changed'), 'job', 'T01')


def test_failure_and_corrupt_receipt_are_not_a_clean_transcript(tmp_path):
    from backend.services.primary_relisten import safe_notes, write_notes
    from backend.services.local_captions import CaptionError
    import pytest
    data, chapter = example()
    def unavailable(*args):
        raise OSError('unavailable')
    write_notes(tmp_path, data, 'job', chapter, None, None, decode=unavailable)
    path = tmp_path/'primary-relisten.json'
    assert json.loads(path.read_text())['complete'] is False
    path.write_text('null')
    assert not safe_notes(tmp_path, data, 'job', 'T01')
    def cancelled(*args):
        raise CaptionError('cancelled')
    with pytest.raises(CaptionError):
        write_notes(tmp_path, data, 'job', chapter, None, None, decode=cancelled)
