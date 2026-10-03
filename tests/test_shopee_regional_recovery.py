from copy import deepcopy
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
import shared_platform.operations_domain_guard as domain_guard

from shared_platform.product_publication_runner import PublicationPlatformRequest, _restrictive_write_budget
from shared_platform.shopee_regional_recovery import (
    ShopeeRegionalRecoveryError,
    _digest,
    _portable_source_identity,
    _require_global_model_exact,
    _trusted_legacy_builder_source,
    build_recovery_executor,
    build_recovery_manifest,
    validate_recovery_manifest,
)
from shared_platform.shopee_recovery_coordination import collect_direct_id_readback
from shared_platform.shopee_recovery_reconciliations import build_reconciliation_receipt


LABEL = "shopee:MY"
D64 = "a" * 64


def test_reportless_global_model_accepts_only_unobservable_status():
    fact = {"global_item_id": "555", "global_model_id": "66", "model_sku": "0988"}
    _require_global_model_exact(
        [{"global_model_id": "66", "global_model_sku": "0988"}], fact,
        allow_unobservable_status=True,
    )
    with pytest.raises(ShopeeRegionalRecoveryError, match="status drifted"):
        _require_global_model_exact(
            [{"global_model_id": "66", "global_model_sku": "0988"}], fact,
        )
    for drift in (
        {"global_model_id": "67", "global_model_sku": "0988"},
        {"global_model_id": "66", "global_model_sku": "0999"},
        {"global_model_id": "66", "global_model_sku": "0988", "status": "DELETED"},
    ):
        with pytest.raises(ShopeeRegionalRecoveryError):
            _require_global_model_exact([drift], fact, allow_unobservable_status=True)


def test_builder_source_identity_is_newline_portable_and_detects_code_drift():
    lf = b"print('same')\nreturn_value = 1\n"
    crlf = lf.replace(b"\n", b"\r\n")
    assert _portable_source_identity(lf) == _portable_source_identity(crlf)
    assert _portable_source_identity(lf)["algorithm"] == "sha256-normalized-lf/v1"
    assert _portable_source_identity(lf)["repo_relative_path"].endswith(
        "build_shopee_existing_media_binding.py"
    )
    assert _portable_source_identity(lf) != _portable_source_identity(
        lf.replace(b"return_value = 1", b"return_value = 2")
    )


def test_legacy_builder_source_is_bound_to_exact_git_history():
    root = Path(__file__).parents[1]
    assert _trusted_legacy_builder_source(repo_root=root)
    assert not _trusted_legacy_builder_source(repo_root=root, commit="0" * 40)
    assert not _trusted_legacy_builder_source(repo_root=root, relative_path="README.md")
    assert not _trusted_legacy_builder_source(repo_root=root, blob_id="0" * 40)
    assert not _trusted_legacy_builder_source(
        repo_root=root, expected_sha256="sha256:" + "0" * 64
    )


def _facts(tmp_path):
    candidate = {
        "schema_version": "publication-release-candidate/v1", "status": "READY_FOR_FINAL_REVIEW",
        "offer_id": "3956742887", "product_revision": 5, "plan_id": "plan",
        "snapshot_digest": "sha256:" + "b" * 64, "candidate_digest": D64,
        "target_labels": [LABEL],
        "review_manifest": {
            "targets": [{"target_label": LABEL, "copy_set_id": "copy", "image_set_id": "images",
                         "category": {"id": "101157"},
                         "prices": [{"model_sku": "0988", "amount": "37", "currency": "MYR"}]}],
            "copy_sets": [{"copy_set_id": "copy", "title": "Tajuk", "description": "Huraian"}],
            "image_sets": [{"image_set_id": "images", "images": [{"url": "https://a"}, {"url": "https://b"}]}],
        },
    }
    approval = {"schema_version": "final-marketplace-approval/v1", "status": "APPROVED",
                "approval_digest": "c" * 64, "candidate_digest": D64,
                "snapshot_digest": candidate["snapshot_digest"], "offer_id": candidate["offer_id"],
                "plan_id": "plan", "target_labels": [LABEL]}
    execution_digest = "sha256:" + "d" * 64
    report = {"schema_version": "product-publication-report/v2",
              "run_id": "product-center-shopee-30c4f4c7c06a92aadfbf15a3b5389531",
              "report_id": "publication-report:product-center-shopee-30c4f4c7c06a92aadfbf15a3b5389531",
              "offer_id": candidate["offer_id"], "plan_id": "plan",
              "revision": 5,
              "snapshot": {"digest": execution_digest}, "status": "PARTIAL",
              "release_authorization": {"candidate_digest": D64, "approval_digest": "c" * 64},
              "targets": [{"target_label": LABEL, "status": "FAILED"}],
              "mutation_budgets": [{"platform": "SHOPEE", "reservations": [
                  {"target_label": LABEL, "operation": "upload_regional_image"},
                  {"target_label": LABEL, "operation": "upload_regional_image"},
                  {"target_label": LABEL, "operation": "update_regional_images"},
              ]}]}
    global_rb = {"schema_version": "shopee-global-published-list-readback/v1", "product_writes": 0,
                 "global_item_id": "555", "response": {"response": {"published_item": [
                     {"shop_region": "MY", "shop_id": 22, "item_id": 33}]}}}
    regional = {"schema_version": "shopee-regional-recovery-preflight/v1", "product_writes": 0,
                "global_item_id": "555", "targets": [{"target_label": LABEL, "shop_id": 22,
                    "item_id": "33", "item_status": "UNLIST", "category_id": "101157",
                    "gallery_image_ids": ["i1", "i2"],
                    "gallery_image_urls": ["https://provider/i1", "https://provider/i2"],
                    "logistics": [{"logistic_id": 7, "enabled": True}],
                    "models": [{"model_id": "44", "model_sku": "0988",
                                "model_status": "MODEL_NORMAL",
                                "price_info": [{"currency": "MYR", "original_price": 37}]}]}]}
    regional["evidence_digest"] = _digest(regional)
    global_models = {"schema_version": "shopee-global-model-readback/v1", "product_writes": 0,
                     "global_item_id": "555", "models": [{"global_model_id": "66", "global_model_sku": "0988",
                                                            "status": "MODEL_NORMAL"}]}
    global_models["evidence_digest"] = _digest(global_models)
    builder_identity = _portable_source_identity((Path(__file__).parents[1]
            / "skills" / "publish-approved-product" / "scripts"
            / "build_shopee_existing_media_binding.py").read_bytes())
    media = {"schema_version": "shopee-existing-media-content-binding/v2",
                 "status": "VERIFIED_UNIQUE", "product_writes": 0,
                 "algorithm": "exif-rgb-contain-pad256-sha256+dhash32-bidirectional/v1",
                 "max_perceptual_distance": 0.08, "minimum_second_best_margin": 0.04,
             "builder_code_sha256": builder_identity["sha256"],
             "builder_source_identity": builder_identity,
             "candidate_digest": D64, "regional_readback_digest": regional["evidence_digest"],
             "prior_run_id": report["run_id"], "targets": [{"target_label": LABEL,
                 "item_id": "33", "ordered_source_urls": ["https://a", "https://b"],
                 "ordered_media_ids": ["i1", "i2"], "image_set_id": "images",
                 "route_digest": _digest({"ordered_urls": ["https://a", "https://b"]}),
                 "bindings": [{"source_url": source, "provider_media_id": media_id,
                                "provider_url": f"https://provider/{media_id}",
                                "match": "NORMALIZED_PIXEL_EXACT",
                                "source_sha256": "sha256:" + "1" * 64,
                                "provider_sha256": "sha256:" + "2" * 64,
                                "source_pixel_sha256": "sha256:" + "3" * 64,
                                "provider_pixel_sha256": "sha256:" + "3" * 64,
                                "source_dimensions": [100, 100],
                                "provider_dimensions": [100, 100],
                                "perceptual_distance": 0.0, "second_best_margin": 1.0,
                                "provider_second_best_margin": 1.0}
                               for source, media_id in zip(["https://a", "https://b"], ["i1", "i2"])]}]}
    media["evidence_digest"] = _digest(media)
    objects = {"prior_report": report, "global_readback": global_rb,
               "global_model_readback": global_models, "regional_readback": regional,
               "media_binding_receipt": media}
    source_files = {}
    for name, value in objects.items():
        path = tmp_path / f"{name}.json"
        path.write_text(__import__("json").dumps(value), encoding="utf-8")
        source_files[name] = path
    manifest = build_recovery_manifest(candidate=candidate, approval=approval, prior_report=report,
        global_readback=global_rb, global_model_readback=global_models, regional_readback=regional,
        source_files=source_files, media_binding_receipt=media,
        expected_prior_run_id=report["run_id"],
        allowed_source_roots=(tmp_path,),
        target_labels=(LABEL,))
    snapshot = {"offer_id": candidate["offer_id"], "product_revision": 5, "plan_id": "plan",
                "snapshot_digest": execution_digest}
    return candidate, approval, snapshot, manifest


def test_manifest_binds_authority_and_official_exact_ids(tmp_path):
    candidate, approval, snapshot, manifest = _facts(tmp_path)
    checked = validate_recovery_manifest(manifest, snapshot=snapshot, candidate=candidate,
                                         approval=approval, allowed_source_roots=(tmp_path,))
    assert checked["targets"][0]["item_id"] == "33"
    assert checked["targets"][0]["model_id"] == "44"
    assert checked["forbidden_operations"] == ["create_publish_task", "upload_image", "global_mutation"]
    drift = deepcopy(manifest); drift["targets"][0]["item_id"] = "34"
    with pytest.raises(ShopeeRegionalRecoveryError, match="digest"):
        validate_recovery_manifest(drift, snapshot=snapshot, candidate=candidate,
                                   approval=approval, allowed_source_roots=(tmp_path,))


@pytest.mark.parametrize("field,value", [
    ("item_id", "99"), ("model_id", "88"), ("shop_id", "77"),
    ("category_id", "999999"), ("model_sku", "tampered"),
    ("allowed_actions", ["list_existing_item"]),
])
def test_self_digested_target_tamper_is_rejected(tmp_path, field, value):
    candidate, approval, snapshot, manifest = _facts(tmp_path)
    drift = deepcopy(manifest); drift["targets"][0][field] = value
    body = deepcopy(drift); body.pop("manifest_digest")
    drift["manifest_digest"] = _digest(body)
    with pytest.raises(ShopeeRegionalRecoveryError, match="frozen evidence"):
        validate_recovery_manifest(drift, snapshot=snapshot, candidate=candidate,
                                   approval=approval, allowed_source_roots=(tmp_path,))


class Ledger:
    def __init__(self): self.calls = []
    def reserve_target(self, label, operation): self.calls.append((label, operation))


class Context:
    region = "MY"; shop_id = 22


class Runtime:
    def __init__(self):
        self.item = {"item_id": 33, "item_status": "UNLIST", "category_id": 101157,
                     "item_name": "English", "description": "English", "description_type": "normal",
                     "image": {"image_id_list": ["i1", "i2"]},
                     "logistic_info": [{"logistic_id": 7, "enabled": True}]}
        self.models = [{"model_id": 44, "model_sku": "0988",
                        "model_status": "MODEL_NORMAL",
                        "price_info": [{"currency": "MYR", "original_price": 37}]}]
        self.copy_calls = []; self.list_calls = []; self.record_calls = []
    def context(self, region): assert region == "MY"; return Context()
    def regional_item(self, context, item_id): assert item_id == "33"; return deepcopy(self.item)
    def regional_models(self, context, item_id): return deepcopy(self.models)
    def resolved_global_item_id(self, context, item_id): return "555"
    def global_models(self, context, global_item_id):
        assert global_item_id == "555"
        return [{"global_model_id": "66", "global_model_sku": "0988", "status": "MODEL_NORMAL"}]
    def update_regional_copy(self, context, item_id, *, title, description):
        self.copy_calls.append(item_id); self.item.update(item_name=title, description=description,
            description_type="extended", description_info={"extended_description": {"field_list": [
                {"field_type": "text", "text": description},
                {"field_type": "image", "image_info": {"image_id": "i1"}},
                {"field_type": "image", "image_info": {"image_id": "i2"}},
            ]}}); return {"external_write_count": 1}
    def update_regional_images(self, *args, **kwargs): raise AssertionError("not needed")
    def list_item(self, context, item_id): self.list_calls.append(item_id); self.item["item_status"] = "NORMAL"
    def record_verified_item(self, **kwargs): self.record_calls.append(kwargs)


def test_executor_only_repairs_existing_copy_media_then_lists(tmp_path):
    candidate, _approval, snapshot, manifest = _facts(tmp_path)
    runtime = Runtime(); ledger = Ledger()
    request = PublicationPlatformRequest(run_id="run", report_id="report", platform="SHOPEE",
        target_labels=(LABEL,), snapshot=snapshot, release_candidate=candidate,
        write_budget_ledger=ledger)
    result = build_recovery_executor(manifest=manifest, approval=_approval,
        allowed_source_roots=(tmp_path,), runtime=runtime)(request)
    assert result["targets"][0]["status"] == "PUBLISHED"
    assert result["external_write_count"] == 2
    assert runtime.copy_calls == ["33"] and runtime.list_calls == ["33"]
    assert runtime.record_calls[0]["model_id"] == "44"
    assert ledger.calls == [(LABEL, "update_copy_and_description_media"), (LABEL, "list_existing_item")]


@pytest.mark.parametrize("schema", [
    "shopee-reportless-recovery-execution-manifest/v1",
    "shopee-known-zero-recovery-execution-manifest/v1",
])
def test_reportless_executor_accepts_unobservable_global_model_status(tmp_path, schema):
    candidate, approval, snapshot, manifest = _facts(tmp_path)
    runtime = Runtime(); ledger = Ledger()
    runtime.global_models = lambda context, global_item_id: [
        {"global_model_id": "66", "global_model_sku": "0988"}
    ]
    checked = deepcopy(manifest)
    checked["schema_version"] = schema
    request = PublicationPlatformRequest(
        run_id="run", report_id="report", platform="SHOPEE", target_labels=(LABEL,),
        snapshot=snapshot, release_candidate=candidate, write_budget_ledger=ledger,
    )
    result = build_recovery_executor(
        manifest=manifest, approval=approval, allowed_source_roots=(tmp_path,), runtime=runtime,
        manifest_validator=lambda *args, **kwargs: deepcopy(checked),
    )(request)
    assert result["targets"][0]["status"] == "PUBLISHED"
    assert ledger.calls == [(LABEL, "update_copy_and_description_media"), (LABEL, "list_existing_item")]


class UnknownRuntime(Runtime):
    def update_regional_copy(self, *args, **kwargs): raise TimeoutError("secret token")


class PostWriteReadbackUnknownRuntime(Runtime):
    def __init__(self):
        super().__init__(); self.reads = 0
    def regional_item(self, context, item_id):
        self.reads += 1
        if self.copy_calls:
            raise TimeoutError("credential leaked")
        return super().regional_item(context, item_id)


def test_unknown_write_stops_before_listing_and_redacts_reason(tmp_path):
    candidate, _approval, snapshot, manifest = _facts(tmp_path)
    runtime = UnknownRuntime(); ledger = Ledger()
    request = PublicationPlatformRequest(run_id="run", report_id="report", platform="SHOPEE",
        target_labels=(LABEL,), snapshot=snapshot, release_candidate=candidate,
        write_budget_ledger=ledger)
    result = build_recovery_executor(manifest=manifest, approval=_approval,
        allowed_source_roots=(tmp_path,), runtime=runtime)(request)
    row = result["targets"][0]
    assert row["status"] == "PROCESSING" and row["evidence"]["outcome_unknown"] is True
    assert runtime.list_calls == [] and result["external_write_count"] is None
    assert "secret" not in row["evidence"]["provider_reason"].lower()


def test_successful_write_then_readback_exception_is_unknown(tmp_path):
    candidate, approval, snapshot, manifest = _facts(tmp_path)
    runtime = PostWriteReadbackUnknownRuntime(); ledger = Ledger()
    request = PublicationPlatformRequest(run_id="run", report_id="report", platform="SHOPEE",
        target_labels=(LABEL,), snapshot=snapshot, release_candidate=candidate,
        write_budget_ledger=ledger)
    result = build_recovery_executor(manifest=manifest, approval=approval,
        allowed_source_roots=(tmp_path,), runtime=runtime)(request)
    row = result["targets"][0]
    assert row["status"] == "PROCESSING"
    assert row["evidence"]["outcome_unknown"] is True
    assert result["external_write_count"] is None and runtime.list_calls == []


def test_recovery_budget_can_only_narrow_approved_limits():
    approved = {"shared_maximum": 9, "per_target_maximum": 9}
    labels = ["shopee:MY", "shopee:TH", "shopee:VN"]
    assert _restrictive_write_budget({"shared_maximum": 0, "per_target_maximum": 3,
        "target_labels": labels}, approved, labels)["per_target_maximum"] == 3
    with pytest.raises(ValueError, match="narrow"):
        _restrictive_write_budget({"shared_maximum": 10, "per_target_maximum": 3,
            "target_labels": labels}, approved, labels)
    with pytest.raises(ValueError, match="narrow"):
        _restrictive_write_budget({"shared_maximum": 0, "per_target_maximum": 3,
            "target_labels": ["shopee:PH"]}, approved, labels)


def test_operations_guard_locks_only_exact_recovery_targets(monkeypatch, tmp_path):
    class Engine:
        def dashboard(self): return {"tasks": []}
        def begin_domain_operation(self, operation, *, skus, shops, owner_task_id):
            self.shops = shops
            return {"acquired": True}
    class Store:
        def approved_publication_snapshot(self, **kwargs):
            return {"plan_id": "plan", "skus": [{"seller_sku": "0988"}],
                    "publication_targets": [{"target_label": label} for label in
                        ("shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN")]}
    engine = Engine(); monkeypatch.setattr(domain_guard, "engine_for", lambda root: engine)
    domain_guard.begin_snapshot_publication(Store(), "offer", "sha256:" + "a" * 64,
        "SHOPEE", tmp_path, target_scope=("shopee:MY", "shopee:TH", "shopee:VN"))
    assert engine.shops == ["shopee:MY", "shopee:TH", "shopee:VN"]


def _continuation_facts(tmp_path):
    (tmp_path / "old").mkdir(parents=True)
    candidate, approval, snapshot, old_manifest = _facts(tmp_path / "old")
    attempt = {
        "schema_version": "product-publication-report/v2", "run_id": "recovery-attempt",
        "report_id": "publication-report:recovery-attempt", "offer_id": candidate["offer_id"],
        "revision": 5, "plan_id": "plan", "snapshot": {"digest": snapshot["snapshot_digest"]},
        "status": "PROCESSING", "updated_at": "2026-09-13T02:00:00+00:00",
        "release_authorization": {"candidate_digest": candidate["candidate_digest"],
                                  "approval_digest": approval["approval_digest"]},
        "recovery_authorization": deepcopy(old_manifest),
        "mutation_budgets": [{"platform": "SHOPEE", "reservations": [], "attempts": {
            "per_target": {LABEL: 1}}}],
        "targets": [{"target_label": LABEL, "status": "PROCESSING", "evidence": {
            "request_attempted": True, "outcome_unknown": True, "external_write_count": None}}],
    }
    sources = tmp_path / "sources"; sources.mkdir(parents=True)
    attempt_path = sources / "attempt.json"; attempt_path.write_text(json.dumps(attempt), encoding="utf-8")
    old_path = sources / "old-manifest.json"; old_path.write_text(json.dumps(old_manifest), encoding="utf-8")
    frozen = old_manifest["targets"][0]

    def getter(resource, query):
        values = {
            "item": {"shop_id": frozen["shop_id"], "item_id": frozen["item_id"], "status": "UNLIST",
                     "category_id": frozen["category_id"], "title": frozen["approved_copy"]["title"],
                     "description": frozen["approved_copy"]["description"], "description_type": "extended",
                     "gallery_image_ids": frozen["approved_image_route"]["existing_media_ids"],
                     "description_image_ids": frozen["approved_image_route"]["existing_media_ids"],
                     "enabled_logistics_ids": frozen["enabled_logistics_ids"]},
            "models": [{"shop_id": frozen["shop_id"], "item_id": frozen["item_id"],
                        "model_id": frozen["model_id"], "model_sku": frozen["model_sku"],
                        "status": "MODEL_NORMAL", "price": frozen["local_original_price"]["amount"],
                        "currency": frozen["local_original_price"]["currency"]}],
            "global_linkage": {"shop_id": frozen["shop_id"], "item_id": frozen["item_id"],
                               "global_item_id": frozen["global_item_id"]},
            "global_item": {"global_item_id": frozen["global_item_id"], "status": "NORMAL"},
            "global_models": [{"global_item_id": frozen["global_item_id"],
                               "global_model_id": frozen["global_model_id"],
                               "model_sku": frozen["model_sku"], "status": "MODEL_NORMAL"}],
        }
        return deepcopy(values[resource])

    readback = collect_direct_id_readback(
        attempt=attempt, manifest=old_manifest, evidence_root=tmp_path / "gets",
        getter=getter, observed_at="2026-09-13T02:01:00+00:00",
        manifest_validator=lambda value: deepcopy(dict(value)), allowed_evidence_roots=[tmp_path])
    file_meta = lambda path: {"path": str(path), "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
    receipt = build_reconciliation_receipt(
        attempt=attempt, manifest=old_manifest, recovery_authorization=old_manifest,
        official_readback=readback,
        source_files={"attempt": file_meta(attempt_path), "manifest": file_meta(old_path)},
        allowed_evidence_roots=[tmp_path])
    assert receipt["result"] == "REMAINING_DIFF"
    loaded = {name: json.loads(Path(path).read_text(encoding="utf-8"))
              for name, path in old_manifest["source_file_paths"].items()}
    for name in ("global_readback", "global_model_readback", "regional_readback"):
        loaded[name]["observed_at"] = "2026-09-13T02:02:00+00:00"
        if "evidence_digest" in loaded[name]:
            loaded[name].pop("evidence_digest")
            loaded[name]["evidence_digest"] = _digest(loaded[name])
    loaded["media_binding_receipt"]["prior_run_id"] = attempt["run_id"]
    loaded["media_binding_receipt"]["regional_readback_digest"] = loaded["regional_readback"]["evidence_digest"]
    loaded["media_binding_receipt"].pop("evidence_digest")
    loaded["media_binding_receipt"]["evidence_digest"] = _digest(loaded["media_binding_receipt"])
    objects = {**loaded, "prior_report": attempt, "predecessor_attempt": attempt,
               "reconciliation_receipt": receipt}
    paths = {}
    for name, value in objects.items():
        path = sources / ("fresh-" + name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8"); paths[name] = path
    manifest = build_recovery_manifest(
        candidate=candidate, approval=approval, prior_report=attempt,
        global_readback=loaded["global_readback"], global_model_readback=loaded["global_model_readback"],
        regional_readback=loaded["regional_readback"], source_files=paths,
        media_binding_receipt=loaded["media_binding_receipt"], expected_prior_run_id=attempt["run_id"],
        allowed_source_roots=[tmp_path], target_labels=[LABEL],
        predecessor_attempt=attempt, reconciliation_receipt=receipt)
    return candidate, approval, snapshot, manifest, receipt


def test_continuation_manifest_binds_direct_predecessor_original_ancestor_and_exact_action(tmp_path):
    candidate, approval, snapshot, manifest, receipt = _continuation_facts(tmp_path)
    checked = validate_recovery_manifest(
        manifest, snapshot=snapshot, candidate=candidate, approval=approval,
        allowed_source_roots=[tmp_path])
    assert checked["prior_run_id"] == "recovery-attempt"
    assert checked["continuation"]["direct_predecessor"]["run_id"] == "recovery-attempt"
    assert checked["continuation"]["original_ancestor"]["run_id"].startswith("product-center-shopee-30c4")
    assert checked["continuation"]["receipt_digest"] == receipt["receipt_digest"]
    assert checked["targets"][0]["allowed_actions"] == ["list_existing_item"]
    assert checked["targets"][0]["mutation_budget"]["target_maximum"] == 1


@pytest.mark.parametrize("drift", ["copy", "image", "price", "approval"])
def test_continuation_cannot_reuse_receipt_after_approved_fact_drift(tmp_path, drift):
    candidate, approval, snapshot, manifest, _receipt = _continuation_facts(tmp_path)
    if drift == "copy":
        candidate["review_manifest"]["copy_sets"][0]["title"] = "changed"
    elif drift == "image":
        candidate["review_manifest"]["image_sets"][0]["images"][0]["url"] = "https://changed"
    elif drift == "price":
        candidate["review_manifest"]["targets"][0]["prices"][0]["amount"] = "99"
    else:
        approval["approval_digest"] = "9" * 64
    with pytest.raises(ShopeeRegionalRecoveryError):
        validate_recovery_manifest(manifest, snapshot=snapshot, candidate=candidate,
                                   approval=approval, allowed_source_roots=[tmp_path])


def test_reportless_continuation_keeps_original_authority_when_successor_is_pending(tmp_path):
    """A pending copy successor cannot replace the recovery manifest's authority.

    A reportless continuation reconstructs its request from the immutable recovery
    manifest.  Even a structurally complete successor with internally matching
    candidate/approval strings must be rejected when it does not match the
    candidate and approval frozen into that manifest and its predecessor report.
    """
    candidate, approval, snapshot, manifest, _receipt = _continuation_facts(tmp_path)
    original_copy = deepcopy(manifest["targets"][0]["approved_copy"])
    original_images = deepcopy(manifest["targets"][0]["approved_image_route"])
    original_price = deepcopy(manifest["targets"][0]["local_original_price"])

    pending = deepcopy(candidate)
    pending["candidate_digest"] = "e" * 64
    pending["review_manifest"]["copy_sets"][0].update(
        title="pending title", description="pending description"
    )
    pending["review_manifest"]["image_sets"][0]["images"][0]["url"] = (
        "https://pending.invalid/image"
    )
    pending["review_manifest"]["targets"][0]["prices"][0]["amount"] = "99"
    pending_approval = deepcopy(approval)
    pending_approval["candidate_digest"] = pending["candidate_digest"]
    pending_approval["approval_digest"] = "f" * 64

    with pytest.raises(ShopeeRegionalRecoveryError, match="identity conflicts"):
        validate_recovery_manifest(
            manifest,
            snapshot=snapshot,
            candidate=pending,
            approval=pending_approval,
            allowed_source_roots=[tmp_path],
        )

    checked = validate_recovery_manifest(
        manifest,
        snapshot=snapshot,
        candidate=candidate,
        approval=approval,
        allowed_source_roots=[tmp_path],
    )
    assert checked["candidate_digest"] == candidate["candidate_digest"]
    assert checked["approval_digest"] == approval["approval_digest"]
    assert checked["targets"][0]["approved_copy"] == original_copy
    assert checked["targets"][0]["approved_image_route"] == original_images
    assert checked["targets"][0]["local_original_price"] == original_price
    assert checked["target_labels"] == manifest["target_labels"]


@pytest.mark.parametrize("source", ["global_readback", "global_model_readback", "regional_readback"])
def test_continuation_rejects_any_nonfresh_official_preflight(tmp_path, source):
    candidate, approval, snapshot, manifest, _receipt = _continuation_facts(tmp_path)
    path = Path(manifest["source_file_paths"][source]); value = json.loads(path.read_text())
    value["observed_at"] = "2026-09-13T02:00:00+00:00"
    if "evidence_digest" in value:
        value.pop("evidence_digest"); value["evidence_digest"] = _digest(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ShopeeRegionalRecoveryError):
        validate_recovery_manifest(manifest, snapshot=snapshot, candidate=candidate,
                                   approval=approval, allowed_source_roots=[tmp_path])
