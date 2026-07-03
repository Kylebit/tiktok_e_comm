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
        key="os",
        title="Orbit OS 总控台",
        subtitle="商品目录、结算、选品、分析与营销",
        port=8765,
        url="http://127.0.0.1:8765/",
        health_url="http://127.0.0.1:8765/api/health",
        command=[PYTHON, str(ROOT / "main.py"), "serve", "--port", "8765", "--no-browser"],
        quick_links=[
            ("首页", "http://127.0.0.1:8765/"),
            ("商品目录", "http://127.0.0.1:8765/catalog"),
            ("结算中心", "http://127.0.0.1:8765/settlement"),
            ("选品中心", "http://127.0.0.1:8765/sourcing"),
        ],
    ),
    ModuleSpec(
        key="treasury",
        title="Orbit Treasury 新品发布台",
        subtitle="独立新品上架与审核工作台",
        port=8766,
        url="http://127.0.0.1:8766/",
        health_url="http://127.0.0.1:8766/health",
        command=[PYTHON, str(ROOT / "scripts" / "start_new_product_server.py"), "8766"],
        quick_links=[("工作台", "http://127.0.0.1:8766/")],
    ),
    ModuleSpec(
        key="rus",
        title="Orbit Rus 俄罗斯台",
        subtitle="独立俄罗斯与 Ozon 运营台",
        port=8767,
        url="http://127.0.0.1:8767/",
        health_url="http://127.0.0.1:8767/health",
        command=[PYTHON, str(ROOT / "scripts" / "start_rus_server.py"), "8767"],
        quick_links=[("俄罗斯台", "http://127.0.0.1:8767/")],
    ),
]


HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Orbit 桌面台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #0f172a;
      --panel: #111827;
      --panel-2: #ffffff;
      --muted: #94a3b8;
      --line: #e5e7eb;
      --text: #0f172a;
      --accent: #2563eb;
      --accent-soft: #dbeafe;
      --ok: #16a34a;
      --warn: #d97706;
      --bad: #dc2626;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: #e5e7eb;
      color: var(--text);
      height: 100vh;
      overflow: hidden;
    }
    .app {
      display: grid;
      grid-template-columns: 300px 1fr;
      height: 100vh;
    }
    .sidebar {
      background: linear-gradient(180deg, #0f172a 0%, #111827 100%);
      color: #f8fafc;
      display: flex;
      flex-direction: column;
      border-right: 1px solid rgba(255,255,255,.08);
    }
    .brand {
      padding: 22px 20px 16px;
      border-bottom: 1px solid rgba(255,255,255,.08);
    }
    .brand h1 {
      margin: 0;
      font-size: 22px;
    }
    .brand p {
      margin: 6px 0 0;
      color: #94a3b8;
      font-size: 13px;
      line-height: 1.5;
    }
    .global-actions {
      display: flex;
      gap: 8px;
      padding: 14px 20px 10px;
      flex-wrap: wrap;
    }
    .btn {
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 13px;
      cursor: pointer;
      background: #fff;
      color: #111827;
    }
    .btn.primary {
      background: var(--accent);
      color: #fff;
    }
    .btn.secondary {
      background: rgba(255,255,255,.08);
      color: #f8fafc;
      border-color: rgba(255,255,255,.1);
    }
    .btn.ghost {
      background: transparent;
      color: #0f172a;
      border-color: var(--line);
    }
    .modules {
      padding: 8px 12px 18px;
      overflow: auto;
      display: grid;
      gap: 10px;
    }
    .module {
      border: 1px solid rgba(255,255,255,.08);
      border-radius: 12px;
      padding: 14px;
      background: rgba(255,255,255,.03);
      cursor: pointer;
    }
    .module.active {
      background: rgba(37,99,235,.18);
      border-color: rgba(96,165,250,.5);
    }
    .module h2 {
      margin: 0;
      font-size: 16px;
    }
    .module p {
      margin: 6px 0 10px;
      color: #94a3b8;
      font-size: 12px;
      line-height: 1.45;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      font-size: 11px;
      padding: 4px 8px;
      background: rgba(255,255,255,.08);
      color: #e2e8f0;
    }
    .status-pill.ok { background: rgba(22,163,74,.18); color: #bbf7d0; }
    .status-pill.bad { background: rgba(220,38,38,.18); color: #fecaca; }
    .main {
      display: grid;
      grid-template-rows: auto auto 1fr 220px;
      background: #f8fafc;
      min-width: 0;
    }
    .toolbar {
      background: #fff;
      border-bottom: 1px solid var(--line);
      padding: 16px 18px 12px;
    }
    .toolbar h2 {
      margin: 0;
      font-size: 22px;
    }
    .toolbar p {
      margin: 6px 0 0;
      color: #64748b;
      font-size: 13px;
    }
    .toolbar-actions {
      margin-top: 14px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .quick-links {
      background: #fff;
      border-bottom: 1px solid var(--line);
      padding: 12px 18px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .viewer {
      min-height: 0;
      background: #dbe4ee;
    }
    iframe {
      width: 100%;
      height: 100%;
      border: 0;
      background: #fff;
    }
    .logs {
      border-top: 1px solid var(--line);
      background: #0b1220;
      color: #dbeafe;
      padding: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 0;
    }
    .logs-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 14px;
      border-bottom: 1px solid rgba(255,255,255,.08);
    }
    .logs-head strong {
      font-size: 13px;
    }
    .logs-body {
      padding: 12px 14px;
      overflow: auto;
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <h1>Orbit 桌面台</h1>
        <p>把 Orbit OS、Orbit Treasury 和 Orbit Rus 放进同一个轻量桌面壳子里。</p>
      </div>
      <div class="global-actions">
        <button class="btn secondary" onclick="startAll()">全部启动</button>
        <button class="btn secondary" onclick="stopAll()">全部停止</button>
        <button class="btn secondary" onclick="refreshAll()">刷新</button>
      </div>
      <div id="modules" class="modules"></div>
    </aside>
    <section class="main">
      <div class="toolbar">
        <h2 id="moduleTitle">Orbit OS 总控台</h2>
        <p id="moduleSubtitle"></p>
        <div class="toolbar-actions">
          <button class="btn primary" onclick="openInPane()">在内嵌窗口打开</button>
          <button class="btn ghost" onclick="openExternal()">外部浏览器打开</button>
          <button class="btn ghost" onclick="startCurrent()">启动</button>
          <button class="btn ghost" onclick="restartCurrent()">重启</button>
          <button class="btn ghost" onclick="stopCurrent()">停止</button>
        </div>
      </div>
      <div id="quickLinks" class="quick-links"></div>
      <div class="viewer">
        <iframe id="frame" src="about:blank"></iframe>
      </div>
      <div class="logs">
        <div class="logs-head">
          <strong>模块日志</strong>
          <button class="btn secondary" onclick="refreshAll()">刷新状态</button>
        </div>
        <div id="logs" class="logs-body"></div>
      </div>
    </section>
  </div>
  <script>
    let modules = [];
    let current = 'os';

    function escapeHtml(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    async function refreshAll() {
      modules = await window.pywebview.api.list_modules();
      renderModules();
      renderCurrent();
    }

    function renderModules() {
      const wrap = document.getElementById('modules');
      wrap.innerHTML = modules.map(m => {
        const cls = m.key === current ? 'module active' : 'module';
        const pill = m.healthy ? 'status-pill ok' : 'status-pill bad';
        const label = m.healthy ? '在线' : '离线';
        return `
          <div class="${cls}" onclick="selectModule('${m.key}')">
            <h2>${escapeHtml(m.title)}</h2>
            <p>${escapeHtml(m.subtitle)}</p>
            <span class="${pill}">${label} · ${escapeHtml(m.url)}</span>
          </div>
        `;
      }).join('');
    }

    function renderCurrent() {
      const m = modules.find(x => x.key === current) || modules[0];
      if (!m) return;
      current = m.key;
      document.getElementById('moduleTitle').textContent = m.title;
      document.getElementById('moduleSubtitle').textContent = m.subtitle + ' · Port ' + m.port;
      const quick = document.getElementById('quickLinks');
      quick.innerHTML = (m.quick_links || []).map(link =>
        `<button class="btn ghost" onclick="loadUrl('${link.url}')">${escapeHtml(link.label)}</button>`
      ).join('');
      document.getElementById('logs').textContent = (m.logs || []).join('\\n') || '暂无日志。';
    }

    function selectModule(key) {
      current = key;
      renderModules();
      renderCurrent();
    }

    function loadUrl(url) {
      document.getElementById('frame').src = url;
    }

    function openInPane() {
      const m = modules.find(x => x.key === current);
      if (m) loadUrl(m.url);
    }

    async function openExternal() {
      await window.pywebview.api.open_external(current);
    }

    async function startCurrent() {
      await window.pywebview.api.start_module(current);
      await refreshAll();
    }

    async function stopCurrent() {
      await window.pywebview.api.stop_module(current);
      await refreshAll();
    }

    async function restartCurrent() {
      await window.pywebview.api.restart_module(current);
      await refreshAll();
    }

    async function startAll() {
      await window.pywebview.api.start_all();
      await refreshAll();
    }

    async function stopAll() {
      await window.pywebview.api.stop_all();
      await refreshAll();
    }

    setInterval(refreshAll, 3000);
    refreshAll().then(() => {
      loadUrl('http://127.0.0.1:8765/');
    });
  </script>
</body>
</html>
"""


class OrbitDesktopBridge:
    def __init__(self, modules: list[ModuleSpec]) -> None:
        self.modules = {m.key: m for m in modules}
        self.lock = threading.Lock()

    def _append_log(self, spec: ModuleSpec, line: str) -> None:
        with self.lock:
            spec.logs.append(line.rstrip())
            spec.logs = spec.logs[-400:]

    def _fetch_health(self, url: str) -> tuple[bool, str]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OrbitDesktop/2.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return True, json.dumps(payload, ensure_ascii=False)
        except urllib.error.URLError as exc:
            return False, str(exc.reason or exc)
        except Exception as exc:
            return False, str(exc)

    def _stream_logs(self, spec: ModuleSpec) -> None:
        proc = spec.process
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            self._append_log(spec, line)
        code = proc.wait()
        self._append_log(spec, f"[exit] code={code}")
        spec.process = None

    def _start_process(self, spec: ModuleSpec) -> None:
        if spec.process and spec.process.poll() is None:
            self._append_log(spec, f"[info] {spec.title} 已在运行")
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
        self._append_log(spec, "[start] " + " ".join(spec.command))
        threading.Thread(target=self._stream_logs, args=(spec,), daemon=True).start()

    def _stop_process(self, spec: ModuleSpec) -> None:
        proc = spec.process
        if not proc or proc.poll() is not None:
            spec.process = None
            self._append_log(spec, f"[info] {spec.title} 当前未运行")
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        spec.process = None
        self._append_log(spec, f"[stop] {spec.title}")

    def list_modules(self) -> list[dict]:
        out = []
        for spec in self.modules.values():
            healthy, detail = self._fetch_health(spec.health_url)
            running = spec.process is not None and spec.process.poll() is None
            out.append(
                {
                    "key": spec.key,
                    "title": spec.title,
                    "subtitle": spec.subtitle,
                    "port": spec.port,
                    "url": spec.url,
                    "health_url": spec.health_url,
                    "healthy": healthy,
                    "health_detail": detail,
                    "running": running,
                    "quick_links": [{"label": label, "url": url} for label, url in spec.quick_links],
                    "logs": spec.logs[-120:],
                }
            )
        return out

    def start_module(self, key: str) -> dict:
        spec = self.modules[key]
        self._start_process(spec)
        return {"ok": True}

    def stop_module(self, key: str) -> dict:
        spec = self.modules[key]
        self._stop_process(spec)
        return {"ok": True}

    def restart_module(self, key: str) -> dict:
        spec = self.modules[key]
        self._append_log(spec, f"[restart] {spec.title}")
        self._stop_process(spec)
        time.sleep(0.5)
        self._start_process(spec)
        return {"ok": True}

    def start_all(self) -> dict:
        for spec in self.modules.values():
            self._start_process(spec)
        return {"ok": True}

    def stop_all(self) -> dict:
        for spec in self.modules.values():
            self._stop_process(spec)
        return {"ok": True}

    def open_external(self, key: str) -> dict:
        webbrowser.open(self.modules[key].url)
        return {"ok": True}


def main() -> int:
    bridge = OrbitDesktopBridge(MODULES)
    window = webview.create_window(
        "Orbit 桌面台",
        html=HTML,
        js_api=bridge,
        width=1440,
        height=920,
        min_size=(1180, 760),
    )
    webview.start(debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
