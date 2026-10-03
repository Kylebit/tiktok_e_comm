from copy import deepcopy
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform.product_publication_runs import ProductPublicationRunStore
from shared_platform.shopee_recovery_run_reconciliations import (
    ShopeeRecoveryRunReconciliationError,
    ShopeeRecoveryRunReconciliationStore,
    validate_stored_recovery_run_receipt,
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def add_digest(value, field):
    result = deepcopy(value)
    result[field] = digest(result)
    return result


IDS = {
    "shopee:PH": ("10", "20", "30", "NORMAL", "478", "PHP"),
    "shopee:MY": ("11", "21", "31", "UNLIST", "37", "MYR"),
    "shopee:TH": ("12", "22", "32", "UNLIST", "294", "THB"),
    "shopee:VN": ("13", "23", "33", "UNLIST", "336000", "VND"),
}
RECOVERY = ("shopee:MY", "shopee:TH", "shopee:VN")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value).encode()
    path.write_bytes(raw)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def make_manifest():
    targets = []
    for label in RECOVERY:
        shop, item, model, _, price, currency = IDS[label]
        targets.append({
            "target_label": label, "region": label[-2:], "shop_id": shop,
            "item_id": item, "model_id": model, "model_sku": "0988",
            "global_item_id": "40", "global_model_id": "50", "category_id": "101157",
            "local_original_price": {"amount": price, "currency": currency},
            "approved_copy": {"title": "new " + label, "description": "new description"},
            "approved_image_route": {"existing_media_ids": ["image-a", "image-b"]},
        })
    value = {
        "schema_version": "shopee-regional-recovery/v1", "status": "READY_ZERO_WRITE_PREFLIGHT",
        "offer_id": "3956742887", "product_revision": 5, "plan_id": "plan",
        "execution_snapshot_digest": "sha256:" + "a" * 64,
        "target_labels": list(RECOVERY), "targets": targets,
        "global_item_id": "40", "global_model_id": "50",
    }
    value["prior_run_id"] = "domain-predecessor"
    value["prior_report_digest"] = digest(domain_report(value))
    return value


def domain_report(manifest):
    return {
        "schema_version": "product-publication-report/v2",
        "report_id": "publication-report:domain-predecessor", "run_id": "domain-predecessor",
        "offer_id": manifest["offer_id"], "revision": manifest["product_revision"],
        "plan_id": manifest["plan_id"], "snapshot": {"digest": manifest["execution_snapshot_digest"]},
        "status": "PARTIAL", "targets": [
            {"target_label": "shopee:PH", "status": "PUBLISHED"},
            *({"target_label": label, "status": "FAILED"} for label in RECOVERY),
        ],
    }


def create_old_sources(tmp_path, manifest):
    old_time = "2026-09-12T00:00:00+00:00"
    published, ph_targets, regional_targets = [], [], []
    for label, (shop, item, model, status, price, currency) in IDS.items():
        published.append({"shop_region": label[-2:], "shop_id": shop, "item_id": item,
                          "item_status": 1 if status == "NORMAL" else 8})
        ph_targets.append({"target_label": label, "complete": True,
            "global_linkage_exact": True, "shop_id": shop, "item_id": item,
            "resolved_global_item_id": "40", "item_status": status,
            "models": [{"model_id": model, "model_sku": "0988", "model_status": "MODEL_NORMAL",
                        "price_info": [{"original_price": price, "currency": currency}]}],
            "observed_at": old_time})
        if label in RECOVERY:
            regional_targets.append({"target_label": label, "category_id": "101157",
                "title": "old " + label, "description": "old description",
                "description_type": "normal", "gallery_image_ids": ["old-image"],
                "description_image_ids": [], "observed_at": old_time})
    docs = {
        "global_readback": {"schema_version": "shopee-global-published-list-readback/v1",
            "product_writes": 0, "observed_at": old_time,
            "response": {"response": {"published_item": published}}},
        "global_model_readback": {"schema_version": "shopee-global-model-readback/v1",
            "product_writes": 0, "observed_at": old_time,
            "models": [{"global_model_id": "50", "global_model_sku": "0988",
                        "price_info": {"original_price": "56.4", "currency": "CNY"}}]},
        "regional_readback": {"schema_version": "shopee-regional-recovery-preflight/v1",
            "product_writes": 0, "observed_at": old_time, "targets": regional_targets},
    }
    paths, hashes = {}, {}
    for name, doc in docs.items():
        path = tmp_path / "old" / (name + ".json")
        hashes[name] = write_json(path, doc)
        paths[name] = str(path.resolve())
    manifest["source_file_paths"], manifest["source_file_sha256"] = paths, hashes
    ph = add_digest({"schema_version": "shopee-successor-regional-item-readback/v1",
                     "product_writes": 0, "observed_at": old_time, "targets": ph_targets},
                    "evidence_digest")
    ph_path = tmp_path / "old" / "ph.json"
    write_json(ph_path, ph)
    global_identity = {"schema_version": "shopee-successor-official-readback/v1",
        "product_writes": 0, "started_at": old_time,
        "global": {"complete": True, "global_item_id": "40", "item_status": "NORMAL"}}
    global_path = tmp_path / "old" / "global-identity.json"
    write_json(global_path, global_identity)
    return ph_path, global_path


def make_fresh(tmp_path, manifest, run_id, report_id, *, top_run_id=None,
               observed="2026-09-14T00:00:00+00:00"):
    tops = []
    for group_index, labels in enumerate((RECOVERY, ("shopee:PH",))):
        targets = []
        for label in labels:
            shop, item, model, status, price, currency = IDS[label]
            query = {"method": "GET", "mode": "DIRECT_ID", "shop_id": shop, "item_id": item,
                     "model_id": model, "model_sku": "0988", "global_item_id": "40",
                     "global_model_id": "50"}
            responses = {
                "item": {"shop_id": shop, "item_id": item, "status": status,
                    "category_id": "101157", "title": "old " + label,
                    "description": "old description", "description_type": "normal",
                    "gallery_image_ids": ["old-image"], "description_image_ids": []},
                "models": [{"model_id": model, "model_sku": "0988", "status": "MODEL_NORMAL",
                            "price": price, "currency": currency}],
                "global_linkage": {"global_item_id": "40"},
                "global_item": {"global_item_id": "40", "status": "NORMAL"},
                "global_models": [{"global_item_id": "40", "global_model_id": "50",
                                   "model_sku": "0988", "price": "56.4", "currency": "CNY"}],
            }
            reads = {}
            for resource, response in responses.items():
                envelope = {"schema_version": "shopee-official-direct-id-get-evidence/v1",
                    "request": {**query, "resource": resource}, "complete": True,
                    "observed_at": observed, "raw_response": response}
                raw_path = tmp_path / "fresh" / "raw" / f"{label[-2:]}-{resource}.json"
                raw_sha = write_json(raw_path, envelope)
                reads[resource] = {"complete": True, "response_ref": str(raw_path.resolve()),
                                   "response_digest": raw_sha, "observed_at": observed}
            targets.append(add_digest({"target_label": label, "query": query, "reads": reads},
                                      "target_evidence_digest"))
        top = add_digest({"schema_version": "shopee-recovery-official-direct-id-readback/v1",
            "authority": "SHOPEE_OFFICIAL_API", "manifest_digest": manifest["manifest_digest"],
            "run_id": top_run_id or run_id, "report_id": report_id,
            "observed_at": observed, "product_writes": 0, "targets": targets}, "evidence_digest")
        top_path = tmp_path / "fresh" / f"{group_index}.json"
        write_json(top_path, top)
        tops.append(top_path)
    return tops


def setup(tmp_path, monkeypatch):
    manifest = make_manifest()
    ph_path, global_path = create_old_sources(tmp_path, manifest)
    manifest = add_digest(manifest, "manifest_digest")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest)
    runs = ProductPublicationRunStore(tmp_path / "runs.db")
    predecessor = runs.create_run(run_id="domain-predecessor", offer_id=manifest["offer_id"],
        revision=manifest["product_revision"], plan_id=manifest["plan_id"],
        snapshot_digest=manifest["execution_snapshot_digest"], platform_scope=["SHOPEE"], target_count=4,
        execution_identity={"skill_digest": "8" * 64, "git_commit": "b" * 40, "code_digest": "5" * 64})
    runs.mark_running(run_id=predecessor.run_id)
    runs.mark_completed(run_id=predecessor.run_id, final_report_id=predecessor.report_id)
    created = runs.create_run(run_id="recovery-failed", offer_id=manifest["offer_id"],
        revision=manifest["product_revision"], plan_id=manifest["plan_id"],
        snapshot_digest=manifest["execution_snapshot_digest"], platform_scope=["SHOPEE"], target_count=3,
        execution_identity={"skill_digest": "9" * 64, "git_commit": "c" * 40, "code_digest": "6" * 64},
        request_identity={"kind": "SHOPEE_RECOVERY", "authority_digest": manifest["manifest_digest"]},
        approved_request_guard=lambda _: None)
    runs.mark_failed(run_id=created.run_id, failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    run = runs.get_run_by_id(run_id=created.run_id)
    observed = (
        datetime.fromisoformat(run["updated_at"]) + timedelta(seconds=1)
    ).isoformat()
    tops = make_fresh(
        tmp_path,
        manifest,
        run["run_id"],
        run["report_id"],
        observed=observed,
    )
    source_refs = {"old_manifest_path": str(manifest_path.resolve()),
        "prior_ph_readback_path": str(ph_path.resolve()),
        "prior_global_identity_path": str(global_path.resolve()),
        "fresh_readback_paths": [str(path.resolve()) for path in tops]}
    import shared_platform.shopee_regional_recovery as recovery
    monkeypatch.setattr(recovery, "validate_recovery_manifest", lambda value, **_: deepcopy(value))
    validation = {"allowed_evidence_roots": [tmp_path], "snapshot": {"s": 1},
                  "candidate": {"c": 1}, "approval": {"a": 1}}
    kwargs = {"run_id": created.run_id, "source_refs": source_refs,
              "registered_by": "independent-review", **validation}
    return runs, run, manifest, kwargs, validation


def test_register_rebuilds_raw_authority_and_replays(tmp_path, monkeypatch):
    runs, _, manifest, kwargs, validation = setup(tmp_path, monkeypatch)
    store = ShopeeRecoveryRunReconciliationStore(runs.path)
    receipt = store.register(**kwargs)
    assert receipt["run_identity"]["request_identity"]["authority_digest"] == manifest["manifest_digest"]
    assert receipt["provider_request_attempted"] is False and receipt["external_write_count"] == 0
    assert receipt["fresh_readback"]["global"]["model_status"] == ""
    assert receipt["fresh_readback"]["global"]["model_presence"] == "EXACT"
    assert len(receipt["fresh_readback"]["source_files"]) == 22
    assert store.register(**kwargs) == receipt
    assert validate_stored_recovery_run_receipt(receipt, run_store_path=runs.path, **validation) == receipt


def test_manual_baselines_and_fake_manifest_digest_are_not_authority(tmp_path, monkeypatch):
    runs, _, _, kwargs, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(TypeError):
        ShopeeRecoveryRunReconciliationStore(runs.path).register(
            **kwargs, prior_baseline={"fabricated": True}, fresh_readback={"fabricated": True})
    manifest_path = Path(kwargs["source_refs"]["old_manifest_path"])
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["source_file_sha256"]["global_readback"] = "sha256:" + "0" * 64
    value.pop("manifest_digest")
    value["manifest_digest"] = digest(value)
    write_json(manifest_path, value)
    with pytest.raises(ValueError, match="bytes digest|request identity"):
        ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)


def test_cross_attempt_and_path_role_swap_are_rejected(tmp_path, monkeypatch):
    runs, run, manifest, kwargs, _ = setup(tmp_path, monkeypatch)
    wrong = make_fresh(tmp_path / "other", manifest, run["run_id"], run["report_id"], top_run_id="other-run")
    bad = deepcopy(kwargs)
    bad["source_refs"]["fresh_readback_paths"] = [str(path.resolve()) for path in wrong]
    with pytest.raises(ValueError, match="identity"):
        ShopeeRecoveryRunReconciliationStore(runs.path).register(**bad)
    swapped = deepcopy(kwargs)
    swapped["source_refs"]["fresh_readback_paths"].reverse()
    with pytest.raises(ValueError, match="source roles"):
        ShopeeRecoveryRunReconciliationStore(runs.path).register(**swapped)


def test_source_outside_allowed_roots_is_rejected(tmp_path, monkeypatch):
    runs, _, _, kwargs, _ = setup(tmp_path, monkeypatch)
    kwargs["allowed_evidence_roots"] = [tmp_path / "unrelated"]
    with pytest.raises(ValueError, match="outside allowed"):
        ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)


def test_resigned_provider_drift_still_fails_semantic_baseline(tmp_path, monkeypatch):
    runs, _, _, kwargs, _ = setup(tmp_path, monkeypatch)
    top_path = Path(kwargs["source_refs"]["fresh_readback_paths"][0])
    top = json.loads(top_path.read_text(encoding="utf-8"))
    top.pop("evidence_digest")
    target = top["targets"][0]
    target.pop("target_evidence_digest")
    item_meta = target["reads"]["item"]
    raw_path = Path(item_meta["response_ref"])
    envelope = json.loads(raw_path.read_text(encoding="utf-8"))
    envelope["raw_response"]["status"] = "NORMAL"
    item_meta["response_digest"] = write_json(raw_path, envelope)
    target["target_evidence_digest"] = digest(target)
    top["evidence_digest"] = digest(top)
    write_json(top_path, top)
    with pytest.raises(ValueError, match="required state"):
        ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)


def test_every_fresh_observation_must_postdate_failure(tmp_path, monkeypatch):
    runs, run, manifest, kwargs, _ = setup(tmp_path, monkeypatch)
    early = make_fresh(tmp_path / "early", manifest, run["run_id"], run["report_id"],
                       observed="2026-09-12T00:00:00+00:00")
    kwargs["source_refs"]["fresh_readback_paths"] = [str(path.resolve()) for path in early]
    with pytest.raises(ValueError, match="postdate"):
        ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)


def test_raw_tamper_is_rejected_on_get(tmp_path, monkeypatch):
    runs, _, _, kwargs, validation = setup(tmp_path, monkeypatch)
    store = ShopeeRecoveryRunReconciliationStore(runs.path)
    receipt = store.register(**kwargs)
    raw_path = Path(receipt["fresh_readback"]["source_files"][-1]["path"])
    raw_path.write_bytes(raw_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="bytes digest"):
        store.get(run_id="recovery-failed", **validation)


def test_self_hashed_database_tamper_is_rejected_by_raw_rebuild(tmp_path, monkeypatch):
    runs, _, _, kwargs, validation = setup(tmp_path, monkeypatch)
    store = ShopeeRecoveryRunReconciliationStore(runs.path)
    receipt = store.register(**kwargs)
    receipt["fresh_readback"]["regions"][0]["item_id"] = "999"
    receipt.pop("receipt_digest")
    receipt["receipt_digest"] = digest(receipt)
    with sqlite3.connect(runs.path) as conn:
        conn.execute("UPDATE shopee_recovery_run_zero_write_reconciliations SET receipt_json=?,receipt_digest=?",
                     (canonical(receipt), receipt["receipt_digest"]))
    with pytest.raises(ShopeeRecoveryRunReconciliationError, match="facts conflict"):
        store.get(run_id="recovery-failed", **validation)


def claim_args(runs, manifest, receipt, validation):
    prepared = SimpleNamespace(offer_id="3956742887", revision=5, plan_id="plan",
        snapshot_digest="sha256:" + "a" * 64, platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": RECOVERY})
    return dict(prepared=prepared, platform="SHOPEE",
        execution_identity=receipt["run_identity"]["execution_identity"], run_store=runs,
        report_store=SimpleNamespace(get_report_by_run=lambda **query:
            domain_report(manifest) if query.get("run_id") == "domain-predecessor" else None),
        release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
        retry_of_run_id="recovery-failed", recovery_manifest_digest=manifest["manifest_digest"],
        recovery_zero_write_receipt=receipt, recovery_zero_write_validation=validation)


def add_old_snapshot_run(runs, *, run_id, state="PUBLISHED"):
    created = runs.create_run(run_id=run_id, offer_id="3956742887", revision=4,
        plan_id="old-plan", snapshot_digest="sha256:" + "b" * 64,
        platform_scope=["SHOPEE"], target_count=1,
        execution_identity={"skill_digest": "7" * 64, "git_commit": "a" * 40, "code_digest": "4" * 64},
        approved_request_guard=lambda _: None)
    runs.mark_running(run_id=created.run_id)
    runs.mark_completed(run_id=created.run_id, final_report_id=created.report_id)
    return {"report_id": created.report_id, "run_id": created.run_id, "offer_id": "3956742887",
        "revision": 4, "plan_id": "old-plan", "snapshot": {"digest": "sha256:" + "b" * 64},
        "status": "COMPLETED", "targets": [{"target_label": "shopee:MY", "status": state}]}


def test_claim_binds_retry_receipt_and_replays_one_successor(tmp_path, monkeypatch):
    runs, _, manifest, kwargs, validation = setup(tmp_path, monkeypatch)
    receipt = ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)
    from shared_platform.product_publication_runner import claim_product_publication_request
    arguments = claim_args(runs, manifest, receipt, validation)
    first, second = claim_product_publication_request(**arguments), claim_product_publication_request(**arguments)
    assert first.created is True and second.created is False and first.run_id == second.run_id
    assert runs.get_run_by_id(run_id=first.run_id)["request_identity"] == {
        "kind": "SHOPEE_RECOVERY_RETRY", "authority_digest": manifest["manifest_digest"],
        "reconciliation_receipt_digest": receipt["receipt_digest"], "retry_of_run_id": "recovery-failed"}


def test_domain_predecessor_requires_exact_manifest_report_digest(tmp_path, monkeypatch):
    runs, _, manifest, kwargs, validation = setup(tmp_path, monkeypatch)
    receipt = ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)
    from shared_platform.product_publication_runner import claim_product_publication_request
    arguments = claim_args(runs, manifest, receipt, validation)
    bad_report = domain_report(manifest)
    bad_report["targets"][1]["status"] = "PUBLISHED"
    arguments["report_store"] = SimpleNamespace(get_report_by_run=lambda **query:
        bad_report if query.get("run_id") == "domain-predecessor" else None)
    with pytest.raises(ValueError, match="domain predecessor report"):
        claim_product_publication_request(**arguments)


def test_non_exact_old_snapshot_completed_history_uses_ordinary_guard(tmp_path, monkeypatch):
    runs, _, manifest, kwargs, validation = setup(tmp_path, monkeypatch)
    receipt = ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)
    old_report = add_old_snapshot_run(runs, run_id="old-completed")
    arguments = claim_args(runs, manifest, receipt, validation)
    arguments["report_store"] = SimpleNamespace(get_report_by_run=lambda **query:
        domain_report(manifest) if query.get("run_id") == "domain-predecessor"
        else old_report if query.get("run_id") == "old-completed" else None)
    from shared_platform.product_publication_runner import claim_product_publication_request
    assert claim_product_publication_request(**arguments).created is True


def test_non_exact_old_snapshot_processing_history_still_blocks(tmp_path, monkeypatch):
    runs, _, manifest, kwargs, validation = setup(tmp_path, monkeypatch)
    receipt = ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)
    old_report = add_old_snapshot_run(runs, run_id="old-processing", state="PROCESSING")
    arguments = claim_args(runs, manifest, receipt, validation)
    arguments["report_store"] = SimpleNamespace(get_report_by_run=lambda **query:
        domain_report(manifest) if query.get("run_id") == "domain-predecessor"
        else old_report if query.get("run_id") == "old-processing" else None)
    from shared_platform.product_publication_runner import claim_product_publication_request
    with pytest.raises(ValueError, match="overlapping publication target"):
        claim_product_publication_request(**arguments)


def test_non_exact_old_snapshot_failed_history_needs_ordinary_reconciliation(tmp_path, monkeypatch):
    runs, _, manifest, kwargs, validation = setup(tmp_path, monkeypatch)
    receipt = ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)
    old = runs.create_run(run_id="old-failed", offer_id="3956742887", revision=4,
        plan_id="old-plan", snapshot_digest="sha256:" + "b" * 64,
        platform_scope=["SHOPEE"], target_count=1,
        execution_identity={"skill_digest": "7" * 64, "git_commit": "a" * 40, "code_digest": "4" * 64},
        approved_request_guard=lambda _: None)
    runs.mark_failed(run_id=old.run_id, failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    from shared_platform.product_publication_runner import claim_product_publication_request
    with pytest.raises(ValueError, match="prior publication snapshot requires reconciliation"):
        claim_product_publication_request(**claim_args(runs, manifest, receipt, validation))


def test_newer_exact_failed_run_blocks_retry(tmp_path, monkeypatch):
    runs, _, manifest, kwargs, validation = setup(tmp_path, monkeypatch)
    receipt = ShopeeRecoveryRunReconciliationStore(runs.path).register(**kwargs)
    newer = runs.create_run(run_id="newer", offer_id="3956742887", revision=5, plan_id="plan",
        snapshot_digest="sha256:" + "a" * 64, platform_scope=["SHOPEE"], target_count=3,
        execution_identity=receipt["run_identity"]["execution_identity"],
        request_identity={"kind": "SHOPEE_RECOVERY", "authority_digest": manifest["manifest_digest"]})
    runs.mark_failed(run_id=newer.run_id, failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    from shared_platform.product_publication_runner import claim_product_publication_request
    with pytest.raises(ValueError, match="latest"):
        claim_product_publication_request(**claim_args(runs, manifest, receipt, validation))
