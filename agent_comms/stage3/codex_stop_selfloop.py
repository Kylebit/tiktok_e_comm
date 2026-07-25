#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Codex Stop-hook 自驱链 (Orbit Hive / CEO 肉肉) — 已按官方规范加固

机制（已官方文档确认）：
  Codex 桌面 App 会从用户级 ~/.codex/hooks.json 发现生命周期 hooks；Stop 是
  turn-scoped 事件（非 CLI 专属）。Stop 钩子返回 {"decision":"block","reason":"..."}
  时，Codex 不会把本轮当失败，而是自动把 reason 当作下一条用户消息续跑该会话。
  stop_hook_active==true 表示本 turn 已被 Stop 钩子续跑过。

安全规范（来自 Codex 官方建议）：
  - reason 只放「受控模板 + 任务 JSON 绝对路径」，不直接拼接不可信任务正文（防注入）。
  - 无待办返回 {"continue":true} 让会话正常结束。
  - stop_hook_active==true 时默认不再续跑，除非设置小的链路预算（本脚本=每 burst 最多 5 个）。
  - 原子地把任务从 pending 标记/移动为 running(CLAIMED) 再返回 decision:block。
  - 任务最终必须写 DONE/BLOCKED 并移出 inbox；异常也要释放/标记，避免同一任务无限续跑。
  - hook 只做「选下一个任务 + 状态迁移」，真实代码工作交给续跑后的 Codex turn。

注意：本脚本不执行任何代码改动，只负责选任务 + 标记 + 注入 + 结束/归档。
"""
import sys
import os
import json
import glob
import time

BASE = r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\agent_comms\stage3"
INBOX = os.path.join(BASE, "codex_inbox")
ARCHIVE = os.path.join(INBOX, "archive")
STATE = os.path.join(BASE, "codex_stop_chain_state.json")  # 放 stage3，避免被 inbox glob 误扫
STALE_SEC = 30 * 60          # 超过 30 分钟仍 CLAIMED 视为卡死，允许重新认领
CHAIN_BUDGET = 5             # 每轮 burst 最多续跑任务数（防无限链）
DONE_STATES = {"DONE", "BLOCKED", "CANCELED", "FAILED"}


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _archive_terminal():
    """把终态任务移出 inbox（规范要求），保持 inbox 干净。"""
    try:
        if not os.path.isdir(ARCHIVE):
            os.makedirs(ARCHIVE, exist_ok=True)
        for path in glob.glob(os.path.join(INBOX, "*.json")):
            data = _read(path)
            if isinstance(data, dict) and data.get("status") in DONE_STATES:
                try:
                    os.replace(path, os.path.join(ARCHIVE, os.path.basename(path)))
                except Exception:
                    pass
    except Exception:
        pass


def _load_state():
    d = _read(STATE)
    if not isinstance(d, dict):
        d = {}
    return {"count": int(d.get("count", 0)), "burst": d.get("burst", "")}


def _save_state(count, burst):
    _write(STATE, {"count": count, "burst": burst})


def main():
    raw = sys.stdin.read()
    try:
        evt = json.loads(raw) if raw.strip() else {}
    except Exception:
        evt = {}

    stop_active = bool(evt.get("stop_hook_active", False))

    # 作用域限定：只在 tiktok_e_comm worker 会话(cwd 在仓库内)生效，
    # 避免其它 Codex 会话(私人聊天等)误抓 Orbit Codex 的任务。
    REPO = r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm".lower()
    cwd = (evt.get("cwd") or "").replace("/", "\\").lower()
    if REPO not in cwd:
        print(json.dumps({"continue": True}))
        return

    # 先把已终态任务移出 inbox
    _archive_terminal()

    # 收集候选任务
    candidates = []
    for path in glob.glob(os.path.join(INBOX, "*.json")):
        data = _read(path)
        if not isinstance(data, dict):
            continue
        st = data.get("status", "")
        if st in DONE_STATES:
            continue
        if st.startswith("RUNNING"):   # 已由方案②(webhook exec)认领，避免双跑
            continue
        assignee = data.get("dispatch", {}).get("assignee", "")
        if assignee and assignee != "Orbit Codex":
            continue
        candidates.append((path, data))

    if not candidates:
        # 无待办：正常结束本轮；重置链路计数
        print(json.dumps({"continue": True}))
        _save_state(0, "")
        return

    # 链路预算：新 burst（stop_active=False）从 0 起；已续跑中沿用计数
    st = _load_state()
    if not stop_active:
        count = 0
        burst = str(int(time.time()))
    else:
        count = st.get("count", 0)
        burst = st.get("burst", str(int(time.time())))

    if count >= CHAIN_BUDGET:
        # 预算耗尽：结束本轮，等下次踢动开新 burst
        print(json.dumps({"continue": True}))
        _save_state(0, "")
        return

    # 选最老 pending 且非进行中（防同一任务重跑）
    now = time.time()
    for path, data in sorted(candidates, key=lambda t: t[1].get("received_at", "")):
        stt = data.get("status")
        claimed = data.get("claimed_at")
        if stt == "CLAIMED_BY_STOPHOOK" and claimed:
            try:
                if now - float(claimed) < STALE_SEC:
                    continue
            except Exception:
                pass

        prompt = data.get("dispatch", {}).get("prompt", "")
        tid = data.get("dispatch", {}).get("task_id", "")
        if not prompt:
            data["status"] = "DONE"
            _write(path, data)
            continue

        # 原子认领
        data["status"] = "CLAIMED_BY_STOPHOOK"
        data["claimed_at"] = now
        data["claimed_by"] = "stop_hook"
        _write(path, data)

        # reason 只放受控模板 + 任务 JSON 绝对路径（不直接拼任务正文，防注入）
        reason = (
            f"[Orbit Codex 自驱·Stop钩子] 执行任务 {tid}。\n"
            f"请读取以下文件的 dispatch.prompt 并严格按照它真实执行"
            f"（改代码/跑命令/git add+commit+push origin/master，禁止伪造 DONE 或 hash）：\n"
            f"文件绝对路径：{os.path.abspath(path)}\n\n"
            f"全部干完后运行：python agent_comms/stage3/codex_adapter.py --complete --task-id {tid}\n"
            f"若只需回报进度：python agent_comms/stage3/codex_adapter.py --report --task-id {tid} --text \"...\" --tool <工具>\n"
            f"本轮只做这一件事，做完即结束，不要自行扩展。"
        )
        _save_state(count + 1, burst)
        print(json.dumps({"decision": "block", "reason": reason}))
        return

    # 所有候选都在进行中/已处理
    print(json.dumps({"continue": True}))
    _save_state(0, "")
    return


if __name__ == "__main__":
    main()
