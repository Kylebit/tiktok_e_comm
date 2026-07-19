"""Sourcing 通用工具（无外部 API 依赖）。"""

from __future__ import annotations

import re


def parse_offer_id(url_or_id: str) -> str:
    """从 1688 链接或任意字符串中提取 offer_id。"""
    raw = (url_or_id or "").strip()
    if re.fullmatch(r"\d+", raw):
        return raw
    m = re.search(r"offer/(\d+)", raw)
    if m:
        return m.group(1)
    m = re.search(r"(\d{8,})", raw)
    return m.group(1) if m else ""
