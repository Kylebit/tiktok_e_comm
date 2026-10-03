"""Read bounded TikTok SEO words for a first-review product after confirmation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import auth
from core.api_client import post as api_post
from core.shops import list_shops
from domains.content_operations.tiktok_keyword_evidence import (
    build_prelisting_diagnosis_request,
    build_review_workflow,
    inherit_livelyhive_sea_evidence,
    parse_diagnosis_response,
)
from modules.sourcing import new_product_workbench as np_mod


def _review_packet(offer_id: str, *, expected_revision: int) -> dict[str, Any]:
    path = REPO_ROOT / "reports" / "product-preparation" / offer_id / "first-review.json"
    if not path.is_file():
        raise RuntimeError("Current first-review packet is required before keyword read")
    packet = json.loads(path.read_text(encoding="utf-8"))
    if int(packet.get("product_center_revision") or 0) != expected_revision:
        raise RuntimeError("First-review packet revision changed; regenerate before official read")
    return packet


def _tiktok_category_id(packet: dict[str, Any]) -> str:
    candidates = set()
    for row in packet.get("targets") or ():
        if not isinstance(row, dict) or not str(row.get("target") or "").startswith("tiktok:"):
            continue
        category = row.get("category") if isinstance(row.get("category"), dict) else {}
        candidate = str(category.get("candidate") or "").strip()
        head = candidate.split("·", 1)[0].strip()
        if head.isdigit():
            candidates.add(head)
    if len(candidates) != 1:
        raise RuntimeError("One exact shared TikTok leaf category is required before keyword read")
    return candidates.pop()


def _target_config(target: str) -> dict[str, Any] | None:
    target_id = target.split(":", 1)[-1].casefold()
    matches = [row for row in np_mod.SEA_MARKETS if row.get("id") == target_id]
    return dict(matches[0]) if len(matches) == 1 else None


def _official_shop_for_target(
    target: str,
    config: dict[str, Any] | None,
    official_shops: list[dict[str, Any]],
) -> dict[str, Any] | None:
    configured_id = str((config or {}).get("shop_id") or "")
    id_matches = [
        row
        for row in official_shops
        if str(row.get("id") or row.get("shop_id") or "") == configured_id
    ]
    if len(id_matches) == 1:
        return id_matches[0]

    target_id = target.split(":", 1)[-1].casefold()
    if not target_id.startswith("lh_"):
        return None
    region = target_id.rsplit("_", 1)[-1].upper()
    semantic_matches = [
        row
        for row in official_shops
        if "".join(
            character
            for character in str(row.get("name") or row.get("shop_name") or "").casefold()
            if character.isalnum()
        ) == "livelyhive"
        and str(row.get("region") or row.get("country") or "").upper() == region
    ]
    return semantic_matches[0] if len(semantic_matches) == 1 else None


def _target_title(state: dict[str, Any], target: str) -> str:
    listing = state.get("listing_copy") if isinstance(state.get("listing_copy"), dict) else {}
    site = target.rsplit("_", 1)[-1].upper()
    for row in listing.get("candidates") or ():
        if (
            isinstance(row, dict)
            and str(row.get("channel") or "").casefold() == "tiktok"
            and str(row.get("site") or "").upper() == site
        ):
            title = " ".join(str(row.get("title") or "").split()).strip()
            if title:
                return title
    title = " ".join(str(listing.get("semantic_master_en") or "").split()).strip()
    if title:
        return title
    review = state.get("review") if isinstance(state.get("review"), dict) else {}
    return " ".join(str(review.get("title") or "").split()).strip()


def read_keyword_evidence(offer_id: str, *, expected_revision: int) -> dict[str, Any]:
    # Validate identity before state paths, review packets, or credentials are read.
    if not isinstance(offer_id, str) or not (1 <= len(offer_id) <= 32) or not all(
        '0' <= character <= '9' for character in offer_id
    ):
        raise ValueError('offer_id must contain 1 to 32 ASCII digits')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision must be a nonnegative integer, not bool')
    state = np_mod.load_state(offer_id)
    if int(state.get("_revision") or 0) != expected_revision:
        raise RuntimeError("Product Center revision changed; refresh before official read")
    packet = _review_packet(offer_id, expected_revision=expected_revision)
    target_selection = packet.get("target_selection") if isinstance(packet.get("target_selection"), dict) else {}
    targets = [str(value) for value in (target_selection.get("requested") or ())]
    workflow = build_review_workflow(targets)
    category_id = _tiktok_category_id(packet)

    token = auth.access_token()
    official_shops = list_shops(token)
    official_shop_rows = [row for row in official_shops if isinstance(row, dict)]
    observed_at = datetime.now(timezone.utc).isoformat()
    results = []
    follower_targets = []
    for planned in workflow["targets"]:
        target = planned["target_label"]
        if planned["status"] == "FOLLOW_LIVELYHIVE_SEA_EVIDENCE":
            follower_targets.append(target)
            continue
        config = _target_config(target)
        shop = _official_shop_for_target(target, config, official_shop_rows)
        cipher = str((shop or {}).get("cipher") or (shop or {}).get("shop_cipher") or "")
        if not config or not cipher:
            results.append({
                "target_label": target,
                "status": "BLOCKED_AUTH",
                "reason_code": "official_shop_authorization_unavailable",
            })
            continue
        request = build_prelisting_diagnosis_request(
            target_label=target,
            shop_cipher=cipher,
            category_id=category_id,
            title=_target_title(state, target),
        )
        response = api_post(
            request["path"],
            token,
            request["query"],
            request["body"],
        )
        try:
            results.append(parse_diagnosis_response(
                response,
                target_label=target,
                category_id=category_id,
                observed_at=observed_at,
            ))
        except ValueError:
            results.append({
                "target_label": target,
                "status": "BLOCKED_CAPABILITY",
                "reason_code": "official_keyword_read_rejected",
            })

    inherited = inherit_livelyhive_sea_evidence(results, follower_targets)
    by_target = {
        str(row.get("target_label") or ""): row
        for row in [*results, *inherited]
        if isinstance(row, dict)
    }
    ordered_results = [
        by_target[row["target_label"]]
        for row in workflow["targets"]
        if row["target_label"] in by_target
    ]
    evidence = {
        **workflow,
        "status": "OFFICIAL_READ_ATTEMPTED",
        "targets": ordered_results,
        "observed_at": observed_at,
        "external_write_count": 0,
    }
    next_state = dict(state)
    next_state["tiktok_keyword_evidence"] = evidence
    saved = np_mod.save_state(offer_id, next_state)
    return {
        "schema_version": "tiktok-keyword-read-result/v1",
        "offer_id": offer_id,
        "revision": int(saved.get("_revision") or 0),
        "evidence": evidence,
        "external_write_count": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--confirm-official-read", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_official_read:
        parser.error("--confirm-official-read is required")
    result = read_keyword_evidence(
        args.offer_id,
        expected_revision=args.expected_revision,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
