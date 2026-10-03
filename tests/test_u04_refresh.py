from __future__ import annotations
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import pytest

ROOT=Path(__file__).resolve().parents[1]
REL='domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts/refresh_supply_chain.py'
spec=importlib.util.spec_from_file_location('u04_refresh_source',ROOT/REL)
refresh=importlib.util.module_from_spec(spec);spec.loader.exec_module(refresh)

def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def git(root,*args):
    result=subprocess.run(['git','-c','safe.directory='+str(root),'-C',str(root),*args],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr;return result.stdout.strip()
def environment(root):
    value={k:os.environ[k] for k in ['SystemRoot','SystemDrive','WINDIR','PATH','TEMP','TMP','COMSPEC','PATHEXT'] if k in os.environ}
    value.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',PYTHONUTF8='1',PYTHONPATH=str(root/'outputs/guard'))
    return value

def fixture_payload():
    coverage={r:{'complete':True,'pagesExpected':1,'pagesRead':1} for r in refresh.REGIONS}
    value={'schema':'supply-chain-refresh-fixture/v1','synthetic':True,
      'inventory':{'capturedAt':'2026-09-05T08:00:00+00:00','source':'fixture://inventory','coverage':coverage,'records':[]},
      'orders':{'capturedAt':'2026-09-04T08:00:00+00:00','days':31,'coverage':coverage,'countries':{}},
      'inbound':{'capturedAt':'2026-09-03T08:00:00+00:00','coverage':coverage,'regions':{}},
      'seed':{'snapshotDate':'2026-09-01','config':{},'countries':{}}}
    event=int(datetime(2026,9,3,tzinfo=timezone.utc).timestamp())
    for region,warehouse in refresh.WAREHOUSES.items():
        value['inventory']['records'].append({'seller_sku':'0001','warehouse':warehouse,'stock':5,'available':5,'allocated':0,'frozen':0,'inbound':3,'captured_at':value['inventory']['capturedAt']})
        value['seed']['config'][region]={'warehouse':warehouse}
        value['seed']['countries'][region]=[{'sku':'0001','name':'Synthetic item','inventory':{},'channels':{}}]
        value['orders']['countries'][region]={
            'tiktok':[{'id':region+'-TK-'+str(i),'status':'COMPLETED' if i==0 else 'CANCELLED','create_time':event,
                'line_items':[{'seller_sku':'0001','product_name':'Synthetic item'}]} for i in range(2)],
            'shopee':[{'order_sn':region+'-SP-'+str(i),'order_status':'COMPLETED','create_time':event,
                'item_list':[{'model_sku':'0001','model_quantity_purchased':2,'item_name':'Synthetic item'}]} for i in range(2)]}
        value['inbound']['regions'][region]={'batches':[{'batchId':region+'-SYNTHETIC-BATCH','skuQuantities':{'0001':3},'totalUnits':3}]}
    return value

@pytest.fixture
def project(tmp_path_factory):
    root=tmp_path_factory.mktemp('r');
    for name in refresh.SOURCE_FILES:
        dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    (root/'.gitignore').write_text('outputs/\n',encoding='utf-8')
    git(root,'init');git(root,'add','.');git(root,'-c','user.name=U04 fixture','-c','user.email=u04@example.invalid','commit','-m','Synthetic source checkpoint')
    artifact=root/'outputs/refresh';artifact.mkdir(parents=True)
    guard=root/'outputs/guard';guard.mkdir()
    (guard/'sitecustomize.py').write_text("import sys\ndef guard(event,args):\n if event in {'socket.connect','socket.getaddrinfo'}: raise RuntimeError('U04 external network forbidden')\n if event=='open' and isinstance(args[0],str) and (args[0].endswith(('.db','.sqlite')) or args[0].replace('\\\\','/').endswith('config/settings.json')): raise RuntimeError('U04 business data forbidden')\nsys.addaudithook(guard)\n",encoding='utf-8')
    profile={'schema':'supply-chain-refresh-profile/v1','project_root':str(root),'branch':git(root,'branch','--show-current'),
        'source_commit':git(root,'rev-parse','HEAD'),'source_sha256':refresh.source_hashes(root),'artifact_root':'outputs/refresh','stable_release':False}
    write(artifact/'profile.json',profile);write(artifact/'source.json',fixture_payload())
    return root,artifact,profile

def command(root,artifact,*args):return [sys.executable,'-B',str(root/REL),'--project-root',str(root),'--profile',str(artifact/'profile.json'),*map(str,args)]
def cli(root,artifact,*args):
    result=subprocess.run(command(root,artifact,*args),cwd=artifact,env=environment(root),capture_output=True,text=True,encoding='utf-8',timeout=15)
    evidence=artifact/'cli';evidence.mkdir(exist_ok=True);write(evidence/(str(len(list(evidence.iterdir())))+'.json'),
      {'command':result.args,'cwd':str(artifact),'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
    return result,json.loads(result.stdout)

def test_real_cli_dry_run_staged_collectors_three_clocks_and_reuse(project):
    root,a,p=project
    result,value=cli(root,a,'--dry-run');assert result.returncode==0 and value['state']=='DRY_RUN';assert not (a/'checkpoint.json').exists()
    result,value=cli(root,a,'--fake-source',a/'source.json');assert result.returncode==0,value
    staged=json.loads((a/value['output']).read_text());assert value['state']=='COMPLETE'
    assert staged['clocks']=={'inventory':'2026-09-05','orders':'2026-09-04T08:00:00+00:00','inbound':'2026-09-03T08:00:00+00:00'}
    assert not staged['auth_modules_loaded'] and all(Path(p).is_relative_to(root) for p in staged['imports'].values())
    for region in refresh.REGIONS:
        row=staged['data']['countries'][region][0]
        assert row['inventory']['available']==5
        assert row['channels']['tiktok']['recent30Units']==1
        assert row['channels']['shopee']['recent30Units']==4
    assert any(r.get('offset')==1 for r in staged['fake_requests'])
    original=(a/'checkpoint.json').read_bytes();result,reused=cli(root,a,'--fake-source',a/'source.json')
    assert result.returncode==0 and reused['state']=='REUSED' and (a/'checkpoint.json').read_bytes()==original
    assert git(root,'status','--porcelain=v1','-uall')==''

@pytest.mark.parametrize('field,state',[('branch','BRANCH_MISMATCH'),('source_commit','SOURCE_MISMATCH'),('source_sha256','SOURCE_MISMATCH')])
def test_profile_drift_preserves_checkpoint(project,field,state):
    root,a,p=project;write(a/'checkpoint.json',{'untouched':'synthetic prior checkpoint'});before=(a/'checkpoint.json').read_bytes()
    p[field]={} if field=='source_sha256' else 'wrong';write(a/'profile.json',p)
    result,value=cli(root,a,'--dry-run');assert result.returncode!=0 and value['state']==state
    assert (a/'checkpoint.json').read_bytes()==before

def test_missing_dependency_and_root_fail_before_stage(project):
    root,a,p=project
    target=root/refresh.SOURCE_FILES[1];target.unlink()
    result,value=cli(root,a,'--dry-run');assert result.returncode!=0 and value['state']=='DEPENDENCIES_MISSING'
    args=command(root,a,'--dry-run');args[args.index('--project-root')+1]=str(root/'absent')
    result=subprocess.run(args,cwd=a,env=environment(root),capture_output=True,text=True,encoding='utf-8');assert json.loads(result.stdout)['state']=='ROOT_MISSING'
    assert not (a/'checkpoint.json').exists()

@pytest.mark.parametrize('failure',['detail','inventory_pages','inbound'])
def test_incomplete_sources_cannot_stage_ready(project,failure):
    root,a,p=project;payload=fixture_payload()
    if failure=='detail':payload['orders']['omit_detail']='TH'
    elif failure=='inventory_pages':payload['inventory']['coverage']['TH']['pagesRead']=0
    else:payload['inbound']['regions']['TH']['batches'][0]['skuQuantities']['0001']=2
    write(a/'source.json',payload)
    result,value=cli(root,a,'--fake-source',a/'source.json');assert result.returncode!=0,value
    assert value['state'] in {'BLOCKED_COVERAGE','BLOCKED_INBOUND'}
    assert json.loads((a/'checkpoint.json').read_text())['state']=='FAILED'
    assert not list(a.glob('*/staged.json'))

def test_two_real_processes_single_writer_then_crash_recovery(project):
    root,a,p=project;payload=fixture_payload();payload['wait_for']='release.flag';write(a/'source.json',payload)
    args=command(root,a,'--fake-source',a/'source.json')
    first=subprocess.Popen(args,cwd=a,env=environment(root),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        deadline=time.monotonic()+10
        while not (a/'checkpoint.json').exists():
            assert first.poll() is None and time.monotonic()<deadline;time.sleep(.05)
        before=(a/'checkpoint.json').read_bytes();original=json.loads(before)
        result,value=cli(root,a,'--dry-run');assert result.returncode!=0 and value['state']=='ALREADY_RUNNING'
        assert (a/'checkpoint.json').read_bytes()==before
        first.kill();stdout,stderr=first.communicate(timeout=5)
        write(a/'owned-process.json',{'pid':first.pid,'command':args,'exit':first.returncode,'stdout':stdout,'stderr':stderr,'checkpoint':original})
        (a/'release.flag').write_text('synthetic provider gate released',encoding='utf-8')
        result,value=cli(root,a,'--fake-source',a/'source.json');assert result.returncode==0,value
        assert value['recovered_from']==original['run_id'] and (a/original['run_id']/'started.json').is_file()
        assert len(list(a.glob('.lingshi-*.lock')))==1
    finally:
        if first.poll() is None:first.kill();first.wait(timeout=5)
