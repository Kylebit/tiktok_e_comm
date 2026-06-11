"""主图优化：基于 TikTok listing 原图 → Photoroom 多风格候选（主图 + 场景图）。"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.config import ROOT, get
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry

# Photoroom Image Editing API 能力说明：
# - 主图：白底 / AI 阴影(ai.soft) / 补光 — 不改变商品本体
# - 场景：background.prompt AI 背景 — 商品抠图后置入生成场景（Plus，沙盒可用带水印）
# - 探索：Edit With AI / 去水印 / 放大 — 文档 image-editing-api-plus-plan
# 文档：https://docs.photoroom.com/image-editing-api-plus-plan/

EDIT_STAGING_PROMPT = (
    "Make it a professional lifestyle photoshoot with the provided object as the focus. "
    "Natural elegant lighting, high-end lifestyle photography. "
    "Integrate a subtle human presence or hand interaction with the object for authenticity. "
    "No text, no watermark."
)

EDIT_ANGLE_PROMPT = (
    "Create a new photograph of this exact same product from a 3/4 side angle viewpoint, "
    "same style and quality, professional e-commerce photo, no text, no watermark."
)

EDIT_DETAIL_PROMPT = (
    "Create a tight macro close-up showing material texture, surface finish and fine details. "
    "Shallow depth of field, professional product detail shot, no text, no watermark."
)

SCENE_SCALE_PROMPT = (
    "The product is placed on a wooden desk next to a smartphone and a standard coffee mug "
    "for size reference, natural daylight, clean e-commerce product photo, "
    "no text, no watermark"
)

TIKTOK_SLOT_GUIDE: list[dict] = [
    {"slot": 1, "title": "主图白底", "note": "搜索缩略图，禁止文字/水印"},
    {"slot": 2, "title": "使用场景 / 人手", "note": "lifestyle，提升转化"},
    {"slot": 3, "title": "尺寸对比", "note": "手机/杯子/手旁参照"},
    {"slot": 4, "title": "细节特写", "note": "材质纹理，可用 listing 第2张原图"},
    {"slot": 5, "title": "多角度", "note": "3/4 侧视"},
    {"slot": 6, "title": "开箱 flat lay", "note": "需包装图，Photoroom flatLay 偏服装"},
    {"slot": 7, "title": "卖点信息图", "note": "需 Canva 等加字，API 不支持"},
    {"slot": 8, "title": "变体色卡", "note": "各色 SKU 原图"},
    {"slot": 9, "title": "信任/评价", "note": "人工截图"},
]

RECIPE_CATALOG: dict[str, dict] = {
    "main_white": {
        "kind": "main",
        "label": "主图·标准白底",
        "params": {"padding": 0.10, "scaling": "fit"},
        "use_white_bg": True,
    },
    "main_tight": {
        "kind": "main",
        "label": "主图·紧凑白底",
        "params": {"padding": 0.06, "scaling": "fit"},
        "use_white_bg": True,
    },
    "main_shadow": {
        "kind": "main",
        "label": "主图·白底+软阴影",
        "params": {"padding": 0.10, "scaling": "fit", "shadow.mode": "ai.soft"},
        "use_white_bg": True,
    },
    "main_relight": {
        "kind": "main",
        "label": "主图·白底+补光",
        "params": {
            "padding": 0.10,
            "scaling": "fit",
            "lighting.mode": "ai.preserve-hue-and-saturation",
        },
        "use_white_bg": True,
    },
    "scene_living": {
        "kind": "scene",
        "label": "场景·客厅 lifestyle",
        "scene_key": "living",
        "params": {"padding": 0.08, "scaling": "fit"},
        "use_white_bg": False,
    },
    "scene_bedroom": {
        "kind": "scene",
        "label": "场景·卧室墙饰",
        "scene_key": "bedroom",
        "params": {"padding": 0.08, "scaling": "fit"},
        "use_white_bg": False,
    },
    "scene_minimal": {
        "kind": "scene",
        "label": "场景·极简家居",
        "scene_key": "minimal",
        "params": {"padding": 0.08, "scaling": "fit"},
        "use_white_bg": False,
    },
    "scene_bathroom": {
        "kind": "scene",
        "label": "场景·浴室/厨房",
        "scene_key": "bathroom",
        "params": {"padding": 0.08, "scaling": "fit"},
        "use_white_bg": False,
        "tiktok_slot": 2,
        "tiktok_hint": "槽位2·场景",
    },
    "prep_dewatermark": {
        "kind": "prep",
        "label": "预处理·去水印+白底",
        "params": {
            "textRemoval.mode": "ai.artificial",
            "padding": 0.10,
            "scaling": "fit",
        },
        "use_white_bg": True,
        "tiktok_slot": 1,
        "tiktok_hint": "槽位1·1688图",
    },
    "prep_upscale": {
        "kind": "prep",
        "label": "预处理·AI放大",
        "params": {"upscale.mode": "ai.fast"},
        "remove_background": False,
        "use_white_bg": False,
        "tiktok_slot": 0,
        "tiktok_hint": "预处理",
    },
    "main_upscale": {
        "kind": "main",
        "label": "主图·放大+白底",
        "params": {
            "upscale.mode": "ai.fast",
            "padding": 0.10,
            "scaling": "fit",
        },
        "use_white_bg": True,
        "tiktok_slot": 1,
        "tiktok_hint": "槽位1·高清主图",
    },
    "edit_staging": {
        "kind": "staging",
        "label": "探索·人手 lifestyle",
        "remove_background": False,
        "use_white_bg": False,
        "params": {"editWithAI.mode": "ai.auto", "editWithAI.prompt": EDIT_STAGING_PROMPT},
        "tiktok_slot": 2,
        "tiktok_hint": "槽位2·人手场景",
    },
    "scene_scale": {
        "kind": "scale",
        "label": "探索·尺寸对比场景",
        "params": {"padding": 0.08, "scaling": "fit"},
        "use_white_bg": False,
        "scene_prompt": SCENE_SCALE_PROMPT,
        "tiktok_slot": 3,
        "tiktok_hint": "槽位3·比大小",
    },
    "edit_angle": {
        "kind": "angle",
        "label": "探索·3/4 侧视角度",
        "remove_background": False,
        "use_white_bg": False,
        "params": {"editWithAI.mode": "ai.auto", "editWithAI.prompt": EDIT_ANGLE_PROMPT},
        "tiktok_slot": 5,
        "tiktok_hint": "槽位5·多角度",
    },
    "edit_detail": {
        "kind": "detail",
        "label": "探索·细节特写",
        "remove_background": False,
        "use_white_bg": False,
        "source_index": 1,
        "params": {"editWithAI.mode": "ai.auto", "editWithAI.prompt": EDIT_DETAIL_PROMPT},
        "tiktok_slot": 4,
        "tiktok_hint": "槽位4·细节",
    },
}

for _rid, _spec in RECIPE_CATALOG.items():
    if "tiktok_slot" not in _spec:
        if _spec["kind"] == "main":
            _spec.setdefault("tiktok_slot", 1)
            _spec.setdefault("tiktok_hint", "槽位1·主图")
        elif _spec["kind"] == "scene":
            _spec.setdefault("tiktok_slot", 2)
            _spec.setdefault("tiktok_hint", "槽位2·场景")

DEFAULT_EVAL_RECIPES = [
    "main_white",
    "main_shadow",
    "main_relight",
    "scene_living",
    "scene_bedroom",
    "scene_minimal",
]

DEFAULT_EXPLORE_RECIPES = [
    "main_white",
    "prep_dewatermark",
    "main_upscale",
    "edit_staging",
    "scene_scale",
    "edit_angle",
    "scene_living",
    "main_shadow",
]

DEFAULT_BASIC_RECIPES = ["main_white", "main_tight", "main_relight"]

SCENE_PROMPT_TEMPLATES: dict[str, str] = {
    "living": (
        "The product is displayed on a wall in a bright modern Southeast Asian apartment living room, "
        "natural window light, cozy sofa and indoor plants softly blurred in the background, "
        "professional TikTok Shop lifestyle product photo, photorealistic, no text, no watermark"
    ),
    "bedroom": (
        "The product is shown on a bedroom wall above a neatly made bed, warm soft morning light, "
        "calm neutral decor, shallow depth of field, e-commerce lifestyle photography, "
        "no text, no logo, no watermark"
    ),
    "minimal": (
        "The product is centered on a clean white wall in a minimalist Scandinavian-style room, "
        "soft diffused daylight, very subtle shadows, premium catalog photography, "
        "no text, no watermark"
    ),
    "bathroom": (
        "The product is applied on a tile wall in a clean modern bathroom or kitchen, "
        "bright even lighting, spa-like atmosphere, product clearly visible, "
        "professional listing photo, no text, no watermark"
    ),
}

# 沙盒/Basic 兼容的旧 preset（mode=basic 且未配 recipes 时）
VARIANT_PRESETS: list[dict] = [
    {"padding": 0.10, "scaling": "fit"},
    {"padding": 0.06, "scaling": "fit"},
    {"padding": 0.08, "scaling": "fit", "lighting.mode": "ai.auto"},
]


def _resolve_photoroom_api_key(cfg: dict) -> str:
    raw = (cfg.get("photoroom_api_key") or "").strip()
    if not raw:
        return ""
    sandbox = bool(cfg.get("sandbox"))
    if sandbox:
        if not raw.startswith("sandbox_"):
            return f"sandbox_{raw}"
        return raw
    if raw.startswith("sandbox_"):
        prod = (cfg.get("photoroom_api_key_production") or "").strip()
        if prod:
            return prod
        raise RuntimeError(
            "images.sandbox=false 但 photoroom_api_key 仍是沙盒 Key，"
            "请填写 photoroom_api_key_production 或关闭 sandbox"
        )
    return raw


def image_config() -> dict:
    ai = get("ai") or {}
    img = get("images") or {}
    provider = (img.get("provider") or "photoroom").strip().lower()
    mode = (img.get("mode") or "eval").strip().lower()
    base_cfg = {
        "provider": provider,
        "mode": mode,
        "sandbox": bool(img.get("sandbox")),
        "photoroom_api_key": (
            os.environ.get("PHOTOROOM_API_KEY")
            or img.get("photoroom_api_key")
            or ""
        ).strip(),
        "photoroom_api_key_production": (img.get("photoroom_api_key_production") or "").strip(),
        "output_size": img.get("output_size") or "1024x1024",
        "background_color": img.get("background_color") or "FFFFFF",
        "variants": int(img.get("variants_per_product") or 6),
        "eval_recipes": img.get("eval_recipes") or DEFAULT_EVAL_RECIPES,
        "explore_recipes": img.get("explore_recipes") or DEFAULT_EXPLORE_RECIPES,
        "ai_background_model": img.get("ai_background_model") or "background-studio-beta-2025-03-17",
        "request_delay_sec": float(
            img.get("request_delay_sec") or ai.get("request_delay_sec") or 0.5
        ),
        "export_format": img.get("export_format") or "jpeg",
    }
    base_cfg["photoroom_api_key_resolved"] = _resolve_photoroom_api_key(base_cfg)
    return base_cfg


def image_enabled() -> bool:
    cfg = image_config()
    if cfg["provider"] == "photoroom":
        return bool(cfg["photoroom_api_key_resolved"])
    return False


def extract_listing_image_urls(detail: dict) -> list[str]:
    """从 TikTok 商品详情提取 main_images 全部 URL。"""
    out: list[str] = []
    seen: set[str] = set()
    for img in detail.get("main_images") or []:
        url = ""
        for key in ("urls", "thumb_urls"):
            for u in img.get(key) or []:
                if u:
                    url = u
                    break
            if url:
                break
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _scene_prompt(scene_key: str, region: str | None, product_name: str | None) -> str:
    base = SCENE_PROMPT_TEMPLATES.get(scene_key) or SCENE_PROMPT_TEMPLATES["living"]
    reg = (region or "SEA").upper()
    name = (product_name or "home decor product").strip()[:120]
    return f"{base} Product context ({reg} market): {name}."


def _recipe_ids(cfg: dict, count: int | None) -> list[str]:
    mode = cfg.get("mode") or "eval"
    if mode == "basic":
        ids = list(DEFAULT_BASIC_RECIPES)
    else:
        raw = cfg.get("eval_recipes") or DEFAULT_EVAL_RECIPES
        ids = [r for r in raw if r in RECIPE_CATALOG]
        if not ids:
            ids = list(DEFAULT_EVAL_RECIPES)
    n = count if count is not None else cfg.get("variants") or len(ids)
    n = max(1, min(int(n), 12))
    return ids[:n]


def _explore_recipe_ids(cfg: dict, count: int | None) -> list[str]:
    raw = cfg.get("explore_recipes") or DEFAULT_EXPLORE_RECIPES
    ids = [r for r in raw if r in RECIPE_CATALOG]
    if not ids:
        ids = list(DEFAULT_EXPLORE_RECIPES)
    n = count if count is not None else len(ids)
    n = max(1, min(int(n), 16))
    return ids[:n]


def list_recipes() -> list[dict]:
    """供前端展示可选 recipe 及 TikTok 槽位建议。"""
    out: list[dict] = []
    for rid, spec in RECIPE_CATALOG.items():
        out.append(
            {
                "id": rid,
                "label": spec.get("label") or rid,
                "kind": spec.get("kind") or "main",
                "tiktok_slot": spec.get("tiktok_slot"),
                "tiktok_hint": spec.get("tiktok_hint"),
                "explore_default": rid in DEFAULT_EXPLORE_RECIPES,
            }
        )
    return out


def _pick_source_url(
    source_urls: list[str],
    spec: dict,
    recipe_index: int,
) -> str:
    kind = spec.get("kind") or "main"
    src_idx = spec.get("source_index")
    if src_idx is not None:
        return source_urls[min(int(src_idx), len(source_urls) - 1)]
    if kind in ("scene", "staging", "angle", "scale", "detail", "prep"):
        return source_urls[0]
    return source_urls[recipe_index % len(source_urls)]


def plan_recipe_jobs(
    source_urls: list[str],
    recipe_ids: list[str],
    region: str | None = None,
    product_name: str | None = None,
) -> list[dict]:
    if not source_urls:
        return []
    jobs: list[dict] = []
    cfg = image_config()
    recipe_index = 0
    for rid in recipe_ids:
        spec = RECIPE_CATALOG.get(rid)
        if not spec:
            continue
        src = _pick_source_url(source_urls, spec, recipe_index)
        recipe_index += 1
        params = dict(spec.get("params") or {})
        headers: dict[str, str] = {}
        if spec.get("scene_key"):
            sk = spec["scene_key"]
            params["background.prompt"] = _scene_prompt(sk, region, product_name)
            if cfg.get("ai_background_model"):
                headers["pr-ai-background-model-version"] = cfg["ai_background_model"]
        elif spec.get("scene_prompt"):
            params["background.prompt"] = spec["scene_prompt"]
            if cfg.get("ai_background_model"):
                headers["pr-ai-background-model-version"] = cfg["ai_background_model"]
        src_no = source_urls.index(src) + 1 if src in source_urls else 1
        label = spec.get("label") or rid
        if spec["kind"] == "main" and len(source_urls) > 1:
            label = f"{label} · 原图#{src_no}"
        elif spec.get("source_index") is not None and len(source_urls) > 1:
            label = f"{label} · 原图#{src_no}"
        jobs.append(
            {
                "recipe_id": rid,
                "kind": spec.get("kind") or "main",
                "label": label,
                "source_url": src,
                "params": params,
                "headers": headers,
                "remove_background": spec.get("remove_background", True),
                "use_white_bg": bool(spec.get("use_white_bg", True)),
                "tiktok_slot": spec.get("tiktok_slot"),
                "tiktok_hint": spec.get("tiktok_hint"),
            }
        )
    return jobs


def plan_custom_jobs(
    source_urls: list[str],
    *,
    region: str | None = None,
    product_name: str | None = None,
    main_recipe_ids: list[str] | None = None,
    custom_scenes: list[dict] | None = None,
    include_default_scenes: bool = False,
    explore_recipe_ids: list[str] | None = None,
) -> list[dict]:
    """组合主图 recipe + 探索 recipe + 用户自定义场景 prompt。"""
    jobs: list[dict] = []
    mains = main_recipe_ids
    if mains is None and not explore_recipe_ids:
        mains = ["main_white", "main_shadow"]
    if mains:
        jobs.extend(plan_recipe_jobs(source_urls, mains, region=region, product_name=product_name))
    if explore_recipe_ids:
        jobs.extend(
            plan_recipe_jobs(source_urls, explore_recipe_ids, region=region, product_name=product_name)
        )
    if include_default_scenes:
        cfg = image_config()
        scene_ids = [
            r for r in (cfg.get("eval_recipes") or DEFAULT_EVAL_RECIPES)
            if r in RECIPE_CATALOG and RECIPE_CATALOG[r].get("kind") == "scene"
        ]
        jobs.extend(plan_recipe_jobs(source_urls, scene_ids, region=region, product_name=product_name))
    cfg = image_config()
    src = source_urls[0] if source_urls else ""
    for i, scene in enumerate(custom_scenes or []):
        prompt = (scene.get("prompt") or "").strip()
        if not prompt or not src:
            continue
        label = (scene.get("label") or f"自定义场景 #{i + 1}").strip()
        headers: dict[str, str] = {}
        if cfg.get("ai_background_model"):
            headers["pr-ai-background-model-version"] = cfg["ai_background_model"]
        jobs.append(
            {
                "recipe_id": f"custom_{i + 1}",
                "kind": "scene",
                "label": label,
                "source_url": src,
                "params": {"padding": 0.08, "scaling": "fit", "background.prompt": prompt},
                "headers": headers,
                "remove_background": True,
                "use_white_bg": False,
                "tiktok_slot": 2,
                "tiktok_hint": "槽位2·自定义场景",
            }
        )
    return jobs


def _photoroom_edit(
    source_url: str,
    extra: dict | None = None,
    headers: dict | None = None,
    *,
    remove_background: bool = True,
    use_white_bg: bool = True,
) -> bytes:
    cfg = image_config()
    api_key = cfg["photoroom_api_key_resolved"]
    if not api_key:
        raise RuntimeError(
            "未配置 Photoroom API Key。请在 settings.json 的 images.photoroom_api_key 填写"
        )
    params: dict = {
        "imageUrl": source_url,
        "removeBackground": "true" if remove_background else "false",
        "outputSize": cfg["output_size"],
        "export.format": cfg["export_format"],
    }
    has_bg_prompt = bool((extra or {}).get("background.prompt"))
    has_edit_ai = bool((extra or {}).get("editWithAI.mode"))
    if use_white_bg and not has_bg_prompt and not has_edit_ai:
        params["background.color"] = cfg["background_color"]
    if extra:
        for k, v in extra.items():
            if v is None:
                continue
            params[k] = str(v).lower() if isinstance(v, bool) else str(v)

    qs = urllib.parse.urlencode(params)
    url = f"https://image-api.photoroom.com/v2/edit?{qs}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("x-api-key", api_key)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urlopen_retry(req, timeout=180, context=SSL_CTX) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Photoroom HTTP {e.code}: {err[:400]}") from e


def _legacy_plan_variant_jobs(source_urls: list[str], count: int) -> list[dict]:
    """legacy basic 模式。"""
    n = max(1, min(count, 9))
    jobs: list[dict] = []
    preset_idx = 0
    for i in range(n):
        src = source_urls[i % len(source_urls)]
        preset = VARIANT_PRESETS[preset_idx % len(VARIANT_PRESETS)]
        preset_idx += 1
        src_no = source_urls.index(src) + 1 if src in source_urls else 1
        label = f"原图#{src_no} · 标准白底"
        if preset.get("lighting.mode"):
            label = f"原图#{src_no} · 补光"
        elif preset.get("padding") == 0.06:
            label = f"原图#{src_no} · 紧凑"
        jobs.append(
            {
                "recipe_id": f"legacy_{i}",
                "kind": "main",
                "label": label,
                "source_url": src,
                "params": preset,
                "headers": {},
                "remove_background": True,
                "use_white_bg": True,
                "tiktok_slot": 1,
                "tiktok_hint": "槽位1·主图",
            }
        )
    return jobs


def _execute_jobs(
    jobs: list[dict],
    out_dir: Path,
) -> tuple[list[str], list[dict], list[str]]:
    cfg = image_config()
    ext = "jpg" if cfg["export_format"] in ("jpeg", "jpg") else cfg["export_format"]
    rel_paths: list[str] = []
    meta: list[dict] = []
    errors: list[str] = []

    for i, job in enumerate(jobs):
        fname = out_dir / f"{job['kind']}_{job['recipe_id']}.{ext}"
        try:
            img_bytes = _photoroom_edit(
                job["source_url"],
                job["params"],
                job.get("headers"),
                remove_background=job.get("remove_background", True),
                use_white_bg=job.get("use_white_bg", True),
            )
            fname.write_bytes(img_bytes)
            rel_paths.append(str(fname.relative_to(ROOT)))
            meta.append(
                {
                    "kind": job["kind"],
                    "recipe_id": job["recipe_id"],
                    "label": job["label"],
                    "source_url": job["source_url"],
                    "params": job.get("params"),
                    "path_index": len(rel_paths) - 1,
                    "tiktok_slot": job.get("tiktok_slot"),
                    "tiktok_hint": job.get("tiktok_hint"),
                }
            )
        except Exception as e:
            err = str(e)[:200]
            errors.append(f"{job['label']}: {err}")
            meta.append(
                {
                    "kind": job["kind"],
                    "recipe_id": job["recipe_id"],
                    "label": job["label"],
                    "source_url": job["source_url"],
                    "error": err,
                }
            )
        if i + 1 < len(jobs) and cfg["request_delay_sec"] > 0:
            time.sleep(cfg["request_delay_sec"])

    if not rel_paths:
        raise RuntimeError("; ".join(errors) or "全部候选生成失败")
    if errors:
        meta.append({"partial_errors": errors})
    return rel_paths, meta, errors


def generate_variants_from_listing(
    detail: dict,
    out_dir: Path,
    count: int | None = None,
    region: str | None = None,
    product_name: str | None = None,
    main_recipe_ids: list[str] | None = None,
    custom_scenes: list[dict] | None = None,
    include_default_scenes: bool = False,
    explore_recipe_ids: list[str] | None = None,
    use_eval_recipes: bool | None = None,
    use_explore_recipes: bool = False,
) -> tuple[list[str], list[str], list[dict]]:
    """
    基于 listing main_images 生成候选（主图 + 可选 AI 场景）。
    返回 (相对路径列表, 原图URL列表, 变体元数据列表)。
    """
    cfg = image_config()
    if cfg["provider"] != "photoroom":
        raise RuntimeError(f"暂不支持的 images.provider: {cfg['provider']}")

    source_urls = extract_listing_image_urls(detail)
    if not source_urls:
        raise RuntimeError("商品无 main_images，无法基于现有图片优化")

    if not product_name:
        product_name = detail.get("title") or ""

    out_dir.mkdir(parents=True, exist_ok=True)
    manual = (
        main_recipe_ids is not None
        or custom_scenes
        or include_default_scenes
        or explore_recipe_ids
        or use_eval_recipes is False
    )
    if manual:
        jobs = plan_custom_jobs(
            source_urls,
            region=region,
            product_name=product_name,
            main_recipe_ids=main_recipe_ids,
            custom_scenes=custom_scenes,
            include_default_scenes=include_default_scenes,
            explore_recipe_ids=explore_recipe_ids,
        )
    elif use_explore_recipes or (cfg.get("mode") or "eval") == "explore":
        recipe_ids = _explore_recipe_ids(cfg, count)
        jobs = plan_recipe_jobs(source_urls, recipe_ids, region=region, product_name=product_name)
    elif (cfg.get("mode") or "eval") == "eval":
        recipe_ids = _recipe_ids(cfg, count)
        jobs = plan_recipe_jobs(source_urls, recipe_ids, region=region, product_name=product_name)
    else:
        n = count if count is not None else cfg["variants"]
        jobs = _legacy_plan_variant_jobs(source_urls, n)

    if not jobs:
        raise RuntimeError("未配置任何生成任务（主图或场景）")

    rel_paths, meta, _errors = _execute_jobs(jobs, out_dir)
    return rel_paths, source_urls, meta


def output_dir_for(product_id: str, shop_cipher: str) -> Path:
    img_cfg = get("images") or {}
    base = Path(img_cfg.get("output_dir") or "exports/main_images")
    if not base.is_absolute():
        base = ROOT / base
    safe_cipher = shop_cipher[:12].replace("/", "_")
    return base / f"{product_id}_{safe_cipher}"


def format_variant_note(meta: list[dict]) -> str:
    return json.dumps(meta, ensure_ascii=False)
