from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

from shared_platform.capability_runtime import digest, load_profile
from modules.tools import duoplus,tikhub

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def tmp_path(tmp_path_factory):
    # Keep real provider ledger paths below the Windows legacy path limit.
    return tmp_path_factory.mktemp('p')

def setup(project,provider,size):
    profile={'schema':'orbit-tool-profile/v1','tenant_id':'c5-process','artifact_root':'a','providers':{
        provider:{'origin':'https://openapi.duoplus.cn' if provider=='duoplus' else 'https://api.tikhub.io',
                  'credential_env':'C5_SYNTHETIC_KEY' if provider=='duoplus' else 'TikHub'}}}
    (project/'profile.json').write_text(json.dumps(profile),encoding='utf-8')
    context=load_profile(project,Path('profile.json'))
    if provider=='duoplus':
        payload={'image_ids':['device-c5'],'app_id':'app-c5','app_version_id':'version-c5','package':'com.example.c5'}
        scope=duoplus.install_scope(tenant_id=context['tenant_id'],profile_digest=context['profile_digest'],origin=profile['providers'][provider]['origin'],**payload)
        lock_digest=digest({'tenant':context['tenant_id'],'provider':scope['origin']})
        directory=context['artifact_root']/'duoplus'/context['profile_digest']
    else:
        payload={'keyword':'c5 synthetic','region':'TH','video_count':1};plan=tikhub.preview(**payload)
        scope={'tenant_id':context['tenant_id'],'profile_digest':context['profile_digest'],'plan_digest':digest(plan),'maximum_paid_requests':2}
        lock_digest=digest(scope);directory=context['artifact_root']/'tikhub'/context['profile_digest']/digest(plan)[:24]
    authorization={'scope':scope,'authorization_id':'c5-fixture-only','instruction_ref':'fixture://c5-process-test'}
    for name,value in [('payload',payload),('authorization',authorization)]:
        (project/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
    directory.mkdir(parents=True)
    lock=directory/('.lingshi-'+lock_digest[:24]+'.lock');lock.write_bytes(b'01'[:size])
    return lock

def start(project,provider,mode,label):
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','PATH','TEMP','TMP','PATHEXT') if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',PYTHONUTF8='1',C5_SYNTHETIC_KEY='fixture-only',TikHub='fixture-only',
        HTTP_PROXY='http://forbidden.invalid:9',HTTPS_PROXY='http://forbidden.invalid:9',ALL_PROXY='http://forbidden.invalid:9',
        OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1')
    return subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/c5_provider_worker.py'),str(ROOT),str(project),provider,mode,label],
        cwd=project,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')

def wait_file(path,process,timeout=30):
    deadline=time.monotonic()+timeout
    while not path.exists():
        if process.poll() is not None:raise AssertionError(process.communicate())
        if time.monotonic()>deadline:raise AssertionError('fixture rendezvous timeout: '+str(path))
        time.sleep(.025)

def finish(process,project,label,timeout=40):
    out,err=process.communicate(timeout=timeout)
    (project/(label+'-result.json')).write_text(json.dumps({'pid':process.pid,'returncode':process.returncode,'stdout':out,'stderr':err}),encoding='utf-8')
    return out,err

def attempts(project,provider):
    rows=[json.loads(line) for p in project.glob('*-transport.jsonl') for line in p.read_text().splitlines()]
    return [r for r in rows if r['url'].split('?')[0].endswith('/install') or (provider=='tikhub' and '/fetch_' in r['url'])]

def ledger(project,provider):
    name='installs.json' if provider=='duoplus' else 'manifest.json'
    value=json.loads(next((project/'a').rglob(name)).read_text())
    return list(value['requests'].values()) if provider=='duoplus' else value['requests']

@pytest.mark.parametrize('provider',['duoplus','tikhub'])
@pytest.mark.parametrize('size',[0,1,2])
def test_two_real_cli_processes_submit_each_request_once_and_keep_lock_bytes(tmp_path,provider,size):
    lock=setup(tmp_path,provider,size);before=lock.read_bytes()
    first=start(tmp_path,provider,'hold','first');second=None
    try:
        wait_file(tmp_path/'first-entered',first)
        second=start(tmp_path,provider,'success','second');wait_file(tmp_path/'second-started',second)
        time.sleep(.2)
        assert not (tmp_path/'second-transport.jsonl').exists()
        (tmp_path/'release').write_text('release')
        one,_=finish(first,tmp_path,'first');two,_=finish(second,tmp_path,'second')
        assert first.returncode==second.returncode==0,(one,two)
        expected=1 if provider=='duoplus' else 2
        assert len(attempts(tmp_path,provider))==expected
        result=json.loads(two)
        assert result['new_install_request_count' if provider=='duoplus' else 'new_paid_request_count']==0
        assert lock.read_bytes()==before
    finally:
        for proc in (first,second):
            if proc is not None and proc.poll() is None:proc.kill();proc.wait()

@pytest.mark.parametrize('provider',['duoplus','tikhub'])
@pytest.mark.parametrize('mode',['timeout','crash'])
def test_real_cli_unknown_and_process_death_never_resubmit(tmp_path,provider,mode):
    setup(tmp_path,provider,0)
    first=start(tmp_path,provider,mode,'first');finish(first,tmp_path,'first')
    assert first.returncode==(91 if mode=='crash' else 2)
    second=start(tmp_path,provider,'success','second');out,_=finish(second,tmp_path,'second')
    assert len(attempts(tmp_path,provider))==1
    rows=ledger(tmp_path,provider);assert len(rows)==1
    assert rows[0]['install_request_count' if provider=='duoplus' else 'paid_request_count']==1
    if provider=='duoplus':assert json.loads(out)['status']=='UNKNOWN'
    else:assert second.returncode==2 and 'unknown' in out

@pytest.mark.parametrize('provider',['duoplus','tikhub'])
def test_real_provider_waiter_times_out_and_crashed_holder_recovers_without_resubmit(tmp_path,provider):
    setup(tmp_path,provider,2)
    first=start(tmp_path,provider,'hold','first');second=None
    try:
        wait_file(tmp_path/'first-entered',first)
        second=start(tmp_path,provider,'success','second');out,_=finish(second,tmp_path,'second',timeout=36)
        assert second.returncode==2 and 'another process' in out
        assert not (tmp_path/'second-transport.jsonl').exists()
        first.kill();finish(first,tmp_path,'first')
        third=start(tmp_path,provider,'success','third');finish(third,tmp_path,'third')
        assert len(attempts(tmp_path,provider))==1
        rows=ledger(tmp_path,provider)
        assert rows[0]['install_request_count' if provider=='duoplus' else 'paid_request_count']==1
    finally:
        for proc in (first,second):
            if proc is not None and proc.poll() is None:proc.kill();proc.wait()
