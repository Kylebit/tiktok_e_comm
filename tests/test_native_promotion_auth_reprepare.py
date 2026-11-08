"""One offline real-consumer contract for an auth failure before any SDK GET.

Actor/R1/R2 scope is an explicit synthetic boundary. The native companion
consumer, OneClick ledger, production SH adapter, SDK signing, pagination,
intent recorder and readback are real; only urlopen is an in-memory provider.
"""
from io import BytesIO
import hashlib
import json
import socket
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from shared_platform import native_companion_promotions as native
from shared_platform.native_sole_final_execution import _ACTIVE, _NativeExecution
from shared_platform.oneclick_release_controlplane import _SCHEMA
from shared_platform.postpublish_promotions import build_approved_postpublish_promotion_policy
from shared_platform.release_store import ReleaseStore
from tests.test_oneclick_release_controlplane import _plan_payload


def test_original_auth_prepare_failure_reenters_same_consumer_without_replay(tmp_path, monkeypatch):
    network_attempts = []
    def no_network(*args, **kwargs):
        network_attempts.append(True); pytest.fail('real network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'shopee': {'enabled': True, 'environment': 'live',
        'partner_id': 123, 'partner_key': 'SYNTHETIC_ONLY'}}), encoding='utf-8')
    token_path = tmp_path / 'tokens.json'
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS', str(settings))
    monkeypatch.setenv('ORBIT_SHOPEE_TOKEN_PATH', str(token_path))
    identities = {'MY': (101, 1001, 2001, 'MYR'), 'PH': (102, 1002, 2002, 'PHP')}
    tokens = {'sync_shop_ids': {r: v[0] for r, v in identities.items()},
        'shops': {str(v[0]): {'region': r, 'merchant_id': 501, 'access_token': 'SYNTHETIC_ONLY', 'expire_at': 1}
            for r, v in identities.items()},
        'merchants': {'501': {'access_token': 'SYNTHETIC_ONLY', 'expire_at': 1}}}
    token_path.write_text(json.dumps(tokens), encoding='utf-8')
    payload = _plan_payload(targets=['miaoshou:COMMON', 'shopee:MY', 'shopee:PH'])
    payload['approved_postpublish_promotion_policy'] = build_approved_postpublish_promotion_policy(
        approval_reference='SYNTHETIC_original_frozen_approval')
    payload['pricing'] = {'selected_targets': {f'shopee:{r}': {'store_prices': [{
        'target_key': r.lower(), 'list_price': 100, 'discount_reserve_pct': 30,
        'sale_after_discount': 70, 'currency': v[3]}]} for r, v in identities.items()}}
    release = ReleaseStore(tmp_path / 'release.db')
    created = release.create_plan(payload)
    release.approve_plan(created['plan_id'], approved_by='Kyle', user_approved=True,
        confirmation_token=created['confirmation_token'])
    plan = release.get_plan(created['plan_id']); run = release.start_run(plan['plan_id'])
    with sqlite3.connect(release.path) as db:
        db.executescript(_SCHEMA)
        for region, (shop, item, model, currency) in identities.items():
            label = 'shopee:' + region
            evidence = {'schema_version': 'native-scoped-v4-target-result/v1', 'target_label': label,
                'verified': True, 'status': 'PUBLISHED', 'outcome_unknown': False, 'reconciliation_required': False,
                'canonical_platform_result': {'readback_completed': True, 'platform': 'SHOPEE', 'targets': [{
                    'target_label': label, 'status': 'PUBLISHED', 'evidence': {'outcome_unknown': False,
                    'target_label': label, 'stage': 'READBACK', 'provider_identity_bound': True,
                    'provider_code': 'shopee_official_readback_verified'}}]}}
            db.execute("UPDATE release_target_runs SET status='SUCCEEDED',external_id=? WHERE run_id=? AND target_label=?",
                (f'shopee:{shop}:item:{item}', run['run_id'], label))
            db.execute('INSERT INTO release_target_readbacks VALUES(?,?,?,?,?)',
                (run['run_id'], label, json.dumps(evidence), native._hash(evidence), 'synthetic-before'))
    original_run = release.get_run(run['run_id'])
    active = object.__new__(_NativeExecution)
    active.store, active.plan_id, active.decision_id, active.owner_sid = release, plan['plan_id'], 'SYNTHETIC_original_decision', 'SYNTHETIC_owner'
    active.runtime = SimpleNamespace(_operations=SimpleNamespace(new_task_worker=object()))
    def gate(data, *, store):
        assert store.path == active.store.path and data == {'synthetic': 'original'}
        return {'plan': plan, 'run': active.store.get_run(run['run_id'])}, None
    active.gate = gate
    from shared_platform import native_new_task_v4_targets
    def source_boundary(a, p):
        assert a is active and p == plan  # Only source/actor boundary is synthetic.
    monkeypatch.setattr(native_new_task_v4_targets, 'scope', source_boundary)
    from modules.shopee import client
    calls = []; members = set()
    def provider(request, **kwargs):
        assert kwargs['attempts'] == 1 and kwargs['allow_curl_fallback'] is False
        parsed = urlparse(request.full_url); query = parse_qs(parsed.query)
        shop = int(query['shop_id'][0]); region, identity = next((r, v) for r, v in identities.items() if v[0] == shop)
        _, item, model, currency = identity
        calls.append((request.get_method(), region, parsed.path))
        if parsed.path == '/api/v2/product/get_model_list':
            response = {'model': [{'model_id': model, 'model_sku': '0954',
                'price_info': [{'currency': currency, 'original_price': 100}]}]}
        elif parsed.path == '/api/v2/discount/get_discount_list':
            assert query['discount_status'] == ['ongoing']
            response = {'more': False, 'discount_list': [{'discount_id': 77, 'start_time': 1,
                'end_time': 4102444800, 'discount_status': 'ongoing'}]}
        elif parsed.path == '/api/v2/discount/get_discount':
            response = {'more': False, 'item_list': ([{'item_id': item, 'model_list': [{
                'model_id': model, 'model_promotion_price': 70}]}] if region in members else [])}
        elif parsed.path == '/api/v2/discount/add_discount_item':
            assert request.get_method() == 'POST' and region not in members
            body = json.loads(request.data)
            assert body['discount_id'] == 77 and body['item_list'][0]['item_id'] == item
            assert body['item_list'][0]['model_list'] == [{'model_id': model, 'model_promotion_price': 70}]
            members.add(region); response = {}
        else: pytest.fail('endpoint outside promotion contract')
        return BytesIO(json.dumps({'error': '', 'response': response}).encode())
    monkeypatch.setattr(client, 'urlopen_retry', provider)
    actions = ('promotion:shopee:MY', 'promotion:shopee:PH')
    def consume():
        token = _ACTIVE.set(active)
        try: return native.execute_active(active, {'synthetic': 'original'}, actions)
        finally: _ACTIVE.reset(token)
    first = consume(); job = first[1]['promotion_job']['job_id']
    def rows(path=release.path):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM oneclick_release_targets WHERE job_id=? AND target_label IN (?,?) ORDER BY target_label', (job, *actions))]
    blocked = rows()
    assert all(r['status'] == 'BLOCKED_CAPABILITY' and r['dispatch_count'] == 0
        and r['reason_code'] == 'adapter_prepare_contract_error' and r['result_json'] is None
        and json.loads(r['reason_detail'])['detail_digest'] == hashlib.sha256(b'SHOPEE_EXISTING_EXACT_AUTH_UNAVAILABLE').hexdigest()
        for r in blocked)
    assert calls == []  # Original auth guard fails before any provider GET.
    with sqlite3.connect(release.path) as db:
        history = db.execute('SELECT * FROM oneclick_release_events ORDER BY event_id').fetchall()
        sequence = db.execute('SELECT batch_sequence FROM oneclick_release_jobs WHERE job_id=?', (job,)).fetchone()[0]
    from shared_platform.final_review_server_admission import ApprovalBlocked
    with pytest.raises(ApprovalBlocked):
        native.execute_active(active, {'synthetic': 'original'}, actions)  # No protected ACTIVE.
    try: consume()  # Still-expired credentials cannot reopen preparation.
    except ValueError as error: assert str(error) == 'SHOPEE_EXISTING_EXACT_AUTH_UNAVAILABLE'
    assert calls == [] and all(r['status'] == 'BLOCKED_CAPABILITY' for r in rows())
    for entry in [*tokens['shops'].values(), *tokens['merchants'].values()]: entry['expire_at'] = int(time.time()) + 3600
    token_path.write_text(json.dumps(tokens), encoding='utf-8')  # Synthetic renewal, no auth API.
    # The same real consumer must refuse every dispatched/uncertain/other-error
    # history. Each variant is an independent clone of this synthetic blocker.
    variants = [
        ("UPDATE oneclick_release_targets SET dispatch_count=1 WHERE target_label=?", (actions[0],)),
        ("UPDATE oneclick_release_targets SET status='RECONCILIATION_REQUIRED' WHERE target_label=?", (actions[0],)),
        ("UPDATE oneclick_release_targets SET pending_write_intent_json='{}',pending_write_intent_digest=? WHERE target_label=?", ('a'*64, actions[0])),
        ("UPDATE oneclick_release_targets SET cumulative_external_write_upper_bound=NULL WHERE target_label=?", (actions[0],)),
        ("UPDATE oneclick_release_targets SET result_json='{}' WHERE target_label=?", (actions[0],)),
        ("UPDATE oneclick_release_targets SET reason_code='other_error' WHERE target_label=?", (actions[0],)),
        ("UPDATE oneclick_release_targets SET reason_detail=? WHERE target_label=?", (json.dumps({'detail_digest': 'f'*64}), actions[0])),
        ("INSERT INTO oneclick_release_outcomes(job_id,target_label,attempt,receipt_json,receipt_digest,created_at,updated_at) VALUES(?,?,1,'{}',?,'before','before')", (job, actions[0], 'e'*64)),
        ("INSERT INTO oneclick_release_write_occurrences(job_id,target_label,attempt,occurrence_id,intended_classes_json,intended_classes_digest,prior_classes_json,prior_classes_digest,confirmed_lower_bound,possible_upper_bound,status,created_at) VALUES(?,?,1,'original-open','[]',?,'[]',?,0,1,'OPEN','before')", (job, actions[0], native._hash([]), native._hash([]))),
    ]
    def semantics(value): return [{k: v for k, v in r.items() if k != 'updated_at'} for r in value]
    for index, (sql, arguments) in enumerate(variants):
        cloned = tmp_path / f'negative-{index}.db'
        with sqlite3.connect(release.path) as source, sqlite3.connect(cloned) as destination:
            source.backup(destination); destination.execute(sql, arguments)
        before = rows(cloned); active.store = ReleaseStore(cloned)
        try:
            try: consume()
            except (ValueError, RuntimeError): pass  # A production gate may reject even earlier.
            assert semantics(rows(cloned)) == semantics(before) and calls == []
            with sqlite3.connect(cloned) as db:
                assert db.execute("SELECT COUNT(*) FROM oneclick_release_events WHERE event_type='NATIVE_PROMOTION_AUTH_PREPARATION_RESUMED'").fetchone()[0] == 0
        finally: active.store = release
    second = consume()
    assert all(r['status'] == 'SUCCEEDED' for r in rows()), second
    assert sorted(c[1] for c in calls if c[0] == 'POST') == ['MY', 'PH']
    assert release.get_run(run['run_id']) == original_run and release.get_plan(plan['plan_id']) == plan
    successful = rows(); count = len(calls); consume()
    assert semantics(rows()) == semantics(successful) and len(calls) == count
    assert network_attempts == [] and all(r['dispatch_count'] == 1 for r in successful)
    with sqlite3.connect(release.path) as db:
        assert db.execute('SELECT * FROM oneclick_release_events WHERE event_id<=? ORDER BY event_id', (history[-1][0],)).fetchall() == history
        assert db.execute('SELECT batch_sequence FROM oneclick_release_jobs WHERE job_id=?', (job,)).fetchone()[0] == sequence
        assert db.execute('SELECT COUNT(*) FROM oneclick_release_jobs').fetchone()[0] == 1
        resumed = db.execute("SELECT event_json FROM oneclick_release_events WHERE event_type='NATIVE_PROMOTION_AUTH_PREPARATION_RESUMED' ORDER BY target_label").fetchall()
        assert semantics([json.loads(r[0])['original_preparation'] for r in resumed]) == semantics(blocked)
        assert db.execute('SELECT COUNT(*) FROM oneclick_release_outcomes').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM oneclick_release_write_occurrences').fetchone()[0] == 2
