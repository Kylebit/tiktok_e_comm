"""授权店铺列表。"""

from core.api_client import get


def list_shops(access_token: str) -> list[dict]:
    result = get("/authorization/202309/shops", access_token)
    if result.get("code") != 0:
        raise RuntimeError(result.get("message", "获取店铺失败"))
    data = result.get("data") or {}
    return data.get("shops", data.get("list", []))
