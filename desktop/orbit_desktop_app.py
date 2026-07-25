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
from pathlib import Path
from tkinter import BOTH, LEFT, RIGHT, TOP, X, Y, END, Tk, StringVar
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

def _project_root() -> Path:
    candidates = [
        Path(os.environ.get("ORBIT_PROJECT_ROOT", "")),
        Path.cwd(),
        Path(sys.executable).resolve().parent,
        Path(__file__).resolve().parents[1],
    ]
    for base in candidates:
        if not str(base):
            continue
        for path in (base, *base.parents):
            if (path / "main.py").is_file() and (path / "modules").is_dir():
                return path
    return Path(__file__).resolve().parents[1]


ROOT = _project_root()
PYTHON = sys.executable if not getattr(sys, "frozen", False) else (shutil.which("pythonw") or shutil.which("python") or "python")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared_platform.orbit_registry import ModuleSpec, NAVIGATION, build_module_specs


MODULES = build_module_specs(ROOT, PYTHON)


class OrbitDesktopApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title("Orbit CEO Desktop")
        self.root.geometry("1080x760")
        self.root.minsize(980, 680)

        self.status_vars: dict[str, StringVar] = {}
        self.log_widgets: dict[str, ScrolledText] = {}
        self.meta_vars: dict[str, StringVar] = {}
        self.btn_sets: dict[str, dict[str, ttk.Button]] = {}

        self._build_ui()
        self._schedule_refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        shell = ttk.Frame(self.root, padding=16)
        shell.pack(fill=BOTH, expand=True)

        header = ttk.Frame(shell)
        header.pack(fill=X)
        ttk.Label(header, text="Orbit CEO Desktop", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="公司运营总览与五个业务域入口；技术进程统一收纳在系统与服务。",
        ).pack(anchor="w", pady=(4, 12))

        business = ttk.LabelFrame(shell, text="业务导航", padding=10)
        business.pack(fill=X, pady=(0, 12))
        for item in (item for item in NAVIGATION if item.level == "primary"):
            url = f"http://127.0.0.1:8765{item.href}"
            ttk.Button(business, text=item.label, command=lambda u=url: webbrowser.open(u)).pack(
                side=LEFT, padx=(0, 8)
            )

        actions = ttk.LabelFrame(shell, text="系统与服务", padding=10)
        actions.pack(fill=X, pady=(0, 12))
        ttk.Button(actions, text="全部启动", command=self.start_all).pack(side=LEFT)
        ttk.Button(actions, text="全部停止", command=self.stop_all).pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="刷新状态", command=self.refresh_all_now).pack(side=LEFT, padx=(8, 0))

        notebook = ttk.Notebook(shell)
        notebook.pack(fill=BOTH, expand=True)

        for spec in MODULES:
            tab = ttk.Frame(notebook, padding=14)
            notebook.add(tab, text=spec.title)
            self._build_module_tab(tab, spec)

    def _build_module_tab(self, tab: ttk.Frame, spec: ModuleSpec) -> None:
        top = ttk.Frame(tab)
        top.pack(fill=X)

        title_wrap = ttk.Frame(top)
        title_wrap.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(title_wrap, text=spec.title, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(title_wrap, text=spec.subtitle).pack(anchor="w", pady=(4, 8))

        right = ttk.Frame(top)
        right.pack(side=RIGHT)
        ttk.Button(right, text="打开", command=lambda s=spec: webbrowser.open(s.url)).pack(side=LEFT)
        ttk.Button(right, text="启动", command=lambda s=spec: self.start_module(s)).pack(side=LEFT, padx=(8, 0))
        ttk.Button(right, text="重启", command=lambda s=spec: self.restart_module(s)).pack(side=LEFT, padx=(8, 0))
        ttk.Button(right, text="停止", command=lambda s=spec: self.stop_module(s)).pack(side=LEFT, padx=(8, 0))

        status = StringVar(value="检查中...")
        meta = StringVar(value=f"Port {spec.port} · {spec.url}")
        self.status_vars[spec.key] = status
        self.meta_vars[spec.key] = meta

        ttk.Label(tab, textvariable=status).pack(anchor="w", pady=(8, 2))
        ttk.Label(tab, textvariable=meta).pack(anchor="w", pady=(0, 10))

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill=X, pady=(0, 10))
        btns: dict[str, ttk.Button] = {}
        for idx, (label, url) in enumerate(spec.quick_links):
            key = f"link_{idx}"
            btns[key] = ttk.Button(btn_row, text=label, command=lambda u=url: webbrowser.open(u))
            btns[key].pack(side=LEFT, padx=(8 if idx else 0, 0))
        self.btn_sets[spec.key] = btns

        ttk.Label(tab, text="运行日志").pack(anchor="w")
        log = ScrolledText(tab, height=22, wrap="word")
        log.pack(fill=BOTH, expand=True)
        log.insert("1.0", f"{spec.title} 已就绪。\n命令: {' '.join(spec.command)}\n")
        log.configure(state="disabled")
        self.log_widgets[spec.key] = log

    def append_log(self, key: str, line: str) -> None:
        widget = self.log_widgets[key]
        widget.configure(state="normal")
        widget.insert(END, line.rstrip() + "\n")
        widget.see(END)
        widget.configure(state="disabled")

    def start_module(self, spec: ModuleSpec) -> None:
        if spec.process and spec.process.poll() is None:
            self.append_log(spec.key, f"[info] {spec.title} 已在运行。")
            return
        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        spec.process = subprocess.Popen(
            spec.command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        self.append_log(spec.key, f"[start] {' '.join(spec.command)}")
        threading.Thread(target=self._stream_logs, args=(spec,), daemon=True).start()
        self.refresh_all_now()

    def stop_module(self, spec: ModuleSpec) -> None:
        proc = spec.process
        if not proc or proc.poll() is not None:
            self.append_log(spec.key, f"[info] {spec.title} 当前未运行。")
            spec.process = None
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        self.append_log(spec.key, f"[stop] {spec.title} 已停止。")
        spec.process = None
        self.refresh_all_now()

    def restart_module(self, spec: ModuleSpec) -> None:
        self.append_log(spec.key, f"[restart] {spec.title}")
        self.stop_module(spec)
        time.sleep(0.5)
        self.start_module(spec)

    def _stream_logs(self, spec: ModuleSpec) -> None:
        proc = spec.process
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            self.root.after(0, self.append_log, spec.key, line)
        code = proc.wait()
        self.root.after(0, self.append_log, spec.key, f"[exit] code={code}")
        spec.process = None
        self.root.after(0, self.refresh_all_now)

    def start_all(self) -> None:
        for spec in MODULES:
            self.start_module(spec)

    def stop_all(self) -> None:
        for spec in MODULES:
            self.stop_module(spec)

    def _fetch_health(self, url: str) -> tuple[bool, str]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OrbitDesktop/1.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return True, json.dumps(payload, ensure_ascii=False)
        except urllib.error.URLError as exc:
            return False, str(exc.reason or exc)
        except Exception as exc:
            return False, str(exc)

    def refresh_all_now(self) -> None:
        for spec in MODULES:
            ok, detail = self._fetch_health(spec.health_url)
            running = spec.process is not None and spec.process.poll() is None
            state = "running" if running else "stopped"
            if ok:
                self.status_vars[spec.key].set(f"Online · {spec.url}")
            else:
                self.status_vars[spec.key].set(f"Offline · process {state} · {detail}")
            self.meta_vars[spec.key].set(f"Port {spec.port} · {spec.url} · health: {spec.health_url}")

    def _schedule_refresh(self) -> None:
        self.refresh_all_now()
        self.root.after(3000, self._schedule_refresh)

    def _on_close(self) -> None:
        self.stop_all()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    OrbitDesktopApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
