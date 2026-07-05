"""Feishu HTTP event callback helpers."""

from __future__ import annotations

import json
import threading
from typing import Any

from modules.hub import feishu_commands as cmd_mod
from modules.hub.feishu_app import reply_text


def handle_http_body(body: dict) -> tuple[int, dict]:
    """Handle Feishu HTTP callback payload."""
    if body.get("type") == "url_verification":
        return 200, {"challenge": body.get("challenge")}

    header = body.get("header") or {}
    event_type = header.get("event_type") or body.get("event", {}).get("type")
    if event_type == "im.message.receive_v1":
        event = body.get("event") or {}
        msg = event.get("message") or {}
        message_id = msg.get("message_id")
        msg_type = msg.get("message_type")
        if message_id and msg_type == "text":
            try:
                content = json.loads(msg.get("content") or "{}")
                text = content.get("text") or ""
            except json.JSONDecodeError:
                text = ""
            if text.strip():
                threading.Thread(
                    target=_process_and_reply,
                    args=(message_id, text),
                    daemon=True,
                ).start()
        return 200, {}

    return 200, {}


def _process_and_reply(message_id: str, text: str) -> None:
    try:
        reply = cmd_mod.handle_command(text, message_id=message_id)
    except Exception as exc:
        reply = f"处理出错：{str(exc)[:300]}"
    if not reply:
        return
    try:
        reply_text(message_id, reply)
    except Exception as exc:
        print(f"[feishu] reply failed: {exc}")


def extract_message_from_p2_event(data: Any) -> tuple[str, str] | None:
    """Convert lark-oapi P2 event to (message_id, text)."""
    try:
        event = data.event
        msg = event.message
        if msg.message_type != "text":
            return None
        content = json.loads(msg.content or "{}")
        text = content.get("text") or ""
        if not text.strip():
            return None
        return msg.message_id, text
    except Exception:
        return None
