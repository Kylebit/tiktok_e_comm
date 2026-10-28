"""Durable cancellation must be consumed across graph and dispatch, not trusted from payload."""
from copy import deepcopy
import hashlib
import json

import pytest

from shared_platform.prepared_target_consumer import (
    PreparedTargetConsumerPins, PreparedTargetReductionConsumer,
    install_prepared_target_consumer,
)
from shared_platform.prepared_target_reduction import canonical_digest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform.r3_frozen_review_producer import rebuild_domain_review_graph, read_stored_domain_graph
from shared_platform.release_store import preview_release_plan
from test_prepared_target_reduction import prepared, record, encoded, TARGETS
from test_prepared_target_reduction_compiler import reduction_for, completed_run
from owned_common_review_fixtures import historical_completed_context
from test_b4b_release_compiler import policy, INCIDENTS


def consumer_for(engine, reduction, root):
    return PreparedTargetReductionConsumer(engine, PreparedTargetConsumerPins(
        reduction['task_id'], reduction['offer_id'], reduction['reduction_digest'], reduction['request_digest'],
        json.dumps(engine.release), reduction['prepared_reference'],
        json.dumps(reduction['predecessor_lineage']['predecessor_R2_identity']), 'original-product-executor'), service_root=root)


@pytest.fixture
def installed(prepared, tmp_path, monkeypatch):
    from modules.products import server
    from shared_platform import prepared_target_consumer as module
    engine = prepared[0]
    reduction = record(prepared)
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    monkeypatch.setattr(module, '_INSTALLED', None)
    consumer = consumer_for(engine, reduction, tmp_path)
    install_prepared_target_consumer(consumer)
    return engine, reduction, consumer


def common_payload(reduction):
    identity = reduction['predecessor_lineage']['predecessor_R2_identity']
    return {'product_id': reduction['offer_id'], 'targets': ['miaoshou:COMMON'],
            'r3_stage_binding': {'marketplace_targets': TARGETS, 'r2_identity': identity,
                                 'round1_snapshot_digest': identity['round1_snapshot_digest']}}


def test_true_fourteen_scope_is_adopted_but_original_fifteen_and_authority_are_preserved(installed):
    engine, reduction, consumer = installed
    task = engine.get(reduction['task_id'])
    assert task['scope']['shops'] == TARGETS
    assert task['effective_scope']['shops'] == [x for x in TARGETS if x != 'ozon:RU']
    assert task['scope_reduction_consumer_required'] is False
    assert reduction['consumer_adopted'] is False
    assert reduction['execution_authority'] is False
    assert consumer.read_verified(reduction['offer_id']) == reduction
    payload = common_payload(reduction)
    engine.assert_publication_scope_consumable(reduction['offer_id'], payload=payload, target_labels=['miaoshou:COMMON'])


def test_only_original_executor_can_claim(installed):
    engine, reduction, consumer = installed
    engine.register_executor('unrelated-executor', ['publication'], engine.release)
    assert engine.claim(reduction['task_id'], 'unrelated-executor') is None
    engine.register_executor(consumer.pins.original_worker, ['publication'], engine.release)
    assert engine.claim(reduction['task_id'], consumer.pins.original_worker)


def test_original_sku_binding_locks_only_fourteen_retained_targets(installed):
    engine, reduction, consumer = installed
    engine.register_executor(consumer.pins.original_worker, ['publication'], engine.release)
    token=engine.claim(reduction['task_id'],consumer.pins.original_worker)['lease_token']
    engine.bind_scope(reduction['task_id'],token,{**reduction['original_scope'],'skus':['0989']})
    with engine.transaction() as db:
        resources=[row[0] for row in db.execute('SELECT resource FROM workbench_resource_locks WHERE task_id=?',(reduction['task_id'],))]
    assert len([value for value in resources if value.startswith('product:')])==14
    assert not any('ozon:RU' in value for value in resources)
    assert engine.get(reduction['task_id'])['scope']['shops']==TARGETS


@pytest.mark.parametrize('drift', ['version', 'scope', 'reference', 'checkpoint', 'record_event'])
def test_current_durable_task_drift_is_terminal(installed, drift):
    engine, reduction, consumer = installed
    with engine.transaction() as db:
        if drift == 'version':
            db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?', ('{}', reduction['task_id']))
        elif drift == 'scope':
            scope = deepcopy(reduction['original_scope']); scope['shops'].remove('tiktok:GB')
            db.execute('UPDATE workbench_execution SET scope_json=? WHERE task_id=?', (json.dumps(scope), reduction['task_id']))
        elif drift in ('reference', 'checkpoint'):
            checkpoint = {'native_preparation': {'prepared_reference': reduction['prepared_reference']}}
            if drift == 'reference': checkpoint['native_preparation']['prepared_reference'] = 'r1-prepared:' + '0' * 64
            else: checkpoint['unexpected'] = 'changed'
            db.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?', (json.dumps(checkpoint), reduction['task_id']))
        else:
            db.execute('DELETE FROM workbench_events WHERE task_id=? AND event_type=?', (reduction['task_id'], 'publication_target_cancelled'))
    with pytest.raises(ValueError): consumer.read_verified(reduction['offer_id'])


@pytest.mark.parametrize('drift', ['r2', 'original_targets', 'dispatch_ozon', 'other_offer'])
def test_common_dispatch_cannot_broaden_or_substitute_predecessor(installed, drift):
    engine, reduction, consumer = installed
    payload = common_payload(reduction); targets = ['miaoshou:COMMON']
    if drift == 'r2': payload['r3_stage_binding']['r2_identity'] = {}
    if drift == 'original_targets': payload['r3_stage_binding']['marketplace_targets'] = reduction['effective_scope']['shops']
    if drift == 'dispatch_ozon': targets = ['ozon:RU']
    if drift == 'other_offer': payload['product_id'] = '111111'
    with pytest.raises(ValueError): consumer.validate_payload(payload, target_labels=targets)


def graph_context(tmp_path, monkeypatch):
    documents, _, store, request, io = historical_completed_context(tmp_path, monkeypatch)
    plan = store.get_plan(request['plan_id']); run = completed_run(store, plan)
    reduction = reduction_for(documents)
    engine = WorkbenchEngine(tmp_path / 'operations.db', reduction['original_version'])
    task = engine.create({'template': 'publication', 'source_key': 'original-preparation', 'scope': reduction['original_scope']})
    reduction['task_id'] = task['task_id']
    checkpoint = {'native_preparation': {'prepared_reference': reduction['prepared_reference']}}
    reduction['checkpoint_sha256'] = hashlib.sha256(encoded(checkpoint)).hexdigest()
    reduction['reduction_digest'] = canonical_digest({k: v for k, v in reduction.items() if k != 'reduction_digest'})
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?', (encoded(checkpoint).decode(), task['task_id']))
        db.execute('INSERT INTO workbench_publication_scope_reductions VALUES(?,?,?)',
                   (task['task_id'], reduction['request_digest'], json.dumps(reduction)))
        engine._event(db, task['task_id'], 'publication_target_cancelled', {'reduction_digest': reduction['reduction_digest']})
    consumer = consumer_for(engine, reduction, tmp_path)
    payload = bridge.build_marketplace_review_material(documents, plan, run, policy=policy(40), incidents=INCIDENTS, target_reduction=reduction)['payload']
    return store, plan, run, payload, consumer, io


def test_full_frozen_graph_is_rebuilt_from_exact_durable_record(tmp_path, monkeypatch):
    store, plan, run, payload, consumer, io = graph_context(tmp_path, monkeypatch)
    before = deepcopy((plan, run))
    graph = rebuild_domain_review_graph(plan, run, preview_release_plan(payload), target_consumer=consumer)
    assert graph.targets == ('tiktok:LH_PH',)
    assert graph.round2_digest == consumer.read_verified(plan['product_id'])['predecessor_lineage']['predecessor_R2_identity']['identity_digest']
    assert (plan, run) == before and io.mutations == 0
    market = store.create_plan(payload)
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        assert read_stored_domain_graph(db, market['plan_id'], target_consumer=consumer) == graph


@pytest.mark.parametrize('drift', ['forged_reader', 'embedded_record', 'reintroduce_target'])
def test_graph_never_accepts_payload_self_certification(tmp_path, monkeypatch, drift):
    store, plan, run, payload, consumer, io = graph_context(tmp_path, monkeypatch)
    if drift == 'forged_reader': consumer = {'consumer_adopted': True}
    if drift == 'embedded_record': payload['r3_marketplace_binding']['target_reduction']['request_digest'] = '0' * 64
    if drift == 'reintroduce_target': payload['targets'].append('ozon:RU')
    with pytest.raises(ValueError):
        rebuild_domain_review_graph(plan, run, preview_release_plan(payload), target_consumer=consumer)
    assert io.mutations == 0


def test_current_service_root_change_blocks_installed_consumer(installed, monkeypatch, tmp_path):
    from modules.products import server
    from shared_platform.prepared_target_consumer import configured_target_consumer
    other = tmp_path / 'other'; other.mkdir()
    monkeypatch.setattr(server, 'ROOT', other)
    with pytest.raises(ValueError, match='SERVICE_ROOT_CHANGED'):
        configured_target_consumer(installed[1]['offer_id'])
