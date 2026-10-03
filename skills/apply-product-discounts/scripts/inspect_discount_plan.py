from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared_platform.postpublish_promotions import (  # noqa: E402
    approved_promotion_action_policy,
    enabled_promotion_action_targets,
    promotion_target_policy,
)


def inspect_plan(payload: dict) -> dict:
    targets = payload.get("targets")
    if not isinstance(targets, list) or any(type(value) is not str for value in targets):
        raise ValueError("immutable plan targets are invalid")
    actions = enabled_promotion_action_targets(payload, targets)
    rows = []
    for action in actions:
        target = promotion_target_policy(action)
        try:
            policy = approved_promotion_action_policy(payload, action)
        except ValueError as error:
            rows.append({"target_label": action, "prerequisite_target": target["prerequisite_target"],
                         "ready": False, "error": str(error), "execution_surface": target["execution_surface"]})
            continue
        rows.append(
            {
                "ready": True,
                "target_label": action,
                "prerequisite_target": target["prerequisite_target"],
                "execution_surface": target["execution_surface"],
                "execution_capability": "REGISTERED_GOVERNED_ADAPTER" if target["execution_surface"] == "tiktok_official" else "ADAPTER_OR_EXPLICIT_BROWSER_WORKFLOW_REQUIRED",
                "currency": target["currency"],
                "discount_percent": policy["discount_percent"],
                "action_policy_digest": policy["action_policy_digest"],
            }
        )
    return {
        "schema_version": "postpublish-discount-inspection/v1",
        "ready": bool(rows) and all(row["ready"] for row in rows),
        "inspection_only": True,
        "execution_authorized": False,
        "actions": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-json", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.plan_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("immutable plan must be a JSON object")
        result = inspect_plan(payload)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {"schema_version": "postpublish-discount-inspection/v1", "ready": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ready") is True else 1

if __name__ == "__main__":
    raise SystemExit(main())
