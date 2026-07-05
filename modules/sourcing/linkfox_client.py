"""Controlled client for selected LinkFox data APIs.

This module intentionally does not implement Skill installation, image upload,
or the LinkFox feedback endpoint. Paid requests require an explicit runtime
flag in addition to the API key environment variable.
"""

from __future__ import annotations

import ipaddress
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from core.http_retry import urlopen as urlopen_retry

BASE_URL = "https://tool-gateway.linkfox.com"
API_KEY_ENV = "LINKFOXAGENT_API_KEY"

TOOL_PATHS = {
    "echotik_product_search": "/echotik/listProduct",
    "echotik_new_product_rank": "/echotik/listNewProductRank",
    "dld_product_search": "/dld/productSearch",
    "alibaba1688_image_search": "/alibaba1688/imageSearch",
}

LIVELYHIVE_REGIONS = frozenset({"PH", "MY", "TH", "VN"})
_MAX_PAGE_SIZE = 20


class LinkfoxClientError(RuntimeError):
    pass


def _bounded_int(value, name: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return number


def validate_public_https_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("imageUrl must be a public HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("imageUrl must not contain embedded credentials")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("imageUrl must not point to a local host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError("imageUrl must not point to a private or reserved IP")
    return url


def validate_payload(tool: str, payload: dict) -> dict:
    if tool not in TOOL_PATHS:
        raise ValueError(f"unsupported LinkFox tool: {tool}")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    clean = dict(payload)

    if "pageSize" in clean:
        clean["pageSize"] = _bounded_int(clean["pageSize"], "pageSize", 1, _MAX_PAGE_SIZE)
    if "pageNum" in clean:
        clean["pageNum"] = _bounded_int(clean["pageNum"], "pageNum", 1, 10)
    if "pageIndex" in clean:
        clean["pageIndex"] = _bounded_int(clean["pageIndex"], "pageIndex", 1, 10)

    if tool.startswith("echotik_"):
        region = str(clean.get("region") or "").upper()
        if region not in LIVELYHIVE_REGIONS:
            raise ValueError("region must be one of PH, MY, TH, VN")
        clean["region"] = region

    if tool == "echotik_product_search":
        keyword = str(clean.get("keyword") or "").strip()
        category = str(clean.get("categoryKeywordCN") or "").strip()
        if not keyword and not category:
            raise ValueError("keyword or categoryKeywordCN is required")
        if len(keyword) > 1000 or len(category) > 1000:
            raise ValueError("search keyword is too long")

    if tool == "echotik_new_product_rank":
        if not str(clean.get("date") or "").strip():
            raise ValueError("date is required")

    if tool == "dld_product_search":
        keyword = str(clean.get("keyWord") or "").strip()
        goods_url = str(clean.get("goodsUrl") or "").strip()
        if not keyword and not goods_url:
            raise ValueError("keyWord or goodsUrl is required")
        if len(keyword) > 50:
            raise ValueError("keyWord must not exceed 50 characters")

    if tool == "alibaba1688_image_search":
        forbidden = {"imageBase64", "imageId"} & set(clean)
        if forbidden:
            raise ValueError("only public imageUrl input is allowed; Base64 and imageId are disabled")
        clean["imageUrl"] = validate_public_https_url(clean.get("imageUrl"))

    return clean


class LinkfoxClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        opener: Callable = urlopen_retry,
        timeout: float = 90,
    ):
        self._api_key = (api_key or os.environ.get(API_KEY_ENV) or "").strip()
        self._opener = opener
        self._timeout = timeout

    def preview(self, tool: str, payload: dict) -> dict:
        clean = validate_payload(tool, payload)
        return {
            "mode": "preview_only_no_network",
            "tool": tool,
            "method": "POST",
            "url": f"{BASE_URL}{TOOL_PATHS[tool]}",
            "payload": clean,
            "requires": ["--execute-paid", API_KEY_ENV],
        }

    def execute(self, tool: str, payload: dict, *, allow_paid: bool = False) -> dict:
        if not allow_paid:
            raise PermissionError("paid LinkFox request requires allow_paid=True")
        if not self._api_key:
            raise LinkfoxClientError(f"missing {API_KEY_ENV}")

        clean = validate_payload(tool, payload)
        body = json.dumps(clean, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE_URL}{TOOL_PATHS[tool]}",
            data=body,
            method="POST",
            headers={
                "Authorization": self._api_key,
                "Content-Type": "application/json",
                "User-Agent": "Orbit-Hive-LinkFox-Adapter/1.0",
            },
        )
        try:
            with self._opener(
                req,
                timeout=self._timeout,
                context=ssl.create_default_context(),
            ) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LinkfoxClientError(f"LinkFox HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LinkfoxClientError(f"LinkFox request failed: {exc}") from exc

        code = result.get("errorCode", result.get("errcode", 200))
        if str(code) not in {"", "0", "200", "None"}:
            message = result.get("errmsg") or result.get("message") or "business error"
            raise LinkfoxClientError(f"LinkFox error {code}: {message}")
        return result

