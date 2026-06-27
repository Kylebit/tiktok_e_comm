"""人工修正的物流重量：覆盖自动扫描结果，用于已确认自动扫描数据有误的 SKU。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "data" / "weight_overrides.json"


def load_overrides() -> dict[str, dict]:
    if not PATH.is_file():
        return {}
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def set_override(match_key: str, weight_g: int, *, reason: str = "") -> dict:
    data = load_overrides()
    data[match_key] = {
        "weight_g": int(weight_g),
        "reason": reason,
        "weight_source": "manual_override",
    }
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data[match_key]


def remove_override(match_key: str) -> bool:
    data = load_overrides()
    if match_key not in data:
        return False
    del data[match_key]
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
