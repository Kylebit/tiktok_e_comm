"""读取 MX 历史人工覆盖数据（仅保留定价/物流事实兼容）。"""
from __future__ import annotations

import json
from pathlib import Path

from core.config import ROOT

OVERRIDES_PATH = ROOT / "data" / "mx_confirm" / "manual_overrides.json"
LEGACY_OVERRIDES_PATH = ROOT / "data" / "mx_confirm" / "feishu_manual_overrides.json"


def load_overrides() -> dict[str, dict]:
    source = OVERRIDES_PATH if OVERRIDES_PATH.is_file() else LEGACY_OVERRIDES_PATH
    if not source.is_file():
        return {}
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_override(match_key: str, patch: dict, *, note: str = "") -> dict:
    mk = str(match_key).zfill(4)[-4:]
    all_ = load_overrides()
    prev = all_.get(mk, {})
    merged = {**prev, **patch}
    if note:
        merged["note"] = note
    all_[mk] = merged
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(json.dumps(all_, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def known_for_match_key(mk: str, seller_sku: str, *, base_known: dict) -> dict:
    """base_known = KNOWN_LOGISTICS or KNOWN_BY_MATCH_KEY 条目。"""
    ov = load_overrides().get(str(mk).zfill(4)[-4:], {})
    if not ov:
        return base_known
    return {**base_known, **ov}
