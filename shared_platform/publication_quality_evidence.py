"""Bind durable image, translation, and pricing evidence to one frozen release.

This module is deliberately read-only.  It never calls a provider or platform;
it only validates the already persisted Product Center evidence for one offer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = ROOT / "reports" / "product-preparation"
EVIDENCE_SCHEMA = "publication-quality-evidence/v1"


def validate_round1_evidence_identity(
    snapshot: Mapping[str, Any], evidence: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Bind approved first-round facts to the exact R3 release, not just an offer."""
    if not isinstance(evidence, Mapping) or "round1_snapshot" not in evidence:
        return None
    round1 = evidence["round1_snapshot"]
    if not isinstance(round1, Mapping):
        raise ValueError("round1 evidence format is invalid")
    from shared_platform.publication_rounds import canonical_digest

    unsigned = dict(round1)
    digest = unsigned.pop("snapshot_digest", None)
    if (
        round1.get("schema_version") != "round1-approved-snapshot/v1"
        or round1.get("offer_id") != snapshot.get("offer_id")
        or digest != canonical_digest(unsigned)
    ):
        raise ValueError("round1 evidence offer or digest conflicts")
    approved = round1.get("approved_by") == "Kyle" or (
        round1.get("approved_by") == "product-publication-autopilot"
        and round1.get("approval_authority") == "ACTIVE_AUTOPILOT_POLICY"
        and round1.get("human_approval") is False
    )
    targets = round1.get("canonical_targets")
    expected = [row["target_label"] for row in snapshot["publication_targets"]]
    if (
        round1.get("status") != "APPROVED" or not approved
        or not isinstance(targets, list)
        or any(type(label) is not str for label in targets)
        or len(targets) != len(set(targets)) or not set(expected).issubset(targets)
    ):
        raise ValueError("round1 evidence approval or target scope conflicts")
    bridge = evidence.get("publication_bridge")
    handoff = bridge.get("release_handoff") if isinstance(bridge, Mapping) else None
    if (
        not isinstance(bridge, Mapping) or bridge.get("offer_id") != snapshot.get("offer_id")
        or not isinstance(handoff, Mapping) or handoff.get("plan_id") != snapshot.get("plan_id")
        or handoff.get("snapshot_digest") != snapshot.get("snapshot_digest")
        or handoff.get("round1_snapshot_digest") != digest
    ):
        raise ValueError("round1 evidence is not bound to this exact frozen release")
    return round1


class PublicationQualityEvidenceError(ValueError):
    def __init__(self, code: str, path: Path):
        self.code, self.path = code, str(path)
        super().__init__(f"sidecar {code}: {path.name}")


def _load_json(path: Path, *, root: Path, offer_id: str) -> dict[str, Any] | None:
    from shared_platform.immutable_approval_files import ImmutableFileError, require_local_path

    try:
        info = require_local_path(path, root=root)
    except ImmutableFileError as error:
        raise PublicationQualityEvidenceError("UNSAFE_PATH", path) from error
    if info is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PublicationQualityEvidenceError("UNREADABLE", path) from error
    except (ValueError, UnicodeError) as error:
        raise PublicationQualityEvidenceError("MALFORMED", path) from error
    if not isinstance(value, dict):
        raise PublicationQualityEvidenceError("MALFORMED", path)
    if "offer_id" in value and str(value["offer_id"]) != offer_id:
        raise PublicationQualityEvidenceError("OFFER_ID_CONFLICT", path)
    return value


def load_publication_quality_evidence(
    offer_id: str,
    *,
    reports_root: Path | None = None,
    workbench_root: Path | None = None,
) -> dict[str, Any] | None:
    """Load the exact four sidecars used by the durable publication handoff."""

    identity = str(offer_id or "").strip()
    if not identity.isdigit() or not 1 <= len(identity) <= 32:
        return None
    declared_root = reports_root or REPORTS_ROOT
    root = declared_root / identity
    names = {
        "workflow_handoff": "workflow-handoff.json",
        "publication_bridge": "dual-brand-publication-handoff.json",
        "translation_plan": "brand-image-translation-plan.json",
        "translation_result": "brand-image-translation.json",
        "image_qa": "automated-image-qa.json",
        "platform_preflight": "platform-preflight.json",
        "shopee_variant_image_successor": "shopee-variant-image-successor.json",
    }
    documents = {key: _load_json(root / name, root=declared_root, offer_id=identity) for key, name in names.items()}
    required = {
        key: value
        for key, value in documents.items()
        if key != "shopee_variant_image_successor"
    }
    if documents["shopee_variant_image_successor"] is None:
        documents.pop("shopee_variant_image_successor")
    # A caller with a custom reports root must also state its workbench root;
    # otherwise production workbench facts could leak into isolated tests.
    effective_workbench_root = (
        ROOT / "data" / "new_product_workbench"
        if reports_root is None
        else workbench_root
    )
    if effective_workbench_root is not None:
        workbench = _load_json(effective_workbench_root / f"{identity}.json", root=effective_workbench_root, offer_id=identity)
        if workbench is not None:
            documents["workbench_state"] = workbench
    round1_path = root / "round1-approved-snapshot.json"
    round1 = _load_json(round1_path, root=declared_root, offer_id=identity)
    if round1 is not None:
        unsigned = dict(round1)
        supplied = str(unsigned.pop("snapshot_digest", ""))
        from shared_platform.publication_rounds import canonical_digest

        if not (
            round1.get("schema_version") == "round1-approved-snapshot/v1"
            and round1.get('offer_id') == identity
            and supplied == canonical_digest(unsigned)
        ):
            raise PublicationQualityEvidenceError("DIGEST_OR_IDENTITY_CONFLICT", round1_path)
        documents["round1_snapshot"] = round1
    # R2 QA is tied to the actual numbered assets and frozen first review.
    # Missing legacy sidecars remain visible as a failed evidence check.
    documents["first_review"] = _load_json(root / "first-review.json", root=declared_root, offer_id=identity)
    documents["generation_result"] = _load_json(root / "brand-image-generation.json", root=declared_root, offer_id=identity)
    if any(value is None for value in required.values()):
        return None
    return documents


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _independent_price_formula_matches(calculation: Mapping[str, Any]) -> bool | None:
    """Recompute v2 reverse pricing without importing the source calculator.

    ``None`` means a legacy calculation contract.  New v2 contracts fail closed
    when an input is absent, a denominator is invalid, or rounded results cannot
    have been produced by the declared formula.
    """

    if calculation.get("contract_version") != "publication-price-calculation/v2":
        return None
    kind = str(calculation.get("kind") or "")
    inputs = calculation.get("inputs")
    result = calculation.get("result")
    if not isinstance(inputs, Mapping) or not isinstance(result, Mapping):
        return False
    if (
        set(inputs) <= {"minimum_profit_cny"}
        and result.get("sale_after_discount") is None
    ):
        # Early round-1 projections incorrectly labelled this incomplete shape
        # as v2. Frozen price equality still applies, but independent formula
        # recomputation is not claimed for that legacy artifact.
        return None

    if kind == "SEA_REVERSE_PRICING":
        cost_keys = ("goods_cost_local", "logistics_local", "fixed_fee_local")
        rate_keys = (
            "commission_rate_pct", "transaction_rate_pct", "affiliate_rate_pct",
            "ad_rate_pct", "creator_rate_pct", "seller_tax_rate_pct",
        )
        extra_rate = _decimal(inputs.get("extra_rate_pct"))
    elif kind == "MX_REVERSE_PRICING":
        cost_keys = ("goods_cost_local", "hidden_shipping_local", "fixed_fee_local")
        rate_keys = (
            "import_tax_rate_pct", "commission_rate_pct", "sfp_rate_pct",
            "affiliate_rate_pct", "ad_rate_pct",
        )
        extra_rate = Decimal("0")
    elif kind == "GB_REVERSE_PRICING":
        cost_keys = ("goods_cost_local", "shipping_local")
        rate_keys = (
            "vat_rate_pct", "commission_rate_pct", "smart_promo_rate_pct",
            "affiliate_rate_pct", "ad_rate_pct",
        )
        extra_rate = Decimal("0")
    else:
        return False

    costs = [_decimal(inputs.get(key)) for key in cost_keys]
    rates = [_decimal(inputs.get(key)) for key in rate_keys]
    margin = _decimal(inputs.get("target_margin_pct"))
    discount = _decimal(inputs.get("discount_reserve_pct"))
    sale_result = _decimal(result.get("sale_after_discount"))
    list_result = _decimal(result.get("list_price"))
    if any(value is None for value in (*costs, *rates, margin, discount, extra_rate, sale_result, list_result)):
        return False
    assert margin is not None and discount is not None and extra_rate is not None
    denominator = Decimal("1") - (margin + sum(rates, Decimal("0")) + extra_rate) / Decimal("100")
    discount_factor = Decimal("1") - discount / Decimal("100")
    if denominator <= 0 or discount_factor <= 0:
        return False
    fixed_cost = sum(costs, Decimal("0"))
    raw_sale = fixed_cost / denominator
    if kind == "SEA_REVERSE_PRICING":
        cap = _decimal(inputs.get("extra_fee_cap_local"))
        if cap is not None and cap > 0 and raw_sale * extra_rate / Decimal("100") >= cap:
            denominator_without_extra = denominator + extra_rate / Decimal("100")
            raw_sale = (fixed_cost + cap) / denominator_without_extra
    raw_list = raw_sale / discount_factor
    effective_sale = list_result * discount_factor
    currency = str(result.get("currency") or "")
    price_step = Decimal("1000") if currency == "VND" else Decimal("1")
    rounded_sale_matches = abs(sale_result - effective_sale) <= Decimal("0.02")
    if result.get("min_profit_adjusted") is True:
        rounded_list_matches = list_result >= raw_list
    else:
        rounded_list_matches = list_result >= raw_list and list_result - raw_list < price_step
    return bool(rounded_sale_matches and rounded_list_matches)


def _legacy_target_override_is_exact(
    *,
    snapshot: Mapping[str, Any],
    workbench: object,
    target_label: str,
    amount: object,
    currency: object,
) -> bool:
    """Recognize the pre-fix A-link override projection without weakening it.

    Older round-1 packets preserved the exact override amount but mislabeled
    its calculation as SEA reverse pricing.  Accept that legacy artifact only
    when the current revision-bound workbench still contains complete official
    source evidence for the exact target, amount, currency and Seller SKU.
    """

    if not isinstance(workbench, Mapping):
        return False
    workbench_revision = workbench.get("_revision")
    snapshot_revision = snapshot.get("product_revision")
    if (
        type(workbench_revision) is not int
        or type(snapshot_revision) is not int
        or workbench_revision < snapshot_revision
    ):
        return False
    review = workbench.get("review")
    if not isinstance(review, Mapping):
        return False
    seller_skus = {
        str(row.get("seller_sku") or "").strip()
        for row in snapshot.get("skus") or ()
        if isinstance(row, Mapping) and str(row.get("seller_sku") or "").strip()
    }
    seller_sku = (
        str(snapshot.get("seller_sku") or "").strip()
        or (next(iter(seller_skus)) if len(seller_skus) == 1 else "")
    )
    if str(review.get("seller_sku") or "").strip() != seller_sku:
        return False
    approval = workbench.get("product_approval")
    if (
        not isinstance(approval, Mapping)
        or str(approval.get("status") or "").casefold() != "approved"
        or str(approval.get("seller_sku") or "").strip() != seller_sku
    ):
        return False
    site = target_label.split(":", 1)[1].lower() if ":" in target_label else ""
    override = review.get("target_price_overrides")
    override = override.get(site) if isinstance(override, Mapping) else None
    if not isinstance(override, Mapping):
        return False
    return bool(
        _decimal(override.get("list_price")) == _decimal(amount)
        and str(override.get("currency") or "").upper()
        == str(currency or "").upper()
        and str(override.get("authority") or "").strip()
        and str(override.get("source_product_id") or "").strip()
        and str(override.get("reason") or "").strip()
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(override.get("evidence_digest") or ""),
        )
    )


def _task_key(row: Mapping[str, Any]) -> tuple[object, str, str, str]:
    return (
        row.get("review_number")
        if row.get("review_number") is not None
        else row.get("source_review_number"),
        str(row.get("brand_id") or ""),
        str(row.get("role") or ""),
        str(row.get("locale") or ""),
    )


def _check(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    code: str,
    ok: bool,
    message_zh: str,
    *,
    target_label: str | None = None,
) -> None:
    checks.append({
        "code": code,
        "status": "PASSED" if ok else "FAILED",
        "target_label": target_label,
    })
    if not ok:
        errors.append({
            "code": code,
            "stage": "DURABLE_EVIDENCE",
            "severity": "BLOCKING",
            "target_label": target_label,
            "message_zh": message_zh,
        })


def _variant_successor_lineage_matches(
    snapshot: Mapping[str, Any],
    successor: object,
    *,
    workflow: Mapping[str, Any],
    release: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> bool:
    """Prove a successor changed only the frozen Shopee SKU-image contract."""

    if not isinstance(successor, Mapping):
        return False
    frozen = successor.get("successor_snapshot")
    current_master = snapshot.get("shopee_global_master")
    current_master = current_master if isinstance(current_master, Mapping) else {}
    frozen_bindings = current_master.get("variant_image_bindings")
    current_digest = str(snapshot.get("snapshot_digest") or "")
    current_plan = str(snapshot.get("plan_id") or "")
    payload_digest = str(successor.get("successor_payload_digest") or "")
    release_payload_digest = str(
        (snapshot.get("bindings") or {}).get("release_payload_digest")
        if isinstance(snapshot.get("bindings"), Mapping)
        else ""
    )
    predecessor_plan = str(successor.get("predecessor_plan_id") or "")
    predecessor_digest = str(successor.get("predecessor_snapshot_digest") or "")
    reconciliation = successor.get("reconciliation")
    return bool(
        successor.get("schema_version")
        == "shopee-variant-image-successor-preparation/v1"
        and successor.get("status") == "APPROVED_AND_FROZEN"
        and successor.get("platform_writes") == 0
        and str(successor.get("offer_id") or "")
        == str(snapshot.get("offer_id") or "")
        and str(successor.get("successor_plan_id") or "") == current_plan
        and str(successor.get("successor_snapshot_digest") or "") == current_digest
        and isinstance(frozen, Mapping)
        and dict(frozen) == dict(snapshot)
        and successor.get("bindings") == frozen_bindings
        and current_master.get("schema_version") == "shopee-global-master/v2"
        and payload_digest
        and release_payload_digest == f"sha256:{payload_digest.removeprefix('sha256:')}"
        and isinstance(reconciliation, Mapping)
        and reconciliation.get("status") == "MATCHED_PERSISTED_SUCCESSOR"
        and reconciliation.get("external_write_count") == 0
        and predecessor_plan
        and predecessor_digest
        and str(workflow.get("plan_id") or "") == predecessor_plan
        and str(release.get("plan_id") or "") == predecessor_plan
        and str(preflight.get("plan_id") or "") == predecessor_plan
        and str(workflow.get("snapshot_digest") or "") == predecessor_digest
        and str(release.get("snapshot_digest") or "") == predecessor_digest
        and str(preflight.get("snapshot_digest") or "") == predecessor_digest
    )


def evaluate_publication_quality_evidence(
    snapshot: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    *,
    pack: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify durable sidecars are for this exact snapshot and complete."""

    if evidence is None:
        return {
            "schema_version": EVIDENCE_SCHEMA,
            "status": "NOT_AVAILABLE",
            "checks": [],
            "errors": [],
            "summary": {"passed": 0, "failed": 0, "not_available": 1},
        }
    documents = dict(evidence)
    workflow = documents.get("workflow_handoff")
    bridge = documents.get("publication_bridge")
    plan = documents.get("translation_plan")
    result = documents.get("translation_result")
    image_qa = documents.get("image_qa")
    preflight = documents.get("platform_preflight")
    workbench = documents.get("workbench_state")
    successor = documents.get("shopee_variant_image_successor")
    if not all(
        isinstance(row, Mapping)
        for row in (workflow, bridge, plan, result, image_qa, preflight)
    ):
        return {
            "schema_version": EVIDENCE_SCHEMA,
            "status": "FAILED",
            "checks": [],
            "errors": [{
                "code": "DURABLE_EVIDENCE_FORMAT_INVALID",
                "stage": "DURABLE_EVIDENCE",
                "severity": "BLOCKING",
                "target_label": None,
                "message_zh": "发布证据文件格式无效。",
            }],
            "summary": {"passed": 0, "failed": 1, "not_available": 0},
        }

    checks: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        round1 = validate_round1_evidence_identity(snapshot, documents)
    except ValueError:
        round1 = None
        _check(checks, errors, "ROUND1_EVIDENCE_IDENTITY_CONFLICT", False,
               "第一轮事实未绑定当前商品、冻结范围与发布快照。")
    offer_id = str(snapshot.get("offer_id") or "")
    plan_id = str(snapshot.get("plan_id") or "")
    digest = str(snapshot.get("snapshot_digest") or "")
    release = bridge.get("release_handoff")
    release = release if isinstance(release, Mapping) else {}
    direct_identities_ok = (
        str(workflow.get("offer_id") or offer_id) == offer_id
        and str(bridge.get("offer_id") or "") == offer_id
        and str(plan.get("offer_id") or "") == offer_id
        and str(result.get("offer_id") or "") == offer_id
        and str(workflow.get("plan_id") or "") == plan_id
        and str(release.get("plan_id") or "") == plan_id
        and str(workflow.get("snapshot_digest") or "") == digest
        and str(release.get("snapshot_digest") or "") == digest
    )
    successor_lineage_ok = _variant_successor_lineage_matches(
        snapshot,
        successor,
        workflow=workflow,
        release=release,
        preflight=preflight,
    )
    identities_ok = direct_identities_ok or successor_lineage_ok
    _check(
        checks, errors, "HANDOFF_IDENTITY_MATCH", identities_ok,
        "妙手交接、翻译结果与冻结发布快照身份不一致。",
    )
    handoff_ok = (
        bridge.get("status") == "MIAOSHOU_VERIFIED"
        and bridge.get("approval_status") == "APPROVED"
        and release.get("status") == "READY_TO_PUBLISH"
        and isinstance(bridge.get("miaoshou_sync"), Mapping)
        and bridge["miaoshou_sync"].get("verified") is True
        and bridge["miaoshou_sync"].get("published") is False
    )
    if bridge.get('approval_status') == 'IMAGE_PREPARATION_VERIFIED':
        from shared_platform.publication_rounds import canonical_digest
        common = bridge.get('common_stage') or {}
        readback = common.get('readback') or {}
        receipt = readback.get('evidence') or {}
        receipt_checks = receipt.get('checks') or {}
        handoff_ok = bool(
            bridge.get('status') == 'MIAOSHOU_VERIFIED'
            and release.get('status') == 'FACTS_PREPARED'
            and common.get('schema_version') == 'r3-common-evidence/v1'
            and common.get('plan_id') and common.get('run_id') and common.get('payload_digest')
            and receipt.get('verified') is True and receipt.get('offer_id') == offer_id
            and receipt.get('source') and readback.get('verified_at')
            and receipt_checks and all(value is True for value in receipt_checks.values())
            and canonical_digest(receipt).removeprefix('sha256:') == readback.get('evidence_digest')
        )
    _check(
        checks, errors, "HANDOFF_APPROVAL_AND_SYNC_VERIFIED", handoff_ok,
        "妙手公共基线尚未完成精确回读，或交接状态不可发布。",
    )

    generation_digest = str(bridge.get("generation_identity_digest") or "")
    translation_digest = str(bridge.get("translation_plan_digest") or "")
    digest_chain_ok = (
        generation_digest
        and generation_digest == str(plan.get("generation_identity_digest") or "")
        and generation_digest == str(result.get("generation_identity_digest") or "")
        and translation_digest == str(result.get("plan_digest") or "")
    )
    _check(
        checks, errors, "IMAGE_DIGEST_CHAIN_MATCH", bool(digest_chain_ok),
        "母版生成、翻译计划与本地化结果的摘要链不一致。",
    )

    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    try:
        r2_identity = validate_r2_identity(documents)
        image_qa_ok = (
            r2_identity["offer_id"] == offer_id
            and r2_identity["generation_identity_digest"] == generation_digest
            and r2_identity["translation_plan_digest"] == translation_digest
            and ("r2_identity" not in bridge or bridge["r2_identity"] == r2_identity)
        )
    except (ValueError, TypeError, KeyError):
        image_qa_ok = False
    _check(
        checks,
        errors,
        "AUTOMATED_IMAGE_QA_BOUND",
        image_qa_ok,
        "第二轮自动 QA 未绑定当前 R1、母图、翻译计划与完整实际产物摘要。",
    )

    targets = [
        str(row.get("target_label") or "")
        for row in snapshot.get("publication_targets") or ()
        if isinstance(row, Mapping)
    ]
    preflight_identity_ok = (
        str(preflight.get("plan_id") or "") == plan_id
        and str(preflight.get("snapshot_digest") or "") == digest
    ) or successor_lineage_ok
    preflight_ok = (
        preflight.get("schema_version") in {
            "platform-publication-preflight/v1",
            "platform-publication-preflight/v2",
            *(('platform-publication-preview-preflight/v1',) if snapshot.get('schema_version') == 'publication-snapshot-preview/v1' else ()),
        }
        and preflight.get("status") == "PASSED"
        and str(preflight.get("offer_id") or "") == offer_id
        and preflight_identity_ok
        and preflight.get("target_labels") == targets
        and preflight.get("external_write_count") == 0
    )
    _check(
        checks,
        errors,
        "PLATFORM_PREFLIGHT_BOUND",
        preflight_ok,
        "平台技术预检未在妙手回读后完成，或已与当前快照发生漂移。",
    )
    routes = bridge.get("image_routes")
    routes = routes if isinstance(routes, Mapping) else {}
    locales = bridge.get("route_locales")
    locales = locales if isinstance(locales, Mapping) else {}
    role_sets = pack.get("brand_role_sets")
    role_sets = dict(role_sets) if isinstance(role_sets, Mapping) else {}
    round1_plan = round1.get("image_plan") if isinstance(round1, Mapping) else None
    if isinstance(round1_plan, Mapping):
        frozen_roles: dict[str, list[str]] = {}
        for brand_plan in round1_plan.get("brand_plans") or ():
            if not isinstance(brand_plan, Mapping):
                continue
            brand_id = str(brand_plan.get("id") or "")
            target_group = str(brand_plan.get("target_group") or "").casefold()
            canonical_brand = (
                "livelyhive-sea"
                if target_group.startswith("livelyhive")
                else "homebloom-sea"
                if target_group.startswith("homebloom")
                else brand_id
            )
            roles = [
                str(row.get("role") or "")
                for row in brand_plan.get("generated_assets") or ()
                if isinstance(row, Mapping)
            ]
            if canonical_brand and roles and len(roles) == len(set(roles)) and all(roles):
                frozen_roles[canonical_brand] = roles
        if frozen_roles:
            role_sets = frozen_roles
    for label in targets:
        rows = routes.get(label)
        rows = rows if isinstance(rows, list) else []
        brand = "homebloom-sea" if label.startswith("tiktok:HB_") else "livelyhive-sea"
        expected_roles = list(role_sets.get(brand) or ())
        positions = [row.get("position") for row in rows if isinstance(row, Mapping)]
        roles = [str(row.get("role") or "") for row in rows if isinstance(row, Mapping)]
        urls = [str(row.get("url") or "") for row in rows if isinstance(row, Mapping)]
        digests = [str(row.get("artifact_digest") or "") for row in rows if isinstance(row, Mapping)]
        expected_count = len(expected_roles)
        route_ok = (
            expected_count > 0
            and len(rows) == expected_count
            and positions == list(range(1, expected_count + 1))
            and roles == expected_roles
            and all(str(row.get("brand_id") or "") == brand for row in rows if isinstance(row, Mapping))
            and len(set(urls)) == expected_count
            and len(set(digests)) == expected_count
            and all(url.startswith("https://") for url in urls)
            and all(value.startswith("sha256:") for value in digests)
            and bool(str(locales.get(label) or ""))
        )
        _check(checks, errors, "TARGET_IMAGE_ARTIFACTS_BOUND", route_ok,
               f"{label} 的已批准图片角色、顺序、语言或产物摘要不完整。",
               target_label=label)

    planned = [row for row in plan.get("tasks") or () if isinstance(row, Mapping)]
    approved = [row for row in result.get("approved_tasks") or () if isinstance(row, Mapping)]
    assets = [row for row in result.get("assets") or () if isinstance(row, Mapping)]
    inventories = [row for row in result.get("text_inventories") or () if isinstance(row, Mapping)]
    asset_by_key = {_task_key(row): row for row in assets}
    inventory_by_source = {
        (row.get("review_number"), str(row.get("brand_id") or ""), str(row.get("role") or "")): row
        for row in inventories
    }
    qa_checks = {
        str(row.get("code") or ""): str(row.get("status") or "")
        for row in image_qa.get("checks") or ()
        if isinstance(row, Mapping)
    }
    zero_translation_scope_ok = (
        not planned
        and bridge.get("translation_mode")
        == "LANGUAGE_NEUTRAL_MASTER_NO_TRANSLATION_REQUIRED"
        and qa_checks.get("OCR_LANGUAGE") == "PASSED"
    )
    translated_scope_ok = bool(planned) and all(
        _task_key(task) in asset_by_key
        and asset_by_key[_task_key(task)].get("status") == "COMPLETED"
        and str(asset_by_key[_task_key(task)].get("artifact_digest") or "").startswith("sha256:")
        and (
            task.get("review_number"),
            str(task.get("brand_id") or ""),
            str(task.get("role") or ""),
        ) in inventory_by_source
        and bool(inventory_by_source[(
            task.get("review_number"),
            str(task.get("brand_id") or ""),
            str(task.get("role") or ""),
        )].get("ocr_regions"))
        for task in planned
    )
    translation_ok = (
        plan.get("status") in {
            "APPROVED_IN_CONVERSATION", "APPROVED_BY_AUTOPILOT"
        }
        and type(plan.get("approved_task_count")) is int
        and plan.get("approved_task_count") == len(planned)
        and {_task_key(row) for row in planned} == {_task_key(row) for row in approved}
        and (translated_scope_ok or zero_translation_scope_ok)
    )
    _check(
        checks, errors, "TRANSLATION_ARTIFACTS_AND_OCR_BOUND", translation_ok,
        "翻译任务、成品产物或 OCR 证据不完整。",
    )

    target_facts = bridge.get("target_facts")
    target_facts = target_facts if isinstance(target_facts, Mapping) else {}
    snapshot_skus = {
        str(row.get("model_sku") or ""): row
        for row in snapshot.get("skus") or ()
        if isinstance(row, Mapping)
    }
    snapshot_categories = snapshot.get("categories_by_target")
    snapshot_categories = (
        snapshot_categories if isinstance(snapshot_categories, Mapping) else {}
    )
    for label in targets:
        facts = target_facts.get(label)
        category = facts.get("category") if isinstance(facts, Mapping) else None
        platform = label.split(":", 1)[0].upper()
        frozen_category_row = snapshot_categories.get(label)
        frozen_category = (
            frozen_category_row.get("category")
            if isinstance(frozen_category_row, Mapping)
            and isinstance(frozen_category_row.get("category"), Mapping)
            else frozen_category_row
        )
        frozen_category_id = str(
            frozen_category.get("id") if isinstance(frozen_category, Mapping) else ""
        ).strip()
        frozen_decision = (
            frozen_category_row.get("decision")
            if isinstance(frozen_category_row, Mapping)
            and isinstance(frozen_category_row.get("decision"), Mapping)
            else {}
        )
        frozen_decision_digest = str(
            frozen_decision.get("decision_digest") or ""
        ).strip()
        expected_categories = (
            {frozen_category_id}
            if frozen_category_id
            else set(
                (pack.get("platforms", {}).get(platform) or {}).get("category_ids") or ()
            )
        )
        candidate_category = (
            str(category.get("candidate") or "").split("·", 1)[0].strip()
            if isinstance(category, Mapping)
            else ""
        )
        candidate_match = re.match(r"^([0-9]+)", candidate_category)
        candidate_category = candidate_match.group(1) if candidate_match else ""
        category_ok = (
            candidate_category in expected_categories
            and str(category.get("evidence_digest") or "").startswith("sha256:")
            and (
                not frozen_decision_digest
                or str(category.get("evidence_digest") or "")
                == frozen_decision_digest
            )
            and str(category.get("authority") or "").strip() != ""
        )
        _check(checks, errors, "DURABLE_CATEGORY_DECISION_MATCH", category_ok,
               f"{label} 的类目决策未绑定到规则内类目和证据摘要。",
               target_label=label)
        price = facts.get("price") if isinstance(facts, Mapping) else None
        rows = price.get("sku_prices") if isinstance(price, Mapping) else None
        rows = rows if isinstance(rows, list) else []
        price_ok = bool(rows) and len(rows) == len(snapshot_skus)
        v2_formula_results: list[bool] = []
        for row in rows:
            if not isinstance(row, Mapping):
                price_ok = False
                continue
            model = str(row.get("model_sku") or "")
            frozen = snapshot_skus.get(model, {}).get("prices", {}).get(label)
            calculation = row.get("calculation")
            calculation = calculation if isinstance(calculation, Mapping) else {}
            result_price = calculation.get("result")
            preview = calculation.get("derived_preview")
            calculated = (
                result_price.get("list_price") if isinstance(result_price, Mapping)
                else preview.get("local_original_price") if isinstance(preview, Mapping) and preview.get("local_original_price") is not None
                else preview.get("price_cny") if isinstance(preview, Mapping)
                else None
            )
            price_ok = price_ok and (
                isinstance(frozen, Mapping)
                and _decimal(frozen.get("amount")) == _decimal(row.get("amount"))
                and str(frozen.get("currency") or "") == str(row.get("currency") or "")
                and _decimal(calculated) == _decimal(row.get("amount"))
                and bool(str(calculation.get("kind") or ""))
            )
            formula_match = _independent_price_formula_matches(calculation)
            if formula_match is False and _legacy_target_override_is_exact(
                snapshot=snapshot,
                workbench=workbench,
                target_label=label,
                amount=row.get("amount"),
                currency=row.get("currency"),
            ):
                formula_match = True
            if formula_match is not None:
                v2_formula_results.append(formula_match)
        _check(checks, errors, "FROZEN_PRICE_RESULT_MATCH", bool(price_ok),
               f"{label} 的冻结售价与交接计算结果不一致或缺少计算类型。",
               target_label=label)
        if v2_formula_results:
            _check(
                checks,
                errors,
                "INDEPENDENT_PRICE_FORMULA_RECOMPUTED",
                len(v2_formula_results) == len(rows) and all(v2_formula_results),
                f"{label} 的新版售价公式无法由独立计算器复算，或部分 SKU 仍缺少新版计算契约。",
                target_label=label,
            )

    passed = sum(row["status"] == "PASSED" for row in checks)
    failed = sum(row["status"] == "FAILED" for row in checks)
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "status": "FAILED" if errors else "PASSED",
        "checks": checks,
        "errors": errors,
        "summary": {"passed": passed, "failed": failed, "not_available": 0},
    }


__all__ = [
    "EVIDENCE_SCHEMA",
    "evaluate_publication_quality_evidence",
    "load_publication_quality_evidence",
]
