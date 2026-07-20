# -*- coding: utf-8 -*-
"""
Stage3 常驻启动器
================
把 Orchestrator + adapter_runner + cursor_adapter + codex_adapter
以 DETACHED 进程拉起，使其脱离本会话存活（会话结束不退出）。

用法：
    python start_stage3.py           # 启动（已全运行则跳过）
    python start_stage3.py --force   # 先按 pidfile 回收，再启动
    python start_stage3.py --status  # 查看存活状态
    python stop_stage3.py            # 停止全部（按 pidfile）

实现要点：
    - 子进程统一用 a2a_poc 的 venv python（自带 aiohttp / sseclient）。
    - 启动前检测 8773 端口是否空闲：被占说明有外部陈旧 Orchestrator，
      直接报错并给出清理命令，避免产生“僵尸 Orchestrator”。
    - 仅依赖 tasklist / taskkill / socket（不调用 powershell/wmic，沙箱更安全）。
    - 日志落在 logs/<name>.log。
"""
import os
import sys
import time
import json
import socket
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))          # tiktok_e_comm
VENV = os.path.join(HERE, "..", "a2a_poc", "venv", "Scripts", "python.exe")
PIDFILE = os.path.join(HERE, ".stage3_pids.json")
LOGS = os.path.join(HERE, "logs")
ORCH_HEALTH = "http://127.0.0.1:8773/health"

PROCS = [
    ("orchestrator", "orchestrator_service.py"),
    ("adapter_runner", "adapter_runner.py"),
    ("cursor_adapter", "cursor_adapter.py"),
    ("codex_adapter", "codex_adapter.py"),
]

# Windows：DETACHED_PROCESS，使子进程脱离控制台/会话存活
DETACHED = 0x00000008


def _alive(pid):
    """tasklist 输出为 GBK，故用 gbk 解码；返回该 pid 是否存活。"""
    if not pid:
        return False
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid],
            capture_output=True, encoding="gbk", errors="ignore", timeout=10,
        )
        return str(pid) in r.stdout
    except Exception:
        return False


def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _stop_all():
    if not os.path.exists(PIDFILE):
        print("  (无可回收的 pid 记录)")
        return
    try:
        data = json.load(open(PIDFILE, encoding="utf-8"))
    except Exception:
        data = {}
    for name, pid in data.items():
        if _alive(pid):
            # /T 杀掉整个进程树（uvicorn 会 spawn 一个 worker 子进程持有端口）
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
            print("  stopped %-16s pid=%s (tree)" % (name, pid))
    try:
        os.remove(PIDFILE)
    except Exception:
        pass


def status():
    print("=== Stage3 进程存活 (pidfile) ===")
    data = {}
    if os.path.exists(PIDFILE):
        try:
            data = json.load(open(PIDFILE, encoding="utf-8"))
        except Exception:
            data = {}
    for name, _ in PROCS:
        pid = data.get(name)
        if pid and _alive(pid):
            print("  %-16s -> OK (pid %s)" % (name, pid))
        else:
            print("  %-16s -> 未运行" % name)
    try:
        import urllib.request
        with urllib.request.urlopen(ORCH_HEALTH, timeout=3) as r:
            print("  orchestrator health:", r.read().decode().strip())
    except Exception:
        print("  orchestrator health: 不可达")


def start(force=False):
    if force:
        print("[stop] 按 pidfile 回收进程 ...")
        _stop_all()
        time.sleep(1)

    # 已全运行则跳过
    data = {}
    if os.path.exists(PIDFILE):
        try:
            data = json.load(open(PIDFILE, encoding="utf-8"))
        except Exception:
            data = {}
    if not force and len(data) == len(PROCS) and all(_alive(data.get(n)) for n, _ in PROCS):
        print("[start] 4 个进程均已在运行，跳过启动。")
        status()
        return

    # 端口预检：8773 被占 = 外部陈旧 Orchestrator
    if not _port_free(8773):
        print("  ERROR: 端口 8773 已被占用（可能存在外部陈旧的 Orchestrator）。")
        print("         请先清理：在 PowerShell 运行：")
        print("         Get-CimInstance Win32_Process -Filter \"Name='python.exe'\""
              " | Where-Object { $_.CommandLine -match 'stage3' }"
              " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        sys.exit(1)

    if not os.path.exists(VENV):
        print("  ERROR: venv python 不存在: %s" % VENV)
        sys.exit(1)

    os.makedirs(LOGS, exist_ok=True)
    pids = {}
    for name, script in PROCS:
        logf = open(os.path.join(LOGS, name + ".log"), "ab", buffering=0)
        p = subprocess.Popen(
            [VENV, "-u", os.path.join(HERE, script)],
            cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
            creationflags=DETACHED,
        )
        pids[name] = p.pid
        print("  started %-16s pid=%d -> logs/%s.log" % (name, p.pid, name))
    json.dump(pids, open(PIDFILE, "w"), indent=2)
    time.sleep(2.5)
    status()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg in ("--stop", "stop"):
        print("[stop] 回收 Stage3 进程 ...")
        _stop_all()
        print("[done]")
    elif arg in ("--status", "status"):
        status()
    elif arg in ("--force", "-f"):
        start(force=True)
    else:
        start(force=False)
