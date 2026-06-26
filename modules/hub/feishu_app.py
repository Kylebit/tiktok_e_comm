"""飞书自建应用：tenant token、回复消息（双向交互）。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from core.config import get
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry

_TOKEN: dict = {"value": "", "expires_at": 0.0}


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
    if not force and _TOKEN["value"] and now < _TOKEN["expires_at"] - 60:
        return _TOKEN["value"]

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
    return _TOKEN["value"]


def reply_text(message_id: str, text: str) -> None:
    """回复某条消息（群 @ 机器人 / 单聊）。"""
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
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"飞书回复 HTTP {e.code}: {err[:300]}") from e
    if body.get("code") != 0:
        raise RuntimeError(f"飞书回复失败: {body.get('msg') or body}")


def send_message(
    receive_id: str,
    msg_type: str,
    content: dict | str,
    *,
    receive_id_type: str = "chat_id",
) -> dict:
    """向群/用户发送消息（自建应用）。"""
    import urllib.parse

    token = tenant_access_token()
    query = urllib.parse.urlencode({"receive_id_type": receive_id_type})
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?{query}"
    if isinstance(content, dict):
        content_str = json.dumps(content, ensure_ascii=False)
    else:
        content_str = content
    payload = json.dumps(
        {"receive_id": receive_id, "msg_type": msg_type, "content": content_str},
        ensure_ascii=False,
    ).encode("utf-8")
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
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"飞书发送 HTTP {e.code}: {err[:300]}") from e
    if body.get("code") != 0:
        raise RuntimeError(f"飞书发送失败: {body.get('msg') or body}")
    return body


def send_interactive_card(receive_id: str, card: dict, *, receive_id_type: str = "chat_id") -> dict:
    return send_message(receive_id, "interactive", card, receive_id_type=receive_id_type)


def upload_image_from_url(image_url: str, *, timeout: int = 25) -> str | None:
    """下载外链主图并上传飞书，返回 img_key；失败返回 None。"""
    if not image_url or not app_ready():
        return None
    try:
        req = urllib.request.Request(image_url, headers={"User-Agent": "OrbitHive/1.0"})
        with urlopen_retry(req, timeout=timeout, context=SSL_CTX) as resp:
            data = resp.read()
        if len(data) < 100 or len(data) > 10 * 1024 * 1024:
            return None
        token = tenant_access_token()
        boundary = "----OrbitHiveBoundary7MA4YWxkTrZu0gW"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image_type"\r\n\r\n'
            f"message\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="main.jpg"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/images",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("code") != 0:
            return None
        return (result.get("data") or {}).get("image_key")
    except Exception:
        return None
