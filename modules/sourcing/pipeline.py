"""1688 选品流水线：下载原图 → AI 文案 → Photoroom 9 槽位。"""

from __future__ import annotations

import json
import re
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.config import ROOT
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry

from modules.products import image_ai
from modules.sourcing.copy_ai import generate_copy
from modules.sourcing.image_classify import CLASS_LABELS, classify_assets

SOURCING_DIR = ROOT / "data" / "sourcing"

# v1：全 Photoroom（保留兼容）
SLOT_PLAN_V1: list[dict] = [
    {"slot": 1, "title": "主图白底", "recipe": "prep_dewatermark", "kind": "photoroom"},
    {"slot": 2, "title": "卧室场景", "recipe": "scene_bedroom", "kind": "photoroom"},
    {"slot": 3, "title": "尺寸对比", "recipe": "scene_scale", "kind": "photoroom"},
    {"slot": 4, "title": "细节图", "recipe": None, "kind": "raw", "source_index": 2},
    {"slot": 5, "title": "多角度", "recipe": None, "kind": "raw", "source_index": 3},
    {"slot": 6, "title": "第二主图", "recipe": None, "kind": "raw", "source_index": 1},
    {"slot": 7, "title": "客厅场景", "recipe": "scene_living", "kind": "photoroom"},
    {"slot": 8, "title": "变体色卡", "recipe": None, "kind": "variants"},
    {"slot": 9, "title": "信任/评价", "recipe": None, "kind": "placeholder"},
]

# v2（路径 A）：supplier 优先 + #1 三候选 AI 白底
SLOT_PLAN_V2: list[dict] = [
    {"slot": 1, "title": "主图白底", "kind": "hero_pick"},
    {"slot": 2, "title": "使用场景", "kind": "supplier", "class": "scene"},
    {"slot": 3, "title": "尺寸规格", "kind": "supplier", "class": "size"},
    {"slot": 4, "title": "细节特写", "kind": "supplier", "class": "detail"},
    {"slot": 5, "title": "功能/材质", "kind": "supplier", "class": "detail", "fallback_class": "marketing"},
    {"slot": 6, "title": "第二主图", "kind": "supplier_main", "source_index": 1},
    {"slot": 7, "title": "卖点长图", "kind": "supplier", "class": "marketing", "manual_review": True},
    {"slot": 8, "title": "变体色卡", "kind": "variants"},
    {"slot": 9, "title": "信任/评价", "kind": "placeholder"},
]

HERO_RECIPES: list[dict] = [
    {"id": "main_white", "label": "标准白底"},
    {"id": "main_shadow", "label": "白底+软阴影"},
    {"id": "main_relight", "label": "白底+补光"},
]

SLOT_PLAN = SLOT_PLAN_V1  # 默认别名

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def offer_dir(offer_id: str) -> Path:
    return SOURCING_DIR / offer_id


def draft_path(offer_id: str) -> Path:
    return SOURCING_DIR / f"{offer_id}_draft.json"


def scrape_path(offer_id: str) -> Path:
    return SOURCING_DIR / f"{offer_id}.json"


def load_scrape(offer_id: str) -> dict:
    p = scrape_path(offer_id)
    if not p.is_file():
        raise FileNotFoundError(f"未找到采集数据: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def load_draft(offer_id: str) -> dict | None:
    p = draft_path(offer_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_offers() -> list[dict]:
    out: list[dict] = []
    if not SOURCING_DIR.is_dir():
        return out
    for p in sorted(SOURCING_DIR.glob("*.json")):
        if p.name.endswith("_draft.json"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        oid = data.get("num_iid") or p.stem
        draft = load_draft(oid)
        out.append(
            {
                "offer_id": oid,
                "title": data.get("title") or "",
                "price": (data.get("price") or {}).get("display") or "",
                "sku_count": len(data.get("skus") or []),
                "has_draft": draft is not None,
                "built_at": (draft or {}).get("built_at"),
                "scraped_at": data.get("scraped_at"),
            }
        )
    return out


def _download_url(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Referer": "https://detail.1688.com/"},
        method="GET",
    )
    with urlopen_retry(req, timeout=60, context=SSL_CTX) as resp:
        dest.write_bytes(resp.read())


def _safe_name(url: str, prefix: str, index: int) -> str:
    ext = "jpg"
    m = re.search(r"\.(jpe?g|png|webp)", url, re.I)
    if m:
        ext = "jpg" if m.group(1).lower() in ("jpeg", "jpg") else m.group(1).lower()
    return f"{prefix}_{index:02d}.{ext}"


def download_images(
    offer_id: str,
    data: dict,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """下载主图/详情图到 data/sourcing/{id}/raw/。"""
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    base = offer_dir(offer_id) / "raw"
    imgs = data.get("images") or {}
    main_urls = imgs.get("main") or []
    detail_urls = imgs.get("detail") or []

    raw_main: list[str] = []
    for i, url in enumerate(main_urls):
        fname = _safe_name(url, "main", i + 1)
        dest = base / fname
        _log(f"下载主图 {i + 1}/{len(main_urls)}")
        try:
            _download_url(url, dest)
            raw_main.append(str(dest.relative_to(ROOT)))
        except Exception as e:
            _log(f"主图 {i + 1} 失败: {e}")

    raw_detail: list[str] = []
    for i, url in enumerate(detail_urls):
        fname = _safe_name(url, "detail", i + 1)
        dest = base / fname
        _log(f"下载详情图 {i + 1}/{len(detail_urls)}")
        try:
            _download_url(url, dest)
            raw_detail.append(str(dest.relative_to(ROOT)))
        except Exception as e:
            _log(f"详情图 {i + 1} 失败: {e}")

    return {"raw_main": raw_main, "raw_detail": raw_detail}


def _public_url(path: str) -> str:
    rel = path.replace("\\", "/")
    if rel.startswith("data/sourcing/"):
        parts = rel.split("/")
        if len(parts) >= 4:
            offer_id = parts[2]
            sub = "/".join(parts[3:])
            return f"/api/sourcing/asset?offer_id={urllib.parse.quote(offer_id)}&file={urllib.parse.quote(sub)}"
    return ""


def generate_hero_candidates(
    offer_id: str,
    data: dict,
    assets: dict,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict], list[str]]:
    """#1 主图：3 种白底 recipe 候选，供人工点选。"""
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    errors: list[str] = []
    candidates: list[dict] = []
    raw_main = assets.get("raw_main") or []
    main_urls = (data.get("images") or {}).get("main") or []
    if not raw_main and not main_urls:
        return candidates, ["无主图"]

    local_src = ROOT / raw_main[0] if raw_main else None
    product_name = (data.get("title") or "")[:120]
    out_dir = offer_dir(offer_id) / "hero_candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = image_ai.image_config()
    ext = "jpg" if cfg["export_format"] in ("jpeg", "jpg") else cfg["export_format"]

    if not image_ai.image_enabled():
        if local_src and local_src.is_file():
            dest = out_dir / f"fallback_main01.{ext}"
            shutil.copy2(local_src, dest)
            rel = str(dest.relative_to(ROOT))
            candidates.append(
                {
                    "id": "fallback_raw",
                    "recipe": "raw",
                    "label": "原图（未配置 Photoroom）",
                    "path": rel,
                    "url": _public_url(rel),
                    "source": "supplier",
                }
            )
        return candidates, errors

    for spec in HERO_RECIPES:
        rid = spec["id"]
        _log(f"主图候选: {spec['label']}")
        jobs = image_ai.plan_recipe_jobs(
            main_urls[:1] if main_urls else [""],
            [rid],
            product_name=product_name,
        )
        if not jobs:
            continue
        job = jobs[0]
        dest = out_dir / f"hero_{rid}.{ext}"
        try:
            if local_src and local_src.is_file():
                img_bytes = image_ai._photoroom_edit_file(
                    local_src,
                    job["params"],
                    job.get("headers"),
                    remove_background=job.get("remove_background", True),
                    use_white_bg=job.get("use_white_bg", True),
                )
            elif main_urls:
                img_bytes = image_ai._photoroom_edit(
                    main_urls[0],
                    job["params"],
                    job.get("headers"),
                    remove_background=job.get("remove_background", True),
                    use_white_bg=job.get("use_white_bg", True),
                )
            else:
                continue
            dest.write_bytes(img_bytes)
            rel = str(dest.relative_to(ROOT))
            candidates.append(
                {
                    "id": rid,
                    "recipe": rid,
                    "label": spec["label"],
                    "path": rel,
                    "url": _public_url(rel),
                    "source": "ai",
                }
            )
            if cfg["request_delay_sec"] > 0:
                import time
                time.sleep(cfg["request_delay_sec"])
        except Exception as e:
            errors.append(f"主图候选 {rid}: {e}")

    return candidates, errors


def _copy_to_slot(
    offer_id: str,
    src_rel: str,
    slot_no: int,
    suffix: str = "supplier",
) -> str:
    dest = offer_dir(offer_id) / "slots" / f"slot{slot_no:02d}_{suffix}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / src_rel, dest)
    return str(dest.relative_to(ROOT))


def generate_slots_v2(
    offer_id: str,
    data: dict,
    assets: dict,
    classification: dict,
    hero_candidates: list[dict],
    *,
    selections: dict | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict], list[str]]:
    """路径 A：supplier 填槽 + #1 用已选或首个 hero 候选。"""
    errors: list[str] = []
    slots: list[dict] = []
    raw_main = assets.get("raw_main") or []
    skus = data.get("skus") or []
    hints = classification.get("slot_hints") or {}
    sel = selections or {}
    hero_pick = sel.get("hero") or sel.get("1")

    chosen_hero = None
    for c in hero_candidates:
        if c.get("id") == hero_pick or c.get("recipe") == hero_pick:
            chosen_hero = c
            break
    if not chosen_hero and hero_candidates:
        chosen_hero = hero_candidates[0]

    by_class: dict[str, list[str]] = classification.get("by_class") or {}
    used: set[str] = set()

    def _resolve_supplier(slot_no: int, plan: dict) -> str | None:
        override = (sel.get("slots") or sel.get(str(slot_no)))
        if isinstance(override, str) and override:
            return override
        hint = hints.get(slot_no) or hints.get(str(slot_no)) or {}
        if hint.get("path"):
            return hint["path"]
        cls = plan.get("class")
        fb = plan.get("fallback_class")
        for key in (cls, fb):
            if not key:
                continue
            for p in by_class.get(key, []):
                if p not in used:
                    return p
        return None

    for plan in SLOT_PLAN_V2:
        slot_no = plan["slot"]
        entry: dict = {
            "slot": slot_no,
            "title": plan["title"],
            "kind": plan["kind"],
            "status": "pending",
        }

        if plan["kind"] == "placeholder":
            entry.update(
                {
                    "status": "manual",
                    "source": "manual",
                    "note": "需人工上传评价截图或品牌信任素材",
                }
            )
            slots.append(entry)
            continue

        if plan["kind"] == "hero_pick":
            if chosen_hero and chosen_hero.get("path"):
                rel = _copy_to_slot(offer_id, chosen_hero["path"], slot_no, "hero")
                entry.update(
                    {
                        "status": "ok",
                        "path": rel,
                        "url": _public_url(rel),
                        "source": "ai",
                        "recipe": chosen_hero.get("recipe"),
                        "label": chosen_hero.get("label"),
                        "hero_candidates": hero_candidates,
                        "selected_hero": chosen_hero.get("id"),
                    }
                )
            else:
                entry.update({"status": "error", "error": "无主图候选", "hero_candidates": hero_candidates})
                errors.append("槽位1: 无主图候选")
            slots.append(entry)
            continue

        if plan["kind"] == "variants":
            variant_paths: list[str] = []
            for i, sku in enumerate(skus[:6]):
                src_idx = min(i, len(raw_main) - 1) if raw_main else 0
                if not raw_main:
                    continue
                rel_src = raw_main[src_idx]
                fname = offer_dir(offer_id) / "slots" / f"slot08_{sku.get('spec') or i + 1}.jpg"
                try:
                    shutil.copy2(ROOT / rel_src, fname)
                    variant_paths.append(str(fname.relative_to(ROOT)))
                except Exception as e:
                    errors.append(f"槽位8 {sku.get('spec')}: {e}")
            entry.update(
                {
                    "status": "ok" if variant_paths else "error",
                    "paths": variant_paths,
                    "labels": [s.get("spec") or "" for s in skus[:6]],
                    "url": _public_url(variant_paths[0]) if variant_paths else "",
                    "source": "supplier",
                }
            )
            slots.append(entry)
            continue

        if plan["kind"] == "supplier_main":
            idx = plan.get("source_index") or 1
            if idx < len(raw_main):
                rel_src = raw_main[idx]
                used.add(rel_src)
                rel_out = _copy_to_slot(offer_id, rel_src, slot_no)
                entry.update(
                    {
                        "status": "ok",
                        "path": rel_out,
                        "url": _public_url(rel_out),
                        "source": "supplier",
                        "class": "main",
                    }
                )
            else:
                entry.update({"status": "error", "error": "原图不足", "source": "supplier"})
                errors.append(f"槽位{slot_no}: 原图不足")
            slots.append(entry)
            continue

        # supplier by class
        rel_src = _resolve_supplier(slot_no, plan)
        if not rel_src:
            # 任意未用详情图兜底
            for item in classification.get("detail") or []:
                p = item.get("path") if isinstance(item, dict) else item
                if p and p not in used:
                    rel_src = p
                    break
        if rel_src:
            used.add(rel_src)
            rel_out = _copy_to_slot(offer_id, rel_src, slot_no)
            cls = plan.get("class") or "unknown"
            st = "review" if plan.get("manual_review") else "ok"
            entry.update(
                {
                    "status": st,
                    "path": rel_out,
                    "url": _public_url(rel_out),
                    "source": "supplier",
                    "class": cls,
                    "class_label": CLASS_LABELS.get(cls, cls),
                    "note": plan.get("note") or ("待 OCR 英译" if plan.get("manual_review") else ""),
                }
            )
        else:
            entry.update(
                {
                    "status": "empty",
                    "source": "supplier",
                    "class": plan.get("class"),
                    "error": f"无 {CLASS_LABELS.get(plan.get('class') or '', '')} 类详情图",
                }
            )
            errors.append(f"槽位{slot_no}: 缺少 {plan.get('class')} 类图")
        slots.append(entry)

    return slots, errors


def generate_slots(
    offer_id: str,
    data: dict,
    assets: dict,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict], list[str]]:
    """生成 TikTok 9 槽位图片。"""
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    errors: list[str] = []
    slots: list[dict] = []
    main_urls = (data.get("images") or {}).get("main") or []
    if not main_urls:
        return slots, ["无主图 URL"]

    out_dir = offer_dir(offer_id) / "slots"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_main = assets.get("raw_main") or []
    skus = data.get("skus") or []
    product_name = (data.get("title") or "")[:120]

    photoroom_ok = image_ai.image_enabled()
    cfg = image_ai.image_config()

    for plan in SLOT_PLAN_V1:
        slot_no = plan["slot"]
        entry: dict = {
            "slot": slot_no,
            "title": plan["title"],
            "kind": plan["kind"],
            "status": "pending",
        }

        if plan["kind"] == "placeholder":
            entry.update(
                {
                    "status": "manual",
                    "note": "需人工上传评价截图或品牌信任素材",
                }
            )
            slots.append(entry)
            continue

        if plan["kind"] == "variants":
            variant_paths: list[str] = []
            for i, sku in enumerate(skus[:6]):
                src_idx = min(i, len(raw_main) - 1) if raw_main else min(i, len(main_urls) - 1)
                fname = out_dir / f"slot08_{sku.get('spec') or i + 1}.jpg"
                try:
                    if raw_main and src_idx < len(raw_main):
                        shutil.copy2(ROOT / raw_main[src_idx], fname)
                    else:
                        _download_url(main_urls[min(i, len(main_urls) - 1)], fname)
                    rel = str(fname.relative_to(ROOT))
                    variant_paths.append(rel)
                except Exception as e:
                    errors.append(f"槽位8 {sku.get('spec')}: {e}")
            entry.update(
                {
                    "status": "ok" if variant_paths else "error",
                    "paths": variant_paths,
                    "labels": [s.get("spec") or "" for s in skus[:6]],
                    "url": _public_url(variant_paths[0]) if variant_paths else "",
                }
            )
            slots.append(entry)
            continue

        if plan["kind"] == "raw":
            idx = plan.get("source_index") or 0
            if idx < len(raw_main):
                rel = raw_main[idx]
                dest = out_dir / f"slot{slot_no:02d}_raw.jpg"
                shutil.copy2(ROOT / rel, dest)
                rel_out = str(dest.relative_to(ROOT))
                entry.update({"status": "ok", "path": rel_out, "url": _public_url(rel_out), "source": "raw"})
            else:
                entry.update({"status": "error", "error": "原图不足"})
                errors.append(f"槽位{slot_no}: 原图不足")
            slots.append(entry)
            continue

        # photoroom
        recipe = plan.get("recipe")
        if not recipe:
            slots.append(entry)
            continue
        if not photoroom_ok:
            entry.update({"status": "skipped", "error": "未配置 Photoroom API Key"})
            errors.append(f"槽位{slot_no}: 未配置 Photoroom")
            slots.append(entry)
            continue

        _log(f"Photoroom 槽位 {slot_no}: {plan['title']}")
        src_idx = plan.get("source_index") or 0
        local_src = None
        if raw_main and src_idx < len(raw_main):
            candidate = ROOT / raw_main[src_idx]
            if candidate.is_file():
                local_src = candidate
        source_for_job = main_urls
        jobs = image_ai.plan_recipe_jobs(
            source_for_job, [recipe], product_name=product_name
        )
        if not jobs:
            entry.update({"status": "error", "error": "无 recipe"})
            slots.append(entry)
            continue
        job = jobs[0]
        ext = "jpg" if cfg["export_format"] in ("jpeg", "jpg") else cfg["export_format"]
        dest = out_dir / f"slot{slot_no:02d}_{recipe}.{ext}"
        try:
            if local_src:
                img_bytes = image_ai._photoroom_edit_file(
                    local_src,
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
                    "recipe": recipe,
                    "label": job.get("label"),
                    "source": "local" if local_src else "url",
                }
            )
            if cfg["request_delay_sec"] > 0:
                import time
                time.sleep(cfg["request_delay_sec"])
        except Exception as e:
            err = str(e)[:200]
            fallback_idx = min(src_idx, len(raw_main) - 1) if raw_main else -1
            if fallback_idx >= 0:
                try:
                    fb_dest = out_dir / f"slot{slot_no:02d}_fallback.jpg"
                    shutil.copy2(ROOT / raw_main[fallback_idx], fb_dest)
                    rel = str(fb_dest.relative_to(ROOT))
                    entry.update(
                        {
                            "status": "fallback",
                            "path": rel,
                            "url": _public_url(rel),
                            "recipe": recipe,
                            "error": err,
                            "note": "Photoroom 失败，已用原图占位",
                        }
                    )
                except Exception as fb_e:
                    entry.update({"status": "error", "error": err})
                    errors.append(f"槽位{slot_no}: {err} (fallback: {fb_e})")
            else:
                entry.update({"status": "error", "error": err})
                errors.append(f"槽位{slot_no}: {err}")
        slots.append(entry)

    return slots, errors


def _load_existing_assets(offer_id: str) -> dict:
    """读取已下载的原图路径（skip_images 时复用）。"""
    raw_dir = offer_dir(offer_id) / "raw"
    raw_main = sorted(str(p.relative_to(ROOT)) for p in raw_dir.glob("main_*.*"))
    raw_detail = sorted(str(p.relative_to(ROOT)) for p in raw_dir.glob("detail_*.*"))
    return {"raw_main": raw_main, "raw_detail": raw_detail}


def save_selections(offer_id: str, selections: dict) -> dict:
    """保存人工槽位 / 主图选择到草稿。"""
    draft = load_draft(offer_id)
    if not draft:
        raise FileNotFoundError(f"未找到草稿: {offer_id}")
    draft["selections"] = selections
    assets = draft.setdefault("assets", {})
    classification = assets.get("classification") or {}
    hero_candidates = assets.get("hero_candidates") or []
    if classification and hero_candidates:
        slots, errs = generate_slots_v2(
            offer_id,
            draft.get("source") or {},
            assets,
            classification,
            hero_candidates,
            selections=selections,
        )
        assets["slots"] = slots
        draft["errors"] = [e for e in (draft.get("errors") or []) if not e.startswith("槽位")]
        draft["errors"].extend(errs)
    draft["updated_at"] = datetime.now(timezone.utc).isoformat()
    draft_path(offer_id).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return draft


def build_draft(
    offer_id: str,
    *,
    skip_images: bool = False,
    skip_copy: bool = False,
    skip_slots: bool = False,
    plan_version: str = "v2",
    progress: Callable[[str], None] | None = None,
) -> dict:
    """完整构建选品草稿。"""
    data = load_scrape(offer_id)
    prev = load_draft(offer_id) or {}
    errors: list[str] = []

    assets = {"raw_main": [], "raw_detail": []}
    if not skip_images:
        try:
            assets = download_images(offer_id, data, progress=progress)
        except Exception as e:
            errors.append(f"下载图片: {e}")
    else:
        assets = _load_existing_assets(offer_id)

    classification: dict = {}
    try:
        if progress:
            progress("分类详情图…")
        classification = classify_assets(
            assets.get("raw_main") or [],
            assets.get("raw_detail") or [],
        )
    except Exception as e:
        errors.append(f"图片分类: {e}")

    hero_candidates: list[dict] = []
    selections = dict(prev.get("selections") or {})

    copy: dict = dict(prev.get("copy") or {}) if skip_copy else {}
    if not skip_copy:
        try:
            if progress:
                progress("生成多平台文案…")
            copy = generate_copy(data)
        except Exception as e:
            errors.append(f"文案生成: {e}")
            from modules.sourcing.copy_ai import _fallback_copy
            copy = _fallback_copy(data)
            copy["notes"] = f"AI 失败: {e}"

    slots: list[dict] = []
    if not skip_slots:
        try:
            if plan_version == "v2":
                if progress:
                    progress("生成主图三候选…")
                hero_candidates, hero_errs = generate_hero_candidates(
                    offer_id, data, assets, progress=progress
                )
                errors.extend(hero_errs)
                if progress:
                    progress("组装 v2 槽位（supplier 优先）…")
                slots, slot_errors = generate_slots_v2(
                    offer_id,
                    data,
                    assets,
                    classification,
                    hero_candidates,
                    selections=selections,
                    progress=progress,
                )
            else:
                slots, slot_errors = generate_slots(
                    offer_id, data, assets, progress=progress
                )
            errors.extend(slot_errors)
        except Exception as e:
            errors.append(f"槽位生成: {e}")

    draft = {
        "offer_id": offer_id,
        "plan_version": plan_version,
        "source": data,
        "assets": {
            **assets,
            "classification": classification,
            "hero_candidates": hero_candidates,
            "slots": slots,
        },
        "selections": selections,
        "copy": copy,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
    }
    draft_path(offer_id).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return draft


def resolve_asset(offer_id: str, file_path: str) -> Path | None:
    """安全解析本地资源路径。"""
    base = offer_dir(offer_id).resolve()
    target = (base / file_path).resolve()
    if not str(target).startswith(str(base)):
        return None
    if not target.is_file():
        return None
    return target
