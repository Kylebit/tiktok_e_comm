"""飞书机器人长连接（免公网 URL）或 HTTP 回调说明。"""

from __future__ import annotations

import json

from modules.hub import feishu_commands as cmd_mod
from modules.hub import feishu_events as evt_mod
from modules.hub.feishu_app import app_config, app_ready, reply_text


def _patch_websocket_ssl() -> None:
    """lark ws 默认校验证书；本地/代理环境可能与 http_retry 一样需要放宽。"""
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


def print_setup_guide() -> None:
    print(
        """
╔══════════════════════════════════════════════════════════╗
║  飞书双向交互 · 自建应用配置（一次性）                    ║
╠══════════════════════════════════════════════════════════╣
║ 1. 打开 https://open.feishu.cn/app → 创建企业自建应用    ║
║ 2. 应用能力 → 开启「机器人」                              ║
║ 3. 权限管理 → 开通：                                      ║
║    · 获取群组中用户 @ 机器人消息 (im:message.group_at_msg)║
║    · 以应用身份发消息 (im:message:send_as_bot)            ║
║ 4. 事件与回调 → 添加事件「接收消息 im.message.receive_v1」║
║ 5. 订阅方式（二选一）：                                     ║
║    A) 长连接（推荐本地）：python3 main.py feishu bot       ║
║    B) HTTP：配置请求 URL 为                                ║
║       https://你的域名/api/feishu/event                    ║
║       （本地可用 ngrok http 8765）                          ║
║ 6. 发布应用 → 把机器人拉进日报群                            ║
║ 7. config/settings.json 填写 feishu_app：                  ║
║    enabled, app_id, app_secret                            ║
║ 8. 群里 @机器人 帮助                                       ║
╚══════════════════════════════════════════════════════════╝

说明：
· 自定义 Webhook 机器人（已有）= 单向推送日报
· 自建应用机器人 = 双向 @ 指令交互
· 两者可同时使用
"""
    )


def run_websocket_bot() -> None:
    """长连接模式（需 pip install lark-oapi）。"""
    if not app_ready():
        print_setup_guide()
        raise RuntimeError("请先配置 feishu_app（app_id / app_secret / enabled）")

    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
    except ImportError as e:
        print(f"缺少或版本不兼容 lark-oapi: {e}")
        print("请运行：pip3 install 'lark-oapi>=1.4.0'")
        print("或使用 HTTP 模式：python3 main.py serve + ngrok")
        raise SystemExit(1) from e

    c = app_config()
    _patch_websocket_ssl()

    def on_message(data: P2ImMessageReceiveV1) -> None:
        parsed = evt_mod.extract_message_from_p2_event(data)
        if not parsed:
            print("[feishu] 收到非文本或空消息，已忽略")
            return
        message_id, text = parsed
        print(f"[feishu] 收到: {text[:80]!r}")
        try:
            reply = cmd_mod.handle_command(text)
            reply_text(message_id, reply)
            print(f"[feishu] 已回复 {len(reply)} 字")
        except Exception as ex:
            print(f"[feishu] 处理失败: {ex}")
            try:
                reply_text(message_id, f"处理出错：{str(ex)[:200]}")
            except Exception as reply_ex:
                print(f"[feishu] 回复失败: {reply_ex}")

    encrypt = c["encrypt_key"] or ""
    verify = c["verification_token"] or ""
    handler = (
        lark.EventDispatcherHandler.builder(encrypt, verify)
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    cli = lark.ws.Client(c["app_id"], c["app_secret"], event_handler=handler, log_level=lark.LogLevel.INFO)
    print("飞书长连接已启动，等待 @ 机器人消息…（Ctrl+C 退出）")
    cli.start()


def test_reply(message_id: str) -> None:
    reply_text(message_id, "测试回复 OK · 双向交互已连通")
