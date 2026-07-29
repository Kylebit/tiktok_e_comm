from __future__ import annotations

import re
from pathlib import Path

from modules.products import server
from shared_platform import oneclick_release_controlplane as controlplane


_WORKSPACE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "web"
    / "static"
    / "product_workspace.js"
)


def _ui_oneclick_actions() -> set[str]:
    script = _WORKSPACE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"const ONECLICK_ACTIONS = new Set\(\[(.*?)\]\);",
        script,
        flags=re.DOTALL,
    )
    assert match is not None, "workspace must expose one canonical action vocabulary"
    return set(re.findall(r'"([a-z0-9_]+)"', match.group(1)))


def test_server_next_actions_are_all_understood_by_the_workspace() -> None:
    ui_actions = _ui_oneclick_actions()
    server_actions = set(server._ONECLICK_RECOVERY_ACTION_PRIORITY)

    cases = [
        (controlplane.PENDING, None, "NONE", None),
        (controlplane.PREPARING, None, "NONE", None),
        (controlplane.READY, None, "NONE", None),
        (controlplane.READY, None, "WAITING", None),
        (controlplane.READY, None, "BLOCKED", None),
        (controlplane.DISPATCHING, None, "NONE", None),
        (controlplane.SUBMITTED_UNVERIFIED, None, "NONE", None),
        (controlplane.SUCCEEDED_MANUAL_REVIEW, None, "NONE", None),
        (controlplane.FAILED_PRE_SUBMIT, None, "NONE", None),
        (
            controlplane.FAILED_PRE_SUBMIT,
            controlplane.SAFE_ACTION_REQUIRED,
            "NONE",
            "SAFE_ACTION",
        ),
        (controlplane.RECONCILIATION_REQUIRED, None, "NONE", None),
        (controlplane.BLOCKED_AUTH, None, "NONE", "AUTH"),
        (controlplane.BLOCKED_INVENTORY, None, "NONE", "INVENTORY"),
        (
            controlplane.BLOCKED_CAPABILITY,
            controlplane.BLOCKED_CAPABILITY,
            "NONE",
            "CONTENT",
        ),
        (
            controlplane.BLOCKED_CAPABILITY,
            controlplane.BLOCKED_CAPABILITY,
            "NONE",
            "LOGISTICS",
        ),
        (
            controlplane.BLOCKED_CAPABILITY,
            controlplane.BLOCKED_CAPABILITY,
            "NONE",
            "CAPABILITY",
        ),
        (
            controlplane.BLOCKED_SOURCE_IDENTITY,
            None,
            "NONE",
            "SYSTEMIC_IDENTITY",
        ),
        (controlplane.BLOCKED_SKU_LINEAGE, None, "NONE", None),
    ]
    emitted: set[str] = set()
    for status, capability, dependency, reason_category in cases:
        action = controlplane._next_action(
            status,
            capability,
            dependency_state=dependency,
            reason_category=reason_category,
        )
        assert action is not None, (
            f"nonterminal state {status}/{capability}/{reason_category} "
            "must never become a UI dead end"
        )
        emitted.add(action)

    assert server_actions <= ui_actions
    assert emitted <= ui_actions


def test_safe_action_classification_never_becomes_a_generic_retry() -> None:
    action = controlplane._next_action(
        controlplane.FAILED_PRE_SUBMIT,
        controlplane.SAFE_ACTION_REQUIRED,
        dependency_state="NONE",
        reason_category="SAFE_ACTION",
    )
    assert action == "perform_governed_safe_action"
