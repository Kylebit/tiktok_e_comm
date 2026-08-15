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


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
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

    @staticmethod
    def _check_revision(project: Mapping[str, Any], expected_revision: object) -> None:
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as error:
            raise LocalizedImagePackError("localized image revision is invalid") from error
        if expected != int(project.get("revision") or 0):
            raise LocalizedImagePackError("localized image revision has changed")

    def _commit(self, offer_id: object, project: dict[str, Any]) -> dict[str, Any]:
        project["revision"] = int(project.get("revision") or 0) + 1
        project["updated_at"] = _now()
        project["external_writes"] = 0
        _atomic_json(self._path(offer_id), project)
        return project

    @staticmethod
    def _pack_image(project: Mapping[str, Any], locale: str, source_url: str) -> dict[str, Any]:
        packs = project.get("packs")
        pack = packs.get(locale) if isinstance(packs, Mapping) else None
        if not isinstance(pack, Mapping):
            raise LocalizedImagePackError("localized pack is unavailable")
        matches = [
            row
            for row in (pack.get("images") or [])
            if isinstance(row, dict) and row.get("source_url") == source_url
        ]
        if len(matches) != 1:
            raise LocalizedImagePackError("localized pack image is unavailable or ambiguous")
        return matches[0]

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

    def save_text_inventory(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        source_url: object,
        source_url_digest: object,
        provider: object,
        regions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Bind locally detected text regions to one approved base image."""

        with self._lock:
            project = self.load(offer_id)
            if not project:
                raise LocalizedImagePackError("localized image project is missing")
            self._check_revision(project, expected_revision)
            clean_url = str(source_url or "").strip()
            base_image = self._pack_image(project, "en-master", clean_url)
            if str(source_url_digest or "") != base_image.get("source_url_digest"):
                raise LocalizedImagePackError("text inventory source identity has changed")
            if str(provider or "") != "rapidocr-local/v1":
                raise LocalizedImagePackError("text inventory provider is unsupported")
            normalized: list[dict[str, Any]] = []
            for raw in regions:
                if not isinstance(raw, Mapping):
                    raise LocalizedImagePackError("text inventory region is invalid")
                region_id = str(raw.get("region_id") or "").strip()
                source_text = str(raw.get("source_text") or "").strip()
                bbox = raw.get("bbox")
                try:
                    box = [round(float(value), 6) for value in bbox]
                    confidence = round(float(raw.get("confidence")), 6)
                except (TypeError, ValueError) as error:
                    raise LocalizedImagePackError("text inventory region is invalid") from error
                if (
                    not re.fullmatch(r"text-[a-f0-9]{20}", region_id)
                    or not source_text
                    or len(source_text) > 500
                    or len(box) != 4
                    or not all(0 <= value <= 1 for value in box)
                    or box[2] <= box[0]
                    or box[3] <= box[1]
                    or not 0 <= confidence <= 1
                    or raw.get("origin") != "rapidocr-local/v1"
                ):
                    raise LocalizedImagePackError("text inventory region is invalid")
                normalized.append(
                    {
                        "region_id": region_id,
                        "source_text": source_text,
                        "bbox": box,
                        "confidence": confidence,
                        "origin": "rapidocr-local/v1",
                    }
                )
            ids = [row["region_id"] for row in normalized]
            if len(ids) != len(set(ids)):
                raise LocalizedImagePackError("text inventory region identity is ambiguous")
            inventory = project.setdefault(
                "text_inventory",
                {"schema_version": "localized-image-text-inventory/v1", "images": {}},
            )
            images = inventory.setdefault("images", {})
            images[clean_url] = {
                "source_url": clean_url,
                "source_url_digest": base_image["source_url_digest"],
                "provider": "rapidocr-local/v1",
                "status": "SCANNED",
                "regions": normalized,
                "scanned_at": _now(),
            }
            inventory["status"] = "SCANNED"
            project.pop("automatic_translation", None)
            for locale, pack in (project.get("packs") or {}).items():
                if locale == "en-master" or not isinstance(pack, dict):
                    continue
                image = self._pack_image(project, locale, clean_url)
                previous = {
                    row.get("region_id"): row
                    for row in (image.get("translations") or [])
                    if isinstance(row, Mapping)
                }
                image["translations"] = [
                    {
                        "region_id": row["region_id"],
                        "source_text": row["source_text"],
                        "translated_text": (
                            str(previous.get(row["region_id"], {}).get("translated_text") or "")
                            if previous.get(row["region_id"], {}).get("source_text")
                            == row["source_text"]
                            else ""
                        ),
                        "status": (
                            "DRAFT_TRANSLATED"
                            if str(previous.get(row["region_id"], {}).get("translated_text") or "").strip()
                            and previous.get(row["region_id"], {}).get("source_text")
                            == row["source_text"]
                            else "PENDING_TRANSLATION"
                        ),
                    }
                    for row in normalized
                ]
                image["status"] = (
                    "TEXT_NOT_PRESENT" if not normalized else "PENDING_TRANSLATION"
                )
                image.pop("preview", None)
                pack["status"] = "PENDING_TEXT_REVIEW"
            return self._commit(offer_id, project)

    def save_translation_draft(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        locale: object,
        source_url: object,
        translations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Save operator-edited translations without approving or publishing them."""

        with self._lock:
            project = self.load(offer_id)
            if not project:
                raise LocalizedImagePackError("localized image project is missing")
            self._check_revision(project, expected_revision)
            clean_locale = str(locale or "").strip()
            if clean_locale == "en-master" or clean_locale not in LOCALES:
                raise LocalizedImagePackError("localized pack is required")
            image = self._pack_image(project, clean_locale, str(source_url or "").strip())
            existing = image.get("translations") or []
            existing_by_id = {
                row.get("region_id"): row for row in existing if isinstance(row, Mapping)
            }
            incoming: dict[str, str] = {}
            for raw in translations:
                if not isinstance(raw, Mapping):
                    raise LocalizedImagePackError("translation draft is invalid")
                region_id = str(raw.get("region_id") or "").strip()
                translated = str(raw.get("translated_text") or "").strip()
                if not region_id or len(translated) > 800 or region_id in incoming:
                    raise LocalizedImagePackError("translation draft is invalid")
                incoming[region_id] = translated
            if set(incoming) != set(existing_by_id):
                raise LocalizedImagePackError("translation region coverage has changed")
            image["translations"] = [
                {
                    "region_id": row["region_id"],
                    "source_text": row["source_text"],
                    "translated_text": incoming[row["region_id"]],
                    "status": (
                        "DRAFT_TRANSLATED"
                        if incoming[row["region_id"]]
                        else "PENDING_TRANSLATION"
                    ),
                }
                for row in existing
            ]
            complete = bool(existing) and all(incoming.values())
            image["status"] = "DRAFT_TRANSLATED" if complete else "PENDING_TRANSLATION"
            image.pop("preview", None)
            pack = project["packs"][clean_locale]
            pack["status"] = "DRAFT_TRANSLATED" if complete else "PENDING_TEXT_REVIEW"
            return self._commit(offer_id, project)

    def save_automatic_bundle(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        items: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Atomically bind all automatic translations and local previews.

        Model output is gathered and rendered before this method is called. The
        project is committed once only after every approved source image and
        every locale has passed exact identity checks.
        """

        with self._lock:
            project = self.load(offer_id)
            if not project:
                raise LocalizedImagePackError("localized image project is missing")
            self._check_revision(project, expected_revision)
            source_urls = list(
                (project.get("base_package") or {}).get("ordered_image_urls") or []
            )
            incoming = {
                str(row.get("source_url") or "").strip(): row
                for row in items
                if isinstance(row, Mapping)
            }
            if (
                len(incoming) != len(items)
                or list(incoming) != source_urls
                or set(incoming) != set(source_urls)
            ):
                raise LocalizedImagePackError(
                    "automatic translation source coverage has changed"
                )
            inventory_images = (
                (project.get("text_inventory") or {}).get("images") or {}
            )
            if set(inventory_images) != set(source_urls):
                raise LocalizedImagePackError(
                    "all approved images must be locally scanned before translation"
                )
            localized_locales = tuple(locale for locale in LOCALES if locale != "en-master")
            prepared: list[dict[str, Any]] = []
            inventory_identity: list[dict[str, Any]] = []
            model_calls = 0
            for source_url in source_urls:
                inventory = inventory_images[source_url]
                if not isinstance(inventory, Mapping) or inventory.get("status") != "SCANNED":
                    raise LocalizedImagePackError("text inventory is incomplete")
                regions = inventory.get("regions") or []
                region_ids = [str(row.get("region_id") or "") for row in regions]
                source_by_id = {
                    str(row.get("region_id") or ""): str(row.get("source_text") or "")
                    for row in regions
                    if isinstance(row, Mapping)
                }
                inventory_identity.append(
                    {
                        "source_url": source_url,
                        "source_url_digest": inventory.get("source_url_digest"),
                        "regions": [
                            {
                                "region_id": row.get("region_id"),
                                "source_text": row.get("source_text"),
                                "bbox": row.get("bbox"),
                            }
                            for row in regions
                            if isinstance(row, Mapping)
                        ],
                    }
                )
                item = incoming[source_url]
                raw_translations = item.get("translations")
                raw_previews = item.get("previews")
                receipt = item.get("receipt")
                if (
                    not isinstance(raw_translations, Mapping)
                    or set(raw_translations) != set(localized_locales)
                    or not isinstance(raw_previews, Mapping)
                    or not isinstance(receipt, Mapping)
                    or receipt.get("provider") != "toapis-chat-completions/v1"
                    or receipt.get("model") != "gpt-5.4-mini-official"
                ):
                    raise LocalizedImagePackError(
                        "automatic translation bundle is invalid"
                    )
                try:
                    item_calls = int(receipt.get("model_calls"))
                except (TypeError, ValueError) as error:
                    raise LocalizedImagePackError(
                        "automatic translation receipt is invalid"
                    ) from error
                expected_calls = 1 if regions else 0
                if (
                    item_calls != expected_calls
                    or receipt.get("status")
                    != ("AUTO_TRANSLATED" if regions else "NO_TEXT_REUSE_BASE")
                    or set(raw_previews) != (set(localized_locales) if regions else set())
                ):
                    raise LocalizedImagePackError(
                        "automatic translation receipt is invalid"
                    )
                model_calls += item_calls
                locale_rows: dict[str, list[dict[str, str]]] = {}
                preview_bytes: dict[str, bytes] = {}
                for locale in localized_locales:
                    rows = raw_translations.get(locale)
                    if not isinstance(rows, list) or len(rows) != len(region_ids):
                        raise LocalizedImagePackError(
                            "automatic translation region coverage has changed"
                        )
                    ids = [
                        str(row.get("region_id") or "")
                        for row in rows
                        if isinstance(row, Mapping)
                    ]
                    if ids != region_ids:
                        raise LocalizedImagePackError(
                            "automatic translation region coverage has changed"
                        )
                    clean_rows: list[dict[str, str]] = []
                    for row in rows:
                        if not isinstance(row, Mapping):
                            raise LocalizedImagePackError(
                                "automatic translation row is invalid"
                            )
                        region_id = str(row.get("region_id") or "")
                        source_text = str(row.get("source_text") or "")
                        translated_text = str(row.get("translated_text") or "").strip()
                        if (
                            source_text != source_by_id.get(region_id)
                            or not translated_text
                            or len(translated_text) > 800
                        ):
                            raise LocalizedImagePackError(
                                "automatic translation row is invalid"
                            )
                        clean_rows.append(
                            {
                                "region_id": region_id,
                                "source_text": source_text,
                                "translated_text": translated_text,
                            }
                        )
                    locale_rows[locale] = clean_rows
                    if regions:
                        artifact = raw_previews.get(locale)
                        if (
                            not isinstance(artifact, bytes)
                            or not artifact.startswith(b"\x89PNG\r\n\x1a\n")
                            or len(artifact) > 20 * 1024 * 1024
                        ):
                            raise LocalizedImagePackError(
                                "automatic translation preview is invalid"
                            )
                        preview_bytes[locale] = artifact
                prepared.append(
                    {
                        "source_url": source_url,
                        "regions_present": bool(regions),
                        "translations": locale_rows,
                        "previews": preview_bytes,
                        "receipt": {
                            "status": receipt.get("status"),
                            "provider": receipt.get("provider"),
                            "model": receipt.get("model"),
                            "model_calls": item_calls,
                        },
                    }
                )

            artifact_records: dict[tuple[str, str], dict[str, str]] = {}
            for item in prepared:
                for locale, artifact in item["previews"].items():
                    artifact_digest = hashlib.sha256(artifact).hexdigest()
                    artifact_id = f"localized-preview-{artifact_digest[:20]}"
                    artifact_path = (
                        self._path(offer_id).parent / "artifacts" / f"{artifact_id}.png"
                    )
                    if artifact_path.exists() and artifact_path.read_bytes() != artifact:
                        raise LocalizedImagePackError(
                            "localized preview artifact identity collision"
                        )
                    if not artifact_path.exists():
                        _atomic_bytes(artifact_path, artifact)
                    artifact_records[(item["source_url"], locale)] = {
                        "artifact_id": artifact_id,
                        "artifact_digest": f"sha256:{artifact_digest}",
                    }

            for item in prepared:
                for locale in localized_locales:
                    image = self._pack_image(project, locale, item["source_url"])
                    translations = [
                        {**row, "status": "AUTO_TRANSLATED"}
                        for row in item["translations"][locale]
                    ]
                    image["translations"] = translations
                    image["automatic_translation_receipt"] = dict(item["receipt"])
                    if not item["regions_present"]:
                        image["status"] = "REUSE_BASE_NO_TEXT"
                        image["output_url"] = item["source_url"]
                        image.pop("preview", None)
                        continue
                    artifact = artifact_records[(item["source_url"], locale)]
                    image["preview"] = {
                        "status": "AUTO_PREVIEW_READY",
                        **artifact,
                        "renderer": "pillow-local-preview/v1",
                        "translation_digest": _canonical_digest(translations),
                        "created_at": _now(),
                    }
                    image["status"] = "AUTO_PREVIEW_READY"

            for locale in localized_locales:
                project["packs"][locale]["status"] = "AUTO_PREVIEW_READY"
            project["automatic_translation"] = {
                "schema_version": "localized-image-automatic-run/v1",
                "status": "AUTO_PREVIEW_READY",
                "provider": "toapis-chat-completions/v1",
                "model": "gpt-5.4-mini-official",
                "model_calls": model_calls,
                "inventory_digest": _canonical_digest(inventory_identity),
                "completed_at": _now(),
            }
            return self._commit(offer_id, project)

    def save_preview_artifact(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        locale: object,
        source_url: object,
        artifact_bytes: bytes,
        renderer: object,
    ) -> dict[str, Any]:
        """Persist a local-only preview bound to the current translation draft."""

        with self._lock:
            project = self.load(offer_id)
            if not project:
                raise LocalizedImagePackError("localized image project is missing")
            self._check_revision(project, expected_revision)
            clean_locale = str(locale or "").strip()
            if clean_locale == "en-master" or clean_locale not in LOCALES:
                raise LocalizedImagePackError("localized pack is required")
            if renderer != "pillow-local-preview/v1":
                raise LocalizedImagePackError("localized preview renderer is unsupported")
            if (
                not isinstance(artifact_bytes, bytes)
                or not artifact_bytes.startswith(b"\x89PNG\r\n\x1a\n")
                or len(artifact_bytes) > 20 * 1024 * 1024
            ):
                raise LocalizedImagePackError("localized preview artifact is invalid")
            image = self._pack_image(project, clean_locale, str(source_url or "").strip())
            translations = image.get("translations") or []
            if not translations or any(
                not str(row.get("translated_text") or "").strip()
                for row in translations
                if isinstance(row, Mapping)
            ):
                raise LocalizedImagePackError("complete translation draft is required")
            artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
            artifact_id = f"localized-preview-{artifact_digest[:20]}"
            artifact_path = self._path(offer_id).parent / "artifacts" / f"{artifact_id}.png"
            if artifact_path.exists() and artifact_path.read_bytes() != artifact_bytes:
                raise LocalizedImagePackError("localized preview artifact identity collision")
            if not artifact_path.exists():
                _atomic_bytes(artifact_path, artifact_bytes)
            translation_identity = [
                {
                    "region_id": row["region_id"],
                    "source_text": row["source_text"],
                    "translated_text": row["translated_text"],
                }
                for row in translations
            ]
            image["preview"] = {
                "status": "PREVIEW_READY",
                "artifact_id": artifact_id,
                "artifact_digest": f"sha256:{artifact_digest}",
                "renderer": "pillow-local-preview/v1",
                "translation_digest": _canonical_digest(translation_identity),
                "created_at": _now(),
            }
            image["status"] = "PREVIEW_READY"
            project["packs"][clean_locale]["status"] = "PREVIEW_READY"
            return self._commit(offer_id, project)

    def preview_artifact_path(self, offer_id: object, artifact_id: object) -> Path:
        project = self.load(offer_id)
        clean = str(artifact_id or "").strip()
        if not re.fullmatch(r"localized-preview-[a-f0-9]{20}", clean):
            raise LocalizedImagePackError("localized preview artifact_id is invalid")
        bound = any(
            (image.get("preview") or {}).get("artifact_id") == clean
            for pack in (project.get("packs") or {}).values()
            if isinstance(pack, Mapping)
            for image in (pack.get("images") or [])
            if isinstance(image, Mapping)
        )
        if not bound:
            raise LocalizedImagePackError("localized preview artifact is not bound")
        path = self._path(offer_id).parent / "artifacts" / f"{clean}.png"
        if not path.is_file():
            raise LocalizedImagePackError("localized preview artifact is missing")
        return path
