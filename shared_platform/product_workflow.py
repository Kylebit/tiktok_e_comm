"""Server-owned product workflow state machine.

The product workspace used to infer its next step independently in the
browser.  That made it possible for a durable release receipt to say one thing
while an older presentation gate kept every control disabled.  This module is
the single, pure projection for the next user-visible action.
"""

from __future__ import annotations

import math
from typing import Any


SCHEMA_VERSION = "product-workflow-next-action/v1"
FIELD_IMPACT_SCHEMA_VERSION = "product-workflow-field-impacts/v1"


_FIELD_IMPACTS: dict[str, tuple[str, ...]] = {
    "title": ("product_approval", "listing_copy", "release_plan"),
    "category": ("product_approval", "listing_copy", "release_plan"),
    "selected_sku_keys": (
        "product_approval",
        "listing_copy",
        "pricing",
        "release_plan",
    ),
    "sku_label_overrides": (
        "product_approval",
        "listing_copy",
        "release_plan",
    ),
    "cost_cny": ("product_approval", "pricing", "release_plan"),
    "weight_kg": ("product_approval", "pricing", "release_plan"),
    "package_cm": ("product_approval", "pricing", "release_plan"),
    "selected_sites": (
        "pricing",
        "release_plan_targets",
    ),
    "fx_rates": ("pricing", "release_plan"),
    "support_cod": ("pricing", "release_plan"),
    "final_images": ("content_approval", "release_plan"),
    "image_order": ("content_approval", "release_plan"),
    "video_decision": ("content_approval", "release_plan"),
    "storyboard": ("generation_input",),
}

_SUCCESS_TARGET_STATES = frozenset({"SUCCEEDED", "MANUALLY_VERIFIED"})
_MANUAL_TARGET_STATES = frozenset({"SUBMITTED_UNVERIFIED"})
_RECONCILIATION_TARGET_STATES = frozenset(
    {
        "FAILED",
        "RECONCILIATION_REQUIRED",
        "DRAFT_VERIFICATION_REQUIRED",
        "DRAFT_VERSION_CONFLICT",
    }
)


def _positive_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        parsed = float(value)
        return math.isfinite(parsed) and parsed > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _action(
    code: str,
    phase: str,
    label: str,
    detail: str,
    *,
    kind: str = "control",
    control_id: str | None = None,
    href: str | None = None,
    reason_codes: tuple[str, ...] = (),
    terminal: bool = False,
    target_counts: dict[str, int] | None = None,
    focus_target_label: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "code": code,
        "phase": phase,
        "label": label,
        "detail": detail,
        "kind": kind,
        "actionable": not terminal,
        "terminal": terminal,
        "reason_codes": list(reason_codes),
    }
    if control_id:
        result["control_id"] = control_id
    if href:
        result["href"] = href
    if target_counts is not None:
        result["target_counts"] = dict(target_counts)
    if focus_target_label:
        result["focus_target_label"] = focus_target_label
    return result


def _product_facts_ready(product: dict[str, Any]) -> bool:
    dimensions = product.get("package_cm")
    return bool(
        str(product.get("offer_id") or "").strip()
        and str(product.get("title") or "").strip()
        and _positive_number(product.get("cost_cny"))
        and _positive_number(product.get("weight_kg"))
        and isinstance(dimensions, list)
        and len(dimensions) == 3
        and all(_positive_number(value) for value in dimensions)
        and list(product.get("selected_sites") or ())
        and list(product.get("selected_sku_keys") or ())
        and (product.get("fact_evidence") or {}).get("ready") is not False
    )


def _content_ready(content: dict[str, Any]) -> bool:
    blockers = [
        str(value)
        for value in (content.get("blockers") or ())
        if not str(value).startswith("external ")
    ]
    return bool(
        content.get("approved")
        and int(content.get("image_count") or len(content.get("images") or ())) > 0
        and not blockers
    )


def project_product_field_impacts() -> dict[str, Any]:
    """Describe the smallest durable authorities invalidated by each field.

    This projection is shared by the server and UI so a later step never has
    to infer a broader dependency than the backend actually enforces.
    """

    return {
        "schema_version": FIELD_IMPACT_SCHEMA_VERSION,
        "fields": {
            field: list(impacts)
            for field, impacts in _FIELD_IMPACTS.items()
        },
    }


def project_product_workflow_next_action(view: dict[str, Any]) -> dict[str, Any]:
    """Return exactly one safe next action for every product workspace state."""

    product = view.get("product") if isinstance(view.get("product"), dict) else {}
    content = view.get("content") if isinstance(view.get("content"), dict) else {}
    release = (
        view.get("release_v1")
        if isinstance(view.get("release_v1"), dict)
        else {}
    )
    plan = release.get("plan") if isinstance(release.get("plan"), dict) else {}
    durable_plan_exists = bool(plan.get("plan_id"))
    governed_plan_approved = bool(
        durable_plan_exists and release.get("plan_approved")
    )
    offer_id = str(product.get("offer_id") or "").strip()

    if not governed_plan_approved and not _product_facts_ready(product):
        return _action(
            "complete_product_facts",
            "product",
            "补齐并保存商品事实",
            "核对标题、规格、成本、重量、包装、SKU 与目标站点；保存后系统会重新计算下一步。",
            control_id="productFactsPanel",
            reason_codes=("product_facts_incomplete",),
        )

    # Product facts and the content package have independent approval
    # fingerprints. Either may be completed first; ReleasePlan creation still
    # waits for both authorities below.
    if not product.get("actual_product_approved"):
        return _action(
            "approve_product_facts",
            "approval",
            "批准并锁定商品事实",
            "核对商品事实后批准并保存当前 revision；此动作不会发布。",
            control_id="approvalButton",
            reason_codes=("product_approval_required",),
        )

    if not governed_plan_approved and not _content_ready(content):
        return _action(
            "complete_content_review",
            "content",
            "完成内容与图片审核",
            "批准当前已审核的最终图片、顺序与视频决定；若仍有未完成项，系统会明确提示并保留内容工作室入口。",
            kind="content_finalize",
            control_id="nextStepActionButton",
            reason_codes=("content_review_incomplete",),
        )

    recovery_actions = [
        value
        for value in (release.get("recovery_actions") or ())
        if isinstance(value, dict)
    ]
    if not plan.get("plan_id") or (
        not release.get("plan_approved")
        and not release.get("eligible_for_plan_approval")
    ):
        if recovery_actions:
            recovery = recovery_actions[0]
            return _action(
                str(recovery.get("code") or "repair_release_plan_input"),
                "plan",
                str(recovery.get("label") or "修复发布计划输入"),
                str(
                    recovery.get("detail")
                    or "完成当前阻断项后系统会重新生成可批准的发布计划。"
                ),
                control_id="releaseRecoveryActions",
                reason_codes=("release_plan_input_blocked",),
            )
        return _action(
            "refresh_release_plan",
            "plan",
            "重新读取并生成发布计划",
            "当前计划输入尚未形成一致版本；重新读取后系统会定位唯一阻断项。",
            kind="refresh",
            control_id="refreshButton",
            reason_codes=("release_plan_unavailable",),
        )

    if not release.get("plan_approved"):
        return _action(
            "approve_release_plan",
            "plan",
            "批准当前不可变发布计划",
            "核对目标、来源、售价与费用后勾选确认；批准只保存计划，不会发布。",
            control_id="releasePlanCheckbox",
            reason_codes=("release_plan_approval_required",),
        )

    if not release.get("miaoshou_prepared"):
        overwrite = (
            release.get("common_overwrite_review")
            if isinstance(release.get("common_overwrite_review"), dict)
            else {}
        )
        if overwrite.get("status") == "MISMATCH":
            return _action(
                "review_miaoshou_common_mismatch",
                "sync",
                "处理妙手 COMMON 版本差异",
                "先核对差异与回读证据；仅在身份完全一致时才会开放一次性覆盖确认。",
                control_id="commonOverwritePanel",
                reason_codes=("miaoshou_common_mismatch",),
            )
        return _action(
            "prepare_miaoshou_common",
            "sync",
            "同步并回读妙手待发布商品",
            "勾选独立确认后执行一次 COMMON 写入与官方回读；不会提交任何店铺。",
            control_id="prepareMiaoshouCheckbox",
            reason_codes=("miaoshou_common_not_verified",),
        )

    run = release.get("run") if isinstance(release.get("run"), dict) else {}
    targets = [
        value for value in (run.get("targets") or ()) if isinstance(value, dict)
    ]
    channel_targets = [
        value
        for value in targets
        if value.get("target_label") != "miaoshou:COMMON"
    ]
    recovery_actions = [
        value
        for value in (release.get("target_recovery_actions") or ())
        if isinstance(value, dict)
        and str(value.get("target_label") or "").strip()
    ]
    statuses = {str(value.get("status") or "") for value in channel_targets}
    if recovery_actions:
        target_counts = {
            "running": sum(
                value.get("action_kind") == "READONLY_RECONCILE"
                and value.get("status") == "RUNNING"
                for value in recovery_actions
            ),
            "reconciliation": sum(
                value.get("action_kind")
                in {"READONLY_RECONCILE", "SAFE_REPAIR"}
                for value in recovery_actions
            ),
            "manual_acceptance": sum(
                value.get("action_kind") == "MANUAL_ACCEPT"
                for value in recovery_actions
            ),
            "pending": sum(
                value.get("runnable") is True
                for value in recovery_actions
            ),
            "blocked_capability": sum(
                value.get("action_kind")
                in {"BLOCKED_CAPABILITY", "BLOCKED"}
                for value in recovery_actions
            ),
        }
    else:
        target_counts = {
            "running": sum(
                str(value.get("status") or "") == "RUNNING"
                for value in channel_targets
            ),
            "reconciliation": sum(
                str(value.get("status") or "")
                in _RECONCILIATION_TARGET_STATES
                for value in channel_targets
            ),
            "manual_acceptance": sum(
                str(value.get("status") or "") in _MANUAL_TARGET_STATES
                for value in channel_targets
            ),
            "pending": sum(
                str(value.get("status") or "") == "PENDING"
                for value in channel_targets
            ),
            "blocked_capability": 0,
        }

    if channel_targets and all(
        str(value.get("status") or "") in _SUCCESS_TARGET_STATES
        for value in channel_targets
    ):
        return _action(
            "release_complete",
            "complete",
            "本次发布与验收已完成",
            "所有店铺均已完成官方回读或 Kyle 人工验收；系统不会再次提交。",
            kind="terminal",
            terminal=True,
        )

    if target_counts["running"] > 0:
        return _action(
            "monitor_release_run",
            "channels",
            "等待当前店铺执行完成",
            "系统正在执行或等待持久化回执；只刷新账本，不重复提交。",
            kind="refresh",
            control_id="refreshButton",
            reason_codes=("release_target_running",),
            target_counts=target_counts,
        )

    runnable_count = release.get("runnable_target_count")
    if type(runnable_count) is not int or runnable_count < 0:
        runnable_count = target_counts["pending"]
    if release.get("publish_ready") and runnable_count > 0:
        return _action(
            "publish_selected_targets",
            "channels",
            f"继续发布 {runnable_count} 个安全目标",
            (
                f"本次只执行 {runnable_count} 个从未提交或已证明零写入的目标；"
                f"{target_counts['reconciliation']} 个待对账、"
                f"{target_counts['manual_acceptance']} 个待人工验收目标保持原状态，"
                "不会被重发，也不会阻塞彼此独立的首发目标。"
            ),
            control_id="publishAllCheckbox",
            reason_codes=("runnable_release_targets_available",),
            target_counts=target_counts,
        )

    reconciliation_actions = [
        value
        for value in recovery_actions
        if value.get("action_kind")
        in {"READONLY_RECONCILE", "SAFE_REPAIR"}
    ]
    if reconciliation_actions or (
        not recovery_actions and statuses & _RECONCILIATION_TARGET_STATES
    ):
        first_reconciliation_target = next(
            (
                str(value.get("target_label") or "")
                for value in (
                    reconciliation_actions
                    if reconciliation_actions
                    else channel_targets
                )
                if (
                    value in reconciliation_actions
                    or str(value.get("status") or "")
                    in _RECONCILIATION_TARGET_STATES
                )
            ),
            "",
        )
        return _action(
            "resolve_release_reconciliation",
            "reconcile",
            "查看失败与对账项",
            (
                f"当前有 {target_counts['reconciliation']} 个失败、草稿冲突或"
                f"回读不确定目标，另有 {target_counts['manual_acceptance']} 个"
                f"待人工验收、{target_counts['pending']} 个尚未执行。"
                "先按下方单店证据处置；系统不会一键重发或掩盖其他状态。"
            ),
            kind="manual",
            control_id="releaseRunLedger",
            reason_codes=("release_reconciliation_required",),
            target_counts=target_counts,
            focus_target_label=first_reconciliation_target,
        )

    manual_actions = [
        value
        for value in recovery_actions
        if value.get("action_kind") == "MANUAL_ACCEPT"
    ]
    if manual_actions or (
        not recovery_actions and statuses & _MANUAL_TARGET_STATES
    ):
        first_manual_target = next(
            (
                str(value.get("target_label") or "")
                for value in (
                    manual_actions if manual_actions else channel_targets
                )
                if (
                    value in manual_actions
                    or str(value.get("status") or "")
                    in _MANUAL_TARGET_STATES
                )
            ),
            "",
        )
        return _action(
            "complete_manual_acceptance",
            "reconcile",
            "完成人工验收",
            (
                f"已有 {target_counts['manual_acceptance']} 个店铺提交但无授权"
                f"官方回读；在下方逐项验收。另有 {target_counts['pending']} 个"
                "尚未执行目标，系统不会自动重发已提交店铺。"
            ),
            kind="manual",
            control_id="releaseRunLedger",
            reason_codes=("manual_acceptance_required",),
            target_counts=target_counts,
            focus_target_label=first_manual_target,
        )

    capability_actions = [
        value
        for value in recovery_actions
        if value.get("action_kind") in {"BLOCKED_CAPABILITY", "BLOCKED"}
    ]
    first_capability_target = (
        str(capability_actions[0].get("target_label") or "")
        if capability_actions
        else ""
    )
    adapter_blockers = [
        str(value) for value in (release.get("adapter_blockers") or ()) if value
    ]
    return _action(
        "resolve_release_capability",
        "channels",
        "查看发布能力阻断",
        (
            adapter_blockers[0]
            if adapter_blockers
            else "当前计划已批准，但服务端尚未给出可执行发布门；请刷新后查看精确阻断。"
        ),
        kind="manual",
        control_id=(
            "releaseRunLedger" if first_capability_target else "publishPanel"
        ),
        reason_codes=(
            "adapter_capability_blocked"
            if adapter_blockers or capability_actions
            else "release_preflight_blocked",
        ),
        target_counts=target_counts,
        focus_target_label=first_capability_target or None,
    )


def assert_no_dead_end(action: dict[str, Any]) -> None:
    """Raise if a non-terminal projection cannot lead anywhere."""

    if action.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("workflow next action schema is invalid")
    if action.get("terminal") is True:
        if action.get("actionable") is not False:
            raise ValueError("terminal workflow action must not be actionable")
        return
    if action.get("actionable") is not True:
        raise ValueError("non-terminal workflow state must be actionable")
    if action.get("kind") == "link":
        if not str(action.get("href") or "").strip():
            raise ValueError("link action must provide href")
    elif not str(action.get("control_id") or "").strip():
        raise ValueError("non-terminal workflow action must provide control_id")
    if (
        action.get("control_id") == "releaseRunLedger"
        and action.get("code")
        in {
            "resolve_release_reconciliation",
            "complete_manual_acceptance",
            "resolve_release_capability",
        }
        and not str(action.get("focus_target_label") or "").strip()
    ):
        raise ValueError(
            "release-ledger action must identify the target it focuses"
        )
