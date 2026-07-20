"""本地 Web 控制台：页面 + REST API。"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, wait
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from core.config import ROOT
from modules.products import costs as cost_mod

WEB_DIR = ROOT / "web"
DEFAULT_PORT = 8765
IMAGE_CACHE_DIR = ROOT / "data" / "web_image_cache"

_scan_lock = threading.Lock()
_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_push_lock = threading.Lock()
_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_promo_scan_lock = threading.Lock()
_promo_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_promo_push_lock = threading.Lock()
_promo_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_deact_scan_lock = threading.Lock()
_deact_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_deact_push_lock = threading.Lock()
_deact_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_mx_publish_lock = threading.Lock()
_mx_publish_job: dict = {
    "running": False,
    "message": "",
    "token": "",
    "match_key": "",
    "error": None,
    "result": None,
}

_uk_publish_lock = threading.Lock()
_uk_publish_job: dict = {
    "running": False,
    "message": "",
    "token": "",
    "match_key": "",
    "error": None,
    "result": None,
}

_analytics_sync_lock = threading.Lock()
_analytics_sync_job: dict = {
    "running": False,
    "message": "",
    "total": 0,
    "by_segment": {},
    "error": None,
}

_image_scan_lock = threading.Lock()
_image_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_catalog_sync_lock = threading.Lock()
_catalog_sync_job: dict = {
    "running": False,
    "message": "",
    "percent": 0,
    "phase": "",
    "mode": "fast",
    "result": None,
    "error": None,
}

_shopee_sync_lock = threading.Lock()
_shopee_sync_job: dict = {
    "running": False,
    "message": "",
    "mode": "",
    "region": "",
    "match_key": "",
    "match_keys": [],
    "result": None,
    "error": None,
}

_sourcing_build_lock = threading.Lock()
_sourcing_build_job: dict = {
    "running": False,
    "message": "",
    "offer_id": "",
    "error": None,
    "result": None,
}

_photoroom_showcase_lock = threading.Lock()
_photoroom_showcase_job: dict = {
    "running": False,
    "message": "",
    "offer_id": "",
    "error": None,
    "result": None,
}

_dewatermark_lock = threading.Lock()
_dewatermark_job: dict = {
    "running": False,
    "message": "",
    "offer_id": "",
    "error": None,
    "result": None,
}


def _run_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    mode: str = "velocity",
) -> None:
    global _scan_job
    try:
        from modules.products import titles as title_mod

        if mode == "analytics":
            _scan_job["message"] = "同步 Analytics，随后 AI 生成标题+详情..."
            n = title_mod.scan_analytics_high_interest(
                limit=limit,
                region=region,
                build_html=False,
                quiet=True,
            )
        else:
            _scan_job["message"] = "正在统计动销，随后 AI 生成标题..."
            n = title_mod.scan_low_velocity(
                days=days,
                max_units=max_units,
                limit=limit,
                region=region,
                build_html=False,
                quiet=True,
            )
        _scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _scan_job.update(running=False, message="", error=str(e))


def _start_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    mode: str = "velocity",
) -> tuple[bool, str]:
    with _scan_lock:
        if _scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(
        target=_run_scan,
        args=(days, max_units, limit, region, mode),
        daemon=True,
    )
    t.start()
    return True, "已开始扫描"


def _scan_status() -> dict:
    with _scan_lock:
        return dict(_scan_job)


def _run_push(items: list[dict]) -> None:
    global _push_job
    from modules.products import titles as title_mod

    try:
        edits = [{
            "product_id": it.get("product_id"),
            "shop_cipher": it.get("shop_cipher"),
            "new_title": it.get("new_title"),
            "new_description": it.get("new_description"),
        } for it in items]
        title_mod.save_edits(edits)
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _push_job["message"] = f"正在推送 0/{total}..."
        result = title_mod.push_approved(ids if ids else None)
        _push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _push_job.update(running=False, message="", error=str(e))


def _start_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可推送的条目"
    with _push_lock:
        if _push_job["running"]:
            return False, "已有推送任务在进行中，请稍候"
        _push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始推送"


def _push_status() -> dict:
    with _push_lock:
        return dict(_push_job)


def _run_promo_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    scope: str,
    mode: str = "velocity",
) -> None:
    global _promo_scan_job
    try:
        from modules.products import promotions as promo_mod

        if mode == "analytics":
            _promo_scan_job["message"] = "同步 Analytics A 类，生成促销建议..."
            n = promo_mod.scan_analytics_high_interest(
                limit=limit,
                region=region,
                scope=scope,
                quiet=True,
            )
        else:
            _promo_scan_job["message"] = "正在统计动销并拉取促销活动..."
            n = promo_mod.scan_low_velocity(
                days=days,
                max_units=max_units,
                limit=limit,
                region=region,
                scope=scope,
                quiet=True,
            )
        _promo_scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _promo_scan_job.update(running=False, message="", error=str(e))


def _start_promo_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    scope: str = "adjust",
    mode: str = "velocity",
) -> tuple[bool, str]:
    with _promo_scan_lock:
        if _promo_scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _promo_scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(
        target=_run_promo_scan,
        args=(days, max_units, limit, region, scope, mode),
        daemon=True,
    )
    t.start()
    return True, "已开始扫描"


def _promo_scan_status() -> dict:
    with _promo_scan_lock:
        return dict(_promo_scan_job)


def _run_promo_push(items: list[dict]) -> None:
    global _promo_push_job
    from modules.products import promotions as promo_mod

    try:
        edits = [{
            "product_id": it.get("product_id"),
            "shop_cipher": it.get("shop_cipher"),
            "new_discount": it.get("new_discount"),
            "flash_price": it.get("flash_price"),
            "promo_price": it.get("promo_price"),
            "action": it.get("action"),
        } for it in items]
        promo_mod.save_edits(edits)
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _promo_push_job["message"] = f"正在推送 0/{total}..."
        result = promo_mod.push_approved(ids if ids else None)
        _promo_push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _promo_push_job.update(running=False, message="", error=str(e))


def _start_promo_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可推送的条目"
    with _promo_push_lock:
        if _promo_push_job["running"]:
            return False, "已有推送任务在进行中，请稍候"
        _promo_push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_promo_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始推送"


def _promo_push_status() -> dict:
    with _promo_push_lock:
        return dict(_promo_push_job)


def _run_analytics_sync(region: str | None) -> None:
    global _analytics_sync_job
    try:
        from modules.products import analytics as analytics_mod

        _analytics_sync_job["message"] = "正在拉取各站 Analytics..."
        result = analytics_mod.sync_all(region=region, quiet=True)
        _analytics_sync_job.update(
            running=False,
            message=f"完成，共 {result['total']} 条",
            total=result["total"],
            by_segment=result.get("by_segment") or {},
            error=None,
        )
    except Exception as e:
        _analytics_sync_job.update(running=False, message="", error=str(e))


def _start_analytics_sync(region: str | None) -> tuple[bool, str]:
    with _analytics_sync_lock:
        if _analytics_sync_job["running"]:
            return False, "已有 Analytics 同步任务在进行中"
        _analytics_sync_job.update(
            running=True, message="启动中...", total=0, by_segment={}, error=None
        )
    t = threading.Thread(target=_run_analytics_sync, args=(region,), daemon=True)
    t.start()
    return True, "已开始同步"


def _analytics_sync_status() -> dict:
    with _analytics_sync_lock:
        return dict(_analytics_sync_job)


def _run_deact_scan(limit: int, region: str | None) -> None:
    global _deact_scan_job
    try:
        from modules.products import deactivate as deact_mod

        _deact_scan_job["message"] = "同步 Analytics 并筛选下架候选..."
        n = deact_mod.scan_candidates(region=region, limit=limit, quiet=True)
        _deact_scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _deact_scan_job.update(running=False, message="", error=str(e))


def _start_deact_scan(limit: int, region: str | None) -> tuple[bool, str]:
    with _deact_scan_lock:
        if _deact_scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _deact_scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(target=_run_deact_scan, args=(limit, region), daemon=True)
    t.start()
    return True, "已开始扫描"


def _deact_scan_status() -> dict:
    with _deact_scan_lock:
        return dict(_deact_scan_job)


def _run_deact_push(items: list[dict]) -> None:
    global _deact_push_job
    from modules.products import deactivate as deact_mod

    try:
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _deact_push_job["message"] = f"正在下架 0/{total}..."
        result = deact_mod.push_approved(ids if ids else None)
        _deact_push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _deact_push_job.update(running=False, message="", error=str(e))


def _start_deact_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可下架的条目"
    with _deact_push_lock:
        if _deact_push_job["running"]:
            return False, "已有下架任务在进行中，请稍候"
        _deact_push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_deact_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始下架"


def _deact_push_status() -> dict:
    with _deact_push_lock:
        return dict(_deact_push_job)


def _run_mx_publish(token: str) -> None:
    global _mx_publish_job
    from modules.miaoshou import mx_web_approval as mx_web

    try:
        _mx_publish_job["message"] = "正在 claim + publish…"
        result = mx_web.publish_token(token)
        _mx_publish_job.update(
            running=False,
            message=f"✅ {result['match_key']} 上架完成 · {result['list_price_ceil_mxn']} MXN",
            match_key=result.get("match_key") or "",
            result=result,
            error=None,
        )
    except Exception as e:
        _mx_publish_job.update(running=False, message="", error=str(e), result=None)


def _start_mx_publish(token: str) -> tuple[bool, str]:
    token = (token or "").strip()
    if not token:
        return False, "缺少 token"
    with _mx_publish_lock:
        if _mx_publish_job.get("running"):
            return False, "已有上架任务进行中"
        _mx_publish_job.update(
            running=True,
            message="排队中…",
            token=token,
            match_key="",
            error=None,
            result=None,
        )
    threading.Thread(target=_run_mx_publish, args=(token,), daemon=True).start()
    return True, "started"


def _mx_publish_status() -> dict:
    with _mx_publish_lock:
        return dict(_mx_publish_job)


def _run_uk_publish(token: str) -> None:
    global _uk_publish_job
    from modules.miaoshou import uk_web_approval as uk_web

    try:
        _uk_publish_job["message"] = "正在 claim + publish…"
        result = uk_web.publish_token(token)
        _uk_publish_job.update(
            running=False,
            message=f"✅ {result['match_key']} 上架完成 · £{result['list_price_ceil_gbp']}",
            match_key=result.get("match_key") or "",
            result=result,
            error=None,
        )
    except Exception as e:
        _uk_publish_job.update(running=False, message="", error=str(e), result=None)


def _start_uk_publish(token: str) -> tuple[bool, str]:
    token = (token or "").strip()
    if not token:
        return False, "缺少 token"
    with _uk_publish_lock:
        if _uk_publish_job.get("running"):
            return False, "已有上架任务进行中"
        _uk_publish_job.update(
            running=True,
            message="排队中…",
            token=token,
            match_key="",
            error=None,
            result=None,
        )
    threading.Thread(target=_run_uk_publish, args=(token,), daemon=True).start()
    return True, "started"


def _uk_publish_status() -> dict:
    with _uk_publish_lock:
        return dict(_uk_publish_job)


def _run_shopee_sync(mode: str, payload: dict) -> None:
    global _shopee_sync_job
    from modules.catalog import shopee_push as sp_push

    try:
        with _shopee_sync_lock:
            _shopee_sync_job["message"] = (
                "正在同步整组到 Shopee..."
                if mode == "group"
                else "正在同步 TikTok 到 Shopee..."
            )
        if mode == "group":
            result = sp_push.sync_tk_group_to_shopee(
                payload.get("match_keys") or [],
                region=str(payload.get("region") or "PH").upper(),
            )
        else:
            result = sp_push.sync_tk_to_shopee_global(
                str(payload.get("match_key") or "").strip(),
                region=str(payload.get("region") or "PH").upper(),
            )
        with _shopee_sync_lock:
            _shopee_sync_job.update(
                running=False,
                message=result.get("message") or "Shopee 同步完成",
                result=result,
                error=None,
            )
    except Exception as e:
        with _shopee_sync_lock:
            _shopee_sync_job.update(
                running=False,
                message="",
                result=None,
                error=str(e),
            )


def _start_shopee_sync(mode: str, payload: dict) -> tuple[bool, str]:
    region = str(payload.get("region") or "PH").upper()
    match_key = str(payload.get("match_key") or "").strip()
    raw_keys = payload.get("match_keys") or []
    if isinstance(raw_keys, str):
        match_keys = [x.strip() for x in raw_keys.replace(";", ",").split(",") if x.strip()]
    else:
        match_keys = [str(x).strip() for x in raw_keys if str(x).strip()]
    if mode == "group":
        if len(match_keys) < 2:
            return False, "整组同步至少需要 2 个对齐码"
    else:
        if not match_key:
            return False, "缺少 match_key"
    with _shopee_sync_lock:
        if _shopee_sync_job.get("running"):
            return False, "已有 Shopee 同步任务正在进行中"
        _shopee_sync_job.update(
            running=True,
            message="排队中...",
            mode=mode,
            region=region,
            match_key=match_key,
            match_keys=match_keys,
            result=None,
            error=None,
        )
    threading.Thread(
        target=_run_shopee_sync,
        args=(mode, {"region": region, "match_key": match_key, "match_keys": match_keys}),
        daemon=True,
    ).start()
    return True, "started"


def _shopee_sync_status() -> dict:
    with _shopee_sync_lock:
        return dict(_shopee_sync_job)


def _run_image_scan(
    limit: int,
    region: str | None,
    variants: int,
    mode: str = "b_class",
    product_items: list[dict] | None = None,
    main_recipe_ids: list[str] | None = None,
    custom_scenes: list[dict] | None = None,
    include_default_scenes: bool = False,
    explore_recipe_ids: list[str] | None = None,
) -> None:
    global _image_scan_job
    try:
        from modules.products import images as image_mod

        if mode in ("manual", "explore") and product_items:
            label = "探索方案" if mode == "explore" else "选定商品"
            _image_scan_job["message"] = f"为 {len(product_items)} 个{label}生成图片..."
            n = image_mod.generate_for_products(
                product_items,
                main_recipe_ids=[] if mode == "explore" else main_recipe_ids,
                custom_scenes=custom_scenes,
                include_default_scenes=include_default_scenes,
                explore_recipe_ids=explore_recipe_ids,
                use_explore_recipes=(mode == "explore" and not explore_recipe_ids),
                quiet=True,
            )
        else:
            _image_scan_job["message"] = "同步 Analytics B 类，随后生成主图+场景..."
            n = image_mod.scan_b_class(
                limit=limit, region=region, variants=variants, quiet=True
            )
        _image_scan_job.update(
            running=False,
            message=f"完成，共 {n} 个商品已生成候选",
            count=n,
            error=None,
        )
    except Exception as e:
        _image_scan_job.update(running=False, message="", error=str(e))


def _start_image_scan(
    limit: int,
    region: str | None,
    variants: int,
    mode: str = "b_class",
    product_items: list[dict] | None = None,
    main_recipe_ids: list[str] | None = None,
    custom_scenes: list[dict] | None = None,
    include_default_scenes: bool = False,
    explore_recipe_ids: list[str] | None = None,
) -> tuple[bool, str]:
    with _image_scan_lock:
        if _image_scan_job["running"]:
            return False, "已有主图生成任务在进行中"
        _image_scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(
        target=_run_image_scan,
        args=(
            limit, region, variants, mode, product_items,
            main_recipe_ids, custom_scenes, include_default_scenes, explore_recipe_ids,
        ),
        daemon=True,
    )
    t.start()
    return True, "已开始生成"


def _run_sourcing_build(
    offer_id: str,
    *,
    plan_version: str = "v2",
    skip_slots: bool = False,
    skip_images: bool = False,
) -> None:
    global _sourcing_build_job
    try:
        from modules.sourcing import pipeline as sourcing_mod

        def progress(msg: str) -> None:
            _sourcing_build_job["message"] = msg

        draft = sourcing_mod.build_draft(
            offer_id,
            progress=progress,
            plan_version=plan_version,
            skip_slots=skip_slots,
            skip_images=skip_images,
        )
        _sourcing_build_job.update(
            running=False,
            message="构建完成",
            offer_id=offer_id,
            error=None,
            result={
                "offer_id": offer_id,
                "plan_version": plan_version,
                "errors": draft.get("errors") or [],
            },
        )
    except Exception as e:
        _sourcing_build_job.update(
            running=False, message="", error=str(e), result=None
        )


def _start_sourcing_build(
    offer_id: str,
    *,
    plan_version: str = "v2",
    skip_slots: bool = False,
    skip_images: bool = False,
) -> tuple[bool, str]:
    oid = (offer_id or "").strip()
    if not oid:
        return False, "缺少 offer_id"
    with _sourcing_build_lock:
        if _sourcing_build_job["running"]:
            return False, "已有选品构建任务在进行中"
        _sourcing_build_job.update(
            running=True,
            message="启动中…",
            offer_id=oid,
            error=None,
            result=None,
        )
    t = threading.Thread(
        target=_run_sourcing_build,
        args=(oid,),
        kwargs={
            "plan_version": plan_version,
            "skip_slots": skip_slots,
            "skip_images": skip_images,
        },
        daemon=True,
    )
    t.start()
    return True, "已开始构建"


def _sourcing_build_status() -> dict:
    with _sourcing_build_lock:
        return dict(_sourcing_build_job)


def _run_photoroom_showcase(offer_id: str) -> None:
    global _photoroom_showcase_job
    try:
        from modules.sourcing import photoroom_showcase as showcase_mod

        def progress(msg: str) -> None:
            _photoroom_showcase_job["message"] = msg

        manifest = showcase_mod.build_showcase(offer_id, progress=progress)
        _photoroom_showcase_job.update(
            running=False,
            message="试跑完成",
            offer_id=offer_id,
            error=None,
            result=manifest.get("summary"),
        )
    except Exception as e:
        _photoroom_showcase_job.update(
            running=False, message="", error=str(e), result=None
        )


def _start_photoroom_showcase(offer_id: str) -> tuple[bool, str]:
    oid = (offer_id or "").strip()
    if not oid:
        return False, "缺少 offer_id"
    with _photoroom_showcase_lock:
        if _photoroom_showcase_job["running"]:
            return False, "Photoroom 试跑进行中，请稍候"
        _photoroom_showcase_job.update(
            running=True,
            message="准备中…",
            offer_id=oid,
            error=None,
            result=None,
        )
    t = threading.Thread(target=_run_photoroom_showcase, args=(oid,), daemon=True)
    t.start()
    return True, "已开始 Photoroom 全能力试跑"


def _photoroom_showcase_status() -> dict:
    with _photoroom_showcase_lock:
        return dict(_photoroom_showcase_job)


def _run_dewatermark_batch(offer_id: str) -> None:
    global _dewatermark_job
    try:
        from modules.sourcing import image_workbench as wb_mod

        def progress(msg: str) -> None:
            _dewatermark_job["message"] = msg

        wb = wb_mod.batch_dewatermark(offer_id, progress=progress, replace_final=True)
        _dewatermark_job.update(
            running=False,
            message="去水印完成",
            offer_id=offer_id,
            error=None,
            result={"main": len(wb.get("final", {}).get("tiktok_main") or [])},
        )
    except Exception as e:
        _dewatermark_job.update(running=False, message="", error=str(e), result=None)


def _start_dewatermark_batch(offer_id: str) -> tuple[bool, str]:
    oid = (offer_id or "").strip()
    if not oid:
        return False, "缺少 offer_id"
    with _dewatermark_lock:
        if _dewatermark_job["running"]:
            return False, "去水印任务进行中"
        _dewatermark_job.update(
            running=True, message="准备中…", offer_id=oid, error=None, result=None
        )
    t = threading.Thread(target=_run_dewatermark_batch, args=(oid,), daemon=True)
    t.start()
    return True, "已开始批量去水印"


def _dewatermark_status() -> dict:
    with _dewatermark_lock:
        return dict(_dewatermark_job)


def _run_catalog_sync() -> None:
    global _catalog_sync_job
    try:
        from modules.catalog.sync import run_catalog_sync
        from modules.catalog.sync_progress import CatalogSyncProgress

        def on_state(state: dict) -> None:
            with _catalog_sync_lock:
                _catalog_sync_job["message"] = state.get("message") or ""
                _catalog_sync_job["percent"] = int(state.get("percent") or 0)
                _catalog_sync_job["phase"] = state.get("phase") or ""

        def on_progress(msg: str) -> None:
            with _catalog_sync_lock:
                _catalog_sync_job["message"] = msg

        result = run_catalog_sync(
            on_progress=on_progress,
            on_state=on_state,
            mode=_catalog_sync_job.get("mode") or "fast",
        )
        errs = result.get("errors") or []
        msg_parts = []
        tk = result.get("tiktok") or {}
        if tk.get("skus"):
            msg_parts.append(f"TK {tk.get('skus')} SKU")
        sp = result.get("shopee") or {}
        if sp.get("skus"):
            msg_parts.append(f"Shopee {sp.get('skus')} SKU")
        elif sp.get("skipped"):
            msg_parts.append("Shopee 跳过")
        oz = result.get("ozon") or {}
        if oz.get("offers"):
            msg_parts.append(f"Ozon {oz.get('offers')} 商品")
        lw = result.get("logistics_weights") or {}
        if lw.get("skus"):
            msg_parts.append(f"重量 {lw.get('skus')}")
        summary = " · ".join(msg_parts) or "完成"
        if errs:
            summary += f"（部分失败: {'; '.join(errs)}）"
        _catalog_sync_job.update(
            running=False,
            message=summary,
            percent=100,
            phase="done",
            result=result,
            error="; ".join(errs) if errs and not msg_parts else None,
        )
    except Exception as e:
        _catalog_sync_job.update(
            running=False,
            message="",
            percent=0,
            phase="",
            result=None,
            error=str(e),
        )


def _start_catalog_sync(mode: str = "fast") -> tuple[bool, str]:
    with _catalog_sync_lock:
        if _catalog_sync_job["running"]:
            return False, "已有同步任务在进行中，请稍候"
        _catalog_sync_job.update(
            running=True,
            message="启动中…",
            percent=0,
            phase="tokens",
            mode="full" if mode == "full" else "fast",
            result=None,
            error=None,
        )
    t = threading.Thread(target=_run_catalog_sync, daemon=True)
    t.start()
    return True, "已开始同步"


def _catalog_sync_status() -> dict:
    with _catalog_sync_lock:
        return dict(_catalog_sync_job)


def _image_scan_status() -> dict:
    with _image_scan_lock:
        return dict(_image_scan_job)


def _api_status() -> dict:
    from core import auth
    from modules.products import titles as title_mod
    from modules.products import promotions as promo_mod
    from modules.products import deactivate as deact_mod
    from modules.products import images as image_mod

    def safe_count(label: str, fn) -> tuple[int, str | None]:
        try:
            return len(fn()), None
        except Exception as e:
            return 0, f"{label}: {e}"

    try:
        tok = auth.load_token()
        access_exp = auth.access_expires_at(tok)
        refresh_exp = auth.refresh_expires_at(tok)
        pending, w_titles = safe_count("titles", lambda: title_mod.load_queue("pending"))
        pending_promos, w_promos = safe_count("promotions", lambda: promo_mod.load_queue("pending"))
        pending_deact, w_deact = safe_count("deactivate", lambda: deact_mod.load_queue("pending"))
        pending_images, w_images = safe_count("images", image_mod.load_active_queue)
        from modules.miaoshou import mx_web_approval as mx_web
        from modules.miaoshou import uk_web_approval as uk_web

        pending_mx = len(mx_web.list_cards(status="pending"))
        pending_uk = len(uk_web.list_cards(status="pending"))
        warnings = [x for x in (w_titles, w_promos, w_deact, w_images) if x]
        return {
            "ok": True,
            "seller_name": tok.get("seller_name"),
            "access_expires": access_exp.isoformat() if access_exp else None,
            "refresh_expires": refresh_exp.isoformat() if refresh_exp else None,
            "pending_titles": pending,
            "pending_promos": pending_promos,
            "pending_deactivate": pending_deact,
            "pending_images": pending_images,
            "pending_mx": pending_mx,
            "pending_uk": pending_uk,
            "warnings": warnings,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _cache_ext(content_type: str, url_path: str) -> str:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    ext = mimetypes.guess_extension(ctype) or Path(url_path).suffix
    if not ext or len(ext) > 8:
        ext = ".jpg"
    return ext


def _image_cache_path(url: str, content_type: str = "") -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    ext = _cache_ext(content_type, urlparse(url).path)
    return IMAGE_CACHE_DIR / f"{digest}{ext}"


def _download_remote_image(url: str) -> tuple[Path, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http/https image URLs are supported")

    cached = _image_cache_path(url)
    for existing in IMAGE_CACHE_DIR.glob(cached.stem + ".*"):
        if existing.is_file() and existing.stat().st_size > 0:
            ctype = mimetypes.guess_type(str(existing))[0] or "image/jpeg"
            return existing, ctype

    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        },
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                content_type = resp.headers.get("Content-Type") or "image/jpeg"
                if not content_type.lower().startswith("image/"):
                    raise ValueError(f"remote URL is not an image: {content_type}")
                data = resp.read(12 * 1024 * 1024)
            break
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt:
                raise
            time.sleep(0.2)
    fp = _image_cache_path(url, content_type)
    fp.write_bytes(data)
    return fp, content_type


def _preview_image_urls(payload: dict, limit: int = 18) -> list[str]:
    urls: list[str] = []

    def add(value):
        if not value:
            return
        text = str(value).strip()
        if text and text not in urls:
            urls.append(text)

    review = payload.get("review") if isinstance(payload, dict) else {}
    for img in (review or {}).get("overseas_image_candidates") or []:
        if len(urls) >= limit:
            break
        if isinstance(img, dict):
            add(img.get("url"))
    for img in (review or {}).get("image_actions") or []:
        if len(urls) >= limit:
            break
        if isinstance(img, dict):
            add(img.get("url"))
    return urls[:limit]


def _warm_preview_image_cache(payload: dict) -> None:
    urls = _preview_image_urls(payload)
    if not urls:
        return
    pool = ThreadPoolExecutor(max_workers=min(8, len(urls)))
    try:
        futures = [pool.submit(_download_remote_image, url) for url in urls]
        wait(futures, timeout=18)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _placeholder_image_bytes(message: str = "image unavailable") -> bytes:
    safe = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="480" viewBox="0 0 480 480">'
        '<rect width="480" height="480" fill="#f1f5f9"/>'
        '<rect x="72" y="96" width="336" height="240" rx="12" fill="#e2e8f0"/>'
        '<circle cx="168" cy="176" r="36" fill="#cbd5e1"/>'
        '<path d="M112 304l84-84 62 62 44-44 66 66z" fill="#cbd5e1"/>'
        f'<text x="240" y="382" text-anchor="middle" font-family="Arial, sans-serif" '
        f'font-size="22" fill="#64748b">{safe}</text>'
        '</svg>'
    ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _module_moved(self, name: str, url: str):
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name} moved</title>
  <style>
    body {{ font-family: Arial, sans-serif; background:#f8fafc; color:#0f172a; margin:0; }}
    main {{ max-width: 760px; margin: 64px auto; background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:28px; }}
    h1 {{ margin:0 0 12px; font-size:28px; }}
    p {{ line-height:1.6; color:#475569; }}
    a {{ color:#2563eb; text-decoration:none; }}
    code {{ background:#f1f5f9; padding:2px 6px; border-radius:6px; }}
  </style>
</head>
<body>
  <main>
    <h1>{name} has moved</h1>
    <p>This module is no longer hosted inside <strong>Orbit OS</strong>.</p>
    <p>Please open it from its standalone service: <a href="{url}">{url}</a></p>
    <p>Old compatibility entry is now retired so the modules can run independently.</p>
  </main>
</body>
</html>""".encode("utf-8")
        return self._bytes(410, html, "text/html; charset=utf-8")

    def _bytes(
        self,
        code: int,
        data: bytes,
        content_type: str,
        *,
        filename: str | None = None,
    ):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{quote(filename)}"',
            )
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, *, cache_seconds: int | None = None):
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype.startswith("text/") and "charset=" not in ctype:
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cache_seconds is not None:
            self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
        self.end_headers()
        self.wfile.write(data)

    def _image_placeholder(self, message: str = "image unavailable"):
        data = _placeholder_image_bytes(message)
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if not length:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _handle_ozon_proxy(self, method: str) -> bool:
        path = urlparse(self.path).path
        if not path.startswith("/api/ozon/"):
            return False
        subpath = path[len("/api/ozon/") :].split("?")[0]
        query = urlparse(self.path).query
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

        # 商品目录驱动的待搬运 / 草稿（覆盖 ozon webapp 旧 tk_sku_map 逻辑）
        try:
            if method == "GET" and subpath == "unmigrated":
                from modules.ozon.catalog_source import list_unmigrated_from_catalog

                return self._json(200, list_unmigrated_from_catalog())
            if method == "GET" and subpath.startswith("draft/"):
                from urllib.parse import unquote

                from modules.ozon.catalog_draft import build_draft

                seller_sku = unquote(subpath[len("draft/") :])
                draft = build_draft(seller_sku)
                if draft.get("error") and not draft.get("draft_title"):
                    return self._json(404, draft)
                return self._json(200, draft)
            if method == "GET" and subpath == "category_options":
                from modules.ozon.category_match import load_category_options
                from modules.ozon.migrate_attrs import BUILTIN_TYPE_PROFILES
                from modules.ozon.tk_category_map import load_map

                tp = {str(k): v for k, v in BUILTIN_TYPE_PROFILES.items()}
                tp.update(load_map().get("type_profiles") or {})
                return self._json(200, {"options": load_category_options(), "type_profiles": tp})
            # 待审草稿队列：agent 生成好存这里，前端打开 /ozon 加载成待审卡片
            if method == "GET" and subpath == "pending_drafts":
                from modules.ozon.pending_drafts import list_pending

                return self._json(200, {"drafts": list_pending()})
            if method == "POST" and subpath == "pending_drafts":
                from modules.ozon.pending_drafts import save_pending

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                return self._json(200, {"saved": save_pending(payload)})
            if method == "POST" and subpath == "pending_drafts/delete":
                from modules.ozon.pending_drafts import delete_pending

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                ok = delete_pending(payload.get("seller_sku") or "")
                return self._json(200, {"deleted": ok})
            # 忽略某产品：记入已忽略并从待搬运列表永久排除
            if method == "POST" and subpath == "dismiss":
                from modules.ozon.pending_drafts import add_dismissed

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                rec = add_dismissed(
                    payload.get("seller_sku") or "",
                    payload.get("tk_id") or "",
                    payload.get("reason") or "",
                )
                return self._json(200, {"dismissed": rec})
            if method == "GET" and subpath == "dismissed":
                from modules.ozon.pending_drafts import list_dismissed

                return self._json(200, {"dismissed": list_dismissed()})
            # Ozon 真实结算汇总（佣金/物流费/广告费拆解），供定价参考
            if method == "GET" and subpath == "settlement_summary":
                from modules.ozon.settlement import build_settlement_summary

                q = parse_qs(query or "")
                months_back = int((q.get("months") or ["3"])[0])
                weeks = q.get("weeks") or q.get("weeks_back")
                weeks_back = int(weeks[0]) if weeks else None
                only_settled = (q.get("only_settled") or ["1"])[0] in ("1", "true", "True")
                force_fx = (q.get("refresh_fx") or ["0"])[0] in ("1", "true", "True")
                return self._json(
                    200,
                    build_settlement_summary(
                        months_back,
                        only_settled,
                        weeks_back=weeks_back,
                        force_fx_refresh=force_fx,
                    ),
                )
            # Ozon 利润分析：真实生效价(含弹性提升折扣) + 保最低利润率的min_price草稿
            if method == "GET" and subpath == "profit_table":
                from modules.ozon.profit_analysis import build_profit_table
                from modules.ozon.pending_drafts import dismissed_offer_ids

                q = parse_qs(query or "")
                target_margin = float((q.get("target_margin") or ["0.05"])[0])
                excluded = dismissed_offer_ids()
                return self._json(200, build_profit_table(target_margin, excluded_offer_ids=excluded))
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return True

        from modules.ozon.webapp_bridge import proxy_request

        try:
            status, data, ctype = proxy_request(method, subpath, query=query or None, body=body)
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/new-product", "/new-product.html"):
            return self._module_moved("Orbit Treasury", "http://127.0.0.1:8766/")
        if path in ("/ozon", "/ozon.html", "/rus", "/rus.html"):
            return self._module_moved("Orbit Rus", "http://127.0.0.1:8767/")
        if path.startswith("/api/new-product/"):
            return self._json(410, {"ok": False, "error": "Orbit Treasury moved to http://127.0.0.1:8766/"})
        if self._handle_ozon_proxy("GET"):
            return
        if path.startswith("/api/ozon/") or path.startswith("/api/rus/"):
            return self._json(410, {"ok": False, "error": "Orbit Rus moved to http://127.0.0.1:8767/"})

        if path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html")
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            return self._file(WEB_DIR / "static" / rel)
        if path == "/api/proxy-image":
            q = parse_qs(urlparse(self.path).query)
            url = unquote((q.get("url") or [""])[0]).strip()
            if not url:
                return self._json(400, {"ok": False, "error": "missing url"})
            try:
                fp, ctype = _download_remote_image(url)
                return self._file(fp, cache_seconds=86400)
            except (ValueError, urllib.error.URLError, TimeoutError, OSError) as e:
                return self._image_placeholder("image unavailable")
        if path in ("/costs", "/costs.html"):
            return self._file(WEB_DIR / "costs.html")
        if path in ("/titles", "/titles.html"):
            return self._file(WEB_DIR / "titles.html")
        if path in ("/promotions", "/promotions.html"):
            return self._file(WEB_DIR / "promotions.html")
        if path in ("/analytics", "/analytics.html"):
            return self._file(WEB_DIR / "analytics.html")
        if path in ("/deactivate", "/deactivate.html"):
            return self._file(WEB_DIR / "deactivate.html")
        if path in ("/images", "/images.html"):
            return self._file(WEB_DIR / "images.html")
        if path in ("/catalog", "/catalog.html"):
            return self._file(WEB_DIR / "catalog.html")
        if path in ("/settlement", "/settlement.html"):
            return self._file(WEB_DIR / "settlement.html")
        if path in ("/sourcing", "/sourcing.html"):
            return self._file(WEB_DIR / "sourcing.html")
        if path in ("/th-dim-fix", "/th-dim-fix.html"):
            return self._file(WEB_DIR / "th-dim-fix.html")
        if path in ("/sourcing/photoroom", "/sourcing/photoroom.html"):
            return self._file(WEB_DIR / "photoroom_showcase.html")
        if path in ("/ozon", "/ozon.html"):
            return self._file(WEB_DIR / "ozon.html")
        if path in ("/mx", "/mx.html"):
            return self._file(WEB_DIR / "mx.html")
        if path in ("/uk", "/uk.html"):
            return self._file(WEB_DIR / "uk.html")
        if path in ("/billing", "/billing.html"):
            return self._file(WEB_DIR / "billing.html")
        if path in ("/shopee-profit", "/shopee-profit.html"):
            return self._file(WEB_DIR / "shopee_profit.html")
        if path == "/billing/shopee_report":
            q = parse_qs(urlparse(self.path).query)
            name = (q.get("f") or [""])[0]
            # 仅允许周报快照文件，防目录穿越
            if not name or not name.startswith("weekly_shopee_profit_") or "/" in name or ".." in name:
                return self.send_error(404)
            fp = ROOT / "outputs" / name
            if not fp.is_file():
                return self.send_error(404)
            return self._file(fp)

        if path == "/api/billing/shopee_reports":
            out_dir = ROOT / "outputs"
            files = []
            if out_dir.is_dir():
                for p in sorted(out_dir.glob("weekly_shopee_profit_*.html"), reverse=True)[:12]:
                    stat = p.stat()
                    files.append({"name": p.name, "mtime": int(stat.st_mtime), "size": stat.st_size})
            return self._json(200, {"ok": True, "reports": files})

        if path == "/api/mx/approvals":
            from modules.miaoshou import mx_web_approval as mx_web

            q = parse_qs(urlparse(self.path).query)
            status = (q.get("status") or ["pending"])[0]
            items = mx_web.list_cards(status=status or None)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path.startswith("/api/mx/approvals/"):
            from modules.miaoshou import mx_web_approval as mx_web

            sub = path[len("/api/mx/approvals/") :].split("/")[0]
            if sub == "publish" or not sub:
                return self.send_error(404)
            detail = mx_web.get_card_detail(sub)
            if not detail:
                return self._json(404, {"ok": False, "error": "not found"})
            return self._json(200, {"ok": True, "card": detail})
        if path == "/api/mx/publish/status":
            return self._json(200, {"ok": True, **_mx_publish_status()})

        if path == "/api/uk/approvals":
            from modules.miaoshou import uk_web_approval as uk_web

            q = parse_qs(urlparse(self.path).query)
            status = (q.get("status") or ["pending"])[0]
            items = uk_web.list_cards(status=status or None)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path.startswith("/api/uk/approvals/"):
            from modules.miaoshou import uk_web_approval as uk_web

            sub = path[len("/api/uk/approvals/") :].split("/")[0]
            if sub == "publish" or not sub:
                return self.send_error(404)
            detail = uk_web.get_card_detail(sub)
            if not detail:
                return self._json(404, {"ok": False, "error": "not found"})
            return self._json(200, {"ok": True, "card": detail})
        if path == "/api/uk/publish/status":
            return self._json(200, {"ok": True, **_uk_publish_status()})

        if path == "/api/sourcing/list":
            from modules.sourcing import pipeline as sourcing_mod
            return self._json(200, {"ok": True, "items": sourcing_mod.list_offers()})
        if path == "/api/sourcing/item":
            from modules.sourcing import pipeline as sourcing_mod
            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or q.get("id") or [""])[0]
            draft = sourcing_mod.load_draft(offer_id)
            if draft:
                return self._json(200, {"ok": True, "draft": draft})
            try:
                scrape = sourcing_mod.load_scrape(offer_id)
            except FileNotFoundError as e:
                return self._json(404, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "scrape": scrape, "draft": None})
        if path == "/api/sourcing/build/status":
            return self._json(200, {"ok": True, **_sourcing_build_status()})
        if path == "/api/sourcing/photoroom-showcase":
            from modules.sourcing import photoroom_showcase as showcase_mod
            from modules.products import image_ai

            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or q.get("id") or [""])[0]
            manifest = showcase_mod.load_showcase(offer_id) if offer_id else None
            return self._json(
                200,
                {
                    "ok": True,
                    "offer_id": offer_id,
                    "manifest": manifest,
                    "recipes": image_ai.list_recipes(),
                    "enabled": image_ai.image_enabled(),
                },
            )
        if path == "/api/sourcing/photoroom-showcase/status":
            return self._json(200, {"ok": True, **_photoroom_showcase_status()})
        if path == "/api/sourcing/detail-text":
            from modules.sourcing import detail_text_cards as dtc_mod

            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or q.get("id") or [""])[0]
            manifest = dtc_mod.load_detail_text_cards(offer_id) if offer_id else None
            return self._json(200, {"ok": True, "offer_id": offer_id, "manifest": manifest})
        if path == "/api/sourcing/workbench":
            from modules.sourcing import image_workbench as wb_mod

            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or q.get("id") or [""])[0]
            if not offer_id:
                return self._json(400, {"ok": False, "error": "缺少 offer_id"})
            try:
                return self._json(200, {"ok": True, **wb_mod.get_workbench(offer_id)})
            except FileNotFoundError as e:
                return self._json(404, {"ok": False, "error": str(e)})
        if path == "/api/sourcing/workbench/shops":
            from modules.sourcing import tk_publish as tk_pub

            try:
                return self._json(200, {"ok": True, "shops": tk_pub.list_shop_options()})
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/sourcing/workbench/dewatermark/status":
            return self._json(200, {"ok": True, **_dewatermark_status()})
        if path == "/api/sourcing/asset":
            from modules.sourcing import pipeline as sourcing_mod
            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or [""])[0]
            file_path = (q.get("file") or [""])[0]
            fp = sourcing_mod.resolve_asset(offer_id, file_path)
            if not fp:
                return self.send_error(404)
            return self._file(fp)
        if path == "/api/new-product/preview":
            from modules.sourcing import new_product_workbench as np_mod
            q = parse_qs(urlparse(self.path).query)
            raw = (q.get("offer_id") or q.get("url") or [""])[0]
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id or url"})
            try:
                return self._json(200, np_mod.build_preview(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/status":
            return self._json(200, _api_status())
        if path == "/api/health":
            return self._json(
                200,
                {
                    "ok": True,
                    "service": "orbit-hive-local-console",
                    "root": str(ROOT),
                    "new_product": (WEB_DIR / "new_product.html").is_file(),
                    "catalog": (WEB_DIR / "catalog.html").is_file(),
                    "threaded": True,
                },
            )
        if path == "/api/digest/preview":
            from modules.hub import digest as digest_mod
            snap = digest_mod.collect_snapshot()
            return self._json(
                200,
                {"ok": True, "text": digest_mod.preview_text(), "snapshot": snap},
            )
        if path == "/api/titles":
            from modules.products import titles as title_mod
            items = title_mod.load_queue("pending")
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/titles/scan/status":
            return self._json(200, {"ok": True, **_scan_status()})
        if path == "/api/titles/push/status":
            return self._json(200, {"ok": True, **_push_status()})
        if path == "/api/analytics/summary":
            from modules.products import analytics as analytics_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            return self._json(200, {"ok": True, **analytics_mod.summary(region=region)})
        if path == "/api/analytics/products":
            from modules.products import analytics as analytics_mod
            q = parse_qs(urlparse(self.path).query)
            segment = (q.get("segment") or [None])[0]
            region = (q.get("region") or [None])[0]
            items = analytics_mod.load_analytics(segment=segment, region=region)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/analytics/sync/status":
            return self._json(200, {"ok": True, **_analytics_sync_status()})
        if path == "/api/deactivate":
            from modules.products import deactivate as deact_mod
            items = deact_mod.load_queue("pending")
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/deactivate/scan/status":
            return self._json(200, {"ok": True, **_deact_scan_status()})
        if path == "/api/deactivate/push/status":
            return self._json(200, {"ok": True, **_deact_push_status()})
        if path == "/api/images/products":
            from modules.products import images as image_mod
            q = parse_qs(urlparse(self.path).query)
            query = (q.get("q") or [None])[0]
            region = (q.get("region") or [None])[0]
            try:
                lim = int((q.get("limit") or ["40"])[0])
            except ValueError:
                lim = 40
            items = image_mod.search_products(query=query, region=region, limit=lim)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/images/recipes":
            from modules.products import image_ai
            return self._json(
                200,
                {
                    "ok": True,
                    "recipes": image_ai.list_recipes(),
                    "slots": image_ai.TIKTOK_SLOT_GUIDE,
                },
            )
        if path == "/api/images":
            from modules.products import images as image_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            items = image_mod.load_active_queue(region=region)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/images/scan/status":
            return self._json(200, {"ok": True, **_image_scan_status()})
        if path == "/api/images/download":
            from modules.products import images as image_mod
            q = parse_qs(urlparse(self.path).query)
            try:
                row_id = int((q.get("id") or ["0"])[0])
                index = int((q.get("index") or ["0"])[0])
            except ValueError:
                return self._json(400, {"ok": False, "error": "invalid id"})
            rows = image_mod.load_queue(status=None)
            row = next((r for r in rows if r["id"] == row_id), None)
            if not row:
                return self.send_error(404)
            paths = row.get("generated_paths") or []
            if index < 0 or index >= len(paths):
                return self.send_error(404)
            fp = image_mod.resolve_image_path(paths[index])
            if not fp:
                return self.send_error(404)
            return self._file(fp)
        if path == "/api/images/download-zip":
            from modules.products import images as image_mod
            q = parse_qs(urlparse(self.path).query)
            try:
                row_id = int((q.get("id") or ["0"])[0])
            except ValueError:
                return self._json(400, {"ok": False, "error": "invalid id"})
            zp = image_mod.export_slot_zip(row_id)
            if not zp or not zp.is_file():
                return self.send_error(404)
            return self._file(zp)
        if path == "/api/promotions":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            act_filter = (q.get("action") or [None])[0]
            region_filter = (q.get("region") or [None])[0]
            items = promo_mod.load_queue(
                "pending", action=act_filter, region=region_filter
            )
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/promotions/activities":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            region_filter = (q.get("region") or [None])[0]
            try:
                acts = promo_mod.list_ongoing_by_shop(region=region_filter)
                return self._json(200, {"ok": True, "activities": acts})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/promotions/scan/status":
            return self._json(200, {"ok": True, **_promo_scan_status()})
        if path == "/api/promotions/push/status":
            return self._json(200, {"ok": True, **_promo_push_status()})
        if path == "/api/promotions/coupons":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            try:
                coupons = promo_mod.list_coupons(region=region)
                return self._json(200, {"ok": True, "coupons": coupons})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/promotions/coupon-drafts":
            from modules.products import promotions as promo_mod
            drafts = promo_mod.load_coupon_drafts()
            return self._json(200, {"ok": True, "drafts": drafts})
        if path == "/api/catalog/stores":
            from modules.catalog import listings as cat_mod
            try:
                return self._json(200, {"ok": True, "stores": cat_mod.store_summary(), "summary": cat_mod.global_summary()})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/catalog/products":
            from modules.catalog import listings as cat_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            sku = (q.get("sku") or [None])[0]
            match_only = (q.get("match_only") or ["0"])[0] in ("1", "true", "yes")
            platform = (q.get("platform") or [None])[0]
            try:
                limit = min(int((q.get("limit") or ["300"])[0] or 300), 500)
                offset = int((q.get("offset") or ["0"])[0] or 0)
                data = cat_mod.list_products(
                    region, sku=sku, match_only=match_only, platform=platform,
                    limit=limit, offset=offset,
                )
                return self._json(200, {"ok": True, **data})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})

        if path == "/api/catalog/export-pdf":
            from modules.catalog import pdf_export as pdf_mod

            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            sku = (q.get("sku") or [None])[0]
            match_only = (q.get("match_only") or ["0"])[0] in ("1", "true", "yes")
            platform = (q.get("platform") or [None])[0]
            limit = min(int((q.get("limit") or ["300"])[0] or 300), 500)
            translate = (q.get("translate") or ["1"])[0] not in ("0", "false", "no")
            try:
                pdf_bytes, fname = pdf_mod.export_catalog_pdf(
                    region,
                    sku=sku,
                    match_only=match_only,
                    platform=platform,
                    limit=limit,
                    translate=translate,
                )
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
            return self._bytes(200, pdf_bytes, "application/pdf", filename=fname)

        if path == "/api/catalog/lookup":
            from modules.catalog import listings as cat_mod
            q = parse_qs(urlparse(self.path).query)
            sku = (q.get("sku") or q.get("q") or [""])[0]
            region = (q.get("region") or [None])[0]
            if not (sku or "").strip():
                return self._json(400, {"ok": False, "error": "请提供 sku 参数"})
            data = cat_mod.lookup_sku(sku, region)
            return self._json(200, data)

        if path == "/api/catalog/sku-edit":
            from modules.catalog import sku_edit as sku_edit_mod
            q = parse_qs(urlparse(self.path).query)
            mk = (q.get("match_key") or q.get("sku") or [""])[0]
            sku_id = (q.get("sku_id") or [""])[0]
            if not (mk or "").strip() and not (sku_id or "").strip():
                return self._json(400, {"ok": False, "error": "请提供 match_key 或 sku_id"})
            return self._json(200, sku_edit_mod.get_edit_rows(mk, sku_id=sku_id))

        if path == "/api/catalog/shopee-find":
            from modules.catalog import sku_edit as sku_edit_mod
            q = parse_qs(urlparse(self.path).query)
            query = (q.get("q") or q.get("sku") or [""])[0]
            if not (query or "").strip():
                return self._json(400, {"ok": False, "error": "请提供 q 参数"})
            live = (q.get("live") or ["1"])[0] not in ("0", "false", "no")
            return self._json(200, sku_edit_mod.find_shopee_rows(query, live=live))

        if path == "/api/catalog/sync/status":
            return self._json(200, {"ok": True, **_catalog_sync_status()})

        if path == "/api/catalog/shopee-sync/status":
            return self._json(200, {"ok": True, **_shopee_sync_status()})

        if path == "/api/catalog/shopee-sync-tk":
            return self._json(405, {"ok": False, "error": "use POST"})

        if path == "/api/catalog/shopee-sync-tk-group":
            return self._json(405, {"ok": False, "error": "use POST"})

        if path == "/api/catalog/shopee-sync-tk":
            match_key = str(data.get("match_key") or "").strip()
            region = str(data.get("region") or "PH").upper()
            if not match_key:
                return self._json(400, {"ok": False, "error": "需要 match_key"})
            ok, msg = _start_shopee_sync("single", {"match_key": match_key, "region": region})
            if not ok:
                return self._json(400, {"ok": False, "error": msg})
            return self._json(200, {"ok": True, "started": True, "message": msg})

        if path == "/api/catalog/shopee-sync-tk-group":
            raw_keys = data.get("match_keys") or data.get("keys") or ""
            region = str(data.get("region") or "PH").upper()
            if not raw_keys:
                return self._json(400, {"ok": False, "error": "需要 match_keys"})
            ok, msg = _start_shopee_sync("group", {"match_keys": raw_keys, "region": region})
            if not ok:
                return self._json(400, {"ok": False, "error": msg})
            return self._json(200, {"ok": True, "started": True, "message": msg})

        if path == "/api/settlement/config":
            from modules.finance import settlement_pull as spull
            from modules.finance.settlement_report import (
                data_range,
                default_ad_rates,
                default_rates,
                fee_column_defs,
            )

            ds, de = spull.default_period()
            return self._json(
                200,
                {
                    "ok": True,
                    "default_start": ds.isoformat(),
                    "default_end": de.isoformat(),
                    "rates": default_rates(),
                    "ad_rates": default_ad_rates(),
                    "fee_columns": fee_column_defs(),
                    "data_range": data_range(),
                },
            )

        if path == "/api/settlement/summary":
            from modules.finance.settlement_report import (
                default_ad_rates,
                default_rates,
                parse_iso_date,
                summarize_period,
            )

            q = parse_qs(urlparse(self.path).query)
            start_s = (q.get("start") or [""])[0]
            end_s = (q.get("end") or [""])[0]
            rates_raw = (q.get("rates") or [""])[0]
            ad_rates_raw = (q.get("ad_rates") or [""])[0]
            if not start_s or not end_s:
                return self._json(400, {"ok": False, "error": "需要 start 与 end"})
            try:
                rates = default_rates()
                ad_rates = default_ad_rates()
                if rates_raw:
                    rates.update(json.loads(rates_raw))
                if ad_rates_raw:
                    ad_rates.update(json.loads(ad_rates_raw))
                data = summarize_period(
                    parse_iso_date(start_s), parse_iso_date(end_s), rates, ad_rates
                )
                self._json(200, {"ok": True, **data})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/settlement/orders-list":
            from modules.finance.settlement_report import (
                default_ad_rates,
                default_rates,
                orders_for_period,
                parse_iso_date,
            )

            q = parse_qs(urlparse(self.path).query)
            start_s = (q.get("start") or [""])[0]
            end_s = (q.get("end") or [""])[0]
            rates_raw = (q.get("rates") or [""])[0]
            ad_rates_raw = (q.get("ad_rates") or [""])[0]
            if not start_s or not end_s:
                return self._json(400, {"ok": False, "error": "需要 start 与 end"})
            try:
                rates = default_rates()
                ad_rates = default_ad_rates()
                if rates_raw:
                    rates.update(json.loads(rates_raw))
                if ad_rates_raw:
                    ad_rates.update(json.loads(ad_rates_raw))
                data = orders_for_period(
                    parse_iso_date(start_s), parse_iso_date(end_s), rates, ad_rates
                )
                self._json(200, {"ok": True, **data})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/settlement/orders":
            from modules.finance.settlement_report import order_rows_for_file

            q = parse_qs(urlparse(self.path).query)
            fname = (q.get("file") or [""])[0]
            if not fname:
                return self._json(400, {"ok": False, "error": "需要 file 参数"})
            try:
                rate = float((q.get("rate") or ["0"])[0] or 0) or None
                ad_rate = (q.get("ad_rate") or [""])[0]
                ad_rate_pct = float(ad_rate) if ad_rate not in ("", None) else None
                statement_id = (q.get("statement_id") or [""])[0] or None
                order_id = (q.get("order_id") or [""])[0] or None
                data = order_rows_for_file(
                    fname,
                    rate=rate,
                    ad_rate_pct=ad_rate_pct,
                    statement_id=statement_id,
                    order_id=order_id,
                )
                self._json(200, {"ok": True, **data})
            except FileNotFoundError as e:
                self._json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/settlement/pull/status":
            from modules.finance import settlement_pull as spull

            return self._json(200, {"ok": True, **spull.pull_status()})

        if path == "/api/costs/export.csv":
            out = ROOT / "exports" / "sku_costs.csv"
            cost_mod.export_csv(out)
            return self._file(out)

        self.send_error(404)

    def _handle_feishu_event(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})
        from modules.hub import feishu_events as feishu_evt
        code, resp = feishu_evt.handle_http_body(body)
        self._json(code, resp)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/feishu/event":
            return self._handle_feishu_event()
        if path.startswith("/api/new-product/"):
            return self._json(410, {"ok": False, "error": "Orbit Treasury moved to http://127.0.0.1:8766/"})
        if self._handle_ozon_proxy("POST"):
            return
        if path.startswith("/api/ozon/") or path.startswith("/api/rus/"):
            return self._json(410, {"ok": False, "error": "Orbit Rus moved to http://127.0.0.1:8767/"})
        try:
            data = self._read_json()
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "error": "invalid json"})

        if path == "/api/mx/approvals/clear":
            from modules.miaoshou import mx_web_approval as mx_web

            result = mx_web.clear_pending_inbox(reason=str(data.get("reason") or "manual_clear"))
            return self._json(200, {"ok": True, **result})

        if path.startswith("/api/mx/approvals/"):
            from modules.miaoshou import mx_web_approval as mx_web

            parts = path[len("/api/mx/approvals/") :].strip("/").split("/")
            token = parts[0] if parts else ""
            action = parts[1] if len(parts) > 1 else ""
            if not token:
                return self._json(400, {"ok": False, "error": "missing token"})
            try:
                if action == "approve":
                    result = mx_web.approve_token(token)
                    return self._json(200, result)
                if action == "reject":
                    result = mx_web.reject_token(token)
                    return self._json(200, result)
                if action == "publish":
                    ok, msg = _start_mx_publish(token)
                    if not ok:
                        return self._json(409, {"ok": False, "error": msg})
                    return self._json(200, {"ok": True, "message": msg})
                if action == "override":
                    l = int(data.get("length_cm") or data.get("l") or 0)
                    w = int(data.get("width_cm") or data.get("w") or 0)
                    h = int(data.get("height_cm") or data.get("h") or 0)
                    if min(l, w, h) <= 0:
                        return self._json(400, {"ok": False, "error": "尺寸须为正整数 cm"})
                    result = mx_web.apply_override(
                        token, length_cm=l, width_cm=w, height_cm=h, note=str(data.get("note") or "")
                    )
                    card = mx_web.get_card_detail(token)
                    return self._json(200, {**result, "card": card})
            except KeyError as e:
                return self._json(404, {"ok": False, "error": str(e)})
            except RuntimeError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
            return self._json(404, {"ok": False, "error": "unknown action"})

        if path == "/api/uk/approvals/clear":
            from modules.miaoshou import uk_web_approval as uk_web

            result = uk_web.clear_pending_inbox(reason=str(data.get("reason") or "manual_clear"))
            return self._json(200, {"ok": True, **result})

        if path.startswith("/api/uk/approvals/"):
            from modules.miaoshou import uk_web_approval as uk_web

            parts = path[len("/api/uk/approvals/") :].strip("/").split("/")
            token = parts[0] if parts else ""
            action = parts[1] if len(parts) > 1 else ""
            if not token:
                return self._json(400, {"ok": False, "error": "missing token"})
            try:
                if action == "approve":
                    result = uk_web.approve_token(token)
                    return self._json(200, result)
                if action == "reject":
                    result = uk_web.reject_token(token)
                    return self._json(200, result)
                if action == "publish":
                    ok, msg = _start_uk_publish(token)
                    if not ok:
                        return self._json(409, {"ok": False, "error": msg})
                    return self._json(200, {"ok": True, "message": msg})
                if action == "override":
                    l = int(data.get("length_cm") or data.get("l") or 0)
                    w = int(data.get("width_cm") or data.get("w") or 0)
                    h = int(data.get("height_cm") or data.get("h") or 0)
                    if min(l, w, h) <= 0:
                        return self._json(400, {"ok": False, "error": "尺寸须为正整数 cm"})
                    result = uk_web.apply_override(
                        token, length_cm=l, width_cm=w, height_cm=h, note=str(data.get("note") or "")
                    )
                    card = uk_web.get_card_detail(token)
                    return self._json(200, {**result, "card": card})
            except KeyError as e:
                return self._json(404, {"ok": False, "error": str(e)})
            except RuntimeError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
            return self._json(404, {"ok": False, "error": "unknown action"})

        if path == "/api/catalog/cost":
            from modules.catalog import listings as cat_mod
            try:
                mk = str(data.get("match_key") or "").strip()
                cost = float(data.get("cost_cny", 0))
                if not mk or cost <= 0:
                    return self._json(400, {"ok": False, "error": "match_key 与 cost_cny 必填且 > 0"})
                saved = cat_mod.save_cost_by_match_key(mk, cost, data.get("note") or "")
                self._json(200, {"ok": True, "saved": saved, "match_key": mk, "cost_cny": cost})
            except (TypeError, ValueError) as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/catalog/seller-sku":
            from modules.catalog import sku_edit as sku_edit_mod
            try:
                plat = str(data.get("platform") or "").strip()
                sku = str(data.get("seller_sku") or "").strip()
                push = bool(data.get("push"))
                result = sku_edit_mod.save_seller_sku(
                    plat,
                    sku,
                    push=push,
                    sku_id=data.get("sku_id"),
                    shop_cipher=data.get("shop_cipher"),
                    global_product_id=data.get("global_product_id"),
                    global_sku_id=data.get("global_sku_id"),
                    model_id=data.get("model_id"),
                    shop_id=data.get("shop_id"),
                    product_id=data.get("product_id"),
                    item_id=data.get("item_id"),
                    match_key=data.get("match_key"),
                )
                self._json(200, {"ok": True, **result})
            except (TypeError, ValueError) as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/catalog/sync":
            mode = str(data.get("mode") or "fast").strip().lower()
            ok, msg = _start_catalog_sync(mode=mode)
            if not ok:
                return self._json(409, {"ok": False, "message": msg})
            return self._json(200, {"ok": True, "message": msg})

        if path == "/api/shopee/th_dim_fix/save":
            from modules.shopee.dim_fix import save_dimension

            try:
                result = save_dimension(
                    int(data["item_id"]),
                    float(data["length_cm"]),
                    float(data["width_cm"]),
                    float(data["height_cm"]),
                )
                return self._json(200, result)
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})

        if path == "/api/new-product/preview":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("url") or data.get("offer_id") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing url or offer_id"})
            try:
                if data.get("precollect"):
                    urls = data.get("overseas_urls") or []
                    if isinstance(urls, str):
                        urls = [x.strip() for x in urls.replace("\r", "\n").split("\n") if x.strip()]
                    result = np_mod.precollect_preview(
                        raw,
                        overseas_urls=list(urls),
                        source_code=str(data.get("source_code") or ""),
                        force=bool(data.get("force")),
                    )
                else:
                    result = np_mod.build_preview(raw, source_code=str(data.get("source_code") or ""))
                _warm_preview_image_cache(result)
                return self._json(200, result)
            except Exception as e:
                try:
                    fallback = np_mod.build_preview(raw, source_code=str(data.get("source_code") or ""))
                    fallback["precollect_error"] = str(e)
                    _warm_preview_image_cache(fallback)
                    return self._json(200, fallback)
                except Exception:
                    return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/review":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.save_review(raw, data.get("review") or {}))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/image-request":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            prompt = str(data.get("prompt") or "").strip()
            if not raw or not prompt:
                return self._json(400, {"ok": False, "error": "missing offer_id or prompt"})
            try:
                return self._json(200, np_mod.add_image_request(raw, prompt, kind=str(data.get("kind") or "supplement")))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/miaoshou-draft":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.prepare_miaoshou_draft(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/miaoshou-draft/commit":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.write_miaoshou_draft(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/miaoshou-second-review/continue":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.start_claim_miaoshou_to_tiktok(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/site-drafts/prepare":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.prepare_miaoshou_site_drafts(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/sku-numbering/fix":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.ensure_common_sequential_skus(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/overseas-source":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            overseas_url = str(data.get("overseas_url") or "").strip()
            if not raw or not overseas_url:
                return self._json(400, {"ok": False, "error": "missing offer_id or overseas_url"})
            try:
                return self._json(200, np_mod.add_overseas_source(raw, overseas_url, fetch=bool(data.get("fetch"))))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/overseas-sources":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            urls = data.get("overseas_urls") or []
            if isinstance(urls, str):
                urls = [x.strip() for x in urls.replace("\r", "\n").split("\n") if x.strip()]
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.save_overseas_sources(raw, list(urls), fetch=bool(data.get("fetch"))))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/catalog/shopee-sync-tk":
            match_key = str(data.get("match_key") or "").strip()
            region = str(data.get("region") or "PH").upper()
            if not match_key:
                return self._json(400, {"ok": False, "error": "需要 match_key"})
            ok, msg = _start_shopee_sync("single", {"match_key": match_key, "region": region})
            if not ok:
                return self._json(400, {"ok": False, "error": msg})
            return self._json(200, {"ok": True, "started": True, "message": msg})

        if path == "/api/catalog/shopee-sync-tk-group":
            raw_keys = data.get("match_keys") or data.get("keys") or ""
            region = str(data.get("region") or "PH").upper()
            if not raw_keys:
                return self._json(400, {"ok": False, "error": "需要 match_keys"})
            ok, msg = _start_shopee_sync("group", {"match_keys": raw_keys, "region": region})
            if not ok:
                return self._json(400, {"ok": False, "error": msg})
            return self._json(200, {"ok": True, "started": True, "message": msg})

        if path == "/api/catalog/shopee-sync-tk":
            match_key = str(data.get("match_key") or "").strip()
            region = str(data.get("region") or "PH").upper()
            if not match_key:
                return self._json(400, {"ok": False, "error": "需要 match_key"})
            try:
                from modules.catalog import shopee_push as sp_push

                result = sp_push.sync_tk_to_shopee_global(match_key, region=region)
                self._json(200, {"ok": True, **result})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/catalog/shopee-sync-tk-group":
            raw_keys = data.get("match_keys") or data.get("keys") or ""
            region = str(data.get("region") or "PH").upper()
            if not raw_keys:
                return self._json(400, {"ok": False, "error": "需要 match_keys"})
            try:
                from modules.catalog import shopee_push as sp_push

                result = sp_push.sync_tk_group_to_shopee(raw_keys, region=region)
                self._json(200, {"ok": True, **result})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/settlement/pull":
            from modules.finance import settlement_pull as spull
            from modules.finance.settlement_report import parse_iso_date

            start_s = str(data.get("start") or "").strip()
            end_s = str(data.get("end") or "").strip()
            if not start_s or not end_s:
                return self._json(400, {"ok": False, "error": "需要 start 与 end (YYYY-MM-DD)"})
            try:
                ok, msg = spull.start_pull(parse_iso_date(start_s), parse_iso_date(end_s))
                code = 200 if ok else 409
                self._json(code, {"ok": ok, "message": msg})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/costs":
            try:
                saved = cost_mod.save_costs_bulk(data.get("costs") or [])
                self._json(200, {"ok": True, "saved": saved})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/digest/send":
            from modules.hub.service import send_digest
            try:
                send_digest(dry_run=bool(data.get("dry_run")))
                self._json(200, {"ok": True, "message": "已发送飞书日报"})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/titles/scan":
            ok, msg = _start_scan(
                days=int(data.get("days") or 30),
                max_units=int(data.get("max_units", 1)),
                limit=int(data.get("limit") or 30),
                region=data.get("region") or None,
                mode=data.get("mode") or "velocity",
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/analytics/sync":
            ok, msg = _start_analytics_sync(data.get("region") or None)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/deactivate/scan":
            ok, msg = _start_deact_scan(
                limit=int(data.get("limit") or 50),
                region=data.get("region") or None,
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/deactivate/push":
            items = data.get("items") or []
            ok, msg = _start_deact_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/images/scan":
            mode = data.get("mode") or "b_class"
            products = data.get("products") or []
            main_recipes = data.get("main_recipes") if "main_recipes" in data else None
            explore_recipes = data.get("explore_recipes") or None
            custom_scenes = data.get("custom_scenes") or None
            include_default = bool(data.get("include_default_scenes"))
            ok, msg = _start_image_scan(
                limit=int(data.get("limit") or 10),
                region=data.get("region") or None,
                variants=int(data.get("variants") or 3),
                mode=mode,
                product_items=products if mode in ("manual", "explore") else None,
                main_recipe_ids=main_recipes,
                custom_scenes=custom_scenes,
                include_default_scenes=include_default,
                explore_recipe_ids=explore_recipes,
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/images/generate":
            from modules.products import images as image_mod
            pid = data.get("product_id") or ""
            cipher = data.get("shop_cipher") or ""
            if not pid or not cipher:
                return self._json(400, {"ok": False, "error": "missing product_id or shop_cipher"})
            try:
                ok = image_mod.generate_for_product(
                    pid,
                    cipher,
                    main_recipe_ids=data.get("main_recipes"),
                    custom_scenes=data.get("custom_scenes"),
                    include_default_scenes=bool(data.get("include_default_scenes")),
                    scan_source="manual",
                )
                self._json(200, {"ok": ok, "message": "已生成" if ok else "生成失败，见队列"})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/images/mark":
            from modules.products import images as image_mod
            row_id = int(data.get("id") or 0)
            action = data.get("action") or "done"
            if not row_id:
                return self._json(400, {"ok": False, "error": "missing id"})
            if action == "skip":
                ok = image_mod.mark_skipped(row_id)
            else:
                ok = image_mod.mark_done(row_id, data.get("selected_path"))
            self._json(200, {"ok": ok})
            return

        if path == "/api/titles/push":
            items = data.get("items") or []
            ok, msg = _start_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/promotions/scan":
            ok, msg = _start_promo_scan(
                days=int(data.get("days") or 30),
                max_units=int(data.get("max_units", 1)),
                limit=int(data.get("limit") or 30),
                region=data.get("region") or None,
                scope=data.get("scope") or "adjust",
                mode=data.get("mode") or "velocity",
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/promotions/coupons/scan":
            from modules.products import promotions as promo_mod
            try:
                n = promo_mod.scan_coupon_suggestions(
                    region=data.get("region") or None,
                    limit=int(data.get("limit") or 4),
                )
                self._json(200, {"ok": True, "count": n})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/promotions/coupon-drafts/mark":
            from modules.products import promotions as promo_mod
            draft_id = int(data.get("id") or 0)
            if draft_id:
                promo_mod.mark_coupon_draft_used(draft_id)
            self._json(200, {"ok": True})
            return

        if path == "/api/promotions/push":
            items = data.get("items") or []
            ok, msg = _start_promo_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/sourcing/build":
            offer_id = data.get("offer_id") or data.get("id") or ""
            plan_version = (data.get("plan_version") or data.get("plan") or "v2").strip()
            skip_slots = bool(data.get("skip_slots"))
            skip_images = bool(data.get("skip_images"))
            ok, msg = _start_sourcing_build(
                str(offer_id),
                plan_version=plan_version,
                skip_slots=skip_slots,
                skip_images=skip_images,
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/sourcing/selection":
            from modules.sourcing import pipeline as sourcing_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                draft = sourcing_mod.save_selections(offer_id, data.get("selections") or data)
                self._json(200, {"ok": True, "draft": draft})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/photoroom-showcase/build":
            offer_id = data.get("offer_id") or data.get("id") or ""
            ok, msg = _start_photoroom_showcase(str(offer_id))
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/sourcing/detail-text/build":
            from modules.sourcing import detail_text_cards as dtc_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                manifest = dtc_mod.build_detail_text_cards(offer_id)
                self._json(200, {"ok": True, "manifest": manifest})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/download":
            from modules.sourcing import image_workbench as wb_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                raw = wb_mod.ensure_downloaded(offer_id)
                wb = wb_mod.get_workbench(offer_id)
                self._json(200, {"ok": True, "raw": raw, "workbench": wb})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/generate":
            from modules.sourcing import image_workbench as wb_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            source_file = str(data.get("source_file") or data.get("file") or "").strip()
            recipe_id = str(data.get("recipe_id") or data.get("recipe") or "").strip()
            if not offer_id or not source_file or not recipe_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id / source_file / recipe_id"})
                return
            try:
                entry = wb_mod.generate_image(offer_id, source_file=source_file, recipe_id=recipe_id)
                wb = wb_mod.get_workbench(offer_id)
                self._json(200, {"ok": True, "generated": entry, "workbench": wb})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/final":
            from modules.sourcing import image_workbench as wb_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            action = str(data.get("action") or "save").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                if action == "add":
                    wb = wb_mod.add_to_final(
                        offer_id,
                        str(data.get("path") or ""),
                        str(data.get("target") or "tiktok_main"),
                    )
                elif action == "remove":
                    wb = wb_mod.remove_from_final(
                        offer_id,
                        str(data.get("path") or ""),
                        str(data.get("target") or "tiktok_main"),
                    )
                elif action == "reorder":
                    wb = wb_mod.reorder_final(
                        offer_id,
                        str(data.get("target") or "tiktok_main"),
                        list(data.get("paths") or []),
                    )
                elif action == "reset_defaults":
                    wb_mod.apply_raw_defaults(offer_id)
                    wb = wb_mod.get_workbench(offer_id)
                else:
                    wb = wb_mod.save_final(
                        offer_id,
                        tiktok_main=data.get("tiktok_main"),
                        tiktok_description=data.get("tiktok_description"),
                    )
                self._json(200, {"ok": True, "workbench": wb})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/dewatermark":
            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            ok, msg = _start_dewatermark_batch(offer_id)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/sourcing/workbench/publish-tk":
            from modules.sourcing import tk_publish as tk_pub

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            product_id = str(data.get("product_id") or "").strip()
            shop_cipher = str(data.get("shop_cipher") or "").strip()
            region = str(data.get("region") or "MY").strip()
            if not offer_id or not product_id or not shop_cipher:
                self._json(400, {"ok": False, "error": "缺少 offer_id / product_id / shop_cipher"})
                return
            try:
                result = tk_pub.publish_to_product(
                    offer_id,
                    product_id=product_id,
                    shop_cipher=shop_cipher,
                    region=region,
                )
                self._json(200, {"ok": True, "result": result})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/export-tk":
            from modules.sourcing import tk_publish as tk_pub

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            region = str(data.get("region") or "MY").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                zp = tk_pub.export_publish_bundle(offer_id, region=region)
                rel = str(zp.relative_to(ROOT))
                parts = rel.replace("\\", "/").split("/")
                sub = "/".join(parts[3:]) if len(parts) > 3 else zp.name
                url = f"/api/sourcing/asset?offer_id={offer_id}&file={quote(sub)}"
                self._json(200, {"ok": True, "path": rel, "url": url})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        self.send_error(404)


def serve(
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    page: str = "index",
    startup_refresh: bool | None = None,
):
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_DIR / "static").mkdir(parents=True, exist_ok=True)

    def _startup_refresh_tokens() -> None:
        try:
            from modules.hub.tokens import refresh_all

            r = refresh_all()
            if r.get("errors"):
                print("  [WARN] Token 刷新:", "; ".join(r["errors"][:2]))
            else:
                print("  [OK] Token 已自动刷新（TikTok + Shopee）")
        except Exception as e:
            print(f"  [WARN] Token 刷新跳过: {e}")

    if not (WEB_DIR / "costs.html").is_file():
        from modules.products.build_page import build_html
        build_html()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        server.daemon_threads = True
    except OSError as e:
        if getattr(e, "errno", None) == 48:
            print(f"  [WARN] 端口 {port} 已被占用。请先停止旧进程（旧版可能没有 /images 路由会 404）：")
            print(f"     lsof -i :{port}   # 查看 PID 后 kill <PID>")
            print(f"     然后重新运行: python3 main.py serve --page images")
        raise
    routes = {
        "index": "/",
        "catalog": "/catalog",
        "settlement": "/settlement",
        "costs": "/costs",
        "titles": "/titles",
        "promotions": "/promotions",
        "analytics": "/analytics",
        "deactivate": "/deactivate",
        "images": "/images",
        "sourcing": "/sourcing",
        "ozon": "/ozon",
        "mx": "/mx",
        "uk": "/uk",
    }
    url = f"http://127.0.0.1:{port}{routes.get(page, '/')}"
    print(f"  [OK] 控制台: http://127.0.0.1:{port}/")
    print(f"  商品目录: http://127.0.0.1:{port}/catalog")
    print(f"  结算利润: http://127.0.0.1:{port}/settlement")
    print(f"  Ozon 运营: http://127.0.0.1:{port}/ozon")
    print(f"  MX 上架审批: http://127.0.0.1:{port}/mx")
    print(f"  UK 上架审批: http://127.0.0.1:{port}/uk")
    print("  Orbit Rus: http://127.0.0.1:8767/")
    print(f"  1688 选品: http://127.0.0.1:{port}/sourcing")
    print("  Orbit Treasury: http://127.0.0.1:8766/")
    print(f"  Listing 优化: http://127.0.0.1:{port}/titles")
    print(f"  主图优化: http://127.0.0.1:{port}/images")
    print(f"  Analytics: http://127.0.0.1:{port}/analytics")
    print(f"  零销下架: http://127.0.0.1:{port}/deactivate")
    print(f"  促销调价: http://127.0.0.1:{port}/promotions")
    print(f"  成本维护: http://127.0.0.1:{port}/costs")
    print("  Ctrl+C 停止")
    if startup_refresh is None:
        startup_refresh = os.environ.get("ORBIT_STARTUP_REFRESH", "").lower() in ("1", "true", "yes")
    if startup_refresh:
        threading.Timer(0.5, _startup_refresh_tokens).start()
    else:
        print("  [OK] Startup token refresh skipped; run `python main.py tokens refresh` when needed.")

    if open_browser:
        import webbrowser
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
        server.server_close()
