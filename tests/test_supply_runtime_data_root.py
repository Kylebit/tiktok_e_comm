"""Explicit private fixtures only; no installed Skill, stable data or provider."""
import json
from pathlib import Path
import shutil
import pytest
from test_u04_refresh import project,cli,write,refresh
from test_u04_complete_serving import serving,pair_get
from domains.supply_chain_operations.captured_serving import CompleteStore

@pytest.fixture
def external(serving,tmp_path):
    root,old,p,s,server,get,dashboard=serving
    runtime=tmp_path/'runtime';runtime.mkdir()
    artifact=runtime/'outputs/refresh'
    shutil.copytree(old,artifact)
    p['runtime_root']=str(runtime)
    write(artifact/'profile.json',p)
    config={'runtime_root':str(runtime),'artifact_root':'outputs/refresh','output_root':'applied'}
    server.supply_chain_capture=config
    return root,artifact,p,s,server,get,dashboard,runtime

def apply(external):
    root,a,*_=external
    run,stage=cli(root,a,'--captured-stage');assert run.returncode==0,stage
    run,result=cli(root,a,'--captured-apply',stage['stage_digest']);assert run.returncode==0,result
    return stage['stage_digest']

def test_external_native_capture_stage_apply_serves_complete_pair(external):
    root,a,p,s,server,get,dashboard,runtime=external
    original={name:(dashboard/name).read_bytes() for name in ('data.js','inbound-plan.js')}
    version=apply(external)
    code,headers,body=get('/supply-chain/')
    assert code==200 and version.encode() in body and b'legacy' not in body
    assert pair_get(get,version)=={name:(a/'applied'/name).read_bytes() for name in original}
    assert {name:(dashboard/name).read_bytes() for name in original}==original
    data=json.loads((a/'applied/data.js').read_text().removeprefix('window.SUPPLY_CHAIN_DATA = ').strip().removesuffix(';'))
    assert data['snapshotDate']=='2026-09-05' and data['orderDemandCapturedAt']=='2026-09-04T08:00:00+00:00'
    assert all(data['countries'][r][0]['inventory']['available']==5 for r in refresh.REGIONS)
    assert all(data['countries'][r][0]['channels']['shopee']['recent30Units']==4 for r in refresh.REGIONS)
    assert not runtime.is_relative_to(root)

@pytest.mark.parametrize('bad',['absent_runtime','under_checkout','artifact_escape','redirect','missing_generation'])
def test_configured_external_failure_visible_without_legacy_fallback(external,bad,tmp_path):
    root,a,p,s,server,get,dashboard,runtime=external
    version=apply(external)
    if bad=='absent_runtime':server.supply_chain_capture['runtime_root']=str(runtime/'absent')
    elif bad=='under_checkout':server.supply_chain_capture['runtime_root']=str(root/'outputs')
    elif bad=='artifact_escape':server.supply_chain_capture['artifact_root']='outputs/../../outside'
    elif bad=='missing_generation':(a/'captured-serving.json').unlink()
    else:
        redirect=runtime/'redirect'
        if __import__('os').name=='nt':
            import subprocess
            run=subprocess.run(['cmd.exe','/c','mklink','/J',str(redirect),str(a)],capture_output=True)
            assert run.returncode==0,run.stderr
        else:redirect.symlink_to(a,target_is_directory=True)
        server.supply_chain_capture['artifact_root']='outputs/../redirect'
    for path in ('/supply-chain/','/supply-chain/inbound-batches.html','/supply-chain/data.js'):
        code,headers,body=get(path)
        assert code==503 and b'legacy' not in body and b'window.SUPPLY_CHAIN_DATA' not in body
    assert get('/supply-chain/captured/'+version+'/data.js')[0]==503

def test_runtime_capture_source_and_consumer_share_lease_across_checkouts(external,tmp_path):
    root,a,p,s,server,get,dashboard,runtime=external
    version=apply(external)
    other=tmp_path/'other-code';other.mkdir()
    selected=CompleteStore(other,server.supply_chain_capture)
    assert selected.artifact==a and selected.root==runtime
    scope=refresh.digest({'root':str(runtime.resolve()).casefold(),'artifact':str(a).casefold()})
    with refresh.business_lock(a,scope,timeout=0):
        assert get('/supply-chain/')[0]==503
        run,result=cli(root,a,'--captured-stage')
        assert run.returncode!=0 and result['state']=='ALREADY_RUNNING'
    assert get('/supply-chain/')[0]==200
    assert selected.pair(version,selected.context()[1])==CompleteStore(root,server.supply_chain_capture).pair(version,selected.context()[1])

@pytest.mark.parametrize('bad',[None,{}, {'runtime_root':'relative','artifact_root':'outputs/x','output_root':'site'}])
def test_bad_native_capture_configuration_rejected_before_platform_startup(bad,monkeypatch):
    from shared_platform.operations_launch import serve
    import scripts.stable_runtime_bootstrap as bootstrap
    def forbidden(*args,**kwargs):raise AssertionError('malformed capture must be rejected before platform binding')
    monkeypatch.setattr(bootstrap,'prebind_workbench_store',forbidden)
    with pytest.raises(ValueError):serve({'supply_chain_capture':bad},Path(__file__).resolve().parents[1])

def test_consumer_and_producer_reject_runtime_root_containing_checkout(tmp_path):
    from domains.supply_chain_operations.captured_serving import capture_artifact_root
    code=tmp_path/'code';code.mkdir()
    config={'runtime_root':str(tmp_path),'artifact_root':'outputs/refresh','output_root':'applied'}
    with pytest.raises(ValueError):capture_artifact_root(code,config)

def test_native_launcher_explicit_binding_cannot_ignore_or_infer_data_root():
    from domains.supply_chain_operations.captured_serving import deployment_capture
    assert deployment_capture({}) is None
    config={'runtime_root':str(Path(__file__).resolve().parents[1].parent/'uncreated-runtime'),'artifact_root':'outputs/refresh','output_root':'applied'}
    result=deployment_capture({'supply_chain_capture':config})
    assert result==config and result is not config
    result['runtime_root']='mutated';assert config['runtime_root']!='mutated'

def test_actual_entry_rejects_bad_capture_before_prebind_and_log(tmp_path):
    """Fresh interpreter, actual main/parser; only forbidden side effects replaced."""
    import os,subprocess,sys,textwrap
    root=Path(__file__).resolve().parents[1]
    deployment=tmp_path/'deployment.json';log=tmp_path/'never-created'/'entry.log'
    prebind=tmp_path/'prebind-called';log_open=tmp_path/'log-open-called'
    deployment.write_text(json.dumps({'execution_mode':'web-only','code_root':str(root),
        'supply_chain_capture':None}),encoding='utf-8')
    code=textwrap.dedent('''
        import sys
        from pathlib import Path
        deployment,log,prebind,log_open=sys.argv[1:]
        import scripts.stable_runtime_bootstrap as bootstrap
        def forbidden(*args,**kwargs):
            Path(prebind).write_text('forbidden binding attempted')
            raise AssertionError('prebind called before capture validation')
        bootstrap.prebind_workbench_store=forbidden
        def audit(event,args):
            if event=='open' and isinstance(args[0],str) and Path(args[0])==Path(log):
                Path(log_open).write_text('forbidden log attempted')
                raise AssertionError('log opened before capture validation')
        sys.addaudithook(audit)
        from scripts.operations_web_entry import main
        sys.argv=['operations_web_entry','--deployment',deployment,'--log',log]
        main()
    ''')
    env={key:value for key,value in os.environ.items() if not key.startswith('ORBIT_')}
    env.pop('TIKTOK_ECOMM_HOME',None)
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    result=subprocess.run([sys.executable,'-X','utf8','-B','-c',code,str(deployment),str(log),str(prebind),str(log_open)],
        cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
    (tmp_path/'entry-rejection-receipt.json').write_text(json.dumps({'command':result.args,'exit':result.returncode,
        'stdout':result.stdout,'stderr':result.stderr,'source_root':str(root)}),encoding='utf-8')
    assert result.returncode!=0 and 'captured snapshot unavailable' in result.stderr
    assert not prebind.exists() and not log_open.exists() and not log.parent.exists()

def test_bad_captured_output_shape_reports_structured_preflight_block_before_writes(project):
    from test_u04_captured_refresh import captured_inputs
    root,a,p,s=captured_inputs(project)
    p['captured'].pop('output_root');write(a/'profile.json',p)
    before={path.name:path.read_bytes() for path in a.glob('*.json')}
    run,result=cli(root,a,'--captured-stage')
    assert run.returncode!=0 and result['state']=='PROFILE_INVALID'
    assert 'captured sources, targets and output_root' in result['detail']
    assert {path.name:path.read_bytes() for path in a.glob('*.json')}==before
    assert not (a/'captured-stages').exists() and not (a/'captured-apply.json').exists()
