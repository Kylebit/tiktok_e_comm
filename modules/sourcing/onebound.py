"""万邦 Onebound 1688 商品详情。"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request

from core.config import get
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry


def _cfg() -> dict:
    return (get("sourcing", {}) or {}).get("onebound") or {}


def parse_offer_id(url_or_id: str) -> str:
    raw = (url_or_id or "").strip()
    if re.fullmatch(r"\d+", raw):
        return raw
    m = re.search(r"offer/(\d+)", raw)
    if m:
        return m.group(1)
    m = re.search(r"(\d{8,})", raw)
    return m.group(1) if m else ""


def fetch_item(offer_id: str, *, pro: bool = False) -> dict:
    """1688/item_get 或 item_get_pro。"""
    cfg = _cfg()
    key = (cfg.get("api_key") or "").strip()
    secret = (cfg.get("api_secret") or "").strip()
    base = (cfg.get("base_url") or "https://api-gw.onebound.cn").rstrip("/")
    if not key or not secret:
        raise ValueError("未配置 sourcing.onebound.api_key / api_secret")
    num_iid = parse_offer_id(offer_id)
    if not num_iid:
        raise ValueError("无效 1688 链接或 offer id")

    api = "item_get_pro" if pro else "item_get"
    params = {
        "key": key,
        "secret": secret,
        "num_iid": num_iid,
        "cache": "no",
        "result_type": "json",
        "lang": "cn",
    }
    url = f"{base}/1688/{api}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    with urlopen_retry(req, timeout=45, context=SSL_CTX) as resp:
        import json

        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    err = (data.get("error") or data.get("reason") or "").strip()
    code = str(data.get("error_code") or data.get("code") or "")
    if err or code not in ("", "0000", "0"):
        hint = ""
        if "1688无权访问" in err or code == "4012":
            hint = "（请在万邦控制台「我的接口」添加 1688/item_get，并确认套餐已绑定当前 Key）"
        elif code == "4005":
            hint = "（Key/Secret 错误或该 Key 未开通 1688；若购买后换了新 Key 请更新 config/settings.json）"
        elif code == "4016":
            hint = "（账户余额不足，请充值）"
        raise RuntimeError(f"[{code}] {err}{hint}".strip())
    item = data.get("item") or data.get("items") or {}
    if isinstance(item, list):
        item = item[0] if item else {}
    return {"ok": True, "num_iid": num_iid, "raw": data, "item": item}


def item_summary(item: dict) -> dict:
    """提取常用字段供预览。"""
    if not item:
        return {}
    pics = item.get("item_imgs") or item.get("images") or []
    if isinstance(pics, dict):
        pics = list(pics.values())
    img_urls = []
    for p in pics:
        if isinstance(p, str):
            u = p if p.startswith("http") else f"https:{p}"
            img_urls.append(u)
        elif isinstance(p, dict):
            u = p.get("url") or p.get("pic_url") or ""
            if u:
                img_urls.append(u if u.startswith("http") else f"https:{u}")
    if not img_urls and item.get("pic_url"):
        u = item["pic_url"]
        img_urls.append(u if str(u).startswith("http") else f"https:{u}")
    return {
        "title": item.get("title") or item.get("item_title") or "",
        "price": item.get("price") or item.get("promotion_price"),
        "seller": item.get("nick") or item.get("seller_nick") or "",
        "num_iid": item.get("num_iid") or item.get("offer_id") or "",
        "image_count": len(img_urls),
        "first_image": img_urls[0] if img_urls else "",
        "detail_url": item.get("detail_url") or item.get("url") or "",
    }
