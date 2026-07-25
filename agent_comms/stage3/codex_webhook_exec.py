#!/usr/bin/env python3
# codex_webhook_exec.py — 方案②：webhook 直接无头唤醒 codex exec（零定时器 / 真事件驱动）
#
# 机制：常驻 HTTP 服务监听 127.0.0.1:8774。
#   POST /exec  {task_id, title, prompt}
#     -> 1) 把任务落盘 codex_inbox/<task_id>.json (status=ACKED_LOCAL_INBOX)
#        2) 标 RUNNING_BY_WEBHOOK (防 Stop 钩子方案①双跑)
#        3) codex exec --sandbox workspace-write "<受控prompt>" 无头拉起 Codex 执行
#     Codex 自己读 inbox 文件、真实改代码/跑命令/git，完成后 --report / --complete 回报。
# 与方案①(Stop钩子)关系：本监听器先标 RUNNING，Stop 钩子扫描跳过 RUNNING，故两路并存也不双跑。

import http.server, json, subprocess, threading, os, datetime, logging, sys, shutil


def _detect_codex():
    # Windows 原生 CreateProcess 找不到无扩展名的 sh 脚本 `codex`，
    # 必须用官方 Windows 入口 codex.cmd（或 codex.ps1）。
    # 优先用 Windows 原生绝对路径，避免 Git Bash 的 MSYS 路径 /c/... 在 CreateProcess 下找不到。
    _hard = [
        r"C:\Users\Windows11\.workbuddy\binaries\node\versions\22.22.2\codex.cmd",
        r"C:\Users\Windows11\.workbuddy\binaries\node\versions\22.22.2\codex.ps1",
    ]
    for c in _hard:
        if os.path.exists(c):
            return c
    # fallback: PATH 探测，并把 MSYS 路径 /c/... 转成 C:\...
    for c in ("codex.cmd", "codex.ps1", "codex"):
        p = shutil.which(c)
        if p:
            if p.startswith("/") and len(p) > 2 and p[2] == "/":
                p = p[1].upper() + ":\\" + p[3:].replace("/", "\\")
            return p
    return "codex"


CODEX_BIN = _detect_codex()

import shlex


def _detect_bash():
    # 通过 Git Bash 运行 codex，能正确解析无扩展名入口与 PATH（已验证 codex --version/exec 可用）
    for b in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe"):
        if os.path.exists(b):
            return b
    return shutil.which("bash") or "bash"


BASH_BIN = _detect_bash()

REPO = r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm"
PORT = 8774
INBOX = os.path.join(REPO, "agent_comms", "stage3", "codex_inbox")
LOG_PATH = os.path.join(REPO, "agent_comms", "stage3", "codex_webhook_exec.log")

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("webhook-exec")


def ensure_inbox(task_id, title, prompt):
    os.makedirs(INBOX, exist_ok=True)
    p = os.path.join(INBOX, f"{task_id}.json")
    if not os.path.exists(p):
        d = {
            "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "agent": "Orbit Codex",
            "dispatch": {
                "task_id": task_id,
                "title": title or task_id,
                "prompt": prompt,
                "assignee": "Orbit Codex",
                "mode": "direct",
            },
            "status": "ACKED_LOCAL_INBOX",
        }
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        log.info("wrote inbox %s", task_id)


def mark_running(task_id):
    p = os.path.join(INBOX, f"{task_id}.json")
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            d["status"] = "RUNNING_BY_WEBHOOK"
            d["claimed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            d["claimed_by"] = "webhook-exec"
            json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            log.info("marked %s RUNNING_BY_WEBHOOK", task_id)
        except Exception as e:
            log.warning("mark_running failed %s: %s", task_id, e)


def run_codex(task_id, prompt):
    log.info("EXEC start task=%s", task_id)
    ensure_inbox(task_id, None, prompt)
    mark_running(task_id)
    # 受控 prompt：task_id 由 CEO 生成；任务正文来自 inbox 文件（受控，非外部不可信）
    # 受控 prompt：只放模板 + 任务文件路径（任务正文在 inbox 文件的 dispatch.prompt，由 CEO 生成，防注入）
    full = (
        f"请读取文件 agent_comms/stage3/codex_inbox/{task_id}.json 中的 dispatch.prompt 字段，"
        f"那就是你要执行的任务要求。在仓库 {REPO} 中真实执行（改代码/跑命令/git 等）。"
        f"完成后按 dispatch.prompt 里的回报指令运行 codex_adapter.py 的 --report 与 --complete。"
        f"绝对不要伪造 DONE / commit hash / 测试通过。若无法完成，运行 --report 写明 BLOCKED 及原因。"
    )
    # danger-full-access：生图任务需经宿主代理 127.0.0.1:10808 出网调 toapis，
    # workspace-write 沙箱无网络出口，故放行网络（Boss 2026-07-22 要求 agent 亲自生图）。
    inner = "codex exec --sandbox danger-full-access " + shlex.quote(full)
    cmd = [BASH_BIN, "-lc", inner]
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=1800)
        log.info("EXEC done task=%s rc=%s", task_id, r.returncode)
        log.info("stdout:%s", r.stdout[-3000:])
        log.info("stderr:%s", r.stderr[-2000:])
    except Exception as e:
        log.error("EXEC error task=%s: %s", task_id, e)


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/exec":
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                self.send_response(400); self.end_headers(); self.wfile.write(str(e).encode()); return
            task_id = body.get("task_id"); prompt = body.get("prompt"); title = body.get("title")
            if not task_id or not prompt:
                self.send_response(400); self.end_headers(); self.wfile.write(b"missing task_id or prompt"); return
            threading.Thread(target=run_codex, args=(task_id, prompt), daemon=True).start()
            self.send_response(202)
            self.end_headers()
            self.wfile.write(json.dumps({"accepted": True, "task_id": task_id}).encode())
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
    log.info("webhook-exec listening on %s (codex=%s bash=%s)", PORT, CODEX_BIN, BASH_BIN)
    print(f"[webhook-exec] listening on http://127.0.0.1:{PORT}/exec", flush=True)
    srv.serve_forever()
