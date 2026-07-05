from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import webview


def _runtime_base() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _project_root() -> Path:
    for base in [
        Path(os.environ.get("ORBIT_PROJECT_ROOT", "")),
        Path.cwd(),
        Path(sys.executable).resolve().parent,
        Path(__file__).resolve().parents[1],
    ]:
        if not str(base):
            continue
        for path in (base, *base.parents):
            if (path / "main.py").is_file() and (path / "modules").is_dir():
                return path
    return Path(__file__).resolve().parents[1]


ROOT = _project_root()


def _find_python() -> str:
    if not getattr(sys, "frozen", False):
        return sys.executable
    python39 = r"C:\Users\Windows11\AppData\Local\Programs\Python\Python39\python.exe"
    if os.path.isfile(python39):
        return python39
    return shutil.which("pythonw") or shutil.which("python") or "python"


PYTHON = _find_python()


@dataclass
class ModuleSpec:
    key: str
    title: str
    subtitle: str
    port: int
    url: str
    health_url: str
    command: list[str]
    quick_links: list[tuple[str, str]] = field(default_factory=list)
    process: subprocess.Popen | None = None
    logs: list[str] = field(default_factory=list)


MODULES = [
    ModuleSpec(
        "os",
        "Orbit OS 总控台",
        "商品目录、结算、选品、分析与营销",
        8765,
        "http://127.0.0.1:8765/",
        "http://127.0.0.1:8765/api/health",
        [PYTHON, str(ROOT / "main.py"), "serve", "--port", "8765", "--no-browser"],
        [
            ("首页", "http://127.0.0.1:8765/"),
            ("商品", "http://127.0.0.1:8765/catalog"),
            ("结算", "http://127.0.0.1:8765/settlement"),
            ("选品", "http://127.0.0.1:8765/sourcing"),
        ],
    ),
    ModuleSpec(
        "treasury",
        "Orbit Treasury",
        "独立新品上架与审核",
        8766,
        "http://127.0.0.1:8766/",
        "http://127.0.0.1:8766/health",
        [PYTHON, str(ROOT / "scripts" / "start_new_product_server.py"), "8766"],
        [("工作台", "http://127.0.0.1:8766/")],
    ),
    ModuleSpec(
        "rus",
        "Orbit Rus",
        "俄罗斯与 Ozon 运营",
        8767,
        "http://127.0.0.1:8767/",
        "http://127.0.0.1:8767/health",
        [PYTHON, str(ROOT / "scripts" / "start_rus_server.py"), "8767"],
        [("俄罗斯台", "http://127.0.0.1:8767/")],
    ),
    ModuleSpec(
        "bot",
        "飞书 Bot",
        "飞书消息响应 @OrbitHive",
        0,
        "",
        "",
        [PYTHON, str(ROOT / "main.py"), "feishu", "bot"],
    ),
]

HTML_PATH = _runtime_base() / "desktop" / "orbit_desktop.html"
HTML = HTML_PATH.read_text(encoding="utf-8")


class OrbitDesktopBridge:
    def __init__(self, modules: list[ModuleSpec]) -> None:
        self.modules = {m.key: m for m in modules}
        self.lock = threading.Lock()
        self._window = None
        self._stop_event = threading.Event()
        self._health_cache = {
            key: {"healthy": False, "health_detail": "pending", "running": False}
            for key in self.modules
        }
        self._bot_check_cache: bool | None = None
        self._bot_check_ts = 0.0

    def set_window(self, window) -> None:
        self._window = window

    def _append_log(self, spec: ModuleSpec, line: str) -> None:
        with self.lock:
            spec.logs.append(line.rstrip())
            spec.logs = spec.logs[-400:]

    def _fetch_health(self, url: str) -> tuple[bool, str]:
        if not url:
            return False, "no-url"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OrbitDesktop/2.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return True, json.dumps(payload, ensure_ascii=False)
        except urllib.error.URLError as exc:
            return False, str(exc.reason or exc)
        except Exception:
            return False, "unreachable"

    def _powershell_creationflags(self) -> int:
        return subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0

    def _list_external_bot_processes(self) -> list[dict[str, str]]:
        if sys.platform != "win32":
            return []
        script = (
            "$rows = Get-CimInstance Win32_Process | Where-Object { "
            "($_.Name -match '^pythonw?\\.exe$') -and "
            "($_.CommandLine -match 'main\\.py\\s+feishu\\s+bot') "
            "} | Select-Object ProcessId, CommandLine; "
            "if ($rows) { $rows | ConvertTo-Json -Compress }"
        )
        try:
            raw = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                timeout=5,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self._powershell_creationflags(),
            ).strip()
        except Exception as exc:
            self._append_log(self.modules["bot"], f"[detect err] {exc}")
            return []
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except Exception as exc:
            self._append_log(self.modules["bot"], f"[detect err] invalid json: {exc}")
            return []
        rows = payload if isinstance(payload, list) else [payload]
        result: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("ProcessId") or "").strip()
            cmd = str(row.get("CommandLine") or "").strip()
            if pid:
                result.append({"pid": pid, "command": cmd})
        return result

    def _check_external_bot(self) -> bool:
        if sys.platform != "win32":
            return False
        now = time.time()
        if self._bot_check_cache is not None and (now - self._bot_check_ts) < 8.0:
            return self._bot_check_cache
        result = bool(self._list_external_bot_processes())
        self._bot_check_cache = result
        self._bot_check_ts = now
        return result

    def _refresh_health(self) -> None:
        snapshot: dict[str, dict[str, object]] = {}
        for spec in self.modules.values():
            healthy, detail = self._fetch_health(spec.health_url)
            running = spec.process is not None and spec.process.poll() is None
            if spec.key == "bot" and not running:
                running = self._check_external_bot()
                if running:
                    with self.lock:
                        if not any("external bot detected" in line for line in spec.logs[-5:]):
                            spec.logs.append("[info] external bot detected (not started by this desktop)")
                            spec.logs = spec.logs[-400:]
            snapshot[spec.key] = {
                "healthy": healthy,
                "health_detail": detail,
                "running": running,
            }
        with self.lock:
            self._health_cache.update(snapshot)

    def _build_payload(self) -> dict:
        with self.lock:
            cache = dict(self._health_cache)
            output = []
            for spec in self.modules.values():
                item = cache.get(spec.key, {})
                output.append(
                    {
                        "key": spec.key,
                        "title": spec.title,
                        "subtitle": spec.subtitle,
                        "port": spec.port,
                        "url": spec.url,
                        "healthy": bool(item.get("healthy", False)),
                        "running": bool(item.get("running", False)),
                        "quick_links": [{"label": label, "url": url} for label, url in spec.quick_links],
                        "logs": list(spec.logs[-120:]),
                    }
                )
        return {"modules": output, "ts": time.time()}

    def _push(self) -> None:
        if not self._window:
            return
        try:
            payload = self._build_payload()
            self._window.evaluate_js("window.__update(" + json.dumps(payload, ensure_ascii=False) + ");")
        except Exception as exc:
            print("[push err]", exc, flush=True)

    def _drain_actions(self) -> None:
        if not self._window:
            return
        try:
            raw = self._window.evaluate_js("window.__getAction()")
            if raw and isinstance(raw, str) and raw.strip():
                self._handle_action(json.loads(raw))
        except Exception:
            pass

    def _handle_action(self, action: dict) -> None:
        kind = action.get("action", "")
        key = action.get("key", "")
        print(f"[action] {kind} key={key}", flush=True)
        if kind == "start_module" and key in self.modules:
            spec = self.modules[key]
            self._append_log(spec, "[action] start requested")
            self._start(spec)
            return
        if kind == "stop_module" and key in self.modules:
            spec = self.modules[key]
            self._append_log(spec, "[action] stop requested")
            self._stop_module_any(spec)
            return
        if kind == "restart_module" and key in self.modules:
            spec = self.modules[key]
            self._append_log(spec, f"[restart] {spec.title}")
            self._stop(spec)
            time.sleep(0.25)
            self._start(spec)
            return
        if kind == "start_all":
            for spec in self.modules.values():
                self._start(spec)
            return
        if kind == "stop_all":
            for spec in self.modules.values():
                self._stop_module_any(spec)
            return
        if kind == "open_external" and key in self.modules:
            spec = self.modules[key]
            if spec.url:
                webbrowser.open(spec.url)

    def _stream(self, spec: ModuleSpec) -> None:
        proc = spec.process
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            self._append_log(spec, line)
        spec.process = None
        self._append_log(spec, "[exit] code=" + str(proc.wait()))

    def _start(self, spec: ModuleSpec) -> None:
        if spec.process and spec.process.poll() is None:
            self._append_log(spec, f"[info] {spec.title} already running")
            return
        env = os.environ.copy()
        if spec.key == "bot":
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env.pop(key, None)
            env["NO_PROXY"] = "*"
            env["no_proxy"] = "*"
        spec.process = subprocess.Popen(
            spec.command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=self._powershell_creationflags(),
        )
        self._append_log(spec, "[start] " + " ".join(spec.command))
        threading.Thread(target=self._stream, args=(spec,), daemon=True).start()

    def _stop(self, spec: ModuleSpec) -> None:
        proc = spec.process
        if not proc or proc.poll() is not None:
            spec.process = None
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        spec.process = None
        self._append_log(spec, "[stop] " + spec.title)

    def _stop_external_bot(self) -> list[str]:
        if sys.platform != "win32":
            return []
        killed: list[str] = []
        for row in self._list_external_bot_processes():
            pid = row["pid"]
            try:
                subprocess.run(
                    ["TASKKILL", "/F", "/PID", pid],
                    capture_output=True,
                    timeout=5,
                    creationflags=self._powershell_creationflags(),
                    check=False,
                )
                killed.append(pid)
                self._append_log(self.modules["bot"], f"[stop] TASKKILL PID={pid}")
            except Exception as exc:
                self._append_log(self.modules["bot"], f"[stop err] PID={pid}: {exc}")
        self._bot_check_cache = False
        self._bot_check_ts = time.time()
        return killed

    def _stop_module_any(self, spec: ModuleSpec) -> bool:
        if spec.process and spec.process.poll() is None:
            self._stop(spec)
            return True
        if spec.key == "bot":
            killed = self._stop_external_bot()
            message = f"[stop] external bot stopped (PID: {', '.join(killed)})" if killed else "[stop] external bot not found"
            self._append_log(spec, message)
            return bool(killed)
        self._append_log(spec, "[info] module not running")
        return False

    def auto_start(self) -> None:
        spec = self.modules.get("os")
        if spec and (not spec.process or spec.process.poll() is not None):
            self._start(spec)


def main() -> int:
    bridge = OrbitDesktopBridge(MODULES)
    bridge.auto_start()

    window = webview.create_window(
        "Orbit Desktop",
        html=HTML,
        width=1440,
        height=920,
        min_size=(1180, 760),
    )
    bridge.set_window(window)

    def background_loop() -> None:
        time.sleep(1.5)
        while not bridge._stop_event.is_set():
            bridge._refresh_health()
            bridge._push()
            bridge._drain_actions()
            for _ in range(20):
                if bridge._stop_event.is_set():
                    return
                time.sleep(0.1)

    threading.Thread(target=background_loop, daemon=True).start()
    webview.start(debug=False)
    bridge._stop_event.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
