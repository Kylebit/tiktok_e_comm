"""Shopee Shop API 请求（签名 + token）。"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.parse
import urllib.request
from pathlib import Path

from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry
from modules.shopee.config import shopee_config
from modules.shopee.sign import sign_merchant, sign_partner, sign_shop

_BOUNDARY = "----ShopeeUploadBoundary7MA4YWxkTrZu0gW"


def _parse_json(raw: str, ctx: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Shopee {ctx} 非 JSON: {raw[:300]}") from e


def shop_get(path: str, shop_id: int, access_token: str, params: dict | None = None) -> dict:
    c = shopee_config()
    ts, sig = sign_shop(path, c["partner_id"], c["partner_key"], access_token, shop_id)
    q = {
        "partner_id": c["partner_id"],
        "timestamp": ts,
        "sign": sig,
        "access_token": access_token,
        "shop_id": shop_id,
    }
    if params:
        q.update(params)
    url = f"{c['host']}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, method="GET")
    with urlopen_retry(req, timeout=60, context=SSL_CTX) as resp:
        return _parse_json(resp.read().decode("utf-8"), path)


def shop_post(path: str, shop_id: int, access_token: str, body: dict) -> dict:
    c = shopee_config()
    ts, sig = sign_shop(path, c["partner_id"], c["partner_key"], access_token, shop_id)
    q = {
        "partner_id": c["partner_id"],
        "timestamp": ts,
        "sign": sig,
        "access_token": access_token,
        "shop_id": shop_id,
    }
    url = f"{c['host']}{path}?{urllib.parse.urlencode(q)}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen_retry(req, timeout=90, context=SSL_CTX) as resp:
        return _parse_json(resp.read().decode("utf-8"), path)


def upload_image(file_path: Path, *, scene: str = "normal") -> dict:
    """v2.media_space.upload_image — Partner 签名，无 shop token。"""
    c = shopee_config()
    path = "/api/v2/media_space/upload_image"
    ts, sig = sign_partner(path, c["partner_id"], c["partner_key"])
    q = urllib.parse.urlencode(
        {"partner_id": c["partner_id"], "timestamp": ts, "sign": sig}
    )
    url = f"{c['host']}{path}?{q}"

    fp = Path(file_path)
    if not fp.is_file():
        raise FileNotFoundError(fp)
    mime = mimetypes.guess_type(str(fp))[0] or "image/jpeg"
    body_parts = [
        f"--{_BOUNDARY}\r\n".encode(),
        f'Content-Disposition: form-data; name="scene"\r\n\r\n{scene}\r\n'.encode(),
        f"--{_BOUNDARY}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="image"; filename="{fp.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode(),
        fp.read_bytes(),
        b"\r\n",
        f"--{_BOUNDARY}--\r\n".encode(),
    ]
    payload = b"".join(body_parts)
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={_BOUNDARY}"},
    )
    with urlopen_retry(req, timeout=120, context=SSL_CTX) as resp:
        data = _parse_json(resp.read().decode("utf-8"), path)
    if data.get("error"):
        raise RuntimeError(data.get("message") or data.get("error") or data)
    return data.get("response") or data


def get_shop_info(shop_id: int, access_token: str) -> dict:
    return shop_get("/api/v2/shop/get_shop_info", shop_id, access_token)


def resolve_global_item_id(
    shop_id: int,
    merchant_id: int,
    merchant_token: str,
    item_id: int | str,
) -> int | None:
    """CNSC：店铺 item_id → global_item_id（merchant API + item_id_list）。"""
    iid = int(item_id)
    resp = merchant_get(
        "/api/v2/global_product/get_global_item_id",
        merchant_id,
        merchant_token,
        {"shop_id": int(shop_id), "item_id_list": str(iid)},
    )
    err = (resp.get("error") or "").strip()
    if err and err != "-":
        return None
    for row in (resp.get("response") or {}).get("item_id_map") or []:
        if int(row.get("item_id") or 0) == iid:
            gid = row.get("global_item_id")
            return int(gid) if gid else None
    return None


def merchant_post(path: str, merchant_id: int, access_token: str, body: dict) -> dict:
    c = shopee_config()
    ts, sig = sign_merchant(path, c["partner_id"], c["partner_key"], access_token, merchant_id)
    q = {
        "partner_id": c["partner_id"],
        "timestamp": ts,
        "sign": sig,
        "access_token": access_token,
        "merchant_id": merchant_id,
    }
    url = f"{c['host']}{path}?{urllib.parse.urlencode(q)}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen_retry(req, timeout=90, context=SSL_CTX) as resp:
        return _parse_json(resp.read().decode("utf-8"), path)


def merchant_get(path: str, merchant_id: int, access_token: str, params: dict | None = None) -> dict:
    c = shopee_config()
    ts, sig = sign_merchant(path, c["partner_id"], c["partner_key"], access_token, merchant_id)
    q = {
        "partner_id": c["partner_id"],
        "timestamp": ts,
        "sign": sig,
        "access_token": access_token,
        "merchant_id": merchant_id,
    }
    if params:
        q.update(params)
    url = f"{c['host']}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, method="GET")
    with urlopen_retry(req, timeout=60, context=SSL_CTX) as resp:
        return _parse_json(resp.read().decode("utf-8"), path)
