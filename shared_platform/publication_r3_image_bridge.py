"""R3 image routing and current R2 evidence binding.

Pure route helpers migrated from frozen r4 (see B00_B4B_R2_R3_BINDING.md).
No paid provider, COMMON writer, automatic approval or ReleaseStore mutation.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

REPORTS_ROOT = Path(__file__).resolve().parents[1] / "reports" / "product-preparation"
DEFAULT_STAGE_BASE_URL = 'http://127.0.0.1:8765'


def request_publication_stage(path, *, data=None, base_url=DEFAULT_STAGE_BASE_URL, timeout=15):
    """Thin loopback client shared by the preparation and status CLIs; never retries POST."""
    import ipaddress
    import urllib.error
    import urllib.parse
    import urllib.request
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme != 'http' or parsed.username or parsed.password or parsed.path not in {'', '/'}
            or parsed.query or parsed.fragment or not parsed.hostname
            or not ipaddress.ip_address(parsed.hostname).is_loopback):
        raise ValueError('an explicit loopback HTTP Product Center URL is required')
    if not path.startswith('/api/product-workspace/'):
        raise ValueError('Product Center stage path is required')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError('stage redirects are not permitted')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(base_url.rstrip('/') + path,
        data=None if data is None else json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {'ok': False, 'error': 'PRODUCT_CENTER_TRANSPORT_UNAVAILABLE',
                   'outcome_unknown': data is not None, 'automatic_retry': False}
    with response:
        value = json.load(response)
        if not isinstance(value, dict):
            raise ValueError('stage response must be an object')
        return response.status, value

BRAND_TRANSLATION_LOCALES = {
    "livelyhive-sea": frozenset({"ms-MY", "th-TH", "vi-VN", "ru-RU", "es-MX"}),
    "homebloom-sea": frozenset({"ms-MY", "th-TH", "vi-VN"}),
}

def _brand_family(brand_id: str) -> str:
    """Resolve approval-bound single-market brand aliases to their SEA family."""

    normalized = str(brand_id or "").strip().casefold()
    if normalized == "livelyhive-sea" or normalized.startswith("livelyhive-"):
        return "livelyhive-sea"
    if normalized == "homebloom-sea" or normalized.startswith("homebloom-"):
        return "homebloom-sea"
    return normalized

def _brand_translation_locales(brand_id: str) -> frozenset[str]:
    return BRAND_TRANSLATION_LOCALES.get(_brand_family(brand_id), frozenset())

DUAL_BRAND_PUBLICATION_BRIDGE_SCHEMA = "dual-brand-publication-bridge/v1"

DUAL_BRAND_APPROVAL_AUTHORITIES = {
    "Kyle": "CONVERSATION_APPROVAL",
    "product-publication-autopilot": "ACTIVE_AUTOPILOT_POLICY",
}

TARGET_LOCALE = {
    "tiktok:LH_PH": "en-PH",
    "tiktok:LH_MY": "ms-MY",
    "tiktok:LH_TH": "th-TH",
    "tiktok:LH_VN": "vi-VN",
    "tiktok:HB_PH": "en-PH",
    "tiktok:HB_MY": "ms-MY",
    "tiktok:HB_TH": "th-TH",
    "tiktok:HB_VN": "vi-VN",
    "tiktok:MX": "es-MX",
    "tiktok:GB": "en-GB",
    "shopee:PH": "en-PH",
    "shopee:MY": "ms-MY",
    "shopee:TH": "th-TH",
    "shopee:VN": "vi-VN",
    "ozon:RU": "ru-RU",
}

def _brand_generation_identity_digest(generation: Mapping[str, Any]) -> str:
    assets = [
        {
            "review_number": int(row.get("review_number") or 0),
            "brand_id": str(row.get("brand_id") or ""),
            "role": str(row.get("role") or ""),
            "artifact_digest": str(row.get("artifact_digest") or ""),
            "public_url": str(row.get("public_url") or ""),
        }
        for row in (generation.get("assets") or [])
        if isinstance(row, dict)
    ]
    assets.sort(key=lambda row: row["review_number"])
    planned_count = int(generation.get("planned_image_count") or len(assets))
    if planned_count < 1 or len(assets) != planned_count or [
        row["review_number"] for row in assets
    ] != list(range(1, planned_count + 1)):
        raise ValueError("brand image generation does not match its approved numbered plan")
    for row in assets:
        if (
            not _brand_translation_locales(str(row["brand_id"]))
            or not row["role"]
            or re.fullmatch(r"sha256:[0-9a-f]{64}", row["artifact_digest"]) is None
            or not row["public_url"].startswith("https://")
        ):
            raise ValueError("brand image generation asset identity is incomplete")
    return _canonical_digest({
        "schema_version": "brand-image-generation-identity/v1",
        "offer_id": str(generation.get("offer_id") or ""),
        "assets": assets,
    })

def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def build_dual_brand_publication_bridge(
    *,
    offer_id: str,
    first_review: Mapping[str, Any],
    generation: Mapping[str, Any],
    translation: Mapping[str, Any],
    approved_by: str,
) -> dict[str, Any]:
    """Freeze reviewed dual-brand assets into exact per-target publication routes."""
    clean_offer_id = str(offer_id or "").strip()
    if not clean_offer_id.isdigit():
        raise ValueError("offer_id must contain digits only")
    approval_authority = DUAL_BRAND_APPROVAL_AUTHORITIES.get(approved_by)
    if approval_authority is None:
        raise ValueError(
            "dual-brand publication approval must use a supported approval authority"
        )
    if (
        first_review.get("offer_id") != clean_offer_id
        or first_review.get("status") != "FIRST_REVIEW_READY"
    ):
        raise ValueError("approved first review is missing or belongs to another offer")

    targets = list((first_review.get("target_selection") or {}).get("requested") or [])
    if not targets or len(targets) != len(set(targets)):
        raise ValueError("first-review publication targets are incomplete or duplicated")
    unsupported = [target for target in targets
                   if target not in TARGET_LOCALE and target != 'miaoshou:COMMON']
    if unsupported:
        raise ValueError("first-review publication target is unsupported")
    if not any(target != 'miaoshou:COMMON' for target in targets):
        raise ValueError('first-review publication market targets are missing')
    target_rows = {
        str(row.get("target") or ""): row
        for row in (first_review.get("targets") or [])
        if isinstance(row, Mapping) and str(row.get("target") or "")
    }
    if set(target_rows) != set(targets):
        raise ValueError("first-review target fact coverage changed")
    for target in targets:
        row = target_rows[target]
        if not all(isinstance(row.get(field), Mapping) for field in ("category", "copy", "price")):
            raise ValueError("first-review target facts are incomplete")

    if (
        generation.get("offer_id") != clean_offer_id
        or generation.get("status") != "BRAND_IMAGE_REVIEW_REQUIRED"
    ):
        raise ValueError("completed dual-brand generation report is missing")
    generated = [dict(row) for row in (generation.get("assets") or []) if isinstance(row, Mapping)]
    image_plan = first_review.get("image_execution_plan") or first_review.get(
        "image_plan"
    ) or {}
    expected_roles = {
        str(plan.get("id") or ""): [
            str(asset.get("role") or "")
            for asset in (plan.get("generated_assets") or [])
            if isinstance(asset, Mapping)
        ]
        for plan in image_plan.get("brand_plans") or []
        if isinstance(plan, Mapping)
    }
    if not expected_roles:
        raise ValueError("the frozen R1 image role plan is required")
    expected_count = sum(len(roles) for roles in expected_roles.values())
    expected_families = [_brand_family(brand) for brand in expected_roles]
    if (
        not 1 <= len(expected_roles) <= 2
        or expected_count < len(expected_roles)
        or any(family not in BRAND_TRANSLATION_LOCALES for family in expected_families)
        or len(expected_families) != len(set(expected_families))
        or any(not roles or len(roles) != len(set(roles)) for roles in expected_roles.values())
    ):
        raise ValueError("first-review brand role plan is incomplete")
    if len(generated) != expected_count or any(
        row.get("status") != "COMPLETED" for row in generated
    ):
        raise ValueError("dual-brand generation coverage differs from the approved role plan")
    generated.sort(key=lambda row: int(row.get("review_number") or 0))
    if [int(row.get("review_number") or 0) for row in generated] != list(
        range(1, expected_count + 1)
    ):
        raise ValueError("dual-brand image review numbering is incomplete")
    by_brand: dict[str, list[dict[str, Any]]] = {}
    for row in generated:
        brand = str(row.get("brand_id") or "")
        role = str(row.get("role") or "")
        url = str(row.get("public_url") or "")
        digest = str(row.get("artifact_digest") or "")
        if (
            not _brand_translation_locales(brand)
            or not role
            or not url.startswith("https://")
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
        ):
            raise ValueError("dual-brand generated asset identity is invalid")
        by_brand.setdefault(brand, []).append(row)
    if any(
        [str(row["role"]) for row in by_brand.get(brand, [])] != roles
        for brand, roles in expected_roles.items()
    ):
        raise ValueError("each brand image role must exactly match the approved plan")
    brand_key_by_family: dict[str, str] = {}
    for brand_key in by_brand:
        family = _brand_family(brand_key)
        if family in brand_key_by_family:
            raise ValueError("multiple image sets resolve to the same brand family")
        brand_key_by_family[family] = brand_key

    if (
        translation.get("offer_id") != clean_offer_id
        or translation.get("status") not in {"LOCALIZED_IMAGE_REVIEW_REQUIRED", "NOT_REQUIRED"}
    ):
        raise ValueError("completed localized image report is missing")
    localized = [dict(row) for row in (translation.get("assets") or []) if isinstance(row, Mapping)]
    if len(localized) != int(translation.get("approved_task_count") or 0):
        raise ValueError("localized image receipt count differs from approved task count")
    localized_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    generated_keys = {
        (str(row["brand_id"]), str(row["role"]), int(row["review_number"]))
        for row in generated
    }
    for row in localized:
        key = (
            str(row.get("brand_id") or ""),
            str(row.get("role") or ""),
            str(row.get("locale") or ""),
        )
        source_key = (key[0], key[1], int(row.get("source_review_number") or 0))
        url = str(row.get("public_url") or "")
        digest = str(row.get("artifact_digest") or "")
        if (
            key in localized_by_key
            or source_key not in generated_keys
            or row.get("status") != "COMPLETED"
            or key[2] not in _brand_translation_locales(key[0])
            or not url.startswith("https://")
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
        ):
            raise ValueError("localized image identity or coverage is invalid")
        localized_by_key[key] = row

    approved_translation_keys = {
        (
            str(row.get("brand_id") or ""),
            str(row.get("role") or ""),
            str(row.get("locale") or ""),
        )
        for row in (translation.get("approved_tasks") or [])
        if isinstance(row, Mapping)
    }
    approved_task_count = int(translation.get("approved_task_count") or 0)
    if approved_task_count and not approved_translation_keys:
        raise ValueError("approved translation task identities are missing")
    if len(approved_translation_keys) != approved_task_count:
        raise ValueError("approved translation task identities differ from approved task count")
    if set(localized_by_key) != approved_translation_keys:
        raise ValueError("localized image coverage does not match the approved translation scope")

    translation_mode = (
        "LOCALIZED_WHERE_APPROVED"
        if approved_translation_keys
        else "LANGUAGE_NEUTRAL_MASTER_NO_TRANSLATION_REQUIRED"
    )

    image_routes: dict[str, list[dict[str, Any]]] = {}
    route_locales: dict[str, str] = {}
    # COMMON is an already-bound technical baseline, not a market locale.
    # Preserve the complete frozen scope and target facts in the identity.
    for target in (label for label in targets if label != 'miaoshou:COMMON'):
        brand = "homebloom-sea" if target.startswith("tiktok:HB_") else "livelyhive-sea"
        asset_brand = brand_key_by_family.get(brand)
        if not asset_brand:
            raise ValueError(f"approved image set is missing for {brand}")
        locale = TARGET_LOCALE[target]
        route_locales[target] = locale
        ordered: list[dict[str, Any]] = []
        for position, master in enumerate(by_brand[asset_brand], start=1):
            route_key = (asset_brand, str(master["role"]), locale)
            localized_asset = localized_by_key.get(route_key)
            if route_key in approved_translation_keys and localized_asset is None:
                raise ValueError("translation coverage is incomplete for publication route")
            selected = localized_asset or master
            ordered.append({
                "position": position,
                "brand_id": brand,
                "role": master["role"],
                "kind": "LOCALIZED_ARTIFACT" if localized_asset else "APPROVED_MASTER",
                "translation_mode": (
                    "LOCALIZED_ARTIFACT"
                    if localized_asset
                    else "LANGUAGE_NEUTRAL_MASTER"
                ),
                "url": selected["public_url"],
                "artifact_id": selected.get("artifact_id"),
                "artifact_digest": selected["artifact_digest"],
                "source_review_number": int(master["review_number"]),
            })
        image_routes[target] = ordered

    livelyhive_asset_brand = brand_key_by_family.get("livelyhive-sea")
    if not livelyhive_asset_brand:
        raise ValueError("LivelyHive common baseline is missing")
    baseline = [
        {
            "position": position,
            "brand_id": "livelyhive-sea",
            "role": row["role"],
            "url": row["public_url"],
            "artifact_id": row.get("artifact_id"),
            "artifact_digest": row["artifact_digest"],
        }
        for position, row in enumerate(by_brand[livelyhive_asset_brand], start=1)
    ]
    identity = {
        "schema_version": DUAL_BRAND_PUBLICATION_BRIDGE_SCHEMA,
        "offer_id": clean_offer_id,
        "targets": targets,
        "route_locales": route_locales,
        "common_miaoshou_baseline": baseline,
        "image_routes": image_routes,
        "target_facts": {target: dict(target_rows[target]) for target in targets},
        "generation_identity_digest": _brand_generation_identity_digest(generation),
        "translation_plan_digest": translation.get("plan_digest"),
        "translation_mode": translation_mode,
        "approved_translation_task_count": approved_task_count,
        "approved_by": approved_by,
        "approval_authority": approval_authority,
    }
    return {
        **identity,
        "bridge_digest": _canonical_digest(identity),
        "status": "MIAOSHOU_SYNC_REQUIRED",
        "approval_status": "APPROVED",
        "target_count": len(targets),
        "route_count": len(image_routes),
        "common_baseline_image_count": len(baseline),
        "platform_writes": 0,
        "miaoshou_writes": 0,
        "product_center_mutated": False,
    }


R2_DOCUMENTS = {
    "round1_snapshot": "round1-approved-snapshot.json",
    "first_review": "first-review.json",
    "generation_result": "brand-image-generation.json",
    "translation_plan": "brand-image-translation-plan.json",
    "translation_result": "brand-image-translation.json",
    "image_qa": "automated-image-qa.json",
}


def _validated_r2_identity(documents: Mapping[str, Any]) -> dict[str, Any]:
    """Verify actual S02 output identities without approving a new business action."""
    if not all(isinstance(documents.get(key), Mapping) for key in R2_DOCUMENTS):
        raise ValueError("R2_IDENTITY_DOCUMENTS_REQUIRED")
    r1, first, generation, plan, translated, qa = (
        documents[key] for key in R2_DOCUMENTS
    )
    unsigned = dict(r1)
    r1_digest = unsigned.pop("snapshot_digest", None)
    offer = r1.get("offer_id")
    actor = r1.get("approved_by")
    approved = actor == "Kyle" or (
        actor == "product-publication-autopilot"
        and r1.get("approval_authority") == "ACTIVE_AUTOPILOT_POLICY"
        and r1.get("human_approval") is False
    )
    first_image_plan = deepcopy(first.get("image_execution_plan") or first.get("image_plan"))
    if isinstance(first_image_plan,dict) and first_image_plan.get("status") in {"PROPOSED","APPROVED"}:
        # build_round1_snapshot approves exactly this plan without changing its content.
        first_image_plan["status"] = "APPROVED"
    if (
        r1.get("schema_version") != "round1-approved-snapshot/v1"
        or r1.get("status") != "APPROVED" or not approved
        or type(offer) is not str or not re.fullmatch(r"[0-9]{1,32}", offer)
        or r1_digest != _canonical_digest(unsigned)
        or any(row.get("offer_id") != offer for row in (first,generation,plan,translated,qa))
        or r1.get("first_review_digest") != _canonical_digest(first)
        or first.get("product_facts") != (r1.get("fact_snapshot") or {}).get("product_facts")
        or first_image_plan != r1.get("image_plan")
    ):
        raise ValueError("R2_ROUND1_IDENTITY_CONFLICT")
    targets = r1.get("canonical_targets")
    requested = (first.get("target_selection") or {}).get("requested")
    target_rows = first.get("targets")
    if (
        not isinstance(targets,list) or not targets
        or any(type(label) is not str for label in targets)
        or len(targets) != len(set(targets))
        or requested != targets or not isinstance(target_rows,list)
        or len(target_rows) != len(targets)
        or any(not isinstance(row,Mapping) for row in target_rows)
        or sorted(row.get("target","") for row in target_rows) != sorted(targets)
    ):
        raise ValueError("R2_TARGET_IDENTITY_CONFLICT")
    master_rows = generation.get("assets")
    if (
        not isinstance(master_rows,list) or not master_rows
        or any(not isinstance(row,Mapping) or type(row.get("review_number")) is not int
               for row in master_rows)
        or ("planned_image_count" in generation and type(generation["planned_image_count"]) is not int)
    ):
        raise ValueError("R2_GENERATION_NUMBERING_INVALID")
    generation_digest = _brand_generation_identity_digest(generation)
    plan_authorities = {
        "Kyle":("APPROVED_IN_CONVERSATION","USER_FINAL_IMAGE_REVIEW"),
        "orbit-product-publication-default-v1":("APPROVED_BY_AUTOPILOT","ACTIVE_AUTOPILOT_POLICY"),
    }
    if plan_authorities.get(plan.get("approved_by")) != (plan.get("status"),plan.get("approval_authority")):
        raise ValueError("R2_TRANSLATION_APPROVAL_CONFLICT")
    if (
        generation.get("schema_version") != "brand-image-generation/v1"
        or generation.get("round1_snapshot_digest") != r1_digest
        or plan.get("schema_version") != "brand-image-translation-plan/v1"
        or plan.get("status") not in {"APPROVED_IN_CONVERSATION","APPROVED_BY_AUTOPILOT"}
        or plan.get("generation_identity_digest") != generation_digest
        or translated.get("schema_version") != "brand-image-translation/v1"
        or translated.get("generation_identity_digest") != generation_digest
        or translated.get("plan_digest") != _canonical_digest(plan)
    ):
        raise ValueError("R2_GENERATION_PLAN_IDENTITY_CONFLICT")
    tasks = plan.get("tasks")
    assets = translated.get("assets")
    if (
        not isinstance(tasks,list) or not isinstance(assets,list)
        or type(plan.get("approved_task_count")) is not int
        or plan["approved_task_count"] != len(tasks)
        or translated.get("approved_tasks") != tasks
        or translated.get("approved_task_count") != len(tasks)
        or len(assets) != len(tasks)
    ):
        raise ValueError("R2_TRANSLATION_COVERAGE_CONFLICT")
    masters = {row["review_number"]:row for row in generation["assets"]}
    keys = set()
    for task in tasks:
        if not isinstance(task,Mapping):
            raise ValueError("R2_TRANSLATION_TASK_INVALID")
        key = (task.get("review_number"),task.get("brand_id"),task.get("role"),task.get("locale"))
        source = masters.get(key[0])
        if (
            key in keys or source is None
            or task.get("brand_id") != source.get("brand_id")
            or task.get("role") != source.get("role")
            or task.get("source_url") != source.get("public_url")
            or task.get("source_artifact_digest") != source.get("artifact_digest")
        ):
            raise ValueError("R2_TRANSLATION_SOURCE_CONFLICT")
        keys.add(key)
    asset_keys = [
        (row.get("source_review_number"),row.get("brand_id"),row.get("role"),row.get("locale"))
        for row in assets if isinstance(row,Mapping)
    ]
    if len(asset_keys) != len(assets) or len(asset_keys) != len(set(asset_keys)) or set(asset_keys) != keys:
        raise ValueError("R2_TRANSLATION_ASSET_CONFLICT")
    all_assets = list(generation["assets"]) + assets
    if any(row.get("status") != "COMPLETED" for row in all_assets):
        raise ValueError("R2_ASSET_NOT_COMPLETED")
    expected_artifacts = sorted({row.get("artifact_digest") for row in all_assets})
    if not all(type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}",value) for value in expected_artifacts):
        raise ValueError("R2_ARTIFACT_DIGEST_INVALID")
    qa_unsigned = dict(qa)
    qa_digest = qa_unsigned.pop("qa_digest",None)
    checks = qa.get("checks")
    required = {"ROLE_COVERAGE","FACTUAL_ALIGNMENT","OCR_LANGUAGE","DUPLICATION","TARGET_ROUTING"}
    if (
        qa.get("schema_version") != "automated-image-qa/v1" or qa.get("status") != "PASSED"
        or qa_digest != _canonical_digest(qa_unsigned)
        or qa.get("round1_snapshot_digest") != r1_digest
        or qa.get("generation_identity_digest") != generation_digest
        or qa.get("artifact_digests") != expected_artifacts
        or qa.get("generated_asset_count") != len(generation["assets"])
        or qa.get("localized_asset_count") != len(assets)
        or not isinstance(checks,list)
        or not required.issubset({row.get("code") for row in checks
                                 if isinstance(row,Mapping) and row.get("status") == "PASSED"})
        or any(isinstance(row,Mapping) and row.get("status") == "FAILED" for row in checks)
    ):
        raise ValueError("R2_QA_IDENTITY_CONFLICT")
    # The existing route builder validates roles, locales, exact task coverage and HTTPS assets.
    build_dual_brand_publication_bridge(offer_id=offer, first_review=first,
        generation=generation, translation=translated, approved_by=actor)
    identity = {
        "schema_version":"publication-r2-identity/v1", "offer_id":offer,
        "round1_snapshot_digest":r1_digest, "first_review_digest":_canonical_digest(first),
        "generation_identity_digest":generation_digest, "generation_digest":_canonical_digest(generation),
        "translation_plan_digest":_canonical_digest(plan), "translation_result_digest":_canonical_digest(translated),
        "qa_digest":qa_digest, "artifact_digests":expected_artifacts,
    }
    return dict(identity,identity_digest=_canonical_digest(identity))


def validate_r2_identity(documents: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return _validated_r2_identity(documents)
    except (TypeError,KeyError,AttributeError) as error:
        raise ValueError("R2_DOCUMENT_CONTRACT_INVALID") from error


def prepare_common_stage_dashboard(
    dashboard: Mapping[str, Any], documents: Mapping[str, Any]
) -> dict[str, Any]:
    """Project current R1/R2 facts for COMMON review; confer no write authority.

    The existing server still recomputes source identity and validates the SKU
    reservation. Marketplace preflight and a final v4 snapshot are deliberately
    later stages, after COMMON's official readback.
    """
    from decimal import Decimal, InvalidOperation

    identity = validate_r2_identity(documents)
    r1, first = documents['round1_snapshot'], documents['first_review']
    product = dashboard.get('product') or {}
    approved = product.get('actual_approval') or {}
    facts = first.get('product_facts') or {}
    category = product.get('category')
    category_name = category.get('name') if isinstance(category, dict) else category
    category_semantic = category_name.strip() if isinstance(category_name, str) else ''
    frozen_category = facts.get('category_semantic')
    frozen_category_name = frozen_category.get('name') if isinstance(frozen_category, dict) else frozen_category
    frozen_category_semantic = frozen_category_name.strip() if isinstance(frozen_category_name, str) else ''

    def number(value):
        try:
            parsed = Decimal(str(value))
            if not parsed.is_finite() or parsed <= 0:
                raise ValueError
            return parsed
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError('COMMON_R1_COMMERCIAL_FACTS_REQUIRED') from None

    if (
        product.get('offer_id') != r1['offer_id']
        or product.get('actual_product_approved') is not True
        or not approved.get('package_id')
        or not r1.get('product_approval_id')
        or not r1.get('product_approval_fingerprint')
        or approved.get('approval_id') != r1['product_approval_id']
        or approved.get('input_fingerprint') != r1['product_approval_fingerprint']
        or type(product.get('revision')) is not int
        or product['revision'] < r1['approved_product_center_revision']
        or not facts.get('title') or not facts.get('seller_sku')
        or product.get('title') != facts['title']
        or product.get('seller_sku_candidate') != facts['seller_sku']
        or not category_semantic
        or not frozen_category_semantic
        or category_semantic != frozen_category_semantic
    ):
        raise ValueError('COMMON_R1_CURRENT_PRODUCT_IDENTITY_CONFLICT')
    for field in ('cost_cny', 'weight_kg'):
        if number(product.get(field)) != number(facts.get(field)):
            raise ValueError('COMMON_R1_COMMERCIAL_FACTS_CONFLICT: ' + field)
    if len(facts.get('package_cm') or []) != 3 or (
        [number(v) for v in product.get('package_cm') or []]
        != [number(v) for v in facts['package_cm']]
    ):
        raise ValueError('COMMON_R1_COMMERCIAL_FACTS_CONFLICT: package_cm')
    selected = product.get('selected_sku_keys')
    reviewed = facts.get('skus')
    if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
        or not isinstance(reviewed, list) or len(reviewed) != len(selected)):
        raise ValueError('COMMON_R1_SELECTED_SKUS_REQUIRED')
    current_rows = [row for row in product.get('source_skus') or [] if row.get('key') in selected]
    by_key = {row.get('key'): row for row in current_rows}
    reviewed_by_key = {row.get('source_key'): row for row in reviewed}
    if len(by_key) != len(current_rows) or set(by_key) != set(selected) or set(reviewed_by_key) != set(selected):
        raise ValueError('COMMON_R1_SELECTED_SKU_IDENTITY_CONFLICT')
    for key in selected:
        current, frozen = by_key[key], reviewed_by_key[key]
        commercial = (product.get('sku_commercial_facts') or {}).get(key) or {}
        if (current.get('model_sku') != frozen.get('seller_sku')
            or current.get('label') != frozen.get('approved_display_name')
            or (product.get('sku_label_overrides') or {}).get(key, current.get('label')) != frozen.get('approved_display_name')):
            raise ValueError('COMMON_R1_SELECTED_SKU_IDENTITY_CONFLICT')
        for field in ('cost_cny', 'weight_kg'):
            if number(commercial.get(field)) != number(frozen.get(field)):
                raise ValueError('COMMON_R1_SKU_COMMERCIAL_FACTS_CONFLICT')
        if len(frozen.get('package_cm') or []) != 3 or (
            [number(v) for v in commercial.get('package_cm') or []]
            != [number(v) for v in frozen['package_cm']]
        ):
            raise ValueError('COMMON_R1_SKU_COMMERCIAL_FACTS_CONFLICT')
    route = build_dual_brand_publication_bridge(
        offer_id=r1['offer_id'], first_review=first,
        generation=documents['generation_result'], translation=documents['translation_result'],
        approved_by=r1['approved_by'],
    )
    # COMMON's baseline is LivelyHive English. Do not silently borrow another
    # brand or a non-English storefront's text when that scope is absent.
    english = [row for row in first['targets'] if row['target'] in {'tiktok:LH_PH','shopee:PH'}]
    if not english or not all(str(english[0]['copy'].get(k) or '').strip() for k in ('title','description')):
        raise ValueError('COMMON_FROZEN_LIVELYHIVE_ENGLISH_COPY_REQUIRED')
    copy = english[0]['copy']
    result = deepcopy(dict(dashboard))
    result['product']['title'] = copy['title']
    result['product']['revision'] = r1['approved_product_center_revision']
    result['publication_scope'] = {'selected_labels': ['miaoshou:COMMON']}
    result['content'] = {
        'approved': True, 'approval_status': 'R2_IMAGE_PREPARATION_VERIFIED',
        'package_id': 'r2-content:' + _canonical_digest(identity)[7:31],
        'strategy': 'R3_COMMON_FROZEN_R2_BASELINE',
        'images': [{**row, 'image_url': row['url'], 'audit_id': identity['qa_digest'],
                    'asset_type': 'generated', 'decision_source': 'CURRENT_R1_R2_QA'}
                   for row in route['common_miaoshou_baseline']],
        'video_urls': [],
    }
    result['listing_copy'] = {
        'schema_version': 'r3-common-frozen-copy/v1', 'status': 'R1_FACTS_APPROVED',
        'input_signature': r1['snapshot_digest'], 'current_input_signature': r1['snapshot_digest'],
        'semantic_master_en': copy['title'], 'shopee_description_en': copy['description'],
        'provider': 'frozen_round1', 'policy_version': 'r3-common-frozen-copy/v1',
        'model': '', 'candidates': [],
    }
    result['pricing_review'] = {'status': 'ready', 'schema_version': 'common-no-marketplace-pricing/v1',
                                'target_pricing': {'miaoshou:COMMON': {'status': 'NOT_APPLICABLE'}}}
    result['omnichannel_preview'] = {'available': True, 'plan_id': 'r3-common:preview', 'targets': [], 'blockers': []}
    result.pop('_approved_publication_snapshot_inputs', None)
    return result


def common_stage_plan_id(payload: Mapping[str, Any], *, offer_id: str) -> str:
    """Recompute the existing COMMON producer ID from its bound R1/R2 identity."""
    binding = payload.get('r3_stage_binding')
    if (not isinstance(binding, Mapping)
            or binding.get('schema_version') != 'r3-common-stage/v1'
            or binding.get('execution_scope') != ['miaoshou:COMMON']
            or payload.get('targets') != ['miaoshou:COMMON']
            or binding.get('image_approval_scope') != 'ROUND2_IMAGES_ONLY'
            or binding.get('write_approval_source') != 'ReleaseStore'
            or type(offer_id) is not str or not re.fullmatch(r'[0-9]{1,32}', offer_id)
            or payload.get('product_id') != offer_id):
        raise ValueError('COMMON_PLAN_IDENTITY_INVALID')
    identity = binding.get('r2_identity')
    if (not isinstance(identity, Mapping)
            or identity.get('schema_version') != 'publication-r2-identity/v1'
            or identity.get('offer_id') != offer_id
            or identity.get('round1_snapshot_digest') != binding.get('round1_snapshot_digest')):
        raise ValueError('COMMON_PLAN_SOURCE_IDENTITY_INVALID')
    identity_facts = dict(identity)
    if identity_facts.pop('identity_digest', None) != _canonical_digest(identity_facts):
        raise ValueError('COMMON_PLAN_SOURCE_IDENTITY_INVALID')
    lineage = payload.get('sku_lineage')
    if not isinstance(lineage, Mapping) or not isinstance(lineage.get('assignment'), Mapping):
        raise ValueError('COMMON_PLAN_LINEAGE_INVALID')
    scope = deepcopy(dict(payload))
    scope.pop('plan_id', None)
    scope['sku_lineage'] = lineage['assignment']
    return 'r3-common:' + offer_id + ':' + _canonical_digest(scope)[7:31]


def bind_common_stage_payload(payload: Mapping[str, Any], documents: Mapping[str, Any], *, store, preparation_source=None) -> dict[str, Any]:
    """Seal COMMON scope in an ordinary ReleasePlan, retaining the same ledger."""
    identity = validate_r2_identity(documents)
    result = deepcopy(dict(payload))
    r1 = documents['round1_snapshot']
    result['r3_stage_binding'] = {
        'schema_version': 'r3-common-stage/v1', 'execution_scope': ['miaoshou:COMMON'],
        'image_approval_scope': 'ROUND2_IMAGES_ONLY', 'write_approval_source': 'ReleaseStore',
        'round1_snapshot_digest': r1['snapshot_digest'], 'r2_identity': identity,
        'marketplace_targets': list(r1['canonical_targets']),
    }
    if preparation_source is not None:
        from shared_platform.native_common_budget_facts import NativePreparationSource, validate_preparation_source
        if type(preparation_source) is not NativePreparationSource:
            raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_REQUIRED')
        validate_preparation_source(store, preparation_source)
        source = preparation_source.payload()
        if (source['offer_id'] != r1['offer_id']
                or source['round1_snapshot_digest'] != r1['snapshot_digest']
                or source['targets'] != sorted(r1['canonical_targets'])
                or source['execution_authority'] is not False):
            raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_CONFLICT')
        result['r3_stage_binding']['native_preparation_source'] = source
    result['product_facts']['source_offer_id'] = (result.get('source_product_identity') or {}).get('source_offer_id')
    lineage = result.get('sku_lineage') or {}
    # Operational lineage mode changes to INHERITED after this plan is stored.
    # The scope hash retains the full actual source and exact assignment; an
    # existing reservation is reused only after both are independently equal.
    result['plan_id'] = common_stage_plan_id(result, offer_id=r1['offer_id'])
    existing = store.get_plan(result['plan_id'])
    if existing:
        old = existing['payload']
        if (old.get('source_product_identity') != result.get('source_product_identity')
            or (old.get('sku_lineage') or {}).get('assignment') != lineage.get('assignment')):
            raise ValueError('COMMON_EXISTING_SOURCE_RESERVATION_CONFLICT')
        result['sku_lineage'] = deepcopy(old.get('sku_lineage'))
    return result


def _report_path(offer_id: str, name: str, *, reports_root: Path | None = None) -> Path:
    if type(offer_id) is not str or not re.fullmatch(r"[0-9]{1,32}",offer_id):
        raise ValueError("offer_id must contain 1 to 32 ASCII digits")
    root = reports_root or REPORTS_ROOT
    if reports_root is None:
        from shared_platform.publication_r2_review import has_registration, registered_project, review_runtime_root
        runtime_root = review_runtime_root(REPORTS_ROOT.parent.parent)
        if has_registration(offer_id, runtime_root=runtime_root):
            root = registered_project(offer_id, runtime_root=runtime_root) / 'reports/product-preparation'
    path = root / offer_id / name
    # Before exists/read/mkdir: dangling links must not turn into a missing record.
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("R3_REPORT_PATH_ESCAPES_ROOT")
    return path


def load_r2_documents(offer_id: str, *, reports_root: Path | None = None,
                      native_review_reader=None) -> dict[str, Any]:
    from shared_platform.publication_r2_review import require_adopted_assets
    if native_review_reader is not None:
        from shared_platform.workbench_publication_native import NativeR2PreparedReviewReader
        if type(native_review_reader) is not NativeR2PreparedReviewReader:
            raise ValueError('NATIVE_R2_PREPARED_READER_REQUIRED')
    effective_root = _report_path(offer_id, 'round1-approved-snapshot.json', reports_root=reports_root).parent.parent
    require_adopted_assets(offer_id, effective_root, None)
    result = {}
    for key,name in R2_DOCUMENTS.items():
        path = _report_path(offer_id,name,reports_root=reports_root)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError) as error:
            raise ValueError(f"R2_DOCUMENT_UNAVAILABLE:{name}") from error
        if not isinstance(document,dict):
            raise ValueError(f"R2_DOCUMENT_INVALID:{name}")
        result[key] = document
    if native_review_reader is not None:
        result['first_review'] = native_review_reader.read_verified(
            offer_id, effective_root, result['first_review'], result['round1_snapshot'])
    require_adopted_assets(offer_id, effective_root, result)
    validate_r2_identity(result)
    return result


def _persist_dual_brand_publication_bridge(
    offer_id: str, *, approved_by: str | None = None, reports_root: Path | None = None,
) -> dict[str, Any]:
    """Persist preparation evidence only; never infer COMMON/platform write authority."""
    documents = load_r2_documents(offer_id,reports_root=reports_root)
    r1 = documents["round1_snapshot"]
    if approved_by is not None and approved_by != r1["approved_by"]:
        raise ValueError("R3_APPROVAL_ACTOR_CONFLICT")
    bridge = build_dual_brand_publication_bridge(offer_id=offer_id,
        first_review=documents["first_review"],generation=documents["generation_result"],
        translation=documents["translation_result"],approved_by=r1["approved_by"])
    bridge["r2_identity"] = validate_r2_identity(documents)
    # Seal the immutable route plus actual R2 lineage. No implicit approval occurs.
    bridge["bridge_digest"] = _canonical_digest({
        "route_digest":bridge["bridge_digest"], "r2_identity":bridge["r2_identity"],
    })
    bridge["status"] = "COMMON_STAGE_PLAN_REQUIRED"
    path = _report_path(offer_id,"dual-brand-publication-handoff.json",reports_root=reports_root)
    encoded = json.dumps(bridge,ensure_ascii=False,sort_keys=True,indent=2)+"\n"
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open("x",encoding="utf-8",newline="\n") as handle:
            handle.write(encoded)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError("R3_BRIDGE_CONFLICT: preserve the prior handoff; do not overwrite it")
    return deepcopy(bridge)


class R3StageAdapterRequired(ValueError):
    """A missing backend stage, not an invitation to bypass its existing store."""


def _frozen_shopee_master_source(prices, r1):
    """Restore legacy master selection metadata without recalculating prices."""
    from domains.channel_operations.pricing_preview import MASTER_SITE_ORDER
    shopee = [label for label in prices if label.startswith('shopee:')]
    if not shopee:
        return None
    master = next((f'shopee:{site}' for site in MASTER_SITE_ORDER if f'shopee:{site}' in shopee), None)
    if master is None:
        raise ValueError('FROZEN_SHOPEE_MASTER_REGION_REQUIRED')
    source = prices[master].get('source') or {}
    if not source.get('target_key') or source.get('region') != master.split(':')[1]:
        raise ValueError('FROZEN_SHOPEE_MASTER_SOURCE_REQUIRED')
    return {**deepcopy(source), 'provenance': {
        'kind': 'derived_from_frozen_selections',
        'round1_snapshot_digest': r1['snapshot_digest'],
        'selection_policy': 'channel-pricing-preview/v1:MASTER_SITE_ORDER',
        'master_site_order': list(MASTER_SITE_ORDER),
        'price_recalculated': False,
    }}


def _marketplace_category_candidate(label, category, *, first_review, common_payload,
                                    category_store=None, category_connection=None):
    """Project the native Shopee receipt shape without changing frozen facts.

    The older proposed candidate shape stays unchanged. Native evidence-bound
    rows need the original fixed-store provenance and all R1 target/context
    checks, not a receipt copied from JSON or a category from another platform.
    """
    if not isinstance(category, Mapping) or category.get('status') != 'EVIDENCE_BOUND':
        return category
    if (not label.startswith('shopee:') or type(category) is not dict
            or set(category) != {'status', 'id', 'name', 'receipt_digest'}):
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_SCOPE_INVALID')
    from shared_platform.release_store import ReleaseStore
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    if type(category_store) is not ReleaseStore or not category_store.path.is_file():
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_SERVICE_STORE_REQUIRED')
    if category_connection is None:
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_TRANSACTION_REQUIRED')
    NativeCommonSourceReader(category_store).validate_context(category_connection)
    from shared_platform.publication_rounds import validate_round1_reviewable
    receipt = (first_review.get('category_evidence_binding') or {}).get('receipt') or {}
    expected_account = receipt.get('account_identity_digest')
    if (common_payload.get('r3_stage_binding') or {}).get('native_preparation_source') is not None:
        from shared_platform.native_common_budget_facts import _validate_plan_source
        source = _validate_plan_source(category_connection, common_payload)
        if (source['offer_id'] != first_review['offer_id']
                or source['targets'] != sorted(first_review['target_selection']['requested'])):
            raise ValueError('MARKETPLACE_BOUND_CATEGORY_SOURCE_CHANGED')
        expected_account = source['account_identity_digest']
    def resolve(reference):
        observed = category_store.round1_category_observation(reference, connection=category_connection)
        if observed is None or observed.get('account_identity_digest') != expected_account:
            raise ValueError('MARKETPLACE_BOUND_CATEGORY_ACCOUNT_CHANGED')
        return observed
    checked = validate_round1_reviewable(first_review, report_directory=REPORTS_ROOT,
        category_observation_resolver=resolve)
    receipt = checked['category_evidence_binding']['receipt']
    row = next((row for row in checked['targets'] if row['target'] == label), None)
    if (row is None or row['category'] != category or label not in receipt['requested_targets']
            or category['id'] != receipt['category']['id']
            or category['name'] != receipt['category']['name']
            or category['receipt_digest'] != receipt['receipt_digest']):
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_PROJECTION_CHANGED')
    return {**category, 'candidate': str(category['id']) + ' · ' + category['name'],
            'evidence_digest': category['receipt_digest'], 'authority': receipt['authority']}


def _native_marketplace_category_source(payload, *, category_store, category_connection):
    """Resolve the original COMMON source in the consuming read transaction."""
    from shared_platform.release_store import ReleaseStore
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    if type(category_store) is not ReleaseStore:
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_SERVICE_STORE_REQUIRED')
    NativeCommonSourceReader(category_store).validate_context(category_connection)
    binding = payload['r3_marketplace_binding']
    row = category_connection.execute(
        'SELECT product_id,payload_json,payload_digest FROM release_plans WHERE plan_id=?',
        (binding['common_plan_id'],)).fetchone()
    if row is None or row['product_id'] != payload['product_id'] or row['payload_digest'] != binding['common_payload_digest']:
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_COMMON_SOURCE_CHANGED')
    common_payload = json.loads(row['payload_json'])
    if _canonical_digest(common_payload).removeprefix('sha256:') != row['payload_digest']:
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_COMMON_BYTES_CHANGED')
    first_review = binding['documents']['first_review']
    round1 = binding['documents']['round1_snapshot']
    if (first_review['offer_id'] != payload['product_id']
            or first_review['product_center_revision'] != round1['reviewed_product_center_revision']
            or payload['product_revision'] != round1['approved_product_center_revision']):
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_COMMON_SOURCE_CHANGED')
    return first_review, common_payload


def _native_shopee_global_category(payload, *, category_store, category_connection):
    """Derive inert category/selected-attribute facts, without a channel approval."""
    first_review, common_payload = _native_marketplace_category_source(payload,
        category_store=category_store, category_connection=category_connection)
    labels = [label for label in payload['targets'] if label.startswith('shopee:')]
    for label in labels:
        category = payload['r3_marketplace_binding']['route']['target_facts'][label]['category']
        _marketplace_category_candidate(label, category, first_review=first_review,
            common_payload=common_payload, category_store=category_store,
            category_connection=category_connection)
    receipt = first_review['category_evidence_binding']['receipt']
    if not labels or sorted(labels) != sorted(receipt['requested_targets']):
        raise ValueError('MARKETPLACE_BOUND_CATEGORY_GLOBAL_SCOPE_CHANGED')
    source = receipt['category']
    decision = {'status': 'EVIDENCE_BOUND', 'category': {
        'id': str(source['id']), 'name': source['name'],
        'path': [{'id': str(node['id']), 'name': node['name']} for node in source['path']]},
        'required_attributes': deepcopy(receipt['selected_attributes']),
        'source_decision_digest': receipt['receipt_digest']}
    decision['decision_digest'] = _canonical_digest({
        'schema_version': 'shopee-global-category-decision/v1', **decision})
    return decision


def build_marketplace_stage_payload(
    documents,
    common_plan,
    common_run,
    *,
    policy,
    incidents,
    promotion_policy=None,
    ozon_stock_decision=None,
    category_store=None,
    category_connection=None,
):
    """Project frozen commercial facts after the caller verifies the durable COMMON receipt."""
    if category_connection is None and any(
            (row.get('category') or {}).get('status') == 'EVIDENCE_BOUND'
            for row in documents['first_review'].get('targets') or []):
        from shared_platform.release_store import ReleaseStore
        if type(category_store) is not ReleaseStore or not category_store.path.is_file():
            raise ValueError('MARKETPLACE_BOUND_CATEGORY_SERVICE_STORE_REQUIRED')
        with category_store._connect_readonly() as category_db:
            category_db.execute('BEGIN')
            return build_marketplace_stage_payload(documents, common_plan, common_run,
                policy=policy, incidents=incidents, promotion_policy=promotion_policy,
                ozon_stock_decision=ozon_stock_decision, category_store=category_store,
                category_connection=category_db)
    from domains.product_operations import build_approved_publication_snapshot_inputs
    from shared_platform.approved_publication_snapshot_projection import project_release_plan_for_publication_snapshot

    identity = validate_r2_identity(documents)
    r1 = documents['round1_snapshot']
    route = build_dual_brand_publication_bridge(offer_id=r1['offer_id'], first_review=documents['first_review'],
        generation=documents['generation_result'], translation=documents['translation_result'], approved_by=r1['approved_by'])
    payload = deepcopy(common_plan['payload'])
    target = next(row for row in common_run['targets'] if row['target_label'] == 'miaoshou:COMMON')
    if (payload.get('r3_stage_binding', {}).get('r2_identity') != identity
            or common_run['plan_id'] != common_plan['plan_id']):
        raise ValueError('COMMON_MARKETPLACE_IDENTITY_CONFLICT')
    payload.pop('r3_stage_binding')
    # The original R1/R2 documents retain COMMON and its complete binding.
    # This derived plan has only market actions; COMMON already ran separately.
    from shared_platform.release_store import _target_labels
    payload['targets'] = list(_target_labels(
        [label for label in r1['canonical_targets'] if label != 'miaoshou:COMMON']))
    if promotion_policy is not None:
        payload['approved_postpublish_promotion_policy'] = deepcopy(promotion_policy)
    payload['r3_marketplace_binding'] = {
        'schema_version': 'r3-marketplace-stage/v1', 'common_plan_id': common_plan['plan_id'],
        'common_payload_digest': common_plan['payload_digest'], 'common_run_id': common_run['run_id'],
        'common_readback': deepcopy(target['readback']), 'r2_identity': identity,
        'documents': deepcopy(documents), 'route': route, 'policy': deepcopy(policy),
        'incident_registry': deepcopy(incidents), 'approval_source': 'ReleaseStore',
        'final_candidate_contract_version': 'stable-final-review/v1',
    }
    payload['publication_business_snapshot_schema_version'] = (
        'publication-business-snapshot/v1'
    )
    facts = payload['product_facts']
    categories, prices, copies, image_routes = {}, {}, {}, {}
    for label in payload['targets']:
        frozen = route['target_facts'][label]
        category = _marketplace_category_candidate(label, frozen['category'],
            first_review=documents['first_review'], common_payload=common_plan['payload'],
            category_store=category_store, category_connection=category_connection)
        match = re.fullmatch(r'([0-9]+)(?:\s*/\s*type\s*([0-9]+))?\s*·\s*(.+)', str(category.get('candidate') or ''))
        if not match or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(category.get('evidence_digest') or '')):
            raise ValueError('MARKETPLACE_CATEGORY_EVIDENCE_REQUIRED: ' + label)
        category_id, type_id, category_name = match.groups()
        platform, site = label.split(':', 1)
        categories[label] = {'target_label': label, 'platform': platform, 'site': site, 'store': site,
            'category': {'id': category_id, 'name': category_name, 'path': [{'id': category_id, 'name': category_name}]},
            'decision': {'status': 'APPROVED', 'decision_digest': category['evidence_digest']}}
        rows = []
        for row in frozen['price'].get('sku_prices') or []:
            value = {'model_sku': row.get('model_sku'), 'list_price': row.get('amount'), 'currency': row.get('currency')}
            if label.startswith('shopee:'):
                value['global_original_price_cny'] = ((row.get('calculation') or {}).get('derived_preview') or {}).get('global_original_price_cny')
            if label == 'ozon:RU':
                value['old_price_cny'] = row.get('old_price')
            rows.append(value)
        prices[label] = {'sku_prices': rows, 'source': {'target_key': frozen.get('selection_key'), 'region': site}}
        # Keep the v2 discount consumer bound to the exact frozen R1 calculation.
        # It requires a single target row; differing SKU prices remain unavailable,
        # never collapsed to the first SKU or replaced with historical defaults.
        store_prices = []
        for row in frozen['price'].get('sku_prices') or []:
            calculation = row.get('calculation') or {}
            derived = calculation.get('derived_preview') or {}
            if calculation.get('discount_rate_percent') is None or derived.get('sale_price') is None:
                store_prices = []
                break
            value = {'currency': row.get('currency'), 'list_price': row.get('amount'),
                'discount_reserve_pct': calculation['discount_rate_percent'], 'sale_after_discount': derived['sale_price']}
            if value not in store_prices:
                store_prices.append(value)
        if store_prices:
            prices[label]['store_prices'] = store_prices
        copies[label] = {'title': frozen['copy'].get('title'), 'description': frozen['copy'].get('description'),
                         'locale': route['route_locales'][label]}
        image_routes[label] = {'locale': route['route_locales'][label],
                              'ordered_images': [row['url'] for row in route['image_routes'][label]]}
    facts['categories_by_target'] = categories
    facts['content_by_target'] = copies
    facts['stock_policy'] = deepcopy(r1['publication_stock_policy'])
    if 'ozon:RU' in payload['targets']:
        if not isinstance(ozon_stock_decision, Mapping):
            raise ValueError('OZON_EXACT_STOCK_WAREHOUSE_DECISION_REQUIRED')
        facts['ozon_stock_decision'] = deepcopy(dict(ozon_stock_decision))
    payload['pricing'] = {'schema_version': 'approved-target-pricing/v1', 'selected_targets': prices}
    master_source = _frozen_shopee_master_source(prices, r1)
    if master_source is not None:
        payload['pricing']['master_price_source'] = master_source
    payload['localized_image_routing'] = {
        'schema_version': 'localized-publication-images/v1', 'approval_digest': route['bridge_digest'],
        'supplement_digest': _canonical_digest(image_routes), 'source_snapshot_digest': identity['generation_identity_digest'],
        'routes': image_routes,
    }
    dashboard = {
        'product': {'actual_product_approved': True, 'offer_id': payload['product_id'],
            'revision': payload['product_revision'], 'actual_approval': {'package_id': payload['product_package_id']},
            'title': facts['title'], 'category': facts['category'], 'selected_sku_keys': facts['selected_sku_keys'],
            'source_skus': facts['selected_skus'], 'sku_commercial_facts': facts['sku_commercial_facts'],
            'stock_policy': deepcopy(facts['stock_policy']),
            **({'ozon_stock_decision': deepcopy(facts['ozon_stock_decision'])}
               if 'ozon_stock_decision' in facts else {})},
        'content': {'approved': True, 'package_id': payload['content_package_id'], 'images': payload['images']},
        'listing_copy': payload['listing_copy'], 'publication_scope': {'selected_labels': payload['targets']},
    }
    native_category_bound = any(row.get('category', {}).get('status') == 'EVIDENCE_BOUND'
        for row in documents['first_review']['targets'] if row['target'].startswith('shopee:'))
    inputs = build_approved_publication_snapshot_inputs(dashboard=dashboard, release_plan_payload=payload,
        native_category_store=category_store if native_category_bound else None,
        native_category_connection=category_connection if native_category_bound else None)
    projection = project_release_plan_for_publication_snapshot(payload, approved_inputs=inputs)
    if not projection.ready:
        raise ValueError('MARKETPLACE_V4_FACTS_REQUIRED: ' + str(projection.missing_fields))
    payload = projection.payload
    payload.pop('plan_id', None)
    payload['plan_id'] = 'r3-marketplace:' + r1['offer_id'] + ':' + _canonical_digest(payload)[7:31]
    return payload


def build_marketplace_review_material(documents, common_plan, common_run, *,
                                      policy, incidents, promotion_policy=None,
                                      ozon_stock_decision=None, category_store=None,
                                      category_connection=None):
    """Rebuild the complete inert original-page candidate from domain sources.

    This is not COMMON authority: the consumer must independently verify its
    durable readback and Offer-wide budget. No target subset is accepted here.
    The digest envelope is identical to the existing original-page compiler.
    """
    from domains.product_operations.approved_publication_snapshot import (
        build_publication_preview, build_publication_business_snapshot,
    )
    from shared_platform import publication_autopilot as compiler
    if category_connection is None and any(
            row.get('category', {}).get('status') == 'EVIDENCE_BOUND'
            for row in documents['first_review']['targets']):
        from shared_platform.release_store import ReleaseStore
        if type(category_store) is not ReleaseStore or not category_store.path.is_file():
            raise ValueError('MARKETPLACE_BOUND_CATEGORY_SERVICE_STORE_REQUIRED')
        with category_store._connect_readonly() as category_db:
            category_db.execute('BEGIN')
            return build_marketplace_review_material(documents, common_plan, common_run,
                policy=policy, incidents=incidents, promotion_policy=promotion_policy,
                ozon_stock_decision=ozon_stock_decision, category_store=category_store,
                category_connection=category_db)
    payload = build_marketplace_stage_payload(
        documents, common_plan, common_run, policy=policy, incidents=incidents,
        promotion_policy=promotion_policy, ozon_stock_decision=ozon_stock_decision,
        category_store=category_store, category_connection=category_connection)
    snapshot = build_publication_preview(payload)
    evidence = marketplace_quality_evidence(snapshot, payload,
        category_store=category_store, category_connection=category_connection)
    candidate = compiler.compile_release_preview(
        snapshot, policy=policy, incident_registry=incidents,
        durable_evidence=evidence)
    if candidate.get('status') != 'READY_FOR_FINAL_REVIEW':
        raise ValueError('DOMAIN_FINAL_CANDIDATE_BLOCKED: ' + json.dumps(
            candidate.get('blockers') or [], ensure_ascii=False, sort_keys=True))
    material = dict(candidate)
    material.pop('candidate_digest')
    material.pop('snapshot_digest')
    payload['r3_marketplace_binding']['reviewed_candidate_facts'] = material
    snapshot = build_publication_preview(payload)
    business = build_publication_business_snapshot(payload)
    candidate = {**material, 'snapshot_digest': business['business_snapshot_digest']}
    candidate['candidate_digest'] = compiler._canonical_digest(candidate)
    return {'payload': payload, 'snapshot': snapshot, 'candidate': candidate}


def marketplace_quality_evidence(snapshot, payload, *, category_store=None, category_connection=None):
    """Rebuild derived quality evidence exclusively from an immutable bound payload."""
    from shared_platform.publication_preflight import build_platform_preflight, build_platform_preview_preflight
    binding = payload['r3_marketplace_binding']
    documents = deepcopy(binding['documents'])
    identity = validate_r2_identity(documents)
    if identity != binding['r2_identity']:
        raise ValueError('MARKETPLACE_FROZEN_R2_IDENTITY_CONFLICT')
    digest = snapshot.get('snapshot_digest') or snapshot.get('preview_digest')
    route = deepcopy(binding['route'])
    rich_labels = [label for label, facts in route['target_facts'].items()
                   if facts.get('category', {}).get('status') == 'EVIDENCE_BOUND']
    if rich_labels:
        first_review, common_payload = _native_marketplace_category_source(payload,
            category_store=category_store, category_connection=category_connection)
        expected_route = build_dual_brand_publication_bridge(
            offer_id=identity['offer_id'], first_review=documents['first_review'],
            generation=documents['generation_result'], translation=documents['translation_result'],
            approved_by=documents['round1_snapshot']['approved_by'])
        if route != expected_route:
            raise ValueError('MARKETPLACE_BOUND_CATEGORY_FROZEN_ROUTE_CHANGED')
        global_decision = (snapshot.get('shopee_global_master') or {}).get('category_decision') or {}
        legacy_binding = (common_payload.get('approved_channel_category_decisions') or {}).get('shopee:GLOBAL')
        legacy_record = (common_payload.get('_channel_category_decision_records') or {}).get('shopee:GLOBAL')
        if legacy_binding is not None or legacy_record is not None:
            from domains.product_operations.approved_publication_snapshot_inputs import _shopee_global_category_and_policy
            expected_global, _ = _shopee_global_category_and_policy(common_payload)
        else:
            expected_global = _native_shopee_global_category(payload,
                category_store=category_store, category_connection=category_connection)
        if global_decision != expected_global:
            raise ValueError('MARKETPLACE_BOUND_CATEGORY_GLOBAL_FACTS_CHANGED')
        for label in rich_labels:
            route['target_facts'][label]['category'] = _marketplace_category_candidate(
                label, route['target_facts'][label]['category'], first_review=first_review,
                common_payload=common_payload, category_store=category_store,
                category_connection=category_connection)
    release = {'status': 'FACTS_PREPARED', 'plan_id': payload['plan_id'], 'snapshot_digest': digest,
               'round1_snapshot_digest': identity['round1_snapshot_digest']}
    route.update(status='MIAOSHOU_VERIFIED', approval_status='IMAGE_PREPARATION_VERIFIED',
        r2_identity=identity, release_handoff=release,
        miaoshou_sync={'verified': True, 'published': False},
        common_stage={'schema_version': 'r3-common-evidence/v1', 'plan_id': binding['common_plan_id'],
                      'run_id': binding['common_run_id'], 'payload_digest': binding['common_payload_digest'],
                      'readback': deepcopy(binding['common_readback'])})
    documents.update(publication_bridge=route, workflow_handoff={'offer_id': payload['product_id'], **release})
    preflight = build_platform_preview_preflight if snapshot.get('status') == 'NOT_APPROVED' else build_platform_preflight
    documents['platform_preflight'] = preflight(snapshot, evidence=documents)
    return documents


def sync_dual_brand_miaoshou_baseline(
    offer_id: str, *, plan_id: str | None = None, confirmation_token: str | None = None,
    confirm_miaoshou_write: bool = False, base_url: str = DEFAULT_STAGE_BASE_URL,
    reports_root: Path | None = None,
) -> dict[str, Any]:
    """Review COMMON by default; dispatch only the exact already approved Store plan."""
    if reports_root is not None:
        raise ValueError('R3_LOCAL_REPORTS_NOT_AUTHORITY: use the Product Center stage service')
    data = {'offer_id': offer_id, 'release_stage': 'R3_COMMON', 'publication_targets': ['miaoshou:COMMON']}
    path = '/api/product-workspace/r3-common/preview'
    if confirm_miaoshou_write or plan_id or confirmation_token:
        if confirm_miaoshou_write is not True or not plan_id or not confirmation_token:
            raise ValueError('exact approved COMMON plan/token and write confirmation are required')
        data.update(plan_id=plan_id, confirmation_token=confirmation_token, confirm_miaoshou_write=True)
        path = '/api/product-workspace/miaoshou-draft/commit'
    status, result = request_publication_stage(path, data=data, base_url=base_url)
    return dict(result, http_status=status)


def finalize_dual_brand_release_handoff(
    offer_id: str, *, base_url: str = DEFAULT_STAGE_BASE_URL, reports_root: Path | None = None,
) -> dict[str, Any]:
    """Return the full unapproved marketplace review from verified COMMON evidence."""
    if reports_root is not None:
        raise ValueError('R3_LOCAL_REPORTS_NOT_AUTHORITY: use the Product Center stage service')
    status, result = request_publication_stage('/api/product-workspace/r3-marketplace/preview',
        data={'offer_id': offer_id}, base_url=base_url)
    return dict(result, http_status=status)
