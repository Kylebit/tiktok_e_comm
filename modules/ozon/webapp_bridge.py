"""桥接 ozon/webapp Flask 应用（同一台机器上的兄弟目录）。"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

from core.config import ROOT


def webapp_dir() -> Path:
    from modules.ozon.config import ozon_data_dir

    data = ozon_data_dir()
    if data and (data.parent / "app.py").is_file():
        return data.parent
    fallback = ROOT.parent / "ozon" / "webapp"
    if fallback.is_dir():
        return fallback
    raise RuntimeError(
        "找不到 Ozon webapp。请配置 ozon.data_dir 或 feishu.ozon_data_dir，"
        f"或确保存在 {fallback}"
    )


@lru_cache(maxsize=1)
def get_flask_app():
    wd = webapp_dir()
    wd_str = str(wd)
    if wd_str not in sys.path:
        sys.path.insert(0, wd_str)
    import app as ozon_app  # noqa: WPS433 — ozon/webapp/app.py

    return ozon_app.app


def proxy_request(
    method: str,
    subpath: str,
    *,
    query: str | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes, str]:
    """转发到 ozon/webapp 的 /api/{subpath}，返回 (status, body_bytes, content_type)。"""
    app = get_flask_app()
    api_path = "/api/" + subpath.lstrip("/")
    if query:
        api_path += "?" + query.lstrip("?")

    with app.test_client() as client:
        if method.upper() == "GET":
            resp = client.get(api_path)
        elif method.upper() == "POST":
            resp = client.post(
                api_path,
                data=body or b"",
                content_type="application/json" if body else None,
            )
        else:
            raise ValueError(f"unsupported method: {method}")
        ct = resp.content_type or "application/json"
        return resp.status_code, resp.get_data(), ct


def proxy_json(method: str, subpath: str, *, query: str | None = None, payload: dict | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    status, raw, _ = proxy_request(method, subpath, query=query, body=body)
    if not raw:
        return status, None
    try:
        return status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", errors="replace")
