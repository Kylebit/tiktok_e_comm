"""Feishu websocket bot entrypoint."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from modules.hub import feishu_commands as cmd_mod
from modules.hub import feishu_events as evt_mod
from modules.hub.feishu_app import app_config, app_ready, reply_text

_CODE_ROOT = Path(__file__).resolve().parents[2]
_SLOW_CMDS = frozenset({"send_both", "send_main_image", "batch_send_both"})


def _safe_log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8", "replace"))
        sys.stdout.flush()


def _process_message(message_id: str, text: str) -> None:
    try:
        reply = cmd_mod.handle_command(text, message_id=message_id)
        if reply:
            reply_text(message_id, reply)
            _safe_log(f"[feishu] replied {len(reply)} chars")
        else:
            _safe_log("[feishu] command handled without text reply")
    except Exception as exc:
        _safe_log(f"[feishu] handler failed: {exc}")
        try:
            reply_text(message_id, f"处理出错：{str(exc)[:200]}")
        except Exception as reply_exc:
            _safe_log(f"[feishu] fallback reply failed: {reply_exc}")


def _patch_websocket_ssl() -> None:
    import functools
    import ssl

    import websockets

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    orig = websockets.connect

    @functools.wraps(orig)
    def connect(url, *args, **kwargs):
        if url.startswith("wss:") and kwargs.get("ssl") is not False:
            kwargs.setdefault("ssl", ctx)
        return orig(url, *args, **kwargs)

    websockets.connect = connect


def _disable_proxy_in_runtime() -> None:
    """Disable env and Windows proxy discovery for the bot process."""
    import urllib.request

    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]
    for key in proxy_keys:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

    urllib.request.getproxies = lambda: {}
    urllib.request.proxy_bypass = lambda host: True
    urllib.request.proxy_bypass_environment = lambda host, proxies=None: True

    try:
        import requests
        import requests.sessions
        import requests.utils

        orig_merge = requests.sessions.Session.merge_environment_settings

        def merge_environment_settings(self, url, proxies, stream, verify, cert):
            merged = orig_merge(self, url, {}, stream, verify, cert)
            merged["proxies"] = {}
            return merged

        requests.sessions.Session.merge_environment_settings = merge_environment_settings
        requests.utils.get_environ_proxies = lambda url, no_proxy=None: {}
        requests.sessions.get_environ_proxies = lambda url, no_proxy=None: {}
    except Exception as exc:
        print(f"[feishu] disable proxy patch skipped: {exc}")


def print_setup_guide() -> None:
    print(
        "\n".join(
            [
                "============================================",
                "Feishu bot setup",
                "1. Open https://open.feishu.cn/app and create a self-built app",
                "2. Enable bot capability",
                "3. Grant message receive and send permissions",
                "4. Subscribe to im.message.receive_v1",
                "5. Configure config/settings.json -> feishu_app",
                "6. Run: python main.py feishu bot",
                "============================================",
            ]
        )
    )


def run_websocket_bot() -> None:
    """Start the Feishu websocket bot."""
    if not app_ready():
        print_setup_guide()
        raise RuntimeError("Please configure feishu_app.app_id / app_secret / enabled first")

    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
    except ImportError as exc:
        print(f"Missing or incompatible lark-oapi: {exc}")
        print("Run: pip install 'lark-oapi>=1.4.0'")
        raise SystemExit(1) from exc

    c = app_config()
    _disable_proxy_in_runtime()
    _patch_websocket_ssl()

    def on_message(data: P2ImMessageReceiveV1) -> None:
        parsed = evt_mod.extract_message_from_p2_event(data)
        if not parsed:
            _safe_log("[feishu] ignored non-text or empty message")
            return
        message_id, text = parsed
        _safe_log(f"[feishu] received: {text[:80]!r}")
        cmd, args = cmd_mod.parse_command(text)
        if cmd in _SLOW_CMDS:
            hint = args or text.strip()
            try:
                reply_text(message_id, f"⏳ 正在查询 SKU {hint}…")
            except Exception as ack_exc:
                _safe_log(f"[feishu] ack failed: {ack_exc}")
        threading.Thread(
            target=_process_message,
            args=(message_id, text),
            daemon=True,
        ).start()

    encrypt = c["encrypt_key"] or ""
    verify = c["verification_token"] or ""
    handler = (
        lark.EventDispatcherHandler.builder(encrypt, verify)
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    cli = lark.ws.Client(
        c["app_id"],
        c["app_secret"],
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    print(f"[feishu] code root: {_CODE_ROOT}")
    print("Feishu websocket bot connected, waiting for @ messages...")
    cli.start()


def test_reply(message_id: str) -> None:
    reply_text(message_id, "测试回复 OK，双向交互已连通")
