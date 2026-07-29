"""Run one exact, user-authorized one-click release and emit a redacted receipt.

This operational helper deliberately fetches the current immutable identity
immediately before POST.  It never prints the confirmation token or raw
marketplace evidence and it never retries the request.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import urllib.error
import urllib.request


def _request_json(url: str, *, body: dict | None = None, timeout: int) -> dict:
    data = None
    headers = {
        "Accept": "application/json",
        "Origin": "http://127.0.0.1:8765",
    }
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "http_status": response.status,
                "payload": json.load(response),
            }
    except urllib.error.HTTPError as error:
        return {
            "http_status": error.code,
            "payload": json.load(error),
        }


def _redacted(result: dict, *, offer_id: str) -> dict:
    payload = result.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    run = payload.get("run")
    run = run if isinstance(run, dict) else {}
    targets = []
    for row in run.get("targets") or ():
        if not isinstance(row, dict):
            continue
        label = str(row.get("target_label") or "")
        if label == "miaoshou:COMMON":
            continue
        latest = row.get("latest_failure_evidence")
        latest = latest if isinstance(latest, dict) else {}
        evidence = latest.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        readback = row.get("readback")
        readback = readback if isinstance(readback, dict) else {}
        readback_evidence = readback.get("evidence")
        readback_evidence = (
            readback_evidence
            if isinstance(readback_evidence, dict)
            else {}
        )
        targets.append(
            {
                "target_label": label,
                "status": row.get("status"),
                "attempts": row.get("attempts"),
                "has_external_id": bool(row.get("external_id")),
                "error": row.get("error"),
                "failure_reason": evidence.get("reason"),
                "failure_writes": list(
                    evidence.get("external_writes_performed") or ()
                ),
                "readback_verified": (
                    readback_evidence.get("verified") is True
                ),
                "readback_writes": list(
                    readback_evidence.get("external_writes_performed")
                    or ()
                ),
            }
        )
    return {
        "offer_id": offer_id,
        "http_status": result.get("http_status"),
        "ok": payload.get("ok"),
        "code": payload.get("code"),
        "message": payload.get("message"),
        "error": payload.get("error"),
        "blocked_target": payload.get("blocked_target"),
        "external_writes_performed": list(
            payload.get("external_writes_performed") or ()
        ),
        "run_status": run.get("status"),
        "targets": targets,
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: probe.py OFFER_ID OUTPUT_JSON")
    offer_id = str(sys.argv[1]).strip()
    output_path = Path(sys.argv[2]).resolve()
    dashboard_result = _request_json(
        (
            "http://127.0.0.1:8765/api/product-workspace/dashboard"
            f"?offer_id={offer_id}"
        ),
        timeout=30,
    )
    dashboard = dashboard_result.get("payload")
    dashboard = dashboard if isinstance(dashboard, dict) else {}
    release = dashboard.get("release_v1")
    release = release if isinstance(release, dict) else {}
    plan = release.get("plan")
    plan = plan if isinstance(plan, dict) else {}
    product = dashboard.get("product")
    product = product if isinstance(product, dict) else {}
    body = {
        "offer_id": offer_id,
        "seller_sku": str(plan.get("seller_sku") or ""),
        "publication_targets": list(plan.get("targets") or ()),
        "plan_id": str(plan.get("plan_id") or ""),
        "confirmation_token": str(plan.get("confirmation_token") or ""),
        "confirm_publish": True,
    }
    if (
        not release.get("publish_ready")
        or not body["seller_sku"]
        or not body["publication_targets"]
        or not body["plan_id"]
        or not body["confirmation_token"]
        or str(product.get("offer_id") or offer_id) != offer_id
    ):
        output_path.write_text(
            json.dumps(
                {
                    "offer_id": offer_id,
                    "http_status": dashboard_result.get("http_status"),
                    "ok": False,
                    "error": "current immutable release identity is not ready",
                    "external_writes_performed": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return 2
    result = _request_json(
        "http://127.0.0.1:8765/api/product-workspace/publish",
        body=body,
        timeout=900,
    )
    output_path.write_text(
        json.dumps(
            _redacted(result, offer_id=offer_id),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
