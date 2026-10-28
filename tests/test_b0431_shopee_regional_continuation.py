"""Actual retained SH11 and whole DB clone; no API or credential access."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform import native_shopee_regional_continuation as recovery
from test_b0431_tiktok_locale_draft_correction import captured, clone, _whole_original_database


@pytest.fixture
def regional(clone, tmp_path, monkeypatch):
    _whole_original_database(clone)
    path = Path(os.environ['ORBIT_B0431_SH_REGIONAL_TEMPLATE'])
    doc = json.loads(path.read_bytes());assert doc['status'] == 'NOT_AUTHORIZED'
    doc['status'] = 'ROOT_TECHNICAL_EXECUTION_APPROVED'  # Local fixture only; no source event/actual authority is created.
    authorized = tmp_path / 'fixture-technical-proof.json'
    authorized.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    ref = dict(path=str(authorized), sha256=hashlib.sha256(authorized.read_bytes()).hexdigest())
    assert recovery.read_regional_proof(ref) == doc
    ctx = recovery._material(ref, doc);ctx['active'] = clone['active']
    monkeypatch.setattr(recovery, '_context', lambda active: ctx if active is clone['active'] else pytest.fail('wrong active'))
    return dict(clone=clone, ctx=ctx, template=path)


def _base(regional, label):
    from shared_platform.native_final_mutation_budget import _DurableLedger
    ctx = regional['ctx'];active = ctx['active'];store = active.store
    with store._connect_readonly() as db:
        scope = db.execute("SELECT binding_json FROM native_final_budget_scopes WHERE platform='SHOPEE' AND plan_id=?", (recovery.PLAN,)).fetchone()
    def recheck(db=None):
        if db is not None:
            active.service._decision(db, recovery.DECISION, owner_sid=active.owner_sid)
            row = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (recovery.RUN, label)).fetchone()
            assert tuple(row) == ('RUNNING', recovery.BEFORE[label] + 1)
    return _DurableLedger(store=store, binding=json.loads(scope[0]), target_label=label, recheck=recheck)


def test_actual_shared10_is_not_regional_dispatch_and_default_authority_refused(regional):
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    ctx = regional['ctx'];store = ctx['active'].store;rows = recovery._joined(ctx['image'])
    assert len(rows) == 11 and len([r for r in rows if r['budget_scope'] == 'shared']) == 10
    assert [r['target_label'] for r in rows if r['budget_scope'] == 'target'] == ['shopee:PH']
    th = next(r for r in ctx['image']['release_target_failure_events'] if r['target_label'] == 'shopee:TH' and r['attempt'] == 3)
    evidence = json.loads(th['evidence_json'])
    assert evidence['canonical_platform_result']['targets'][0]['status'] == 'PROCESSING'
    assert not any(r['budget_scope'] == 'target' and r['target_label'] == 'shopee:TH' for r in rows)
    template = regional['template'];ref = dict(path=str(template), sha256=hashlib.sha256(template.read_bytes()).hexdigest())
    with pytest.raises(NativeMutationBudgetHold, match='EXACT_TECHNICAL_AUTHORITY_REQUIRED'):recovery.read_regional_proof(ref)
    before = deepcopy(store.get_run(recovery.RUN))
    assert recovery.activate_failed_targets(ctx['active']) == tuple(recovery.BEFORE)
    after = store.get_run(recovery.RUN)
    for original, current in zip(before['targets'], after['targets']):
        assert original['failure_events'] == current['failure_events']
        if current['target_label'] in recovery.BEFORE:
            assert current['status'] == 'PENDING' and current['attempts'] == original['attempts']
        else:assert current == original
    with store._connect_readonly() as db:assert recovery._facts(db, ctx) == rows


@pytest.mark.parametrize('label', recovery.NOT_SENT)
def test_regional_NOT_SENT_original_begin_and_budget_no_shared_recreate(regional, label):
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    ctx = regional['ctx'];active = ctx['active'];store = active.store
    recovery.activate_failed_targets(active)
    target = store.begin_target(recovery.RUN, label)
    assert target['status'] == 'RUNNING' and target['attempts'] == recovery.BEFORE[label] + 1
    ledger = recovery.for_active(active, _base(regional, label))
    assert ledger.shared_attempt_count == 10 and ledger.target_attempt_counts[label] == 0
    with pytest.raises(NativeMutationBudgetHold, match='SHARED_WRITE_FORBIDDEN'):ledger.reserve_shared('create_global_item')
    ledger.reserve_target(label, 'create_publish_task')
    with pytest.raises(NativeMutationBudgetHold):ledger.reserve_target(label, 'create_publish_task')
    assert ledger.target_attempt_counts[label] == 1 and ledger.shared_attempt_count == 10
    # Even an undispatched UNKNOWN is not an implicit regional retry.
    with pytest.raises(NativeMutationBudgetHold, match='ALREADY_ATTEMPTED_OR_UNKNOWN'):
        _failed_preimage(regional, label)


def _failed_preimage(regional, label):
    # A separate local clone scenario: zero-claim preimage with a new UNKNOWN
    # reservation may not be activated. No original row in evidence is changed.
    active = regional['ctx']['active']
    with active.store._transaction() as db:
        db.execute("UPDATE release_target_runs SET status='FAILED',attempts=? WHERE run_id=? AND target_label=?",
            (recovery.BEFORE[label], recovery.RUN, label))
    return recovery.activate_failed_targets(active)


def test_original_loop_PH_existing_item_list_outlet_no_new_task(regional, monkeypatch, tmp_path):
    from modules.products import server, release_adapters
    from domains.channel_operations.release_executor import AdapterRegistration
    from shared_platform import native_tiktok_locale_draft_correction as tiktok
    from shared_platform import native_ozon_three_card_media_recovery as media
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    from shared_platform.product_publication_executors import ShopeeRegionExecutorDependencies
    from modules.shopee.skill_regions import OfficialShopeeRegionRuntime
    from modules.shopee import client
    active = regional['ctx']['active'];store = active.store;plan = store.get_plan(recovery.PLAN);claims = [];calls = []
    image = regional['ctx']['image'];refs = image['official_SH_global_and_PH_observation_refs']
    def load(name):return json.loads(recovery._ref(refs[name]))
    item_reply = load('SHOPEE_PH_47970054181_PH_ITEM.response.private.json')
    model_reply = load('SHOPEE_PH_47970054181_PH_MODELS.response.private.json')
    context = SimpleNamespace(region='PH', shop_id=1527371343, merchant_id=4970102, shop_token='fixture', merchant_token='fixture')
    class WireOutlet(BaseException):pass
    def adapter(request):
        if request.target_label == 'shopee:MY':
            # The original frozen order begins MY. Its resolver is a named
            # local boundary in this PH outlet case, with no provider request.
            raise ValueError('Fixture MY resolver boundary, no provider request')
        assert request.target_label == 'shopee:PH'
        ledger = recovery.for_active(active, _base(regional, request.target_label))
        class Runtime(OfficialShopeeRegionRuntime):
            def existing_regional_item(self, ctx, gid):return None
            def resolved_global_item_id(self, ctx, item):return recovery.GLOBAL
            def list_item(self, ctx, item):
                return ledger.dispatch_reserved('list_regional_item',
                    lambda wire: super(Runtime, self).list_item(ctx, item), {'item_id': item})
        def get(path, shop, token, body):
            assert shop == context.shop_id
            if path.endswith('/get_item_base_info'):return deepcopy(item_reply)
            if path.endswith('/get_model_list'):return deepcopy(model_reply)
            raise AssertionError('Uncaptured read is not a passed provider fact')
        def post(path, shop, token, body):
            calls.append((path, shop, deepcopy(body)))
            assert path == '/api/v2/product/unlist_item' and shop == 1527371343
            assert body == {'item_list': [{'item_id': 47970054181, 'unlist': False}]}
            with store._connect_readonly() as db:
                rows = recovery._facts(db, regional['ctx'])
                assert len(rows) == 12 and rows[-1]['operation'] == 'list_regional_item' and rows[-1]['request_digest']
            raise WireOutlet('Original unlist=False SDK outlet, no provider request')
        monkeypatch.setattr(client, 'shop_get', get);monkeypatch.setattr(client, 'shop_post', post)
        class Resolver:
            def __call__(self, request):return recovery.GLOBAL
            def write_count(self, request):return 0
        deps = recovery.bind_dependencies(ShopeeRegionExecutorDependencies(Resolver(), Runtime()), ledger)
        assert deps.global_item_id_resolver(request) == recovery.GLOBAL
        assert deps.runtime.existing_regional_item(context, recovery.GLOBAL) == recovery.PH_ITEM
        class WrongModels(Runtime):
            def regional_models(self, ctx, item):return [{'model_sku': '2000431', 'model_id': 'wrong'}]
        wrong = recovery.bind_dependencies(ShopeeRegionExecutorDependencies(Resolver(), WrongModels()), ledger)
        with pytest.raises(NativeMutationBudgetHold, match='PH_OFFICIAL_MODELS_REQUIRED'):
            wrong.runtime.existing_regional_item(context, recovery.GLOBAL)
        with pytest.raises(NativeMutationBudgetHold, match='PH_RECREATE'):
            deps.runtime.create_publish_task(context, {'global_item_id': int(recovery.GLOBAL)})
        with pytest.raises(NativeMutationBudgetHold, match='NO_RECREATE'):
            ledger.reserve_target('shopee:PH', 'create_publish_task')
        ledger.reserve_target('shopee:PH', 'list_regional_item')
        deps.runtime.list_item(context, recovery.PH_ITEM)
    registry = {name: AdapterRegistration(name, adapter if channel == 'shopee' else None, True, True, True, True)
        for channel, name in release_adapters.ADAPTER_NAMES.items()}
    def gate(data, *, store):
        with store._connect_readonly() as db:active.service._decision(db, recovery.DECISION, owner_sid=active.owner_sid)
        return dict(dashboard={'frozen_review_projection': True}, payload=plan['payload'], plan=plan, run=store.get_run(recovery.RUN),
            predecessor_run=None, registry=registry, target_rows=[dict(channel=label.split(':')[0], site=label.split(':')[1],
                adapter=release_adapters.ADAPTER_NAMES[label.split(':')[0]]) for label in plan['targets']]), None
    active.gate = gate;active.common_dependency_verified = lambda gate: True
    monkeypatch.setattr(server, '_activate_native_shopee_global_zero', lambda active: ())
    monkeypatch.setattr(server, '_activate_native_tiktok_claimed_save', lambda active: ())
    monkeypatch.setattr(tiktok, 'activate_failed_targets', lambda active: ())
    monkeypatch.setattr(media, 'activate_failed_targets', lambda active: (_ for _ in ()).throw(NativeMutationBudgetHold('OZON_THREE_MEDIA_TEST_BOUNDARY')))
    monkeypatch.setattr(media, 'recovery_actions', lambda active, run, actions: actions)
    original = store.begin_target
    def begin(run, label):
        row = original(run, label);claims.append((label, row['status'], row['attempts']));return row
    monkeypatch.setattr(store, 'begin_target', begin)
    try:server._publish_selected_release(dict(offer_id=plan['product_id'], plan_id=recovery.PLAN,
        confirmation_token=plan['confirmation_token'], confirm_publish=True), native_gate=active)
    except WireOutlet:pass
    else:pytest.fail(str(dict(claims=claims, calls=calls)))
    assert claims == [('shopee:MY', 'RUNNING', 4), ('shopee:PH', 'RUNNING', 4)] and len(calls) == 1
