import sys
from copy import deepcopy
import pytest

from backend.services.caption_glossary import suggest
from backend.services.editorial_polish import polish_report


def test_lexicon_suggests_without_writing_and_rejects_ambiguous_alias():
    rows=[{'id':'s','text':'明晨科技 与 明辰科技'}]
    assert suggest('明晨科技 => 明辰科技',rows)[0]['after']=='明辰科技 与 明辰科技'
    assert rows[0]['text'].startswith('明晨')
    if sys.platform=='darwin':
        assert suggest('明辰科技',rows)[0]['after']=='明辰科技 与 明辰科技'
        assert not suggest('明辰科技\n明宸科技',rows)
    with pytest.raises(ValueError): suggest('甲乙 => 甲丙\n甲乙 => 甲丁',rows)


def fixture(text):
    cues=[{'id':str(i),'start':i,'end':i+(.4 if i==2 else .9),'text':v} for i,v in enumerate(text)]
    c={'id':'T01','first':'0','last':'6','start':0,'end':7,'editorial_review':'passed','removed_silence':[],
       'warnings':[],'dropped':[],'selection':{'evidence':{k:{'first':str(i),'last':str(i)}
       for i,k in [(0,'claim'),(4,'support'),(6,'closing')]}},
       'scenes':[{'id':'s','first':'0','last':'6','start':0,'end':7,'role':'close'}]}
    return cues,{'chapters':[c],'selection':{}}


@pytest.mark.parametrize('question,approved,expected',[(False,True,1),(True,True,0),(False,False,0)])
def test_polish_filler_joins_accept_or_reject(question,approved,expected):
    cues,report=fixture(['观点','继续吗？' if question else '继续介绍','嗯','方法','证据','结束','总结'])
    before=deepcopy(report)
    def call(system,payload,**kwargs):
        if 'options' in payload: return {'drop':['2']}
        return dict.fromkeys(('flow','meaning','opening','ending'),approved)
    r=polish_report(report,cues,lambda r:[],call,lambda _: {},lambda *args:None)
    assert before==report
    item=r['chapters'][0]; assert item['second_pass']['passed']==approved
    assert item['second_pass']['filler']==expected
    assert r['recommended'] is None  # requires a whole-source eligible moved hook
    assert len({s['id'] for s in item['scenes']}) == len(item['scenes'])
    if expected: assert all(not s['start']<=2<s['end'] for s in item['scenes'])


def test_sogou_parser_requires_paired_lengths_and_matching_phone_ids():
    from scripts.local_sogou_terms import read_names
    import struct
    word='明辰科技'.encode('utf-16le')
    flags=b'\x08\x00'; phone=b'\x51\x00'*4
    entry=flags+phone+b'\x14\x00'+flags+word+flags+phone
    data=bytearray(140)+entry
    struct.pack_into('<I',data,16,len(data));struct.pack_into('<III',data,68,140,len(entry),len(entry))
    assert read_names(data)=={'明辰科技'}
    data[-1]=9
    assert read_names(data)==set()
    assert not read_names(b'not a user dictionary')


def test_final_hook_comparison_blind_on_actual_opening_and_exact_current_edits():
    from backend.services.editorial_finalists import live_order, rank_finalists
    cues = [{'id':str(i), 'text':s} for i,s in enumerate(['正文甲','开场甲','收束甲','正文乙','开场乙','收束乙'])]
    def clip(k, offset):
        def cut(i, role):
            return {'role':role,'first':str(i+offset),'last':str(i+offset)}
        return {'id':k,'mode':'hook_first','second_pass':{'passed':True,'edit_hash':str(offset)*64},
                'scenes':[cut(1,'hook'),cut(0,'body'),cut(2,'close')],
                'selection':{'audience':'听众','payoff':'结论','evidence':{'support':{'quote':'实测佐证'}}}}
    a,b=clip('T01',0),clip('T02',3)
    chapters=[a,b]
    def prefer_second(system,payload,**opts):
        cards=payload['cards']
        assert set(cards).isdisjoint({'T01','T02'})
        assert {c['opening'] for c in cards.values()} == {'开场甲','开场乙'}
        key=next(k for k,v in cards.items() if v['ending']=='收束乙')
        return {'order':[key,*[h for h in cards if h!=key]]}
    receipt=rank_finalists(chapters,cues,prefer_second)
    assert live_order(receipt,chapters)==([b,a],'model')
    b['second_pass']['edit_hash']='new'
    assert live_order(receipt,chapters)==([a,b],'source_rank')
    for bad in [None, {'order':['fake','fake']},'not-a-ranking']:
        bad_receipt=rank_finalists(chapters,cues,lambda *args,**kwargs:bad)
        assert live_order(bad_receipt,chapters)==([a,b],'source_rank')


@pytest.mark.parametrize('body_ok,final_ok,n_calls', [(False, True, 1), (True, False, 2), (True, True, 2)])
def test_second_pass_rechecks_same_body_before_accepting_the_actual_hook_order(body_ok, final_ok, n_calls):
    from backend.services.editorial_polish import reviewed_order_hash
    cues,report=fixture(['先讲需求','前提','实际结论','举个例子','案例','所以上述','收束'])
    ch=report['chapters'][0]
    def cut(a,b,role):
        return dict(id=f's{a}',first=str(a),last=str(b),start=cues[a]['start'],end=cues[b]['end'],role=role)
    ch.update(mode='hook_first',scenes=[cut(2,2,'hook'),cut(0,1,'body'),cut(3,6,'close')])
    orders=[]
    def inspect(system,payload,**opts):
        roles=[scene[0] for scene in payload['edit']]
        orders.append(roles)
        return dict.fromkeys(('flow','meaning','opening','ending'),final_ok if roles[0]=='hook' else body_ok)
    result=polish_report(report,cues,lambda _: [],inspect,
                         lambda _: pytest.fail('no face detector may approve a single-speaker crop'),lambda *args:None)
    edited=result['chapters'][0]
    assert len(orders)==n_calls and orders[0]==['body','body','close']
    if n_calls==2: assert orders[-1]==['hook','body','close']
    assert edited['second_pass']['passed']==(body_ok and final_ok)
    assert edited['second_pass']['edit_hash']==(reviewed_order_hash(edited['scenes']) if body_ok and final_ok else None)
