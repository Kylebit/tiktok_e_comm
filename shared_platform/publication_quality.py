"""Deterministic product-family quality gates for final publication review.

The module consumes only a frozen publication snapshot and machine-readable
knowledge.  It performs no provider calls and returns bounded Chinese errors
instead of leaking exceptions or hidden reasoning into the workspace UI.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = ROOT / "skills" / "prepare-product-publication" / "references"
QUALITY_SCHEMA = "publication-quality-gate/v1"


class PublicationQualityContractError(ValueError):
    """Raised when a maintained rule pack is structurally invalid."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def load_product_family_pack(product_family: str) -> dict[str, Any]:
    name = str(product_family or "").strip().casefold()
    if name not in {"wallpaper", "wall_sticker"}:
        raise PublicationQualityContractError("不支持的商品类目规则包")
    path = PACK_ROOT / f"product-family-{name.replace('_', '-')}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PublicationQualityContractError("商品类目规则包无法读取") from error
    return validate_product_family_pack(document)


def validate_product_family_pack(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicationQualityContractError("商品类目规则包必须是对象")
    document = json.loads(_canonical_json(value))
    if (
        document.get("schema_version") != "product-family-rule-pack/v1"
        or document.get("language") != "zh-CN"
        or document.get("product_family") not in {"wallpaper", "wall_sticker"}
    ):
        raise PublicationQualityContractError("商品类目规则包身份无效")
    platforms = document.get("platforms")
    if not isinstance(platforms, dict) or set(platforms) != {
        "TIKTOK",
        "SHOPEE",
        "OZON",
    }:
        raise PublicationQualityContractError("商品类目规则包平台范围无效")
    for platform, row in platforms.items():
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("category_ids"), list)
            or not row["category_ids"]
            or any(not isinstance(item, str) or not item for item in row["category_ids"])
        ):
            raise PublicationQualityContractError(f"{platform} 类目规则无效")
    image_policy = document.get("image_policy")
    if (
        not isinstance(image_policy, dict)
        or type(image_policy.get("minimum_images_per_brand")) is not int
        or image_policy["minimum_images_per_brand"] < 1
        or len(image_policy.get("required_roles") or ())
        != image_policy["minimum_images_per_brand"]
    ):
        raise PublicationQualityContractError("商品图片规则无效")
    role_sets = document.get("brand_role_sets")
    if (
        not isinstance(role_sets, dict)
        or set(role_sets) != {"livelyhive-sea", "homebloom-sea"}
        or any(
            not isinstance(roles, list)
            or len(roles) != image_policy["minimum_images_per_brand"]
            or len(roles) != len(set(roles))
            for roles in role_sets.values()
        )
    ):
        raise PublicationQualityContractError("双品牌图片角色规则无效")
    return document


def _category_id(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    nested = value.get("category")
    source = nested if isinstance(nested, Mapping) else value
    return str(source.get("id") or "").strip()


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _error(
    code: str,
    message_zh: str,
    *,
    stage: str,
    target_label: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "stage": stage,
        "severity": "BLOCKING",
        "message_zh": message_zh,
        "target_label": target_label,
    }


def _product_family(snapshot: Mapping[str, Any], pack: Mapping[str, Any]) -> str | None:
    product = snapshot.get("product")
    product = product if isinstance(product, Mapping) else {}
    main = product.get("main_category")
    main = main if isinstance(main, Mapping) else {}
    main_id = str(main.get("id") or "").casefold()
    text = " ".join(
        str(value or "").casefold()
        for value in (main.get("name"), product.get("title"), product.get("description"))
    )
    match = pack["match"]
    if main_id in {str(value).casefold() for value in match["main_category_ids"]}:
        return str(pack["product_family"])
    if any(str(term).casefold() in text for term in match.get("exclude_terms") or ()):
        return None
    if any(str(term).casefold() in text for term in match["semantic_terms"]):
        return str(pack["product_family"])
    return None


def select_product_family_pack(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    packs = [load_product_family_pack(name) for name in ("wall_sticker", "wallpaper")]
    product = snapshot.get("product")
    product = product if isinstance(product, Mapping) else {}
    main = product.get("main_category")
    main = main if isinstance(main, Mapping) else {}
    main_id = str(main.get("id") or "").casefold()
    explicit = [pack for pack in packs if main_id in {str(value).casefold() for value in pack['match']['main_category_ids']}]
    if len(explicit)==1:
        return explicit[0]
    matches = [pack for pack in packs if _product_family(snapshot,pack) is not None]
    if len(explicit)>1 or len(matches)>1:
        raise PublicationQualityContractError("商品同时匹配多个类目规则包，需先明确冻结分类")
    return matches[0] if matches else None


def _target_images(snapshot: Mapping[str, Any], label: str) -> list[str]:
    from domains.product_operations import (
        ApprovedPublicationSnapshotError,
        publication_images_for_target,
    )

    try:
        return list(publication_images_for_target(snapshot, label))
    except ApprovedPublicationSnapshotError:
        return []


def evaluate_publication_quality(
    snapshot: Mapping[str, Any],
    *,
    pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate maintained category, image, claim, locale, and price invariants."""

    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")
    try:
        rules = (
            validate_product_family_pack(pack)
            if pack is not None
            else select_product_family_pack(snapshot)
        )
    except PublicationQualityContractError as error:
        return {
            "schema_version": QUALITY_SCHEMA, "status": "FAILED",
            "product_family": None, "rule_pack_version": None,
            "rule_pack_digest": None, "checks": [],
            "errors": [_error("QUALITY_RULE_SELECTION_REQUIRED", str(error), stage="CATEGORY")],
            "summary": {"passed": 0, "failed": 1, "not_applicable": 0},
        }
    family = _product_family(snapshot, rules) if rules is not None else None
    checks: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if family is None:
        return {
            "schema_version": QUALITY_SCHEMA,
            "status": "NOT_APPLICABLE",
            "product_family": None,
            "rule_pack_version": rules["rule_pack_version"] if rules is not None else None,
            "rule_pack_digest": hashlib.sha256(_canonical_json(rules).encode("utf-8")).hexdigest() if rules is not None else None,
            "checks": [],
            "errors": [],
            "summary": {"passed": 0, "failed": 0, "not_applicable": 1},
        }

    targets = snapshot.get("publication_targets")
    targets = targets if isinstance(targets, list) else []
    categories = snapshot.get("categories_by_target")
    categories = categories if isinstance(categories, Mapping) else {}
    minimum = rules["image_policy"]["minimum_images_per_brand"]
    routing = (
        snapshot.get("product", {}).get("image_routing")
        if isinstance(snapshot.get("product"), Mapping)
        else None
    )
    routes = routing.get("routes") if isinstance(routing, Mapping) else None
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        label = str(target.get("target_label") or "")
        platform = str(target.get("platform") or "").upper()
        control_target = platform == "MIAOSHOU" and label == "miaoshou:COMMON"
        expected = set((rules["platforms"].get(platform) or {}).get("category_ids") or ())
        actual = _category_id(categories.get(label))
        category_row = categories.get(label)
        decision = category_row.get("decision") if isinstance(category_row, Mapping) else None
        deferred = (
            isinstance(decision, Mapping)
            and decision.get("status") == "DEFERRED_TO_SKILL"
            and str(decision.get("decision_digest") or "").startswith("sha256:")
        )
        category_ok = control_target or bool(actual and actual in expected) or deferred
        checks.append({
            "code": (
                "CATEGORY_RULE_NOT_APPLICABLE"
                if control_target
                else (
                    "CATEGORY_RULE_DEFERRED_TO_DURABLE_EVIDENCE"
                    if deferred and not actual
                    else "CATEGORY_RULE_MATCH"
                )
            ),
            "status": "PASSED" if category_ok else "FAILED",
            "target_label": label,
        })
        if not category_ok:
            errors.append(_error(
                "CATEGORY_RULE_MISMATCH",
                f"{label} 的冻结类目 {actual or '缺失'} 不符合{rules['name_zh']}类目规则。",
                stage="CATEGORY",
                target_label=label,
            ))
        images = _target_images(snapshot, label)
        image_ok = len(images) >= minimum and len(images) == len(set(images))
        checks.append({
            "code": "IMAGE_SET_COMPLETE",
            "status": "PASSED" if image_ok else "FAILED",
            "target_label": label,
            "observed_count": len(images),
            "required_count": minimum,
        })
        if not image_ok:
            errors.append(_error(
                "IMAGE_SET_INCOMPLETE",
                f"{label} 至少需要 {minimum} 张不重复图片，当前为 {len(images)} 张。",
                stage="IMAGE_QA",
                target_label=label,
            ))
        if isinstance(routes, Mapping) and isinstance(routes.get(label), Mapping):
            site = str(target.get("site") or "").split("_")[-1].upper()
            expected_locale = rules["target_locales"].get(site)
            actual_locale = str(routes[label].get("locale") or "").casefold()
            expected_locale_text = str(expected_locale or "").casefold()
            locale_ok = (
                not expected_locale_text
                or actual_locale == expected_locale_text
                or actual_locale.startswith(expected_locale_text + "-")
            )
            checks.append({
                "code": "IMAGE_LANGUAGE_ROUTE_MATCH",
                "status": "PASSED" if locale_ok else "FAILED",
                "target_label": label,
                "expected_locale": expected_locale,
                "actual_locale": actual_locale,
            })
            if not locale_ok:
                errors.append(_error(
                    "IMAGE_LANGUAGE_ROUTE_MISMATCH",
                    f"{label} 图片语言应为 {expected_locale}，冻结路由为 {actual_locale or '缺失'}。",
                    stage="LANGUAGE_QA",
                    target_label=label,
                ))

    product = snapshot.get("product")
    product = product if isinstance(product, Mapping) else {}
    copy = f"{product.get('title') or ''}\n{product.get('description') or ''}".casefold()
    verified = product.get("verified_claims")
    verified = verified if isinstance(verified, Mapping) else {}
    for claim, terms in rules["claim_policy"]["terms"].items():
        if not any(str(term).casefold() in copy for term in terms):
            continue
        row = verified.get(claim)
        claim_ok = isinstance(row, Mapping) and row.get("value") is True and row.get("fact_verified") is True
        checks.append({
            "code": "MARKETING_CLAIM_VERIFIED",
            "status": "PASSED" if claim_ok else "FAILED",
            "claim": claim,
        })
        if not claim_ok:
            errors.append(_error(
                "UNVERIFIED_MARKETING_CLAIM",
                f"文案包含尚未冻结事实证据的营销词：{claim}。",
                stage="CLAIM_QA",
            ))

    skus = [row for row in (snapshot.get("skus") or ()) if isinstance(row, Mapping)]
    target_labels = [
        str(row.get("target_label") or "") for row in targets if isinstance(row, Mapping)
    ]
    for label in target_labels:
        comparable: list[tuple[Decimal, Decimal, str]] = []
        for sku in skus:
            cost = sku.get("cost")
            prices = sku.get("prices")
            cost_amount = _decimal(cost.get("amount")) if isinstance(cost, Mapping) else None
            price = prices.get(label) if isinstance(prices, Mapping) else None
            price_amount = _decimal(price.get("amount")) if isinstance(price, Mapping) else None
            if cost_amount is not None and price_amount is not None:
                comparable.append((cost_amount, price_amount, str(sku.get("model_sku") or "")))
        comparable.sort(key=lambda row: row[0])
        price_ok = all(
            comparable[index - 1][1] <= comparable[index][1]
            for index in range(1, len(comparable))
            if comparable[index - 1][0] < comparable[index][0]
        )
        checks.append({
            "code": "VARIANT_PRICE_ORDER",
            "status": "PASSED" if price_ok else "FAILED",
            "target_label": label,
        })
        if not price_ok:
            errors.append(_error(
                "VARIANT_PRICE_ORDER_INVERSION",
                f"{label} 出现采购成本更高但售价更低的变体，必须重新核算。",
                stage="PRICING_QA",
                target_label=label,
            ))

    passed = sum(row["status"] == "PASSED" for row in checks)
    failed = sum(row["status"] == "FAILED" for row in checks)
    return {
        "schema_version": QUALITY_SCHEMA,
        "status": "FAILED" if errors else "PASSED",
        "product_family": family,
        "rule_pack_version": rules["rule_pack_version"],
        "rule_pack_digest": hashlib.sha256(_canonical_json(rules).encode("utf-8")).hexdigest(),
        "checks": checks,
        "errors": errors,
        "summary": {"passed": passed, "failed": failed, "not_applicable": 0},
    }


__all__ = [
    "PublicationQualityContractError",
    "evaluate_publication_quality",
    "load_product_family_pack",
    "select_product_family_pack",
    "validate_product_family_pack",
]
