"""Local, evidence-bound image cleanup and OCR-region contracts.

Batch 1 deliberately contains no paid OCR or remote image-edit provider.  It
creates immutable derived artifacts from an operator-reviewed region manifest,
and leaves the original source untouched.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw


SCHEMA_VERSION = "image-localization-manifest/v1"
ALLOWED_CLASSIFICATIONS = frozenset(
    {
        "watermark",
        "supplier_metadata",
        "translatable",
        "product_fact",
        "protected_natural_text",
        "dimension",
        "rebuild_required",
        "ignore",
    }
)
ALLOWED_ORIGINS = frozenset({"manual", "ocr"})
REMOVAL_CLASSIFICATIONS = frozenset({"watermark", "supplier_metadata"})
PROTECTED_CLASSIFICATIONS = frozenset({"protected_natural_text"})
LOCAL_CLEAN_METHOD = "local_region_fill/v1"
_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


class ImageLocalizationValidationError(ValueError):
    """The localization manifest or requested local operation is unsafe."""


def _enabled(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def image_localization_feature_flags(
    environ: Mapping[str, str] | None = None,
) -> dict[str, bool]:
    env = os.environ if environ is None else environ
    return {
        "manifest_enabled": _enabled(
            env.get("ORBIT_IMAGE_LOCALIZATION_MANIFEST"), default=True
        ),
        "local_clean_master_enabled": _enabled(
            env.get("ORBIT_IMAGE_LOCALIZATION_LOCAL_CLEAN_MASTER"), default=True
        ),
        "manual_region_editor_enabled": _enabled(
            env.get("ORBIT_IMAGE_LOCALIZATION_MANUAL_EDITOR"), default=True
        ),
        "ocr_provider_enabled": _enabled(
            env.get("ORBIT_IMAGE_LOCALIZATION_OCR_PROVIDER"), default=False
        ),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        candidate = Path(name)
        if candidate.exists():
            candidate.unlink()


def _clean_offer_id(value: object) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,32}", clean):
        raise ImageLocalizationValidationError("offer_id must contain only digits")
    return clean


def _normalized_bbox(value: object) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise ImageLocalizationValidationError("bbox must contain four normalized numbers")
    try:
        box = [round(float(item), 6) for item in value]
    except (TypeError, ValueError) as exc:
        raise ImageLocalizationValidationError("bbox must contain four normalized numbers") from exc
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ImageLocalizationValidationError("bbox must be ordered inside the normalized image")
    return box


def _normalize_region(
    raw: Mapping[str, Any],
    *,
    default_origin: str,
) -> dict[str, Any]:
    region_id = str(raw.get("region_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", region_id):
        raise ImageLocalizationValidationError("region_id is invalid")
    classification = str(raw.get("classification") or "").strip()
    if classification not in ALLOWED_CLASSIFICATIONS:
        raise ImageLocalizationValidationError("classification is not allowed")
    origin = str(raw.get("origin") or default_origin).strip()
    if origin not in ALLOWED_ORIGINS or origin != default_origin:
        raise ImageLocalizationValidationError(f"origin must be {default_origin}")
    text = str(raw.get("text") or "")
    if len(text) > 2000:
        raise ImageLocalizationValidationError("region text is too long")
    language = str(raw.get("detected_language") or "").strip().lower()
    if len(language) > 24:
        raise ImageLocalizationValidationError("detected_language is too long")
    row: dict[str, Any] = {
        "region_id": region_id,
        "bbox": _normalized_bbox(raw.get("bbox")),
        "text": text,
        "classification": classification,
        "origin": origin,
    }
    if language:
        row["detected_language"] = language
    if raw.get("confidence") is not None:
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ImageLocalizationValidationError("confidence must be between 0 and 1") from exc
        if not 0 <= confidence <= 1:
            raise ImageLocalizationValidationError("confidence must be between 0 and 1")
        row["confidence"] = round(confidence, 6)
    return row


def _boxes_overlap(left: Sequence[float], right: Sequence[float]) -> bool:
    return not (
        left[2] <= right[0]
        or right[2] <= left[0]
        or left[3] <= right[1]
        or right[3] <= left[1]
    )


class ImageLocalizationStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        lock_key = str(self.root.resolve())
        with _STORE_LOCKS_GUARD:
            self._lock = _STORE_LOCKS.setdefault(lock_key, threading.RLock())

    def _offer_dir(self, offer_id: object) -> Path:
        return self.root / _clean_offer_id(offer_id)

    def _manifest_path(self, offer_id: object) -> Path:
        return self._offer_dir(offer_id) / "manifest.json"

    def load(self, offer_id: object) -> dict[str, Any]:
        path = self._manifest_path(offer_id)
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ImageLocalizationValidationError("unsupported image localization manifest")
        return payload

    def initialize(
        self,
        offer_id: object,
        sources: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            return self._initialize(offer_id, sources)

    def _initialize(
        self,
        offer_id: object,
        sources: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        clean_offer = _clean_offer_id(offer_id)
        if not image_localization_feature_flags()["manifest_enabled"]:
            raise ImageLocalizationValidationError("image localization manifest is disabled")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, source in enumerate(sources):
            url = str(source.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                raise ImageLocalizationValidationError("source_url must be http or https")
            identity_digest = _sha256(url.encode("utf-8"))
            if identity_digest in seen:
                continue
            seen.add(identity_digest)
            normalized.append(
                {
                    "asset_id": f"source-{index + 1:02d}-{identity_digest[:12]}",
                    "source_url": url,
                    "source_kind": str(source.get("kind") or "main")[:32],
                    "source_identity_digest": identity_digest,
                    "regions": [],
                    "ocr": {"status": "not_scanned"},
                    "clean_master": {"status": "not_created"},
                }
            )
        if not normalized:
            raise ImageLocalizationValidationError("at least one source image is required")
        current = self.load(clean_offer)
        if current:
            current_identities = [row.get("source_identity_digest") for row in current.get("assets") or []]
            incoming_identities = [row["source_identity_digest"] for row in normalized]
            if current_identities == incoming_identities:
                return current
            prior = {
                row.get("source_identity_digest"): row
                for row in current.get("assets") or []
                if isinstance(row, dict)
            }
            for index, row in enumerate(normalized):
                existing = prior.get(row["source_identity_digest"])
                if existing:
                    normalized[index] = existing
            revision = int(current.get("revision") or 0) + 1
        else:
            revision = 1
        payload = {
            "schema_version": SCHEMA_VERSION,
            "offer_id": clean_offer,
            "revision": revision,
            "features": image_localization_feature_flags(),
            "assets": normalized,
            "updated_at": _now(),
        }
        _atomic_json(self._manifest_path(clean_offer), payload)
        return payload

    @staticmethod
    def _asset(manifest: dict[str, Any], asset_id: object) -> dict[str, Any]:
        clean = str(asset_id or "").strip()
        matches = [row for row in manifest.get("assets") or [] if row.get("asset_id") == clean]
        if len(matches) != 1:
            raise ImageLocalizationValidationError("asset_id is unavailable or ambiguous")
        return matches[0]

    @staticmethod
    def _check_revision(manifest: Mapping[str, Any], expected_revision: object) -> None:
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as exc:
            raise ImageLocalizationValidationError("expected_revision is required") from exc
        if expected != int(manifest.get("revision") or 0):
            raise ImageLocalizationValidationError("stale revision; refresh before saving")

    def _commit(self, offer_id: object, manifest: dict[str, Any]) -> dict[str, Any]:
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        manifest["updated_at"] = _now()
        _atomic_json(self._manifest_path(offer_id), manifest)
        return manifest

    def save_regions(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        asset_id: object,
        regions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            return self._save_regions(
                offer_id,
                expected_revision=expected_revision,
                asset_id=asset_id,
                regions=regions,
            )

    def _save_regions(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        asset_id: object,
        regions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        manifest = self.load(offer_id)
        if not manifest:
            raise ImageLocalizationValidationError("image localization manifest is missing")
        self._check_revision(manifest, expected_revision)
        asset = self._asset(manifest, asset_id)
        normalized = [_normalize_region(row, default_origin="manual") for row in regions]
        ids = [row["region_id"] for row in normalized]
        if len(ids) != len(set(ids)):
            raise ImageLocalizationValidationError("region_id values must be unique")
        asset["regions"] = normalized
        asset["clean_master"] = {"status": "stale_regions_changed"}
        return self._commit(offer_id, manifest)

    def merge_ocr_regions(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        asset_id: object,
        source_identity_digest: object,
        provider: object,
        provider_version: object,
        regions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            return self._merge_ocr_regions(
                offer_id,
                expected_revision=expected_revision,
                asset_id=asset_id,
                source_identity_digest=source_identity_digest,
                provider=provider,
                provider_version=provider_version,
                regions=regions,
            )

    def _merge_ocr_regions(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        asset_id: object,
        source_identity_digest: object,
        provider: object,
        provider_version: object,
        regions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        manifest = self.load(offer_id)
        if not manifest:
            raise ImageLocalizationValidationError("image localization manifest is missing")
        self._check_revision(manifest, expected_revision)
        asset = self._asset(manifest, asset_id)
        if str(source_identity_digest or "") != asset.get("source_identity_digest"):
            raise ImageLocalizationValidationError("OCR source identity does not match the image")
        provider_name = str(provider or "").strip()
        provider_rev = str(provider_version or "").strip()
        if not provider_name or len(provider_name) > 80 or not provider_rev or len(provider_rev) > 80:
            raise ImageLocalizationValidationError("OCR provider identity is invalid")
        manual = [
            row for row in asset.get("regions") or []
            if isinstance(row, dict) and row.get("origin") == "manual"
        ]
        scanned = [_normalize_region(row, default_origin="ocr") for row in regions]
        ids = [row["region_id"] for row in manual + scanned]
        if len(ids) != len(set(ids)):
            raise ImageLocalizationValidationError("OCR region_id conflicts with a manual region")
        asset["regions"] = manual + scanned
        asset["ocr"] = {
            "status": "scanned",
            "provider": provider_name,
            "provider_version": provider_rev,
            "source_identity_digest": asset["source_identity_digest"],
            "region_count": len(scanned),
            "scanned_at": _now(),
        }
        asset["clean_master"] = {"status": "stale_regions_changed"}
        return self._commit(offer_id, manifest)

    @staticmethod
    def _pixel_box(box: Sequence[float], width: int, height: int) -> tuple[int, int, int, int]:
        x0 = max(0, min(width - 1, int(round(box[0] * width))))
        y0 = max(0, min(height - 1, int(round(box[1] * height))))
        x1 = max(x0 + 1, min(width, int(round(box[2] * width))))
        y1 = max(y0 + 1, min(height, int(round(box[3] * height))))
        return x0, y0, x1, y1

    @staticmethod
    def _sample_fill(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
        width, height = image.size
        x0, y0, x1, y1 = box
        samples: list[tuple[int, int, int]] = []
        if y0 > 0:
            samples.extend(image.getpixel((x, y0 - 1))[:3] for x in range(x0, x1))
        if y1 < height:
            samples.extend(image.getpixel((x, y1))[:3] for x in range(x0, x1))
        if x0 > 0:
            samples.extend(image.getpixel((x0 - 1, y))[:3] for y in range(y0, y1))
        if x1 < width:
            samples.extend(image.getpixel((x1, y))[:3] for y in range(y0, y1))
        if not samples:
            samples = [image.getpixel((0, 0))[:3]]
        return tuple(int(sum(pixel[channel] for pixel in samples) / len(samples)) for channel in range(3))

    def create_clean_master(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        asset_id: object,
        source_path: Path,
        method: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._create_clean_master(
                offer_id,
                expected_revision=expected_revision,
                asset_id=asset_id,
                source_path=source_path,
                method=method,
            )

    def _create_clean_master(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        asset_id: object,
        source_path: Path,
        method: str,
    ) -> dict[str, Any]:
        if not image_localization_feature_flags()["local_clean_master_enabled"]:
            raise ImageLocalizationValidationError("local clean master is disabled")
        if method == "ai.all":
            raise ImageLocalizationValidationError("blanket text removal is forbidden")
        if method != LOCAL_CLEAN_METHOD:
            raise ImageLocalizationValidationError("clean master method is not approved")
        manifest = self.load(offer_id)
        if not manifest:
            raise ImageLocalizationValidationError("image localization manifest is missing")
        self._check_revision(manifest, expected_revision)
        asset = self._asset(manifest, asset_id)
        regions = [row for row in asset.get("regions") or [] if isinstance(row, dict)]
        removals = [row for row in regions if row.get("classification") in REMOVAL_CLASSIFICATIONS]
        protected = [row for row in regions if row.get("classification") in PROTECTED_CLASSIFICATIONS]
        if not removals:
            raise ImageLocalizationValidationError("at least one reviewed removal region is required")
        for removal in removals:
            if any(_boxes_overlap(removal["bbox"], safe["bbox"]) for safe in protected):
                raise ImageLocalizationValidationError("removal overlaps a protected region")
        source = Path(source_path)
        if not source.is_file():
            raise ImageLocalizationValidationError("source image file is unavailable")
        source_bytes = source.read_bytes()
        source_digest = _sha256(source_bytes)
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        draw = ImageDraw.Draw(image)
        for region in removals:
            pixel_box = self._pixel_box(region["bbox"], *image.size)
            draw.rectangle(pixel_box, fill=self._sample_fill(image, pixel_box))
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        artifact_bytes = output.getvalue()
        artifact_digest = _sha256(artifact_bytes)
        artifact_id = f"clean-{artifact_digest[:20]}"
        path = self._offer_dir(offer_id) / "artifacts" / f"{artifact_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != artifact_bytes:
            raise ImageLocalizationValidationError("artifact identity collision")
        if not path.exists():
            path.write_bytes(artifact_bytes)
        asset["clean_master"] = {
            "status": "created",
            "artifact_id": artifact_id,
            "artifact_digest": artifact_digest,
            "source_content_digest": source_digest,
            "source_identity_digest": asset["source_identity_digest"],
            "method": method,
            "removal_region_ids": [row["region_id"] for row in removals],
            "protected_region_ids": [row["region_id"] for row in protected],
            "created_at": _now(),
        }
        return self._commit(offer_id, manifest)

    def artifact_path(self, offer_id: object, artifact_id: object) -> Path:
        manifest = self.load(offer_id)
        clean = str(artifact_id or "").strip()
        if not re.fullmatch(r"clean-[a-f0-9]{20}", clean):
            raise ImageLocalizationValidationError("artifact_id is invalid")
        if not any(
            (row.get("clean_master") or {}).get("artifact_id") == clean
            for row in manifest.get("assets") or []
            if isinstance(row, dict)
        ):
            raise ImageLocalizationValidationError("artifact is not bound to this offer")
        path = self._offer_dir(offer_id) / "artifacts" / f"{clean}.png"
        if not path.is_file():
            raise ImageLocalizationValidationError("artifact file is missing")
        return path
