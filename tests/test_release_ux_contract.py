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
            "adoptTitleCandidate": [
                "titleAdoptSubmitting = true",
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
            "overwriteMiaoshou": [
                "releaseSubmitting = true",
                "confirm_miaoshou_overwrite: true",
                'approved_by: "Kyle"',
                "expected_revision",
                "payload_digest",
                "try {",
                "catch (error)",
                "reconciliation_required",
                "finally",
                "commonOverwriteMessage",
            ],
            "previewShopeePriceRepair": [
                'phase: "checking"',
                "shopee-price-repair-preview",
                "repair_allowed === true",
                'phase: "preview"',
                "catch (error)",
            ],
            "submitShopeePriceRepair": [
                "releaseSubmitting = true",
                "shopee-price-repair",
                "confirm_shopee_price_repair: true",
                'approved_by: "Kyle"',
                "expected_revision",
                "payload_digest",
                "preflight_digest",
                "durable_state_uncertain",
                "reconciliation_required",
                "fetchDashboard",
                "catch (error)",
            ],
            "reconcileShopeePriceRepair": [
                'phase: "reconciling"',
                "shopee-price-reconciliation-preview",
                "reconciliation_allowed === true",
                "shopee-price-reconciliation",
                "confirm_shopee_price_reconciliation: true",
                'approved_by: "Kyle"',
                "operation_digest",
                "零平台写入",
                "fetchDashboard",
                "catch (error)",
                "finally",
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
                "reportPlanningBlocker",
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


def test_common_overwrite_html_contract_is_explicit_and_separate():
    html = (WEB / "product_workspace.html").read_text(encoding="utf-8")
    source = (WEB / "static" / "product_workspace.js").read_text(
        encoding="utf-8"
    )

    for element_id in (
        "commonOverwritePanel",
        "commonOverwriteIdentity",
        "commonOverwriteDiff",
        "commonOverwriteCheckbox",
        "commonOverwriteButton",
        "commonOverwriteMessage",
    ):
        assert f'id="{element_id}"' in html
    assert "按当前 ReleasePlan 覆盖妙手公共草稿并回读" in html
    normal = _function_body(source, "prepareMiaoshou")
    overwrite = _function_body(source, "overwriteMiaoshou")
    assert "confirm_miaoshou_overwrite" not in normal
    assert "confirm_miaoshou_overwrite: true" in overwrite
    assert 'approved_by: "Kyle"' in overwrite


def test_shopee_price_repair_ui_is_target_scoped_and_dedicated():
    source = (WEB / "static" / "product_workspace.js").read_text(
        encoding="utf-8"
    )
    styles = (WEB / "static" / "product_workspace.css").read_text(
        encoding="utf-8"
    )
    submit = _function_body(source, "submitShopeePriceRepair")
    preview = _function_body(source, "previewShopeePriceRepair")
    reconcile = _function_body(source, "reconcileShopeePriceRepair")

    assert 'new Set(["shopee:PH", "shopee:TH"])' in source
    assert "shopeePriceRepairEligible" in source
    assert "target?.status === \"FAILED\"" in source
    assert "target?.external_id" in source
    assert "!target?.repair" in source
    assert (
        "/api/product-workspace/release-target/"
        "shopee-price-repair-preview"
    ) in preview
    assert "repair_allowed === true" in preview
    assert (
        "/api/product-workspace/release-target/shopee-price-repair"
    ) in submit
    assert "confirm_shopee_price_repair: true" in submit
    assert "confirm: true" not in submit
    assert 'approved_by: "Kyle"' in submit
    assert "currentReleaseBody" in submit
    assert "shopee-price-repair-panel" in styles
    assert "我确认仅原地修正该站点价格，不重发商品。" in source
    assert "挂牌价已写入，等待只读对账" in source
    assert "只读回读并结案" in source
    assert "零平台写入" in source
    assert "SIP差异待财务审查" in source
    assert "shopee-price-reconciliation-preview" in reconcile
    assert "shopee-price-reconciliation" in reconcile
    assert "confirm_shopee_price_reconciliation: true" in reconcile
    assert "confirm: true" not in reconcile
    assert 'approved_by: "Kyle"' in reconcile
    assert "operation_digest" in reconcile


def test_remaining_channel_retry_ui_uses_only_the_target_scoped_seam():
    source = (WEB / "static" / "product_workspace.js").read_text(
        encoding="utf-8"
    )
    styles = (WEB / "static" / "product_workspace.css").read_text(
        encoding="utf-8"
    )
    preview = _function_body(source, "previewTargetScopedAction")
    submit = _function_body(source, "submitTargetScopedAction")

    assert 'new Set(["shopee:MY", "shopee:VN", "ozon:RU"])' in source
    assert "target?.status !== \"FAILED\"" in source
    assert "target-scoped-action-preview" in preview
    assert "target-scoped-action" in submit
    assert "confirm_target_scoped_action: true" in submit
    assert 'approved_by: "Kyle"' in submit
    for field in ("proof_digest", "failure_attempt", "payload_digest", "planned_command_digest", "preflight_digest"):
        assert field in submit
    assert "planned_command:" not in submit
    assert "publishSelectedTargets" not in submit
    assert "target-scoped-action-panel" in styles
    assert "Shopee 自动翻译 · 发布后官方回读" in source
    assert "区域商品身份已验证 · 平台翻译/图片待人工复核" in source
    assert "需要对账" in source


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
            'id="nextStepActionButton"',
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


def test_disabled_release_checkboxes_expose_visible_reasons():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    studio_html = (ROOT / "web/ai_image_studio.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/disabled_control_hints.js").read_text(
        encoding="utf-8"
    )
    style = (ROOT / "web/static/product_workspace.css").read_text(encoding="utf-8")
    studio_style = (ROOT / "web/static/ai_image_studio.css").read_text(
        encoding="utf-8"
    )

    assert "disabled_control_hints.js?v=20260729-v2" in html
    assert "disabled_control_hints.js?v=20260729-v2" in studio_html
    for control_id in (
        "releasePlanCheckbox",
        "prepareMiaoshouCheckbox",
        "commonOverwriteCheckbox",
        "publishAllCheckbox",
    ):
        assert control_id in script
    assert "暂不可选：" in script
    assert "control.dataset.disabledReason" in script
    assert 'known.includes("计划预览已形成")' not in script
    assert 'control.setAttribute("aria-describedby", hint.id)' in script
    assert 'document.querySelectorAll(\'input[type="checkbox"]\')' in script
    assert ".disabled-control-reason" in style
    assert ".disabled-control-reason" in studio_style


def test_workspace_consumes_server_owned_next_action_instead_of_guessing():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )

    assert 'id="nextStepActionButton"' in html
    assert "product-workflow-next-action/v1" in script
    assert "data.workflow_next_action" in script
    assert "runWorkflowNextAction" in script
    assert "canonical_common_readback" in script
    assert (
        '$("#nextStepActionButton").addEventListener("click", '
        "runWorkflowNextAction)"
    ) in script


def test_blocked_release_plan_exposes_a_recovery_action_instead_of_a_dead_end():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )
    style = (ROOT / "web/static/product_workspace.css").read_text(
        encoding="utf-8"
    )

    for token in (
        'id="releasePlanRecovery"',
        'id="releasePlanRecoveryDetail"',
        'id="releasePlanRecoveryActions"',
        'id="listingCopyAssistant"',
    ):
        assert token in html
    assert "renderReleaseRecovery(release)" in script
    assert 'code === "refresh_listing_copy"' in script
    assert 'code === "adopt_listing_copy"' in script
    assert 'data-release-recovery="${esc(action.code)}"' in script
    assert "重新检查并定位未完成步骤" in script
    assert "titleAdoptSubmitting" in script
    assert "updateReleaseControls(currentData || {})" in script
    assert "pageLoading = false;" in script
    assert ".release-plan-recovery" in style


def test_release_plan_failure_refreshes_the_current_gate_and_explains_reapproval():
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )
    server = (ROOT / "modules/products/server.py").read_text(encoding="utf-8")

    assert "error.payload?.dashboard" in script
    assert "adoptWorkflowDashboard(error.payload.dashboard)" in script
    assert '"error_code": "release_plan_not_ready"' in server
    assert '"error": blockers[0]' in server
    assert '"dashboard": current_dashboard' in server


def test_oneclick_release_ui_uses_only_the_async_server_controlplane():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )
    style = (ROOT / "web/static/product_workspace.css").read_text(
        encoding="utf-8"
    )
    publish = _function_body(script, "publishSelectedTargets")
    preview = _function_body(script, "requestOneClickPreview")
    status = _function_body(script, "pollOneClickStatus")

    for control_id in (
        "oneClickExecutionPreview",
        "oneClickExecutionGroups",
        "oneClickExecutionMessage",
        "oneClickNextActionButton",
    ):
        assert f'id="{control_id}"' in html
    assert "/api/product-workspace/publish-preview?" in preview
    assert "/api/product-workspace/publish-status?" in status
    assert '"/api/product-workspace/publish"' in publish
    assert 'ONECLICK_PREVIEW_SCHEMA = "release-batch-preparation/v1"' in script
    assert 'ONECLICK_STATUS_SCHEMA = "oneclick-release-status/v1"' in script
    assert "payload.persisted !== false" in preview
    assert "payload.accepted !== true" in publish
    assert "{ expectedStatus: 202 }" in publish
    assert "response.status !== expectedStatus" in script
    assert "oneClickExecution.postAttempted = true" in publish
    assert "oneClickExecution.job" in publish
    assert "scheduleOneClickStatusPoll(generation, 0)" in publish
    assert "AbortController" in preview
    assert "AbortController" in status
    assert "generation !== oneClickExecution.generation" in preview
    assert "generation !== oneClickExecution.generation" in status
    assert "if (approvalSubmitting || releaseSubmitting) return;" in script
    assert "projection?.canonical_next_action" in script
    assert "error.oneClickContractError === true" in status
    assert "ONECLICK_JOB_PHASES.has(projection.phase)" in script
    assert "ONECLICK_TARGET_STATUSES.has(target.status)" in script
    assert "ONECLICK_CLASSIFICATIONS.has(target.classification)" in script
    assert "sameSortedValues(summary.will_dispatch" in script
    assert "dashboardFromPayload" not in publish
    assert "while (" not in publish
    assert ".oneclick-execution-group" in style
    assert ".oneclick-target-card:focus-visible" in style


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
            timeout=180,
            check=False,
        )
    assert result.returncode == 0, (
        "Chromium release UX contract failed.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
