"""TikTok Shop UK 佣金率（按类目）+ VAT 20%。"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMISSION_PATH = ROOT / "config" / "uk_commission_rates.json"


@lru_cache(maxsize=1)
def load_commission_config() -> dict:
    with COMMISSION_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def vat_rate_pct() -> float:
    return float(load_commission_config().get("vat_rate_pct") or 20)


def vat_from_gross_gbp(sale_gbp: float) -> float:
    """前台售价为 VAT 含税价：VAT = sale × rate / (100 + rate)。"""
    rate = vat_rate_pct()
    return round(float(sale_gbp) * rate / (100.0 + rate), 2)


def default_commission_pct() -> float:
    return float(load_commission_config().get("default_commission_pct") or 9)


@lru_cache(maxsize=1)
def _index() -> tuple[dict[tuple[str, str], float], dict[str, float]]:
    cfg = load_commission_config()
    exact: dict[tuple[str, str], float] = {}
    for row in cfg.get("entries") or []:
        cat = _norm(row.get("category"))
        sub = _norm(row.get("sub_category"))
        exact[(cat, sub)] = float(row["commission_pct"])
    cat_all = {_norm(k): float(v) for k, v in (cfg.get("category_all_rates") or {}).items()}
    return exact, cat_all


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).casefold()


def commission_pct(*, category: str = "", sub_category: str = "") -> float:
    """按 Category + Sub-category 查佣金；缺省 9%。"""
    cat = _norm(category)
    sub = _norm(sub_category) or "all"
    exact, cat_all = _index()
    if cat and sub:
        hit = exact.get((cat, sub))
        if hit is not None:
            return hit
    if cat:
        hit = exact.get((cat, "all"))
        if hit is not None:
            return hit
        hit = cat_all.get(cat)
        if hit is not None:
            return hit
    return default_commission_pct()


def commission_label(*, category: str = "", sub_category: str = "") -> str:
    pct = commission_pct(category=category, sub_category=sub_category)
    parts = [p for p in (category, sub_category) if p]
    if parts:
        return f"{' / '.join(parts)} @ {pct:g}%"
    return f"default @ {pct:g}%"


def extract_category_from_product(product: dict | None) -> tuple[str, str]:
    """从 TikTok product detail 尽量提取类目名（用于佣金 lookup）。"""
    if not isinstance(product, dict):
        return "", ""
    for key in ("category_chains", "category_list", "categories"):
        chains = product.get(key)
        if isinstance(chains, list) and chains:
            names = []
            for node in chains:
                if isinstance(node, dict):
                    n = node.get("local_name") or node.get("name") or node.get("category_name")
                    if n:
                        names.append(str(n))
                elif isinstance(node, str):
                    names.append(node)
            if names:
                return names[0], names[-1] if len(names) > 1 else "All"
    leaf = product.get("category") or product.get("leaf_category") or {}
    if isinstance(leaf, dict):
        return (
            str(leaf.get("parent_name") or leaf.get("category_name") or ""),
            str(leaf.get("category_name") or leaf.get("name") or "All"),
        )
    return "", ""
