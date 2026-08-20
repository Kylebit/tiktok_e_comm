#!/usr/bin/env python3
"""Build a first-review publication packet with zero external writes.

Miaoshou synchronization belongs exclusively to the second-round image skill.
Legacy execution flags remain parseable only so older callers receive a clear,
deterministic failure instead of silently crossing the first-round boundary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class PreparationError(RuntimeError):
    """A deterministic preparation or write-boundary failure."""


PreviewBuilder = Callable[[str], dict[str, Any]]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _unique_text(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(value for raw in values if (value := _clean_text(raw))))


def _selection_key(value: Any) -> str:
    raw = _clean_text(value).lower().replace("-", "_")
    aliases = {
        "miaoshou:common": "miaoshou_common",
        "ozon:ru": "ozon_ru",
        "shopee:ph": "shopee_ph",
        "shopee:my": "shopee_my",
        "shopee:th": "shopee_th",
        "shopee:vn": "shopee_vn",
        "tiktok:mx": "mx",
        "tiktok:gb": "gb",
    }
    if raw in aliases:
        return aliases[raw]
    if raw.startswith("tiktok:"):
        return raw.split(":", 1)[1]
    return raw.replace(":", "_")


def _safe_skus(source: dict[str, Any], review: dict[str, Any]) -> list[dict[str, Any]]:
    selected = set(_unique_text(review.get("selected_sku_keys") or []))
    labels = review.get("sku_label_overrides") if isinstance(review.get("sku_label_overrides"), dict) else {}
    rows: list[dict[str, Any]] = []
    for raw in source.get("skus") or []:
        if not isinstance(raw, dict):
            continue
        source_key = _clean_text(raw.get("key") or raw.get("name"))
        if selected and source_key not in selected:
            continue
        rows.append(
            {
                "source_key": source_key,
                "seller_sku": _clean_text(raw.get("seller_sku") or raw.get("item_num")),
                "approved_display_name": _clean_text(labels.get(source_key)),
            }
        )
    return rows


def _safe_dashboard_skus(product: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in product.get("source_skus") or []:
        if not isinstance(raw, dict):
            continue
        commercial = raw.get("commercial_facts") if isinstance(raw.get("commercial_facts"), dict) else {}
        rows.append(
            {
                "source_key": _clean_text(raw.get("key")),
                "seller_sku": _clean_text(raw.get("model_sku")),
                "approved_display_name": _clean_text(raw.get("label") or raw.get("name")),
                "cost_cny": commercial.get("cost_cny"),
                "weight_kg": commercial.get("weight_kg"),
                "package_cm": list(commercial.get("package_cm") or []),
            }
        )
    return rows


def _safe_product_facts(preview: dict[str, Any]) -> dict[str, Any]:
    product = preview.get("product") if isinstance(preview.get("product"), dict) else {}
    if product:
        return {
            "title": _clean_text(product.get("title")),
            "seller_sku": _clean_text(product.get("seller_sku_candidate")),
            "category_semantic": product.get("category") if isinstance(product.get("category"), dict) else _clean_text(product.get("category")),
            "cost_cny": product.get("cost_cny"),
            "weight_kg": product.get("weight_kg"),
            "package_cm": list(product.get("package_cm") or []),
            "skus": _safe_dashboard_skus(product),
            "source_image_count": len(((preview.get("content") or {}).get("images") or [])),
        }
    source = preview.get("source") if isinstance(preview.get("source"), dict) else {}
    review = preview.get("review") if isinstance(preview.get("review"), dict) else {}
    return {
        "title": _clean_text(review.get("title") or source.get("title_recommended") or source.get("title_source")),
        "seller_sku": _clean_text(review.get("seller_sku") or source.get("seller_sku")),
        "category_semantic": _clean_text(review.get("category") or source.get("category")),
        "cost_cny": review.get("cost_cny", source.get("cost_cny")),
        "weight_kg": review.get("weight_kg", source.get("weight_kg")),
        "package_cm": list(review.get("package_cm") or source.get("package_cm") or []),
        "skus": _safe_skus(source, review),
        "source_image_count": len(source.get("images") or []),
    }


def _pricing_rows(preview: dict[str, Any]) -> list[dict[str, Any]]:
    modern = preview.get("pricing_review") if isinstance(preview.get("pricing_review"), dict) else {}
    target_pricing = modern.get("target_pricing") if isinstance(modern.get("target_pricing"), dict) else {}
    if target_pricing:
        rows: list[dict[str, Any]] = []
        for target, raw in target_pricing.items():
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row["id"] = _clean_text(target)
            derived = row.get("derived_preview") if isinstance(row.get("derived_preview"), dict) else {}
            row.setdefault("list_price", derived.get("amount") or derived.get("price"))
            row.setdefault("currency", derived.get("currency"))
            rows.append(row)
        return rows
    pricing = preview.get("pricing") if isinstance(preview.get("pricing"), dict) else {}
    rows: list[dict[str, Any]] = []
    for raw in pricing.get("sea") or []:
        if isinstance(raw, dict):
            rows.append(raw)
    for key in ("mx", "uk", "ozon"):
        raw = pricing.get(key)
        if isinstance(raw, dict) and raw:
            row = dict(raw)
            row.setdefault("id", key)
            rows.append(row)
    return rows


def _safe_target_facts(preview: dict[str, Any], requested_targets: list[str]) -> list[dict[str, Any]]:
    prices = {_selection_key(row.get("id") or row.get("target_id") or row.get("region")): row for row in _pricing_rows(preview)}
    facts: list[dict[str, Any]] = []
    for requested in requested_targets:
        key = _selection_key(requested)
        price = prices.get(key) or {}
        facts.append(
            {
                "target": requested,
                "selection_key": key,
                "category": {"status": "AGENT_RESOLUTION_REQUIRED", "candidate": None},
                "price": {
                    "amount": price.get("list_price"),
                    "currency": price.get("currency"),
                    "source": "product_center_rule_pricing" if price else None,
                },
                "copy": {"status": "AGENT_GENERATION_REQUIRED"},
            }
        )
    return facts


def _collect_blockers(preview: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    workflow = preview.get("workflow") if isinstance(preview.get("workflow"), dict) else {}
    facts = preview.get("product_facts") if isinstance(preview.get("product_facts"), dict) else {}
    blockers.extend(_unique_text(workflow.get("blockers") or []))
    blockers.extend(_unique_text(facts.get("blockers") or []))
    product = preview.get("product") if isinstance(preview.get("product"), dict) else {}
    fact_evidence = product.get("fact_evidence") if isinstance(product.get("fact_evidence"), dict) else {}
    blockers.extend(_unique_text(fact_evidence.get("blockers") or []))
    if preview.get("ok") is not True:
        blockers.append("Product Center preview is not ready")
    return _unique_text(blockers)


def _safe_image_execution_plan(value: Any) -> dict[str, Any]:
    """Validate one agent-proposed, user-reviewable image plan.

    The packet deliberately contains positions and language routes only.  It
    must not persist provider URLs, generation payloads or hidden OCR output.
    """
    if value is None:
        return {
            "schema_version": "first-review-image-plan/v1",
            "status": "USER_DECISION_REQUIRED",
            "source_actions": [],
            "generated_assets": [],
            "summary": {
                "translation_positions": [],
                "localized_output_count": 0,
                "net_new_output_count": 0,
                "paid_generation_required": False,
            },
        }
    if not isinstance(value, dict):
        raise PreparationError("image execution plan must be an object")
    if value.get("schema_version") != "first-review-image-plan/v1":
        raise PreparationError("image execution plan schema is invalid")
    if value.get("status") not in {"PROPOSED", "APPROVED", "SKIPPED"}:
        raise PreparationError("image execution plan status is invalid")
    source_actions = value.get("source_actions")
    generated_assets = value.get("generated_assets")
    summary = value.get("summary")
    if not isinstance(source_actions, list) or len(source_actions) > 50:
        raise PreparationError("image source actions must be a bounded list")
    if not isinstance(generated_assets, list) or len(generated_assets) > 20:
        raise PreparationError("generated image assets must be a bounded list")
    if not isinstance(summary, dict):
        raise PreparationError("image execution plan summary is required")
    seen_positions: set[int] = set()
    clean_actions: list[dict[str, Any]] = []
    for raw in source_actions:
        if not isinstance(raw, dict):
            raise PreparationError("image source action must be an object")
        position = raw.get("position")
        action = _clean_text(raw.get("action")).upper()
        languages = _unique_text(raw.get("target_languages") or [])
        output_count = raw.get("output_count")
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or not 1 <= position <= 50
            or position in seen_positions
        ):
            raise PreparationError("image source positions must be unique positive integers")
        if action not in {"KEEP", "TRANSLATE", "REMOVE", "REFERENCE"}:
            raise PreparationError("image source action is invalid")
        if isinstance(output_count, bool) or not isinstance(output_count, int) or output_count < 0:
            raise PreparationError("image output_count must be a non-negative integer")
        if action == "TRANSLATE" and output_count != len(languages):
            raise PreparationError("translated output_count must equal target language count")
        seen_positions.add(position)
        clean_actions.append(
            {
                "position": position,
                "action": action,
                "original_language": _clean_text(raw.get("original_language")),
                "target_languages": languages,
                "output_count": output_count,
                "reason": _clean_text(raw.get("reason"))[:240],
            }
        )
    clean_summary = {
        "translation_positions": [
            int(position) for position in (summary.get("translation_positions") or [])
        ],
        "localized_output_count": int(summary.get("localized_output_count") or 0),
        "net_new_output_count": int(summary.get("net_new_output_count") or 0),
        "paid_generation_required": summary.get("paid_generation_required") is True,
    }
    clean_plan = {
        "schema_version": "first-review-image-plan/v1",
        "status": value["status"],
        "source_actions": clean_actions,
        "generated_assets": [dict(row) for row in generated_assets if isinstance(row, dict)],
        "summary": clean_summary,
    }
    if clean_plan != value:
        raise PreparationError("image execution plan contains unsupported or inconsistent fields")
    return clean_plan


def _build_release_dashboard_with_bootstrap(
    offer_id: str,
    *,
    dashboard_builder: Callable[..., dict[str, Any]] | None = None,
    bootstrapper: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Create missing local workbench state through the existing read-only import."""
    if dashboard_builder is None:
        from shared_platform.release_control import build_release_dashboard

        dashboard_builder = build_release_dashboard
    if bootstrapper is None:
        from modules.products.server import _collect_product_workspace_locally

        def collect(offer: str) -> None:
            status, result = _collect_product_workspace_locally({"offer_id": offer})
            if status not in {200, 201} or result.get("ok") is not True:
                raise PreparationError(
                    _clean_text(result.get("error") or "Product Center collection failed")
                )

        bootstrapper = collect
    try:
        return dashboard_builder(offer_id=offer_id)
    except FileNotFoundError:
        bootstrapper(offer_id)
        return dashboard_builder(offer_id=offer_id)


def _default_preview_builder() -> PreviewBuilder:
    def dashboard(offer_id: str) -> dict[str, Any]:
        return _build_release_dashboard_with_bootstrap(offer_id)

    return dashboard


def prepare_offer(
    *,
    offer_id: str,
    requested_targets: list[str],
    execute_miaoshou: bool = False,
    confirm_miaoshou_write: bool = False,
    skip_miaoshou: bool = False,
    image_execution_plan: dict[str, Any] | None = None,
    preview_builder: PreviewBuilder | None = None,
) -> dict[str, Any]:
    """Return the safe first-review packet without provider mutations."""
    clean_offer_id = _clean_text(offer_id)
    targets = _unique_text(requested_targets)
    if not clean_offer_id:
        raise PreparationError("offer_id is required")
    if not targets:
        raise PreparationError("at least one target store is required")
    if execute_miaoshou or confirm_miaoshou_write:
        raise PreparationError("Miaoshou synchronization belongs to the second round")

    preview_builder = preview_builder or _default_preview_builder()

    preview = preview_builder(clean_offer_id)
    if not isinstance(preview, dict):
        raise PreparationError("Product Center preview returned an invalid shape")

    review = preview.get("review") if isinstance(preview.get("review"), dict) else {}
    publication_scope = preview.get("publication_scope") if isinstance(preview.get("publication_scope"), dict) else {}
    selected_sites = _unique_text(publication_scope.get("selected_labels") or review.get("selected_sites") or [])
    selected_keys = {_selection_key(value) for value in selected_sites}
    missing_targets = [target for target in targets if _selection_key(target) not in selected_keys]
    blockers = _collect_blockers(preview)
    if missing_targets:
        blockers.append(
            "requested targets are not selected in Product Center: "
            + ", ".join(missing_targets)
        )

    blockers = _unique_text(blockers)
    safe_image_plan = _safe_image_execution_plan(image_execution_plan)
    translation_positions = list(
        safe_image_plan["summary"].get("translation_positions") or []
    )
    packet: dict[str, Any] = {
        "schema": "publication-preparation-decision/v1",
        "offer_id": clean_offer_id,
        "product_center_revision": max(0, int((((preview.get("product") or {}).get("revision")) if isinstance(preview.get("product"), dict) else None) or preview.get("revision") or 0)),
        "status": (
            "DECISION_REQUIRED"
            if blockers
            else "FIRST_REVIEW_READY"
        ),
        "target_selection": {
            "requested": targets,
            "selected_in_product_center": selected_sites,
            "missing_from_product_center": missing_targets,
        },
        "product_facts": _safe_product_facts(preview),
        "targets": _safe_target_facts(preview, targets),
        "image_decisions": {
            "translation_status": (
                "PROPOSED_FOR_USER_REVIEW"
                if safe_image_plan["status"] == "PROPOSED"
                else safe_image_plan["status"]
            ),
            "translation_positions": translation_positions,
            "note": "Do not use OCR to choose images; the user selects positions during first review.",
        },
        "image_execution_plan": safe_image_plan,
        "content_groups": {
            "status": "USER_DECISION_REQUIRED",
            "groups": [],
            "note": "Do not assume LivelyHive/HomeBloom dual content; the user selects groups during first review.",
        },
        "blockers": blockers,
        "miaoshou_sync": {
            "status": "DEFERRED_TO_SECOND_ROUND",
            "written_to_miaoshou": False,
            "verified": False,
            "claimed": False,
            "published": False,
            "change_summary": {},
        },
        "external_write_count": 0,
        "request_attempted": False,
        "readback_verified": False,
    }

    return packet


def _parse_targets(raw: str) -> list[str]:
    return _unique_text(raw.split(","))


def _default_output_path(offer_id: str) -> Path:
    return (
        REPO_ROOT
        / "reports"
        / "product-preparation"
        / _clean_text(offer_id)
        / "first-review.json"
    )


def _write_text_atomic(path: Path, text_value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text_value, encoding="utf-8")
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--targets", required=True, help="Comma-separated exact target labels")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute-miaoshou", action="store_true", help="Legacy flag; rejected because first round is zero-write")
    parser.add_argument("--confirm-miaoshou-write", action="store_true", help="Legacy flag; rejected because first round is zero-write")
    parser.add_argument("--skip-miaoshou", action="store_true", help="Legacy no-op; first round always defers Miaoshou")
    parser.add_argument(
        "--image-plan",
        type=Path,
        help="Validated first-review image plan JSON; performs no image generation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        image_plan = (
            json.loads(args.image_plan.read_text(encoding="utf-8"))
            if args.image_plan
            else None
        )
        packet = prepare_offer(
            offer_id=args.offer_id,
            requested_targets=_parse_targets(args.targets),
            execute_miaoshou=args.execute_miaoshou,
            confirm_miaoshou_write=args.confirm_miaoshou_write,
            skip_miaoshou=args.skip_miaoshou,
            image_execution_plan=image_plan,
        )
    except Exception as exc:
        error = {
            "schema": "publication-preparation-error/v1",
            "status": "FAILED",
            "kind": type(exc).__name__,
            "reason": _clean_text(exc)[:240],
            "external_write_count": 0,
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        return 2

    rendered = json.dumps(packet, ensure_ascii=False, indent=2)
    output = args.output or _default_output_path(args.offer_id)
    _write_text_atomic(output, rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
