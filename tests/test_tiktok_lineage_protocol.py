"""Synthetic pure-protocol fixtures; never reads source evidence or stores."""
from copy import deepcopy
import hashlib
import json
import pytest
import shared_platform.tiktok_lineage_recovery as recovery_module
from shared_platform.tiktok_lineage_recovery import (
 AUTHORITY_SCHEMA_VERSION, APPROVED_COMPLETION_AUTHORITY_SCHEMA_VERSION,
 APPROVED_COMPLETION_SCHEMA_VERSION,TIKTOK_TARGET_ORDER,TRANSPORT_BINDING_KIND,
 TikTokLineageRecoveryError,assert_manifest_matches_approved_snapshot,
 compile_tiktok_lineage_recovery,compile_tiktok_approved_first_completion,
 validate_tiktok_approved_first_completion_manifest)
RECOVERY=tuple(label for label in TIKTOK_TARGET_ORDER if label not in {'tiktok:LH_PH','tiktok:HB_PH'})
def _facts(label: str) -> dict:
    title = "Approved " + label
    description = "Approved description " + label
    skus = [{"seller_sku": "660988", "model_sku": "0988",
             "price": {"amount": "123.00", "currency": "MYR"},
             "parcel": {"weight_kg": "0.17", "package_cm": ["20", "20", "3"]},
             "specification": {"style": "default"}, "variant_gallery": []}]
    gallery = [{"content_digest": "sha256:" + "4" * 64}, {"content_digest": "sha256:" + "5" * 64}]
    warehouse = {"semantic_digest": "sha256:" + "6" * 64, "inventory_digest": "sha256:" + "7" * 64}
    return {
        "title": title,
        "description": description,
        "skus": skus,
        "category": {"id": "123456", "evidence_digest": "sha256:" + "3" * 64},
        "gallery": gallery,
        "warehouse": warehouse,
        "provider_target": {"expected_title": title, "expected_description": description,
                            "expected_skus": skus, "expected_category_id": "123456",
                            "expected_gallery": gallery, "expected_warehouse": warehouse},
    }


def _lineage() -> dict:
    return {
        "offer_id": "9990000001", "revision": "5",
        "plan_id": "omnichannel:" + "8" * 64,
        "snapshot_digest": "sha256:" + "1" * 64,
        "candidate_digest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "approval_digest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "source_evidence_digest": "sha256:" + "2" * 64,
        "source_run_id": "product-center-tiktok-original",
        "targets": [{"target_label": label, "facts": _facts(label),
                     "snapshot_projection_digest": "sha256:" + "b" * 64}
                    for label in TIKTOK_TARGET_ORDER],
    }


def _observations() -> dict:
    result = {}
    for label in TIKTOK_TARGET_ORDER:
        status = "PUBLISHED" if label == "tiktok:LH_PH" else "PROCESSING" if label == "tiktok:HB_PH" else "FAILED"
        facts = _facts(label)
        result[label] = {
            "status": status, "outcome_known": True,
            "submission_accepted": status != "FAILED",
            "existing_detail_id": "detail-" + label.split(":")[1],
            "create_or_claim_allowed": False, "facts": deepcopy(facts),
            "transport_binding": {
                "kind": TRANSPORT_BINDING_KIND,
                "assets": [
                    {"url": f"https://cdn.example/{index}.png", "content_digest": asset["content_digest"],
                     "verification_digest": "sha256:" + chr(97 + index) * 64}
                    for index, asset in enumerate(facts["gallery"])
                ],
            },
        }
        body = {key: result[label]["transport_binding"][key] for key in ("kind", "assets")}
        result[label]["transport_binding"]["verification_receipt_digest"] = recovery_module._canonical_digest(body)
    return result


def _authority() -> dict:
    lineage = _lineage()
    authority = {
        "schema_version": AUTHORITY_SCHEMA_VERSION, "subject": "recorded-approver",
        **{key: lineage[key] for key in ("offer_id", "revision", "plan_id", "snapshot_digest", "candidate_digest", "approval_digest")},
        "target_operations": {label: {"save_draft": 1, "publish_target": 1} for label in RECOVERY},
    }
    authority["authority_receipt_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return authority


def _manifest() -> dict:
    return compile_tiktok_lineage_recovery(_lineage(), _observations(), recovery_target_labels=RECOVERY, authority=_authority())


def _completion_addendum() -> dict:
    lineage = _lineage()
    body = {
        "schema_version": APPROVED_COMPLETION_AUTHORITY_SCHEMA_VERSION,
        "authority_kind": "first_exact_completion", "subject": "Kyle", "created_at": "2026-09-14T00:00:00+00:00",
        "status": "READY_FOR_RUNTIME_VALIDATION", "external_writes_during_artifact_generation": {"neutral_cdn_upload": 1, "miaoshou": 0, "marketplace": 0},
        "user_authorization_chronology": [{"sequence": index} for index in range(1, 5)],
        "offer_id": lineage["offer_id"], "revision": lineage["revision"],
        "approved_lineage": {"plan_id": lineage["plan_id"], "candidate_digest": lineage["candidate_digest"],
                             "approval_digest": lineage["approval_digest"], "execution_snapshot_digest": lineage["snapshot_digest"]},
        "readonly_whole_file_digest": "sha256:" + "a" * 64,
        "neutral_checkpoint_whole_file_digest": "sha256:" + "b" * 64,
        "canonical_ten_target_status_table": [{"target_label": label, "status": "PUBLISHED" if label == "tiktok:LH_PH" else "PROCESSING" if label == "tiktok:HB_PH" else "FAILED"} for label in TIKTOK_TARGET_ORDER],
        "scope": {"included_targets": [
            {"target_label": label, "existing_detail_id": "detail-" + label.split(":")[1],
             "shop_id": "shop-" + label.split(":")[1], "site_code": label.split(":")[1],
             "create_or_claim_allowed": False, "write_budget": {"save_draft": 1, "publish_target": 1},
             "facts": _facts(label), "preread_target_digest": "sha256:" + "c" * 64,
             "warehouse_authority": {"kind": "mutable_operational_execution_fact", "shop_id": "shop-" + label.split(":")[1],
                                     "all_active_rows": [{"warehouse_id": "1", "warehouse_name": "approved", "stock": 1}],
                                     "chosen_allocation": [{"warehouse_id": "1", "warehouse_name": "approved", "stock": 1}],
                                     "positive_stock": True, "unique_semantic_match": True,
                                     "fresh_reread_required_before_execution": True,
                                     "semantic_digest": _facts(label)["warehouse"]["semantic_digest"],
                                     "inventory_digest": _facts(label)["warehouse"]["inventory_digest"]},
             "transport_binding": deepcopy(_observations()[label]["transport_binding"])}
            for label in RECOVERY], "excluded_targets": [
                {"target_label": "tiktok:LH_PH", "status": "PUBLISHED", "retry_forbidden": True},
                {"target_label": "tiktok:HB_PH", "status": "PROCESSING", "retry_forbidden": True},
            ]},
        "execution_constraints": {"provider_channel": "official_api_only", "browser_or_ui_control_allowed": False,
                                  "create_detail_allowed": False, "claim_detail_allowed": False,
                                  "target_scope_expansion_allowed": False,
                                  "state_machine": {"accepted_without_official_exact": "PROCESSING", "official_exact": "PUBLISHED", "unknown_write": "NO_RETRY"}},
    }
    body["authority_receipt_digest"] = recovery_module._canonical_digest(body)
    return body


def _completion_manifest() -> dict:
    lineage = _lineage(); lineage["targets"] = [row for row in lineage["targets"] if row["target_label"] in RECOVERY]
    preread = _observations()
    return compile_tiktok_approved_first_completion(
        lineage, preread, completion_target_labels=RECOVERY,
        authority_addendum=_completion_addendum(),
    )


def test_approved_first_completion_is_honest_and_has_no_source_run_claim():
    manifest = _completion_manifest()
    assert manifest["schema_version"] == APPROVED_COMPLETION_SCHEMA_VERSION
    assert manifest["operation_kind"] == "approved_first_completion"
    assert manifest["completion_target_labels"] == list(RECOVERY)
    assert "source_run_id" not in manifest and "source_evidence_digest" not in manifest
    assert validate_tiktok_approved_first_completion_manifest(manifest) == manifest


def test_approved_first_completion_rejects_scope_or_existing_detail_tamper():
    preread = _observations(); preread[RECOVERY[0]]["existing_detail_id"] = "attacker-detail"
    lineage = _lineage(); lineage["targets"] = [row for row in lineage["targets"] if row["target_label"] in RECOVERY]
    with pytest.raises(TikTokLineageRecoveryError, match="blocked"):
        compile_tiktok_approved_first_completion(
            lineage, preread, completion_target_labels=RECOVERY,
            authority_addendum=_completion_addendum(),
        )


def test_approved_completion_snapshot_assert_uses_bound_content_and_operational_warehouse(monkeypatch):
    manifest = _completion_manifest(); projections = {}
    for command in manifest["commands"]:
        facts = command["approved_facts"]
        projection = {"target": {"target_label": command["target_label"]},
                      "content": {"title": facts["title"], "description": facts["description"]},
                      "images": ["https://expired.example/image" for _ in facts["gallery"]],
                      "category": {"category": {"id": facts["category"]["id"]}},
                      "skus": [{"seller_sku": row["seller_sku"], "model_sku": row["model_sku"],
                                "price": row["price"], "parcel": row["parcel"], "specification": row["specification"],
                                "variant_images": ["https://expired.example/variant" for _ in row["variant_gallery"]]}
                               for row in facts["skus"]], "warehouse": None, "stock_policy": None}
        facts["category"]["evidence_digest"] = recovery_module._canonical_digest(projection["category"])
        command["restored_fact_digest"] = recovery_module._canonical_digest(facts)
        command["snapshot_projection_digest"] = recovery_module._canonical_digest(projection)
        cb = dict(command); cb.pop("command_digest"); command["command_digest"] = recovery_module._canonical_digest(cb)
        projections[command["target_label"]] = projection
    mb = dict(manifest); mb.pop("manifest_digest"); manifest["manifest_digest"] = recovery_module._canonical_digest(mb)
    monkeypatch.setattr(recovery_module, "snapshot_tiktok_target_projection", lambda _snapshot, label: deepcopy(projections[label]))
    assert_manifest_matches_approved_snapshot(
        manifest, {}, asset_digest_reader=lambda _url: pytest.fail("expired snapshot image route must not be fetched")
    )
    addendum = _completion_addendum(); addendum["scope"]["included_targets"][0]["target_label"] = "tiktok:LH_PH"
    body = dict(addendum); body.pop("authority_receipt_digest")
    addendum["authority_receipt_digest"] = recovery_module._canonical_digest(body)
    lineage = _lineage(); lineage["targets"] = [row for row in lineage["targets"] if row["target_label"] in RECOVERY]
    with pytest.raises(TikTokLineageRecoveryError, match="scope"):
        compile_tiktok_approved_first_completion(
            lineage, _observations(), completion_target_labels=RECOVERY,
            authority_addendum=addendum,
        )


def test_manifest_binds_original_authority_ten_targets_and_exact_eight_subset():
    manifest = _manifest()
    assert manifest["lineage"]["candidate_digest"].endswith("cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc")
    assert manifest["lineage"]["approval_digest"].endswith("dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd")
    assert manifest["approved_target_labels"] == list(TIKTOK_TARGET_ORDER)
    assert manifest["recovery_target_labels"] == list(RECOVERY)
    assert [row["target_label"] for row in manifest["commands"]] == list(RECOVERY)
    assert manifest["no_scope_expansion"] is True
    assert all(row["forbidden_operations"] == ["create", "claim"] for row in manifest["commands"])


def test_manifest_preserves_ordered_multisku_prices_parcels_specs_and_variant_images():
    lineage, observations = _lineage(), _observations()
    for approved in lineage["targets"]:
        facts = approved["facts"]
        second = {"seller_sku": "660988", "model_sku": "0988-B",
                  "price": {"amount": "129.00", "currency": "MYR"},
                  "parcel": {"weight_kg": "0.19", "package_cm": ["21", "20", "3"]},
                  "specification": {"style": "blue"},
                  "variant_gallery": [{"content_digest": "sha256:" + "c" * 64}]}
        facts["skus"].append(second); facts["provider_target"]["expected_skus"] = deepcopy(facts["skus"])
    for label in TIKTOK_TARGET_ORDER:
        observations[label]["facts"] = deepcopy(next(row["facts"] for row in lineage["targets"] if row["target_label"] == label))
        binding = observations[label]["transport_binding"]
        binding["assets"].append({"url": "https://cdn.example/variant-b.png",
                                  "content_digest": "sha256:" + "c" * 64,
                                  "verification_digest": "sha256:" + "d" * 64})
        binding["verification_receipt_digest"] = recovery_module._canonical_digest(
            {"kind": binding["kind"], "assets": binding["assets"]}
        )
    manifest = compile_tiktok_lineage_recovery(
        lineage, observations, recovery_target_labels=RECOVERY, authority=_authority()
    )
    assert [row["model_sku"] for row in manifest["commands"][0]["approved_facts"]["skus"]] == ["0988", "0988-B"]


@pytest.mark.parametrize("field", ["title", "description", "skus", "category", "gallery", "warehouse"])
def test_manifest_rejects_any_approved_execution_fact_drift(field):
    observations = _observations()
    row = observations[RECOVERY[0]]["facts"]
    row[field] = "drift" if field in {"title", "description"} else [] if field == "skus" else {}
    with pytest.raises(TikTokLineageRecoveryError, match="blocked"):
        compile_tiktok_lineage_recovery(_lineage(), observations, recovery_target_labels=RECOVERY, authority=_authority())


def test_manifest_rejects_cdn_url_content_digest_or_authority_drift():
    observations = _observations()
    observations[RECOVERY[0]]["transport_binding"]["assets"][0]["content_digest"] = "sha256:" + "f" * 64
    with pytest.raises(TikTokLineageRecoveryError, match="blocked"):
        compile_tiktok_lineage_recovery(_lineage(), observations, recovery_target_labels=RECOVERY, authority=_authority())
    authority = _authority(); authority["candidate_digest"] = "sha256:" + "0" * 64
    with pytest.raises(TikTokLineageRecoveryError, match="candidate_digest conflicts"):
        compile_tiktok_lineage_recovery(_lineage(), _observations(), recovery_target_labels=RECOVERY, authority=authority)


def test_unknown_or_processing_target_cannot_enter_recovery_subset():
    observations = _observations(); observations[RECOVERY[0]]["status"] = "UNKNOWN"
    with pytest.raises(TikTokLineageRecoveryError, match="blocked"):
        compile_tiktok_lineage_recovery(_lineage(), observations, recovery_target_labels=RECOVERY, authority=_authority())


@pytest.mark.parametrize('status',['UNKNOWN','PROCESSING','PUBLISHED','SUBMITTED_UNVERIFIED'])
def test_nonresubmittable_states_never_compile_as_retry(status):
    observations=_observations();observations[RECOVERY[0]]['status']=status
    with pytest.raises(TikTokLineageRecoveryError):
        compile_tiktok_lineage_recovery(_lineage(),observations,recovery_target_labels=RECOVERY,authority=_authority())


@pytest.mark.parametrize('key',['candidate_digest','approval_digest','snapshot_digest'])
def test_recorded_approval_identity_cannot_be_rebound(key):
    authority=_authority();authority[key]='sha256:'+'e'*64
    body=dict(authority);body.pop('authority_receipt_digest');authority['authority_receipt_digest']=recovery_module._canonical_digest(body)
    with pytest.raises(TikTokLineageRecoveryError):
        compile_tiktok_lineage_recovery(_lineage(),_observations(),recovery_target_labels=RECOVERY,authority=authority)


def test_ten_target_scope_cannot_drop_or_append_a_country():
    for labels in [list(TIKTOK_TARGET_ORDER[:-1]),list(TIKTOK_TARGET_ORDER)+['tiktok:EXTRA']]:
        lineage=_lineage();lineage['targets']=[{'target_label':label,'facts':_facts(label),'snapshot_projection_digest':'sha256:'+'b'*64} for label in labels]
        with pytest.raises(TikTokLineageRecoveryError):
            compile_tiktok_lineage_recovery(lineage,_observations(),recovery_target_labels=RECOVERY,authority=_authority())


def test_compilation_has_no_execution_or_default_network_fallback():
    assert not hasattr(recovery_module,'execute_tiktok_lineage_recovery')
    assert not hasattr(recovery_module,'read_https_asset_digest')
    with pytest.raises(TikTokLineageRecoveryError,match='ASSET_READER_NOT_AVAILABLE'):
        recovery_module._asset_reader_unavailable('https://example.invalid/asset.png')
    manifest=_manifest()
    assert manifest['lineage']['approval_digest']==_lineage()['approval_digest']
    assert manifest['default_provider_writes_enabled'] is False


def test_depth2_retry_rejects_rehashed_chain_or_third_depth(tmp_path):
    predecessor = _completion_manifest()
    predecessor["schema_version"] = "tiktok-approved-first-completion-manifest/v3"
    body = dict(predecessor); body.pop("manifest_digest")
    predecessor["manifest_digest"] = recovery_module._canonical_digest(body)
    with pytest.raises(TikTokLineageRecoveryError, match="retry schema conflicts"):
        validate_tiktok_approved_first_completion_manifest(predecessor)


def test_v1_completion_manifest_cannot_smuggle_retry_binding():
    manifest = _completion_manifest()
    manifest["zero_write_retry"] = {
        "schema_version": "tiktok-approved-first-completion-zero-write-retry/v1"
    }
    body = dict(manifest); body.pop("manifest_digest")
    manifest["manifest_digest"] = recovery_module._canonical_digest(body)
    with pytest.raises(TikTokLineageRecoveryError, match="retry schema conflicts"):
        recovery_module.validate_tiktok_approved_first_completion_manifest(manifest)


@pytest.mark.parametrize("field,value", [("positive_stock", False), ("unique_semantic_match", False)])
def test_approved_completion_zero_or_ambiguous_warehouse_is_not_compilable(field, value):
    addendum = _completion_addendum(); addendum["scope"]["included_targets"][0]["warehouse_authority"][field] = value
    body = dict(addendum); body.pop("authority_receipt_digest"); addendum["authority_receipt_digest"] = recovery_module._canonical_digest(body)
    lineage = _lineage(); lineage["targets"] = [row for row in lineage["targets"] if row["target_label"] in RECOVERY]
    with pytest.raises(TikTokLineageRecoveryError, match="blocked"):
        compile_tiktok_approved_first_completion(lineage, _observations(), completion_target_labels=RECOVERY, authority_addendum=addendum)

