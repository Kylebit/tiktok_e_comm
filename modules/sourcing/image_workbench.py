"""1688 选图工作台：原图导出 → 选图 + Photoroom → 选定 TK 主图/详情。"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.config import ROOT
from modules.products import image_ai
from modules.sourcing.pipeline import (
    _load_existing_assets,
    _public_url,
    download_images,
    draft_path,
    load_draft,
    load_scrape,
    offer_dir,
)

WORKBENCH_FILE = "workbench.json"
GENERATED_DIR = "generated"
DEFAULT_RECIPE = "prep_dewatermark"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def workbench_path(offer_id: str) -> Path:
    return offer_dir(offer_id) / WORKBENCH_FILE


def _default_state() -> dict:
    return {
        "generated": [],
        "final": {
            "tiktok_main": [],
            "tiktok_description": [],
        },
        "updated_at": None,
    }


def load_state(offer_id: str) -> dict:
    p = workbench_path(offer_id)
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("generated", [])
        data.setdefault("final", {"tiktok_main": [], "tiktok_description": []})
        return data
    return _default_state()


def save_state(offer_id: str, state: dict) -> dict:
    state["updated_at"] = _now()
    workbench_path(offer_id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    draft = load_draft(offer_id)
    if draft:
        draft["workbench"] = state
        draft["updated_at"] = state["updated_at"]
        draft_path(offer_id).write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return state


def _image_entry(offer_id: str, rel_path: str, *, kind: str, label: str, source_url: str = "") -> dict:
    sub = rel_path.replace("\\", "/")
    if sub.startswith("data/sourcing/"):
        sub = "/".join(sub.split("/")[3:])
    return {
        "path": rel_path.replace("\\", "/"),
        "file": sub,
        "kind": kind,
        "label": label,
        "url": _public_url(rel_path.replace("\\", "/")),
        "source_url": source_url,
        "name": Path(sub).name,
    }


def list_raw_images(offer_id: str, data: dict | None = None) -> dict:
    """列出已下载的全部 1688 原图。"""
    data = data or load_scrape(offer_id)
    assets = _load_existing_assets(offer_id)
    imgs = (data.get("images") or {}) if data else {}
    main_urls = imgs.get("main") or []
    detail_urls = imgs.get("detail") or []

    main: list[dict] = []
    for i, rel in enumerate(assets.get("raw_main") or []):
        main.append(
            _image_entry(
                offer_id,
                rel,
                kind="main",
                label=f"主图 {i + 1}",
                source_url=main_urls[i] if i < len(main_urls) else "",
            )
        )
    detail: list[dict] = []
    for i, rel in enumerate(assets.get("raw_detail") or []):
        detail.append(
            _image_entry(
                offer_id,
                rel,
                kind="detail",
                label=f"详情 {i + 1}",
                source_url=detail_urls[i] if i < len(detail_urls) else "",
            )
        )
    return {
        "main": main,
        "detail": detail,
        "total": len(main) + len(detail),
        "main_urls": main_urls,
        "detail_urls": detail_urls,
    }


def ensure_downloaded(offer_id: str, *, progress=None) -> dict:
    """仅下载 1688 原图，不跑 Photoroom。"""
    data = load_scrape(offer_id)
    assets = download_images(offer_id, data, progress=progress)
    draft = load_draft(offer_id) or {
        "offer_id": offer_id,
        "plan_version": "manual",
        "source": data,
        "assets": {},
        "copy": {},
        "built_at": _now(),
        "errors": [],
    }
    draft["plan_version"] = "manual"
    draft["source"] = data
    draft["assets"] = {**(draft.get("assets") or {}), **assets}
    draft["updated_at"] = _now()
    draft_path(offer_id).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    apply_raw_defaults(offer_id)
    return list_raw_images(offer_id, data)


def apply_raw_defaults(offer_id: str) -> dict:
    """主图默认=全部 1688 主图；详情默认=全部 1688 详情图。"""
    raw = list_raw_images(offer_id)
    state = load_state(offer_id)
    state.setdefault("final", {})
    state["final"]["tiktok_main"] = [x["path"] for x in (raw.get("main") or [])][:9]
    state["final"]["tiktok_description"] = [x["path"] for x in (raw.get("detail") or [])][:20]
    save_state(offer_id, state)
    return state


def batch_dewatermark(
    offer_id: str,
    *,
    progress: Callable[[str], None] | None = None,
    replace_final: bool = True,
) -> dict:
    """对全部原图跑 prep_dewatermark，并替换当前选用为无水印版。"""
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    if not image_ai.image_enabled():
        raise RuntimeError("未配置 Photoroom API Key")

    raw = list_raw_images(offer_id)
    state = load_state(offer_id)
    mapping: dict[str, str] = {}
    items = (raw.get("main") or []) + (raw.get("detail") or [])
    for i, it in enumerate(items):
        _log(f"去水印 {i + 1}/{len(items)} · {it.get('label') or it.get('name')}")
        entry = generate_image(offer_id, source_file=it["file"], recipe_id=DEFAULT_RECIPE)
        mapping[it["path"]] = entry["path"]

    if replace_final:
        final = state.setdefault("final", {"tiktok_main": [], "tiktok_description": []})

        def _repl(paths: list[str]) -> list[str]:
            return [mapping.get(p, p) for p in paths]

        mains = final.get("tiktok_main") or []
        descs = final.get("tiktok_description") or []
        if mains:
            final["tiktok_main"] = _repl(mains)
        else:
            final["tiktok_main"] = [mapping[x["path"]] for x in (raw.get("main") or [])[:9] if x["path"] in mapping]
        if descs:
            final["tiktok_description"] = _repl(descs)
        else:
            final["tiktok_description"] = [
                mapping[x["path"]] for x in (raw.get("detail") or []) if x["path"] in mapping
            ]
        save_state(offer_id, state)
    return get_workbench(offer_id)


def _resolve_source_file(offer_id: str, source_file: str) -> Path:
    base = offer_dir(offer_id).resolve()
    target = (base / source_file).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError("非法路径")
    if not target.is_file():
        raise FileNotFoundError(f"找不到源图: {source_file}")
    allowed = ("raw/", "generated/", "hero_candidates/")
    rel = str(target.relative_to(base)).replace("\\", "/")
    if not any(rel.startswith(p) for p in allowed):
        raise ValueError("仅允许 raw / generated / hero_candidates 下的图片")
    return target


def generate_image(
    offer_id: str,
    *,
    source_file: str,
    recipe_id: str,
    product_name: str | None = None,
) -> dict:
    """对指定原图跑单个 Photoroom recipe。"""
    if not image_ai.image_enabled():
        raise RuntimeError("未配置 Photoroom API Key")

    spec = image_ai.RECIPE_CATALOG.get(recipe_id)
    if not spec:
        raise ValueError(f"未知 recipe: {recipe_id}")

    data = load_scrape(offer_id)
    local_path = _resolve_source_file(offer_id, source_file)
    main_urls = ((data.get("images") or {}).get("main") or []) or [""]
    name = product_name or (data.get("title") or "")[:120]

    jobs = image_ai.plan_recipe_jobs(main_urls, [recipe_id], product_name=name)
    if not jobs:
        raise RuntimeError("无法创建 Photoroom 任务")
    job = jobs[0]

    cfg = image_ai.image_config()
    ext = "jpg" if cfg["export_format"] in ("jpeg", "jpg") else cfg["export_format"]
    stem = Path(source_file).stem
    out_name = f"{uuid.uuid4().hex[:8]}_{recipe_id}_{stem}.{ext}"
    dest = offer_dir(offer_id) / GENERATED_DIR / out_name
    dest.parent.mkdir(parents=True, exist_ok=True)

    img_bytes = image_ai._photoroom_edit_file(
        local_path,
        job["params"],
        job.get("headers"),
        remove_background=job.get("remove_background", True),
        use_white_bg=job.get("use_white_bg", True),
    )
    dest.write_bytes(img_bytes)
    rel = str(dest.relative_to(ROOT)).replace("\\", "/")

    entry = {
        "id": uuid.uuid4().hex[:12],
        "source_file": source_file.replace("\\", "/"),
        "recipe_id": recipe_id,
        "recipe_label": spec.get("label") or recipe_id,
        "path": rel,
        "file": f"{GENERATED_DIR}/{out_name}",
        "url": _public_url(rel),
        "bytes": len(img_bytes),
        "created_at": _now(),
    }
    state = load_state(offer_id)
    state["generated"].insert(0, entry)
    save_state(offer_id, state)
    if cfg["request_delay_sec"] > 0:
        time.sleep(cfg["request_delay_sec"])
    return entry


def _path_pool(offer_id: str, state: dict, data: dict | None = None) -> dict[str, dict]:
    """path → 元数据，供 final 区展示。"""
    pool: dict[str, dict] = {}
    raw_data = list_raw_images(offer_id, data)
    for it in (raw_data.get("main") or []) + (raw_data.get("detail") or []):
        pool[it["path"]] = it
    for g in state.get("generated") or []:
        pool[g["path"]] = {
            "path": g["path"],
            "file": g.get("file") or "",
            "kind": "generated",
            "label": f"{g.get('recipe_label')} ← {Path(g.get('source_file') or '').name}",
            "url": g.get("url") or _public_url(g["path"]),
            "recipe_id": g.get("recipe_id"),
            "source_file": g.get("source_file"),
        }
    return pool


def build_description_html(intro_html: str, image_paths: list[str], offer_id: str) -> str:
    """把选中的详情图拼进 description HTML（TK 详情页用）。"""
    intro = (intro_html or "").strip()
    if intro and not intro.startswith("<"):
        intro = f"<p>{intro}</p>"
    blocks = [intro] if intro else []
    for p in image_paths:
        url = _public_url(p)
        if url:
            blocks.append(f'<p><img src="{url}" alt="detail" style="max-width:100%"/></p>')
    return "\n".join(blocks)


def get_workbench(offer_id: str) -> dict:
    data = load_scrape(offer_id)
    draft = load_draft(offer_id)
    state = load_state(offer_id)
    if draft and draft.get("workbench"):
        state = draft["workbench"]
    raw = list_raw_images(offer_id, data)
    if not (state.get("final") or {}).get("tiktok_main") and raw.get("total"):
        apply_raw_defaults(offer_id)
        state = load_state(offer_id)
    pool = _path_pool(offer_id, state, data)
    final = state.get("final") or {}
    main_paths = final.get("tiktok_main") or []
    desc_paths = final.get("tiktok_description") or []

    def _resolve(paths: list[str]) -> list[dict]:
        out = []
        for p in paths:
            meta = pool.get(p) or {"path": p, "url": _public_url(p), "label": Path(p).name}
            out.append(meta)
        return out

    copy = (draft or {}).get("copy") or {}
    intro = ""
    tiktok_my = (copy.get("tiktok") or {}).get("MY") or {}
    if copy.get("tiktok"):
        intro = tiktok_my.get("description_html") or ""

    cfg = image_ai.image_config()
    resolved_key = cfg.get("photoroom_api_key_resolved") or ""
    photoroom_sandbox = bool(cfg.get("sandbox")) or resolved_key.startswith("sandbox_")

    return {
        "offer_id": offer_id,
        "title": data.get("title") or "",
        "source_url": data.get("url") or "",
        "photoroom_enabled": image_ai.image_enabled(),
        "photoroom_sandbox": photoroom_sandbox,
        "photoroom_note": (
            "当前为 Photoroom 沙盒 Key：输出图会带 Photoroom 水印（不是 1688 水印）。"
            "请在 config/settings.json 设置 images.sandbox=false 并填写 photoroom_api_key_production。"
            if photoroom_sandbox
            else ""
        ),
        "recipes": image_ai.list_recipes(),
        "default_recipe": DEFAULT_RECIPE,
        "raw": raw,
        "generated": state.get("generated") or [],
        "final": {
            "tiktok_main": _resolve(main_paths),
            "tiktok_description": _resolve(desc_paths),
        },
        "copy": {
            "MY": {
                "title": tiktok_my.get("title") or "",
                "description_html": tiktok_my.get("description_html") or "",
            },
            "PH": (copy.get("tiktok") or {}).get("PH") or {},
        },
        "description_preview_html": build_description_html(intro, desc_paths, offer_id),
        "updated_at": state.get("updated_at"),
        "tk_guide": TK_GUIDE,
    }


def save_final(
    offer_id: str,
    *,
    tiktok_main: list[str] | None = None,
    tiktok_description: list[str] | None = None,
) -> dict:
    state = load_state(offer_id)
    final = state.setdefault("final", {})
    if tiktok_main is not None:
        final["tiktok_main"] = [p for p in tiktok_main if p][:9]
    if tiktok_description is not None:
        final["tiktok_description"] = [p for p in tiktok_description if p][:20]
    save_state(offer_id, state)
    return get_workbench(offer_id)


TK_GUIDE = {
    "main_images": "TK 主图（main_images）：搜索/商品页顶部轮播，1–9 张，建议方图 1:1，第 1 张是缩略图。",
    "description": "TK 详情页（description）：独立 HTML 描述区，需单独上传长图并写入 <img>，不会自动用主图拼出来。",
    "workflow": "默认选用 1688 原图；点「一键去水印」后替换为 Photoroom 无水印版。主图/详情分开选用。",
}


def add_to_final(offer_id: str, path: str, target: str) -> dict:
    """target: tiktok_main | tiktok_description"""
    state = load_state(offer_id)
    final = state.setdefault("final", {"tiktok_main": [], "tiktok_description": []})
    key = "tiktok_main" if target == "tiktok_main" else "tiktok_description"
    lst: list[str] = final.setdefault(key, [])
    if path not in lst:
        max_n = 9 if key == "tiktok_main" else 20
        if len(lst) >= max_n:
            raise ValueError(f"已达上限 {max_n} 张")
        lst.append(path)
    save_state(offer_id, state)
    return get_workbench(offer_id)


def remove_from_final(offer_id: str, path: str, target: str) -> dict:
    state = load_state(offer_id)
    final = state.setdefault("final", {"tiktok_main": [], "tiktok_description": []})
    key = "tiktok_main" if target == "tiktok_main" else "tiktok_description"
    lst = final.setdefault(key, [])
    final[key] = [p for p in lst if p != path]
    save_state(offer_id, state)
    return get_workbench(offer_id)


def reorder_final(offer_id: str, target: str, paths: list[str]) -> dict:
    state = load_state(offer_id)
    final = state.setdefault("final", {"tiktok_main": [], "tiktok_description": []})
    key = "tiktok_main" if target == "tiktok_main" else "tiktok_description"
    final[key] = paths
    save_state(offer_id, state)
    return get_workbench(offer_id)
