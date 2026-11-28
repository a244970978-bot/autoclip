import pytest
from types import SimpleNamespace

from backend.services.local_director import pace, opening_options
from backend.services.local_speaker_camera import locate


def test_cadence_protects_evidence_conditions_and_figures():
    assert pace('bridge',['先看看背景。'],6)==1.12
    for text in ('只有这种条件才算。','上涨了12%。'):
        assert pace('bridge',[text],5)==1
    assert pace('example',['例子'],5)==1
    assert pace('payoff',['这是需要慢下来才能听清的限定和特别重要的结论。'],3)==.96


def test_native_question_is_not_the_later_claim():
    rows=[{'id':str(i),'start':i*2,'end':i*2+2,'text':v} for i,v in enumerate(('为什么会这样？','这是标准答案。','改变了方法。'))]
    c={'scenes':[dict(first='0',last='2',start=0)],'selection':{'evidence':{'claim':{'first':'1','last':'1'}}}}
    assert ('0','0') in opening_options(c,rows) and ('1','1') in opening_options(c,rows)


def test_fixed_face_lock_abandons_occlusion_and_ambiguous_talkers():
    import numpy as np
    n=80;k=np.arange(n);p=.1+.08*np.sin(k*.3);pcm=np.repeat(p,1000)
    faces=[[ [.73,.45,.06,.08,1,float(p[i])*6] ] if not 25<=i<32 else [] for i in range(n)]
    cam=locate(faces,pcm,5)
    assert cam and cam['hold']==25/16 and cam['zoom']>2 and cam['x']>.75
    for i in range(n):faces[i].append([.2,.42,.08,.1,1,float(p[i])*6])
    assert locate(faces,pcm,5) is None


def test_cue_suggestions_require_two_agreeing_reads(monkeypatch):
    from backend.services import local_caption_relisten as mod
    monkeypatch.setattr(mod,'simplified',lambda t:t)
    cue=dict(id='a',text='保留错别字',start=1,end=3)
    def read(s):return [dict(text=s,start=1,end=3,prob=.95)]
    assert mod.suggestions([cue],[read('保留措别字'),read('保留措别字')])[0]['after']=='保留措别字'
    assert not mod.suggestions([cue],[read('保留措别字'),read('保留错别字')])
    assert cue['text']=='保留错别字'


def test_director_variant_rejects_stale_receipts_and_missing_evidence(tmp_path):
    import json
    from backend.services.local_director import read_variant
    rows=[dict(id=f'c{i}',text='说明证据。',start=i,end=i+1) for i in range(9)]
    data=dict(cues=rows,source_sha256='src',subtitle_sha256='sub')
    ev={k:{'first':f'c{n}','last':f'c{n+1}'} for k,n in [('claim',1),('support',4),('closing',7)]}
    old=dict(id='T01',second_pass={'passed':True},scenes=[dict(first='c0',last='c8',start=0,end=9)],selection={'evidence':ev})
    base=dict(id='captions',title='example',scenes=[dict(id='s',start=0,end=9)])
    cuts=[dict(first='c1',last='c2',role='opening'),dict(first='c4',last='c5',role='example'),dict(first='c7',last='c8',role='payoff')]
    receipt=dict(source='src',captions='sub',job='j',clip='T01',version='director_1',flow_review=True,cuts=cuts,
                 camera=dict(x=.7,y=.5,zoom=2.4,hold=1.1))
    path=tmp_path/'director.json';path.write_text(json.dumps(receipt))
    draft,_=read_variant(path,data,'j',old,base)
    assert draft['scenes'][0]['punch']==2.4 and all(v['punch']==1 for v in draft['scenes'][1:])
    assert base['scenes'][0].get('punch') is None
    for change in ({'captions':'stale'},{'flow_review':False},{'cuts':[cuts[0],dict(first='c3',last='c3',role='bridge'),cuts[-1]]}):
        path.write_text(json.dumps(dict(receipt,**change)))
        with pytest.raises(ValueError):read_variant(path,data,'j',old,base)
