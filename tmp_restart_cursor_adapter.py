# -*- coding: utf-8 -*-
"""精准重启 cursor_adapter：停旧进程 -> 同 start_stage3 方式 DETACHED 拉起 -> 更新 pidfile。"""
import json, os, subprocess, sys, time

ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)))   # tiktok_e_comm
HERE = os.path.join(ROOT, "agent_comms", "stage3")
VENV = os.path.join(HERE, "..", "a2a_poc", "venv", "Scripts", "python.exe")
PIDFILE = os.path.join(HERE, ".stage3_pids.json")
LOGS = os.path.join(HERE, "logs")
DETACHED = 0x00000008
NO_WINDOW = 0x08000000
CREATE_FLAGS = DETACHED | NO_WINDOW

# venv 不存在则退回当前（托管）python —— cursor_adapter 仅用标准库
if not os.path.exists(VENV):
    VENV = sys.executable
    print(f"[restart] venv 缺失，改用 {VENV}")

# 1) 读 pidfile，停旧 cursor_adapter
pid = None
if os.path.exists(PIDFILE):
    try:
        pids = json.load(open(PIDFILE, encoding="utf-8"))
        pid = pids.get("cursor_adapter")
    except Exception:
        pids = {}
else:
    pids = {}

if pid:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, encoding="gbk", errors="ignore", timeout=10)
        print(f"[restart] killed old cursor_adapter pid={pid}")
    except Exception as e:
        print("[restart] kill 旧进程异常:", e)
    time.sleep(1)

# 2) DETACHED 拉起新进程（同 start_stage3 行为）
os.makedirs(LOGS, exist_ok=True)
logf = open(os.path.join(LOGS, "cursor_adapter.log"), "ab", buffering=0)
p = subprocess.Popen(
    [VENV, "-u", os.path.join(HERE, "cursor_adapter.py")],
    cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
    creationflags=CREATE_FLAGS,
)
new_pid = p.pid
pids["cursor_adapter"] = new_pid
json.dump(pids, open(PIDFILE, "w"), indent=2)
print(f"[restart] started cursor_adapter pid={new_pid} -> logs/cursor_adapter.log")
time.sleep(3)

# 3) 健康检查：看日志是否出现启动 banner
try:
    with open(os.path.join(LOGS, "cursor_adapter.log"), "rb") as f:
        data = f.read()[-1500:]
    txt = data.decode("utf-8", errors="replace")
    print("--- cursor_adapter.log tail ---")
    print(txt[-900:])
except Exception as e:
    print("[restart] 读日志失败:", e)
