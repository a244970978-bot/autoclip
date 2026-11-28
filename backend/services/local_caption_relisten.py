"""Compare two local re-decodes against the original. Suggestions never alter source clocks."""
from difflib import SequenceMatcher
import re
from backend.services.local_emphasis import simplified

def norm(text):
    return ''.join(c for c in simplified(text).casefold() if c.isalnum())


def suggestions(cues, readings):
    source=[]; owners=[]
    for cue in cues:
        s=norm(cue['text']); source.extend(s); owners.extend([cue]*len(s))
    original=''.join(source)
    def compare(words):
        other=[]; timing=[]
        for w in words:
            s=norm(w['text']);other.extend(s)
            timing.extend([(w['start'],w['end'],w['prob'])]*len(s))
        target=''.join(other);ops=SequenceMatcher(None, original,target,autojunk=False).get_opcodes()
        found={}
        for n,(tag,i,j,a,b) in enumerate(ops):
            if tag!='replace' or not 1<=j-i<=6 or not 1<=b-a<=6 or owners[i]['id']!=owners[j-1]['id']:continue
            if not(0<n<len(ops)-1 and ops[n-1][0]==ops[n+1][0]=='equal'
                   and ops[n-1][2]-ops[n-1][1]>=1 and ops[n+1][2]-ops[n+1][1]>=1):continue
            q=owners[i]
            if abs((timing[a][0]+timing[b-1][1]-q['start']-q['end'])/2)>2:continue
            found[i,j]=(target[a:b],min(t[2] for t in timing[a:b]))
        return found
    both=[compare(w) for w in readings];out=[]
    if len(both)!=2:return []
    for (i,j),(new,pr) in both[0].items():
        hit=both[1].get((i,j))
        if hit is None or hit[0]!=new or min(pr,hit[1])<.50:continue
        cue=owners[i];old=original[i:j];before=simplified(cue['text'])
        if before.count(old)!=1:continue
        out.append({'cue':cue['id'],'start':cue['start'],'end':cue['end'],'before':before,
                    'after':before.replace(old,new,1),'basis':'two_local_decodes','min_probability':round(min(pr,hit[1]),3)})
    return out


def review_notes(cues, readings):
    """Only put agreed alternatives on screen. Disagreement is an audition, not a guessed quote."""
    if len(readings)!=2:return []
    proposals={v['cue']:v for v in suggestions(cues,readings)}; notes=[]
    for c in cues:
        if c['id'] in proposals:
            notes.append(proposals[c['id']]);continue
        alts=[norm(''.join(w['text'] for w in words if c['start']<=(w['start']+w['end'])/2<c['end'])) for words in readings]
        original=norm(c['text'])
        if (len(original)>=3 and min(map(len,alts))>=2 and
                max(SequenceMatcher(None,original,v,autojunk=False).ratio() for v in alts)<.73):
            notes.append(dict(cue=c['id'],start=c['start'],end=c['end'],before=simplified(c['text']),after='',basis='listen'))
    return notes


def relisten(video, cues, run):
    """Bounded CPU re-decode using the installed, pinned Whisper. No automatic replacement."""
    import json, math
    from pathlib import Path
    import numpy as np
    from faster_whisper import WhisperModel
    from backend.services.local_captions import digest
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    root=Path(__file__).resolve().parents[2];m=root/'.local/downloads/whisper'
    pins=json.loads((root/'backend/assets/local_captions/runtime-pins.json').read_text())['whisper_files']
    if not cues or not all((m/f).is_file() and digest(m/f)==h for f,h in pins.items()):return None
    lo,hi=cues[0]['start'],cues[-1]['end']
    if hi-lo>158:return None
    model=WhisperModel(str(m),device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
    passes=[]
    for sec,guard in ((8,1.4),(12,1.8)):
        words=[]
        for start in range(math.floor(lo),math.floor(hi)+1,sec):
            a=max(0,start-guard);b=min(hi+1.8,start+sec+guard)
            pcm=run([get_ffmpeg_path(),'-nostdin','-v','error','-protocol_whitelist','file,pipe','-ss',str(a),
                     '-i',str(video),'-t',str(b-a),'-map','0:a:0','-ac','1','-ar','16000','-f','f32le','-'],
                    capture_output=True,timeout=40,check=True)
            segs,_=model.transcribe(np.frombuffer(pcm.stdout,'<f4'),language='zh',beam_size=5,temperature=0,
                                     word_timestamps=True,condition_on_previous_text=False,vad_filter=False,
                                     hallucination_silence_threshold=1.2)
            for s in segs:
                for w in s.words:
                    if start<=a+(w.start+w.end)/2<start+sec:
                        words.append(dict(start=w.start+a,end=w.end+a,text=simplified(w.word.strip()),prob=w.probability))
        passes.append(words)
    return review_notes(cues,passes)
