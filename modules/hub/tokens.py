"""各平台 Token 自动刷新（首次 OAuth 仍需浏览器授权一次）。"""

from __future__ import annotations

from typing import Callable


def refresh_all(on_progress: Callable[[str], None] | None = None) -> dict:
    """同步/启动前调用：TikTok + Shopee 过期 token 自动续期。"""
    out: dict = {"tiktok": None, "shopee": [], "errors": []}

    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    prog("Token：TikTok…")
    try:
        from core import auth

        tok = auth.ensure_valid_token()
        out["tiktok"] = {
            "ok": True,
            "access_expires": auth.access_expires_at(tok).isoformat() if auth.access_expires_at(tok) else None,
        }
    except Exception as e:
        out["errors"].append(f"TikTok: {e}")

    prog("Token：Shopee 四国…")
    try:
        from modules.shopee.config import ready as shopee_ready
        from modules.shopee.auth import ensure_merchant_token, ensure_shop_token, load_tokens
        from modules.shopee.shops import list_sync_shops

        if not shopee_ready():
            out["shopee"] = {"skipped": True}
        else:
            store = load_tokens()
            for t in list_sync_shops():
                sid = int(t["shop_id"])
                try:
                    ensure_shop_token(sid)
                    out["shopee"].append({"shop_id": sid, "region": t.get("region"), "ok": True})
                except Exception as e:
                    out["shopee"].append({"shop_id": sid, "ok": False, "error": str(e)})
                    out["errors"].append(f"Shopee {sid}: {e}")
            for mid in store.get("merchant_id_list") or []:
                try:
                    ensure_merchant_token(int(mid))
                except Exception:
                    pass
    except Exception as e:
        out["errors"].append(f"Shopee: {e}")

    # Ozon 使用 Client-Id + Api-Key，无 OAuth
    return out


def status_summary() -> dict:
    """各平台 token 状态摘要（供 /api/status）。"""
    summary: dict = {"tiktok": {}, "shopee": {}, "ozon": {"auth": "api_key"}}

    try:
        from core import auth

        tok = auth.load_token()
        summary["tiktok"] = {
            "has_token": True,
            "access_expires": auth.access_expires_at(tok).isoformat() if auth.access_expires_at(tok) else None,
            "refresh_expires": auth.refresh_expires_at(tok).isoformat() if auth.refresh_expires_at(tok) else None,
            "needs_reauth": auth.is_refresh_expired(tok),
        }
    except Exception as e:
        summary["tiktok"] = {"has_token": False, "error": str(e)}

    try:
        from modules.shopee.config import ready as shopee_ready
        from modules.shopee.auth import load_tokens
        import time

        if shopee_ready():
            shops = load_tokens().get("shops") or {}
            now = int(time.time())
            summary["shopee"] = {
                "shops": len(shops),
                "expired": sum(1 for s in shops.values() if int(s.get("expire_at") or 0) < now),
                "has_refresh": sum(1 for s in shops.values() if s.get("refresh_token")),
            }
        else:
            summary["shopee"] = {"enabled": False}
    except Exception as e:
        summary["shopee"] = {"error": str(e)}

    return summary
