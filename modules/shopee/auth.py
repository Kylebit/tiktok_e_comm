"""Shopee OAuth：授权链接、换 token、刷新。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.config import ROOT
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry
from modules.shopee.config import ready, shopee_config
from modules.shopee.sign import sign_partner


def token_path() -> Path:
    rel = shopee_config()["token_file"]
    p = Path(rel)
    return p if p.is_absolute() else ROOT / rel


def load_tokens() -> dict:
    path = token_path()
    if not path.is_file():
        return {"shops": {}}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_tokens(data: dict) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def auth_partner_url() -> str:
    """生成店铺授权链接（浏览器打开，回调 URL 带 code + shop_id）。"""
    if not ready():
        raise RuntimeError("未配置 shopee.partner_id / partner_key")
    c = shopee_config()
    path = "/api/v2/shop/auth_partner"
    ts, sig = sign_partner(path, c["partner_id"], c["partner_key"])
    q = {
        "partner_id": c["partner_id"],
        "timestamp": ts,
        "sign": sig,
        "redirect": c["redirect_url"],
    }
    return f"{c['auth_host']}{path}?{urllib.parse.urlencode(q)}"


def _token_get_body(code: str, *, shop_id: int | None = None, main_account_id: int | None = None) -> bytes:
    c = shopee_config()
    payload: dict = {"code": code, "partner_id": c["partner_id"]}
    if main_account_id is not None:
        payload["main_account_id"] = int(main_account_id)
    elif shop_id is not None:
        payload["shop_id"] = int(shop_id)
    else:
        raise ValueError("需要 shop_id 或 main_account_id")
    return json.dumps(payload).encode("utf-8")


def _call_token_get(code: str, *, shop_id: int | None = None, main_account_id: int | None = None) -> dict:
    if not ready():
        raise RuntimeError("未配置 shopee")
    c = shopee_config()
    path = "/api/v2/auth/token/get"
    ts, sig = sign_partner(path, c["partner_id"], c["partner_key"])
    url = f"{c['host']}{path}?partner_id={c['partner_id']}&timestamp={ts}&sign={sig}"
    body = _token_get_body(code, shop_id=shop_id, main_account_id=main_account_id)
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data.get("message") or data)
    if not data.get("access_token"):
        raise RuntimeError(f"换 token 失败: {data}")
    return data


def exchange_code(code: str, shop_id: int) -> dict:
    """用授权 code + shop_id 换取 access_token。"""
    data = _call_token_get(code, shop_id=shop_id)
    entry = {
        "shop_id": int(shop_id),
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expire_at": int(time.time()) + int(data.get("expire_in") or 14400),
        "region": data.get("region") or "",
        "updated_at": int(time.time()),
    }
    store = load_tokens()
    store.setdefault("shops", {})[str(shop_id)] = entry
    save_tokens(store)
    return entry


def exchange_code_main(code: str, main_account_id: int) -> dict:
    """主账号授权（回调带 main_account_id）→ token + shop_id_list。"""
    data = _call_token_get(code, main_account_id=main_account_id)
    now = int(time.time())
    expire_at = now + int(data.get("expire_in") or 14400)
    store = load_tokens()
    store["main_account_id"] = int(main_account_id)
    store["merchant_id_list"] = data.get("merchant_id_list") or []
    shops = store.setdefault("shops", {})
    merchants = store.setdefault("merchants", {})

    # 主账号 token 通常共用；为每个 shop_id 建条目便于后续 API
    shop_ids = data.get("shop_id_list") or []
    if not shop_ids and data.get("shop_id"):
        shop_ids = [data["shop_id"]]

    entry_base = {
        "main_account_id": int(main_account_id),
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expire_at": expire_at,
        "updated_at": now,
    }
    for mid in store["merchant_id_list"]:
        merchants[str(mid)] = {
            **entry_base,
            "merchant_id": int(mid),
        }
    if not shop_ids:
        key = f"main_{main_account_id}"
        shops[key] = {**entry_base, "shop_id": None, "region": ""}
    else:
        for sid in shop_ids:
            shops[str(sid)] = {**entry_base, "shop_id": int(sid), "region": ""}

    save_tokens(store)
    return {"main_account_id": main_account_id, "shop_id_list": shop_ids, **entry_base}


def refresh_merchant_token(merchant_id: int) -> dict:
    """CNSC 全球商品 API 须用 merchant_id 刷新的 access_token（与 shop token 独立）。"""
    c = shopee_config()
    store = load_tokens()
    merchants = store.setdefault("merchants", {})
    merchant = merchants.get(str(merchant_id)) or {}
    refresh = merchant.get("refresh_token")
    if not refresh:
        # 首次授权后 shop 与 merchant 共用 refresh_token，可从任一 shop 取
        for shop in (store.get("shops") or {}).values():
            if shop.get("refresh_token"):
                refresh = shop["refresh_token"]
                break
    if not refresh:
        raise RuntimeError(
            f"merchant {merchant_id} 无 refresh_token，请运行 python3 main.py shopee auth-url 重新授权（勾选 Auth Merchant）"
        )
    path = "/api/v2/auth/access_token/get"
    ts, sig = sign_partner(path, c["partner_id"], c["partner_key"])
    url = f"{c['host']}{path}?partner_id={c['partner_id']}&timestamp={ts}&sign={sig}"
    body = json.dumps(
        {
            "merchant_id": int(merchant_id),
            "refresh_token": refresh,
            "partner_id": c["partner_id"],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"merchant token 刷新失败 ({exc.code}): {detail}. "
            "请运行 python3 main.py shopee auth-url 重新授权，并勾选 Auth Merchant。"
        ) from exc
    if data.get("error"):
        raise RuntimeError(data.get("message") or data)
    entry = {
        "merchant_id": int(merchant_id),
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or refresh,
        "expire_at": int(time.time()) + int(data.get("expire_in") or 14400),
        "updated_at": int(time.time()),
    }
    merchants[str(merchant_id)] = entry
    store["merchants"] = merchants
    save_tokens(store)
    return entry


def ensure_merchant_token(merchant_id: int, *, shop_id: int | None = None) -> str:
    """全球商品 API 用 merchant token；过期则按 merchant_id 刷新。"""
    store = load_tokens()
    merchants = store.get("merchants") or {}
    entry = merchants.get(str(merchant_id)) or {}
    token = entry.get("access_token") or ""
    if token and int(entry.get("expire_at") or 0) >= int(time.time()) + 120:
        return token
    if entry.get("refresh_token") or any(
        s.get("refresh_token") for s in (store.get("shops") or {}).values()
    ):
        return refresh_merchant_token(merchant_id)["access_token"]
    if shop_id:
        return ensure_shop_token(shop_id)
    raise RuntimeError(
        f"merchant {merchant_id} 无 token，请重新 Shopee 授权（勾选 Auth Merchant）"
    )


def ensure_shop_token(shop_id: int) -> str:
    store = load_tokens()
    entry = store.get("shops", {}).get(str(shop_id), {})
    token = entry.get("access_token") or ""
    if not token:
        raise RuntimeError(f"shop_id={shop_id} 无 token，请 shopee auth")
    if int(entry.get("expire_at") or 0) < int(time.time()) + 120:
        entry = refresh_token(shop_id)
        token = entry["access_token"]
    return token


def refresh_token(shop_id: int) -> dict:
    c = shopee_config()
    store = load_tokens()
    shop = store.get("shops", {}).get(str(shop_id))
    if not shop or not shop.get("refresh_token"):
        raise RuntimeError(f"shop {shop_id} 无 refresh_token，请重新授权")
    path = "/api/v2/auth/access_token/get"
    ts, sig = sign_partner(path, c["partner_id"], c["partner_key"])
    url = f"{c['host']}{path}?partner_id={c['partner_id']}&timestamp={ts}&sign={sig}"
    body = json.dumps(
        {
            "shop_id": int(shop_id),
            "refresh_token": shop["refresh_token"],
            "partner_id": c["partner_id"],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data.get("message") or data)
    shop.update(
        {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token") or shop["refresh_token"],
            "expire_at": int(time.time()) + int(data.get("expire_in") or 14400),
            "updated_at": int(time.time()),
        }
    )
    store["shops"][str(shop_id)] = shop
    save_tokens(store)
    return shop


def status_text() -> str:
    from modules.shopee.shops import status_lines

    if not ready():
        return "Shopee：未配置 partner_id / partner_key"
    shops = load_tokens().get("shops") or {}
    if not shops:
        lines = status_lines()[:1]
        lines.append("已授权店铺: 0")
        lines.append("  运行 python3 main.py shopee auth-url 获取授权链接")
        return "\n".join(lines)
    if not load_tokens().get("sync_shop_ids"):
        try:
            from modules.shopee.shops import refresh_shop_regions
            refresh_shop_regions(quiet=True)
        except Exception:
            pass
    return "\n".join(status_lines())
