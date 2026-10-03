"""Crash/hold the real save path at its atomic replace, in a real child process."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shared_platform.publication_r2_review import decide

root,mode,ready,raw_body=sys.argv[1:]
original_replace=os.replace


def intercepted_replace(*args,**kwargs):
    if mode=='before_replace':
        os._exit(77)
    if mode=='hold':
        Path(ready).write_text('writer-held',encoding='utf-8')
        if sys.stdin.buffer.read(1)!=b'x':
            os._exit(79)
    result=original_replace(*args,**kwargs)
    if mode=='after_replace':
        os._exit(78)
    return result


os.replace=intercepted_replace
print(json.dumps(decide(json.loads(raw_body),runtime_root=root)),flush=True)
