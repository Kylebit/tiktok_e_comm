"""Ozon 配置：settings.json 或 ozon/webapp/app.py 凭据。"""

from __future__ import annotations

import re
from pathlib import Path

from core.config import ROOT, get


def ozon_data_dir() -> Path | None:
    raw = (get("ozon.data_dir") or get("feishu.ozon_data_dir") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def _from_webapp_app() -> tuple[str, str]:
    data = ozon_data_dir()
    candidates = []
    if data:
        candidates.append(data.parent / "app.py")
    candidates.append(ROOT.parent / "ozon" / "webapp" / "app.py")
    for app_py in candidates:
        if not app_py.is_file():
            continue
        text = app_py.read_text(encoding="utf-8")
        m1 = re.search(r'CLIENT_ID\s*=\s*"([^"]+)"', text)
        m2 = re.search(r'API_KEY\s*=\s*"([^"]+)"', text)
        if m1 and m2:
            return m1.group(1), m2.group(1)
    return "", ""


def ozon_credentials() -> tuple[str, str]:
    cfg = get("ozon") or {}
    cid = str(cfg.get("client_id") or "").strip()
    key = str(cfg.get("api_key") or "").strip()
    if cid and key:
        return cid, key
    return _from_webapp_app()


def ready() -> bool:
    cid, key = ozon_credentials()
    return bool(cid and key and ozon_data_dir())
