"""Actual public asset bytes cannot silently leave a running identity READY.

The fixture follows test_unified_entry's isolated identity/config contract,
without importing the HTTP server or starting any service. Only Git identity
and optional package discovery are fixed; real file hashing, startup capture,
health comparison and public-root containment execute unchanged.
"""
from pathlib import Path
import json
import shutil

import pytest

from shared_platform import runtime_identity as identity

ROOT = Path(__file__).resolve().parents[1]
TASK_ASSETS = ('web/task_workspace.html', 'web/static/task_workspace.css',
               'web/static/task_workspace.js')
CASES = [(asset, action) for asset in TASK_ASSETS for action in ('append', 'delete', 'escape')]
CASES += [('web/static/knowledge_guides.json', 'append'),
          ('web/static/knowledge_guides.css', 'append')]


@pytest.fixture
def isolated_asset_identity(tmp_path, monkeypatch):
    from core import config
    from shared_platform import report_store, release_store, workbench_store

    root = tmp_path / 'private-code'
    # Copy actual source bytes, including the assets missing from the old list.
    for name in set(identity.ENTRY_FILES + identity.ASSET_FILES + TASK_ASSETS):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (root / 'shared_platform/entry_catalog.json').write_text('{"supply_images":[]}', encoding='utf-8')
    settings = root / 'config/settings.json'
    settings.parent.mkdir(exist_ok=True)
    settings.write_text('{}', encoding='utf-8')
    ozon = root / 'ozon'
    ozon.mkdir()
    for name in ('ORBIT_CATALOG_DATABASE', 'ORBIT_REPORT_STORE_PATH',
                 'ORBIT_RELEASE_STORE_PATH', 'ORBIT_WORKBENCH_STORE_PATH', 'ORBIT_OZON_DATA_ROOT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, 'CONFIG_PATH', settings)
    monkeypatch.setattr(config, 'FALLBACK_CONFIG_PATHS', [])
    monkeypatch.setattr(config, '_cache', {'ozon': {'data_dir': str(ozon)}})
    monkeypatch.setattr(config, '_cache_source', settings)
    monkeypatch.setattr(config, '_cache_source_stat', (settings.stat().st_size, settings.stat().st_mtime_ns))
    store = root / 'data/orbit_platform.db'
    workbench = root / 'data/orbit_workbench.db'
    monkeypatch.setattr(report_store, 'DEFAULT_REPORT_STORE_PATH', store)
    monkeypatch.setattr(release_store, 'DEFAULT_RELEASE_STORE_PATH', store)
    monkeypatch.setattr(workbench_store, 'DEFAULT_WORKBENCH_STORE_PATH', workbench)
    monkeypatch.setattr(workbench_store.WorkbenchStore.__init__, '__defaults__', (workbench,))
    profile = root / 'runtime-profile.json'
    profile.write_text(json.dumps({'profile_id': 'private-task-asset-identity',
        'settings_path': str(settings), 'stores': {
            'catalog': str(root / 'data/shop.db'), 'reports_release': str(store),
            'workbench': str(workbench), 'ozon': str(ozon)}}), encoding='utf-8')
    monkeypatch.setenv('ORBIT_RUNTIME_PROFILE', str(profile))
    monkeypatch.setattr(identity, '_commit', lambda *_: 'a' * 40)
    original_find_spec = identity.importlib.util.find_spec
    monkeypatch.setattr(identity.importlib.util, 'find_spec',
        lambda name: object() if name in {'requests', 'PIL'} else original_find_spec(name))
    startup = identity.capture_runtime_identity('orbit-hive-local-console', root=root)
    assert identity.health_payload('orbit-hive-local-console', root=root, startup=startup)['state'] == 'READY'
    return root, startup


@pytest.mark.parametrize('asset,action', CASES)
def test_running_home_and_guide_asset_drift_is_detected(isolated_asset_identity, tmp_path, monkeypatch, asset, action):
    root, startup = isolated_asset_identity
    target = root / asset
    outside = tmp_path / ('outside-' + target.name)
    outside_reads = []
    if action == 'append':
        # Size changes as well as content, deliberately within existing cache semantics.
        target.write_bytes(target.read_bytes() + b'\n/* private identity mutation */\n')
    elif action == 'delete':
        target.unlink()
    else:
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
        read_bytes = Path.read_bytes
        def tracked_read(path):
            if path.resolve() == outside.resolve():
                outside_reads.append(str(path))
            return read_bytes(path)
        monkeypatch.setattr(Path, 'read_bytes', tracked_read)
    actual = identity.health_payload('orbit-hive-local-console', root=root, startup=startup)
    assert actual['state'] == ('ASSET_MISMATCH' if action == 'append' else 'ASSET_MISSING')
    assert actual['assets'] == startup['assets'], 'running startup identity must remain frozen'
    assert actual['identity_only'] is True and actual['business_execution_verified'] is False
    assert outside_reads == [], 'an out-of-root replacement must not be read or hashed'
