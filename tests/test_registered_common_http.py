import json
from copy import deepcopy
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from modules.products import server
from shared_platform import publication_r2_review as review
from shared_platform import publication_r3_image_bridge as bridge
from test_operations_publication_common import setup


@pytest.fixture
def http_context(tmp_path, monkeypatch):
    engine, task_id, profile, transport, request, store = setup(tmp_path, monkeypatch)
    identity = bridge.validate_r2_identity(bridge.load_r2_documents(request['offer_id']))
    # Narrow registration boundary substitute; real retained R1/R2 documents,
    # COMMON compiler, original approval and ReleaseStore execute below.
    view = {'offer_id': request['offer_id'], 'r2_consumer': {'status': 'PASSED', 'identity': identity}}
    monkeypatch.setattr(review, 'has_registration', lambda *a, **kw: kw.get('runtime_root') == server.ROOT)
    monkeypatch.setattr(review, 'review_view', lambda *a, **kw: deepcopy(view))
    http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    def post(path, body):
        req = Request(f'http://127.0.0.1:{http.server_port}'+path,
            data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        yield post, request, store, transport, view
    finally:
        http.shutdown(); http.server_close(); thread.join(5)


def test_common_http_retired_second_approval_preserves_zero_write(http_context):
    post, request, store, transport, view = http_context
    code, preview = post('/api/product-workspace/r3-common/preview', request)
    assert code == 200, preview
    plan = preview['common']['plan']
    approval = dict(request, plan_id=plan['plan_id'], confirmation_token=plan['confirmation_token'],
                    user_approved=True, approved_by='Kyle')
    bad = dict(approval, confirmation_token='not-the-frozen-token')
    bad_code, bad_result = post('/api/product-workspace/release-plan/approve', bad)
    assert bad_code == 409, bad_result
    code, result = post('/api/product-workspace/release-plan/approve', approval)
    assert code == 409, result
    assert result['error'] == 'R3_COMMON_LEGACY_APPROVAL_RETIRED'
    assert result['external_writes_performed'] == []
    assert store.active_plan_for_product(request['offer_id']) is None
    assert not store.path.exists()
    assert transport.mutations == 0


@pytest.mark.parametrize('damage', ['r2_failed', 'different_identity', 'targets', 'stage', 'facts', 'market_approve', 'market_publish'])
def test_registered_http_denies_other_writes_and_identity_drift(http_context, damage):
    post, request, store, transport, view = http_context
    path = '/api/product-workspace/release-plan/approve'
    data = dict(request, user_approved=True, approved_by='Kyle')
    if damage == 'r2_failed': view['r2_consumer']['status'] = 'BLOCKED'
    elif damage == 'different_identity': view['r2_consumer']['identity']['qa_digest'] = 'wrong'
    elif damage == 'targets': data['publication_targets'] = ['tiktok:LH_PH']
    elif damage == 'stage': data['release_stage'] = 'R3_MARKETPLACE'
    elif damage == 'facts': path = '/api/product-workspace/facts'
    elif damage == 'market_approve': path = '/api/product-workspace/r3-marketplace/approve'
    else: path = '/api/product-workspace/publish-tiktok'
    status, result = post(path, data)
    if damage == 'market_publish' and status == 503:
        assert result['error'] == 'stable runtime identity is not verified'
        assert result['external_write_count'] == 0
    else:
        assert status in {400, 409}, result
    assert not store.path.exists()
    assert transport.mutations == 0


def test_market_publish_identity_gate_denies_before_any_write(
    http_context, monkeypatch
):
    from shared_platform import runtime_identity

    post, request, store, transport, _view = http_context
    monkeypatch.setenv('ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME', '1')
    monkeypatch.setattr(
        runtime_identity,
        'health_payload',
        lambda *args, **kwargs: {'state': 'DATA_PROFILE_MISMATCH'},
    )

    status, result = post('/api/product-workspace/publish-tiktok', request)

    assert status == 503
    assert result['error'] == 'stable runtime identity is not verified'
    assert result['external_write_count'] == 0
    assert not store.path.exists()
    assert transport.mutations == 0


def test_final_preview_passes_admission_but_keeps_domain_preconditions(http_context):
    post, request, store, transport, view = http_context
    code, result = post('/api/product-workspace/r3-marketplace/preview', {'offer_id':request['offer_id']})
    # No COMMON write/readback has happened: the original compiler must refuse.
    assert code == 409, result
    assert '未通过本操作' not in result.get('error', '')
    assert not store.path.exists()
    assert transport.mutations == 0
