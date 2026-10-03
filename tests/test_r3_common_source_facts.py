"""Real retained domain graph; every transport is closed SYNTHETIC_TEST_ONLY.

This exercises source extraction, never a claim of official provenance/budget.
"""
import hashlib
import json
import sqlite3

import pytest

from shared_platform.r3_frozen_review_producer import DomainFrozenReviewProducer, DomainReviewBlocked


@pytest.fixture
def stored_common(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace
    documents, dashboard, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    store.create_plan(market["plan"]["payload"])
    assert io.mutations == 0
    assert market["final_review_available"] is False
    assert market["final_review_admission"]["execution_authority"] is False
    return store, market["plan"]["plan_id"]


def connect(store):
    db = sqlite3.connect(f"file:{store.path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    return db


def rows(db):
    return {row[0]: [tuple(value) for value in db.execute('SELECT * FROM "' + row[0] + '"')]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


def test_full_graph_source_preserves_original_stored_bytes_without_claiming_official(stored_common):
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    store, market_id = stored_common
    with connect(store) as db:
        before = rows(db)
        facts = DomainFrozenReviewProducer(NativeCommonSourceReader(store)).read_native_source_facts(db, market_id)
        again = NativeCommonSourceReader(store).read_source_facts(db, market_id)
        assert before == rows(db)
        assert facts.graph.targets == ("tiktok:LH_PH", "ozon:RU")
        assert facts.graph.common_plan_id != market_id
        assert facts.source_coverage == "LOCAL_RETAINED_ONLY"
        assert facts.official_provenance == "UNKNOWN"
        assert facts.budget_status == "UNKNOWN"
        assert facts.execution_authority is False
        assert facts.evidence_kind == "LOCAL_RETAINED_COMMON_SOURCE_FACTS"
        assert facts.records == again.records
        readback = next(row for row in facts.records if row.table == "release_target_readbacks")
        expected = db.execute("SELECT evidence_json,evidence_digest FROM release_target_readbacks WHERE run_id=?",
                              (facts.graph.common_run_id,)).fetchone()
        assert readback.evidence_bytes == expected[0].encode("utf-8")
        assert readback.evidence_digest == expected[1]
        assert hashlib.sha256(readback.evidence_bytes).hexdigest() == expected[1]
        assert readback.official_response_bytes is None
        # This owned historical fixture retains the original readonly detail
        # envelope. Its source label never grants official provenance or budget.
        assert json.loads(readback.evidence_bytes)["source"] == "miaoshou_common_readonly_detail"
        assert not hasattr(facts, "budget_digest")


def test_requires_existing_snapshot_and_exact_store(stored_common, tmp_path):
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.release_store import ReleaseStore
    store, market_id = stored_common
    with connect(store) as db:
        with pytest.raises(DomainReviewBlocked, match="COMMON_SOURCE_DATABASE_MISMATCH"):
            NativeCommonSourceReader(ReleaseStore(tmp_path / "other.db")).read_source_facts(db, market_id)
        db.rollback()
        with pytest.raises(DomainReviewBlocked, match="DOMAIN_SQLITE_SNAPSHOT_REQUIRED"):
            NativeCommonSourceReader(store).read_source_facts(db, market_id)


def test_native_facts_cannot_authorize_candidate_or_accept_caller_proof(stored_common):
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    store, market_id = stored_common
    with connect(store) as db:
        before = rows(db)
        with pytest.raises(DomainReviewBlocked, match="COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED"):
            DomainFrozenReviewProducer(NativeCommonSourceReader(store)).build(db, "unadmitted", market_id)
        with pytest.raises(DomainReviewBlocked, match="COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED"):
            DomainFrozenReviewProducer({"verified": True, "budget": 0}).build(db, "unadmitted", market_id)
        assert rows(db) == before


def test_mixed_and_unstarted_common_plans_remain_facts_not_lifetime_budget(stored_common):
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    store, market_id = stored_common
    common = store.get_plan(store.get_plan(market_id)["payload"]["r3_marketplace_binding"]["common_plan_id"])
    # Retained draft identity comes from the actual ReleaseStore compiler.
    payload = {"plan_id": "source-extra:mixed-common-draft", "product_id": common["product_id"],
               "seller_sku": "1099", "product_package_id": "source-extra-product",
               "content_package_id": "source-extra-content", "product_revision": 1,
               "targets": ["miaoshou:COMMON", "tiktok:LH_PH"]}
    extra = store.create_plan(payload)
    with connect(store) as db:
        before = rows(db)
        facts = NativeCommonSourceReader(store).read_source_facts(db, market_id)
        assert extra["plan_id"] in facts.common_plan_ids
        assert extra["plan_id"] in facts.unstarted_common_plan_ids
        assert len(facts.common_plan_ids) == 2
        assert facts.budget_status == "UNKNOWN"
        assert facts.source_coverage == "LOCAL_RETAINED_ONLY"
        assert rows(db) == before


def test_unrelated_channel_history_is_not_common_budget_or_review_content(stored_common):
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    store, market_id = stored_common
    with connect(store) as db:
        first = NativeCommonSourceReader(store).read_source_facts(db, market_id)
    store.create_plan({"plan_id": "source-extra:ozon-draft", "product_id": first.graph.offer_id,
                       "seller_sku": "1099", "product_package_id": "source-extra-product",
                       "content_package_id": "source-extra-content", "product_revision": 1,
                       "targets": ["ozon:RU"]})
    with connect(store) as db:
        later = NativeCommonSourceReader(store).read_source_facts(db, market_id)
        assert first.graph == later.graph
        assert first.records == later.records
        assert first.common_plan_ids == later.common_plan_ids
        assert later.budget_status == "UNKNOWN"


def test_changed_history_evidence_bytes_fail_without_mutation(stored_common):
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    store, market_id = stored_common
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_readbacks SET evidence_json=evidence_json || ' '")
    with connect(store) as db:
        before = rows(db)
        with pytest.raises(DomainReviewBlocked, match="DOMAIN_STORED_READBACK_BYTES_CHANGED"):
            NativeCommonSourceReader(store).read_source_facts(db, market_id)
        assert rows(db) == before


def test_mixed_history_keeps_only_common_member_attempt_and_unknown_evidence(stored_common):
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    store, market_id = stored_common
    offer = store.get_plan(market_id)["product_id"]
    extra = store.create_plan({"plan_id": "source-extra:mixed-run", "product_id": offer,
                              "seller_sku": "1099", "product_package_id": "source-extra-product",
                              "content_package_id": "source-extra-content", "product_revision": 1,
                              "targets": ["miaoshou:COMMON", "ozon:RU"]})
    # Legacy fixture setup is not production approval authority. The reader
    # must retain this source independently of the current registered graph.
    store.approve_plan(extra["plan_id"], approved_by="Kyle", user_approved=True,
                       confirmation_token=extra["confirmation_token"])
    run = store.start_run(extra["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    unknown = {"source": "SYNTHETIC_TEST_ONLY", "write_outcome": "unknown_after_dispatch"}
    store.record_target_failure(run["run_id"], "miaoshou:COMMON", error="fixture timeout", failure_evidence=unknown)
    with connect(store) as db:
        before = rows(db)
        facts = NativeCommonSourceReader(store).read_source_facts(db, market_id)
        history = [value for value in facts.records if value.identity[0] == run["run_id"]]
        target = next(value for value in history if value.table == "release_target_runs")
        assert json.loads(target.record_bytes)["attempts"] == 1
        assert json.loads(target.record_bytes)["status"] == "FAILED"
        failure = next(value for value in history if value.table == "release_target_failure_events")
        assert json.loads(failure.evidence_bytes) == unknown
        assert all(value.identity[1] == "miaoshou:COMMON" for value in history if value.table != "release_runs")
        assert facts.budget_status == "UNKNOWN"
        assert rows(db) == before
