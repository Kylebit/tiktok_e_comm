from __future__ import annotations

import contextlib
import http.server
import os
from pathlib import Path
import socket
import subprocess
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
BROWSER_CONTRACT = ROOT / "tests" / "browser" / "release_ux_contract.js"


class _ReleaseStaticHandler(http.server.SimpleHTTPRequestHandler):
    route_files = {
        "/": "index.html",
        "/product-workspace": "product_workspace.html",
        "/ai-image-studio": "ai_image_studio.html",
        "/profit": "profit_center.html",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self):  # noqa: N802 - stdlib handler contract
        path = self.path.split("?", 1)[0]
        if path in self.route_files:
            self.path = f"/{self.route_files[path]}"
        super().do_GET()

    def log_message(self, _format, *args):
        return


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _static_server():
    port = _free_port()
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port),
        _ReleaseStaticHandler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _browser_runtime() -> tuple[Path, Path] | None:
    explicit_node = os.environ.get("ORBIT_NODE_BIN", "").strip()
    explicit_modules = os.environ.get("ORBIT_NODE_MODULES", "").strip()
    candidates = []
    if explicit_node and explicit_modules:
        candidates.append((Path(explicit_node), Path(explicit_modules)))
    runtime = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
    )
    candidates.append((runtime / "bin" / "node.exe", runtime / "node_modules"))
    candidates.append((runtime / "bin" / "node", runtime / "node_modules"))
    for node, modules in candidates:
        if node.is_file() and (modules / "playwright").is_dir():
            return node, modules
    return None


def _function_body(source: str, function_name: str) -> str:
    marker = f"async function {function_name}("
    start = source.find(marker)
    assert start >= 0, f"missing async function {function_name}"
    next_start = source.find("\n  async function ", start + len(marker))
    if next_start < 0:
        next_start = len(source)
    return source[start:next_start]


def test_every_formal_async_action_has_loading_success_and_failure_feedback():
    """Fast source contract; Chromium verifies that the feedback is actually visible."""

    manifests = {
        "web/static/product_workspace.js": {
            "submitFactsEdit": [
                "is-submitting",
                "try {",
                "catch (error)",
                "finally",
                "factsEditMessage",
            ],
            "generateTitleDraft": [
                "titleDraftSubmitting = true",
                "try {",
                "titleDraftStatus",
                "catch (error)",
                "finally",
            ],
            "submitApproval": [
                "is-submitting",
                "try {",
                "catch (error)",
                "finally",
                "approvalMessage",
            ],
            "approveReleasePlan": [
                "releaseSubmitting = true",
                "try {",
                "catch (error)",
                "finally",
                "releasePlanMessage",
            ],
            "prepareMiaoshou": [
                "releaseSubmitting = true",
                "try {",
                "catch (error)",
                "finally",
                "prepareMiaoshouMessage",
            ],
            "publishSelectedTargets": [
                "releaseSubmitting = true",
                "try {",
                "catch (error)",
                "finally",
                "publishRunMessage",
            ],
            "refreshQueueProduct": [
                "item.loading = true",
                "item.activity",
                "try {",
                "catch (error)",
                "finally",
            ],
            "refreshAllQueueProducts": [
                "queueRefreshing = true",
                "queueMessage",
                "Promise.allSettled",
                "queueRefreshing = false",
            ],
        },
        "web/static/ai_image_studio.js": {
            "load": ["setLoading", "try {", "toast(", "catch (error)", "finally"],
            "saveSourceReview": [
                "setLoading",
                "try {",
                "toast(",
                "catch (error)",
                "finally",
            ],
            "saveContentReview": [
                "setLoading",
                "try {",
                "toast(",
                "catch (error)",
                "finally",
            ],
            "preparePackage": [
                "setLoading",
                "renderPlanningProgress",
                "toast(",
                'status: "failed"',
                "finally",
            ],
            "requestAiPlan": [
                "setLoading",
                "renderPlanningProgress",
                "toast(",
                'status: "failed"',
                "finally",
            ],
            "prepareGeneration": [
                "setLoading",
                "renderGenerationProgress",
                "toast(",
                'status: "error"',
                "finally",
            ],
            "startPaidGeneration": [
                "setLoading",
                "renderGenerationProgress",
                "toast(",
                'status: "error"',
                "finally",
            ],
            "saveVersionReview": [
                "setLoading",
                "try {",
                "toast(",
                "catch (error)",
                "finally",
            ],
            "saveOrder": ["setLoading", "try {", "toast(", "catch (error)", "finally"],
            "syncMiaoshou": [
                "setLoading",
                "renderSyncFeedback",
                "try {",
                "catch (error)",
                "finally",
            ],
        },
        "web/static/profit_center.js": {
            "loadWeekly": [
                "setLoading",
                "try {",
                "renderWeekly",
                "catch (error)",
                "renderUnavailableWeekly",
                "finally",
            ],
            "loadSku": [
                "setLoading",
                "try {",
                "renderSku",
                "catch (error)",
                "empty-state",
                "finally",
            ],
        },
    }
    for relative_path, functions in manifests.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for function_name, tokens in functions.items():
            body = _function_body(source, function_name)
            missing = [token for token in tokens if token not in body]
            assert not missing, (
                f"{relative_path}:{function_name} misses release UX tokens {missing}"
            )


def test_formal_pages_expose_accessible_feedback_regions():
    html_contract = {
        "web/product_workspace.html": [
            'id="queueGrid"',
            'id="queueMessage"',
            'role="status"',
            'id="pageAlert"',
            'role="alert"',
            'id="nextStepTitle"',
            'id="nextStepDescription"',
        ],
        "web/ai_image_studio.html": [
            'id="alert"',
            'role="alert"',
            'id="toast"',
            'role="status"',
            'id="planningProgress"',
            'id="generationProgress"',
            'id="syncProgress"',
        ],
        "web/profit_center.html": [
            'id="weeklyAlert"',
            'id="weeklyVerdict"',
            'aria-live="polite"',
            'id="skuAlert"',
            'id="skuResult"',
        ],
    }
    for relative_path, tokens in html_contract.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        missing = [token for token in tokens if token not in source]
        assert not missing, f"{relative_path} misses feedback contracts {missing}"


def test_release_pages_in_real_chromium():
    runtime = _browser_runtime()
    if runtime is None:
        message = (
            "Node + Playwright runtime unavailable; set ORBIT_NODE_BIN and "
            "ORBIT_NODE_MODULES, or run in the bundled Codex runtime"
        )
        if os.environ.get("ORBIT_REQUIRE_BROWSER_TESTS") == "1":
            pytest.fail(message)
        pytest.skip(message)

    node, modules = runtime
    environment = os.environ.copy()
    environment["NODE_PATH"] = str(modules)
    with _static_server() as base_url:
        result = subprocess.run(
            [str(node), str(BROWSER_CONTRACT), base_url],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        "Chromium release UX contract failed.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
