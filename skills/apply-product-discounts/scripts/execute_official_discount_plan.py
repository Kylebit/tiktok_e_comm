"""Migration guard for historical, unapproved batch action files.

Execution belongs to OneClickReleaseStore + its registered postpublish action.
This entry does not accept READY, an action hash, or a command-line confirmation
as a substitute for the immutable plan's approval and durable target claim.
"""
from __future__ import annotations
import argparse
import json


def migration_result(channel=None):
    return {
        "schema_version": "postpublish-discount-migration/v1",
        "status": "BLOCKED_LEGACY_DIRECT_EXECUTION",
        "reason_code": "GOVERNED_POSTPUBLISH_ACTION_REQUIRED",
        "channel": channel,
        "external_write_count": 0,
        "execution_owner": "shared_platform.oneclick_release_controlplane.OneClickReleaseWorker",
        "adapter": "postpublish_promotion",
        "capabilities": {"tiktok_livelyhive_sea": "REGISTERED_GOVERNED_ADAPTER",
                         "shopee": "ADAPTER_REQUIRED", "miaoshou_browser": "EXPLICIT_BROWSER_WORKFLOW_REQUIRED"},
        "next_step": "Use the approved Product Center plan and its postpublish action; reconcile any prior UNKNOWN before resuming.",
    }


def execute_tiktok(*args, **kwargs):
    return migration_result("tiktok")


def execute_shopee(*args, **kwargs):
    return migration_result("shopee")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan")
    parser.add_argument("--channel", choices=("tiktok", "shopee", "all"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--target", action="append")
    args = parser.parse_args(argv)
    print(json.dumps(migration_result(args.channel), ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
