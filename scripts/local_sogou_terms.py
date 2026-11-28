"""Opt-in, offline: shortlist organization-name candidates from a specified Mac user lexicon.

Supports only the observed paired-length/phonetic-ID layout; not a general backup decoder.
No finding means no import, not an empty user dictionary. Never prints individual terms.
"""
import argparse
import json
import os
import re
from pathlib import Path
from struct import unpack_from

SUFFIXES = ('科技','集团','有限公司','工作室','研究院','实验室','传媒','网络公司','教育公司')
NAME = re.compile(r'[\u4e00-\u9fff]{4,25}\Z')


def read_names(data):
    if len(data)<140 or unpack_from('<I',data,16)[0]!=len(data): return set()
    start,cap,used=unpack_from('<III',data,68)
    if not (140<=start<=len(data) and 0<used<=cap<=len(data)-start): return set()
    area=data[start:start+used]; result=set()
    for suffix in SUFFIXES:
        needle=suffix.encode('utf-16le'); pos=0
        while (ix:=area.find(needle,pos))!=-1:
            pos=ix+2; end=ix+len(needle)
            if end+2>len(area): continue
            size=unpack_from('<H',area,end)[0]
            word=end-size; phone=word-6-size
            if size%2 or not 8<=size<=50 or phone<0 or end+2+size>len(area): continue
            if unpack_from('<H',area,word-2)[0]!=size or unpack_from('<H',area,phone)[0]!=size: continue
            if area[phone+2:phone+2+size]!=area[end+2:end+2+size]: continue
            name=area[word:end].decode('utf-16le',errors='replace')
            if NAME.fullmatch(name) and name.endswith(suffix): result.add(name)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('file',type=Path,nargs='+');ap.add_argument('--inbox',type=Path,required=True)
    args=ap.parse_args()
    private=Path(__file__).resolve().parents[1]/'.local'
    out=args.inbox.resolve()
    if not out.is_relative_to(private) or out.name!='ime-term-inbox.json' or not out.parent.is_dir():
        ap.error('Choose the ignored project metadata inbox')
    names=set()
    for f in args.file:
        if f.name!='sgim_gd_usr.bin' or f.is_symlink() or not 140<=f.stat().st_size<=32*1024*1024:
            ap.error('Unsupported user lexicon')
        names.update(read_names(f.read_bytes()))
    keep=sorted(names)[:300]
    tmp=out.with_suffix('.tmp')
    with tmp.open('x') as fp:
        os.fchmod(fp.fileno(),0o600); json.dump({'terms':keep},fp,ensure_ascii=False)
    os.replace(tmp,out)
    print(f'{len(keep)} possible organization terms. No captions or user lexicons changed.')
