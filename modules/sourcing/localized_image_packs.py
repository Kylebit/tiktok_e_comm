"""Independent locale-image packs derived from an approved publication snapshot.

This store is deliberately separate from Product Center workbench state and the
ReleaseStore.  Initializing a project only writes a local derivative manifest;
it never mutates a ReleasePlan, calls a translation provider, or writes a
marketplace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_SCHEMA_VERSION = "localized-image-project/v1"
APPROVED_SNAPSHOT_SCHEMA_VERSION = "approved-publication-snapshot/v4"
LOCALES = ("en-master", "ms-MY", "th-TH", "vi-VN", "ru-RU", "es-MX")
SITE_LOCALES = {
    "PH": "en-master",
    "GB": "en-master",
    "MY": "ms-MY",
    "TH": "th-TH",
    "VN": "vi-VN",
    "MX": "es-MX",
    "RU": "ru-RU",
}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class LocalizedImagePackError(ValueError):
    """The approved base or requested localization project is unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_digest(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _offer_id(value: object) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,32}", clean):
        raise LocalizedImagePackError("approved offer_id is invalid")
    return clean


def _text(value: object, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise LocalizedImagePackError(f"{label} is required")
    return clean


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        candidate = Path(temp_name)
        if candidate.exists():
            candidate.unlink()


def _approved_images(snapshot: Mapping[str, Any]) -> list[str]:
    product = snapshot.get("product")
    if not isinstance(product, Mapping):
        raise LocalizedImagePackError("approved snapshot product is missing")
    raw_images = product.get("images")
    if (
        not isinstance(raw_images, Sequence)
        or isinstance(raw_images, (str, bytes))
        or not raw_images
    ):
        raise LocalizedImagePackError("approved snapshot images are required")
    images = [str(value or "").strip() for value in raw_images]
    if any(not value.startswith("https://") for value in images):
        raise LocalizedImagePackError("approved snapshot images must use HTTPS")
    if len(images) != len(set(images)):
        raise LocalizedImagePackError("approved snapshot images are ambiguous")
    return images


def _target_locale(target_label: str) -> str:
    if target_label == "miaoshou:COMMON":
        return "en-master"
    channel, separator, target = target_label.partition(":")
    if not separator or channel not in {"tiktok", "shopee", "ozon"}:
        raise LocalizedImagePackError(
            f"unsupported publication target for localized images: {target_label}"
        )
    site = target.rsplit("_", 1)[-1] if channel == "tiktok" else target
    locale = SITE_LOCALES.get(site)
    if locale is None:
        raise LocalizedImagePackError(
            f"unsupported publication target for localized images: {target_label}"
        )
    return locale


def _publication_targets(snapshot: Mapping[str, Any]) -> list[str]:
    rows = snapshot.get("publication_targets")
    if not isinstance(rows, list) or not rows:
        raise LocalizedImagePackError("approved publication targets are required")
    labels: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise LocalizedImagePackError("approved publication target is invalid")
        label = _text(row.get("target_label"), "approved target_label")
        _target_locale(label)
        labels.append(label)
    if len(labels) != len(set(labels)):
        raise LocalizedImagePackError("approved publication targets are ambiguous")
    return labels


def _pack(
    *, offer_id: str, locale: str, ordered_images: Sequence[str], created_at: str
) -> dict[str, Any]:
    is_base = locale == "en-master"
    images = [
        {
            "position": position,
            "source_url": source_url,
            "source_url_digest": _canonical_digest(source_url),
            "output_url": source_url if is_base else None,
            "status": "REUSE_BASE" if is_base else "PENDING_TEXT_REVIEW",
        }
        for position, source_url in enumerate(ordered_images, start=1)
    ]
    identity = {
        "schema_version": "localized-image-pack/v1",
        "offer_id": offer_id,
        "locale": locale,
        "revision": 1,
        "images": images,
    }
    return {
        **identity,
        "pack_id": f"localized-images:{offer_id}:{locale}:r1",
        "pack_digest": _canonical_digest(identity),
        "status": "READY_BASE" if is_base else "PENDING_TEXT_REVIEW",
        "created_at": created_at,
    }


class LocalizedImagePackStore:
    """Append-safe local derivative state for one approved image master."""

    def __init__(self, root: Path):
        self.root = Path(root)
        lock_key = str(self.root.resolve())
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(lock_key, threading.RLock())

    def _path(self, offer_id: object) -> Path:
        return self.root / _offer_id(offer_id) / "project.json"

    def load(self, offer_id: object) -> dict[str, Any]:
        path = self._path(offer_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LocalizedImagePackError(
                "localized image project is unreadable"
            ) from error
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != PROJECT_SCHEMA_VERSION
            or payload.get("offer_id") != _offer_id(offer_id)
        ):
            raise LocalizedImagePackError("localized image project is invalid")
        return payload

    def initialize_from_approved_snapshot(
        self, snapshot: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            return self._initialize_from_approved_snapshot(snapshot)

    def _initialize_from_approved_snapshot(
        self, snapshot: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(snapshot, Mapping):
            raise LocalizedImagePackError("approved snapshot is required")
        if snapshot.get("schema_version") != APPROVED_SNAPSHOT_SCHEMA_VERSION:
            raise LocalizedImagePackError("approved snapshot schema is unsupported")
        offer_id = _offer_id(snapshot.get("offer_id"))
        plan_id = _text(snapshot.get("plan_id"), "approved plan_id")
        snapshot_digest = _text(
            snapshot.get("snapshot_digest"), "approved snapshot_digest"
        )
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", snapshot_digest):
            raise LocalizedImagePackError("approved snapshot_digest is invalid")
        images = _approved_images(snapshot)
        targets = _publication_targets(snapshot)

        existing = self.load(offer_id)
        if existing:
            if existing.get("approved_snapshot_digest") == snapshot_digest:
                return existing
            raise LocalizedImagePackError(
                "localized image project belongs to a different approved snapshot"
            )

        created_at = _now()
        packs = {
            locale: _pack(
                offer_id=offer_id,
                locale=locale,
                ordered_images=images,
                created_at=created_at,
            )
            for locale in LOCALES
        }
        routes = {
            target_label: {
                "locale": locale,
                "pack_id": packs[locale]["pack_id"],
                "pack_digest": packs[locale]["pack_digest"],
                "fallback_policy": (
                    "USE_APPROVED_BASE"
                    if locale == "en-master"
                    else "BLOCK_IF_PACK_NOT_APPROVED"
                ),
            }
            for target_label in targets
            for locale in (_target_locale(target_label),)
        }
        base_identity = {
            "schema_version": "approved-base-image-package/v1",
            "offer_id": offer_id,
            "release_plan_id": plan_id,
            "approved_snapshot_digest": snapshot_digest,
            "ordered_image_urls": images,
        }
        project = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "offer_id": offer_id,
            "revision": 1,
            "release_plan_id": plan_id,
            "approved_snapshot_digest": snapshot_digest,
            "base_package": {
                **base_identity,
                "package_id": (
                    f"base-images:{offer_id}:{snapshot_digest.removeprefix('sha256:')[:16]}"
                ),
                "package_digest": _canonical_digest(base_identity),
            },
            "packs": packs,
            "route_draft": {
                "schema_version": "publication-image-supplement-draft/v1",
                "status": "DRAFT",
                "release_plan_id": plan_id,
                "approved_snapshot_digest": snapshot_digest,
                "routes": routes,
            },
            "external_writes": 0,
            "created_at": created_at,
            "updated_at": created_at,
        }
        _atomic_json(self._path(offer_id), project)
        return project
