from __future__ import annotations

from typing import Any, Mapping


USER_LABELS = {
    "SUCCEEDED": "发布成功",
    "PROCESSING": "平台处理中",
    "PARTIAL": "部分成功",
    "FAILED": "发布失败",
}


def classify(
    dispatch: Mapping[str, Any], readback: Mapping[str, Any]
) -> dict[str, Any]:
    verified = readback.get("verified") is True
    complete = readback.get("complete") is True
    verified_count = _count(readback.get("verified_count"))
    expected_count = _count(readback.get("expected_count"))
    status = str(readback.get("status") or "").upper()
    if (
        dispatch.get("write_outcome") == "REJECTED"
        and readback.get("provider") == "miaoshou_collectbox_receipt"
    ):
        code = "FAILED"
    elif verified and complete:
        code = "SUCCEEDED"
    elif verified_count > 0 and expected_count > verified_count:
        code = "PARTIAL"
    elif status in {"PENDING", "PROCESSING", "UNAVAILABLE"} and dispatch.get("accepted") is True:
        code = "PROCESSING"
    elif dispatch.get("accepted") is True and status == "DELETED":
        code = "FAILED"
    elif dispatch.get("accepted") is True and readback.get("exists") is False:
        code = "FAILED"
    elif dispatch.get("accepted") is True and readback.get("mismatch") is True:
        code = "PARTIAL" if verified_count else "FAILED"
    else:
        code = "FAILED"
    return {
        "code": code,
        "label_zh": USER_LABELS[code],
        "retry_safe": readback.get("retry_safe") is True,
    }


def _count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0
