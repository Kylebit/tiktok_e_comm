"""Actual domain compiler and retained R1/R2; all COMMON I/O is synthetic.

No HTTP server, browser, real provider, worker or formal database is used.
The fixture's official-shaped readback proves compiler shape, not official fact.
"""
from copy import deepcopy
import json

import pytest

from shared_platform.r3_frozen_review_producer import (
    DomainFrozenReviewProducer, DomainReviewBlocked, rebuild_domain_review_graph,
)
from shared_platform.release_store import preview_release_plan


@pytest.fixture
def full_sources(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace
    documents, _dashboard, store, _data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    binding = market["plan"]["payload"]["r3_marketplace_binding"]
    common_plan = store.get_plan(binding["common_plan_id"])
    common_run = store.get_run(binding["common_run_id"])
    assert io.mutations == 0  # Historical normalized seed, no current transport write.
    assert documents["round1_snapshot"]["canonical_targets"] == ["tiktok:LH_PH", "ozon:RU"]
    assert len(documents["round1_snapshot"]["fact_snapshot"]["product_facts"]["skus"]) == 2
    return common_plan, common_run, market["plan"]


def test_complete_domain_graph_is_distinct_from_common_and_preserves_display(full_sources):
    common, run, market = full_sources
    before = deepcopy(full_sources)
    graph = rebuild_domain_review_graph(common, run, market)
    assert graph.marketplace_plan_id != graph.common_plan_id
    assert graph.marketplace_plan_id == market["plan_id"]
    assert graph.common_plan_id == common["plan_id"]
    assert graph.common_run_id == run["run_id"]
    assert graph.targets == ("tiktok:LH_PH", "ozon:RU")
    display = json.loads(graph.display_bytes)
    manifest = json.loads(graph.manifest_bytes)
    assert display["review_manifest"] == manifest
    assert display["target_labels"] == list(graph.targets)
    assert len(manifest["variants"]) == 2
    assert [row["target_label"] for row in manifest["targets"]] == list(graph.targets)
    assert manifest["copy_sets"] and manifest["image_sets"]
    assert graph.candidate_digest == display["candidate_digest"]
    assert full_sources == before
    assert not hasattr(graph, "execution_authority")


def test_default_service_cannot_infer_real_budget_from_a_complete_candidate(full_sources):
    with pytest.raises(DomainReviewBlocked, match="COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED"):
        DomainFrozenReviewProducer().build(None, "retained-common", full_sources[2]["plan_id"])


@pytest.mark.parametrize("drift", ["same_common_plan", "target_subset", "readback", "r2_qa", "title", "price", "review_manifest"])
def test_recomputed_display_checksums_do_not_accept_source_or_content_drift(full_sources, drift):
    common, run, market = deepcopy(full_sources)
    payload = market["payload"]
    binding = payload["r3_marketplace_binding"]
    if drift == "same_common_plan":
        market["plan_id"] = common["plan_id"]
    elif drift == "target_subset":
        payload["targets"] = payload["targets"][:1]
        market["targets"] = payload["targets"]
    elif drift == "readback":
        binding["common_readback"]["evidence"]["verified"] = False
    elif drift == "r2_qa":
        binding["documents"]["image_qa"]["qa_digest"] = "sha256:" + "f" * 64
    elif drift == "title":
        payload["product_facts"]["title"] = "changed after display"
    elif drift == "price":
        label = payload["targets"][0]
        payload["pricing"]["selected_targets"][label]["sku_prices"][0]["amount"] = "900"
    else:
        binding["reviewed_candidate_facts"]["review_manifest"]["product"]["title"] = "forged display"
    # Valid JSON and a fresh ordinary plan checksum must not replace lineage.
    if drift != "same_common_plan":
        rechecksummed = preview_release_plan(payload)
        market["payload_digest"] = rechecksummed["payload_digest"]
    with pytest.raises(DomainReviewBlocked) as blocked:
        rebuild_domain_review_graph(common, run, market)
    assert "NOT_IMPLEMENTED" not in str(blocked.value)


@pytest.mark.parametrize("missing", ["generation_result", "translation_result", "image_qa", "round1_snapshot"])
def test_all_predecessor_reports_are_required(full_sources, missing):
    common, run, market = deepcopy(full_sources)
    market["payload"]["r3_marketplace_binding"]["documents"].pop(missing)
    with pytest.raises(DomainReviewBlocked) as blocked:
        rebuild_domain_review_graph(common, run, market)
    assert "NOT_IMPLEMENTED" not in str(blocked.value)


def test_stored_domain_diagnostic_preserves_bytes_and_remains_blocked(full_sources, tmp_path):
    import sqlite3
    from shared_platform.release_store import ReleaseStore
    common, run, market = full_sources
    store = ReleaseStore(tmp_path / "release.db")
    store.create_plan(market["payload"])
    with sqlite3.connect(f"file:{store.path.as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        before = {row[0]: db.execute('SELECT * FROM "' + row[0] + '"').fetchall()
                  for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = DomainFrozenReviewProducer().inspect(db, "unadmitted-common-reservation", market["plan_id"])
        after = {row[0]: db.execute('SELECT * FROM "' + row[0] + '"').fetchall()
                 for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert before == after
    assert result["status"] == "BLOCKED"
    assert result["final_review_available"] is False
    assert result["execution_authority"] is False
    assert "COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN" in result["blockers"]
    assert result["candidate_digest"] == json.loads(
        rebuild_domain_review_graph(common, run, market).display_bytes)["candidate_digest"]
    assert result["manifest"]["variants"]
    assert not {"nonce", "review_digest", "approval_saved"} & set(result)
