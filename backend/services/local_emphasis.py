"""Reviewed local framing for a single grounded statement. Never changes speech order."""
import ctypes as ct
from functools import lru_cache
import json
import math
import sys


@lru_cache(maxsize=1)
def _converter():
    if sys.platform != 'darwin':
        raise RuntimeError('Local simplified-Chinese conversion needs the prepared macOS runtime')
    from backend.services.caption_glossary import _core
    lib, _ = _core()
    transform = lib.CFStringCreateWithCString(None, b'Traditional-Simplified', 0x08000100)
    if not transform:
        raise RuntimeError('Missing local character transform')
    return lib, transform


def simplified(text):
    if not text or text.isascii():
        return text
    lib, transform = _converter()
    raw = lib.CFStringCreateWithCString(None, text.encode('utf-8'), 0x08000100)
    mutable = lib.CFStringCreateMutableCopy(None, 0, raw)
    try:
        out = ct.create_string_buffer(16*len(text.encode())+32)
        if not (lib.CFStringTransform(mutable, None, transform, False)
                and lib.CFStringGetCString(mutable, out, len(out), 0x08000100)):
            raise ValueError('Local character conversion failed')
        return out.value.decode()
    finally:
        lib.CFRelease(mutable)
        lib.CFRelease(raw)


def apply_emphasis(path, data, jid, chapter, draft):
    """Sidecar is private and derived; don't trust a stale receipt or mutate old reports."""
    if not chapter.get('second_pass', {}).get('passed') or path.is_symlink() or not path.is_file():
        return draft
    try:
        if path.stat().st_size > 2048:
            return draft
        v = json.loads(path.read_text())
        if any(v.get(k) != val for k,val in {'job':jid, 'clip':chapter['id'], 'source':data['source_sha256'],
                                              'captions':data['subtitle_sha256']}.items()):
            return draft
        a,b,x,y,z = (float(v[k]) for k in ('start','end','x','y','zoom'))
        ref = chapter['selection']['evidence']['claim']
        if (not all(map(math.isfinite,(a,b,x,y,z))) or (a,b)!=(ref['start'],ref['end'])
                or not (0<=x<=1 and 0<=y<=1 and 1<z<=1.6 and b-a>=1.2)):
            return draft
        for scene in draft['scenes']:
            if scene['start'] <= a and b+.32 <= scene['end']:
                scene.update(punch=z, focus_x=x, focus_y=y, emphasis=[a,b])
                break
    except (ValueError, OSError, KeyError, TypeError):
        pass
    return draft


def animated_filter(scene, source_width, source_height):
    # Normalize PTS/frame rate first: one output per input frame. One motion
    # expression for preview/export: ramp in on the sentence, ramp out after.
    a,b = (t-scene.start for t in scene.emphasis)
    ramp = f'max(0,min(1,min((on/30-{a:.6f})/0.32,({b+.32:.6f}-on/30)/0.32)))'
    return (f"[0:v]setpts=PTS-STARTPTS,fps=30,zoompan=z='1+{scene.punch-1:.8f}*{ramp}':"
            f"x='(iw-iw/zoom)*{scene.focus_x:.8f}':y='(ih-ih/zoom)*{scene.focus_y:.8f}':"
            f"d=1:s={source_width}x{source_height}:fps=30,setsar=1")
