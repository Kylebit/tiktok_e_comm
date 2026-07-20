# -*- coding: utf-8 -*-
"""
EigenFlux CLI 封装（a2a-eigenflux bridge 的传输层）

调用 `eigenflux msg send --receiver-id <id> --content "..."` 派发，
调用 `eigenflux msg history --conv-id <id>` 回收。
CLI 路径可配：环境变量 EIGENFLUX_CLI，否则在常见位置搜索。
"""
import glob
import json
import os
import shutil
import subprocess


def _find_cli():
    p = shutil.which("eigenflux")
    if p:
        return p
    home = os.path.expanduser("~")
    localapp = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        # Windows 原生安装位（EigenFlux 桌面客户端）
        os.path.join(localapp, "EigenFlux", "bin", "eigenflux.exe"),
        os.path.join(home, "AppData", "Local", "EigenFlux", "bin", "eigenflux.exe"),
        # node / npm 全局
        os.path.join(home, ".workbuddy", "binaries", "node", "workspace", "node_modules", ".bin", "eigenflux*"),
        os.path.join(home, "AppData", "Roaming", "npm", "eigenflux*"),
        os.path.join(home, ".npm", "eigenflux*"),
        os.path.join(home, ".eigenflux-workbuddy", "*.exe"),
    ]
    for c in candidates:
        hits = glob.glob(c)
        if hits:
            return hits[0]
    raise FileNotFoundError("eigenflux CLI not found on PATH or common locations")


def _resolve_cli():
    """延迟解析 CLI 路径：import 时不触发，避免无 CLI 环境（mock 模式）崩溃。"""
    if os.environ.get("EIGENFLUX_CLI"):
        return os.environ["EIGENFLUX_CLI"]
    return _find_cli()


def send_message(receiver_id, content, timeout=60):
    """经 EigenFlux 给子 agent 发消息。返回 CLI 原始输出（stdout+stderr 合并，因 JSON 在 stderr）。"""
    cli = _resolve_cli()
    r = subprocess.run(
        [cli, "msg", "send", "--receiver-id", receiver_id, "--content", content],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"eigenflux send failed (rc={r.returncode}): {r.stdout.strip()}")
    return r.stdout.strip()


def send_message_json(receiver_id, content, timeout=60):
    """经 EigenFlux 给子 agent 发消息，返回解析后的 dict（含 conv_id / msg_id）。

    CLI 输出形如 'Message sent\\n{...}'，JSON 跨多行，需整体提取首尾 {} 解析。
    """
    out = send_message(receiver_id, content, timeout=timeout)
    s = out.find("{")
    e = out.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(out[s:e + 1])
        except json.JSONDecodeError:
            pass
    return {"raw": out}


def get_history(conv_id, limit=20, timeout=60):
    """读某会话的 EigenFlux 历史。返回 CLI 原始文本（stdout+stderr 合并，因 JSON 在 stderr）。"""
    cli = _resolve_cli()
    r = subprocess.run(
        [cli, "msg", "history", "--conv-id", conv_id, "--limit", str(limit)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"eigenflux history failed (rc={r.returncode}): {r.stdout.strip()}")
    return r.stdout


def parse_history(text):
    """尽力把 EigenFlux CLI 文本输出解析为 [{role, text}] 列表。

    各 agent 回报格式不固定，这里只做最小结构化：保留原文行。
    真实 bridge 里应由各子 agent 约定回报格式（如首行 STATE:working）。
    """
    msgs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        msgs.append({"raw": line})
    return msgs


if __name__ == "__main__":
    try:
        print("eigenflux CLI:", _resolve_cli())
    except FileNotFoundError as e:
        print("eigenflux CLI 不可用:", e)
