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
ALLOWED_ORIGINS = frozenset({"manual", "ocr", "local_detector"})
REMOVAL_CLASSIFICATIONS = frozenset({"watermark", "supplier_metadata"})
PROTECTED_CLASSIFICATIONS = frozenset({"protected_natural_text"})
LOCAL_CLEAN_METHOD = "local_region_fill/v1"
LOCAL_AUTO_WATERMARK_METHOD = "local_ocr_watermark_inpaint/v1"
_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


class ImageLocalizationValidationError(ValueError):
    """The localization manifest or requested local operation is unsafe."""


class LocalWatermarkDetector:
    """Detect repeated supplier watermarks locally without a paid provider.

    The detector deliberately accepts only URL/shop-shaped text close to an
    outer image edge.  It never treats all OCR text as removable.  When one
    sibling image exposes the whole watermark and another exposes only a small
    fragment, the strong sibling supplies a glyph mask for the weak image of
    the exact same dimensions.
    """

    provider_name = "rapidocr-onnxruntime"
    provider_version = "edge-watermark/v1"

    def __init__(self, engine_factory=None):
        self._engine_factory = engine_factory

    @staticmethod
    def _watermark_text(value: object) -> bool:
        clean = re.sub(r"[^a-z0-9.]", "", str(value or "").lower())
        return bool(
            "shop" in clean
            or "1688" in clean
            or clean == "com"
            or clean.endswith(".com")
        )

    @staticmethod
    def _candidate_box(points: object, width: int, height: int, *, y_offset: int = 0) -> list[float] | None:
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
            return None
        try:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) + y_offset for point in points]
        except (IndexError, TypeError, ValueError):
            return None
        if not xs or not ys or width <= 0 or height <= 0:
            return None
        pad_x = max(2.0, width * 0.004)
        pad_y = max(2.0, height * 0.004)
        return [
            max(0.0, (min(xs) - pad_x) / width),
            max(0.0, (min(ys) - pad_y) / height),
            min(1.0, (max(xs) + pad_x) / width),
            min(1.0, (max(ys) + pad_y) / height),
        ]

    @staticmethod
    def _iou(left: Sequence[float], right: Sequence[float]) -> float:
        x0 = max(left[0], right[0])
        y0 = max(left[1], right[1])
        x1 = min(left[2], right[2])
        y1 = min(left[3], right[3])
        intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
        right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
        union = left_area + right_area - intersection
        return intersection / union if union else 0.0

    def _new_engine(self):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise ImageLocalizationValidationError(
                "local watermark OCR is unavailable; install rapidocr-onnxruntime"
            ) from exc
        return self._engine_factory() if self._engine_factory else RapidOCR()

    def _scan(self, image, *, engine=None) -> list[dict[str, Any]]:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise ImageLocalizationValidationError(
                "local watermark OCR is unavailable; install opencv-python"
            ) from exc
        active_engine = engine or self._new_engine()
        height, width = image.shape[:2]
        y_offset = int(height * 0.84)
        bottom = image[y_offset:, :]
        gray = cv2.cvtColor(bottom, cv2.COLOR_BGR2GRAY)
        enhanced = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
        passes = ((image, 0), (enhanced, y_offset))
        found: list[dict[str, Any]] = []
        for source, offset in passes:
            result, _ = active_engine(source)
            for row in result or []:
                if not isinstance(row, Sequence) or len(row) < 3:
                    continue
                points, text, confidence = row[0], str(row[1] or ""), float(row[2] or 0)
                box = self._candidate_box(points, width, height, y_offset=offset)
                if (
                    box is None
                    or confidence < 0.50
                    or box[3] < 0.88
                    or not self._watermark_text(text)
                ):
                    continue
                candidate = {
                    "bbox": [round(value, 6) for value in box],
                    "text": text[:2000],
                    "confidence": round(confidence, 6),
                }
                duplicate = next(
                    (
                        existing
                        for existing in found
                        if self._iou(existing["bbox"], candidate["bbox"]) >= 0.65
                    ),
                    None,
                )
                if duplicate is None:
                    found.append(candidate)
                elif candidate["confidence"] > duplicate["confidence"]:
                    duplicate.update(candidate)
        return found

    @staticmethod
    def _union_box(candidates: Sequence[Mapping[str, Any]]) -> list[float]:
        return [
            min(row["bbox"][0] for row in candidates),
            min(row["bbox"][1] for row in candidates),
            max(row["bbox"][2] for row in candidates),
            max(row["bbox"][3] for row in candidates),
        ]

    @staticmethod
    def _direct_mask(image, candidates: Sequence[Mapping[str, Any]]):
        import cv2
        import numpy as np

        height, width = image.shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        pad = max(2, int(round(min(width, height) * 0.004)))
        for row in candidates:
            x0, y0, x1, y1 = ImageLocalizationStore._pixel_box(row["bbox"], width, height)
            cv2.rectangle(
                mask,
                (max(0, x0 - pad), max(0, y0 - pad)),
                (min(width - 1, x1 + pad), min(height - 1, y1 + pad)),
                255,
                -1,
            )
        return mask

    @staticmethod
    def _rendered_text_mask(image, candidates: Sequence[Mapping[str, Any]]):
        """Approximate OCR glyphs without filling the full OCR rectangle."""
        import cv2
        import numpy as np

        height, width = image.shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        font = cv2.FONT_HERSHEY_SIMPLEX
        for row in candidates:
            text = re.sub(r"[^A-Za-z0-9._-]", "", str(row.get("text") or ""))
            if not text:
                continue
            x0, y0, x1, y1 = ImageLocalizationStore._pixel_box(
                row["bbox"], width, height
            )
            box_width = max(1, x1 - x0)
            box_height = max(1, y1 - y0)
            base_size, base_line = cv2.getTextSize(text, font, 1.0, 1)
            if base_size[0] <= 0 or base_size[1] <= 0:
                continue
            scale = min(
                box_width / base_size[0],
                max(1, box_height - base_line) / base_size[1],
            )
            scale = max(0.1, float(scale))
            thickness = max(1, int(round(box_height * 0.08)))
            rendered_size, rendered_base = cv2.getTextSize(
                text, font, scale, thickness
            )
            origin_x = x0 + max(0, (box_width - rendered_size[0]) // 2)
            origin_y = y0 + max(
                rendered_size[1],
                (box_height + rendered_size[1] - rendered_base) // 2,
            )
            cv2.putText(
                mask,
                text,
                (origin_x, min(height - 1, origin_y)),
                font,
                scale,
                255,
                thickness,
                cv2.LINE_AA,
            )
        if int(np.count_nonzero(mask)):
            mask = cv2.dilate(
                mask,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
                iterations=1,
            )
        return mask

    @classmethod
    def _glyph_mask(cls, image, candidates: Sequence[Mapping[str, Any]]):
        import cv2

        union = cls._union_box(candidates)
        components = cls._template_mask(image, union)
        rendered = cls._rendered_text_mask(image, candidates)
        return cv2.bitwise_or(components, rendered)

    @staticmethod
    def _template_mask(image, union_box: Sequence[float]):
        import cv2
        import numpy as np

        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        residual = cv2.subtract(gray, cv2.medianBlur(gray, 9))
        roi = np.zeros_like(gray)
        x0, y0, x1, y1 = ImageLocalizationStore._pixel_box(union_box, width, height)
        pad = max(3, int(round(min(width, height) * 0.006)))
        cv2.rectangle(
            roi,
            (max(0, x0 - pad), max(0, y0 - pad)),
            (min(width - 1, x1 + pad), min(height - 1, y1 + pad)),
            255,
            -1,
        )
        seed = np.where((roi > 0) & (residual > 14), 255, 0).astype(np.uint8)
        seed = cv2.morphologyEx(
            seed,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        )
        count, labels, stats, _ = cv2.connectedComponentsWithStats(seed)
        mask = np.zeros_like(gray)
        for index in range(1, count):
            left, top, component_width, component_height, area = [
                int(value) for value in stats[index]
            ]
            if (
                area < max(4, int(width * height * 0.00003))
                or component_height < max(3, int(height * 0.005))
                or component_width > int(width * 0.14)
                or component_height > int(height * 0.06)
            ):
                continue
            mask[labels == index] = 255
        if int(np.count_nonzero(mask)):
            mask = cv2.dilate(
                mask,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                iterations=1,
            )
        return mask

    @classmethod
    def plan_from_candidates(
        cls,
        images: Mapping[str, Any],
        candidates_by_asset: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        import numpy as np

        dimensions: dict[tuple[int, int], list[str]] = {}
        for asset_id, image in images.items():
            height, width = image.shape[:2]
            dimensions.setdefault((width, height), []).append(asset_id)
        plans: dict[str, dict[str, Any]] = {}
        for asset_ids in dimensions.values():
            strong_assets: list[tuple[float, str, list[float]]] = []
            for asset_id in asset_ids:
                candidates = list(candidates_by_asset.get(asset_id) or [])
                if not candidates:
                    continue
                union = cls._union_box(candidates)
                width = union[2] - union[0]
                text = " ".join(str(row.get("text") or "").lower() for row in candidates)
                if width >= 0.25 or "shop" in text or "1688" in text:
                    strong_assets.append((width, asset_id, union))
            strong_assets.sort(reverse=True)
            exemplar = strong_assets[0] if strong_assets else None
            template_mask = None
            if exemplar is not None:
                exemplar_candidates = list(
                    candidates_by_asset.get(exemplar[1]) or []
                )
                template_mask = cls._glyph_mask(
                    images[exemplar[1]], exemplar_candidates
                )
                if not int(np.count_nonzero(template_mask)):
                    template_mask = None
            for asset_id in asset_ids:
                candidates = list(candidates_by_asset.get(asset_id) or [])
                if not candidates:
                    continue
                union = cls._union_box(candidates)
                union_width = union[2] - union[0]
                text = " ".join(str(row.get("text") or "").lower() for row in candidates)
                direct = union_width >= 0.25 or "shop" in text or "1688" in text
                if direct:
                    selective_mask = cls._glyph_mask(
                        images[asset_id], candidates
                    )
                    if int(np.count_nonzero(selective_mask)):
                        mask = selective_mask
                        strategy = "glyph_ocr_mask"
                    else:
                        mask = cls._direct_mask(images[asset_id], candidates)
                        strategy = "direct_ocr_boxes"
                    regions = [
                        {
                            "region_id": f"auto-watermark-{index + 1}",
                            "bbox": list(row["bbox"]),
                            "text": str(row.get("text") or "")[:2000],
                            "classification": "watermark",
                            "origin": "local_detector",
                            "confidence": float(row.get("confidence") or 0),
                        }
                        for index, row in enumerate(candidates)
                    ]
                elif template_mask is not None:
                    mask = template_mask.copy()
                    regions = [{
                        "region_id": "auto-watermark-repeated",
                        "bbox": list(exemplar[2]),
                        "text": "repeated sibling edge watermark",
                        "classification": "watermark",
                        "origin": "local_detector",
                        "confidence": max(float(row.get("confidence") or 0) for row in candidates),
                    }]
                    strategy = "repeated_sibling_template"
                else:
                    continue
                if int(np.count_nonzero(mask)):
                    plans[asset_id] = {
                        "strategy": strategy,
                        "mask": mask,
                        "regions": regions,
                    }
        return plans

    def analyze(self, images: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        engine = self._new_engine()
        candidates = {
            asset_id: self._scan(image, engine=engine)
            for asset_id, image in images.items()
        }
        return self.plan_from_candidates(images, candidates)


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

    def auto_clean_watermarks(
        self,
        offer_id: object,
        *,
        expected_revision: object,
        source_paths: Mapping[str, Path],
        detector: LocalWatermarkDetector | None = None,
    ) -> dict[str, Any]:
        """Detect and locally inpaint edge watermarks for all source siblings.

        This is a local derived-artifact operation.  Source files are read only,
        and only URL/shop-shaped edge OCR is eligible for removal.
        """
        with self._lock:
            manifest = self.load(offer_id)
            if not manifest:
                raise ImageLocalizationValidationError(
                    "image localization manifest is missing"
                )
            self._check_revision(manifest, expected_revision)
            try:
                import cv2
                import numpy as np
            except ImportError as exc:  # pragma: no cover - deployment guard
                raise ImageLocalizationValidationError(
                    "local watermark cleanup dependencies are unavailable"
                ) from exc
            images: dict[str, Any] = {}
            source_bytes_by_asset: dict[str, bytes] = {}
            for asset in manifest.get("assets") or []:
                asset_id = str(asset.get("asset_id") or "")
                source_path = Path(source_paths.get(asset_id) or "")
                if not source_path.is_file():
                    raise ImageLocalizationValidationError(
                        f"source image file is unavailable for {asset_id}"
                    )
                source_bytes = source_path.read_bytes()
                image = cv2.imdecode(
                    np.frombuffer(source_bytes, dtype=np.uint8),
                    cv2.IMREAD_COLOR,
                )
                if image is None:
                    raise ImageLocalizationValidationError(
                        f"source image is invalid for {asset_id}"
                    )
                source_bytes_by_asset[asset_id] = source_bytes
                images[asset_id] = image
            active_detector = detector or LocalWatermarkDetector()
            plans = active_detector.analyze(images)
            created = 0
            detected_regions = 0
            for asset in manifest.get("assets") or []:
                asset_id = str(asset.get("asset_id") or "")
                plan = plans.get(asset_id)
                existing = [
                    row
                    for row in (asset.get("regions") or [])
                    if isinstance(row, dict)
                    and row.get("origin") != "local_detector"
                ]
                if not plan:
                    asset["watermark_detection"] = {
                        "status": "not_detected",
                        "provider": active_detector.provider_name,
                        "provider_version": active_detector.provider_version,
                        "scanned_at": _now(),
                    }
                    continue
                automatic = [
                    _normalize_region(row, default_origin="local_detector")
                    for row in plan.get("regions") or []
                ]
                protected = [
                    row
                    for row in existing
                    if row.get("classification") in PROTECTED_CLASSIFICATIONS
                ]
                removals = [
                    row
                    for row in existing + automatic
                    if row.get("classification") in REMOVAL_CLASSIFICATIONS
                ]
                for removal in removals:
                    if any(
                        _boxes_overlap(removal["bbox"], safe["bbox"])
                        for safe in protected
                    ):
                        raise ImageLocalizationValidationError(
                            "automatic watermark removal overlaps a protected region"
                        )
                mask = plan.get("mask")
                image = images[asset_id]
                if not isinstance(mask, np.ndarray) or mask.shape != image.shape[:2]:
                    raise ImageLocalizationValidationError(
                        "automatic watermark mask is invalid"
                    )
                mask = mask.astype(np.uint8, copy=True)
                for row in existing:
                    if row.get("classification") not in REMOVAL_CLASSIFICATIONS:
                        continue
                    x0, y0, x1, y1 = self._pixel_box(
                        row["bbox"], image.shape[1], image.shape[0]
                    )
                    cv2.rectangle(mask, (x0, y0), (x1, y1), 255, -1)
                if not int(np.count_nonzero(mask)):
                    continue
                cleaned = cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)
                ok, encoded = cv2.imencode(".png", cleaned)
                if not ok:
                    raise ImageLocalizationValidationError(
                        "automatic watermark artifact could not be encoded"
                    )
                artifact_bytes = encoded.tobytes()
                artifact_digest = _sha256(artifact_bytes)
                artifact_id = f"clean-{artifact_digest[:20]}"
                artifact_path = (
                    self._offer_dir(offer_id)
                    / "artifacts"
                    / f"{artifact_id}.png"
                )
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                if artifact_path.exists() and artifact_path.read_bytes() != artifact_bytes:
                    raise ImageLocalizationValidationError("artifact identity collision")
                if not artifact_path.exists():
                    artifact_path.write_bytes(artifact_bytes)
                asset["regions"] = existing + automatic
                asset["watermark_detection"] = {
                    "status": "detected",
                    "provider": active_detector.provider_name,
                    "provider_version": active_detector.provider_version,
                    "strategy": str(plan.get("strategy") or ""),
                    "region_count": len(automatic),
                    "scanned_at": _now(),
                }
                asset["clean_master"] = {
                    "status": "created",
                    "artifact_id": artifact_id,
                    "artifact_digest": artifact_digest,
                    "source_content_digest": _sha256(
                        source_bytes_by_asset[asset_id]
                    ),
                    "source_identity_digest": asset["source_identity_digest"],
                    "method": LOCAL_AUTO_WATERMARK_METHOD,
                    "removal_region_ids": [row["region_id"] for row in removals],
                    "protected_region_ids": [row["region_id"] for row in protected],
                    "created_at": _now(),
                }
                created += 1
                detected_regions += len(automatic)
            manifest["last_auto_watermark_scan"] = {
                "provider": active_detector.provider_name,
                "provider_version": active_detector.provider_version,
                "created_asset_count": created,
                "detected_region_count": detected_regions,
                "scanned_asset_count": len(images),
                "scanned_at": _now(),
            }
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
