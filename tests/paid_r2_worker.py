"""Fresh-process offline worker; only external I/O and approval reads are fixtures."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.dont_write_bytecode=True
external=[]
def guard(event,args):
    if event in {'socket.connect','socket.getaddrinfo','sqlite3.connect'}:
        external.append(event)
        raise AssertionError('offline worker blocks '+event)
sys.addaudithook(guard)

import pytest
from test_publication_paid_entry import Workflow, FixtureClient, OFFER, read
from modules.sourcing import image_generation_checkpoint as checkpoint
from modules.sourcing import lingshi_client, localized_image_auto_translation as translator
from shared_platform.publication_paid_requests import PaidRequestContext

root=Path(sys.argv[1])
mode=sys.argv[2]
cut=sys.argv[3] if len(sys.argv)>3 else ''
spy=root/'provider-boundary.jsonl'
mp=pytest.MonkeyPatch()
w=Workflow(root,mp,initialize=False)

class DurableClient(FixtureClient):
    def record(self,kind):
        with checkpoint.business_lock(root,'offline-provider-spy'):
            rows=spy.read_text().splitlines() if spy.exists() else []
            task=1000+len(rows)+1
            with spy.open('ab') as handle:
                handle.write((json.dumps({'kind':kind,'task':task,'pid':os.getpid()})+'\n').encode())
                handle.flush();os.fsync(handle.fileno())
        if cut=='post':os._exit(71)
        return task

mp.setattr(lingshi_client,'LingshiClient',DurableClient)
mp.setattr(translator,'LingshiClient',DurableClient)
mp.setattr(w.qa,'LingshiClient',DurableClient)
if cut=='reserve':
    original=PaidRequestContext.reserve
    def reserve(self,**kwargs):
        value=original(self,**kwargs)
        os._exit(71)
    mp.setattr(PaidRequestContext,'reserve',reserve)
if cut in {'submitting','ack','completed'}:
    original=checkpoint.ImageCheckpoint.persist
    def persist(self,state,**kwargs):
        value=original(self,state,**kwargs)
        if value['status']=={'submitting':'SUBMITTING','ack':'SUBMITTED','completed':'COMPLETED'}[cut]:os._exit(71)
        return value
    mp.setattr(checkpoint.ImageCheckpoint,'persist',persist)
if cut=='raw':
    original=PaidRequestContext.record
    def record(self,key,event,**details):
        if event=='RECEIVED':os._exit(71)
        return original(self,key,event,**details)
    mp.setattr(PaidRequestContext,'record',record)
if cut=='rebuild-active':
    import shared_platform.publication_paid_requests as paid_module
    original=paid_module.atomic_json
    def atomic_json(path,value):
        result=original(path,value)
        if Path(path).name.startswith('plan-'):os._exit(71)
        return result
    mp.setattr(paid_module,'atomic_json',atomic_json)

try:
    if mode=='brand':result=w.masters()
    elif mode=='translation':result=w.entry.run(w.args(execute_paid=True))
    elif mode=='qa':result=w.qa.run(argparse.Namespace(offer_id=OFFER,model='fixture-qa',assessment=None))
    elif mode=='rebuild':result=w.context().activate_plan_rebuild(next((w.directory/'paid-requests').glob('proposal-*.json')))
    else:raise ValueError(mode)
    report={'ok':True,'result':result}
except Exception as error:
    report={'ok':False,'error_type':type(error).__name__,'error':str(error),'traceback':traceback.format_exc()}
report['summary']=w.context().summary()
report['external_attempts']=external
report['imports']={name:module.__file__ for name,module in list(sys.modules.items())
    if name.startswith(('modules.sourcing','shared_platform')) and getattr(module,'__file__',None)}
assert all(Path(path).is_relative_to(ROOT) for path in report['imports'].values())
(root/f'worker-{os.getpid()}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ok':report['ok'],'occupied':report['summary']['occupied'],'external':external}))
