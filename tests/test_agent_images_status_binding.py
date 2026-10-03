"""Existing captured image status only; synthetic provenance, poisoned effects."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
WRAPPER=ROOT/'scripts/repo_bound_agent_entry.py'
IMAGES='skills/prepare-product-images/scripts/prepare_product_images.py'


def digest(value):
    return 'sha256:'+hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()


def environment():
    return {k:v for k,v in os.environ.items() if not k.startswith(('ORBIT_','GIT_'))
            and k not in ('TIKTOK_E_COMM_ROOT','LINGSHI_IMAGE_QA_MODEL')}


def commit(root):
    env=environment(); env.update(GIT_AUTHOR_NAME='Synthetic Fixture',GIT_AUTHOR_EMAIL='fixture@example.invalid',
        GIT_COMMITTER_NAME='Synthetic Fixture',GIT_COMMITTER_EMAIL='fixture@example.invalid')
    for args in (['init','-q'],['add','.'],['commit','-qm','synthetic captured source provenance']):
        subprocess.run(['git','-C',str(root),*args],env=env,check=True,capture_output=True)
    return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],env=env,text=True).strip()


@pytest.fixture
def captured(tmp_path):
    root=tmp_path/'source'; producer=tmp_path/'original-producer'
    for selected in (root,producer):
        selected.mkdir(); (selected/'.gitignore').write_text('reports/\ndata/\n__pycache__/\n')
    for relative in ('scripts/repo_bound_agent_entry.py',IMAGES,'shared_platform/publication_rounds.py',
        'shared_platform/publication_stock_policy.py','modules/sourcing/image_generation_checkpoint.py'):
        target=root/relative; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,target)
    for relative in ('core/config.py','core/db.py','core/auth.py','modules/sourcing/lingshi_client.py',
                     'shared_platform/publication_paid_requests.py'):
        target=root/relative; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text("raise RuntimeError('AUTH_CLIENT_PAID_CONFIG_DB_IMPORT_FORBIDDEN')\n")
    (root/'modules/sourcing/new_product_workbench.py').write_text('''import json
from pathlib import Path
def load_state(offer_id, *, state_dir=None):
    if state_dir is None: raise RuntimeError('SOURCE_STATE_FALLBACK_FORBIDDEN')
    return json.loads((Path(state_dir)/(offer_id+'.json')).read_text())
''')
    (root/'guarded_images.py').write_text('''import runpy,sys
def guard(event,args):
    if event=='import' and str(args[0]).split('.')[0] in {'sqlite3','requests'}:
        raise RuntimeError('NETWORK_DB_IMPORT_FORBIDDEN')
    if event.startswith(('sqlite3.','socket.')): raise RuntimeError('NETWORK_DB_EVENT_FORBIDDEN')
    if event=='open' and str(args[0]).replace('\\\\','/').endswith(('events.jsonl','forbidden-history.json')):
        raise RuntimeError('PAID_HISTORY_READ_FORBIDDEN')
sys.addaudithook(guard)
runpy.run_path(sys.argv.pop(1),run_name='__main__')
''')
    for relative in ('core/config.py','core/db.py','modules/sourcing/new_product_workbench.py',
                     'shared_platform/publication_rounds.py',IMAGES):
        target=producer/relative; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/relative,target)
    source_head=commit(root); producer_head=commit(producer)
    paths={k:tmp_path/k for k in ('state_dir','round1_reports_root')}
    for path in paths.values(): path.mkdir()
    paths['r2_reports_root']=producer/'reports/product-preparation'; paths['r2_reports_root'].mkdir(parents=True)
    paths['phase_lock_root']=paths['r2_reports_root']
    profile={'schema':'orbit-agent-entry/v2','entry_mode':'images-captured-status',
        'source_root':str(root),'expected_source_head':source_head,'r2_producer_mode':'direct-cli-source-reports',
        'r2_producer_source_root':str(producer),'expected_r2_producer_head':producer_head,
        **{k:str(v) for k,v in paths.items()}}
    provenance={'authority':'synthetic captured fixture, no real action','source_head':source_head,
        'producer_source_head':producer_head,'image_script_sha256':hashlib.sha256((root/IMAGES).read_bytes()).hexdigest()}
    snapshot={'schema_version':'round1-approved-snapshot/v1','status':'APPROVED','offer_id':'123',
        'product_approval_id':'synthetic-freeze-123','product_approval_fingerprint':'synthetic-facts-v1',
        'workbench_tiktok_sites':['lh_my'],'canonical_targets':['tiktok:LH_MY'],
        'image_plan':{'brand_plans':[{'id':'livelyhive-sea','generated_assets':[{'role':'cover'}]}],
                      'translation_plan':{'status':'DEFERRED_UNTIL_ALL_IMAGES_GENERATED'}},'source_provenance':provenance}
    snapshot['snapshot_digest']=digest(snapshot)
    state={'offer_id':'123','product_approval':{'status':'approved','approval_id':snapshot['product_approval_id'],
        'input_fingerprint':snapshot['product_approval_fingerprint']},'review':{'selected_sites':['lh_my']},
        'source_provenance':provenance}
    generated={'status':'SUBMISSION_UNKNOWN','assets':[{'status':'SUBMISSION_UNKNOWN',
        'checkpoint_path':'X:/forbidden-history.json'}],'paid_requests':{'unknown':1},'source_provenance':provenance}
    documents={paths['state_dir']/'123.json':state,
        paths['round1_reports_root']/'123/round1-approved-snapshot.json':snapshot,
        paths['r2_reports_root']/'123/brand-image-generation.json':generated}
    for path,value in documents.items():
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value))
    for base in (root,tmp_path):
        directory=base/'reports/product-preparation/123'; directory.mkdir(parents=True,exist_ok=True)
        (directory/'brand-image-generation.json').write_text('{"status":"DECOY_SOURCE_OR_CWD"}')
        state_dir=base/'data/product_workbench'; state_dir.mkdir(parents=True,exist_ok=True)
        (state_dir/'123.json').write_text('{"offer_id":"DECOY"}')
    profile_path=tmp_path/'profile.json'
    def save(): profile_path.write_text(json.dumps(profile))
    def invoke(check=False,arguments=None,runner=WRAPPER,extra_env=None):
        save(); argv=[sys.executable,'-I','-B',str(runner),'--profile',str(profile_path),'--entry','images']
        if check: argv.append('--check-binding')
        if arguments is None and not check: arguments=['--offer-id','123']
        if arguments: argv.extend(['--',*arguments])
        env=environment(); env.update(extra_env or {})
        return subprocess.run(argv,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    def direct(arguments=None):
        save(); argv=[sys.executable,'-I','-B',str(root/'guarded_images.py'),str(root/IMAGES)]
        argv.extend(arguments if arguments is not None else ['--offer-id','123','--binding-profile',str(profile_path),
            '--binding-profile-sha256',hashlib.sha256(profile_path.read_bytes()).hexdigest()])
        return subprocess.run(argv,cwd=tmp_path,env=environment(),capture_output=True,text=True,timeout=20)
    return {'root':root,'producer':producer,'profile':profile,'paths':paths,'documents':documents,
        'profile_path':profile_path,'save':save,'invoke':invoke,'direct':direct,'tmp':tmp_path}


def locks(f):
    return list(f['tmp'].rglob('.lingshi-*.lock'))


def test_actual_status_uses_split_captures_and_original_lock(captured):
    f=captured; originals={p:p.read_bytes() for p in f['documents']}
    result=f['invoke']()
    assert result.returncode==0,result.stderr+result.stdout
    value=json.loads(result.stdout)
    assert value['status']=='SUBMISSION_UNKNOWN' and value['completed_brand_image_count']==1
    assert value['external_generation_count']==value['platform_writes']==0
    assert len(locks(f))==1 and locks(f)[0].parent==f['paths']['phase_lock_root']/'123'
    assert originals=={p:p.read_bytes() for p in originals}
    assert not list(f['tmp'].rglob('events.jsonl')) and not list(f['tmp'].rglob('*.db'))


def test_guarded_actual_consumer_does_not_import_or_call_side_effects(captured):
    f=captured; result=f['direct']()
    assert result.returncode==0,result.stderr+result.stdout
    assert json.loads(result.stdout)['status']=='SUBMISSION_UNKNOWN'


def test_metadata_only_copied_checker_and_qa_producer_contract_remains_valid(captured):
    f=captured; copy=f['tmp']/'.codex/skills/copied/scripts/entry.py'; copy.parent.mkdir(parents=True)
    shutil.copyfile(WRAPPER,copy)
    result=f['invoke'](check=True,runner=copy)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value['domain_imported'] is False and value['sql_connections']==value['provider_calls']==0
    assert value['local_writes']==['original direct producer phase lock']
    for field in ('settings_path','output_root','workbench_store_path','checkpoint_root','history_root'):
        assert value['field_propagation'][field]['supported'] is False
    assert not locks(f)
    import runpy
    # Exercise the existing QA AST checker on both new consumer and original producer.
    namespace=runpy.run_path(str(WRAPPER))
    for source in (f['root'],f['producer']): namespace['direct_producer_lock_contract'](source)


@pytest.mark.parametrize('kind',['state','r1','r2'])
def test_missing_capture_stops_before_any_lock(captured,kind):
    f=captured; paths={'state':f['paths']['state_dir']/'123.json',
        'r1':f['paths']['round1_reports_root']/'123/round1-approved-snapshot.json',
        'r2':f['paths']['r2_reports_root']/'123/brand-image-generation.json'}
    paths[kind].unlink(); result=f['invoke']()
    assert result.returncode!=0 and 'IMAGES_CAPTURED_' in result.stderr+result.stdout
    assert not locks(f)


@pytest.mark.parametrize('arguments',[['--off','123'],['--o','123'],['--offer-id','123','--offer-id','999'],
    ['--repo-r','X:/drift'],['--binding-pro','X:/drift'],['--entry-mode','paid'],['--execute-paid'],
    ['--execute-brand-generation'],['--approve-translation-images','1'],['--prepare-brand-rework'],
    ['--retry-localized-review-number','1'],['--uploaded-assets','X:/file'],['--model','paid'],
    ['--paid-policy','X:/policy'],['--offer-id','../123'],['--offer-id','１２３']])
def test_only_canonical_offer_flag_is_accepted(captured,arguments):
    f=captured; result=f['invoke'](check=True,arguments=arguments)
    assert result.returncode!=0
    assert not locks(f)


@pytest.mark.parametrize('key',['ORBIT_HIVE_SETTINGS','ORBIT_WORKBENCH_STORE_PATH','ORBIT_R2_REVIEW_RUNTIME_ROOT',
    'ORBIT_RELEASE_STORE_PATH','ORBIT_CATALOG_DATABASE','TIKTOK_E_COMM_ROOT'])
def test_inherited_roots_cannot_override_profile(captured,key):
    f=captured; result=f['invoke'](extra_env={key:'X:/unbound'})
    assert result.returncode!=0 and 'ENV_BINDING_' in result.stderr
    assert not locks(f)


@pytest.mark.parametrize('kind',['mode','producer-mode','source-head','producer-head','lock-root','r2-root','extra-field'])
def test_profile_mapping_and_scope_fail_closed(captured,kind):
    f=captured; p=f['profile']
    if kind=='mode': p['entry_mode']='images-paid'
    elif kind=='producer-mode': p['r2_producer_mode']='native-runtime'
    elif kind=='source-head': p['expected_source_head']='0'*40
    elif kind=='producer-head': p['expected_r2_producer_head']='0'*40
    elif kind=='extra-field': p['output_root']=str(f['tmp'])
    else: p[{'lock-root':'phase_lock_root','r2-root':'r2_reports_root'}[kind]]=str(f['tmp'])
    result=f['invoke'](check=True)
    assert result.returncode!=0
    assert not locks(f)


@pytest.mark.parametrize('kind',['state','r1','r2','translation'])
def test_invalid_capture_rejects_before_lock(captured,kind):
    f=captured; path={'state':f['paths']['state_dir']/'123.json',
        'r1':f['paths']['round1_reports_root']/'123/round1-approved-snapshot.json',
        'r2':f['paths']['r2_reports_root']/'123/brand-image-generation.json',
        'translation':f['paths']['r2_reports_root']/'123/brand-image-translation.json'}[kind]
    path.write_text('null')
    result=f['invoke']()
    assert result.returncode!=0 and not locks(f)


def test_optional_translation_is_read_only_and_supplies_existing_status(captured):
    f=captured; path=f['paths']['r2_reports_root']/'123/brand-image-translation.json'
    path.write_text('{"status":"LOCALIZED_IMAGE_REVIEW_REQUIRED","checkpoint_path":"X:/forbidden"}')
    before=path.read_bytes(); result=f['invoke']()
    assert result.returncode==0,result.stderr+result.stdout
    assert json.loads(result.stdout)['status']=='LOCALIZED_IMAGE_REVIEW_REQUIRED' and path.read_bytes()==before


def test_direct_consumer_checks_digest_and_disallows_paid_flags(captured):
    f=captured; f['save']()
    args=['--offer-id','123','--binding-profile',str(f['profile_path']),'--binding-profile-sha256','0'*64]
    result=f['direct'](args)
    assert result.returncode!=0 and 'IMAGES_PROFILE_DIGEST_CHANGED' in result.stdout+result.stderr
    args[-1]=hashlib.sha256(f['profile_path'].read_bytes()).hexdigest(); args.append('--execute-paid')
    result=f['direct'](args)
    assert result.returncode!=0 and 'IMAGES_CAPTURED_STATUS_MODE_ONLY' in result.stdout+result.stderr
    assert not locks(f)


def test_actual_selected_source_and_producer_drift_reject(captured):
    f=captured; (f['root']/IMAGES).write_text((f['root']/IMAGES).read_text()+'\n# drift\n')
    result=f['invoke']()
    assert result.returncode!=0 and 'SOURCE_DIRTY' in result.stderr and not locks(f)


def test_producer_lock_ast_mismatch_is_rejected(captured):
    f=captured; path=f['producer']/IMAGES
    path.write_text(path.read_text().replace("'round2-phase'","'moved-phase'"))
    f['profile']['expected_r2_producer_head']=commit(f['producer'])
    result=f['invoke'](check=True)
    assert result.returncode!=0 and 'DIRECT_PRODUCER_LOCK_CONTRACT_UNPROVEN' in result.stderr
    assert not locks(f)


@pytest.mark.parametrize('kind',['state','r1','generation','translation','lock'])
def test_capture_or_lock_alias_rejects_before_local_write(captured,kind):
    f=captured; base=f['paths']['r2_reports_root']/'123'
    phase=hashlib.sha256(json.dumps({'scope':'round2-phase','offer_id':'123'},sort_keys=True,
        separators=(',',':')).encode()).hexdigest()
    path={'state':f['paths']['state_dir']/'123.json',
        'r1':f['paths']['round1_reports_root']/'123/round1-approved-snapshot.json',
        'generation':base/'brand-image-generation.json','translation':base/'brand-image-translation.json',
        'lock':base/f'.lingshi-{phase[:24]}.lock'}[kind]
    target=f['tmp']/'alias-target.json'
    if path.exists(): target.write_bytes(path.read_bytes()); path.unlink()
    else: target.write_text('{"status":"UNKNOWN"}')
    path.symlink_to(target); before=target.read_bytes()
    result=f['invoke']()
    assert result.returncode!=0 and 'REPARSE_REJECTED' in result.stdout+result.stderr
    assert target.read_bytes()==before
    assert all(p.is_symlink() for p in locks(f))


@pytest.mark.parametrize('source',['consumer','producer'])
def test_old_clean_source_without_status_capability_or_lock_proof_rejects(captured,source):
    f=captured; root=f['root'] if source=='consumer' else f['producer']; path=root/IMAGES
    old=path.read_text()
    if source=='consumer':
        old=old.replace("IMAGES_STATUS_BINDING_CONTRACT = 'orbit-images-status-paths/v2'",'')
    else:
        old=old.replace("'round2-phase'","'different-phase'")
    path.write_text(old)
    f['profile']['expected_source_head' if source=='consumer' else 'expected_r2_producer_head']=commit(root)
    result=f['invoke'](check=True)
    assert result.returncode!=0
    assert ('CAPABILITY_MISSING' if source=='consumer' else 'LOCK_CONTRACT_UNPROVEN') in result.stderr
    assert not locks(f)


def test_profile_drift_while_acquiring_original_lock_stops_status(captured):
    f=captured; checkpoint=f['root']/'modules/sourcing/image_generation_checkpoint.py'
    checkpoint.write_text(checkpoint.read_text()+'''\n_original_business_lock=business_lock
@contextmanager
def business_lock(*args,**kwargs):
    with _original_business_lock(*args,**kwargs):
        path=Path('''+repr(str(f['profile_path']))+''')
        value=json.loads(path.read_text()); value['entry_mode']='images-paid'
        path.write_text(json.dumps(value))
        yield
''')
    f['profile']['expected_source_head']=commit(f['root'])
    originals={p:p.read_bytes() for p in f['documents']}; result=f['invoke']()
    assert result.returncode!=0 and 'IMAGES_PROFILE_DIGEST_CHANGED' in result.stdout+result.stderr
    assert len(locks(f))==1 and locks(f)[0].parent==f['paths']['phase_lock_root']/'123'
    assert originals=={p:p.read_bytes() for p in originals}


def test_native_or_custom_runtime_cannot_enter_frozen_status(captured):
    f=captured; script=f['tmp']/'runtime_probe.py'
    script.write_text('''import runpy,sys
from pathlib import Path
source=Path(sys.argv[1]); sys.path.insert(0,str(source))
entry=runpy.run_path(str(source/'''+repr(IMAGES)+'''))
try: entry['run'](None,runtime=object(),binding={})
except ValueError as error:
    assert str(error)=='IMAGES_FROZEN_STATUS_BINDING_REQUIRED'
else: raise AssertionError('custom runtime accepted')
''')
    result=subprocess.run([sys.executable,'-I','-B',str(script),str(f['root'])],env=environment(),
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr+result.stdout
    assert not locks(f)


def test_equals_canonical_offer_remains_compatible(captured):
    result=captured['invoke'](arguments=['--offer-id=123'])
    assert result.returncode==0,result.stderr+result.stdout
    assert json.loads(result.stdout)['status']=='SUBMISSION_UNKNOWN'


@pytest.mark.parametrize('kind',['source','capture'])
def test_source_or_capture_drift_under_original_lock_rejects(captured,kind):
    f=captured; checkpoint=f['root']/'modules/sourcing/image_generation_checkpoint.py'
    target=f['root']/IMAGES if kind=='source' else f['paths']['r2_reports_root']/'123/brand-image-generation.json'
    mutation='path.write_text(path.read_text(encoding="utf-8")+"\\n# drift\\n",encoding="utf-8")' if kind=='source' else 'path.unlink()'
    checkpoint.write_text(checkpoint.read_text()+'''\n_original_business_lock=business_lock
@contextmanager
def business_lock(*args,**kwargs):
    with _original_business_lock(*args,**kwargs):
        path=Path('''+repr(str(target))+''')
        '''+mutation+'''
        yield
''')
    f['profile']['expected_source_head']=commit(f['root'])
    result=f['invoke']()
    assert result.returncode!=0
    assert ('SOURCE_DIRTY' if kind=='source' else 'IMAGES_CAPTURED_R2_GENERATION_MISSING') in result.stdout+result.stderr
    assert len(locks(f))==1 and locks(f)[0].parent==f['paths']['phase_lock_root']/'123'
    assert not list(f['tmp'].rglob('events.jsonl')) and not list(f['tmp'].rglob('*.db'))


@pytest.mark.parametrize('option',['--off','--offer','--binding-profile-sha'])
def test_frozen_direct_cli_rejects_abbreviation_before_local_write(captured,option):
    f=captured; f['save']()
    args=['--offer-id','123','--binding-profile',str(f['profile_path']),
          '--binding-profile-sha256',hashlib.sha256(f['profile_path'].read_bytes()).hexdigest()]
    args[0 if option in ('--off','--offer') else 4]=option
    originals={p:p.read_bytes() for p in f['documents']}; result=f['direct'](args)
    assert result.returncode!=0 and 'IMAGES_CAPTURED_STATUS_MODE_ONLY' in result.stdout+result.stderr
    assert not locks(f) and originals=={p:p.read_bytes() for p in originals}


def test_original_unbound_parser_keeps_abbreviation_compatibility(captured):
    f=captured; script=f['tmp']/'parser_probe.py'
    script.write_text('''import runpy,sys
from pathlib import Path
source=Path(sys.argv[1]); sys.path.insert(0,str(source))
entry=runpy.run_path(str(source/'''+repr(IMAGES)+'''))
seen=[]
def fake_run(args,**kwargs):
    seen.append(args.offer_id)
    return {'status':'SYNTHETIC_PARSER_ONLY'}
entry['main'].__globals__['run']=fake_run
sys.argv=['entry','--off','123']
assert entry['main']()==0 and seen==['123']
''')
    result=subprocess.run([sys.executable,'-I','-B',str(script),str(f['root'])],env=environment(),
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr+result.stdout
    assert not locks(f)
