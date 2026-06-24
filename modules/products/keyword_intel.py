"""热搜词 + 竞品标题：CSV 词库（可扩展第三方 API）。"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from core.config import ROOT, get

# 竞品标题用 || 分隔（标题内可有逗号）；热搜词用 | 分隔
COMPETITOR_SEP = "||"
KEYWORD_SEP = "|"
DEFAULT_CATEGORY = "*"


def _keywords_dir() -> Path:
    rel = get("keywords.dir", "data/keywords")
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def _csv_path(region: str) -> Path:
    return _keywords_dir() / f"{region.upper()}.csv"


def _split_keywords(raw: str) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(KEYWORD_SEP):
        w = re.sub(r"\s+", " ", part.strip())
        if not w or len(w) < 2:
            continue
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _split_competitors(raw: str) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(COMPETITOR_SEP):
        t = re.sub(r"\s+", " ", part.strip())
        if not t or len(t) < 8:
            continue
        key = t.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(t[:255])
    return out


def _load_csv_rows(region: str) -> list[dict]:
    path = _csv_path(region)
    if not path.is_file():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cat = (row.get("category_leaf") or row.get("category") or "").strip()
            rows.append({
                "category_leaf": cat or DEFAULT_CATEGORY,
                "hot_keywords": _split_keywords(row.get("hot_keywords") or ""),
                "competitor_titles": _split_competitors(row.get("competitor_titles") or ""),
            })
    return rows


def _match_score(row_cat: str, product_cat: str, title: str) -> int:
    rc = row_cat.strip().lower()
    pc = product_cat.strip().lower()
    tl = title.lower()
    if rc in ("*", "default", "all", ""):
        return 1
    if pc and rc == pc:
        return 100
    if pc and (rc in pc or pc in rc):
        return 60
    if pc and any(tok in pc for tok in rc.split() if len(tok) > 3):
        return 40
    # 标题里出现类目关键词
    if rc != DEFAULT_CATEGORY and rc in tl:
        return 25
    return 0


def _lookup_csv(
    category_leaf: str,
    region: str,
    current_title: str = "",
    *,
    max_keywords: int = 8,
    max_competitors: int = 5,
) -> dict:
    rows = _load_csv_rows(region)
    if not rows:
        return {
            "hot_keywords": [],
            "competitor_titles": [],
            "matched_categories": [],
            "source": "csv",
            "region": region.upper(),
        }

    scored: list[tuple[int, dict]] = []
    for row in rows:
        score = _match_score(row["category_leaf"], category_leaf, current_title)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: (-x[0], x[1]["category_leaf"]))

    hot: list[str] = []
    comps: list[str] = []
    matched: list[str] = []
    hot_seen: set[str] = set()
    comp_seen: set[str] = set()

    for _, row in scored:
        if row["category_leaf"] not in matched and row["category_leaf"] not in ("*", "default", "all"):
            matched.append(row["category_leaf"])
        for kw in row["hot_keywords"]:
            k = kw.lower()
            if k not in hot_seen:
                hot_seen.add(k)
                hot.append(kw)
        for t in row["competitor_titles"]:
            k = t.lower()[:80]
            if k not in comp_seen:
                comp_seen.add(k)
                comps.append(t)
        if len(hot) >= max_keywords and len(comps) >= max_competitors:
            break

    return {
        "hot_keywords": hot[:max_keywords],
        "competitor_titles": comps[:max_competitors],
        "matched_categories": matched[:5],
        "source": "csv",
        "region": region.upper(),
    }


def _lookup_api(
    category_leaf: str,
    region: str,
    current_title: str = "",
) -> dict:
    """预留：第三方关键词/竞品 API。"""
    raise NotImplementedError("keywords.provider=api 尚未接入，请先用 CSV 或改 provider=csv")


def lookup(
    category_leaf: str,
    region: str,
    current_title: str = "",
) -> dict:
    provider = (get("keywords.provider") or "csv").lower()
    if provider == "api":
        return _lookup_api(category_leaf, region, current_title)
    return _lookup_csv(category_leaf, region, current_title)


def list_regions_with_data() -> list[str]:
    d = _keywords_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem.upper() for p in d.glob("*.csv") if p.is_file())
