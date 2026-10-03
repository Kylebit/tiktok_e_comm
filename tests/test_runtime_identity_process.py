import http.client
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from shared_platform import runtime_identity as identity
from scripts.stable_runtime_bootstrap import STORE_ENV
ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.run(['git', '-c', 'safe.directory=' + root.as_posix(), '-c', 'user.name=U00 Fixture',
                           '-c', 'user.email=u00-fixture@invalid', '-C', str(root), *args],
                          capture_output=True, text=True, encoding='utf-8', check=True)


@pytest.fixture
def code_fixture(tmp_path):
    root = tmp_path / 'code'
    for name in identity.ENTRY_FILES + identity.ASSET_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('generated non-production fixture\n', encoding='utf-8')
    (root / 'shared_platform/entry_catalog.json').write_text('{"supply_images":[]}', encoding='utf-8')
    settings = root / 'config/settings.json'; settings.parent.mkdir(exist_ok=True)
    ozon = root / 'ozon'; ozon.mkdir()
    settings.write_text(json.dumps({'ozon': {'data_dir': str(ozon)}}), encoding='utf-8')
    profile = {'profile_id': 'u00-process-fixture', 'settings_path': str(settings), 'stores': {
        'catalog': str(root/'data/shop.db'), 'reports_release': str(root/'data/orbit_platform.db'),
        'workbench': str(root/'data/orbit_workbench.db'), 'ozon': str(ozon)}}
    (root/'runtime-profile.json').write_text(json.dumps(profile), encoding='utf-8')
    git(root, 'init'); git(root, 'add', '.'); git(root, 'commit', '-m', 'generated fixture base')
    return root


def start(root):
    # The synthetic child builds its own profile; inherited store pins belong
    # to the parent runtime and would make this fixture a different deployment.
    child_env = os.environ.copy()
    for name in STORE_ENV.values():
        child_env.pop(name, None)
    process = subprocess.Popen([sys.executable, '-B', str(ROOT/'tests/runtime_identity_process.py'), str(root)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                               env=child_env)
    line = process.stdout.readline()
    assert line, process.stderr.read()
    return process, json.loads(line)


def health(port):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        conn.request('GET','/api/health'); response=conn.getresponse()
        assert response.status==200
        return json.loads(response.read())
    finally: conn.close()


def test_process_fixture_uses_its_own_store_profile(code_fixture, monkeypatch):
    monkeypatch.setenv('ORBIT_CATALOG_DATABASE', str(code_fixture / 'other-catalog.db'))
    monkeypatch.setenv('ORBIT_REPORT_STORE_PATH', str(code_fixture / 'other-report.db'))
    process, started = start(code_fixture)
    try:
        assert health(started['port'])['state'] == 'READY'
    finally:
        process.terminate(); process.wait(timeout=5)


@pytest.mark.parametrize('change,before_first_health', [('source', True), ('source', False), ('commit', True), ('config-path', True), ('config-bytes', False)])
def test_live_process_keeps_startup_identity_until_own_restart(code_fixture, change, before_first_health, request):
    root=code_fixture; process, started=start(root)
    try:
        if not before_first_health: assert health(started['port'])['state']=='READY'
        if change=='source': (root/'main.py').write_text('changed generated implementation\n',encoding='utf-8')
        elif change=='commit': git(root,'commit','--allow-empty','-m','generated next commit')
        elif change=='config-path':
            destination=root/'config/settings-b.json';destination.write_text((root/'config/settings.json').read_text(encoding='utf-8'),encoding='utf-8')
            (root/'selected-settings.txt').write_text(str(destination),encoding='utf-8')
            profile=json.loads((root/'runtime-profile.json').read_text(encoding='utf-8'))
            profile['settings_path']=str(destination)
            (root/'runtime-profile.json').write_text(json.dumps(profile),encoding='utf-8')
        else: (root/'config/settings.json').write_text((root/'config/settings.json').read_text(encoding='utf-8')+'\n',encoding='utf-8')
        old=health(started['port'])
        assert old['state']==('SOURCE_MISMATCH' if change in {'source','commit'} else 'CONFIG_MISMATCH')
        assert old['commit']==started['startup']['commit'] and old['build']==started['startup']['build']
        request.node.user_properties.append(('old_pid',started['pid']))
    finally:
        process.terminate();process.wait(timeout=5)
    process,restarted=start(root)
    try:
        assert restarted['pid']!=started['pid']
        assert health(restarted['port'])['state']=='READY'
        request.node.user_properties.append(('new_pid',restarted['pid']))
    finally:process.terminate();process.wait(timeout=5)
