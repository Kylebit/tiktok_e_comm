from types import SimpleNamespace
from pathlib import Path
import importlib.util
import json
import sys

import pytest

from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform import workbench_publication_native as native


@pytest.fixture
def setup(tmp_path):
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'test'})
    engine.register_executor('worker', ['publication', 'delisting'], engine.release)
    task = engine.create({'template': 'publication', 'source_key': 'native-test', 'scope': {'offer_id': '123', 'shops': ['tiktok:LH_MY']}})
    token = engine.claim(task['task_id'], 'worker')['lease_token']
    return engine, task, token, SimpleNamespace(root=tmp_path)


def identity():
    return {'offer_id': '123', 'targets': ['tiktok:LH_MY'], 'snapshot_digest': 'sha256:frozen',
            'prepared_reference': 'r1-prepared:fixture', 'internal_sku': '0988', 'seller_sku': '660988'}


def test_preparation_waits_actual_domain_then_resumes(setup, monkeypatch):
    engine, task, token, profile = setup
    from modules.sourcing import new_product_workbench as workbench
    state = [None]
    calls = []
    monkeypatch.setattr(native, 'read_frozen', lambda *_: state[0])
    monkeypatch.setattr(native, '_report_dir', lambda *_: profile.root / 'reports/product-preparation/123')
    monkeypatch.setattr(workbench, 'load_state', lambda *_: {'product_approval': {}})
    monkeypatch.setattr(native, 'prepare_facts', lambda *_: calls.append('prepare') or {'status': 'DECISION_REQUIRED'})
    native.run(engine, task, token, profile)
    waiting = engine.get(task['task_id'])
    assert waiting['execution_state'] == 'waiting_user'
    assert waiting['required_action']['url'].startswith('/product-workspace?offer_id=123')
    assert not native.observe(engine, waiting, profile)
    with pytest.raises(ValueError):
        engine.user_action(task['task_id'], 'provide-input', {'action_id': waiting['required_action']['action_id'], 'note': 'approved'})
    state[0] = identity()
    assert native.observe(engine, waiting, profile)
    token = engine.claim(task['task_id'], 'worker')['lease_token']
    native.run(engine, engine.get(task['task_id']), token, profile)
    assert engine.get(task['task_id'])['current_step'] == 'images'
    assert calls == ['prepare']
    other = engine.create({'template': 'delisting', 'source_key': 'delist', 'scope': {'skus': ['0988'], 'shops': ['tiktok:LH_MY']}})
    assert engine.claim(other['task_id'], 'worker') is None


def test_no_missing_later_adapter_false_completion(setup):
    engine, task, token, profile = setup
    engine.complete_step(task['task_id'], token, expected_step='facts', checkpoint={})
    with pytest.raises(ValueError, match='NATIVE_DOMAIN_STAGE_ADAPTER_REQUIRED'):
        native.run(engine, engine.get(task['task_id']), token, profile)
    assert engine.get(task['task_id'])['current_step'] == 'images'


def test_exact_targets_required_before_prepare(setup, monkeypatch):
    engine, task, token, profile = setup
    task['scope']['shops'] = []
    with pytest.raises(ValueError, match='NATIVE_EXACT_TARGETS_REQUIRED'):
        native.run(engine, task, token, profile)


def test_observer_receipt_rechecks_domain_drift(setup, monkeypatch):
    engine, task, token, profile = setup
    monkeypatch.setattr(native, 'read_frozen', lambda *_: None)
    monkeypatch.setattr(native, 'prepare_facts', lambda *_: {})
    native.run(engine, task, token, profile)
    seq = iter([identity(), {**identity(), 'snapshot_digest': 'sha256:changed'}])
    monkeypatch.setattr(native, 'read_frozen', lambda *_: next(seq))
    with pytest.raises(ValueError):
        native.observe(engine, engine.get(task['task_id']), profile)
    assert engine.get(task['task_id'])['execution_state'] == 'waiting_user'


def test_actual_prepare_skill_receives_source_region_for_new_offer(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('native_actual_prepare', root / 'skills/prepare-product-publication/scripts/prepare_product_publication.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, 'shared_platform.round1_workspace', SimpleNamespace(_module=lambda _: module))
    monkeypatch.setattr(native, '_server', lambda _: object())
    monkeypatch.setattr(native, '_report_dir', lambda *_: tmp_path)
    task = {'scope': {'offer_id': '12345', 'shops': ['shopee:MY', 'shopee:PH']}}
    preview = {'ok': True, 'revision': 7, 'review': {'title': 'Fixture product', 'category': 'Fixture category',
               'selected_sites': ['shopee:MY', 'shopee:PH'], 'cost_cny': 8, 'weight_kg': 0.2, 'package_cm': [20,20,3]}}
    result = native._prepare_facts_locked(task, SimpleNamespace(root=tmp_path), preview_builder=lambda _: preview)
    packet = json.loads(Path(result['review_path']).read_text(encoding='utf-8'))
    assert packet['category_review_context']['source_region'] == 'MY'
    assert packet['target_selection']['requested'] == task['scope']['shops']
    assert packet['external_write_count'] == 0
    packet['category_review_context']['source_region'] = 'PH'
    Path(result['review_path']).write_text(json.dumps(packet), encoding='utf-8')
    native._prepare_facts_locked(task, SimpleNamespace(root=tmp_path), preview_builder=lambda _: preview)
    assert json.loads(Path(result['review_path']).read_text(encoding='utf-8'))['category_review_context']['source_region'] == 'PH'


def test_missing_r2_is_distinct_from_existing_corrupt_r2(tmp_path, monkeypatch):
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS
    directory = tmp_path/'reports/product-preparation/123'
    directory.mkdir(parents=True)
    monkeypatch.setattr(native, 'read_frozen', lambda *_: identity())
    monkeypatch.setattr(native, '_report_dir', lambda *_: directory)
    with pytest.raises(FileNotFoundError, match='NOT_YET_PRODUCED'):
        native.read_images({}, SimpleNamespace(root=tmp_path))
    for name in R2_DOCUMENTS.values():
        (directory/name).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        native.read_images({}, SimpleNamespace(root=tmp_path))


@pytest.mark.parametrize('target',['tiktok:UNKNOWN','shopee:US','ozon:MY','miaoshou:OTHER'])
def test_unknown_native_target_rejected(target):
    with pytest.raises(ValueError):
        native._scope({'scope':{'offer_id':'123','shops':[target]}})


def test_common_retained_without_adding_or_replacing_stores():
    requested=['tiktok:LH_MY','miaoshou:COMMON','shopee:PH']
    assert native._scope({'scope':{'offer_id':'123','shops':requested}})[1]==sorted(requested)
    with pytest.raises(ValueError):
        native._scope({'scope':{'offer_id':'123','shops':['miaoshou:COMMON']}})
