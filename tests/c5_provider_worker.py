"""Subprocess-only transport fixture for the actual portable CLI and OS locks."""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import sys
import time
from email.message import Message
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

runtime, project, provider, mode, label = sys.argv[1:]
root=Path(runtime); project=Path(project)
sys.path.insert(0,str(root))

def audit(event,args):
    if event in {'socket.connect','socket.getaddrinfo'}:
        raise AssertionError('C5 real network forbidden')
sys.addaudithook(audit)

def fake_https(handler, req):
    path=urlsplit(req.full_url).path
    with (project/(label+'-transport.jsonl')).open('a',encoding='utf-8') as log:
        log.write(json.dumps({'pid':os.getpid(),'url':req.full_url,'method':req.get_method(),'attempted':1})+'\n')
        log.flush();os.fsync(log.fileno())
    submitting=path.endswith('/install') or '/fetch_' in path
    if submitting and mode in {'hold','timeout','crash'}:
        (project/(label+'-entered')).write_text('transport attempted',encoding='utf-8')
        if mode=='crash':os._exit(91)
        if mode=='timeout':raise TimeoutError('synthetic transport timeout')
        deadline=time.monotonic()+45
        while not (project/'release').exists():
            if time.monotonic()>deadline:raise TimeoutError('synthetic held transport timeout')
            time.sleep(.025)
    if path.endswith('/app/list'):
        value={'code':200,'data':{'list':[{'id':'app-c5','pkg':'com.example.c5','version_list':[{'id':'version-c5'}]}]}}
    elif path.endswith('/installedList'):
        value={'code':200,'data':{'list':['com.example.c5']}}
    else:value={'code':200,'data':[]}
    response=addinfourl(io.BytesIO(json.dumps(value).encode()),Message(),req.full_url,200)
    response.msg='Synthetic OK';return response

HTTPSHandler.https_open=fake_https
from scripts.orbit_tools import main
(project/(label+'-started')).write_text(str(os.getpid()),encoding='utf-8')
raise SystemExit(main(['--runtime-root',str(root),'--project-root',str(project),'--profile','profile.json',
    'execute',provider,'install-app' if provider=='duoplus' else 'query','--payload','payload.json','--authorization','authorization.json']))
