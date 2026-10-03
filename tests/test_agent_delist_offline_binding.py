"""Real synthetic catalog/cache consumers; never use workstation inputs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = 'skills/delist-products-by-sku/scripts/delist_products_by_sku.py'


def environment():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(('GIT_', 'ORBIT_')) and key != 'TIKTOK_E_COMM_ROOT'}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def git(root, *arguments):
    env = environment()
    env.update(GIT_AUTHOR_NAME='Synthetic Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='Synthetic Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    return subprocess.check_output(['git', '-C', str(root), *arguments], env=env, text=True).strip()


@pytest.fixture
def bound(tmp_path):
    root = tmp_path/'selected source'
    root.mkdir()
    files = ('scripts/repo_bound_agent_entry.py', SCRIPT, 'core/config.py', 'core/db.py',
             'modules/sourcing/new_product_workbench.py', 'shared_platform/publication_rounds.py',
             'modules/catalog/ozon_data.py', 'modules/catalog/sku_key.py',
             'modules/catalog/ozon_offline_data.py')
    for name in files:
        source = ROOT/name
        if source.is_file():
            target = root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    # A failed fallback must be observable, including a caught auth exception.
    for name in ('core/auth.py', 'core/shops.py', 'modules/ozon/config.py'):
        target = root/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("raise RuntimeError('POISON: forbidden settings/auth/provider import')\n")
    (root/'core/__init__.py').write_text('''import sys
def _offline_audit(event, args):
    if event.startswith('socket.'):
        raise RuntimeError('POISON: network')
    if event == 'import' and args[0] in ('core.auth','core.shops','modules.ozon.config',
            'modules.shopee.auth','core.api_client','shared_platform.operations_domain_guard'):
        raise RuntimeError('POISON: forbidden domain import '+args[0])
sys.addaudithook(_offline_audit)
''')
    with (root/'core/config.py').open('a', encoding='utf-8') as stream:
        stream.write("\ndef load_settings():\n    raise RuntimeError('POISON: settings contents')\n")
    # Source defaults are deliberately wrong; bound readers must not inspect them.
    write(root/'data/shopee_global_sku_map.json', {'poison': 'default cache'})
    write(root/'reports/product-preparation/default/cache.json', {'seller_sku':'7999'})
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'synthetic source')
    roots = {name:tmp_path/name for name in ('captured reports','cached data','ozon cache','new reports')}
    for directory in roots.values():
        directory.mkdir()
    database = tmp_path/'catalog.db'
    conn = sqlite3.connect(database)
    conn.executescript('''CREATE TABLE shops(cipher TEXT, shop_id TEXT, name TEXT, region TEXT);
CREATE TABLE products(sku_id TEXT, shop_cipher TEXT, product_id TEXT, seller_sku TEXT,
product_name TEXT, sku_name TEXT, status TEXT);
INSERT INTO shops VALUES('synthetic-cipher','synthetic-shop','LivelyHive','PH');
INSERT INTO products VALUES('synthetic-sku','synthetic-cipher','catalog-product','7001','Fixture','','ACTIVATE');''')
    conn.close()
    write(roots['captured reports']/'product-preparation/offer/history.json', {'seller_sku':'7001'})
    write(roots['captured reports']/'product-publication/offer/run/description-media-repair.json',
          {'targets':[{'target_label':'tiktok:LH_MY','product_id':'history-product','after':{'status':'ACTIVATE'}}]})
    write(roots['captured reports']/'product-discounts/miaoshou-product-identity-evidence-fixture.json',
          {'rows':[{'target_label':'tiktok:HB_PH','product_id':'miaoshou-product',
                    'seller_skus':['7001'],'status':'EXACT_IDENTITY'}]})
    write(roots['cached data']/'shopee_global_sku_map.json',
          {'global-fixture':{'models':[{'global_model_sku':'7001'}],
                             'shop_items':{'TH':{'shop_id':'synthetic-shop','item_id':'shopee-product'}}}})
    write(roots['ozon cache']/'all_products_attrs.json', {'result':[{'offer_id':'7001','sku':'ozon-product','name':'Fixture'}]})
    profile = {'schema':'orbit-agent-entry/v2','entry_mode':'delist-offline-diagnostic',
               'source_root':str(root),'expected_source_head':git(root,'rev-parse','HEAD'),
               'catalog_database':str(database),'captured_reports_root':str(roots['captured reports']),
               'data_root':str(roots['cached data']),'ozon_data_root':str(roots['ozon cache']),
               'output_root':str(roots['new reports'])}
    profile_path = tmp_path/'offline-profile.json'
    def save():
        write(profile_path, profile)
    save()
    def invoke(arguments=None, *, check=False, direct=False, extra_env=None, supplied_digest=None):
        args = arguments if arguments is not None else ['plan','--sku','7001','--scope','all','--no-live']
        if direct:
            argv = [sys.executable,'-I','-B',str(root/SCRIPT),*args,'--binding-profile',str(profile_path),
                    '--binding-profile-sha256',supplied_digest or hashlib.sha256(profile_path.read_bytes()).hexdigest()]
        else:
            argv = [sys.executable,'-I','-B',str(root/'scripts/repo_bound_agent_entry.py'),
                    '--profile',str(profile_path),'--entry','delist']
            if check:
                argv.append('--check-binding')
            argv.extend(['--',*args])
        env=environment();env.update(extra_env or {})
        return subprocess.run(argv,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    return {'root':root,'roots':roots,'database':database,'profile':profile,'profile_path':profile_path,
            'save':save,'invoke':invoke,'tmp':tmp_path}


def test_full_split_profile_reaches_real_offline_consumers(bound):
    before = hashlib.sha256(bound['database'].read_bytes()).hexdigest()
    result = bound['invoke']()
    assert result.returncode == 0, result.stdout+result.stderr
    output = bound['roots']['new reports']/'product-delisting/7001/delist-plan.json'
    plan = json.loads(output.read_text(encoding='utf-8'))
    found = {row['target_label']:row for row in plan['targets']}
    for label, product in (('tiktok:LH_PH','catalog-product'),('tiktok:LH_MY','history-product'),
                           ('tiktok:HB_PH','miaoshou-product'),('shopee:TH','shopee-product'),('ozon:RU','ozon-product')):
        assert found[label]['product_id'] == product
    assert plan['planning_mode'] == 'OFFLINE_DIAGNOSTIC'
    assert all(row['blocked'] and row['executable'] is False for row in plan['targets'])
    assert hashlib.sha256(bound['database'].read_bytes()).hexdigest() == before
    assert not (bound['root']/'reports/product-delisting').exists()
    payload = dict(plan);supplied=payload.pop('plan_digest')
    assert supplied == 'sha256:'+hashlib.sha256(json.dumps(payload, ensure_ascii=False,
        sort_keys=True, separators=(',',':')).encode()).hexdigest()
    assert plan['path_binding']['profile_sha256'] == hashlib.sha256(bound['profile_path'].read_bytes()).hexdigest()


def assert_no_output(bound):
    assert list(bound['roots']['new reports'].iterdir()) == []
    assert not (bound['root']/'reports/product-delisting').exists()


@pytest.mark.parametrize('arguments', [
    ['execute','--plan','unused.json'], ['readback','--plan','unused.json'],
    ['plan','--sku','7001'], ['plan','--sk','7001','--no-live'],
    ['plan','--sku','7001','--no-l'], ['plan','--sku','7001','--no-live','--no-live'],
    ['plan','--sku','7001','--no-live','--scope','all','--scope=all'],
    ['plan','--sku','7001','--no-live','--scope','store'],
    ['plan','--sku','7001','--no-live','extra'],
    ['plan','--sku','7001','--no-live','--output','outside.json'],
    ['plan','--sku','7001','--no-live','--binding-profile','override.json'],
])
def test_only_offline_canonical_arguments_reach_consumer(bound, arguments):
    for direct in (False, True):
        result=bound['invoke'](arguments,direct=direct)
        assert result.returncode != 0
        assert 'DELIST_OFFLINE_' in result.stderr
        assert 'POISON:' not in result.stderr
        assert_no_output(bound)


def test_equals_and_repeated_exact_sku_flags_preserve_normalization(bound):
    result=bound['invoke'](['plan','--sku=7001','--sku','fixture-7001','--scope=all','--no-live'])
    assert result.returncode == 0, result.stderr
    plan=json.loads((bound['roots']['new reports']/'product-delisting/7001/delist-plan.json').read_text())
    assert plan['requested_skus']==['7001']
    assert len(plan['targets'])==15


@pytest.mark.parametrize('field', ['settings_path','config_root','workbench_store_path','report_store_path'])
def test_unconsumed_configuration_or_store_fields_reject(bound,field):
    bound['profile'][field]=str(bound['tmp']/'unused')
    bound['save']()
    result=bound['invoke'](check=True)
    assert result.returncode==2 and 'PROFILE_FIELD_UNSUPPORTED' in result.stderr
    assert_no_output(bound)


@pytest.mark.parametrize('key', ['ORBIT_HIVE_SETTINGS','ORBIT_OPERATIONS_PROFILE','ORBIT_RELEASE_STORE_PATH','TIKTOK_E_COMM_ROOT'])
def test_ambient_settings_authority_or_path_overrides_reject(bound,key):
    result=bound['invoke'](extra_env={key:str(bound['tmp']/'override')})
    assert result.returncode==2 and 'ENV_BINDING_UNSUPPORTED_DELIST_OFFLINE' in result.stderr
    assert_no_output(bound)


@pytest.mark.parametrize('kind', ['head','dirty','capability','missing-catalog','output-overlap'])
def test_source_and_input_layout_fail_before_reads_or_mkdir(bound,kind):
    if kind=='head':
        bound['profile']['expected_source_head']='0'*40
    elif kind=='dirty':
        (bound['root']/'unexpected.py').write_text('# dirty\n')
    elif kind=='capability':
        target=bound['root']/SCRIPT
        text=target.read_text().replace("DELIST_OFFLINE_BINDING_CONTRACT = 'orbit-delist-offline-paths/v2'",'')
        target.write_text(text,encoding='utf-8')
        git(bound['root'],'add',SCRIPT);git(bound['root'],'commit','-qm','synthetic old capability')
        bound['profile']['expected_source_head']=git(bound['root'],'rev-parse','HEAD')
    elif kind=='missing-catalog':
        bound['profile']['catalog_database']=str(bound['tmp']/'missing.db')
    else:
        bound['profile']['output_root']=str(bound['roots']['captured reports'])
    bound['save']()
    result=bound['invoke']()
    assert result.returncode==2 and 'POISON:' not in result.stderr
    assert_no_output(bound)


@pytest.mark.parametrize('kind', ['catalog','capture-root','nested-prep','nested-publication','ozon-file','output-file'])
def test_root_nested_cache_and_destination_aliases_reject(bound,kind):
    # Real temporary symlinks; no workstation junction/ACL changes or skips.
    if kind=='catalog':
        alias=bound['tmp']/'catalog-link.db';alias.symlink_to(bound['database'])
        bound['profile']['catalog_database']=str(alias);bound['save']()
    elif kind=='capture-root':
        alias=bound['tmp']/'capture-link';alias.symlink_to(bound['roots']['captured reports'],target_is_directory=True)
        bound['profile']['captured_reports_root']=str(alias);bound['save']()
    elif kind=='nested-prep':
        (bound['roots']['captured reports']/'product-preparation/offer/alias.json').symlink_to(bound['profile_path'])
    elif kind=='nested-publication':
        (bound['roots']['captured reports']/'product-publication/offer/escape').symlink_to(bound['tmp'],target_is_directory=True)
    elif kind=='ozon-file':
        (bound['roots']['ozon cache']/'migrated_offers.json').symlink_to(bound['profile_path'])
    else:
        parent=bound['roots']['new reports']/'product-delisting/7001';parent.mkdir(parents=True)
        (parent/'delist-plan.json').symlink_to(bound['profile_path'])
    before=bound['profile_path'].read_bytes()
    result=bound['invoke']()
    assert result.returncode != 0 and 'REPARSE_REJECTED' in result.stderr
    assert bound['profile_path'].read_bytes()==before
    if kind!='output-file':
        assert_no_output(bound)


def test_missing_optional_cache_children_are_blocked_without_creation(bound):
    # Existing empty roots are valid; no cache file is manufactured.
    for field in ('captured_reports_root','data_root','ozon_data_root'):
        empty=bound['tmp']/('empty-'+field);empty.mkdir()
        bound['profile'][field]=str(empty)
    bound['save']()
    result=bound['invoke'](['plan','--sku','7999','--no-live'])
    assert result.returncode==0, result.stderr
    plan=json.loads((bound['roots']['new reports']/'product-delisting/7999/delist-plan.json').read_text())
    assert all(row['status']!='NOT_FOUND' and not row['executable'] for row in plan['targets'])
    assert {row['status'] for row in plan['targets']}=={'BLOCKED_MISSING_IDENTITY','NEEDS_PROVIDER_DISCOVERY'}
    for field in ('captured_reports_root','data_root','ozon_data_root'):
        assert list(Path(bound['profile'][field]).iterdir())==[]


def test_direct_consumer_rejects_wrong_frozen_digest(bound):
    result=bound['invoke'](direct=True,supplied_digest='0'*64)
    assert result.returncode != 0 and 'PROFILE_DIGEST_CHANGED' in result.stderr
    assert 'POISON:' not in result.stderr
    assert_no_output(bound)


def test_metadata_check_has_no_catalog_or_domain_import(bound):
    # Layout validity does not open/validate SQL contents or create output.
    bound['database'].write_bytes(b'not a SQLite database')
    result=bound['invoke'](check=True)
    assert result.returncode==0, result.stderr
    metadata=json.loads(result.stdout)
    assert metadata['sql_connections']==0 and metadata['domain_imported'] is False
    assert metadata['field_propagation']['settings_path']['supported'] is False
    assert_no_output(bound)
    result=bound['invoke']()
    assert result.returncode!=0 and 'database' in result.stderr
    assert_no_output(bound)


def load_module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('kind',['full','empty','resolver-error','malformed-json'])
def test_ozon_default_setup_and_parser_results_match_accepted_source(tmp_path,monkeypatch,kind):
    base=tmp_path/'ozon';base.mkdir()
    if kind=='full':
        write(base/'all_products_attrs.json',{'result':[{'offer_id':'fixture-7001','sku':'attrs','name':'Fixture'}]})
        write(base/'tk_sku_map.json',{'7001':{'seller_sku':'7001','tk_id':'mapped'},
            '7002':{'seller_sku':'7002','tk_id':'migration'},'7003':{'seller_sku':'7003','tk_id':'pending'}})
        write(base/'migrated_offers.json',['7002'])
    elif kind=='malformed-json':
        for name in ('all_products_attrs.json','tk_sku_map.json','migrated_offers.json'):
            (base/name).write_text('{ malformed')
    calls=[]
    config=ModuleType('modules.ozon.config')
    def resolver():
        calls.append('default-resolver')
        if kind=='resolver-error':
            raise RuntimeError('synthetic resolver error')
        return base
    config.ozon_data_dir=resolver
    monkeypatch.setitem(sys.modules,'modules.ozon.config',config)
    original=tmp_path/'accepted_ozon_data.py'
    original.write_text(git(ROOT,'show','22edca2cedbdcb4f0356ad46835c464beeefed92:modules/catalog/ozon_data.py'),encoding='utf-8')
    old=load_module(original,'accepted_ozon_default')
    new=load_module(ROOT/'modules/catalog/ozon_data.py','candidate_ozon_default')
    assert calls==[]  # Both original import paths load config but do not resolve it.
    if kind=='resolver-error':
        for module in (old,new):
            with pytest.raises(RuntimeError,match='synthetic resolver error'):
                module.load_ozon_by_key()
    else:
        expected=old.load_ozon_by_key()
        assert new.load_ozon_by_key()==expected
        from modules.catalog.ozon_offline_data import load_ozon_from_directory
        assert load_ozon_from_directory(base)==expected
        if kind=='full':
            assert expected['7001']['product_id']=='attrs' and expected['7001']['tk_id']=='mapped'
            assert expected['7002']['status']=='live' and expected['7003']['status']=='pending'
    assert calls==['default-resolver','default-resolver']


def test_profile_drift_during_reads_stops_before_output(bound):
    # The real consumer's permanent audit trap belongs only in a fresh child.
    probe=bound['tmp']/'drift_probe.py'
    probe.write_text('''import importlib.util, json, sys
from pathlib import Path
root, profile = map(Path,sys.argv[1:3])
spec=importlib.util.spec_from_file_location('drift_consumer',root/'skills/delist-products-by-sku/scripts/delist_products_by_sku.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
original=module.build_plan
def drift(*args,**kwargs):
    plan=original(*args,**kwargs)
    value=json.loads(profile.read_text());value['unexpected']='changed'
    profile.write_text(json.dumps(value))
    return plan
module.build_plan=drift
sys.argv=[str(root/'skills/delist-products-by-sku/scripts/delist_products_by_sku.py'),
    'plan','--sku','7001','--no-live','--binding-profile',str(profile),
    '--binding-profile-sha256',sys.argv[3]]
module.main()
''',encoding='utf-8')
    result=subprocess.run([sys.executable,'-I','-B',str(probe),str(bound['root']),str(bound['profile_path']),
        hashlib.sha256(bound['profile_path'].read_bytes()).hexdigest()],cwd=bound['tmp'],env=environment(),
        capture_output=True,text=True,timeout=20)
    assert result.returncode!=0 and 'PROFILE_DIGEST_CHANGED' in result.stderr
    assert 'POISON:' not in result.stderr
    assert_no_output(bound)
