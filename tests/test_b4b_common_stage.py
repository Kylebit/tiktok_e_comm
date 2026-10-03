from copy import deepcopy
import json
from pathlib import Path

import pytest

from modules.products import server, release_adapters
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import release_control, release_store

FIXTURE = Path(__file__).parent / 'fixtures/b4b_common_actual'
OFFER = '9000052'


def context(tmp_path, monkeypatch, *, fixture=FIXTURE):
    from shared_platform.publication_runtime_config import capture_startup_config
    # Synthetic provider tests must never inherit the workstation's stable ledger.
    monkeypatch.delenv('ORBIT_OPERATIONS_DATA_ROOT', raising=False)
    monkeypatch.setenv('ORBIT_OPERATIONS_PROFILE', str(tmp_path / 'no-operations-profile.json'))
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', capture_startup_config(root=tmp_path, environ={}))
    documents = {key: json.loads((fixture / name).read_text(encoding='utf-8'))
                 for key, name in bridge.R2_DOCUMENTS.items()}
    dashboard = json.loads((fixture / 'source-dashboard.json').read_text(encoding='utf-8'))
    root = tmp_path / 'reports'
    directory = root / OFFER
    directory.mkdir(parents=True)
    for key, name in bridge.R2_DOCUMENTS.items():
        (directory / name).write_text(json.dumps(documents[key]), encoding='utf-8')
    monkeypatch.setattr(bridge, 'REPORTS_ROOT', root)
    from shared_platform import publication_rounds as rounds
    from modules.sourcing import new_product_workbench as workbench
    # These retained original-producer documents predate prepared references.
    # Bind their actual legacy approval facts and roots to the same fixture;
    # do not let the service classifier read a checkout/another fixture root.
    snapshot=documents['round1_snapshot']
    actual=dashboard['product']['actual_approval']
    assert dashboard['product']['actual_product_approved'] is True
    assert snapshot['product_approval_id']==actual['approval_id']
    assert snapshot['product_approval_fingerprint']==actual['input_fingerprint']
    assert snapshot['approved_by']=='Kyle' and snapshot['approval_authority']=='EXPLICIT_CONVERSATION_APPROVAL'
    assert snapshot['decision_receipt_digest'] is None
    assert rounds.canonical_digest(documents['first_review'])==snapshot['first_review_digest']
    state_root=tmp_path/'legacy-product-state';state_root.mkdir()
    state={'offer_id':OFFER,'review':{'selected_sites':snapshot['workbench_tiktok_sites']},
           'product_approval':{**deepcopy(actual),'status':'approved',
              'approved_by':snapshot['approved_by'],'approved_at':snapshot['approved_at'],
              'approval_authority':snapshot['approval_authority']}}
    (state_root/(OFFER+'.json')).write_text(json.dumps(state),encoding='utf-8')
    monkeypatch.setattr(workbench,'STATE_DIR',state_root)
    monkeypatch.setattr(rounds,'REPORTS_ROOT',root)
    monkeypatch.setattr(server,'_NATIVE_FINAL_SERVICE',None)
    assert rounds.validate_round2_input(OFFER,workbench.load_state(OFFER))==snapshot
    monkeypatch.setattr(release_control, 'build_release_dashboard', lambda **kw: deepcopy(dashboard))
    store = release_store.ReleaseStore(tmp_path / 'release.db')
    monkeypatch.setattr(release_store, 'default_release_store', lambda: store)
    request = {'offer_id': OFFER, 'publication_targets': ['miaoshou:COMMON'], 'release_stage': 'R3_COMMON'}
    return documents, dashboard, store, request


def test_actual_r1_r2_preview_needs_only_common_scope_and_no_store_write(tmp_path, monkeypatch):
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    status, result = server._preview_r3_common_stage(request)
    assert status == 200, result
    assert result['common']['status'] == 'TECHNICAL_CONDITIONS_UNKNOWN'
    assert result['common']['new_common_human_approval_needed'] is False
    assert result['external_writes_performed'] == []
    assert result['common']['plan']['status'] == 'NOT_PERSISTED'
    assert result['common']['plan']['persisted'] is False
    assert result['common']['plan']['approved'] is False
    payload = result['common']['plan']['payload']
    assert payload['targets'] == ['miaoshou:COMMON']
    assert 'approved_publication_snapshot_schema_version' not in payload
    assert payload['r3_stage_binding']['round1_snapshot_digest'] == documents['round1_snapshot']['snapshot_digest']
    assert 'approved_postpublish_promotion_policy' not in payload
    assert not store.path.exists()


@pytest.mark.parametrize('field', ['weight_kg','seller_sku_candidate','title'])
def test_common_preview_rejects_current_product_drift(tmp_path, monkeypatch, field):
    _, dashboard, store, request = context(tmp_path, monkeypatch)
    dashboard['product'][field] = 'wrong'
    status, result = server._preview_r3_common_stage(request)
    assert status == 409, result
    assert result['external_writes_performed'] == []
    assert not store.path.exists()


class CommonTransport:
    def __init__(self, monkeypatch, *, timeout=False, synthetic_technical_authority=True):
        from modules.miaoshou import client

        if synthetic_technical_authority:
            # Test-only future producer: retains old provider/readback mechanics
            # coverage while production remains UNKNOWN/BLOCKED for every Offer.
            from shared_platform import publication_common_write_admission as admission
            monkeypatch.setattr(admission, 'inspect_common_write_admission',
                lambda plan, **context: {'status': 'READY', 'blockers': [],
                    'binding': {'offer_id': plan['product_id'], 'plan_id': plan['plan_id'],
                                'payload_digest': plan['payload_digest']},
                    'receipt_digest': 'synthetic-authority-only'})
        self.mutations = 0
        self.reads = 0
        self.timeout = timeout
        self.detail = {'commonCollectBoxDetailId': int(OFFER), 'sourceOfferId': '986159122616',
                       'title': 'Before', 'itemNum': '0952',
                       'skuMap': {'default': {'itemNum': '0952', 'specLabel': 'Blue'}}}
        monkeypatch.setattr(client, 'post_open', self.post)

    def post(self, path, body):
        assert body['commonCollectBoxDetailId'] == int(OFFER)
        if path == release_adapters.MIAOSHOU_COMMON_DETAIL_PATH:
            self.reads += 1
            return {'result': 'success', 'data': {'editCommonCollectBoxDetail': deepcopy(self.detail), 'ossMd5': 'synthetic-md5'}}
        assert path == release_adapters.MIAOSHOU_COMMON_EDIT_PATH
        self.mutations += 1
        self.detail = deepcopy(body['editCommonCollectBoxDetail'])
        if self.timeout:
            raise TimeoutError('synthetic response lost after edit dispatch')
        return {'result': 'success'}


def approve(request):
    from shared_platform.release_store import default_release_store
    status, view = server._preview_r3_common_stage(request)
    assert status == 200, view
    plan = view['common']['plan']
    approved_request = dict(request, plan_id=plan['plan_id'], confirmation_token=plan['confirmation_token'])
    # Historical approved-plan fixture for readback and recovery tests. The
    # retired HTTP approval route must no longer create this second decision.
    store = default_release_store()
    store.create_plan(plan['payload'])
    store.approve_plan(plan['plan_id'], user_approved=True, approved_by='Kyle',
                       confirmation_token=plan['confirmation_token'])
    return approved_request


def test_actual_common_writer_claim_approval_readback_and_replay(tmp_path, monkeypatch):
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch)
    status, missing = server._prepare_miaoshou_release(dict(request, confirm_miaoshou_write=True))
    assert status == 409 and io.mutations == 0 and io.reads == 0
    approved = approve(request)
    assert io.mutations == 0
    for expected_idempotent in [False, True]:
        status, result = server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))
        assert status == 200, result
        assert result['idempotent'] is expected_idempotent
        assert result['run']['targets'][0]['status'] == 'SUCCEEDED'
    assert io.mutations == 1 and io.reads == 2
    assert io.detail['skuMap']['default']['itemNum'] == '0952'
    assert server._preview_r3_common_stage(request)[1]['common']['status'] == 'VERIFIED'


@pytest.mark.parametrize('damage', ['current_id', 'stored_id', 'stored_facts', 'stored_revision'])
def test_common_comparison_authenticates_id_and_complete_stored_payload(tmp_path, monkeypatch, damage):
    _, _, _, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch)
    approved = approve(request)
    compare = server._approved_plan_matches_current_payload
    examined = []

    def inspect(persisted, preview, *, current_source_payload=None):
        assert compare(persisted, preview, current_source_payload=current_source_payload)
        stored = deepcopy(persisted)
        raw = deepcopy(current_source_payload)
        if damage == 'current_id':
            raw['plan_id'] += '-tampered'
        elif damage == 'stored_id':
            stored['payload']['plan_id'] += '-tampered'
        elif damage == 'stored_facts':
            stored['payload']['product_facts']['source_offer_id'] = '123'
        else:
            stored['payload']['product_revision'] = 'tampered'
        changed_preview = release_store.preview_release_plan(raw)
        assert not compare(stored, changed_preview, current_source_payload=raw)
        examined.append(damage)
        return False

    monkeypatch.setattr(server, '_approved_plan_matches_current_payload', inspect)
    status, _ = server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))
    assert status == 409 and examined == [damage]
    assert io.mutations == 0 and io.reads == 0


@pytest.mark.parametrize('mode', ['timeout', 'claimed_before_process_loss'])
def test_actual_common_unknown_or_claimed_never_redispatches(tmp_path, monkeypatch, mode):
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch, timeout=True)
    approved = approve(request)
    if mode == 'claimed_before_process_loss':
        run = store.start_run(approved['plan_id'])
        store.begin_target(run['run_id'], 'miaoshou:COMMON')
    for _ in range(2):
        status, result = server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))
        assert status in {409, 502}, result
    assert io.mutations == (1 if mode == 'timeout' else 0)
    assert server._preview_r3_common_stage(request)[1]['common']['status'] == 'RECONCILIATION_REQUIRED'


@pytest.mark.parametrize('field,value', [('commonCollectBoxDetailId', 123), ('sourceOfferId','123')])
def test_actual_common_writer_rejects_wrong_official_binding_before_edit(tmp_path, monkeypatch, field, value):
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch)
    io.detail[field] = value
    approved = approve(request)
    status, result = server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))
    assert status != 200
    assert io.mutations == 0


@pytest.mark.parametrize('damage', ['missing_receipt', 'bad_digest', 'wrong_offer', 'false_check', 'missing_check'])
def test_common_stage_requires_durable_exact_readback(tmp_path, monkeypatch, damage):
    import sqlite3
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch)
    approved = approve(request)
    status, result = server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))
    assert status == 200, result
    with sqlite3.connect(store.path) as db:
        if damage == 'missing_receipt':
            db.execute('DELETE FROM release_target_readbacks')
        elif damage == 'bad_digest':
            db.execute("UPDATE release_target_readbacks SET evidence_digest = 'bad'")
        else:
            text = db.execute('SELECT evidence_json FROM release_target_readbacks').fetchone()[0]
            evidence = json.loads(text)
            if damage == 'wrong_offer':
                evidence['offer_id'] = '123'
            elif damage == 'false_check':
                evidence['checks']['title'] = False
            else:
                evidence['checks'].pop('selected_sku_numbers')
            encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            import hashlib
            db.execute('UPDATE release_target_readbacks SET evidence_json = ?, evidence_digest = ?',
                       (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    view = server._preview_r3_common_stage(request)[1]
    assert view['common']['status'] == 'RECONCILIATION_REQUIRED', view
    assert view['common']['blockers']
    status, replay = server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))
    assert status == 409, replay
    assert io.mutations == 1


@pytest.mark.parametrize('when', ['before_claim', 'after_claim'])
@pytest.mark.parametrize('drift', ['facts', 'source', 'assignment', 'qa', 'generation', 'round1'])
def test_common_current_identity_drift_never_dispatches(tmp_path, monkeypatch, when, drift):
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch)
    approved = approve(request)
    def change():
        if drift == 'facts':
            dashboard['product']['sku_commercial_facts']['default']['cost_cny'] = '999'
        elif drift == 'source':
            dashboard['_source_identity_inputs']['collect_box']['source_item_id'] = '123'
        elif drift == 'assignment':
            dashboard['_sku_lineage']['assignment']['seller_sku'] = '9999'
        else:
            key = {'qa': 'image_qa', 'generation': 'generation_result', 'round1': 'round1_snapshot'}[drift]
            documents[key]['offer_id'] = '123'
            (bridge.REPORTS_ROOT / OFFER / bridge.R2_DOCUMENTS[key]).write_text(json.dumps(documents[key]), encoding='utf-8')
    if when == 'before_claim':
        change()
    else:
        begin = store.begin_target
        def begin_then_change(*args, **kwargs):
            result = begin(*args, **kwargs)
            change()
            return result
        monkeypatch.setattr(store, 'begin_target', begin_then_change)
    status, result = server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))
    assert status != 200, result
    assert io.mutations == 0 and io.reads == 0


def test_real_store_concurrent_common_claim_dispatches_once(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch)
    exact = approve(request)
    entered, release = threading.Event(), threading.Event()
    from modules.miaoshou import client
    def delayed(path, body):
        if path == release_adapters.MIAOSHOU_COMMON_EDIT_PATH:
            entered.set()
            assert release.wait(5)
        return io.post(path, body)
    monkeypatch.setattr(client, 'post_open', delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(server._prepare_miaoshou_release, dict(exact, confirm_miaoshou_write=True))
        try:
            assert entered.wait(5)
            second = pool.submit(server._prepare_miaoshou_release, dict(exact, confirm_miaoshou_write=True)).result(5)
            assert second[0] == 409, second
        finally:
            release.set()
        assert first.result(5)[0] == 200
    assert io.mutations == 1 and io.reads == 2


def test_common_unknown_does_not_lock_an_independent_provider_detail(tmp_path, monkeypatch):
    from test_release_store import _plan
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch, timeout=True)
    exact = approve(request)
    assert server._prepare_miaoshou_release(dict(exact, confirm_miaoshou_write=True))[0] != 200
    # Ledger scope test only; this minimal Store fixture makes no R1/R2 quality claim.
    independent = _plan(plan_id='r3-independent-detail', targets=['miaoshou:COMMON'],
        r3_stage_binding={'schema_version': 'r3-common-stage/v1'})
    assert independent['product_id'] != OFFER
    assert store.create_plan(independent)['status'] == 'PENDING_APPROVAL'
    assert store.common_reconciliation_reference(independent['product_id']) is None
    assert store.common_reconciliation_reference(OFFER)['plan_id'] == exact['plan_id']
    assert io.mutations == 1


def test_actual_sparse_r2_cannot_create_common_commercial_plan(tmp_path, monkeypatch):
    _, _, store, request = context(tmp_path, monkeypatch)
    sparse = Path(__file__).parent / 'fixtures/b4b_r2_actual'
    for filename in bridge.R2_DOCUMENTS.values():
        (bridge.REPORTS_ROOT / OFFER / filename).write_bytes((sparse / filename).read_bytes())
    status, result = server._preview_r3_common_stage(request)
    assert status == 409, result
    assert not store.path.exists()


@pytest.mark.parametrize('prior', ['timeout', 'missing_success_receipt', 'failed_without_receipt'])
def test_unknown_common_cannot_be_replaced_by_a_new_valid_r1_r2_identity(tmp_path, monkeypatch, prior):
    _, dashboard, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch, timeout=prior == 'timeout')
    exact = approve(request)
    if prior == 'failed_without_receipt':
        # Existing public Store API permits a failed claimed attempt without an event.
        run = store.start_run(exact['plan_id'])
        store.begin_target(run['run_id'], 'miaoshou:COMMON')
        store.record_target_failure(run['run_id'], 'miaoshou:COMMON', error='process failure without dispatch evidence')
    else:
        status, result = server._prepare_miaoshou_release(dict(exact, confirm_miaoshou_write=True))
        assert (status == 200) == (prior == 'missing_success_receipt'), result
        if prior == 'missing_success_receipt':
            # Same durable-loss scenario as the retained current-plan readback red.
            import sqlite3
            with sqlite3.connect(store.path) as db:
                db.execute('DELETE FROM release_target_readbacks')
    fixture = Path(__file__).parent / 'fixtures/b4b_common_multivariant_actual'
    dashboard.clear()
    dashboard.update(json.loads((fixture / 'source-dashboard.json').read_text(encoding='utf-8')))
    for filename in bridge.R2_DOCUMENTS.values():
        (bridge.REPORTS_ROOT / OFFER / filename).write_bytes((fixture / filename).read_bytes())
    status, preview = server._preview_r3_common_stage(request)
    if status == 200:
        plan = preview['common']['plan']
        assert plan['plan_id'] != exact['plan_id']
        status, result = server._approve_release_plan_locally(dict(request, plan_id=plan['plan_id'],
            confirmation_token=plan['confirmation_token'], user_approved=True, approved_by='Kyle'))
        assert status == 409, result
    else:
        assert status == 409, preview
    assert io.mutations == (0 if prior == 'failed_without_receipt' else 1)
    assert store.active_plan_for_product(OFFER)['plan_id'] == exact['plan_id']


@pytest.mark.parametrize('field,value', [('weight', 999), ('packageLength', 999), ('weight', None),
    ('packageWidth', None), ('weight', 100), ('itemNum', '095201')])
def test_multivariant_official_logistics_corruption_cannot_pass_readback(tmp_path, monkeypatch, field, value):
    from test_b4b_publication_preview import multivariant_context
    from modules.miaoshou import client
    _, _, _, request, io = multivariant_context(tmp_path, monkeypatch)
    def corrupt_after_edit(path, body):
        result = io.post(path, body)
        if path == release_adapters.MIAOSHOU_COMMON_EDIT_PATH:
            if value is None:
                io.detail['skuMap']['blue'].pop(field)
            else:
                io.detail['skuMap']['blue'][field] = value
        return result
    monkeypatch.setattr(client, 'post_open', corrupt_after_edit)
    exact = approve(request)
    status, result = server._prepare_miaoshou_release(dict(exact, confirm_miaoshou_write=True))
    assert status != 200, result
    assert server._preview_r3_common_stage(request)[1]['common']['status'] == 'RECONCILIATION_REQUIRED'
    assert io.mutations == 1
