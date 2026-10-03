from copy import deepcopy
from types import SimpleNamespace

import pytest

from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform import workbench_publication_adapter as adapter


@pytest.fixture
def case(tmp_path, monkeypatch):
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'synthetic'})
    engine.register_executor('test', ['publication'], engine.release, ttl=300)
    product = {'frozen_review_projection': True, 'frozen_first_review': {'offer_id': '123',
        'approved_revision': 5, 'snapshot_digest': 'frozen-r1', 'targets': [{'target': 'tiktok:LH_MY'}]}}
    images = {'offer_id': '123', 'seller_sku': '0988', 'approved_revision': 5,
        'binding_sha256': 'frozen-r2', 'revision': 0, 'targets': ['tiktok:LH_MY'],
        'images': [{'image_id': 'i1', 'action': 'review'}], 'keep_count': 0,
        'r2_consumer': {'status': 'BLOCKED'}}
    release = {'ok': True, 'offer_id': '123', 'current_r2_matches_approved': True,
        'common': {'status': 'VERIFIED'}, 'marketplace': {'plan': {'plan_id': 'plan1', 'status': 'APPROVED',
        'product_id': '123', 'targets': ['tiktok:LH_MY']}, 'final_review': {'approval_recorded': True,
        'approval_id': 'approval1', 'candidate_digest': 'candidate1', 'execution_recorded': False},
        'execution_summary': {'index_valid': True, 'blockers': []}, 'target_results': []}}
    monkeypatch.setattr(adapter, '_read_product', lambda *_: (deepcopy(product), deepcopy(images)))
    monkeypatch.setattr(adapter, '_read_release', lambda *_: deepcopy(release))
    task = engine.create({'template': 'publication', 'source_key': 'synthetic', 'scope': {'offer_id': '123'}})
    profile = SimpleNamespace(root=tmp_path, environment='preview', data_root=tmp_path, version='synthetic')
    return engine, task['task_id'], profile, product, images, release


def run_one(case):
    engine, task_id, profile, *_ = case
    claim = engine.claim(task_id, 'test')
    assert claim
    adapter.run(engine, engine.get(task_id), claim['lease_token'], profile)
    return engine.get(task_id)


def keep_images(images):
    images['images'][0]['action'] = 'keep'
    images['keep_count'] = 1
    images['revision'] += 1
    images['r2_consumer'] = {'status': 'PASSED', 'identity': {'r2': 'real-contract-fixture'}}


def executing(release, *, official=False, unknown=False):
    market = release['marketplace']
    market['final_review']['execution_recorded'] = True
    market['target_results'] = [{'target_label': 'tiktok:LH_MY', 'source': {'run_id': 'run1', 'report_digest': 'report1'},
        'request_attempted': True, 'official_success': official, 'readback_completed': official,
        'outcome_unknown': unknown, 'status': 'PUBLISHED' if official else 'PROCESSING'}]


def test_full_original_review_resume_to_readback(case):
    engine, task_id, profile, _, images, release = case
    assert run_one(case)['current_step'] == 'images'
    waiting = run_one(case)
    assert waiting['execution_state'] == 'waiting_user'
    assert waiting['required_action']['url'] == '/product-workspace?offer_id=123&round=images#originalPublicationReview'
    assert not adapter.observe(engine, waiting, profile)
    keep_images(images)
    assert adapter.observe(engine, waiting, profile)
    assert run_one(case)['current_step'] == 'release'
    waiting = run_one(case)
    assert waiting['execution_state'] == 'waiting_user'
    assert not adapter.observe(engine, waiting, profile)  # final approved != executed
    executing(release)
    assert adapter.observe(engine, waiting, profile)
    assert run_one(case)['current_step'] == 'readback'
    waiting = run_one(case)
    assert waiting['execution_state'] == 'waiting_domain'
    assert waiting['required_action'] is None
    executing(release, official=True)
    assert adapter.observe(engine, waiting, profile)
    done = run_one(case)
    assert done['execution_state'] == 'completed'
    assert done['result_url'] == '/product-workspace?offer_id=123&round=final#originalPublicationReview'
    assert done['checkpoint']['provider_readback_ref']


def test_passed_without_keep_cannot_resume(case):
    engine, task_id, profile, _, images, _ = case
    run_one(case); waiting = run_one(case)
    images['r2_consumer'] = {'status': 'PASSED', 'identity': {'r2': 'fixture'}}
    assert not adapter.observe(engine, waiting, profile)


def test_facts_bind_scope_and_exclude_concurrent_delisting(case):
    engine, task_id, profile, *_ = case
    result = run_one(case)
    assert result['scope']['skus'] == ['0988']
    assert result['scope']['shops'] == ['tiktok:LH_MY']
    engine.register_executor('delist', ['delisting'], engine.release)
    conflict = engine.create({'template': 'delisting', 'source_key': 'conflict',
        'scope': {'skus': ['0988'], 'shops': ['tiktok:LH_MY']}})
    assert engine.claim(conflict['task_id'], 'delist') is None


def test_prefixed_seller_sku_uses_same_internal_lock(case):
    engine, task_id, profile, _, images, _ = case
    images['seller_sku'] = '660988'
    result = run_one(case)
    assert result['scope']['skus'] == ['0988']
    assert result['steps'][0]['checkpoint']['publication_identity']['seller_sku'] == '660988'
    engine.register_executor('delist', ['delisting'], engine.release)
    conflict = engine.create({'template': 'delisting', 'source_key': 'prefix-conflict',
        'scope': {'skus': ['0988'], 'shops': ['tiktok:LH_MY']}})
    assert engine.claim(conflict['task_id'], 'delist') is None


def test_arbitrary_platform_id_not_truncated_into_internal_sku(case):
    images = case[4]
    images['seller_sku'] = '7484848400000988'
    assert run_one(case)['execution_state'] == 'failed'


def test_changed_r1_does_not_resume_old_wait(case):
    engine, task_id, profile, product, images, _ = case
    run_one(case); waiting = run_one(case); keep_images(images)
    product['frozen_first_review']['snapshot_digest'] = 'other'
    with pytest.raises(ValueError, match='冻结身份已变化'):
        adapter.observe(engine, waiting, profile)
    assert engine.get(task_id)['execution_state'] == 'waiting_user'


def test_kept_images_withdrawn_after_resume_blocks(case):
    engine, task_id, profile, _, images, _ = case
    keep_images(images); run_one(case); run_one(case)
    images['images'][0]['action'] = 'remove'; images['revision'] += 1
    assert run_one(case)['execution_state'] == 'failed'


@pytest.mark.parametrize('mutation', ['wrong_offer', 'wrong_targets', 'unknown', 'no_approval', 'queued', 'superseded'])
def test_wrong_or_unverified_release_cannot_complete(case, mutation):
    engine, task_id, profile, _, images, release = case
    keep_images(images); run_one(case); run_one(case); executing(release, official=True)
    market = release['marketplace']
    if mutation == 'wrong_offer': market['plan']['product_id'] = '456'
    if mutation == 'wrong_targets': market['plan']['targets'] = ['shopee:MY']
    if mutation == 'unknown': market['target_results'][0]['outcome_unknown'] = True
    if mutation == 'no_approval': market['final_review']['approval_recorded'] = False
    if mutation == 'queued': market['target_results'][0].update(request_attempted=False, official_success=False)
    if mutation == 'superseded': market['plan']['status'] = 'SUPERSEDED'
    result = run_one(case)
    expected = 'reconciliation_required' if mutation == 'unknown' else 'failed' if mutation in {'wrong_offer','wrong_targets','superseded'} else 'waiting_user' if mutation == 'no_approval' else 'waiting_domain'
    assert result['execution_state'] == expected
    assert result['current_step'] == 'release'


def test_scope_mismatch_fails_without_adopting_registered_targets(case):
    engine, task_id, profile, *_ = case
    other = engine.create({'template': 'publication', 'source_key': 'other', 'scope': {'offer_id': '123', 'shops': ['another-shop']}})
    token = engine.claim(other['task_id'], 'test')['lease_token']
    adapter.run(engine, engine.get(other['task_id']), token, profile)
    assert engine.get(other['task_id'])['execution_state'] == 'failed'


def test_observed_unknown_keeps_lock_and_has_no_generic_retry(case):
    engine, task_id, profile, _, images, release = case
    keep_images(images); run_one(case); run_one(case); executing(release)
    run_one(case); waiting = run_one(case)
    assert waiting['execution_state'] == 'waiting_domain'
    assert waiting['required_action'] is None
    executing(release, unknown=True)
    assert adapter.observe(engine, waiting, profile) is False
    assert engine.get(task_id)['execution_state'] == 'reconciliation_required'
    for action in ['retry','cancel']:
        with pytest.raises(ValueError): engine.user_action(task_id, action)
    engine.register_executor('delist', ['delisting'], engine.release)
    conflict = engine.create({'template': 'delisting', 'source_key': 'unknown-conflict',
        'scope': {'skus': ['0988'], 'shops': ['tiktok:LH_MY']}})
    assert engine.claim(conflict['task_id'], 'delist') is None
