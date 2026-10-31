"""Bounded orchestration for independently authorized native platform scopes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import time


def run_native_platform_partitions(platforms, invoke, *, max_parallel=3):
    """Invoke every partition independently and preserve its real result.

    ``invoke`` creates its own original native execution context inside its
    worker thread. This helper grants no authority and supplies no payload,
    budget, approval, retry, provider client, or success fallback.
    """
    selected = tuple(platforms)
    if (not selected or len(selected) != len(set(selected))
            or any(p not in {"TIKTOK", "SHOPEE", "OZON"} for p in selected)):
        raise ValueError("native platform partitions must be exact unique platforms")
    if type(max_parallel) is not int or not 1 <= max_parallel <= 3:
        raise ValueError("max_parallel must be an integer from 1 to 3")
    if not callable(invoke):
        raise TypeError("native partition consumer must be callable")
    def run(platform):
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        try:
            code, result = invoke(platform)
        except Exception as error:
            # Do not synthesize zero-write or success. The original consumer's
            # durable target/intent records remain the reconciliation source.
            code, result = 409, {"ok": False, "code": "native_platform_partition_exception",
                "platform": platform, "outcome_unknown": True,
                "error_type": type(error).__name__,
                "new_human_review_required": False,
                "next_action": "RECONCILE_ORIGINAL_PARTITION_READONLY"}
        if isinstance(result, dict):
            result = {**result, "orchestration_span": {
                "schema_version": "publication-orchestration-span/v1", "platform": platform,
                "kind": "NATIVE_PLATFORM_WORKER", "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": time.monotonic() - started,
                "includes_provider_wait": True,
                "next_action": result.get("next_action"),
                "blocked_by": result.get("code") if result.get("outcome_unknown") else None}}
        return code, result
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(selected))) as pool:
        futures = {p: pool.submit(run, p) for p in selected}
        return {p: futures[p].result() for p in selected}
