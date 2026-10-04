"""R1 scoped-path contract; only temporary captured inputs and domain stubs."""
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
from test_agent_entry_binding import ROOT, ENTRY, ENTRIES, synthetic, isolated_entry_env


def commit_fixture(root):
    subprocess.run(['git','-C',str(root),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Synthetic Fixture','-c',
        'user.email=fixture@example.invalid','commit','-qm','synthetic captured R1 provenance'],
        check=True,capture_output=True)
    return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()


@pytest.fixture
def v2(synthetic, tmp_path):
    root, profile, invoke=synthetic
    profile['schema']='orbit-agent-entry/v2'
    for key in ('config_root','data_root','state_dir','source_outputs_root','content_outputs_root','output_root'):
        path=tmp_path/key; path.mkdir(); profile[key]=str(path)
    settings=Path(profile['config_root'])/'selected.json'; settings.write_text('{}')
    profile['settings_path']=str(settings)
    for key in ('catalog_database','release_store_path','report_store_path','workbench_store_path','lingshi_config_path'):
        path=tmp_path/(key+'.synthetic'); path.write_text('synthetic metadata, no credentials or SQLite')
        profile[key]=str(path)
    for relative in ('shared_platform/release_control.py','modules/sourcing/pipeline.py',
                     'modules/sourcing/manual_product_intake.py'):
        path=root/relative; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text("R1_PATH_BINDING_CONTRACT = 'orbit-r1-paths/v2'\nraise RuntimeError('DOMAIN_IMPORT_FORBIDDEN')\n")
    workbench=root/'modules/sourcing/new_product_workbench.py'
    workbench.write_text("R1_PATH_BINDING_CONTRACT = 'orbit-r1-paths/v2'\nraise RuntimeError('DOMAIN_IMPORT_FORBIDDEN')\n")
    target=root/ENTRIES['preparation']
    target.write_text("R1_PATH_BINDING_CONTRACT = 'orbit-r1-paths/v2'\nraise RuntimeError('DOMAIN_IMPORT_FORBIDDEN')\n")
    (root/'scripts').mkdir()
    shutil.copyfile(ENTRY,root/'scripts/repo_bound_agent_entry.py')
    profile['expected_source_head']=commit_fixture(root)
    return root,profile,invoke


def test_v2_separate_inputs_are_bound_without_domain_import(v2):
    root,profile,invoke=v2
    result=invoke()
    assert result.returncode == 0,result.stderr
    value=json.loads(result.stdout)
    for key in ('config_root','data_root','state_dir','output_root','source_outputs_root','content_outputs_root'):
        assert value[key] == profile[key]
        assert value['field_propagation'][key]['supported'] is True
    assert value['domain_imported'] is False
    assert value['sql_connections'] == value['provider_calls'] == 0
    assert value['field_propagation']['lingshi_config_path']['scope'] == 'metadata discovery only'


@pytest.mark.parametrize('entry',['images','qa','delist'])
def test_v2_unpropagated_entries_fail_explicitly(v2,entry):
    root,profile,invoke=v2
    result=invoke(entry)
    assert result.returncode != 0
    assert 'ENTRY_PATH_BINDING_UNSUPPORTED_V2' in result.stderr


@pytest.mark.parametrize('key',['state_dir','source_outputs_root','content_outputs_root','release_store_path'])
def test_v2_missing_inputs_are_not_recreated(v2,tmp_path,key):
    root,profile,invoke=v2
    missing=tmp_path/(key+'-missing'); profile[key]=str(missing)
    result=invoke()
    assert result.returncode != 0
    assert key.upper()+'_MISSING' in result.stderr
    assert not missing.exists()


def test_v2_conflicting_ambient_workbench_is_rejected(v2,tmp_path):
    root,profile,invoke=v2
    result=invoke(env={'ORBIT_WORKBENCH_STORE_PATH':str(tmp_path/'old.db')})
    assert 'ENV_BINDING_CONFLICT: ORBIT_WORKBENCH_STORE_PATH' in result.stderr


@pytest.mark.parametrize('argv',[['--repo-r','X:/drift'],['--data-r=X:/drift'],
    ['--binding-pro','X:/other-profile'],['--wor','X:/drift']])
def test_v2_argv_root_and_binding_abbreviations_cannot_drift(v2,argv):
    root,profile,invoke=v2
    result=invoke(arguments=argv)
    assert result.returncode != 0
    assert 'ENTRY_' in result.stderr


def install_actual_r1_with_dashboard_stub(root, profile):
    for relative in (ENTRIES['preparation'],'scripts/repo_bound_agent_entry.py',
        'shared_platform/publication_rounds.py','shared_platform/publication_stock_policy.py',
        'shared_platform/r1_input_lineage.py',
        'shared_platform/round1_category_evidence.py','domains/product_operations/sku_display_name.py'):
        target=root/relative; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,target)
    server=root/'modules/products/server.py'; server.parent.mkdir(parents=True,exist_ok=True)
    server.write_text("raise RuntimeError('SERVER_BOOTSTRAP_FORBIDDEN')\n")
    stub=root/'shared_platform/release_control.py'
    stub.write_text("""R1_PATH_BINDING_CONTRACT = 'orbit-r1-paths/v2'
import json
from pathlib import Path
import sys
SAFETY_COUNTS = {'network':0,'sql':0}
def deny_external(event, arguments):
    if event in ('socket.connect','sqlite3.connect'):
        kind = 'network' if event == 'socket.connect' else 'sql'
        SAFETY_COUNTS[kind] += 1
        raise RuntimeError('REAL_PROVIDER_OR_SQL_FORBIDDEN')
sys.addaudithook(deny_external)
def build_release_dashboard(*,offer_id,**paths):
    state=json.loads((Path(paths['state_dir'])/(offer_id+'.json')).read_text())
    assert state['offer_id']==offer_id
    trace={key:str(value) for key,value in paths.items()}
    trace['synthetic_safety'] = SAFETY_COUNTS
    (Path(paths['data_root'])/'dashboard-paths.json').write_text(json.dumps(trace))
    return {'revision':state['_revision'],'review':{'selected_sites':['lh_my']},'source':state['source']}
""")
    profile['expected_source_head']=commit_fixture(root)


def test_actual_r1_consumes_separate_captured_roots_and_preserves_source(v2):
    root,profile,invoke=v2
    install_actual_r1_with_dashboard_stub(root,profile)
    state={'offer_id':'123','_revision':7,'source':{'title_source':'Synthetic captured lamp',
        'cost_cny':5,'weight_kg':0.3,'package_cm':[10,20,30],
        'source_authority':'synthetic-captured-fixture','source_record':{'fixture':True}}}
    (Path(profile['state_dir'])/'123.json').write_text(json.dumps(state))
    before=(root/'modules/sourcing/new_product_workbench.py').read_bytes()
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode == 0,result.stderr+result.stdout
    packet=Path(profile['output_root'])/'product-preparation/123/first-review.json'
    assert packet.is_file()
    trace=json.loads((Path(profile['data_root'])/'dashboard-paths.json').read_text())
    for key in ('data_root','state_dir','source_outputs_root','content_outputs_root','release_store_path','report_store_path'):
        assert trace[key] == profile[key]
    assert trace['database_path'] == profile['catalog_database']
    assert before == (root/'modules/sourcing/new_product_workbench.py').read_bytes()
    assert not (root/'reports/product-preparation/123/first-review.json').exists()
    assert not (root/'data/new_product_workbench/123.json').exists()


def test_v2_missing_captured_state_never_imports_bootstrap(v2):
    root,profile,invoke=v2
    install_actual_r1_with_dashboard_stub(root,profile)
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode != 0
    assert 'R1_CAPTURED_STATE_MISSING' in result.stdout+result.stderr
    assert 'SERVER_BOOTSTRAP_FORBIDDEN' not in result.stdout+result.stderr
    assert not (Path(profile['output_root'])/'product-preparation/123/first-review.json').exists()


def test_scoped_source_reader_does_not_change_workbench_globals(tmp_path):
    from modules.sourcing import new_product_workbench as workbench
    original=(workbench.STATE_DIR,workbench.OUTPUTS_DIR,workbench.ROOT)
    data=tmp_path/'data-input'; data.mkdir()
    state=tmp_path/'state-input'; state.mkdir()
    outputs=tmp_path/'captures'; outputs.mkdir()
    (data/'sourcing').mkdir()
    (data/'sourcing/123.json').write_text(json.dumps({'title':'Synthetic captured source','price':{'min':4},'images':{'main':[]}}))
    (state/'123.json').write_text(json.dumps({'offer_id':'123','source':{'source_mode':'captured'},'_revision':7}))
    source=workbench._source_summary('123',state_dir=state,data_root=data,source_outputs_root=outputs)
    assert source['title_source']=='Synthetic captured source'
    assert (workbench.STATE_DIR,workbench.OUTPUTS_DIR,workbench.ROOT)==original


def test_v2_requires_each_source_consumer_capability(v2):
    root,profile,invoke=v2
    path=root/'modules/sourcing/pipeline.py'
    path.write_text("raise RuntimeError('OLD_SOURCE_MUST_NOT_IMPORT')\n")
    profile['expected_source_head']=commit_fixture(root)
    result=invoke()
    assert 'SOURCE_R1_PATH_BINDING_CAPABILITY_MISSING: modules/sourcing/pipeline.py' in result.stderr


def test_v2_settings_must_belong_to_explicit_config_root(v2,tmp_path):
    root,profile,invoke=v2
    outside=tmp_path/'outside.json'; outside.write_text('{}')
    profile['settings_path']=str(outside)
    assert 'SETTINGS_OUTSIDE_CONFIG_ROOT_V2' in invoke().stderr


def test_v2_unknown_profile_path_is_an_explicit_gap(v2):
    root,profile,invoke=v2
    profile['provider_config_root']=profile['config_root']
    assert 'PROFILE_FIELD_UNSUPPORTED_V2: provider_config_root' in invoke().stderr


def test_v2_optional_workbench_missing_is_not_required_or_created(v2):
    root,profile,invoke=v2
    missing=Path(profile.pop('workbench_store_path')); missing.unlink()
    result=invoke()
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['field_propagation']['workbench_store_path']['supported'] is False
    assert not missing.exists()


def test_child_rejects_profile_changed_after_freeze(v2):
    root,profile,invoke=v2
    install_actual_r1_with_dashboard_stub(root,profile)
    frozen=root.parent/'frozen.json'; frozen.write_text(json.dumps(profile))
    result=subprocess.run([sys.executable,'-I','-B',str(root/ENTRIES['preparation']),
        '--offer-id','123','--targets','tiktok:LH_MY','--binding-profile',str(frozen),
        '--binding-profile-sha256','0'*64],env=isolated_entry_env(),capture_output=True,text=True)
    assert result.returncode==2
    assert 'R1_PROFILE_DIGEST_CHANGED' in result.stdout
    assert 'SERVER_BOOTSTRAP_FORBIDDEN' not in result.stdout+result.stderr


def test_actual_dashboard_uses_separate_roots_and_databases_without_writes(tmp_path,monkeypatch):
    from test_release_control import _release_fixture
    from shared_platform import release_control as control
    captured,database=_release_fixture(tmp_path/'captured')
    source=tmp_path/'source'; source.mkdir()
    captures=tmp_path/'source-captures'; captures.mkdir()
    data=captured/'data'; states=data/'new_product_workbench'
    offer='3828811808'
    (data/'sourcing').mkdir()
    (data/'sourcing'/f'{offer}.json').write_text(json.dumps({'title':'Scoped captured source',
        'price':{'min':4.4},'images':{'main':[]},'source_record':{'fixture':True}}))
    ledger=tmp_path/'separate-release.db'; history=tmp_path/'separate-report.db'
    from shared_platform import release_store,report_store
    for path,schema in ((ledger,release_store._SCHEMA),(history,report_store._SCHEMA)):
        with sqlite3.connect(path) as connection:
            connection.executescript(schema)
    ledger_paths=[]
    original_store=release_store.ReleaseStore
    def selected_store(path):
        ledger_paths.append(Path(path)); return original_store(path)
    monkeypatch.setattr(release_store,'ReleaseStore',selected_store)
    calls=[]
    original=control.latest_weekly_profit_summary
    def weekly(path):
        calls.append(Path(path)); return original(path)
    monkeypatch.setattr(control,'latest_weekly_profit_summary',weekly)
    before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in tmp_path.rglob('*') if p.is_file()}
    result=control.build_release_dashboard(offer_id=offer,root=source,database_path=database,
        data_root=data,state_dir=states,source_outputs_root=captures,
        content_outputs_root=captured/'outputs/image_suite_from_miaoshou',
        release_store_path=ledger,report_store_path=history,seller_sku='0946')
    assert result['product']['source_title_zh']=='Scoped captured source'
    assert result['content']['approved'] is True
    assert calls==[history]
    assert ledger_paths==[ledger]
    assert result['safety']['external_writes_performed']==[]
    after={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in tmp_path.rglob('*') if p.is_file()}
    assert before==after


def test_scoped_dashboard_reservations_come_from_bound_state(tmp_path):
    from test_release_control import _release_fixture
    from shared_platform.release_control import build_release_dashboard
    captured,database=_release_fixture(tmp_path/'captured')
    data=captured/'data'; states=data/'new_product_workbench'
    source=tmp_path/'unrelated-source'; source.mkdir()
    (states/'9999999999.json').write_text(json.dumps({'offer_id':'9999999999',
        'product_approval':{'status':'approved','subject_type':'product',
        'subject_id':'9999999999','seller_sku':'990946'}}))
    result=build_release_dashboard(offer_id='3828811808',root=source,database_path=database,
        state_dir=states,report_store_path=tmp_path/'missing-synthetic-history.db',seller_sku='0946')
    assert 'seller_sku is reserved by another workbench or verified TikTok claim' in result['approval_rehearsal']['blockers']


def test_scoped_manual_source_preserves_existing_authority(tmp_path):
    from test_u01_manual_intake import packet
    from modules.sourcing import manual_product_intake as manual
    captured=tmp_path/'captured'
    original=manual.create_manual_intake(packet(),root=captured)
    source=tmp_path/'unrelated-source'; source.mkdir()
    value=manual.load_manual_source(original['offer_id'],root=source,data_root=captured/'data')
    assert value['source_authority']=='manual-intake'
    assert value['title_source']=='Local blue tile'
