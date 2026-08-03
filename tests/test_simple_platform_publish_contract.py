"""Regression contract for the simplified platform publish buttons.

The browser submits one platform batch and the HTTP response itself is the
final Miaoshou result.  The UI must not create a client-side polling or manual
acceptance workflow after that response.
"""

from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
import inspect
import os
from pathlib import Path
import subprocess
import threading
import urllib.error
import urllib.request

from modules.miaoshou import oneclick_release as miaoshou
from modules.products import server as product_server
from shared_platform import release_store as release_store_module
from shared_platform.collectbox_action import CollectBoxActionStore
from tests.test_tiktok_collectbox_publish_bridge import (
    _approved_tiktok_context,
    _persist_collectbox_result,
)
from tests.test_release_ux_contract import (
    BROWSER_CONTRACT,
    ROOT,
    _browser_runtime,
    _static_server,
)


def _post_json(url: str, payload: dict[str, object]):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_tiktok_button_response_is_the_final_miaoshou_result(
    tmp_path, monkeypatch
):
    release, plan = _approved_tiktok_context(tmp_path)
    _persist_collectbox_result(CollectBoxActionStore(release.path), plan)
    publish_calls: list[dict[str, object]] = []

    def fake_post(path, body):
        if path == miaoshou.PUBLISH_PATH:
            publish_calls.append(dict(body))
            return {"result": "success", "data": {"accepted": True}}
        raise AssertionError(f"unexpected Miaoshou call: {path}")

    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake_post)
    )
    monkeypatch.setattr(
        release_store_module, "default_release_store", lambda: release
    )
    monkeypatch.setattr(
        product_server,
        "_release_dashboard_for_request",
        lambda _data: ({"fixture": "approved-current-facts"}, None),
    )
    monkeypatch.setattr(
        product_server,
        "_release_plan_payload_from_dashboard",
        lambda _dashboard, **_kwargs: (dict(plan["payload"]), []),
    )
    # The old implementation only woke a background worker and returned 202.
    # The simplified contract must complete through the request instead.
    monkeypatch.setattr(
        product_server,
        "_wake_oneclick_worker",
        lambda _job_id: (_ for _ in ()).throw(
            AssertionError("the HTTP result must not depend on a background wake")
        ),
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _post_json(
            (
                f"http://127.0.0.1:{server.server_address[1]}"
                "/api/product-workspace/publish-tiktok"
            ),
            {
                "offer_id": plan["payload"]["product_id"],
                "plan_id": plan["plan_id"],
                "confirmation_token": plan["confirmation_token"],
                "publication_targets": list(plan["targets"]),
                "confirm_publish": True,
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert body == {
        "schema_version": "miaoshou-platform-publish-result/v1",
        "ok": True,
        "platform": "TIKTOK",
        "success": True,
        "message": "TikTok 发布成功",
        "target_count": 6,
        "successful_target_count": 6,
        "failed_targets": [],
        "retryable": True,
    }
    assert len(publish_calls) == 6


def test_frontend_uses_only_four_simple_states_and_never_polls_after_post():
    script = (
        product_server.ROOT / "web/static/product_workspace.js"
    ).read_text(encoding="utf-8")
    start = script.index("async function publishPlatformBatch(")
    end = script.index("async function publishSelectedTargets()", start)
    publish = script[start:end]

    assert "发布中" in publish
    assert "发布成功" in publish
    assert "发布失败" in publish
    assert "payload.success !== true" in publish
    assert "response.status !== 200" in publish
    assert "scheduleOneClickStatusPoll" not in publish
    assert "payload.accepted" not in publish
    assert "payload.job" not in publish
    assert "WAITING_MANUAL_ACCEPTANCE" not in publish
    assert "RECONCILIATION_REQUIRED" not in publish


def test_server_startup_never_resumes_historical_publish_jobs():
    """Only an explicit platform-button request may trigger Miaoshou writes."""

    startup = inspect.getsource(product_server.serve)

    assert "_start_oneclick_background_worker()" not in startup


def test_real_chromium_covers_all_simple_publish_paths_with_screenshots(
    tmp_path,
):
    runtime = _browser_runtime()
    assert runtime is not None, "bundled Node + Playwright runtime is required"
    node, modules = runtime
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    assert chrome.is_file(), "local Chrome executable is required"
    configured_artifacts = os.environ.get("ORBIT_BROWSER_ARTIFACT_DIR")
    artifacts = (
        Path(configured_artifacts)
        if configured_artifacts
        else tmp_path / "simple-platform-publish-screenshots"
    )
    environment = os.environ.copy()
    environment.update(
        {
            "NODE_PATH": str(modules),
            "ORBIT_CHROMIUM_BIN": str(chrome),
            "ORBIT_BROWSER_CONTRACT_ONLY": "simplified-platform-publish",
            "ORBIT_BROWSER_ARTIFACT_DIR": str(artifacts),
        }
    )
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
        "simplified platform publish Chromium contract failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    expected = {
        f"{viewport}-{state}.png"
        for viewport in ("1440x900", "390x844")
        for state in (
            "initial",
            "publishing",
            "failure-and-independent-success",
            "all-success-after-retry",
        )
    }
    assert {path.name for path in artifacts.glob("*.png")} == expected
