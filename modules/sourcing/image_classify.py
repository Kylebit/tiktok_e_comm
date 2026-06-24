"""1688 原图启发式分类（路径 A：supplier 优先选槽）。"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from core.config import ROOT

# 分类标签 → 中文展示
CLASS_LABELS: dict[str, str] = {
    "main": "主图",
    "scene": "场景",
    "size": "尺寸/规格",
    "detail": "细节特写",
    "marketing": "卖点长图",
    "divider": "分隔/装饰",
    "unknown": "未分类",
}

CLASS_COLORS: dict[str, str] = {
    "main": "#2563eb",
    "scene": "#059669",
    "size": "#d97706",
    "detail": "#7c3aed",
    "marketing": "#db2777",
    "divider": "#94a3b8",
    "unknown": "#64748b",
}


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    while i < len(data) - 8:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = struct.unpack(">H", data[i + 5 : i + 7])[0]
            w = struct.unpack(">H", data[i + 7 : i + 9])[0]
            return w, h
        if marker in (0xD8, 0xD9):
            break
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + seg_len
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w = struct.unpack(">I", data[16:20])[0]
        h = struct.unpack(">I", data[20:24])[0]
        return w, h
    return None


def image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        head = path.read_bytes()[:65536]
    except OSError:
        return None
    if head[:2] == b"\xff\xd8":
        return _jpeg_size(head)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_size(head)
    return None


def classify_local_image(rel_path: str, *, group: str = "detail") -> dict[str, Any]:
    """对单张本地原图打标签。group: main | detail"""
    path = ROOT / rel_path.replace("\\", "/")
    name = path.name.lower()
    size_bytes = path.stat().st_size if path.is_file() else 0
    dims = image_dimensions(path) if path.is_file() else None
    w, h = dims if dims else (0, 0)
    aspect = (w / h) if w and h else 0.0
    signals: list[str] = []

    if group == "main":
        return {
            "path": rel_path,
            "group": "main",
            "class": "main",
            "label": CLASS_LABELS["main"],
            "confidence": 0.95,
            "width": w,
            "height": h,
            "bytes": size_bytes,
            "signals": ["主图轮播"],
        }

    # 极小文件多为分隔条/占位
    if size_bytes and size_bytes < 8000:
        signals.append("文件极小")
        return _entry(rel_path, "divider", 0.85, w, h, size_bytes, signals)

    if aspect >= 4.0:
        signals.append("超宽横幅")
        cls = "divider" if h and h < 80 else "marketing"
        return _entry(rel_path, cls, 0.8, w, h, size_bytes, signals)

    # 1688 尺寸标注图：竖长或扁宽参数条
    if aspect >= 1.55 and h and h < 480:
        signals.append("扁宽尺寸条")
        return _entry(rel_path, "size", 0.82, w, h, size_bytes, signals)

    if aspect >= 1.75:
        signals.append("竖长/尺寸")
        return _entry(rel_path, "size", 0.78, w, h, size_bytes, signals)

    # 750×550 一类多格场景图
    if 1.25 <= aspect <= 1.45 and h and h >= 500:
        signals.append("多格场景")
        return _entry(rel_path, "scene", 0.72, w, h, size_bytes, signals)

    if aspect and 0.85 <= aspect <= 1.15:
        signals.append("近方图")
        if size_bytes > 100_000:
            return _entry(rel_path, "scene", 0.68, w, h, size_bytes, signals)
        return _entry(rel_path, "detail", 0.65, w, h, size_bytes, signals)

    if aspect and aspect <= 0.75:
        signals.append("横图")
        return _entry(rel_path, "marketing", 0.65, w, h, size_bytes, signals)

    if 40_000 <= size_bytes <= 130_000:
        signals.append("中等文件")
        return _entry(rel_path, "detail", 0.58, w, h, size_bytes, signals)

    signals.append("默认")
    return _entry(rel_path, "unknown", 0.4, w, h, size_bytes, signals)


def _entry(
    rel_path: str,
    cls: str,
    confidence: float,
    w: int,
    h: int,
    size_bytes: int,
    signals: list[str],
) -> dict[str, Any]:
    return {
        "path": rel_path,
        "group": "detail",
        "class": cls,
        "label": CLASS_LABELS.get(cls, cls),
        "confidence": round(confidence, 2),
        "width": w,
        "height": h,
        "bytes": size_bytes,
        "signals": signals,
    }


def classify_assets(raw_main: list[str], raw_detail: list[str]) -> dict[str, Any]:
    """分类全部已下载原图，并给出槽位推荐映射。"""
    main_items = [classify_local_image(p, group="main") for p in raw_main]
    detail_items = [classify_local_image(p, group="detail") for p in raw_detail]

    by_class: dict[str, list[dict]] = {}
    for item in detail_items:
        by_class.setdefault(item["class"], []).append(item)

    slot_hints = _slot_hints(main_items, by_class)
    return {
        "main": main_items,
        "detail": detail_items,
        "by_class": {k: [i["path"] for i in v] for k, v in by_class.items()},
        "slot_hints": slot_hints,
        "summary": {
            cls: len(by_class.get(cls, []))
            for cls in ("size", "scene", "detail", "marketing", "divider", "unknown")
        },
    }


def _slot_hints(main_items: list[dict], by_class: dict[str, list[dict]]) -> dict[int, dict]:
    """v2 槽位默认 supplier 推荐（可被人工覆盖）。"""
    used: set[str] = set()

    def _take(cls: str, fallback: str | None = None) -> str | None:
        for key in (cls, fallback) if fallback else (cls,):
            if not key:
                continue
            for item in by_class.get(key, []):
                p = item["path"]
                if p not in used:
                    used.add(p)
                    return p
        return None

    hints: dict[int, dict] = {}
    hints[2] = {"class": "scene", "path": _take("scene"), "source": "supplier"}
    hints[3] = {"class": "size", "path": _take("size"), "source": "supplier"}
    hints[4] = {"class": "detail", "path": _take("detail"), "source": "supplier"}
    hints[5] = {
        "class": "detail",
        "path": _take("detail", "marketing"),
        "source": "supplier",
    }
    if len(main_items) > 1:
        p = main_items[1]["path"]
        used.add(p)
        hints[6] = {"class": "main", "path": p, "source": "supplier"}
    hints[7] = {
        "class": "marketing",
        "path": _take("marketing"),
        "source": "supplier",
        "note": "含中文时可路径 B 做 OCR 翻译",
    }
    return hints


def image_pool(draft: dict) -> list[dict]:
    """可供槽位下拉选择的全部图片。"""
    assets = draft.get("assets") or {}
    pool: list[dict] = []
    seen: set[str] = set()

    def _add(path: str, label: str, cls: str, source: str) -> None:
        if not path or path in seen:
            return
        seen.add(path)
        pool.append({"path": path, "label": label, "class": cls, "source": source})

    for item in (assets.get("classification") or {}).get("main") or []:
        _add(item["path"], f"主图 {Path(item['path']).name}", "main", "supplier")
    for item in (assets.get("classification") or {}).get("detail") or []:
        _add(
            item["path"],
            f"{item.get('label') or item['class']} · {Path(item['path']).name}",
            item.get("class") or "unknown",
            "supplier",
        )
    for c in assets.get("hero_candidates") or []:
        _add(c.get("path") or "", f"AI · {c.get('recipe') or c.get('label')}", "main", "ai")
    for s in assets.get("slots") or []:
        if s.get("path"):
            _add(s["path"], Path(s["path"]).name, s.get("class") or "", s.get("source") or "ai")
    return pool
