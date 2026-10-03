"""Closed entry identity probes; all runtime inputs below are synthetic."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'scripts/repo_bound_agent_entry.py'
ENTRIES = {
    'preparation': 'skills/prepare-product-publication/scripts/prepare_product_publication.py',
    'images': 'skills/prepare-product-images/scripts/prepare_product_images.py',
    'qa': 'skills/prepare-product-images/scripts/run_automated_image_qa.py',
    'delist': 'skills/delist-products-by-sku/scripts/delist_products_by_sku.py',
}


def isolated_entry_env():
    # conftest deliberately installs a synthetic operations profile for domain
    # tests. Entry binding tests define their own bounded process environment.
    return {key:value for key,value in os.environ.items()
            if not (key.startswith('ORBIT_') and key.endswith(('_ROOT','_PATH','_DATABASE','_PROFILE')))}


def config_module():
    spec = importlib.util.spec_from_file_location('synthetic_entry_config', ROOT/'core/config.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_explicit_settings_wins_over_source_and_is_selected_after_import(tmp_path, monkeypatch):
    monkeypatch.delenv('ORBIT_HIVE_SETTINGS', raising=False)
    config = config_module()
    source = tmp_path/'source.json'; source.write_text('{"identity":"source"}')
    selected = tmp_path/'selected.json'; selected.write_text('{"identity":"explicit"}')
    config.CONFIG_PATH = source
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS', str(selected))
    assert config.settings_path().resolve() == selected.resolve()
    assert config.load_settings()['identity'] == 'explicit'


def test_explicit_invalid_settings_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv('ORBIT_HIVE_SETTINGS', raising=False)
    config = config_module()
    source = tmp_path/'source.json'; source.write_text('{}')
    config.CONFIG_PATH = source
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS', str(tmp_path/'absent.json'))
    with pytest.raises(FileNotFoundError, match='EXPLICIT_SETTINGS_MISSING'):
        config.load_settings()


def test_cached_settings_cannot_silently_keep_another_explicit_source(tmp_path, monkeypatch):
    first = tmp_path/'first.json'; first.write_text('{"identity":"first"}')
    second = tmp_path/'second.json'; second.write_text('{"identity":"second"}')
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS', str(first))
    config = config_module(); config.CONFIG_PATH = first
    assert config.load_settings()['identity'] == 'first'
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS', str(second))
    with pytest.raises(RuntimeError, match='SETTINGS_CACHE_SOURCE_CHANGED'):
        config.load_settings()
    with pytest.raises(RuntimeError, match='SETTINGS_CACHE_SOURCE_CHANGED'):
        config.settings_base_dir()
    monkeypatch.delenv('ORBIT_CATALOG_DATABASE', raising=False)
    monkeypatch.setitem(sys.modules, 'core.config', config)
    spec = importlib.util.spec_from_file_location('synthetic_entry_db', ROOT/'core/db.py')
    db = importlib.util.module_from_spec(spec); spec.loader.exec_module(db)
    with pytest.raises(RuntimeError, match='SETTINGS_CACHE_SOURCE_CHANGED'):
        db.db_path()


def test_removing_explicit_selection_cannot_reuse_cache_with_source_default(tmp_path, monkeypatch):
    first = tmp_path/'first.json'; first.write_text('{"identity":"first"}')
    source = tmp_path/'source.json'; source.write_text('{"identity":"source"}')
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS', str(first))
    config = config_module(); config.CONFIG_PATH = source
    config.load_settings()
    monkeypatch.delenv('ORBIT_HIVE_SETTINGS')
    with pytest.raises(RuntimeError, match='SETTINGS_CACHE_SOURCE_CHANGED'):
        config.settings_base_dir()


@pytest.mark.parametrize('name', ENTRIES)
def test_personal_physical_script_rejects_guessed_codex_root(tmp_path, name):
    relative = Path(ENTRIES[name]).relative_to('skills')
    script = tmp_path/'.codex/skills'/relative
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT/ENTRIES[name], script)
    result = subprocess.run([sys.executable, '-I', '-B', str(script), '--help'],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'COMPLETE_AGENT_SOURCE_REQUIRED' in result.stderr
    assert 'ModuleNotFoundError' not in result.stderr


@pytest.fixture
def synthetic(tmp_path):
    root = tmp_path/'source'; root.mkdir()
    for relative in ['core/config.py', 'core/db.py',
                     'modules/sourcing/new_product_workbench.py',
                     'shared_platform/publication_rounds.py', *ENTRIES.values()]:
        path = root/relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("raise RuntimeError('DOMAIN_IMPORT_FORBIDDEN')\n")
    (root/'core/config.py').write_text("SETTINGS_BINDING_CONTRACT = 'orbit-settings-binding/v1'\nraise RuntimeError('DOMAIN_IMPORT_FORBIDDEN')\n")
    for name in ('config', 'data', 'reports'):
        (root/name).mkdir(exist_ok=True)
    settings = root/'config/settings.json'; settings.write_text('{}')
    database = root/'data/catalog.db'; database.write_bytes(b'synthetic, not SQLite')
    git_env = {**os.environ, 'GIT_AUTHOR_NAME': 'Synthetic Fixture',
               'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
               'GIT_COMMITTER_NAME': 'Synthetic Fixture',
               'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
    for args in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'synthetic entry fixture']):
        subprocess.run(['git', '-C', str(root), *args], env=git_env, check=True,
                       capture_output=True, text=True)
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    profile = {'schema': 'orbit-agent-entry/v1', 'source_root': str(root),
               'expected_source_head': head, 'settings_path': str(settings),
               'config_root': str(root/'config'), 'data_root': str(root/'data'),
               'output_root': str(root/'reports'), 'catalog_database': str(database)}
    profile_path = tmp_path/'profile.json'
    def invoke(name='preparation', check=True, arguments=(), runner=ENTRY, env=None):
        profile_path.write_text(json.dumps(profile))
        command = [sys.executable, '-I', '-B', str(runner), '--profile', str(profile_path),
                   '--entry', name]
        if check: command.append('--check-binding')
        if arguments: command.extend(['--', *arguments])
        selected_env=isolated_entry_env()
        if env: selected_env.update(env)
        return subprocess.run(command, cwd=tmp_path, env=selected_env, capture_output=True,
                              text=True, timeout=15)
    return root, profile, invoke


@pytest.mark.parametrize('name', ENTRIES)
def test_check_binding_is_stdlib_only_and_cwd_independent(synthetic, name):
    root, profile, invoke = synthetic
    result = invoke(name)
    assert result.returncode == 0, result.stderr
    result = json.loads(result.stdout)
    assert result['source_root'] == str(root.resolve())
    assert result['entry'] == str((root/ENTRIES[name]).resolve())
    assert result['domain_imported'] is False
    assert result['provider_calls'] == result['sql_connections'] == 0


def test_copied_runner_uses_profile_and_execution_keeps_argv(synthetic, tmp_path):
    root, profile, invoke = synthetic
    runner = tmp_path/'.codex/skills/example/scripts/repo_bound_agent_entry.py'
    runner.parent.mkdir(parents=True); shutil.copyfile(ENTRY, runner)
    target = root/ENTRIES['images']
    target.write_text("import json,os,sys\nprint(json.dumps({'file':__file__,'argv':sys.argv[1:],'settings':os.environ['ORBIT_HIVE_SETTINGS'],'catalog':os.environ['ORBIT_CATALOG_DATABASE'],'cwd':os.getcwd(),'git_dir':os.environ.get('GIT_DIR')}))\n")
    subprocess.run(['git', '-C', str(root), 'add', str(target)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Synthetic Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'synthetic dispatch'],
                   check=True, capture_output=True)
    profile['expected_source_head'] = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    env = {'ORBIT_HIVE_SETTINGS': str(tmp_path/'wrong.json'),
           'ORBIT_CATALOG_DATABASE': str(tmp_path/'wrong.db'),
           'GIT_DIR':str(tmp_path/'wrong.git'),'GIT_WORK_TREE':str(tmp_path/'wrong-tree')}
    result = invoke('images', False, ['--offer-id', '001', '--approved-by', 'unchanged actor'], runner, env)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['file'] == str(target.resolve())
    assert value['argv'] == ['--offer-id', '001', '--approved-by', 'unchanged actor']
    assert value['settings'] == profile['settings_path']
    assert value['catalog'] == profile['catalog_database']
    assert value['cwd'] == str(root.resolve())
    assert value['git_dir'] is None


@pytest.mark.parametrize('field,code', [('source_root','SOURCE_ROOT_MISSING'),
    ('settings_path','SETTINGS_PATH_MISSING'), ('data_root','DATA_ROOT_MISSING'),
    ('output_root','OUTPUT_ROOT_MISSING'), ('catalog_database','CATALOG_DATABASE_MISSING')])
def test_missing_bound_inputs_fail_before_domain_import(synthetic, field, code):
    root, profile, invoke = synthetic
    profile[field] = str(root/'missing')
    result = invoke('delist')
    assert result.returncode != 0
    assert code in result.stderr
    assert 'DOMAIN_IMPORT_FORBIDDEN' not in result.stderr


def test_missing_arguments_cannot_start_domain(synthetic):
    root, profile, invoke = synthetic
    result = invoke(check=False)
    assert result.returncode != 0
    assert 'EXPLICIT_ENTRY_ARGUMENTS_REQUIRED' in result.stderr
    assert 'DOMAIN_IMPORT_FORBIDDEN' not in result.stderr


@pytest.mark.parametrize('field', ['config_root', 'data_root', 'output_root'])
def test_separate_roots_are_rejected_instead_of_fake_redirect(synthetic, tmp_path, field):
    root, profile, invoke = synthetic
    other = tmp_path/field; other.mkdir()
    profile[field] = str(other)
    result = invoke()
    code = {'config_root':'CONFIG', 'data_root':'DATA', 'output_root':'OUTPUT'}[field]
    assert 'SEPARATE_' + code + '_ROOT_UNSUPPORTED_V1' in result.stderr


def test_wrong_head_and_dirty_source_cannot_dispatch(synthetic):
    root, profile, invoke = synthetic
    original = profile['expected_source_head']
    profile['expected_source_head'] = '0'*40
    assert 'SOURCE_HEAD_MISMATCH' in invoke().stderr
    profile['expected_source_head'] = original
    (root/ENTRIES['preparation']).write_text('changed source')
    assert 'SOURCE_DIRTY' in invoke().stderr


def test_explicit_relative_settings_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS', 'relative-settings.json')
    config = config_module()
    with pytest.raises(ValueError, match='EXPLICIT_SETTINGS_MUST_BE_ABSOLUTE'):
        config.load_settings()


def test_check_binding_audit_hook_forbids_domain_sql_network_and_input_reads(synthetic, tmp_path):
    root, profile, invoke = synthetic
    profile_path = tmp_path/'audit-profile.json'
    profile_path.write_text(json.dumps(profile))
    code = """import runpy,sys
from pathlib import Path
entry,profile,settings,database=sys.argv[1:]
def audit(event,args):
    if event=='import' and str(args[0]).split('.')[0] in {'core','modules','domains','shared_platform','sqlite3','socket'}:
        raise RuntimeError('DOMAIN_OR_IO_IMPORT_FORBIDDEN')
    if event.startswith(('sqlite3.','socket.')):
        raise RuntimeError('SQL_OR_NETWORK_FORBIDDEN')
    if event=='open' and str(args[0]) in {settings,database}:
        raise RuntimeError('BUSINESS_INPUT_READ_FORBIDDEN')
sys.addaudithook(audit)
sys.argv=[entry,'--profile',profile,'--entry','delist','--check-binding']
runpy.run_path(entry,run_name='__main__')
"""
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ENTRY),
        str(profile_path), profile['settings_path'], profile['catalog_database']],
        cwd=tmp_path, env=isolated_entry_env(), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['domain_imported'] is False


def test_legacy_source_without_settings_capability_cannot_claim_override(synthetic):
    root, profile, invoke = synthetic
    (root/'core/config.py').write_text("raise RuntimeError('DOMAIN_IMPORT_FORBIDDEN')\n")
    subprocess.run(['git','-C',str(root),'add','core/config.py'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Synthetic Fixture','-c',
                    'user.email=fixture@example.invalid','commit','-qm','synthetic legacy config'],
                   check=True,capture_output=True)
    profile['expected_source_head']=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    result=invoke()
    assert result.returncode != 0
    assert 'SOURCE_SETTINGS_BINDING_CAPABILITY_MISSING' in result.stderr


def test_inherited_workbench_path_cannot_redirect_bound_data(synthetic, tmp_path):
    root, profile, invoke=synthetic
    env={'ORBIT_WORKBENCH_STORE_PATH':str(tmp_path/'old-workbench.db')}
    result=invoke(env=env)
    assert result.returncode != 0
    assert 'ENV_BINDING_CONFLICT: ORBIT_WORKBENCH_STORE_PATH' in result.stderr


def test_inherited_workbench_matching_source_default_is_supported(synthetic):
    root,profile,invoke=synthetic
    result=invoke(env={'ORBIT_WORKBENCH_STORE_PATH':str(root/'data/orbit_workbench.db')})
    assert result.returncode == 0, result.stderr


def test_unbound_operations_profile_is_an_explicit_gap(synthetic, tmp_path):
    root,profile,invoke=synthetic
    result=invoke(env={'ORBIT_OPERATIONS_PROFILE':str(tmp_path/'old-profile.json')})
    assert result.returncode != 0
    assert 'ENV_BINDING_UNSUPPORTED_V1: ORBIT_OPERATIONS_PROFILE' in result.stderr


@pytest.mark.parametrize('name,arguments',[('images',['--repo-root','X:/old']),
    ('qa',['--repo-root=X:/old']),('images',['--workdir','X:/old']),
    ('preparation',['--output','X:/old/packet.json'])])
def test_argument_root_drift_cannot_override_profile(synthetic,name,arguments):
    root,profile,invoke=synthetic
    result=invoke(name,arguments=arguments)
    assert result.returncode != 0
    assert 'ENTRY_' in result.stderr
    assert 'DOMAIN_IMPORT_FORBIDDEN' not in result.stderr
