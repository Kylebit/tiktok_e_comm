"""Feishu app helpers: token, text reply, image reply, SKU image, purchase link."""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.request

from core.config import get
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry

_TOKEN: dict[str, float | str] = {"value": "", "expires_at": 0.0}


def app_config() -> dict:
    cfg = get("feishu_app") or {}
    return {
        "enabled": bool(cfg.get("enabled")),
        "app_id": (cfg.get("app_id") or "").strip(),
        "app_secret": (cfg.get("app_secret") or "").strip(),
        "verification_token": (cfg.get("verification_token") or "").strip(),
        "encrypt_key": (cfg.get("encrypt_key") or "").strip(),
    }


def app_ready() -> bool:
    c = app_config()
    return c["enabled"] and bool(c["app_id"]) and bool(c["app_secret"])


def tenant_access_token(*, force: bool = False) -> str:
    c = app_config()
    if not app_ready():
        raise RuntimeError("未配置 feishu_app.app_id / app_secret 或未 enabled")
    now = time.time()
    if not force and _TOKEN["value"] and now < float(_TOKEN["expires_at"]) - 60:
        return str(_TOKEN["value"])

    payload = json.dumps({"app_id": c["app_id"], "app_secret": c["app_secret"]}).encode("utf-8")
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {body.get('msg') or body}")
    _TOKEN["value"] = body["tenant_access_token"]
    _TOKEN["expires_at"] = now + int(body.get("expire", 7200))
    return str(_TOKEN["value"])


def reply_text(message_id: str, text: str) -> None:
    token = tenant_access_token()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
    content = json.dumps({"text": text[:4000]}, ensure_ascii=False)
    payload = json.dumps({"msg_type": "text", "content": content}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"飞书回复 HTTP {exc.code}: {err[:300]}") from exc
    if body.get("code") != 0:
        raise RuntimeError(f"飞书回复失败: {body.get('msg') or body}")


def mimetype_to_ext(mime: str) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(mime, "jpg")


def upload_image(image_bytes: bytes, filename: str = "image.jpg") -> str:
    import requests as req_lib

    token = tenant_access_token()
    mime_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
    if not any(filename.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
        ext = mimetype_to_ext(mime_type)
        filename = f"image.{ext}" if ext else "image.jpg"

    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}
    files = {
        "image_type": (None, "message"),
        "image": (filename, image_bytes, mime_type),
    }
    resp = req_lib.post(url, headers=headers, files=files, timeout=60)
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"飞书图片上传失败 [{body.get('code')}]: {body.get('msg')}")
    return body["data"]["image_key"]


def reply_image(message_id: str, image_key: str) -> None:
    token = tenant_access_token()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
    content = json.dumps({"image_key": image_key}, ensure_ascii=False)
    payload = json.dumps({"msg_type": "image", "content": content}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"飞书图片回复 HTTP {exc.code}: {err[:300]}") from exc
    if body.get("code") != 0:
        raise RuntimeError(f"飞书图片回复失败: {body.get('msg') or body}")


def _image_unavailable_text(product_name: str | None, seller_sku: str | None = None) -> str:
    display_name = (product_name or seller_sku or "该商品").strip()
    return f"[{display_name}] 图片暂时无法加载，请稍后再试"


def download_image(url: str, timeout: int = 10) -> bytes:
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    user_agents = [
        "TikTokShop/1.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ]
    timeouts = (timeout, timeout * 2, timeout * 4)
    last_err: Exception | None = None
    for attempt_index, attempt_timeout in enumerate(timeouts):
        for ua in user_agents:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Referer": "https://www.tiktok.com/"})
            try:
                with urllib.request.urlopen(req, timeout=attempt_timeout, context=ctx) as resp:
                    data = resp.read()
                    if len(data) > 1024:
                        return data
                    last_err = RuntimeError(f"image too small: {len(data)} bytes")
            except (TimeoutError, socket.timeout, urllib.error.URLError, OSError, RuntimeError) as exc:
                last_err = exc
                continue
        if attempt_index < len(timeouts) - 1:
            time.sleep(2**attempt_index)
    raise RuntimeError(f"图片下载失败 ({url[:80]}...): {last_err}") from last_err


def _db_path() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "shop.db"))


def _find_sku(sku: str) -> list:
    conn = sqlite3.connect(_db_path())
    try:
        rows = conn.execute(
            "SELECT sku_id, seller_sku, product_name, image_url FROM products "
            "WHERE seller_sku = ? OR sku_id = ? LIMIT 10",
            (sku, sku),
        ).fetchall()
        if rows:
            return rows

        try:
            rows = conn.execute(
                "SELECT sku_id, seller_sku, product_name, image_url FROM products "
                "WHERE parent_sku = ? LIMIT 10",
                (sku,),
            ).fetchall()
            if rows:
                return rows
        except sqlite3.OperationalError:
            pass

        if len(sku) == 4 and sku.isdigit():
            pattern = f"%{sku}"
            try:
                return conn.execute(
                    "SELECT sku_id, seller_sku, product_name, image_url FROM products "
                    "WHERE seller_sku LIKE ? OR parent_sku = ? LIMIT 10",
                    (pattern, sku),
                ).fetchall()
            except sqlite3.OperationalError:
                return conn.execute(
                    "SELECT sku_id, seller_sku, product_name, image_url FROM products "
                    "WHERE seller_sku LIKE ? LIMIT 10",
                    (pattern,),
                ).fetchall()
        return []
    finally:
        conn.close()


def reply_product_image(message_id: str, sku: str) -> str:
    matches = _find_sku(sku)
    if not matches:
        conn = sqlite3.connect(_db_path())
        try:
            sample_rows = conn.execute(
                "SELECT seller_sku, product_name FROM products WHERE seller_sku IS NOT NULL AND seller_sku != '' LIMIT 10"
            ).fetchall()
        finally:
            conn.close()
        sample = "、".join(r[0] for r in sample_rows if r[0])
        raise RuntimeError(f"未找到 SKU【{sku}】的商品（支持后四位匹配）。现有 SKU 示例：{sample}")
    if len(matches) > 1:
        best = None
        for row in matches:
            if row[1] == sku:
                best = row
                break
        if not best:
            best = min(matches, key=lambda row: len(str(row[1])))
        _, seller_sku, product_name, image_url = best
    else:
        _, seller_sku, product_name, image_url = matches[0]
    if not image_url:
        raise RuntimeError(f"SKU【{seller_sku}】未存储主图地址")
    try:
        img_bytes = download_image(image_url)
    except Exception:
        return _image_unavailable_text(product_name, seller_sku)
    if len(img_bytes) > 10 * 1024 * 1024:
        raise RuntimeError("图片过大（>10MB），无法发送")
    ext = image_url.rsplit(".", 1)[-1].split("?")[0][:4] or "jpg"
    filename = f"{seller_sku}_main.{ext}"
    try:
        image_key = upload_image(img_bytes, filename)
        reply_image(message_id, image_key)
    except Exception:
        return _image_unavailable_text(product_name, seller_sku)
    return f"✅ 已发送 SKU {seller_sku} 主图（{str(product_name)[:30]}）"


def reply_product_link(sku: str) -> str:
    conn = sqlite3.connect(_db_path())
    try:
        matches = _find_sku(sku)
        canonical_sku = sku
        if matches:
            best = None
            for row in matches:
                if row[1] == sku:
                    best = row
                    break
            if not best:
                best = min(matches, key=lambda row: len(str(row[1])))
            canonical_sku = str(best[1])
        row = conn.execute(
            "SELECT url, platform, updated_at FROM purchasing_links "
            "WHERE sku = ? OR parent_sku = ? ORDER BY updated_at DESC LIMIT 1",
            (canonical_sku, sku),
        ).fetchone()
        if not row:
            return f"📦 SKU {canonical_sku} 暂无采购链接，请发送“{sku} 采购链接 https://...”保存"
        url, _platform, updated = row
        date_text = updated[:10] if updated else ""
        return f"📦 {canonical_sku} 采购链接（{date_text}）\n{url}"
    finally:
        conn.close()
