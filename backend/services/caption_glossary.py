"""Opt-in local spelling suggestions; no IME, account, cloud or model access."""
import ctypes as ct
from functools import lru_cache
import re
import sys
import unicodedata


@lru_cache(maxsize=1)
def _core():
    if sys.platform != 'darwin':
        return None
    lib = ct.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    p, n = ct.c_void_p, ct.c_long
    for name, args, out in [('CFStringCreateWithCString',[p,ct.c_char_p,ct.c_uint32],p),
                            ('CFStringCreateMutableCopy',[p,n,p],p), ('CFRelease',[p],None),
                            ('CFStringTransform',[p,p,p,ct.c_bool],ct.c_bool),
                            ('CFStringGetCString',[p,ct.c_char_p,n,ct.c_uint32],ct.c_bool)]:
        fn = getattr(lib,name); fn.argtypes=args; fn.restype=out
    return lib, ct.c_void_p.in_dll(lib,'kCFStringTransformMandarinLatin')


@lru_cache(maxsize=8192)
def syllable(char):
    api = _core()
    if api is None: return char
    lib, transform = api
    raw = lib.CFStringCreateWithCString(None,char.encode(),0x08000100)
    tmp = lib.CFStringCreateMutableCopy(None,0,raw)
    try:
        buf = ct.create_string_buffer(100)
        if lib.CFStringTransform(tmp,None,transform,False) and lib.CFStringGetCString(tmp,buf,100,0x08000100):
            return ''.join(c for c in unicodedata.normalize('NFD',buf.value.decode()) if not unicodedata.combining(c))
        return char
    finally:
        lib.CFRelease(tmp);lib.CFRelease(raw)


def suggest(raw, cues):
    names, aliases = set(), {}
    lines = [v.strip() for v in raw.splitlines() if v.strip()]
    if len(lines)>300: raise ValueError('too many terms')
    for line in lines:
        parts = [s.strip() for s in line.split('=>')]
        if len(parts)>2 or any(not 2 <= len(s) <= 60 for s in parts): raise ValueError('invalid term')
        names.add(parts[-1])
        if len(parts)==2:
            if parts[0] in aliases and aliases[parts[0]]!=parts[1]: raise ValueError('ambiguous alias')
            aliases[parts[0]]=parts[1]
    # Terms themselves must never get rewritten to another term.
    if names & aliases.keys(): raise ValueError('canonical name also used as alias')
    lookup = {}
    for name in names:
        if re.fullmatch(r'[\u3400-\u9fff]{3,20}',name):
            key=tuple(map(syllable,name))
            lookup.setdefault(len(name),{}).setdefault(key,set()).add(name)
    exact = re.compile('|'.join(map(re.escape,sorted(aliases.keys()|names,key=len,reverse=True)))) if names else None
    output=[]
    for cue in cues:
        src=cue['text']; result=[]; i=0
        while i<len(src):
            m=exact.match(src,i) if exact else None
            if m:
                result.append(aliases.get(m[0],m[0])); i=m.end(); continue
            for n in sorted(lookup,reverse=True):
                word=src[i:i+n]
                if len(word)!=n or not re.fullmatch(r'[\u3400-\u9fff]+',word): continue
                hits=lookup[n].get(tuple(map(syllable,word)),set())
                if len(hits)==1:
                    replacement=next(iter(hits))
                    if sum(a!=b for a,b in zip(word,replacement))==1 and not any(t in word for t in names):
                        result.append(replacement);i+=n; break
            else:
                result.append(src[i]); i+=1
        final=''.join(result)
        if src!=final: output.append({'id':cue['id'],'before':src,'after':final})
    return output
