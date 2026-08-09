"""Miaoshou-backed source-link precollection for the first review page.

This adapter only writes source links to the common collection box and reads
the resulting details. It never claims a product to a shop or publishes it.
Credentials stay in the existing local Miaoshou config file.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.config import ROOT

FETCH_PATH = "/open/v1/product/common_collect_box/common_collect_box/fetch_item"
LIST_PATH = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list"
DETAIL_PATH = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
BASE_URL = "https://openapi-erp.91miaoshou.com"
CACHE_DIR = ROOT / "data" / "new_product_workbench"
MAIN_REPO = Path(os.environ.get("TIKTOK_ECOMM_HOME") or r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
CONFIG_CANDIDATES = (
    ROOT / "config" / "miaoshou.local.json",
    MAIN_REPO / "config" / "miaoshou.local.json",
)
OPEN_REQUEST_INTERVAL_SECONDS = 1.1
_open_request_lock = threading.Lock()
_last_open_request_at = 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_path(offer_id: str) -> Path:
    return CACHE_DIR / f"{offer_id}_miaoshou.json"


def load_cache(offer_id: str) -> dict[str, Any]:
    path = cache_path(offer_id)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_config() -> dict[str, Any]:
    for path in CONFIG_CANDIDATES:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("Miaoshou local config was not found in the workspace or main project.")


def _sign(secret: str, path: str, timestamp: int, key: str, body_json: str) -> str:
    content = f"{secret}{path}{timestamp}{key}{body_json}{secret}"
    return hmac.new(secret.encode(), content.encode(), hashlib.sha256).hexdigest()


def _wait_for_open_slot() -> None:
    """Space signed Open API requests below the account's one-second limit."""

    global _last_open_request_at
    with _open_request_lock:
        now = time.monotonic()
        remaining = OPEN_REQUEST_INTERVAL_SECONDS - (now - _last_open_request_at)
        if _last_open_request_at and remaining > 0:
            time.sleep(remaining)
            now = time.monotonic()
        _last_open_request_at = now


def post_open(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _load_config()
    key = str(cfg["app_id"])
    secret = str(cfg["app_secret"])
    root = str(cfg.get("base_url") or BASE_URL).rstrip("/")
    payload = body or {}
    body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    _wait_for_open_slot()
    timestamp = int(time.time())
    request = urllib.request.Request(
        root + path,
        data=body_json.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-app-key": key,
            "x-timestamp": str(timestamp),
            "x-sign": _sign(secret, path, timestamp, key, body_json),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Miaoshou HTTP {exc.code}: {raw[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Miaoshou network error: {exc}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Miaoshou returned non-JSON data: {raw[:300]}") from exc
    if str(result.get("result") or "").lower() != "success":
        raise RuntimeError(str(result.get("message") or result.get("code") or "Miaoshou request failed"))
    return result


def source_item_id(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("goods_id", "id", "item_id"):
        value = str((query.get(key) or [""])[0]).strip()
        if re.fullmatch(r"\d{9,20}", value):
            return value
    patterns = (
        r"/offer/(\d{9,20})\.html",
        r"/(\d{12,20})(?:[/?#.]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, parsed.geturl())
        if match:
            return match.group(1)
    return ""


def _find_item(source_id: str, *, post: Callable = post_open) -> dict[str, Any] | None:
    response = post(
        LIST_PATH,
        {
            "pageNo": 1,
            "pageSize": 10,
            "filter": {"tabPaneName": "all", "sourceItemIdKeyword": source_id},
        },
    )
    items = (response.get("data") or {}).get("detailList") or []
    exact = []
    for item in items:
        ids = {str(x.get("sourceItemId") or "") for x in item.get("sourceList") or []}
        if source_id in ids:
            exact.append(item)
    candidates = exact or items
    if not candidates:
        return None
    # Miaoshou may put a later duplicate row in ``skip`` while the earlier
    # canonical row remains ``success``.  The unreadable alias must not shadow
    # the exact successful source record.
    for status in ("success", "collecting", "pending", "processing"):
        preferred = next(
            (
                item
                for item in candidates
                if str(item.get("status") or "").strip().lower() == status
            ),
            None,
        )
        if preferred is not None:
            return preferred
    return candidates[0]


def _fetch_detail(common_id: int, *, post: Callable = post_open) -> dict[str, Any]:
    response = post(DETAIL_PATH, {"commonCollectBoxDetailId": int(common_id)})
    data = response.get("data") or {}
    return data.get("editCommonCollectBoxDetail") or data


def _find_item_by_common_detail_id(
    common_id: int,
    *,
    post: Callable = post_open,
    max_pages: int = 20,
) -> dict[str, Any] | None:
    """Read the bounded collect-box index to identify one duplicate alias."""

    page_size = 100
    wanted = str(int(common_id))
    for page_no in range(1, max_pages + 1):
        response = post(
            LIST_PATH,
            {
                "pageNo": page_no,
                "pageSize": page_size,
                "filter": {"tabPaneName": "all"},
            },
        )
        data = response.get("data") or {}
        items = data.get("detailList") or []
        for item in items:
            if str(item.get("commonCollectBoxDetailId") or "") == wanted:
                return item
        if not items or len(items) < page_size:
            break
        try:
            total = int(data.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if total and page_no * page_size >= total:
            break
    return None


def source_meta(detail: dict[str, Any]) -> dict[str, str]:
    source = (detail.get("sourceList") or [{}])[0] or {}
    return {
        "source": str(source.get("source") or ""),
        "source_id": str(source.get("sourceItemId") or ""),
        "source_url": str(source.get("sourceItemUrl") or ""),
    }


def _resolve_skipped_duplicate_detail(
    common_id: int,
    *,
    post: Callable = post_open,
) -> tuple[int, dict[str, Any]] | None:
    """Resolve a skipped duplicate to the exact successful source record."""

    alias = _find_item_by_common_detail_id(common_id, post=post)
    if str((alias or {}).get("status") or "").strip().lower() != "skip":
        return None
    source_ids = {
        str(item.get("sourceItemId") or "").strip()
        for item in (alias or {}).get("sourceList") or []
        if str(item.get("sourceItemId") or "").strip()
    }
    if len(source_ids) != 1:
        return None
    source_id = next(iter(source_ids))
    canonical = _find_item(source_id, post=post)
    if str((canonical or {}).get("status") or "").strip().lower() != "success":
        return None
    try:
        resolved_id = int(canonical.get("commonCollectBoxDetailId"))
    except (TypeError, ValueError):
        return None
    if resolved_id == int(common_id):
        return None
    detail = _fetch_detail(resolved_id, post=post)
    if source_meta(detail).get("source_id") != source_id:
        raise RuntimeError(
            "Miaoshou duplicate resolution changed the exact source identity"
        )
    return resolved_id, detail


def _detail_image_urls(detail: dict[str, Any]) -> list[str]:
    urls = [str(x) for x in detail.get("imgUrls") or [] if x]
    notes = html.unescape(str(detail.get("notes") or ""))
    urls.extend(re.findall(r'''(?:src|data-src)=["'](https?://[^"']+)["']''', notes, re.I))
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _collect_failure_notes(item: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for key in ("reason", "failReason", "errorMessage", "message", "remark"):
        value = str(item.get(key) or "").strip()
        if value and value not in notes:
            notes.append(html.unescape(value))
    return notes


def normalize_detail(detail: dict[str, Any], *, source_url: str, source_id: str) -> dict[str, Any]:
    attrs = {
        str(x.get("name") or "").strip(): str(x.get("value") or "").strip()
        for x in detail.get("sourceAttrs") or []
        if x.get("name")
    }
    sku_rows = []
    for name, row in (detail.get("skuMap") or {}).items():
        row = row or {}
        sku_rows.append(
            {
                "key": str(name),
                "name": str(name).strip(";"),
                "item_num": str(row.get("itemNum") or ""),
                "price": row.get("price"),
                "stock": row.get("stock"),
                "weight": row.get("weight"),
                "package_cm": [row.get("packageLength"), row.get("packageWidth"), row.get("packageHeight")],
            }
        )
    dimensions = [detail.get("packageLength"), detail.get("packageWidth"), detail.get("packageHeight")]
    has_dimensions = all(x not in (None, "", 0) for x in dimensions)
    category = [str(x) for x in detail.get("cateList") or [] if x]
    return {
        "source_url": source_url,
        "source_id": source_id,
        "common_collect_id": detail.get("commonCollectBoxDetailId"),
        "source_item_code": str(detail.get("itemNum") or attrs.get("货号") or ""),
        "title": str(detail.get("title") or ""),
        "cost_cny": detail.get("price"),
        "stock": detail.get("stock"),
        "weight_kg": detail.get("weight"),
        "weight_present": detail.get("weight") not in (None, "", 0),
        "package_cm": dimensions if has_dimensions else [],
        "category_path": category,
        "images": _detail_image_urls(detail),
        "main_image_count": len(detail.get("imgUrls") or []),
        "video_url": str(detail.get("mainImgVideoUrl") or ""),
        "attributes": attrs,
        "skus": sku_rows,
        "notes_html": str(detail.get("notes") or ""),
    }


def import_common_collect_detail(
    common_id: str | int,
    *,
    post: Callable = post_open,
    state_key: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Import one Miaoshou common collect detail as a first-review source."""
    cid = int(str(common_id).strip())
    key = state_key or str(cid)
    resolved_cid = cid
    try:
        detail = _fetch_detail(cid, post=post)
    except RuntimeError as original_error:
        # A duplicate row has no independent detail payload.  Follow only its
        # exact source identity to an already-successful canonical row; never
        # guess by title, image, seller code, or numeric proximity.
        resolved = _resolve_skipped_duplicate_detail(cid, post=post)
        if resolved is None:
            raise original_error
        resolved_cid, detail = resolved
    meta = source_meta(detail)
    normalized = normalize_detail(
        detail,
        source_url=(
            meta.get("source_url")
            or f"miaoshou://common_collect/{resolved_cid}"
        ),
        source_id=meta.get("source_id") or str(resolved_cid),
    )
    record = {
        "url": normalized["source_url"],
        "source_id": normalized["source_id"],
        "common_collect_id": resolved_cid,
        "status": "success",
        "title": normalized.get("title"),
        "source": meta.get("source") or "miaoshou",
        "notes": [],
    }
    payload = {
        "offer_id": key,
        "mode": "miaoshou_common_collect_detail",
        "requested_common_collect_id": cid,
        "resolved_common_collect_id": resolved_cid,
        "resolved_duplicate": resolved_cid != cid,
        "input_sources": [{
            "url": record["url"],
            "source_id": record["source_id"],
            "common_collect_id": resolved_cid,
        }],
        "records": [record],
        "normalized": normalized,
        "updated_at": _now(),
        "side_effect_scope": "read_existing_common_collect_detail_only",
        "claimed": False,
        "published": False,
        "support_cod": True,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(key).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return key, payload


def refresh_precollect(
    offer_id: str,
    source_url: str,
    overseas_urls: list[str] | None = None,
    *,
    force: bool = False,
    post: Callable = post_open,
) -> dict[str, Any]:
    urls = [source_url] + [str(x).strip() for x in overseas_urls or [] if str(x).strip()]
    source_ids = [source_item_id(url) for url in urls]
    signature = [{"url": url, "source_id": sid} for url, sid in zip(urls, source_ids)]
    cached = load_cache(offer_id)
    if not force and cached.get("input_sources") == signature and cached.get("normalized"):
        return cached

    records: list[dict[str, Any]] = []
    missing_urls: list[str] = []
    known: dict[str, dict[str, Any]] = {}
    for url, sid in zip(urls, source_ids):
        if not sid:
            records.append({"url": url, "source_id": "", "status": "unsupported", "notes": ["No source item id found."]})
            continue
        item = _find_item(sid, post=post)
        if item:
            known[sid] = item
        else:
            missing_urls.append(url)

    if missing_urls:
        response = post(FETCH_PATH, {"collectLinks": missing_urls})
        mapping = (response.get("data") or {}).get("sourceItemIdAndDetailIdMap") or {}
        for url in missing_urls:
            sid = source_item_id(url)
            if sid in mapping:
                known[sid] = {
                    "commonCollectBoxDetailId": mapping[sid],
                    "status": "collecting",
                    "sourceList": [{"sourceItemId": sid, "sourceItemUrl": url}],
                }

    deadline = time.time() + 24
    while time.time() < deadline:
        pending = [sid for sid, item in known.items() if str(item.get("status") or "").lower() not in {"success", "fail"}]
        if not pending:
            break
        time.sleep(2)
        for sid in pending:
            refreshed = _find_item(sid, post=post)
            if refreshed:
                known[sid] = refreshed

    normalized: dict[str, Any] = {}
    for url, sid in zip(urls, source_ids):
        if not sid:
            continue
        item = known.get(sid) or {}
        status = str(item.get("status") or "unknown").lower()
        common_id = item.get("commonCollectBoxDetailId")
        record = {
            "url": url,
            "source_id": sid,
            "common_collect_id": common_id,
            "status": status,
            "title": item.get("title"),
            "source": str(((item.get("sourceList") or [{}])[0]).get("source") or ""),
            "notes": _collect_failure_notes(item),
        }
        if status == "success" and common_id:
            detail = _fetch_detail(int(common_id), post=post)
            record["detail"] = detail
            candidate = normalize_detail(detail, source_url=url, source_id=sid)
            if sid == offer_id or not normalized:
                normalized = candidate
        records.append(record)

    payload = {
        "offer_id": offer_id,
        "mode": "miaoshou_common_collect_preflight",
        "input_sources": signature,
        "records": records,
        "normalized": normalized,
        "updated_at": _now(),
        "side_effect_scope": "common_collect_box_only",
        "claimed": False,
        "published": False,
        "support_cod": True,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(offer_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
