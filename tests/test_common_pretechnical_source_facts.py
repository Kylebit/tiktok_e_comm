"""COMMON preparation source must be readable before final marketplace state.

Real ReleaseStore/bridge with closed fixtures; these are retained input facts,
not account/standing-policy/coverage receipts or a dispatch capability.
"""
import hashlib
import json

import pytest

from modules.products import server
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.r3_frozen_review_producer import DomainFrozenReviewProducer, DomainReviewBlocked
from test_b4b_common_stage import context
from test_r3_common_source_facts import connect, rows


def _read_common_input(store, db, common_plan_id):
    # Exercise the existing production entry, not a proposed method's absence.
    try:
        return NativeCommonSourceReader(store).read_source_facts(db, common_plan_id)
    except DomainReviewBlocked as error:
        pytest.fail('PRE_COMMON_SOURCE_REQUIRES_POST_COMMON_GRAPH: ' + str(error))


def test_first_common_input_does_not_require_marketplace_or_successful_common_run(tmp_path, monkeypatch):
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    code, view = server._preview_r3_common_stage(request)
    assert code == 200, view
    plan = store.create_plan(view['common']['plan']['payload'])
    assert plan['targets'] == ['miaoshou:COMMON']
    with connect(store) as db:
        before = rows(db)
        assert db.execute('SELECT COUNT(*) FROM release_plans').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
        facts = _read_common_input(store, db, plan['plan_id'])
        assert facts.graph.offer_id == request['offer_id']
        assert facts.graph.common_plan_id == plan['plan_id']
        assert facts.graph.marketplace_plan_id is None and facts.graph.common_run_id is None
        assert facts.graph.targets == tuple(plan['payload']['r3_stage_binding']['marketplace_targets'])
        assert facts.common_plan_ids == facts.unstarted_common_plan_ids == (plan['plan_id'],)
        retained = next(row for row in facts.records if row.table == 'release_plans')
        raw = db.execute('SELECT payload_json,payload_digest FROM release_plans WHERE plan_id=?',
                         (plan['plan_id'],)).fetchone()
        assert retained.evidence_bytes == raw[0].encode('utf-8')
        assert retained.evidence_digest == raw[1] == hashlib.sha256(retained.evidence_bytes).hexdigest()
        assert facts.source_coverage == 'LOCAL_RETAINED_ONLY'
        assert facts.official_provenance == facts.budget_status == 'UNKNOWN'
        assert facts.execution_authority is False
        with pytest.raises(DomainReviewBlocked, match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
            NativeCommonSourceReader(store).read_verified(db, 'unadmitted', plan['plan_id'])
        assert rows(db) == before


def test_common_input_retains_normalized_history_without_authority_or_final_graph_adoption(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace
    monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY', None)
    documents, dashboard, store, request, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    assert io.mutations == 0  # Explicit historical seed; no current EDIT.
    common_id = market['plan']['payload']['r3_marketplace_binding']['common_plan_id']
    # The marketplace draft has not been persisted. Source facts cannot depend
    # on adopting the already-returned display or importing a private marker.
    assert store.get_plan(market['plan']['plan_id']) is None
    with connect(store) as db:
        before = rows(db)
        facts = _read_common_input(store, db, common_id)
        assert facts.graph.common_plan_id == common_id
        assert facts.graph.marketplace_plan_id is None
        readback = next(row for row in facts.records if row.table == 'release_target_readbacks')
        expected = db.execute('SELECT evidence_json,evidence_digest FROM release_target_readbacks').fetchone()
        assert readback.evidence_bytes == expected[0].encode('utf-8')
        assert readback.evidence_digest == expected[1]
        assert 'native_common_observation' not in json.loads(readback.evidence_bytes)
        assert readback.transport_observation is None and readback.official_response_bytes is None
        assert facts.official_provenance == facts.budget_status == 'UNKNOWN'
        assert facts.execution_authority is False and not hasattr(facts, 'budget_digest')
        with pytest.raises(DomainReviewBlocked, match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
            DomainFrozenReviewProducer(NativeCommonSourceReader(store)).build(db, 'unadmitted', common_id)
        assert rows(db) == before
