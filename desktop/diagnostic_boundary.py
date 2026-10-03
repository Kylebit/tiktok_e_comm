"""Opt-in offline boundary for testing the shipped executable with synthetic data.

This is not a page API and never runs during ordinary client use. The test
orchestrator owns the declared loopback proxy and supplies its request ledger.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from threading import local


def install(selection, proxy_port, directory):
    if type(proxy_port) is not int or not 1 <= proxy_port <= 65535:
        raise ValueError('Offline diagnostics require a declared loopback proxy port.')
    directory.mkdir(parents=True, exist_ok=True)
    log_path=directory/'python-boundary.jsonl'
    context=local()
    allowed_ports={selection.port,proxy_port}
    root=selection.project_root
    git=['git','-c','safe.directory='+root.as_posix(),'-C',str(root),'rev-parse','HEAD'] if root else None

    def log(kind,event,args):
        context.logging=True
        try:
            with log_path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'pid':os.getpid(),'kind':kind,'event':event,'args':str(args)[:500]})+'\n')
        finally:context.logging=False

    def guard(event,args):
        if getattr(context,'logging',False):return
        blocked=False
        if event=='socket.connect':
            address=args[1]
            blocked=not (isinstance(address,tuple) and address[0] in {'127.0.0.1','::1','localhost'} and address[1] in allowed_ports)
        elif event=='socket.bind':blocked=True
        elif event=='socket.getaddrinfo':blocked=args[0] not in {'127.0.0.1','::1','localhost'}
        elif event in {'socket.gethostbyname','socket.gethostbyaddr','sqlite3.connect','os.system','os.exec','os.spawn'}:blocked=True
        elif event=='subprocess.Popen':
            blocked=git is None or args[1] not in (git,subprocess.list2cmdline(git))
        elif event=='open' and args and isinstance(args[0],(str,bytes)):
            path=Path(os.fsdecode(args[0])).resolve()
            blocked=path.name.lower() in {'settings.json','tiktok_tokens.json','lingshi.local.json','.env'} or path.suffix.lower() in {'.db','.sqlite','.sqlite3'}
        if blocked:
            log('blocked',event,args)
            raise RuntimeError('OFFLINE_DESKTOP_DIAGNOSTICS: '+event)
        if event in {'socket.connect','subprocess.Popen'}:log('allowed',event,args)

    os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']=(
        f'--proxy-server=http://127.0.0.1:{proxy_port} --proxy-bypass-list=<-loopback> '
        '--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" --disable-quic '
        '--disable-background-networking --disable-component-update --disable-sync '
        '--disable-renderer-backgrounding --disable-backgrounding-occluded-windows')
    sys.addaudithook(guard)
    log('startup','offline-diagnostic-boundary',{'ports':sorted(allowed_ports),'root':str(root)})
