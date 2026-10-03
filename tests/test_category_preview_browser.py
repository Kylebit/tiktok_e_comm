"""Real browser coverage of legacy-host recovery and the managed R3 boundary."""
import os
import subprocess
import pytest

from test_release_ux_contract import _browser_runtime, _static_server, BROWSER_CONTRACT, ROOT
from tests.test_category_preview_readonly import _pending_intent, _logical_category_state
from tests.test_channel_category_decision_http import category_context, http_server, _observed_options
from modules.products import server as product_server
from shared_platform.channel_category_decisions import build_category_options


@pytest.mark.parametrize("mode", ["pending", "fresh", "changed"])
def test_category_preview_actual_managed_chromium_is_readonly(category_context, http_server, monkeypatch, mode):
    dashboard, store = category_context
    if mode != "fresh":
        _pending_intent(category_context, http_server, monkeypatch)
    if mode == "changed":
        monkeypatch.setattr(product_server, "_observe_channel_category_options", lambda payload, **_kwargs: build_category_options(
            _observed_options(missing_required=True), context=product_server._channel_category_context(payload),
            creation_seed=product_server._channel_category_creation_seed(payload)))
    before = store.path.read_bytes() if store.path.exists() else None
    logical = _logical_category_state(store) if before is not None else None
    runtime = _browser_runtime()
    assert runtime is not None, "explicit Node/Playwright runtime required"
    node, modules = runtime
    env = os.environ.copy()
    env["NODE_PATH"] = str(modules)
    env["ORBIT_BROWSER_CONTRACT_ONLY"] = "category-managed-readonly"
    env["ORBIT_CHROMIUM_BIN"] = env["ORBIT_BROWSER_EXECUTABLE"]
    env["ORBIT_CATEGORY_FIXTURE_BASE"] = http_server
    env["ORBIT_CATEGORY_FIXTURE_OFFER"] = dashboard["product"]["offer_id"]
    env["ORBIT_CATEGORY_EXPECTED_STATUS"] = "READY_FOR_SELECTION" if mode == "fresh" else "RECHECK_REQUIRED"
    with _static_server() as base:
        result = subprocess.run([str(node), str(BROWSER_CONTRACT), base], cwd=ROOT,
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert (store.path.read_bytes() if store.path.exists() else None) == before
    assert (_logical_category_state(store) if store.path.exists() else None) == logical
