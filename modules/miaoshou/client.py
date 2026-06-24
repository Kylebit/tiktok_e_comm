"""妙手开放平台客户端（HmacSHA256 签名）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CFG = ROOT / "config" / "miaoshou.local.json"
BASE_URL = "https://openapi-erp.91miaoshou.com"


def load_config(path: Path | None = None) -> dict:
    cfg_path = path or DEFAULT_CFG
    if not cfg_path.exists():
        raise FileNotFoundError(f"缺少配置文件: {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def generate_sign(
    app_secret: str,
    path: str,
    timestamp: int,
    app_key: str,
    body_json: str = "",
) -> str:
    content = f"{app_secret}{path}{timestamp}{app_key}{body_json}{app_secret}"
    return hmac.new(
        app_secret.encode("utf-8"),
        content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def post_open(
    path: str,
    body: dict | None = None,
    *,
    app_key: str | None = None,
    app_secret: str | None = None,
    base_url: str | None = None,
    cfg: dict | None = None,
) -> dict[str, Any]:
    conf = cfg or load_config()
    key = app_key or conf["app_id"]
    secret = app_secret or conf["app_secret"]
    root = (base_url or conf.get("base_url") or BASE_URL).rstrip("/")

    if not path.startswith("/"):
        path = "/" + path

    payload = body or {}
    body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    timestamp = int(time.time())
    sign = generate_sign(secret, path, timestamp, key, body_json)

    req = urllib.request.Request(
        root + path,
        data=body_json.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-app-key": key,
            "x-timestamp": str(timestamp),
            "x-sign": sign,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {e.code}: {raw}") from e
        data["_http_status"] = e.code
        return data
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"非 JSON 响应: {raw[:500]}") from e


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
