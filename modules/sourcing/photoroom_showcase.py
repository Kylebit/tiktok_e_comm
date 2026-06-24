"""Photoroom 全能力试跑：对当前货源生成所有 recipe 样例图。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.config import ROOT
from modules.products import image_ai
from modules.sourcing.pipeline import (
    _load_existing_assets,
    _public_url,
    load_scrape,
    offer_dir,
)

SHOWCASE_MANIFEST = "photoroom_showcase.json"

# 详情页试跑：选 3 张有代表性的详情图 + 适用 recipe
DETAIL_SAMPLES: list[dict] = [
    {"file": "detail_01.jpg", "label": "详情·场景方图"},
    {"file": "detail_02.jpg", "label": "详情·尺寸条"},
    {"file": "detail_08.jpg", "label": "详情·功能近景"},
]

DETAIL_RECIPES: list[str] = [
    "prep_dewatermark",
    "prep_upscale",
    "main_white",
    "scene_bedroom",
    "edit_detail",
    "edit_staging",
]


def showcase_dir(offer_id: str) -> Path:
    return offer_dir(offer_id) / "photoroom_showcase"


def manifest_path(offer_id: str) -> Path:
    return offer_dir(offer_id) / SHOWCASE_MANIFEST


def load_showcase(offer_id: str) -> dict | None:
    p = manifest_path(offer_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _run_one(
    *,
    offer_id: str,
    recipe_id: str,
    local_path: Path,
    source_urls: list[str],
    product_name: str,
    out_subdir: str,
    out_name: str,
) -> dict:
    spec = image_ai.RECIPE_CATALOG.get(recipe_id) or {}
    cfg = image_ai.image_config()
    ext = "jpg" if cfg["export_format"] in ("jpeg", "jpg") else cfg["export_format"]
    dest_dir = showcase_dir(offer_id) / out_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{out_name}_{recipe_id}.{ext}"

    jobs = image_ai.plan_recipe_jobs(
        source_urls,
        [recipe_id],
        product_name=product_name,
    )
    if not jobs:
        return {
            "recipe_id": recipe_id,
            "label": spec.get("label") or recipe_id,
            "kind": spec.get("kind") or "",
            "status": "error",
            "error": "无 recipe job",
        }

    job = jobs[0]
    entry: dict = {
        "recipe_id": recipe_id,
        "label": job.get("label") or spec.get("label") or recipe_id,
        "kind": spec.get("kind") or "",
        "tiktok_hint": spec.get("tiktok_hint") or "",
        "source_file": local_path.name,
        "status": "pending",
    }
    try:
        if local_path.is_file():
            img_bytes = image_ai._photoroom_edit_file(
                local_path,
                job["params"],
                job.get("headers"),
                remove_background=job.get("remove_background", True),
                use_white_bg=job.get("use_white_bg", True),
            )
        else:
            img_bytes = image_ai._photoroom_edit(
                job["source_url"],
                job["params"],
                job.get("headers"),
                remove_background=job.get("remove_background", True),
                use_white_bg=job.get("use_white_bg", True),
            )
        dest.write_bytes(img_bytes)
        rel = str(dest.relative_to(ROOT))
        entry.update(
            {
                "status": "ok",
                "path": rel,
                "url": _public_url(rel),
                "bytes": len(img_bytes),
            }
        )
    except Exception as e:
        entry.update({"status": "error", "error": str(e)[:300]})
    return entry


def build_showcase(
    offer_id: str,
    *,
    progress: Callable[[str], None] | None = None,
    include_detail: bool = True,
) -> dict:
    """对 offer 跑全部 Photoroom recipe，写入 manifest。"""
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    if not image_ai.image_enabled():
        raise RuntimeError("未配置 Photoroom API Key（images.photoroom_api_key）")

    data = load_scrape(offer_id)
    assets = _load_existing_assets(offer_id)
    raw_main = assets.get("raw_main") or []
    if not raw_main:
        raise FileNotFoundError("请先构建/下载原图（main.py sourcing build）")

    main_urls = (data.get("images") or {}).get("main") or []
    product_name = (data.get("title") or "")[:120]
    main_local = ROOT / raw_main[0]
    source_urls = main_urls if main_urls else [""]

    cfg = image_ai.image_config()
    recipe_ids = list(image_ai.RECIPE_CATALOG.keys())
    groups: dict[str, list[dict]] = {
        "main": [],
        "scene": [],
        "prep": [],
        "explore": [],
        "detail_page": [],
    }

    _log(f"主图试跑 {len(recipe_ids)} 个 recipe…")
    for i, rid in enumerate(recipe_ids):
        spec = image_ai.RECIPE_CATALOG[rid]
        kind = spec.get("kind") or "other"
        _log(f"[{i + 1}/{len(recipe_ids)}] {spec.get('label') or rid}")
        entry = _run_one(
            offer_id=offer_id,
            recipe_id=rid,
            local_path=main_local,
            source_urls=source_urls,
            product_name=product_name,
            out_subdir="main",
            out_name="main01",
        )
        bucket = "explore" if kind in ("staging", "angle", "scale", "detail") else kind
        if bucket not in groups:
            bucket = "explore"
        groups[bucket].append(entry)
        if cfg["request_delay_sec"] > 0 and i + 1 < len(recipe_ids):
            time.sleep(cfg["request_delay_sec"])

    if include_detail:
        raw_dir = offer_dir(offer_id) / "raw"
        for sample in DETAIL_SAMPLES:
            fp = raw_dir / sample["file"]
            if not fp.is_file():
                continue
            for j, rid in enumerate(DETAIL_RECIPES):
                spec = image_ai.RECIPE_CATALOG.get(rid) or {}
                _log(f"详情 {sample['file']} · {spec.get('label') or rid}")
                entry = _run_one(
                    offer_id=offer_id,
                    recipe_id=rid,
                    local_path=fp,
                    source_urls=source_urls,
                    product_name=product_name,
                    out_subdir="detail",
                    out_name=Path(sample["file"]).stem,
                )
                entry["detail_label"] = sample["label"]
                groups["detail_page"].append(entry)
                if cfg["request_delay_sec"] > 0:
                    time.sleep(cfg["request_delay_sec"])

    all_items = [x for g in groups.values() for x in g]
    manifest = {
        "offer_id": offer_id,
        "title": data.get("title") or "",
        "source_main": raw_main[0],
        "recipe_catalog": [
            {
                "id": rid,
                "label": spec.get("label"),
                "kind": spec.get("kind"),
                "tiktok_hint": spec.get("tiktok_hint"),
            }
            for rid, spec in image_ai.RECIPE_CATALOG.items()
        ],
        "groups": groups,
        "summary": {
            "total": len(all_items),
            "ok": sum(1 for x in all_items if x.get("status") == "ok"),
            "error": sum(1 for x in all_items if x.get("status") == "error"),
        },
        "sandbox": cfg.get("sandbox"),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path(offer_id).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def resolve_showcase_asset(offer_id: str, file_path: str) -> Path | None:
    base = showcase_dir(offer_id).resolve()
    target = (base / file_path).resolve()
    if not str(target).startswith(str(base)):
        return None
    if not target.is_file():
        return None
    return target
