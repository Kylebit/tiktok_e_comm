"""Captured QA assessment paths; synthetic Git provenance and poisoned side effects."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT/'scripts/repo_bound_agent_entry.py'
QA = 'skills/prepare-product-images/scripts/run_automated_image_qa.py'
IMAGES = 'skills/prepare-product-images/scripts/prepare_product_images.py'


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return 'sha256:'+hashlib.sha256(raw).hexdigest()


def environment():
    return {key:value for key,value in os.environ.items()
            if not key.startswith(('ORBIT_', 'GIT_')) and key != 'LINGSHI_IMAGE_QA_MODEL'}


def commit(root):
    env=environment()
    env.update(GIT_AUTHOR_NAME='Synthetic Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='Synthetic Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    for args in (['init','-q'],['add','.'],['commit','-qm','synthetic QA source provenance']):
        subprocess.run(['git','-C',str(root),*args],env=env,check=True,capture_output=True)
    return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],env=env,text=True).strip()


@pytest.fixture
def qa_fixture(tmp_path):
    root=tmp_path/'source'; root.mkdir()
    producer=tmp_path/'original-producer'; producer.mkdir()
    for selected in (root,producer):
        (selected/'.gitignore').write_text('reports/\ndata/\n__pycache__/\n')
    for relative in ('scripts/repo_bound_agent_entry.py',QA,IMAGES,
                     'shared_platform/publication_rounds.py','shared_platform/publication_image_qa.py',
                     'shared_platform/publication_stock_policy.py','modules/sourcing/image_generation_checkpoint.py'):
        target=root/relative; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,target)
    for relative in ('core/config.py','core/db.py'):
        target=root/relative; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(("SETTINGS_BINDING_CONTRACT = 'orbit-settings-binding/v1'\n" if relative.endswith('config.py') else '')+
                          "raise RuntimeError('CONFIG_DB_IMPORT_FORBIDDEN')\n")
    (root/'modules/sourcing/new_product_workbench.py').write_text('''import json
from pathlib import Path
def load_state(offer_id, *, state_dir=None):
    if state_dir is None: raise RuntimeError('SOURCE_STATE_FALLBACK_FORBIDDEN')
    return json.loads((Path(state_dir)/(offer_id+'.json')).read_text())
''')
    (root/'modules/sourcing/lingshi_client.py').write_text('''class LingshiClient:
    def __init__(self,*a,**k): raise RuntimeError('CLIENT_INIT_FORBIDDEN')
    @classmethod
    def from_config(cls,*a,**k): raise RuntimeError('CLIENT_CONFIG_FORBIDDEN')
    def chat_completions(self,*a,**k): raise RuntimeError('CLIENT_CHAT_FORBIDDEN')
''')
    (root/'shared_platform/publication_paid_requests.py').write_text('''def load_paid_context(*a,**k):
    raise RuntimeError('PAID_CONTEXT_FORBIDDEN')
''')
    (root/'core/auth.py').write_text("raise RuntimeError('AUTH_IMPORT_FORBIDDEN')\n")
    (root/'guarded_qa.py').write_text('''import runpy,sys
def guard(event,args):
    if event=='import' and str(args[0]).split('.')[0] in {'sqlite3','socket','requests'}:
        raise RuntimeError('NETWORK_DB_IMPORT_FORBIDDEN')
    if event.startswith(('sqlite3.','socket.')): raise RuntimeError('NETWORK_DB_EVENT_FORBIDDEN')
sys.addaudithook(guard)
runpy.run_path(sys.argv.pop(1),run_name='__main__')
''')
    target=producer/IMAGES; target.parent.mkdir(parents=True)
    shutil.copyfile(ROOT/IMAGES,target)
    for relative in ('core/config.py','core/db.py','modules/sourcing/new_product_workbench.py',
                     'shared_platform/publication_rounds.py'):
        target=producer/relative; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/relative,target)
    source_head=commit(root); producer_head=commit(producer)
    paths={}
    for name in ('state_dir','round1_reports_root','qa_output_root'):
        paths[name]=tmp_path/name; paths[name].mkdir()
    paths['r2_reports_root']=producer/'reports/product-preparation'
    paths['r2_reports_root'].mkdir(parents=True)
    paths['phase_lock_root']=paths['r2_reports_root']
    paths['assessment_path']=tmp_path/'captured-assessment.json'
    profile={'schema':'orbit-agent-entry/v2','entry_mode':'qa-existing-assessment',
             'source_root':str(root),'expected_source_head':source_head,
             'r2_producer_mode':'direct-cli-source-reports','r2_producer_source_root':str(producer),
             'expected_r2_producer_head':producer_head, **{k:str(v) for k,v in paths.items()}}
    provenance={'authority':'synthetic captured fixture, no real action', 'source_head':source_head,
                'producer_source_head':producer_head, 'qa_script_sha256':hashlib.sha256((root/QA).read_bytes()).hexdigest()}
    snapshot={'schema_version':'round1-approved-snapshot/v1','status':'APPROVED','offer_id':'123',
              'product_approval_id':'synthetic-freeze-123','product_approval_fingerprint':'synthetic-facts-v1',
              'workbench_tiktok_sites':['lh_my'],'canonical_targets':['tiktok:LH_MY'],
              'image_plan':{'brand_plans':[{'id':'livelyhive-sea','generated_assets':[{'role':'cover','quantity':1}]}]},
              'source_provenance':provenance}
    snapshot['snapshot_digest']=digest(snapshot)
    state={'offer_id':'123','product_approval':{'status':'approved','approval_id':snapshot['product_approval_id'],
                                            'input_fingerprint':snapshot['product_approval_fingerprint']},
           'review':{'selected_sites':['lh_my']},'source_provenance':provenance}
    generated={'status':'BRAND_IMAGE_REVIEW_REQUIRED','assets':[{'brand_id':'livelyhive-sea','role':'cover',
        'status':'COMPLETED','artifact_digest':'sha256:'+hashlib.sha256(b'synthetic master bytes').hexdigest()}],
               'source_provenance':provenance}
    assessment={'schema_version':'lingshi-image-qa-assessment/v1','status':'PASSED','offer_id':'123',
                'artifact_digests':[generated['assets'][0]['artifact_digest']],
                'checks':[{'code':code,'status':'PASSED'} for code in ('FACTUAL_ALIGNMENT','OCR_LANGUAGE','DUPLICATION')],
                'source_provenance':provenance}
    assessment['assessment_digest']=digest(assessment)
    documents={paths['state_dir']/'123.json':state,
               paths['round1_reports_root']/'123/round1-approved-snapshot.json':snapshot,
               paths['r2_reports_root']/'123/brand-image-generation.json':generated,
               paths['assessment_path']:assessment}
    for path,document in documents.items():
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(document))
    # Decoys are deliberately invalid and must never select the source/cwd inputs.
    for directory in (root/'reports/product-preparation/123',tmp_path/'reports/product-preparation/123'):
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'brand-image-generation.json').write_text('{"status":"DECOY_SOURCE_OR_CWD"}')
    profile_path=tmp_path/'profile.json'
    def save(): profile_path.write_text(json.dumps(profile))
    def invoke(check=False, arguments=None, runner=WRAPPER, extra_env=None):
        save(); argv=[sys.executable,'-I','-B',str(runner),'--profile',str(profile_path),'--entry','qa']
        if check: argv.append('--check-binding')
        if arguments is None and not check: arguments=['--offer-id','123','--assessment',str(paths['assessment_path'])]
        if arguments: argv.extend(['--',*arguments])
        env=environment(); env.update(extra_env or {})
        return subprocess.run(argv,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    return {'root':root,'producer':producer,'profile':profile,'paths':paths,'profile_path':profile_path,
            'documents':documents,'invoke':invoke,'save':save,'tmp':tmp_path}


def test_actual_assessment_uses_split_inputs_output_and_original_producer_lock(qa_fixture):
    f=qa_fixture; originals={path:path.read_bytes() for path in f['documents']}
    result=f['invoke']()
    assert result.returncode==0,result.stderr+result.stdout
    value=json.loads(result.stdout)
    output=f['paths']['qa_output_root']/'123/automated-image-qa.json'
    assert value['status']=='PASSED' and Path(value['report_path'])==output
    assert value['paid_requests'] is None and value['platform_writes']==0
    assert output.is_file() and (output.parent/'lingshi-image-qa-assessment.json').is_file()
    assert len(list((f['paths']['phase_lock_root']/'123').glob('.lingshi-*.lock')))==1
    assert not list(output.parent.glob('.lingshi-*.lock'))
    assert not (f['root']/'reports/product-preparation/123/automated-image-qa.json').exists()
    assert not (f['paths']['r2_reports_root']/'123/automated-image-qa.json').exists()
    assert not list(f['tmp'].rglob('*.db')) and not list(f['tmp'].rglob('events.jsonl'))
    assert originals=={path:path.read_bytes() for path in originals}


def test_check_binding_is_metadata_only_with_copied_checker_and_poison_domains(qa_fixture):
    f=qa_fixture; copy=f['tmp']/'.codex/skills/copied/scripts/entry.py'; copy.parent.mkdir(parents=True)
    shutil.copyfile(WRAPPER,copy)
    result=f['invoke'](check=True,runner=copy)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value['domain_imported'] is False and value['sql_connections']==value['provider_calls']==0
    assert value['source_root']==str(f['root'])
    assert value['field_propagation']['master_qa_reports_root']['supported'] is False
    assert value['field_propagation']['catalog_database']['supported'] is False
    assert not (f['paths']['qa_output_root']/'123').exists()


@pytest.mark.parametrize('kind',['state','r1','r2','assessment'])
def test_missing_captured_input_blocks_before_local_writes(qa_fixture,kind):
    f=qa_fixture
    paths={'state':f['paths']['state_dir']/'123.json',
           'r1':f['paths']['round1_reports_root']/'123/round1-approved-snapshot.json',
           'r2':f['paths']['r2_reports_root']/'123/brand-image-generation.json',
           'assessment':f['paths']['assessment_path']}
    paths[kind].unlink()
    result=f['invoke']()
    assert result.returncode!=0 and 'QA_CAPTURED_' in result.stderr
    assert not (f['paths']['qa_output_root']/'123').exists()
    assert not list((f['paths']['r2_reports_root']/'123').glob('.lingshi-*.lock'))


@pytest.mark.parametrize('argv',[['--off','123'],['--ass','X:/drift'],['--repo-r','X:/drift'],
    ['--binding-pro','X:/drift'],['--model','synthetic-paid'],['--paid-policy','X:/policy'],
    ['--verified-local-assets'],['--execute-paid'],['--entry-mode','paid'],
    ['--assessment','X:/drift'],['--offer-id','123','--offer-id','999']])
def test_canonical_mode_and_root_arguments_cannot_drift(qa_fixture,argv):
    f=qa_fixture; result=f['invoke'](arguments=argv)
    assert result.returncode!=0 and ('QA_' in result.stderr or 'ENTRY_' in result.stderr)
    assert not (f['paths']['qa_output_root']/'123').exists()


@pytest.mark.parametrize('name',['ORBIT_WORKBENCH_STORE_PATH','ORBIT_HIVE_SETTINGS',
                               'ORBIT_CATALOG_DATABASE','ORBIT_OPERATIONS_PROFILE','LINGSHI_IMAGE_QA_MODEL'])
def test_ambient_override_cannot_switch_frozen_assessment_mode(qa_fixture,name):
    f=qa_fixture; result=f['invoke'](extra_env={name:str(f['tmp']/'foreign-input')})
    assert result.returncode!=0 and 'ENV_BINDING_' in result.stderr
    assert not (f['paths']['qa_output_root']/'123').exists()


@pytest.mark.parametrize('change',['lock','reports','runtime-mode','producer-head','source-head','capability'])
def test_source_and_original_direct_producer_lock_proof_is_required(qa_fixture,change):
    f=qa_fixture; profile=f['profile']
    if change=='lock': profile['phase_lock_root']=profile['qa_output_root']
    elif change=='reports': profile['r2_reports_root']=str(f['paths']['round1_reports_root'])
    elif change=='runtime-mode': profile['r2_producer_mode']='native-runtime'
    elif change=='producer-head': profile['expected_r2_producer_head']='0'*40
    elif change=='source-head': profile['expected_source_head']='0'*40
    else:
        path=f['root']/QA
        text=path.read_text(); text=text.replace("QA_ASSESSMENT_BINDING_CONTRACT = 'orbit-qa-assessment-paths/v2'",'')
        path.write_text(text); profile['expected_source_head']=commit(f['root'])
    result=f['invoke'](check=True)
    assert result.returncode!=0 and ('QA_' in result.stderr or 'SOURCE_' in result.stderr)
    assert not (f['paths']['qa_output_root']/'123').exists()


def test_leaf_profile_digest_and_abbreviations_reject_before_actions(qa_fixture):
    f=qa_fixture; f['save']()
    base=[sys.executable,'-I','-B',str(f['root']/'guarded_qa.py'),str(f['root']/QA),
          '--offer-id','123','--assessment',str(f['paths']['assessment_path'])]
    for technical in (['--binding-profile',str(f['profile_path']),'--binding-profile-sha256','0'*64],
                      ['--binding-pro',str(f['profile_path'])]):
        result=subprocess.run([*base,*technical],cwd=f['tmp'],env=environment(),capture_output=True,text=True,timeout=15)
        assert result.returncode!=0
        assert 'QA_PROFILE_DIGEST_CHANGED' in result.stderr or 'unrecognized arguments' in result.stderr
    assert not (f['paths']['qa_output_root']/'123').exists()


def test_signed_superseded_attempt_archives_only_under_qa_output(qa_fixture):
    f=qa_fixture; assert f['invoke']().returncode==0
    output=f['paths']['qa_output_root']/'123/automated-image-qa.json'
    first=output.read_bytes(); first_doc=json.loads(first)
    source_bytes=(f['paths']['r2_reports_root']/'123/brand-image-generation.json').read_bytes()
    assessment=json.loads(f['paths']['assessment_path'].read_text())
    assessment['checks'][1]['status']='FAILED'; assessment.pop('assessment_digest')
    assessment['assessment_digest']=digest(assessment)
    f['paths']['assessment_path'].write_text(json.dumps(assessment))
    result=f['invoke']()
    assert result.returncode==2 and json.loads(result.stdout)['status']=='FAILED'
    archived=output.parent/'image-qa-attempts'/(first_doc['qa_digest'].removeprefix('sha256:')+'.json')
    assert json.loads(archived.read_bytes())==json.loads(first)
    assert source_bytes==(f['paths']['r2_reports_root']/'123/brand-image-generation.json').read_bytes()
    assert not (f['paths']['r2_reports_root']/'123/image-qa-attempts').exists()


@pytest.mark.parametrize('field,value',[('entry_mode','paid-qa'),('master_qa_reports_root','X:/unused'),
                                      ('config_root','X:/unused')])
def test_unconsumed_paths_and_other_modes_are_not_advertised(qa_fixture,field,value):
    f=qa_fixture; f['profile'][field]=value
    result=f['invoke'](check=True)
    assert result.returncode!=0 and ('UNSUPPORTED' in result.stderr)
    assert not (f['paths']['qa_output_root']/'123').exists()


def test_output_offer_directory_alias_is_rejected_before_lock(qa_fixture):
    f=qa_fixture; foreign=f['tmp']/'foreign-output'; foreign.mkdir()
    alias=f['paths']['qa_output_root']/'123'
    try:
        alias.symlink_to(foreign,target_is_directory=True)
    except OSError:
        if os.name != 'nt': raise
        subprocess.run(['cmd','/c','mklink','/J',str(alias),str(foreign)],check=True,capture_output=True)
    result=f['invoke']()
    assert result.returncode!=0 and 'QA_OUTPUT_REPARSE_REJECTED' in result.stderr
    assert not list(foreign.iterdir())
    assert not list((f['paths']['phase_lock_root']/'123').glob('.lingshi-*.lock'))


def test_profile_changed_while_waiting_for_phase_lock_blocks_output(qa_fixture):
    f=qa_fixture; checkpoint=f['root']/'modules/sourcing/image_generation_checkpoint.py'
    with checkpoint.open('a') as stream:
        stream.write('''
_original_business_lock=business_lock
@contextmanager
def business_lock(root, business_digest, **kwargs):
    with _original_business_lock(root,business_digest,**kwargs):
        Path(os.environ['SYNTHETIC_PROFILE_TO_MUTATE']).write_text('{}')
        yield
''')
    f['profile']['expected_source_head']=commit(f['root'])
    result=f['invoke'](extra_env={'SYNTHETIC_PROFILE_TO_MUTATE':str(f['profile_path'])})
    assert result.returncode!=0 and 'QA_PROFILE_DIGEST_CHANGED' in result.stderr
    assert not (f['paths']['qa_output_root']/'123').exists()


@pytest.mark.parametrize('kind',['state','r1','r2','assessment','phase-lock'])
def test_captured_input_and_original_lock_aliases_are_rejected(qa_fixture,kind):
    f=qa_fixture
    if kind=='phase-lock':
        foreign=f['paths']['phase_lock_root']; alias=f['tmp']/'phase-alias'
        f['profile']['phase_lock_root']=str(alias)
    else:
        alias={'state':f['paths']['state_dir']/'123.json',
               'r1':f['paths']['round1_reports_root']/'123/round1-approved-snapshot.json',
               'r2':f['paths']['r2_reports_root']/'123/brand-image-generation.json',
               'assessment':f['paths']['assessment_path']}[kind]
        foreign=f['tmp']/('foreign-'+kind+'.json'); foreign.write_bytes(alias.read_bytes()); alias.unlink()
    try:
        alias.symlink_to(foreign,target_is_directory=kind=='phase-lock')
    except OSError:
        if os.name!='nt' or kind!='phase-lock': raise
        subprocess.run(['cmd','/c','mklink','/J',str(alias),str(foreign)],check=True,capture_output=True)
    result=f['invoke']()
    assert result.returncode!=0 and 'REPARSE_REJECTED' in result.stderr
    assert not (f['paths']['qa_output_root']/'123').exists()
    assert not list((f['paths']['r2_reports_root']/'123').glob('.lingshi-*.lock'))


def test_v1_original_layout_metadata_still_accepts_canonical_qa_flags(qa_fixture):
    f=qa_fixture; root=f['root']; (root/'config').mkdir(); (root/'data').mkdir()
    settings=root/'config/selected.json'; settings.write_text('{}')
    head=commit(root)
    f['profile'].clear()
    f['profile'].update(schema='orbit-agent-entry/v1',source_root=str(root),expected_source_head=head,
        settings_path=str(settings),config_root=str(root/'config'),data_root=str(root/'data'),output_root=str(root/'reports'))
    result=f['invoke'](check=True,arguments=['--offer-id','123','--model','original-approved-model'])
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['settings_override_supported'] is True


@pytest.mark.parametrize('input_root',['round1_reports_root','r2_reports_root'])
def test_qa_output_cannot_overlap_retained_input_root(qa_fixture,input_root):
    f=qa_fixture; f['profile']['qa_output_root']=f['profile'][input_root]
    result=f['invoke'](check=True)
    assert result.returncode!=0 and 'QA_INPUT_OUTPUT_ROOT_OVERLAP' in result.stderr


def test_original_phase_lock_file_alias_cannot_write_foreign_file(qa_fixture):
    f=qa_fixture; foreign=f['tmp']/'foreign-lock'; foreign.write_bytes(b'untouched foreign lock')
    phase=digest({'scope':'round2-phase','offer_id':'123'}).removeprefix('sha256:')
    path=f['paths']['phase_lock_root']/'123'/f'.lingshi-{phase[:24]}.lock'
    path.symlink_to(foreign)
    result=f['invoke']()
    assert result.returncode!=0 and 'QA_PHASE_LOCK_REPARSE_REJECTED' in result.stderr
    assert foreign.read_bytes()==b'untouched foreign lock'
    assert not (f['paths']['qa_output_root']/'123').exists()


def test_output_archive_alias_is_rejected_before_lock(qa_fixture):
    f=qa_fixture; folder=f['paths']['qa_output_root']/'123'; folder.mkdir()
    foreign=f['tmp']/'foreign-archive'; foreign.mkdir()
    (folder/'image-qa-attempts').symlink_to(foreign,target_is_directory=True)
    result=f['invoke']()
    assert result.returncode!=0 and 'QA_OUTPUT_REPARSE_REJECTED' in result.stderr
    assert not list(foreign.iterdir())
    assert not list((f['paths']['phase_lock_root']/'123').glob('.lingshi-*.lock'))
