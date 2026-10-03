#!/usr/bin/env python3
"""Build a GET-only approved-source to existing Shopee media binding receipt."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = MAX_PIXELS if "MAX_PIXELS" in globals() else 40_000_000


SCHEMA = "shopee-existing-media-content-binding/v2"
ALGORITHM = "exif-rgb-contain-pad256-sha256+dhash32-bidirectional/v1"
MAX_DISTANCE = 0.08
MIN_MARGIN = 0.04
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
ALLOWED_HOSTS = frozenset({"tos.lingkeai.vip", "img2.storage-googleapis.com", "cf.shopee.sg",
                           "cf.shopee.com.my", "cf.shopee.co.th", "cf.shopee.vn"})
BUILDER_RELATIVE_PATH = "skills/publish-approved-product/scripts/build_shopee_existing_media_binding.py"
BUILDER_DIGEST_ALGORITHM = "sha256-normalized-lf/v1"


def source_identity(raw: bytes) -> dict[str, str]:
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    blob = b"blob " + str(len(normalized)).encode("ascii") + b"\0" + normalized
    return {
        "algorithm": BUILDER_DIGEST_ALGORITHM,
        "repo_relative_path": BUILDER_RELATIVE_PATH,
        "sha256": "sha256:" + hashlib.sha256(normalized).hexdigest(),
        "git_blob_sha1": "sha1:" + hashlib.sha1(blob).hexdigest(),
    }


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def fetch(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username:
        raise ValueError("media URL is outside the approved HTTPS hosts")
    request = Request(url, headers={"User-Agent": "OrbitHive-readonly-media-binding/1"})
    with urlopen(request, timeout=30) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError("media redirect escaped the approved HTTPS hosts")
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        length = response.headers.get("Content-Length")
        if not content_type.startswith("image/") or (length and int(length) > MAX_BYTES):
            raise ValueError("media response is not a bounded image")
        raw = response.read(MAX_BYTES + 1)
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError("media response size is invalid")
        return raw


def fingerprints(raw: bytes) -> dict:
    with Image.open(BytesIO(raw)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
            raise ValueError("media pixel count is invalid")
        normalized = ImageOps.pad(image, (256, 256), Image.Resampling.LANCZOS,
                                  color=(255, 255, 255), centering=(0.5, 0.5))
        gray = ImageOps.pad(image.convert("L"), (33, 32), Image.Resampling.LANCZOS,
                            color=255, centering=(0.5, 0.5))
        pixels = list(gray.getdata())
        bits = []
        for y in range(32):
            offset = y * 33
            bits.extend(pixels[offset + x] > pixels[offset + x + 1] for x in range(32))
        return {
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "pixel_sha256": "sha256:" + hashlib.sha256(normalized.tobytes()).hexdigest(),
            "width": width, "height": height, "dhash_bits": bits,
        }


def distance(left: dict, right: dict) -> float:
    a, b = left["dhash_bits"], right["dhash_bits"]
    return sum(x != y for x, y in zip(a, b)) / len(a)


def target_route(candidate: dict, label: str) -> tuple[str, list[str]]:
    review = candidate["review_manifest"]
    target = next(row for row in review["targets"] if row["target_label"] == label)
    image_set = next(row for row in review["image_sets"]
                     if row["image_set_id"] == target["image_set_id"])
    return target["image_set_id"], [str(row["url"]) for row in image_set["images"]]


def build(candidate: dict, regional: dict, prior_run_id: str) -> dict:
    regional_digest = regional.get("evidence_digest")
    targets = []
    for observed in regional["targets"]:
        label = observed["target_label"]
        image_set_id, sources = target_route(candidate, label)
        media_ids = [str(value) for value in observed["gallery_image_ids"]]
        provider_urls = [str(value) for value in observed["gallery_image_urls"]]
        if len(sources) != len(media_ids) or len(media_ids) != len(provider_urls):
            raise ValueError(f"{label} media coverage conflicts")
        with ThreadPoolExecutor(max_workers=8) as pool:
            source_fps = list(pool.map(lambda url: fingerprints(fetch(url)), sources))
            provider_fps = list(pool.map(lambda url: fingerprints(fetch(url)), provider_urls))
        if (len({row["sha256"] for row in source_fps}) != len(source_fps)
                or len({row["sha256"] for row in provider_fps}) != len(provider_fps)):
            raise ValueError(f"{label} duplicate image bytes make mapping ambiguous")
        matrix = [[distance(source, provider) for provider in provider_fps]
                  for source in source_fps]
        chosen = [min(range(len(row)), key=row.__getitem__) for row in matrix]
        if len(set(chosen)) != len(chosen):
            raise ValueError(f"{label} perceptual mapping is not one-to-one")
        bindings = []
        for source_index, provider_index in enumerate(chosen):
            ranked = sorted(matrix[source_index])
            column_ranked = sorted(row[provider_index] for row in matrix)
            best = ranked[0]
            margin = ranked[1] - best if len(ranked) > 1 else 1.0
            column_margin = column_ranked[1] - column_ranked[0] if len(column_ranked) > 1 else 1.0
            source, provider = source_fps[source_index], provider_fps[provider_index]
            exact = source["pixel_sha256"] == provider["pixel_sha256"]
            if not exact and (best > MAX_DISTANCE or margin < MIN_MARGIN
                              or column_margin < MIN_MARGIN):
                raise ValueError(f"{label} media mapping is not uniquely verified")
            bindings.append({
                "source_url": sources[source_index],
                "provider_url": provider_urls[provider_index],
                "provider_media_id": media_ids[provider_index],
                "source_sha256": source["sha256"], "provider_sha256": provider["sha256"],
                "source_pixel_sha256": source["pixel_sha256"],
                "provider_pixel_sha256": provider["pixel_sha256"],
                "source_dimensions": [source["width"], source["height"]],
                "provider_dimensions": [provider["width"], provider["height"]],
                "perceptual_distance": round(best, 6),
                "second_best_margin": round(margin, 6),
                "provider_second_best_margin": round(column_margin, 6),
                "match": "NORMALIZED_PIXEL_EXACT" if exact else "UNIQUE_PERCEPTUAL",
            })
        # The recovery contract preserves approved order, so the unique map must
        # also prove that provider gallery order equals approved route order.
        if chosen != list(range(len(chosen))):
            raise ValueError(f"{label} provider gallery order conflicts with approved route")
        targets.append({
            "target_label": label, "item_id": str(observed["item_id"]),
            "image_set_id": image_set_id,
            "route_digest": digest({"ordered_urls": sources}),
            "ordered_source_urls": sources, "ordered_media_ids": media_ids,
            "bindings": bindings,
        })
    builder_identity = source_identity(Path(__file__).read_bytes())
    body = {
        "schema_version": SCHEMA, "status": "VERIFIED_UNIQUE", "product_writes": 0,
        "algorithm": ALGORITHM, "max_perceptual_distance": MAX_DISTANCE,
        "minimum_second_best_margin": MIN_MARGIN,
        "builder_code_sha256": builder_identity["sha256"],
        "builder_source_identity": builder_identity,
        "candidate_digest": candidate["candidate_digest"],
        "prior_run_id": prior_run_id, "regional_readback_digest": regional_digest,
        "targets": targets,
    }
    body["evidence_digest"] = digest(body)
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--regional-readback", type=Path, required=True)
    parser.add_argument("--prior-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    regional = json.loads(args.regional_readback.read_text(encoding="utf-8"))
    receipt = build(candidate, regional, args.prior_run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=args.output.parent, prefix=f".{args.output.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(encoded); temporary = Path(handle.name)
    temporary.replace(args.output)
    print(json.dumps({"output": str(args.output), "evidence_digest": receipt["evidence_digest"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
