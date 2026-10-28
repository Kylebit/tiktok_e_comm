from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
from pathlib import Path
import sys


def _bound_source(repository: Path | None):
    script = Path(__file__).resolve()
    if repository is None:
        repository = script.parents[3]
        if script != repository / "skills/apply-product-discounts/scripts/inspect_discount_plan.py" or not (repository / ".git").exists():
            raise ValueError("installed Skill requires --repository with an absolute Git root")
    elif not repository.is_absolute():
        raise ValueError("--repository must be an absolute Git root")
    root = repository.resolve(strict=True)
    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            text=True, encoding="utf-8", stderr=subprocess.DEVNULL,
        ).strip()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("--repository must select the exact Git top-level")
    if not (root / "AGENTS.md").is_file():
        raise ValueError("selected repository has no AGENTS.md")
    sys.path.insert(0, str(root))
    policy = importlib.import_module("shared_platform.postpublish_promotions")
    adapters = importlib.import_module("domains.channel_operations.oneclick_release_adapters")
    for module in (policy, adapters):
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise ValueError("imported policy/adapter source differs from --repository")
    registration = adapters.production_adapter_registry().get("postpublish_promotion")
    for name in ("modules.tiktok.oneclick_promotion", "modules.shopee.oneclick_promotion"):
        module = sys.modules.get(name)
        if module is not None and not Path(module.__file__).resolve().is_relative_to(root):
            raise ValueError("imported promotion source differs from --repository")
    def source_ref(path):
        return {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    source = {
        "repository": str(root), "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain=v1", "-uall")),
        "script": source_ref(script), "policy": source_ref(policy.__file__),
        "registration": source_ref(adapters.__file__),
        "adapter_policy_digest": getattr(registration, "policy_digest", None),
    }
    return policy, registration, source


def inspect_plan(payload: dict, *, repository: Path | None = None) -> dict:
    policy_module, registration, source = _bound_source(repository)
    approved_promotion_action_policy = policy_module.approved_promotion_action_policy
    enabled_promotion_action_targets = policy_module.enabled_promotion_action_targets
    promotion_target_policy = policy_module.promotion_target_policy
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
                "execution_capability": (
                    "REGISTERED_GOVERNED_ADAPTER"
                    if target["execution_surface"] in {"tiktok_official", "shopee_official"}
                    and registration is not None and action in registration.target_labels
                    and registration.preparation_available and registration.dispatch_available
                    else "ADAPTER_OR_EXPLICIT_BROWSER_WORKFLOW_REQUIRED"
                ),
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
        "source": source,
        "actions": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, help="Absolute verified Git root; required for a personal installed copy")
    parser.add_argument("--plan-json", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.plan_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("immutable plan must be a JSON object")
        result = inspect_plan(payload, repository=args.repository)
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.CalledProcessError) as error:
        result = {"schema_version": "postpublish-discount-inspection/v1", "ready": False,
                  "inspection_only": True, "execution_authorized": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ready") is True else 1

if __name__ == "__main__":
    raise SystemExit(main())
