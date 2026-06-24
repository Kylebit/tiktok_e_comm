"""飞书自定义机器人 Webhook 推送。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from core.config import get
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry


def feishu_config() -> dict:
    cfg = get("feishu") or {}
    return {
        "enabled": bool(cfg.get("enabled")),
        "webhook_url": (cfg.get("webhook_url") or "").strip(),
        "console_base_url": (cfg.get("console_base_url") or "http://127.0.0.1:8765").rstrip("/"),
        "ozon_data_dir": (cfg.get("ozon_data_dir") or "").strip(),
    }


def send_text(text: str, *, webhook_url: str | None = None) -> None:
    url = webhook_url or feishu_config()["webhook_url"]
    if not url:
        raise RuntimeError("未配置 feishu.webhook_url")
    payload = {"msg_type": "text", "content": {"text": text[:4000]}}
    _post_webhook(url, payload)


def send_post(title: str, content_rows: list[list[dict]], *, webhook_url: str | None = None) -> None:
    """发送富文本 post。content_rows: [[{tag, text, href?}, ...], ...]"""
    url = webhook_url or feishu_config()["webhook_url"]
    if not url:
        raise RuntimeError("未配置 feishu.webhook_url")
    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title[:100],
                    "content": content_rows,
                }
            }
        },
    }
    _post_webhook(url, payload)


def _post_webhook(url: str, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"飞书 Webhook HTTP {e.code}: {err[:300]}") from e

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        return
    # 飞书成功: {"StatusCode":0,"StatusMessage":"success","code":0,"msg":"success"}
    code = result.get("code")
    status = result.get("StatusCode")
    if code not in (0, None) or (status is not None and status != 0):
        raise RuntimeError(f"飞书返回错误: {result}")
