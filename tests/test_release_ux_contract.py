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
        else:
            self.path = path
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
                    "releasePlanApprovalSubmitting = true",
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
            "publishPlatformBatch": [
                'result.status = "PROCESSING"',
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


def test_failed_current_queue_read_is_not_presented_as_still_loading():
    """A terminal source-read error must expose an immediate retry action."""

    source = (WEB / "static" / "product_workspace.js").read_text(
        encoding="utf-8"
    )
    render_queue = source[
        source.index("function renderQueue("):
        source.index("function syncCurrentUrl(")
    ]
    refresh_queue_product = _function_body(source, "refreshQueueProduct")

    assert "const switchLabel" in render_queue
    assert "const switchDisabled" in render_queue
    assert "item.error" in render_queue
    assert "item.loading" in render_queue
    assert "item.error = message" in refresh_queue_product
    assert '$("#queueMessage").textContent' in refresh_queue_product


def test_product_workspace_never_discards_a_dirty_fact_draft_for_a_later_action():
    source = (WEB / "static" / "product_workspace.js").read_text(
        encoding="utf-8"
    )
    refresh_queue_product = _function_body(source, "refreshQueueProduct")
    submit_approval = _function_body(source, "submitApproval")
    workflow_next = _function_body(source, "runWorkflowNextAction")
    approve_plan = _function_body(source, "approveReleasePlan")
    generate_title = _function_body(source, "generateTitleDraft")

    assert "const preservedFactsDraft = captureProductFactsDraft()" in refresh_queue_product
    assert "restoreProductFactsDraft(preservedFactsDraft" in refresh_queue_product
    assert "await saveDirtyProductFactsBeforeAction" in submit_approval
    assert "await saveDirtyProductFactsBeforeAction" in workflow_next
    assert "await saveDirtyProductFactsBeforeAction" in approve_plan
    assert "const preservedFactsDraft = captureProductFactsDraft()" in generate_title
    assert "restoreProductFactsDraft(preservedFactsDraft" in generate_title


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
        'id="releasePlanRecoveryReview"',
        'id="listingCopyAssistant"',
    ):
        assert token in html
    assert "renderReleaseRecovery(release)" in script
    assert 'code === "refresh_listing_copy"' in script
    assert 'code === "adopt_listing_copy"' in script
    assert 'code === "review_shopee_global_plan"' in script
    assert "shopeeGlobalPlanIdentity(currentData)" in script
    assert "shopeeGlobalPlanReviewRequired(data, projection)" in script
    assert '#releasePlanRecoveryReview .shopee-global-plan-approval-form' in script
    assert 'data-release-recovery="${esc(action.code)}"' in script
    assert "重新检查并定位未完成步骤" in script
    assert "titleAdoptSubmitting" in script
    assert "updateReleaseControls(currentData || {})" in script
    assert "pageLoading = false;" in script
    assert ".release-plan-recovery" in style


def test_release_plan_disabled_reason_uses_the_server_owned_next_action():
    html = (ROOT / "web" / "product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "static" / "product_workspace.js").read_text(
        encoding="utf-8"
    )

    assert 'id="approveReleasePlanButton"' in html
    assert 'aria-describedby="releasePlanMessage releasePlanRecoveryDetail"' in html
    assert 'code: "continue_workflow"' in script
    assert 'label: workflow.label' in script
    assert 'detail: workflow.detail' in script
    assert 'if (code === "continue_workflow")' in script
    assert "await runWorkflowNextAction()" in script
    assert "approvalButton.dataset.disabledReason" in script
    assert 'id="factsImpactSummary"' in html
    assert "function renderFieldImpactSummary" in script
    assert "data.field_impact_map" in script


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


def test_platform_publish_ui_polls_the_durable_publication_report():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )
    style = (ROOT / "web/static/product_workspace.css").read_text(
        encoding="utf-8"
    )
    publish = _function_body(script, "publishPlatformBatch")
    poll = _function_body(script, "pollPublicationReport")
    ensure = script[
        script.index("function ensureOneClickExecution("):
        script.index("async function postProductWorkspace(")
    ]

    for control_id in (
        "oneClickExecutionPreview",
        "oneClickExecutionGroups",
        "oneClickExecutionMessage",
    ):
        assert f'id="{control_id}"' in html
    assert 'id="oneClickNextActionButton"' not in html
    assert 'id="oneClickReadRetryButton"' not in html
    assert "boundedJsonFetch(\n        endpoint," in publish
    assert "payload.schema_version !== \"product-publication-start/v1\"" in publish
    assert "response.status !== 202" in publish
    assert "payload.report_id" in publish
    assert "payload.run_id" in publish
    assert "boundedJsonFetch" in publish
    assert 'result.status = "PROCESSING"' in publish
    assert 'result.status = "FAILED"' in publish
    assert "/api/product-workspace/publication-report?" in poll
    assert "PUBLICATION_REPORT_STATUSES.has(report?.status)" in poll
    assert "result.status = report.status" in poll
    assert 'result.status = "PUBLISHED"' not in publish
    assert "scheduleOneClickStatusPoll" not in publish
    assert "payload.accepted" not in publish
    assert "payload.job" not in publish
    assert "requestOneClickPreview" not in ensure
    assert "pollOneClickStatus" not in ensure
    assert "oneClickExecution.job" not in ensure
    assert ".oneclick-execution-group" in style
    assert ".oneclick-target-card:focus-visible" in style


def test_approved_release_flow_has_three_isolated_platform_actions():
    """Each platform has one explicit button and one server-owned endpoint."""

    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )

    assert 'id="releasePrimaryActionButton"' in html
    assert 'id="shopeeGlobalReleaseButton"' in html
    assert 'id="ozonReleaseButton"' in html
    assert 'id="collectboxActionButton"' in html
    assert 'id="legacyReleaseActionPanels"' in html
    primary_projection = script[
        script.index("function updateReleasePrimaryAction("):
        script.index("function updateReleaseControls(")
    ]
    assert "legacyPanels.hidden = unifiedAuthority" in primary_projection


def test_collectbox_and_platform_release_actions_are_not_cross_wired():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )

    assert 'id="releasePrimaryActionButton"' in html
    assert 'id="collectboxActionPanel"' in html
    assert 'id="collectboxActionMessage"' in html
    assert 'id="collectboxActionStatus"' in html
    assert "product_workspace.js?v=20260804-v37" in html
    assert 'COLLECTBOX_ACTION_SCHEMA = "collectbox-action-status/v1"' in script
    assert "/api/product-workspace/collectbox-action/preview?" in script
    assert "/api/product-workspace/collectbox-action/status?" in script
    assert '"/api/product-workspace/collectbox-action/start"' in script
    assert "confirm_collectbox_action: true" in script
    assert 'approved_by: "Kyle"' in script
    assert "本批次结果待确认；可重新导入并创建新批次" in script
    assert "结果待人工确认，不能重试" not in script
    assert '"target_outcomes"' in script
    assert "function renderCollectboxTargetOutcomes(" in script
    assert "data-collectbox-target-outcome" in script
    assert "collectboxTargetFailureText" in script
    assert ">= row.external_writes.classes.length" in script
    assert '"miaoshou:collectbox:claim:tiktok"' in script
    assert '"miaoshou:collectbox:claim:shopee"' in script
    assert (
        '$("#collectboxActionButton").addEventListener(\n'
        '    "click",\n'
        "    runCollectboxPrimaryAction,"
    ) in script
    assert "oneClickPreview.hidden = !unifiedAuthority" in script
    assert "collectboxPanel.hidden = !unifiedAuthority" in script
    action = _function_body(script, "runCollectboxPrimaryAction")
    assert "/api/product-workspace/collectbox-action/start" in action
    assert "/api/product-workspace/publish" not in action
    assert "prepareMiaoshou" not in action
    assert "publishSelectedTargets" not in action
    assert '"/api/product-workspace/publish-tiktok"' in script
    assert '"/api/product-workspace/publish-shopee-global"' in script
    assert '"/api/product-workspace/publish-ozon"' in script
    assert (
        '$("#releasePrimaryActionButton").addEventListener('
        '"click", runTiktokReleaseAction)'
    ) in script
    assert (
        '$("#shopeeGlobalReleaseButton").addEventListener('
        '"click", runShopeeGlobalReleaseAction)'
    ) in script
    assert (
        '$("#ozonReleaseButton").addEventListener('
        '"click", runOzonReleaseAction)'
    ) in script


def test_oneclick_mvp_keeps_legacy_reads_idle_without_legacy_resubmit():
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )
    global_preview = _function_body(script, "requestShopeeGlobalPlanPreview")
    global_approval = _function_body(script, "submitShopeeGlobalPlanApproval")
    reconcile = _function_body(script, "reconcileOneClickAcceptance")
    router = _function_body(script, "routeOneClickNextAction")

    assert "ONECLICK_LOCAL_READ_TIMEOUT_MS = 15000" in script
    assert "ONECLICK_LOCAL_POST_TIMEOUT_MS = 15000" in script
    assert "SHOPEE_GLOBAL_READ_TIMEOUT_MS = 180000" in script
    assert "SHOPEE_GLOBAL_READ_TIMEOUT_MS" in global_preview
    assert "approvalPostAttempted" in global_approval
    assert "responseOutcomeUnknown" in global_approval
    assert "/api/product-workspace/publish-status?" in reconcile
    assert "reconcileOneClickAcceptance(generation)" in script
    assert "retryOneClickReadOnly" in script
    assert "refresh_release_state" in router
    assert "review_shopee_global_plan" in router
    assert "focusFirstControl" in router
    assert "wait_for_channel_capability" in script
    assert "resumeExactZeroWriteFailures" not in script
    assert '"/api/product-workspace/publish"' not in script
    assert "await retryOneClickReadOnly()" in router
    assert "旧版恢复入口已退役" in router
    assert "verify_submission_in_marketplace" in router
    assert "marketplace_product_id" in router
    assert "reconcile_before_any_retry" in router
    assert "data-target-scoped-action='preview'" in router
    assert "restore_channel_authorization" in router
    assert "Shopee 授权管理入口" in router
    publish = _function_body(script, "publishPlatformBatch")
    primary = _function_body(script, "runReleasePrimaryAction")
    render = script[
        script.index("function renderOneClickExecution("):
        script.index("function focusOneClickTarget(")
    ]
    assert "await publishSelectedTargets()" in primary
    assert "preview.start_allowed" not in publish
    assert "preview.preparation_pending_count" not in publish
    assert "reconcileOneClickAcceptance" not in publish
    assert 'result.status = "FAILED"' in publish
    assert "oneClickObservationWarningForm(target)" not in render
    assert "shopeeGlobalControlCard(control)" not in render


def test_shopee_global_plan_ui_is_redacted_and_fail_closed():
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )
    validator = script
    panel = script

    assert "shopee-global-plan-preview/v1" in script
    assert "shopee-global-plan-candidate/v1" in script
    assert "approved-shopee-global-plan/v1" in script
    assert "approved-shopee-global-plan/v2" in script
    assert (
        '["approved-shopee-global-plan/v1", "NEW_GLOBAL"]'
        in validator
    )
    assert (
        '["approved-shopee-global-plan/v2", "EXISTING_GLOBAL"]'
        in validator
    )
    assert (
        "APPROVED_SHOPEE_GLOBAL_PLAN_SCHEMA_MODES.get("
        in validator
    )
    assert '"BLOCKED_AUTH"' in validator
    assert '"BLOCKED_CAPABILITY"' in validator
    assert "reason_category" in validator
    assert "shopee-global-auth-restore" in panel
    assert "shopee-global-plan-preview-retry" in panel
    assert "confirm_approved_shopee_global_plan" in script
    assert "expected_candidate_digest" in script
    assert "raw response" not in panel.lower()

    assert "channel-category-decision-preview/v2" in script
    assert "RECHECK_REQUIRED" in script
    assert "required_attribute_selections" in script
    assert "selected_brand_identity_digest" in script
    assert "selected_location_identity_digest" in script
    assert "selected_creation_fact_identity_digest" in script
    assert "confirm_seller_stock_quantity: true" in script
    assert "confirm_condition_and_preorder: true" in script
    assert "confirm_required_attribute_selections: true" in script
    assert 'data-selection-kind="SINGLE"' in script
    assert 'data-selection-kind="MULTI"' in script
    assert 'data-selection-kind="TEXT"' in script
    assert "requestShopeeCategoryDecisionPreview(identity)" in script


def test_release_v2_terminal_history_never_reenters_primary_operation():
    """PRD-002/031/042: completed attempts are audit-only UI facts."""

    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )
    render = script[
        script.index("function renderOneClickExecution("):
        script.index("function focusOneClickTarget(")
    ]
    ensure = script[
        script.index("function ensureOneClickExecution("):
        script.index("async function postProductWorkspace(")
    ]
    controls = script[
        script.index("function updateReleaseControls("):
        script.index("function renderReleaseV1(")
    ]

    for historical_copy in (
        "上次未完成",
        "上次发布失败",
        "上次结果未确认",
        "上次未发布",
        "已显示上一轮妙手提交结果",
    ):
        assert historical_copy not in render

    assert "Historical jobs are" in ensure
    assert "oneClickExecution.job" not in ensure
    assert "requestOneClickPreview" not in ensure
    assert "本计划已有终态持久任务，不能再次提交" not in controls

    browser_contract = BROWSER_CONTRACT.read_text(encoding="utf-8")
    assert (
        "async function releaseV2TerminalHistoryIsolationContract("
        in browser_contract
    )
    assert '=== "release-v2-terminal-history"' in browser_contract
    assert "await releaseV2TerminalHistoryIsolationContract(" in browser_contract


def test_oneclick_manual_review_forms_keep_warning_and_apiless_contracts_separate():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )
    warning_submit = _function_body(
        script,
        "submitOneClickObservationAcceptance",
    )
    apiless_submit = _function_body(script, "submitManualTargetVerification")

    assert "product_workspace.css?v=20260801-v20" in html
    assert "product_workspace.js?v=20260804-v37" in html
    assert '"SUCCEEDED_MANUAL_REVIEW"' in script
    assert '"review_verified_observation_warning"' in script
    assert "oneclick-observation-review-form" in script
    assert "官方硬事实已验证" in script
    assert "存在平台派生翻译/图片观察警告，等待Kyle人工验收" in script
    assert 'name="manual_review_accepted"' in script
    assert "observation_evidence_digest" in warning_submit
    assert "manual_review_accepted: true" in warning_submit
    assert 'targetLabel.startsWith("shopee:")' in warning_submit
    assert '"/api/product-workspace/release-target/manual-verify"' in warning_submit
    assert "{ expectedStatus: 200 }" in warning_submit
    assert "marketplace_product_id" not in warning_submit
    assert "checks:" not in warning_submit
    assert "oneClickExecution.job = acceptedJob" in warning_submit
    assert 'acceptedTarget?.status !== "SUCCEEDED"' in warning_submit

    assert "marketplace_product_id: productId" in apiless_submit
    assert "checks:" in apiless_submit
    for check_name in (
        "identity_matches",
        "seller_sku_matches",
        "single_listing_for_sku",
        "title_matches",
        "price_matches",
        "images_match",
        "logistics_match",
    ):
        assert f"{check_name}: true" in apiless_submit
    assert "manual_review_accepted" not in apiless_submit
    assert "observation_evidence_digest" not in apiless_submit


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
    local_chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not environment.get("ORBIT_CHROMIUM_BIN") and local_chrome.is_file():
        environment["ORBIT_CHROMIUM_BIN"] = str(local_chrome)
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
