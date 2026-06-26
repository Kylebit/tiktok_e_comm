"""Ozon 改价预警：扫描 RED 价格指数、缓存、改价与抑制提醒。"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from modules.ozon.client import ozon_post
from modules.ozon.config import ozon_data_dir, ready
from modules.ozon.sync import fetch_product_info

PRICES_PATH = "/v5/product/info/prices"
IMPORT_PRICES_PATH = "/v1/product/import/prices"
PAGE_LIMIT = 1000
SUPPRESS_DAYS = 14
RED_FLAGS = frozenset({"RED", "COLOR_INDEX_RED"})


def _require_ready() -> Path:
    if not ready():
        raise RuntimeError(
            "Ozon 未就绪：请在 config/settings.json 填写 ozon.client_id / ozon.api_key，"
            "或创建 config/ozon.local.json"
        )
    base = ozon_data_dir()
    if not base:
        raise RuntimeError("Ozon data_dir 未配置或目录不存在")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_red(item: dict) -> bool:
    idx = item.get("price_indexes") or {}
    return str(idx.get("color_index") or "") in RED_FLAGS


def _price_index_value(item: dict) -> float:
    idx = item.get("price_indexes") or {}
    best = 0.0
    for key in ("ozon_index_data", "external_index_data", "self_marketplaces_index_data"):
        data = idx.get(key) or {}
        try:
            v = float(data.get("price_index_value") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > best:
            best = v
    return best


def _min_competitor_price(item: dict) -> float | None:
    idx = item.get("price_indexes") or {}
    best: float | None = None
    for key in ("ozon_index_data", "external_index_data", "self_marketplaces_index_data"):
        data = idx.get(key) or {}
        try:
            v = float(data.get("min_price") or 0)
        except (TypeError, ValueError):
            continue
        if v > 0 and (best is None or v < best):
            best = v
    return best


def _suggested_prices(item: dict) -> tuple[int, int]:
    cur = item.get("price") or {}
    try:
        cur_price = float(cur.get("price") or 0)
    except (TypeError, ValueError):
        cur_price = 0.0
    min_comp = _min_competitor_price(item)
    if min_comp and min_comp > 0:
        suggested = max(1, int(min_comp) - 1)
    elif cur_price > 0:
        suggested = max(1, int(cur_price * 0.92))
    else:
        suggested = 0
    suggested_old = max(suggested + 1, int(suggested * 1.25)) if suggested else 0
    return suggested, suggested_old


def _fetch_all_prices() -> list[dict]:
    items: list[dict] = []
    cursor = ""
    while True:
        body = {
            "cursor": cursor,
            "filter": {"visibility": "ALL"},
            "limit": PAGE_LIMIT,
        }
        resp = ozon_post(PRICES_PATH, body)
        batch = resp.get("items") or []
        items.extend(batch)
        cursor = str(resp.get("cursor") or "")
        if not cursor or not batch:
            break
        time.sleep(0.3)
    return items


def _load_suppressed(base: Path) -> dict[str, str]:
    raw = _load_json(base / "price_suppress.json", {})
    if isinstance(raw, dict) and isinstance(raw.get("suppressed"), dict):
        return {str(k): str(v) for k, v in raw["suppressed"].items()}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if k not in ("suppressed_until",)}
    return {}


def _active_suppressed(suppressed: dict[str, str]) -> set[str]:
    today = date.today()
    out: set[str] = set()
    for offer_id, until in suppressed.items():
        try:
            if date.fromisoformat(until) >= today:
                out.add(offer_id)
        except ValueError:
            continue
    return out


def _previous_offer_ids(base: Path) -> set[str]:
    cached = _load_json(base / "pending_price_review.json", {})
    rows = cached.get("rows") if isinstance(cached, dict) else []
    if not isinstance(rows, list):
        return set()
    return {str(r.get("offer_id") or "") for r in rows if r.get("offer_id")}


def _build_row(item: dict, *, is_new: bool, info: dict | None = None) -> dict:
    cur = item.get("price") or {}
    info = info or {}
    try:
        cur_price = int(float(cur.get("price") or 0))
    except (TypeError, ValueError):
        cur_price = 0
    try:
        cur_old = int(float(cur.get("old_price") or 0))
    except (TypeError, ValueError):
        cur_old = 0
    suggested, suggested_old = _suggested_prices(item)
    idx_val = _price_index_value(item)
    return {
        "offer_id": str(item.get("offer_id") or info.get("offer_id") or ""),
        "product_id": item.get("product_id"),
        "name": info.get("name") or "",
        "image": info.get("image") or "",
        "cur_price": cur_price,
        "cur_old_price": cur_old,
        "price_index": f"{idx_val:.2f}" if idx_val else "RED",
        "suggested_price": suggested,
        "suggested_old_price": suggested_old,
        "is_new": is_new,
    }


def scan_red_prices() -> list[dict]:
    """拉全店价格，筛 RED 并写入 pending_price_review.json。"""
    base = _require_ready()
    suppressed = _active_suppressed(_load_suppressed(base))
    previous = _previous_offer_ids(base)

    all_prices = _fetch_all_prices()
    red_items = [it for it in all_prices if _is_red(it)]
    red_items = [it for it in red_items if str(it.get("offer_id") or "") not in suppressed]

    product_ids = [int(it["product_id"]) for it in red_items if it.get("product_id")]
    info_by_id = fetch_product_info(product_ids) if product_ids else {}

    rows: list[dict] = []
    for it in red_items:
        pid = str(it.get("product_id") or "")
        info = info_by_id.get(pid) or {}
        offer_id = str(it.get("offer_id") or info.get("offer_id") or "")
        is_new = offer_id not in previous if offer_id else False
        rows.append(_build_row(it, is_new=is_new, info=info))

    rows.sort(key=lambda r: (not r.get("is_new"), r.get("offer_id") or ""))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save_json(
        base / "pending_price_review.json",
        {"generated_at": generated_at, "rows": rows},
    )
    return rows


def get_pending_price_review() -> dict | None:
    base = ozon_data_dir()
    if not base:
        return None
    cached = _load_json(base / "pending_price_review.json", None)
    if isinstance(cached, dict) and cached.get("rows") is not None:
        return cached
    return None


def apply_prices(items: list[dict]) -> dict:
    """批量改价并写入 price_log.json。"""
    _require_ready()
    if not items:
        return {"result": []}

    prices = []
    for it in items:
        offer_id = str(it.get("offer_id") or "").strip()
        if not offer_id:
            continue
        prices.append(
            {
                "offer_id": offer_id,
                "price": str(it.get("price") or ""),
                "old_price": str(it.get("old_price") or ""),
                "currency_code": "RUB",
            }
        )
    if not prices:
        return {"result": []}

    resp = ozon_post(IMPORT_PRICES_PATH, {"prices": prices})
    result = resp.get("result") or []

    base = ozon_data_dir()
    if base:
        log_path = base / "price_log.json"
        log = _load_json(log_path, [])
        if not isinstance(log, list):
            log = []
        today = str(date.today())
        for it in items:
            offer_id = str(it.get("offer_id") or "")
            updated = any(
                str(r.get("offer_id") or "") == offer_id and r.get("updated")
                for r in result
            )
            if updated:
                log.append(
                    {
                        "date": today,
                        "offer_id": offer_id,
                        "price": it.get("price"),
                        "old_price": it.get("old_price"),
                    }
                )
        _save_json(log_path, log[-500:])

    return {"result": result}


def suppress_prices(offer_ids: list[str]) -> dict:
    """14 天内不再提醒这些 offer。"""
    base = _require_ready()
    until = date.today() + timedelta(days=SUPPRESS_DAYS)
    until_str = until.isoformat()

    path = base / "price_suppress.json"
    raw = _load_json(path, {})
    suppressed = _load_suppressed(base)
    for oid in offer_ids:
        oid = str(oid or "").strip()
        if oid:
            suppressed[oid] = until_str
    _save_json(path, {"suppressed": suppressed, "suppressed_until": until_str})

    cached = get_pending_price_review()
    if cached and isinstance(cached.get("rows"), list):
        hide = set(str(x) for x in offer_ids)
        cached["rows"] = [r for r in cached["rows"] if str(r.get("offer_id") or "") not in hide]
        _save_json(base / "pending_price_review.json", cached)

    return {"ok": True, "suppressed_until": until_str, "count": len(offer_ids)}


def get_daily_summary() -> dict | None:
    base = ozon_data_dir()
    if not base:
        return None
    data = _load_json(base / "daily_summary.json", None)
    return data if isinstance(data, dict) else None
