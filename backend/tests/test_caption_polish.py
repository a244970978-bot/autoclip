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
    assert (r['recommended'] is not None)==approved
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
