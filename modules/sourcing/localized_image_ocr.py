"""Local-only OCR normalization for approved image masters."""

from __future__ import annotations

import hashlib
import json
import re
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image


OCR_PROVIDER = "rapidocr-local/v1"
MIN_CONFIDENCE = 0.45
MAX_REGIONS = 80


class LocalizedImageOcrError(ValueError):
    """The local OCR result cannot be safely bound to the image."""


def _region_id(image_digest: str, bbox: list[float], text: str) -> str:
    raw = json.dumps(
        {"image_digest": image_digest, "bbox": bbox, "text": text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"text-{hashlib.sha256(raw).hexdigest()[:20]}"


def _engine_result(engine: Any, image: np.ndarray) -> list[Any]:
    raw = engine(image)
    if isinstance(raw, tuple):
        raw = raw[0]
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LocalizedImageOcrError("local OCR returned an invalid result")
    return raw


def detect_english_text_regions(
    image_bytes: bytes, *, engine: Any | None = None
) -> list[dict[str, Any]]:
    """Detect confident English text locally and return normalized image boxes.

    This function performs no network calls.  The optional engine seam is used
    by tests; production lazily loads RapidOCR's local ONNX models.
    """

    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise LocalizedImageOcrError("image bytes are required")
    image_digest = hashlib.sha256(bytes(image_bytes)).hexdigest()
    try:
        with Image.open(BytesIO(bytes(image_bytes))) as opened:
            rgb = opened.convert("RGB")
            width, height = rgb.size
            image = np.asarray(rgb)
    except Exception as error:
        raise LocalizedImageOcrError("image bytes are invalid") from error
    if width <= 0 or height <= 0:
        raise LocalizedImageOcrError("image dimensions are invalid")
    if engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            engine = RapidOCR()
        except Exception as error:
            raise LocalizedImageOcrError("local RapidOCR is unavailable") from error

    regions: list[dict[str, Any]] = []
    for raw in _engine_result(engine, image):
        if not isinstance(raw, (list, tuple)) or len(raw) < 3:
            continue
        points, raw_text, raw_confidence = raw[:3]
        text = re.sub(r"\s+", " ", str(raw_text or "")).strip()
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if (
            not text
            or len(text) > 500
            or confidence < MIN_CONFIDENCE
            or not re.search(r"[A-Za-z]", text)
        ):
            continue
        if not isinstance(points, (list, tuple)) or len(points) < 2:
            continue
        try:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
        except (TypeError, ValueError, IndexError):
            continue
        bbox = [
            round(max(0.0, min(xs) / width), 6),
            round(max(0.0, min(ys) / height), 6),
            round(min(1.0, max(xs) / width), 6),
            round(min(1.0, max(ys) / height), 6),
        ]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        regions.append(
            {
                "region_id": _region_id(image_digest, bbox, text),
                "source_text": text,
                "bbox": bbox,
                "confidence": round(confidence, 6),
                "origin": OCR_PROVIDER,
            }
        )
        if len(regions) >= MAX_REGIONS:
            break
    ids = [row["region_id"] for row in regions]
    if len(ids) != len(set(ids)):
        raise LocalizedImageOcrError("local OCR region identity is ambiguous")
    return regions
