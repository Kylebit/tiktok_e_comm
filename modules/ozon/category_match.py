"""TikTok 类目 + 映射表 + 标题 → Ozon type_id。"""

from __future__ import annotations

import json
import re

from modules.ozon.config import ozon_data_dir
from modules.ozon.listing_text import is_tablecloth_title
from modules.ozon.migrate_attrs import resolve_profile
from modules.ozon.tk_category_map import lookup as map_lookup

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 2}


def load_category_options() -> list[dict]:
    base = ozon_data_dir()
    if not base:
        return []
    path = base / "category_options.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def lookup_category_names(category_id: int, type_id: int) -> dict[str, str]:
    """按 type_id（优先）或 cat_id 查 Ozon 类目中文名。"""
    options = load_category_options()
    entry = next((c for c in options if c.get("type_id") == type_id), None)
    if not entry:
        entry = next((c for c in options if c.get("cat_id") == category_id), None)
    if not entry:
        if type_id == 92692:
            return {"category_name_zh": "毛巾和桌布", "type_name_zh": "桌布"}
        return {"category_name_zh": "", "type_name_zh": ""}
    return {
        "category_name_zh": (entry.get("cat_name_zh") or "").strip(),
        "type_name_zh": (entry.get("type_name_zh") or "").strip(),
    }


def fetch_tk_category_info(product_id: str, shop_cipher: str) -> dict:
    """返回 path, leaf, category_id（TK 叶子类目 ID）。"""
    empty = {"path": "", "leaf": "", "category_id": ""}
    if not product_id or not shop_cipher:
        return empty
    try:
        from core import auth as tk_auth
        from core.api_client import get as tk_get

        token = tk_auth.ensure_valid_token()["access_token"]
        resp = tk_get(
            f"/product/202309/products/{product_id}",
            token,
            {"shop_cipher": shop_cipher},
        )
        detail = resp.get("data") or {}
        chain = detail.get("category_chains") or []
        names = [c.get("local_name") or "" for c in chain if c.get("local_name")]
        leaf = names[-1] if names else ""
        leaf_id = ""
        if chain:
            leaf_id = str(chain[-1].get("id") or "")
        return {"path": " > ".join(names), "leaf": leaf, "category_id": leaf_id}
    except Exception:
        return empty


def fetch_tk_category(product_id: str, shop_cipher: str) -> tuple[str, str]:
    info = fetch_tk_category_info(product_id, shop_cipher)
    return info["path"], info["leaf"]


def score_option(opt: dict, *, title: str, tk_path: str, tk_leaf: str) -> int:
    score = 0
    hay = f"{title} {tk_path} {tk_leaf}".lower()
    for field in ("type_name_zh", "cat_name_zh", "type_name", "cat_name"):
        val = (opt.get(field) or "").strip()
        if not val:
            continue
        vl = val.lower()
        if vl in hay:
            score += 12
        for tok in _tokens(val):
            if len(tok) >= 2 and tok in hay:
                score += 3
    leaf_toks = _tokens(tk_leaf)
    type_toks = _tokens(opt.get("type_name_zh") or "")
    cat_toks = _tokens(opt.get("cat_name_zh") or "")
    overlap = len(leaf_toks & type_toks) + len(leaf_toks & cat_toks)
    score += overlap * 5
    return score


def match_category(
    *,
    title: str,
    tk_path: str = "",
    tk_leaf: str = "",
    tk_category_id: str = "",
    top_n: int = 25,
    auto_threshold: int = 28,
) -> dict:
    # ① 标题强规则（桌布等，优先于易错的 TK 类目映射）
    if is_tablecloth_title(title):
        type_id = 92692
        category_id = 17028730
        profile = "tablecloth"
        opt = next((c for c in load_category_options() if c.get("type_id") == type_id), None)
        return {
            "candidates": [opt] if opt else [],
            "suggested": opt or {"type_id": type_id, "cat_id": category_id},
            "best_score": 999,
            "tk_category_path": tk_path,
            "tk_category_leaf": tk_leaf,
            "tk_category_id": tk_category_id,
            "match_method": "title_tablecloth",
            "type_id": type_id,
            "category_id": category_id,
            "migrate_profile": profile,
        }

    # ② 映射表命中
    mapped = map_lookup(tk_category_id)
    if mapped:
        type_id = int(mapped["type_id"])
        category_id = int(mapped["category_id"])
        profile = mapped.get("profile") or resolve_profile(type_id)
        opt = next((c for c in load_category_options() if c["type_id"] == type_id), None)
        return {
            "candidates": [opt] if opt else [],
            "suggested": opt or {"type_id": type_id, "cat_id": category_id},
            "best_score": 999,
            "tk_category_path": tk_path,
            "tk_category_leaf": tk_leaf,
            "tk_category_id": tk_category_id,
            "match_method": "tk_category_map",
            "type_id": type_id,
            "category_id": category_id,
            "migrate_profile": profile,
        }

    options = load_category_options()
    if not options:
        return {
            "candidates": [],
            "suggested": None,
            "best_score": 0,
            "tk_category_path": tk_path,
            "tk_category_leaf": tk_leaf,
            "tk_category_id": tk_category_id,
            "match_method": "none",
            "migrate_profile": "generic",
        }

    scored = []
    for opt in options:
        s = score_option(opt, title=title, tk_path=tk_path, tk_leaf=tk_leaf)
        scored.append((s, opt))
    scored.sort(key=lambda x: (-x[0], x[1].get("type_id", 0)))

    top = [o for _, o in scored[:top_n]]
    best_score, best_opt = scored[0] if scored else (0, None)
    suggested = best_opt if best_score >= auto_threshold else None
    type_id = int(suggested["type_id"]) if suggested else None
    profile = resolve_profile(type_id) if type_id else "generic"

    return {
        "candidates": top,
        "suggested": suggested,
        "best_score": best_score,
        "tk_category_path": tk_path,
        "tk_category_leaf": tk_leaf,
        "tk_category_id": tk_category_id,
        "match_method": "rule_auto" if suggested else "rule_narrow_ai",
        "type_id": type_id,
        "category_id": int(suggested["cat_id"]) if suggested else None,
        "migrate_profile": profile,
    }
