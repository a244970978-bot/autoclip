"""Use offline mouth landmarks and the same recording's amplitude to select a fixed shot."""
import math
import tempfile
from pathlib import Path
from subprocess import SubprocessError


def locate(frames, pcm, seconds):
    import numpy as np
    tracks=[]
    for k,faces in enumerate(frames):
        used=set()
        for x,y,w,h,confidence,mouth in faces:
            if confidence<.75 or not all(map(math.isfinite,(x,y,w,h,mouth))):continue
            opts=[(math.hypot(t[-1][1]-x,t[-1][2]-y),n) for n,t in enumerate(tracks)
                  if n not in used and k-t[-1][0]<=16]
            dist,n=min(opts,default=(1,-1))
            if dist>max(.022,w*1.15):n=len(tracks);tracks.append([])
            tracks[n].append((k,x,y,w,mouth));used.add(n)
    power=np.array([math.log1p(100*float(np.sqrt(np.mean(pcm[k*1000:(k+1)*1000]**2))))
                    if k*1000<len(pcm) else 0 for k in range(len(frames))])
    ranked=[]
    for t in tracks:
        ar=np.array(t);ks=ar[:,0].astype(int)
        if len(t)<max(15,.54*len(frames)):continue
        mouths=ar[:,-1].clip(0,2)
        if mouths.std()<.03:continue
        rs=[]
        for lag in (-1,0,1):
            z=ks+lag;m=(z>=0)&(z<len(power))
            if power[z[m]].std()<.02 or mouths[m].std()<.02:break
            rs.append(float(np.corrcoef(mouths[m],power[z[m]])[0,1]))
        if len(rs)!=3:continue
        ranked.append((sum(rs)/3,t))
    ranked.sort(key=lambda it:it[0],reverse=True)
    if not ranked:return None
    score,t=ranked[0]
    if score<.45 or len(ranked)>1 and score-ranked[1][0]<.16 or t[0][0]>1 or len(t)<.68*len(frames):return None
    seen={n for n,*_ in t};hold=seconds
    for n in range(len(frames)):
        if all(i not in seen for i in range(n,min(n+4,len(frames)))) and (n+4<=len(frames) or n>max(seen)):
            hold=min(hold,n/16);break
    if hold<.65:return None
    obs=np.array([r for r in t if r[0]<hold*16])
    x,y,w=np.median(obs,axis=0)[1:4]
    if w<.035 or np.ptp(obs[:,1])>.10 or np.ptp(obs[:,2])>.10:return None
    zoom=float(np.clip(.15/w,1.75,2.8))
    return {'x':round(float(np.clip((x*zoom-.5)/(zoom-1),0,1)),4),
            'y':round(float(np.clip((y*zoom-.39)/(zoom-1),0,1)),4),
            'zoom':round(zoom,3),'hold':round(hold,4)}


def detect(video, start, end, run):
    import numpy as np
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    helper=Path(__file__).resolve().parents[2]/'.local/prepared/local-mouth-observations'
    if not helper.is_file() or helper.is_symlink() or not 1.2<=end-start<=12:return None
    before=[get_ffmpeg_path(),'-nostdin','-v','error','-protocol_whitelist','file,pipe','-ss',str(start),'-i',str(video),'-t',str(end-start)]
    try:
        with tempfile.TemporaryDirectory(prefix='director-camera-') as tmp:
            run(before+['-vf','fps=16','-frames:v','192',str(Path(tmp)/'f%03d.jpg')],capture_output=True,check=True,timeout=40)
            imgs=sorted(Path(tmp).glob('f*.jpg'))
            if not imgs:return None
            obs=run([str(helper),*map(str,imgs)],capture_output=True,check=True,timeout=95)
            frames=[[list(map(float,m.split(','))) for m in line.split()[3:]] for line in obs.stdout.decode().splitlines()]
            if len(frames)!=len(imgs):return None
            wav=run(before+['-map','0:a:0','-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True,timeout=30)
            return locate(frames,np.frombuffer(wav.stdout,'<f4'),end-start)
    except FileNotFoundError as exc:
        from backend.core.project_cancellation import ProjectDeleted
        if isinstance(exc,ProjectDeleted): raise
        return None
    except (ValueError,OSError,SubprocessError):return None
