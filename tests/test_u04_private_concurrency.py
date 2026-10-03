import json,threading,subprocess,sys
from pathlib import Path
import pytest
from test_u04_private_storage import case,SCRIPT

def second(case):
    m,paths,private,values,plan,auth,stopped=case
    other=private.parent/'legacy-B';other.mkdir();new={k:other/p.name for k,p in paths.items()}
    for k,p in new.items():
        value=json.loads(paths[k].read_text(encoding='utf-8'));value['synthetic_owner']='B';p.write_text(json.dumps(value),encoding='utf-8')
    b=m.prepare(**new,private=private,run_id='synthetic-B',writers=['synthetic-writer'],database_target=other/'data/shop.db')
    approved={**auth,'plan_digest':m.g.plan_digest(b)}
    return new,b,approved

def test_two_run_ids_cannot_mix_private_destination(case,monkeypatch):
    m,paths,private,values,plan,auth,stopped=case;other,b,approved=second(case)
    entered=threading.Event();release=threading.Event();outcomes={};reads=[];original=m._atomic_private;read=Path.read_bytes
    def record(p):
        if threading.current_thread() is threading.main_thread() and p in other.values():reads.append(str(p))
        return read(p)
    monkeypatch.setattr(Path,'read_bytes',record)
    def pause(destination,raw,directory):
        if threading.current_thread().name=='migration-A' and destination.name=='tiktok.json':entered.set();assert release.wait(10)
        return original(destination,raw,directory)
    monkeypatch.setattr(m,'_atomic_private',pause)
    def a():
        try:outcomes['A']=m.execute(plan,auth,stopped)['state']
        except Exception as e:outcomes['A']=str(e)
    thread=threading.Thread(target=a,name='migration-A');thread.start()
    try:
        assert entered.wait(10)
        before={p.name:read(p) for p in private.iterdir() if p.is_file()}
        try:outcomes['B']=m.execute(b,approved,stopped)['state']
        except m.g.RecoveryError as e:outcomes['B']=str(e)
        unchanged=before=={p.name:read(p) for p in private.iterdir() if p.is_file()}
    finally:release.set();thread.join(10)
    assert not thread.is_alive()
    owners={k:json.loads(read(private/(k+'.json'))).get('synthetic_owner','A') for k in ('tiktok','shopee')}
    (private.parent/'concurrency-proof.json').write_text(json.dumps({'outcomes':outcomes,'blocked_source_reads':reads,'blocked_target_unchanged':unchanged,'owners':owners}),encoding='utf-8')
    assert outcomes=={'A':'COMPLETE','B':'LOCKED_OR_UNPROTECTED'} and not reads and unchanged and owners=={'tiktok':'A','shopee':'A'}

def test_process_orphan_blocks_different_run_before_source_reads(case,monkeypatch):
    m,paths,private,values,plan,auth,stopped=case;other,b,approved=second(case)
    public=private.parent/'crash-plan.json';public.write_text(json.dumps({'plan':plan,'auth':auth}),encoding='utf-8')
    script=private.parent/'crash.py';script.write_text("import importlib.util,json,os,sys\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location('m',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nx=json.loads(Path(sys.argv[2]).read_text());original=m._atomic_private\ndef crash(d,r,p):\n original(d,r,p)\n if d.name=='tiktok.json':os._exit(73)\nm._atomic_private=crash\nm.execute(x['plan'],x['auth'],lambda p:{'plan_digest':m.g.plan_digest(p),'writers':p['writers'],'all_stopped':True,'evidence':'synthetic child only'})\n",encoding='utf-8')
    run=subprocess.run([sys.executable,'-B','-X','utf8',str(script),str(SCRIPT),str(public)],capture_output=True,timeout=20);assert run.returncode==73
    read=Path.read_bytes;reads=[]
    def record(p):
        if p in other.values():reads.append(str(p))
        return read(p)
    monkeypatch.setattr(Path,'read_bytes',record);before={p.name:read(p) for p in private.iterdir() if p.is_file()}
    try:m.execute(b,approved,stopped);blocked='NOT_BLOCKED'
    except m.g.RecoveryError as e:blocked=str(e)
    unchanged=before=={p.name:read(p) for p in private.iterdir() if p.is_file()}
    (private.parent/'orphan-proof.json').write_text(json.dumps({'child_exit':run.returncode,'second_run':blocked,'source_reads':reads,'target_unchanged':unchanged}),encoding='utf-8')
    assert blocked=='LOCKED_OR_UNPROTECTED' and not reads and unchanged
    locks=list(private.glob('*.recovery-lock'));assert len(locks)==1
    locks[0].unlink() # Known synthetic child exited; explicit test-only reconciliation.
    assert m.execute(plan,auth,stopped)['state']=='COMPLETE'
    for k in ('tiktok','shopee'):assert read(private/(k+'.json'))==read(paths[k])
