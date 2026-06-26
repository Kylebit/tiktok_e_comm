"""解析飞书审批卡 modify_note（尺寸/重量）。"""
from __future__ import annotations

import re


_DIM = re.compile(
    r"(\d+(?:\.\d+)?)\s*[×xX\*]\s*(\d+(?:\.\d+)?)\s*[×xX\*]\s*(\d+(?:\.\d+)?)",
)
_WEIGHT_G = re.compile(r"(\d+(?:\.\d+)?)\s*g\b", re.I)
_WEIGHT_KG = re.compile(r"(\d+(?:\.\d+)?)\s*kg\b", re.I)


def parse_modify_note(note: str) -> dict:
    """返回 {l,w,h,weight_kg,source} 子集。"""
    text = (note or "").strip()
    if not text:
        return {}
    out: dict = {}
    m = _DIM.search(text)
    if m:
        l, w, h = (int(round(float(x))) for x in m.groups())
        out["l"], out["w"], out["h"] = l, w, h
        out["source"] = f"feishu manual {l}×{w}×{h} cm"
        out["volumetric_confirmed"] = True
    mg = _WEIGHT_G.search(text)
    if mg:
        out["weight_kg"] = round(float(mg.group(1)) / 1000, 3)
    else:
        mk = _WEIGHT_KG.search(text)
        if mk:
            out["weight_kg"] = round(float(mk.group(1)), 3)
    return out
