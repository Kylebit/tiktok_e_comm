"""Observation scope and new homepage bytes are pinned before app startup."""
from pathlib import Path
import os

import pytest

from scripts.operations_web_entry import prebind_observation_paths
from tests.test_unified_entry import entry_http


def test_unconfigured_entry_discards_inherited_preview_scope(monkeypatch):
    for variable in ('ORBIT_WORK_OBSERVATIONS_MANIFEST', 'ORBIT_WORK_FEEDBACK_HEALTH'):
        monkeypatch.setenv(variable, 'C:/old-preview/observations.json')
    prebind_observation_paths({})
    assert 'ORBIT_WORK_OBSERVATIONS_MANIFEST' not in os.environ
    assert 'ORBIT_WORK_FEEDBACK_HEALTH' not in os.environ


@pytest.mark.parametrize('key,variable', [
    ('work_observations_manifest', 'ORBIT_WORK_OBSERVATIONS_MANIFEST'),
    ('work_feedback_health', 'ORBIT_WORK_FEEDBACK_HEALTH')])
def test_explicit_path_only_and_drift_rejected(key, variable, tmp_path, monkeypatch):
    monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ValueError, match='absolute local'):
        prebind_observation_paths({key: 'relative.json'})
    selected = tmp_path / 'observations.json'
    prebind_observation_paths({key: str(selected)})
    assert os.environ[variable] == str(selected.resolve())
    with pytest.raises(ValueError, match='inherited'):
        prebind_observation_paths({key: str(tmp_path / 'different.json')})


@pytest.mark.parametrize('filename,state', [
    ('web/static/task_overview.js', 'ASSET_MISMATCH'),
    ('web/static/task_overview.css', 'ASSET_MISMATCH'),
    ('shared_platform/workbench_observations.py', 'SOURCE_MISMATCH'),
    ('shared_platform/workbench_source_feedback.py', 'SOURCE_MISMATCH'),
    ('shared_platform/operations_http.py', 'SOURCE_MISMATCH')])
def test_new_home_assets_and_feedback_code_drift_fail_health(filename, state, entry_http, tmp_path, monkeypatch):
    from shared_platform import runtime_identity as identity
    original_find_spec = identity.importlib.util.find_spec
    monkeypatch.setattr(identity.importlib.util, 'find_spec',
        lambda name: object() if name in {'requests', 'PIL'} else original_find_spec(name))
    tree = tmp_path / 'synthetic-source'
    tree.mkdir()
    for name in identity.ENTRY_FILES + identity.ASSET_FILES:
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('synthetic fixture', encoding='utf-8')
    (tree / 'shared_platform/entry_catalog.json').write_text('{"supply_images":[]}', encoding='utf-8')
    monkeypatch.setattr(identity, '_commit', lambda *_: 'a' * 40)
    startup = identity.capture_runtime_identity('orbit-hive-local-console', root=tree)
    assert identity.health_payload('orbit-hive-local-console', root=tree, startup=startup)['state'] == 'READY'
    (tree / filename).write_text('changed fixture', encoding='utf-8')
    assert identity.health_payload('orbit-hive-local-console', root=tree, startup=startup)['state'] == state
