"""Verified director variant: a discrete speaker shot, story roles and conservative speech pacing."""
import json
import math
import re

VERSION = 'director_1'


def opening_options(chapter, rows):
    ids={v['id']:n for n,v in enumerate(rows)}
    first=chapter['scenes'][0]; proof=chapter['selection']['evidence']['claim']
    opts=[(first['first'],first['last'])]
    earliest=min(chapter['scenes'],key=lambda s:s['start'])
    a,b=ids[earliest['first']],ids[earliest['last']]
    for n in range(a,b+1):
        duration=rows[n]['end']-rows[a]['start']
        if duration>9: break
        if duration>=1.5 and re.search(r'[。？！!?][”」]?$',rows[n]['text'].strip()):
            opts.append((rows[a]['id'],rows[n]['id']));break
    opts.append((proof['first'],proof['last']))
    return list(dict.fromkeys(opts))


def pace(role, words, seconds):
    # A cut is not permission to accelerate qualifications, figures or already fast speech.
    text = ''.join(words)
    density = len(re.findall(r'[\w]', text)) / max(.1,seconds)
    if role in ('opening','example','payoff','method'):
        return .96 if role == 'payoff' and density >= 5 else 1.
    if (seconds < 3 or density > 5.2 or
            re.search(r'[0-9%％]|百分之|只有|并非|除非|不代表|至少|不一定|不是|不能',text)):
        return 1.
    return 1.12 if density < 3.6 else 1.06


def read_variant(path, data, jid, chapter, base):
    from backend.services.studio.models import Draft
    if path.is_symlink() or not path.is_file() or path.stat().st_size>131072:
        raise ValueError('missing director review')
    r=json.loads(path.read_text())
    if (not chapter.get('second_pass',{}).get('passed') or r.get('version')!=VERSION
        or any(r.get(k)!=v for k,v in [('source',data['source_sha256']),('captions',data['subtitle_sha256']),
                                       ('job',jid),('clip',chapter['id'])])
        or r.get('flow_review') is not True):
        raise ValueError('stale director review')
    rows=data['cues'];ids={v['id']:i for i,v in enumerate(rows)}
    kept={i for s in chapter['scenes'] for i in range(ids[s['first']],ids[s['last']]+1)}
    proof=chapter['selection']['evidence']
    cuts=r['cuts'];seen=set();scenes=[];prev=-1
    if not 3<=len(cuts)<=30:raise ValueError('invalid cut count')
    for n,s in enumerate(cuts):
        a,b=ids[s['first']],ids[s['last']];indices=set(range(a,b+1));role=s['role'];start,end=rows[a]['start'],rows[b]['end']
        if (not indices or not indices<=kept or seen & indices or (n and a<=prev)
            or role not in ('opening','bridge','example','method','payoff')
            or (role=='opening')!=(n==0)
            or any(rows[i+1]['start']-rows[i]['end']>1.25 for i in range(a,b))):
            raise ValueError('invalid director range')
        if n:prev=b
        seen.update(indices)
        tempo=pace(role,[q['text'] for q in rows[a:b+1]],end-start)
        scene={'id':'director'+str(n),'start':start,'end':end,'tempo':tempo,'label':role}
        if n==0:
            if (s['first'],s['last']) not in opening_options(chapter,rows):
                raise ValueError('wrong opening')
            cam=r['camera'];x,y,zoom,hold=(float(cam[k]) for k in ('x','y','zoom','hold'))
            if not(all(math.isfinite(v) for v in (x,y,zoom,hold)) and 0<=x<=1 and 0<=y<=1 and
                   1<zoom<=3 and .6<=hold<=end-start+.001):raise ValueError('bad portrait shot')
            scene.update(punch=zoom,focus_x=x,focus_y=y,portrait_hold=min(hold,end-start))
        scenes.append(scene)
    if cuts[-1]['role']!='payoff' or not all(set(range(ids[q['first']],ids[q['last']]+1))<=seen for q in proof.values()):
        raise ValueError('missing source evidence')
    ordered=sorted(scenes,key=lambda s:s['start'])
    if any(a['end']>b['start']+.001 for a,b in zip(ordered,ordered[1:])):
        raise ValueError('overlapping speech')
    notes=[]
    for v in r.get('relisten',[]):
        if v.get('cue') not in ids or ids[v['cue']] not in seen:continue
        notes.append(v)
    return Draft.model_validate(dict(base,scenes=scenes)).model_dump(),notes
