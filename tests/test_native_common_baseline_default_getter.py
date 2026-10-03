"""The actual UI's offer-only GET chooses and returns the same retained plan."""
import http.client
import json
from urllib.parse import urlencode

import pytest

from shared_platform import native_common_technical_execution as technical
from shared_platform.publication_runtime_config import redact_http_documents
from shared_platform.r3_frozen_review_producer import DomainFrozenReviewProducer
from shared_platform.release_store import ReleaseStore
from test_round1_workspace_freeze import live
from test_native_common_retained_review_graph import _completed, _market
from test_native_common_baseline_source import _service_config
from test_native_common_baseline_getter import _no_new_write_or_current_read


@pytest.mark.parametrize('retained', [True, False], ids=['retained', 'unknown'])
def test_offer_only_real_getter_returns_exact_matrix_plan_or_stays_unknown(live, monkeypatch, tmp_path, retained):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, plan, run_id)
    _service_config(tmp_path, monkeypatch)
    if not retained:
        with technical._existing_transaction(store) as db:
            db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?', ('{}', run_id))
    _no_new_write_or_current_read(monkeypatch)
    original_inspect = DomainFrozenReviewProducer.inspect
    called = []
    def observe_inspect(producer, db, common_run_id, market_id):
        assert db.in_transaction and db.execute('PRAGMA query_only').fetchone()[0] == 1
        called.append((common_run_id, market_id))
        def forbidden(*args, **kwargs):
            raise AssertionError('Matrix rebuild must use the existing snapshot, not a second connection')
        with monkeypatch.context() as guarded:
            guarded.setattr(ReleaseStore, '_connect_readonly', forbidden)
            return original_inspect(producer, db, common_run_id, market_id)
    monkeypatch.setattr(DomainFrozenReviewProducer, 'inspect', observe_inspect)
    before = store.path.read_bytes()
    connection = http.client.HTTPConnection('127.0.0.1', live['port'], timeout=15)
    try:
        connection.request('GET', '/api/product-workspace/publication-stages?' + urlencode({'offer_id':live['offer']}))
        response = connection.getresponse()
        code, value = response.status, json.loads(response.read())
    finally:
        connection.close()
    assert code == 200, value
    common, result = value['common'], value['marketplace']
    assert result['plan']['plan_id'] == market['plan_id']
    assert result['plan']['payload_digest'] == market['payload_digest']
    if retained:
        assert result['plan']['payload'] == redact_http_documents(market['payload'])
        assert called == [(run_id, market['plan_id'])]
        assert common['status'] == 'RETAINED_TECHNICAL_BASELINE'
        assert result['status'] == 'INERT_COMPLETE_CANDIDATE'
        assert result['manifest']['targets'] and result['manifest']['copy_sets'] and result['manifest']['image_sets']
        assert common['user_status']['candidate_summary'] == '完整候选资料已恢复'
    else:
        assert called == []
        assert common['status'] == 'TECHNICAL_CONDITIONS_UNKNOWN' and result['status'] == 'UNKNOWN'
        assert not {'candidate', 'manifest'} & set(result)
    assert common['user_status']['common_review_needed'] is False
    assert result['final_review_available'] is result['execution_authority'] is False
    assert result['final_review']['approval_recorded'] is False
    assert value['external_writes_performed'] == [] and store.path.read_bytes() == before
