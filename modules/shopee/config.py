"""Shopee 配置（config/settings.json → shopee）。"""

from __future__ import annotations

from core.config import get

HOSTS = {
    "test": "https://partner.test-stable.shopeemobile.com",
    "live": "https://partner.shopeemobile.com",
}
# 浏览器授权页（中国大陆请用 openplatform.shopee.cn，海外用 partner.shopeemobile.com）
AUTH_HOSTS = {
    "test": "https://openplatform.sandbox.test-stable.shopee.cn",
    "live_cn": "https://openplatform.shopee.cn",
    "live": "https://partner.shopeemobile.com",
}


def shopee_config() -> dict:
    cfg = get("shopee") or {}
    env = (cfg.get("environment") or "test").strip().lower()
    if env not in HOSTS:
        env = "test"
    auth_host = (cfg.get("auth_host") or "").strip()
    if not auth_host:
        auth_host = AUTH_HOSTS["live_cn" if cfg.get("auth_region") == "cn" else env]
    return {
        "enabled": bool(cfg.get("enabled")),
        "environment": env,
        "host": HOSTS[env],
        "auth_host": auth_host.rstrip("/"),
        "partner_id": int(cfg.get("partner_id") or 0),
        "partner_key": (cfg.get("partner_key") or "").strip(),
        "redirect_url": (cfg.get("redirect_url") or "https://open.shopee.com").strip(),
        "token_file": cfg.get("token_file") or "shopee_tokens.json",
        "regions": list(cfg.get("regions") or ["MY", "VN", "TH", "PH"]),
    }


def ready() -> bool:
    c = shopee_config()
    return c["enabled"] and c["partner_id"] > 0 and bool(c["partner_key"])
