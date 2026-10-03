"""Actual source dashboard assets in Chromium; no DB, provider or business POST."""
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler
import os
from pathlib import Path
import subprocess
import threading
from urllib.parse import unquote, urlsplit

import pytest

from shared_platform.bounded_web_server import BoundedThreadingHTTPServer
from test_release_ux_contract import _browser_runtime

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "domains/supply_chain_operations/dashboard"


@contextmanager
def _dashboard_server():
    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, value):
            path = unquote(urlsplit(value).path)
            if path.startswith("/supply-chain/"):
                relative = path.removeprefix("/supply-chain/") or "index.html"
                target = (DASHBOARD / relative).resolve()
                return str(target) if target.is_relative_to(DASHBOARD) else str(DASHBOARD / "missing")
            return str(ROOT / "web" / path.lstrip("/"))

        def log_message(self, *_):
            pass

    server = BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize("failure", ["data-503", "app-network-error"])
def test_supply_asset_failure_is_visible_and_retry_restores_source_values(tmp_path, failure):
    runtime = _browser_runtime()
    assert runtime is not None, "Confirmed Node/Playwright is required for this actual browser contract"
    node, modules = runtime
    environment = os.environ.copy()
    environment["NODE_PATH"] = str(modules)
    chromium = Path.home() / "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe"
    environment.setdefault("ORBIT_CHROMIUM_BIN", str(chromium))
    with _dashboard_server() as base:
        result = subprocess.run(
            [str(node), str(ROOT / "tests/browser/supply_load_errors.js"), base, str(tmp_path), failure],
            cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
