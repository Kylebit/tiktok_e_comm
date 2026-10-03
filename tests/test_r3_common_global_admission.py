"""Counterexamples for every old R3 COMMON HTTP/provider entry."""

from modules.products import server
from test_b4b_common_stage import CommonTransport, approve, context
from test_b4b_common_http_cli import api, request


def test_legacy_http_approval_cannot_create_second_r3_common_decision(api):
    base, (_, _, store, data, io) = api
    status, preview = request(base, '/api/product-workspace/r3-common/preview', data)
    assert status == 200
    plan = preview['common']['plan']
    status, result = request(base, '/api/product-workspace/release-plan/approve', {
        **data, 'plan_id': plan['plan_id'],
        'confirmation_token': plan['confirmation_token'],
        'user_approved': True, 'approved_by': 'Kyle',
    })
    assert status == 409, result
    assert result['error'] == 'R3_COMMON_LEGACY_APPROVAL_RETIRED'
    assert result['external_writes_performed'] == []
    assert store.get_plan(plan['plan_id']) is None
    assert io.mutations == 0


def test_existing_approved_r3_common_cannot_write_without_offer_history(tmp_path, monkeypatch):
    _, _, store, data = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch, synthetic_technical_authority=False)
    exact = approve(data)  # Historical approval fixture; must remain durable.
    before = store.get_plan(exact['plan_id'])
    status, result = server._prepare_miaoshou_release({
        **exact, 'confirm_miaoshou_write': True,
    })
    assert status == 409, result
    assert result['error'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED'
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in result['blockers']
    assert result['external_writes_performed'] == []
    assert store.get_plan(exact['plan_id']) == before
    assert io.mutations == 0


def test_common_authority_is_rechecked_after_claim_before_provider(tmp_path, monkeypatch):
    from shared_platform import publication_common_write_admission as admission
    _, _, store, data = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch, synthetic_technical_authority=False)
    exact = approve(data)
    inspect = admission.inspect_common_write_admission
    calls = []
    claimed = {}
    begin = store.begin_target
    def begin_target(run_id, target_label):
        result = begin(run_id, target_label)  # Real claim commits before observation.
        claimed['run_id'] = run_id
        assert result['status'] == 'RUNNING'
        return result
    monkeypatch.setattr(store, 'begin_target', begin_target)
    def authority(plan, **context):
        target_status = None
        if claimed:
            target = next(row for row in store.get_run(claimed['run_id'])['targets']
                          if row['target_label'] == 'miaoshou:COMMON')
            target_status = target['status']
        calls.append((plan['plan_id'], target_status))
        if not claimed:
            return {'status': 'READY', 'binding': {
                'offer_id': plan['product_id'], 'plan_id': plan['plan_id'],
                'payload_digest': plan['payload_digest']},
                'receipt_digest': 'synthetic-expiring-authority'}
        return inspect(plan, **context)
    monkeypatch.setattr(admission, 'inspect_common_write_admission', authority)
    status, result = server._prepare_miaoshou_release({**exact, 'confirm_miaoshou_write': True})
    assert status != 200, result
    # Preflight, real post-claim guard, then the blocked read-only projection.
    # A moved/omitted post-claim guard must not be hidden by the getter call.
    assert calls == [(exact['plan_id'], None),
                     (exact['plan_id'], 'RUNNING'),
                     (exact['plan_id'], 'FAILED')]
    assert result['error'] == 'COMMON_TECHNICAL_ADMISSION_CHANGED_BEFORE_DISPATCH'
    assert io.mutations == 0


def test_legacy_manual_approval_remains_recorded_but_common_write_is_held(tmp_path, monkeypatch):
    from test_product_release_v1 import _dashboard, _request
    from shared_platform import release_control, release_store
    store = release_store.ReleaseStore(tmp_path / 'release.db')
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    monkeypatch.setattr(release_store, 'default_release_store', lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(release_control, 'build_release_dashboard', lambda **_: dashboard)
    request = _request(server._product_workspace_view(dashboard))
    status, approved = server._approve_release_plan_locally({
        **request, 'approved_by': 'Kyle', 'user_approved': True})
    assert status == 200, approved  # Existing manual entry remains a HOLD.
    before = store.get_plan(request['plan_id'])
    calls = []
    from modules.products import release_adapters
    monkeypatch.setattr(release_adapters, 'write_miaoshou_common_from_plan',
                        lambda payload: calls.append(payload) or {})
    status, blocked = server._prepare_miaoshou_release({
        **request, 'confirm_miaoshou_write': True})
    assert status == 409, blocked
    assert blocked['error'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED'
    assert 'COMMON_TECHNICAL_BINDING_INVALID' in blocked['blockers']
    assert store.get_plan(request['plan_id']) == before
    assert calls == []
