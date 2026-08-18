"""Build an image-routing-only successor from a human-approved supplement."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import urlparse


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha(value: object, name: str) -> str:
    text = str(value or "").strip()
    plain = text.removeprefix("sha256:")
    if len(plain) != 64 or any(char not in "0123456789abcdef" for char in plain):
        raise ValueError(f"{name} is invalid")
    return "sha256:" + plain


def _https(value: object, name: str) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{name} must use public HTTPS")
    return text


def build_localized_image_successor_payload(
    predecessor_payload: Mapping[str, Any],
    *,
    predecessor_snapshot: Mapping[str, Any],
    supplement: Mapping[str, Any],
    uploaded_assets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Clone every predecessor fact and add only approved target image routes."""
    if not isinstance(predecessor_payload, Mapping) or not isinstance(predecessor_snapshot, Mapping):
        raise TypeError("localized image successor requires frozen predecessor facts")
    if not isinstance(supplement, Mapping) or supplement.get("schema_version") != "publication-image-supplement/v1":
        raise ValueError("approved localized image supplement is invalid")
    if supplement.get("status") != "APPROVED_LOCAL_ASSETS" or supplement.get("platform_writes") != 0 or supplement.get("product_center_mutated") is not False:
        raise ValueError("localized image supplement is not locally approved")
    if (
        supplement.get("offer_id") != predecessor_snapshot.get("offer_id")
        or supplement.get("release_plan_id") != predecessor_payload.get("plan_id")
        or supplement.get("approved_snapshot_digest") != predecessor_snapshot.get("snapshot_digest")
    ):
        raise ValueError("localized image supplement identity drifted")
    routes = supplement.get("routes")
    targets = predecessor_payload.get("targets")
    base_images = (predecessor_snapshot.get("product") or {}).get("images")
    if not isinstance(routes, Mapping) or type(targets) is not list or set(routes) != set(targets):
        raise ValueError("localized image route coverage drifted")
    if type(base_images) is not list or not base_images:
        raise ValueError("predecessor image facts are invalid")
    if not isinstance(uploaded_assets, Mapping):
        raise ValueError("localized image uploads are required")

    used_artifacts: set[str] = set()
    normalized_routes: dict[str, dict[str, Any]] = {}
    for label in targets:
        route = routes[label]
        if not isinstance(route, Mapping) or set(route) != {"locale", "ordered_images"}:
            raise ValueError(f"{label} localized route is invalid")
        ordered = route.get("ordered_images")
        if type(ordered) is not list or len(ordered) != len(base_images):
            raise ValueError(f"{label} localized image count drifted")
        urls: list[str] = []
        for position, row in enumerate(ordered, start=1):
            if not isinstance(row, Mapping) or row.get("position") != position:
                raise ValueError(f"{label} localized image position drifted")
            if row.get("kind") == "APPROVED_BASE_URL":
                if set(row) != {"position", "kind", "url"} or row.get("url") != base_images[position - 1]:
                    raise ValueError(f"{label} base image identity drifted")
                urls.append(_https(row["url"], f"{label} base image"))
                continue
            if row.get("kind") != "LOCALIZED_ARTIFACT" or set(row) != {
                "position", "kind", "artifact_id", "artifact_digest", "source_url"
            } or row.get("source_url") != base_images[position - 1]:
                raise ValueError(f"{label} localized artifact identity drifted")
            artifact_id = str(row.get("artifact_id") or "")
            asset = uploaded_assets.get(artifact_id)
            if not isinstance(asset, Mapping) or set(asset) != {"artifact_digest", "url"}:
                raise ValueError("localized uploaded asset coverage is incomplete")
            if _sha(asset.get("artifact_digest"), "uploaded artifact digest") != _sha(row.get("artifact_digest"), "supplement artifact digest"):
                raise ValueError("localized uploaded artifact digest drifted")
            used_artifacts.add(artifact_id)
            urls.append(_https(asset.get("url"), "localized uploaded image"))
        normalized_routes[label] = {
            "locale": str(route.get("locale") or "").strip(),
            "ordered_images": urls,
        }
        if not normalized_routes[label]["locale"]:
            raise ValueError(f"{label} localized locale is invalid")
    if set(uploaded_assets) != used_artifacts:
        raise ValueError("localized uploaded asset coverage drifted")

    candidate = deepcopy(dict(predecessor_payload))
    candidate["localized_image_routing"] = {
        "schema_version": "localized-publication-images/v1",
        "approval_digest": _sha(supplement.get("approval_digest"), "localized approval digest"),
        "supplement_digest": _sha(supplement.get("supplement_digest"), "localized supplement digest"),
        "source_snapshot_digest": _sha(supplement.get("approved_snapshot_digest"), "localized source snapshot digest"),
        "routes": normalized_routes,
    }
    candidate.pop("plan_id", None)
    candidate["plan_id"] = "omnichannel:" + _digest(candidate)
    return candidate


__all__ = ["build_localized_image_successor_payload"]
