"""Ozon Seller API HTTP 客户端。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from core.http_retry import DEFAULT_SSL_CTX, urlopen
from modules.ozon.config import ozon_credentials

BASE = "https://api-seller.ozon.ru"


def ozon_post(path: str, body: dict, *, timeout: int = 90) -> dict:
    cid, key = ozon_credentials()
    if not cid or not key:
        raise RuntimeError("未配置 Ozon Client-Id / Api-Key")
    url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Client-Id": cid,
            "Api-Key": key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout, context=DEFAULT_SSL_CTX) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ozon HTTP {e.code}: {err[:400]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ozon 网络错误: {e.reason}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ozon 响应非 JSON: {raw[:200]}") from e
