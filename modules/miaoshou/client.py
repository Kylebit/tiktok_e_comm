"""Miaoshou API clients for both signed open endpoints and web endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import gzip
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from core.config import ROOT

OPEN_BASE_URL = "https://openapi-erp.91miaoshou.com"
WEB_BASE_URL = "https://erp.91miaoshou.com"
MAIN_REPO = Path(os.environ.get("TIKTOK_ECOMM_HOME") or r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
CONFIG_CANDIDATES = (
    ROOT / "config" / "miaoshou.local.json",
    MAIN_REPO / "config" / "miaoshou.local.json",
)


def _load_config() -> dict[str, Any]:
    for path in CONFIG_CANDIDATES:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("Miaoshou local config was not found in the workspace or main project.")


def load_config(path: Path | None = None) -> dict[str, Any]:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    return _load_config()


def _open_sign(secret: str, path: str, timestamp: int, key: str, body_json: str) -> str:
    content = f"{secret}{path}{timestamp}{key}{body_json}{secret}"
    return hmac.new(secret.encode(), content.encode(), hashlib.sha256).hexdigest()


def generate_sign(
    app_secret: str,
    path: str,
    timestamp: int,
    app_key: str,
    body_json: str = "",
) -> str:
    return _open_sign(app_secret, path, timestamp, app_key, body_json)


def _decode_json_response(response: urllib.request.addinfourl) -> dict[str, Any]:
    raw_bytes = response.read()
    encoding = str(response.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip" or raw_bytes[:2] == b"\x1f\x8b":
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except OSError:
            pass
    raw = raw_bytes.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Miaoshou returned non-JSON data: {raw[:300]}") from exc


def post_open(
    path: str,
    body: dict[str, Any] | None = None,
    *,
    app_key: str | None = None,
    app_secret: str | None = None,
    base_url: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = cfg or _load_config()
    key = str(app_key or config["app_id"])
    secret = str(app_secret or config["app_secret"])
    root = str(base_url or config.get("base_url") or OPEN_BASE_URL).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    payload = body or {}
    body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    timestamp = int(time.time())
    request = urllib.request.Request(
        root + path,
        data=body_json.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-app-key": key,
            "x-timestamp": str(timestamp),
            "x-sign": _open_sign(secret, path, timestamp, key, body_json),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = _decode_json_response(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Miaoshou HTTP {exc.code}: {raw[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Miaoshou network error: {exc}") from exc
    if str(result.get("result") or "").lower() != "success":
        raise RuntimeError(str(result.get("message") or result.get("code") or "Miaoshou request failed"))
    return result


def get_shop_list(
    platform: str,
    site: str,
    page_no: int = 1,
    page_size: int = 20,
    **kwargs: Any,
) -> dict[str, Any]:
    return post_open(
        "/open/v1/product/shop/shop/get_shop_list",
        {
            "platform": platform,
            "site": site,
            "pageNo": page_no,
            "pageSize": page_size,
        },
        **kwargs,
    )


def _default_web_headers(cfg: dict[str, Any], path: str) -> dict[str, str]:
    root = str(cfg.get("web_base_url") or cfg.get("erp_base_url") or WEB_BASE_URL).rstrip("/")
    referer = str(cfg.get("web_referer") or f"{root}/")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": root,
        "Referer": referer,
        "User-Agent": str(cfg.get("web_user_agent") or cfg.get("user_agent") or "Mozilla/5.0"),
        "X-Timestamp": str(int(time.time() * 1000)),
    }
    cookie = str(
        cfg.get("web_cookie")
        or cfg.get("erp_cookie")
        or os.environ.get("MIAOSHOU_WEB_COOKIE")
        or ""
    ).strip()
    if cookie:
        headers["Cookie"] = cookie
    for key, value in (cfg.get("web_headers") or {}).items():
        if value is None:
            continue
        headers[str(key)] = str(value)
    return headers


def _web_ok(result: dict[str, Any]) -> bool:
    if result.get("success") is True:
        return True
    code = result.get("code")
    if code in (0, "0", 200, "200"):
        return True
    status = str(result.get("status") or "").lower()
    return status in {"success", "ok"}


def request_web(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    form: dict[str, Any] | list[tuple[str, Any]] | str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    cfg = _load_config()
    root = str(cfg.get("web_base_url") or cfg.get("erp_base_url") or WEB_BASE_URL).rstrip("/")
    url = root + path
    if query:
        qs = urllib.parse.urlencode(query, doseq=True)
        url = f"{url}?{qs}"
    if isinstance(form, str):
        data = form.encode("utf-8")
    elif form is None:
        data = None
    else:
        data = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method.upper())
    merged_headers = _default_web_headers(cfg, path)
    if headers:
        merged_headers.update(headers)
    for key, value in merged_headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = _decode_json_response(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Miaoshou web HTTP {exc.code}: {raw[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Miaoshou web network error: {exc}") from exc
    if not _web_ok(result):
        raise RuntimeError(
            str(
                result.get("reason")
                or result.get("msg")
                or result.get("message")
                or result.get("code")
                or "Miaoshou web request failed"
            )
        )
    return result


def web_claim_common_to_platform(detail_id: int | str, *, platform: str = "tiktok", serial_number: int = 1) -> dict[str, Any]:
    return request_web(
        "POST",
        "/api/move/common_collect_box/claimed",
        form={
            "detailSerialNumberPlatformList[0][detailId]": int(detail_id),
            "detailSerialNumberPlatformList[0][platform]": platform,
            "detailSerialNumberPlatformList[0][serialNumber]": int(serial_number),
        },
    )


def web_claim_to_shop(detail_ids: list[int | str], shop_ids: list[int | str]) -> dict[str, Any]:
    form: list[tuple[str, Any]] = []
    for index, detail_id in enumerate(detail_ids):
        form.append((f"detailIds[{index}]", int(detail_id)))
    for index, shop_id in enumerate(shop_ids):
        form.append((f"shopIds[{index}]", int(shop_id)))
    return request_web("POST", "/api/platform/tiktok/move/collect_box/claimToShop", form=form)


def web_get_collect_item_info(detail_id: int | str) -> dict[str, Any]:
    return request_web(
        "POST",
        "/api/platform/tiktok/move/collect_box/getCollectItemInfo",
        form={"detailId": int(detail_id)},
    )


def web_get_site_and_claimed_shops_map(detail_id: int | str) -> dict[str, Any]:
    return request_web(
        "GET",
        "/api/platform/tiktok/move/collect_box/getSiteAndClaimedShopsMap",
        query={"detailId": int(detail_id)},
    )


def web_get_global_shop_warehouse_list(*, status: str = "enable") -> dict[str, Any]:
    return request_web(
        "GET",
        "/api/platform/tiktok/move/collect_box/getGlobalShopWarehouseList",
        query={"status": status},
    )


def web_get_shop_warehouse_list(shop_ids: list[int | str]) -> dict[str, Any]:
    query = [("shopIds[]", int(shop_id)) for shop_id in shop_ids]
    return request_web(
        "GET",
        "/api/platform/tiktok/move/collect_box/getShopWarehouseList",
        query=query,
    )


def web_check_sku_price_include_vat_covering_base_cost(
    *,
    package_weight: float,
    sku_map: dict[str, Any],
    site: str,
) -> dict[str, Any]:
    return request_web(
        "POST",
        "/api/platform/tiktok/move/collect_box/checkSkuPriceIncludeVatCoveringBaseCost",
        form={
            "packageWeight": package_weight,
            "skuMap": json.dumps(sku_map, ensure_ascii=False, separators=(",", ":")),
            "site": site,
        },
    )


def web_save_shop_collect_item_info(shop_collect_item_info: dict[str, Any]) -> dict[str, Any]:
    return request_web(
        "POST",
        "/api/platform/tiktok/move/collect_box/saveShopCollectItemInfo",
        form={
            "shopCollectItemInfo": json.dumps(
                shop_collect_item_info,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        },
    )
