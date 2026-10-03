"""Ozon 配置：settings.json 或 webapp 本地凭据文件。"""

from __future__ import annotations

import json
from pathlib import Path

from core.config import ROOT, get, settings_base_dir
from shared_platform.ozon_runtime_credentials import configured_pinned_ozon_credentials


def ozon_data_dir() -> Path | None:
    raw = (get("ozon.data_dir") or get("feishu.ozon_data_dir") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = settings_base_dir() / p
        p = p.resolve()
        if p.is_dir():
            return p
    fallback = ROOT / "modules" / "ozon" / "legacy_webapp" / "data"
    if fallback.is_dir():
        return fallback
    legacy_sibling = ROOT.parent / "ozon" / "webapp" / "data"
    return legacy_sibling if legacy_sibling.is_dir() else None


def _webapp_data_dir() -> Path | None:
    return ozon_data_dir()


def _from_credentials_file(path: Path | None) -> tuple[str, str]:
    if path is None or not path.is_file():
        return "", ""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "", ""
    return str(d.get("client_id") or "").strip(), str(d.get("api_key") or "").strip()


def _from_local_credentials_file() -> tuple[str, str]:
    data = _webapp_data_dir()
    return _from_credentials_file(data / "credentials.local.json" if data else None)


def ozon_credentials() -> tuple[str, str]:
    pinned = configured_pinned_ozon_credentials()
    if pinned is not None:
        return pinned.account_id, pinned.api_key
    cfg = get("ozon") or {}
    cid = str(cfg.get("client_id") or "").strip()
    key = str(cfg.get("api_key") or "").strip()
    if cid and key:
        return cid, key
    cid, key = _from_credentials_file(settings_base_dir() / "config" / "ozon.local.json")
    if cid and key:
        return cid, key
    return _from_local_credentials_file()


def ready() -> bool:
    cid, key = ozon_credentials()
    return bool(cid and key and ozon_data_dir())
