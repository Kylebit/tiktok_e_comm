"""从 Ozon Seller API 拉取商品快照（与 ozon/webapp 相同接口）。"""

from __future__ import annotations

import json
import time
from typing import Callable

from modules.ozon.client import ozon_post
from modules.ozon.config import ozon_data_dir, ready

CHUNK_ATTRS = 100
CHUNK_INFO = 100
PAGE_LIMIT = 1000


def _progress(cb: Callable[[str], None] | None, msg: str) -> None:
    if cb:
        cb(msg)


def fetch_all_product_ids(
    on_progress: Callable[[str], None] | None = None,
    on_fraction: Callable[[float, str], None] | None = None,
) -> list[int]:
    all_ids: list[int] = []
    last_id = ""
    page = 0
    while True:
        page += 1
        msg = f"Ozon 商品列表 第 {page} 页…"
        _progress(on_progress, msg)
        if on_fraction:
            on_fraction(min(0.35, 0.1 + page * 0.08), msg)
        resp = ozon_post(
            "/v3/product/list",
            {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": PAGE_LIMIT},
        )
        result = resp.get("result") or {}
        items = result.get("items") or []
        for it in items:
            pid = it.get("product_id")
            if pid is not None:
                all_ids.append(int(pid))
        last_id = result.get("last_id") or ""
        if not last_id or not items:
            break
        time.sleep(0.3)
    return all_ids


def fetch_product_attributes(
    product_ids: list[int],
    on_progress: Callable[[str], None] | None = None,
    on_fraction: Callable[[float, str], None] | None = None,
) -> list[dict]:
    out: list[dict] = []
    total = len(product_ids)
    for i in range(0, total, CHUNK_ATTRS):
        chunk = [str(p) for p in product_ids[i : i + CHUNK_ATTRS]]
        done = min(i + CHUNK_ATTRS, total)
        msg = f"Ozon 商品详情 {done}/{total}…"
        _progress(on_progress, msg)
        if on_fraction and total:
            on_fraction(0.4 + 0.55 * (done / total), msg)
        resp = ozon_post(
            "/v4/product/info/attributes",
            {"filter": {"product_id": chunk, "visibility": "ALL"}, "limit": CHUNK_ATTRS},
        )
        out.extend(resp.get("result") or [])
        time.sleep(0.5)
    return out


def fetch_product_info(product_ids: list[int]) -> dict[str, dict]:
    """product_id → {offer_id, name, image, price}（补充 v3 info）。"""
    by_id: dict[str, dict] = {}
    for i in range(0, len(product_ids), CHUNK_INFO):
        chunk = product_ids[i : i + CHUNK_INFO]
        time.sleep(0.3)
        resp = ozon_post("/v3/product/info/list", {"product_id": chunk})
        for it in resp.get("items") or []:
            pid = str(it.get("id") or "")
            image = (it.get("images") or [""])[0] if it.get("images") else ""
            by_id[pid] = {
                "offer_id": it.get("offer_id") or "",
                "name": it.get("name") or "",
                "image": image,
                "price": it.get("price") or "",
            }
    return by_id


def _save_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _refresh_migrated_offers(data_dir, offer_ids: list[str]) -> None:
    path = data_dir / "migrated_offers.json"
    existing: set[str] = set()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = set(str(x) for x in raw)
        except json.JSONDecodeError:
            pass
    existing.update(x for x in offer_ids if x)
    _save_json(path, sorted(existing))


def sync_catalog(
    on_progress: Callable[[str], None] | None = None,
    on_fraction: Callable[[float, str], None] | None = None,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> dict:
    """拉全店商品 → 写入 all_products_attrs.json（与 ozon webapp 同格式）。"""
    if not ready():
        raise RuntimeError("Ozon 未就绪：需 data_dir + Client-Id/Api-Key")
    data_dir = ozon_data_dir()
    assert data_dir is not None

    attrs_path = data_dir / "all_products_attrs.json"
    from modules.catalog.sync_cache import ozon_snapshot_fresh

    if use_cache and not force_refresh and ozon_snapshot_fresh(attrs_path):
        raw = json.loads(attrs_path.read_text(encoding="utf-8"))
        offers = len(raw.get("result") or [])
        msg = f"Ozon：使用本地缓存（{offers} 个商品，未过期）"
        _progress(on_progress, msg)
        if on_fraction:
            on_fraction(1.0, msg)
        return {"products": offers, "offers": offers, "path": str(attrs_path), "cached": True}

    _progress(on_progress, "Ozon：拉取商品 ID…")
    if on_fraction:
        on_fraction(0.05, "Ozon：拉取商品 ID…")
    product_ids = fetch_all_product_ids(on_progress, on_fraction)
    if not product_ids:
        snapshot = {"result": [], "total": 0, "last_id": ""}
        _save_json(data_dir / "all_products_attrs.json", snapshot)
        return {"products": 0, "offers": 0}

    msg = f"Ozon：共 {len(product_ids)} 个，拉取属性…"
    _progress(on_progress, msg)
    if on_fraction:
        on_fraction(0.38, msg)
    attrs = fetch_product_attributes(product_ids, on_progress, on_fraction)

    info_by_id = fetch_product_info(product_ids)
    for it in attrs:
        pid = str(it.get("id") or "")
        extra = info_by_id.get(pid) or {}
        if extra.get("name") and not it.get("name"):
            it["name"] = extra["name"]
        if not it.get("primary_image") and extra.get("image"):
            it["primary_image"] = extra["image"]

    snapshot = {"result": attrs, "total": len(attrs), "last_id": ""}
    attrs_path = data_dir / "all_products_attrs.json"
    _save_json(attrs_path, snapshot)

    offer_ids = [str(it.get("offer_id") or "").strip() for it in attrs if it.get("offer_id")]
    _refresh_migrated_offers(data_dir, offer_ids)

    _progress(on_progress, f"Ozon 完成：{len(attrs)} 个商品")
    if on_fraction:
        on_fraction(1.0, f"Ozon 完成：{len(attrs)} 个商品")
    return {"products": len(product_ids), "offers": len(attrs), "path": str(attrs_path)}
