"""Shop Open API 授权：读取/保存 token，过期前自动 refresh。"""

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from core.config import ROOT, get, load_settings
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry

AUTH_HOST = "https://auth.tiktok-shops.com"
REFRESH_BUFFER_SEC = 300  # 过期前 5 分钟主动刷新


def token_path() -> Path:
    rel = get("token_file", "tiktok_tokens.json")
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def load_token() -> dict:
    path = token_path()
    if not path.is_file():
        raise FileNotFoundError(f"未找到 token 文件 {path}，请先运行: python3 main.py auth")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_token(data: dict) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _expiry_ts(token: dict, key: str) -> int | None:
    v = token.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def access_expires_at(token: dict) -> datetime | None:
    ts = _expiry_ts(token, "access_token_expire_in")
    return datetime.fromtimestamp(ts) if ts else None


def refresh_expires_at(token: dict) -> datetime | None:
    ts = _expiry_ts(token, "refresh_token_expire_in")
    return datetime.fromtimestamp(ts) if ts else None


def is_access_expired(token: dict, buffer_sec: int = REFRESH_BUFFER_SEC) -> bool:
    exp = _expiry_ts(token, "access_token_expire_in")
    if exp is None:
        return False
    return time.time() >= exp - buffer_sec


def is_refresh_expired(token: dict) -> bool:
    exp = _expiry_ts(token, "refresh_token_expire_in")
    if exp is None:
        return False
    return time.time() >= exp


def refresh_access_token(force: bool = False) -> dict:
    """用 refresh_token 换取新的 access_token（TikTok 约 7 天有效）。"""
    token = load_token()
    if not force and not is_access_expired(token, buffer_sec=0):
        return token

    refresh = token.get("refresh_token")
    if not refresh:
        raise RuntimeError("无 refresh_token，请重新授权: python3 main.py auth")
    if is_refresh_expired(token):
        raise RuntimeError("refresh_token 已过期，请重新授权: python3 main.py auth")

    settings = load_settings()
    params = {
        "app_key": settings["app_key"],
        "app_secret": settings["app_secret"],
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }
    url = f"{AUTH_HOST}/api/v2/token/refresh?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
        result = json.loads(resp.read())

    if result.get("code") != 0:
        msg = result.get("message", str(result))
        raise RuntimeError(f"刷新 Token 失败: {msg}（若持续失败请运行 python3 main.py auth）")

    data = result["data"]
    updated = {
        **token,
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or refresh,
        "access_token_expire_in": data.get("access_token_expire_in"),
        "refresh_token_expire_in": data.get(
            "refresh_token_expire_in", token.get("refresh_token_expire_in")
        ),
        "saved_at": int(time.time()),
    }
    if data.get("open_id"):
        updated["open_id"] = data["open_id"]
    if data.get("seller_name"):
        updated["seller_name"] = data["seller_name"]
    if data.get("authorized_shops"):
        updated["authorized_shops"] = data["authorized_shops"]

    save_token(updated)
    return updated


def ensure_valid_token() -> dict:
    token = load_token()
    if is_access_expired(token):
        return refresh_access_token(force=True)
    return token


def access_token() -> str:
    return ensure_valid_token()["access_token"]
