"""从本地商品库自动生成 data/keywords/{站点}.csv。"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from core.config import ROOT, get
from core.db import connect, init_db

COMPETITOR_SEP = "||"
KEYWORD_SEP = "|"

REGION_RULES: dict[str, list[tuple[str, re.Pattern]]] = {
    "MY": [
        ("Decorative Stickers", re.compile(r"sticker|pelekat|wallpaper|dinding|vinyl|decal|kertas dinding", re.I)),
        ("Decorative Flowers, Plants & Fruit", re.compile(r"flower|bunga|tiruan|artificial|pokok|vine|anggur|daisy|eucalyptus", re.I)),
        ("Festive Decorations", re.compile(r"table runner|alas meja|taplak|tablecloth|lace|ren|mandala", re.I)),
        ("Hooks & Rails", re.compile(r"hook|cangk|langsir|curtain|rail|penyangkut|rak", re.I)),
        ("Wall Art", re.compile(r"tapestry|wall art|hiasan dinding|3d|outlet cover", re.I)),
        ("Storage & Organization", re.compile(r"organizer|storage|laci|shelf|eva|liner", re.I)),
    ],
    "PH": [
        ("Decorative Stickers", re.compile(r"sticker|wallpaper|decal|vinyl|peel|adhesive|backsplash|recoloring", re.I)),
        ("Storage Baskets", re.compile(r"laundry|basket|hamper|storage", re.I)),
        ("Festive Decorations", re.compile(r"table runner|tablecloth|tapestry|runner|table cover", re.I)),
        ("Decorative Flowers, Plants & Fruit", re.compile(r"flower|artificial|vine|plant|floral|wreath", re.I)),
        ("Home Decor", re.compile(r"magnetic screen|curtain|decor|wall hanging", re.I)),
    ],
    "VN": [
        ("Hình dán trang trí", re.compile(r"dán|decal|giấy dán|sticker|tường|gạch|đề can", re.I)),
        ("Hoa, cây & trái cây trang trí", re.compile(r"hoa|treo|lụa|cây|bông|cúc|eucalyptus", re.I)),
        ("Đồ trang trí lễ hội", re.compile(r"khăn trải|bàn|table|lễ hội|tua rua", re.I)),
        ("Tượng & Tượng nhỏ", re.compile(r"tượng|resin|mèo|khay|đĩa", re.I)),
        ("Home Organization", re.compile(r"lót|kệ|tủ|organizer|eva|riêng tư", re.I)),
    ],
    "TH": [
        ("สติกเกอร์สำหรับตกแต่ง", re.compile(r"สติ๊กเกอร์|สติกเกอร์|กระเบื้อง|ติดผนัง|เทปตกแต่ง|ซับตู้", re.I)),
        ("ดอกไม้ พืช และผลไม้สำหรับตกแต่ง", re.compile(r"ดอกไม้|เถาวัลย์|ประดิษฐ์|แขวน|เดซี่", re.I)),
        ("ของตกแต่งงานรื่นเริง", re.compile(r"ผ้าปู|โต๊ะ|table|runner|ลูกไม้", re.I)),
        ("ที่เก็บของและชั้นวาง", re.compile(r"ชั้น|เก็บของ|ติดผนัง|ห้องน้ำ", re.I)),
        ("พรมผนัง", re.compile(r"พรม|ผ้าแขวน|ทอ|nordic|cotton", re.I)),
        ("ถังขยะ", re.compile(r"ถังขยะ|ขยะ", re.I)),
    ],
}

STOPWORDS = {
    "for", "and", "the", "with", "1pc", "1", "2pcs", "set", "pcs", "pc", "new", "hot",
    "yang", "dan", "untuk", "dengan", "bilik", "ruang", "helai", "unit", "sesuai", "hiasan",
    "cái", "chiếc", "bộ", "cho", "và", "của", "trang", "trí", "thích", "hợp", "phòng", "nhà",
    "ชิ้น", "สำหรับ", "และ", "ห้อง", "แบบ", "เหมาะ", "ideal", "perfect", "suitable", "home",
    "decor", "decoration", "style", "design", "high", "quality", "premium",
}


def _keywords_dir() -> Path:
    rel = get("keywords.dir", "data/keywords")
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def _load_category_map(conn) -> dict[tuple[str, str], str]:
    try:
        rows = conn.execute(
            "SELECT region, product_id, category_leaf FROM title_queue WHERE category_leaf != ''"
        ).fetchall()
    except Exception:
        return {}
    return {(r["product_id"], r["region"]): r["category_leaf"] for r in rows}


def _infer_category(region: str, product_id: str, title: str, cat_map: dict) -> str:
    key = (product_id, region)
    if key in cat_map:
        return cat_map[key]
    for cat, pat in REGION_RULES.get(region, []):
        if pat.search(title):
            return cat
    defaults = {
        "MY": "Home Decor",
        "PH": "Home Decor",
        "VN": "Trang trí nhà cửa",
        "TH": "ของตกแต่งบ้าน",
    }
    return defaults.get(region, "Home Decor")


def _tokenize(title: str) -> list[str]:
    title = re.sub(r"[^\w\s\u0e00-\u0e7f\u0100-\u024f\u1ea0-\u1ef9*,./-]", " ", title.lower())
    tokens = re.split(r"[\s,./|+-]+", title)
    out: list[str] = []
    for t in tokens:
        t = t.strip()
        if len(t) < 3 or t in STOPWORDS or t.isdigit():
            continue
        out.append(t)
    return out


def _bigrams(title: str) -> list[str]:
    words = _tokenize(title)
    phrases: list[str] = []
    for i in range(len(words) - 1):
        bg = f"{words[i]} {words[i+1]}"
        if len(bg) >= 6:
            phrases.append(bg)
    return phrases


def _extract_keywords(titles: list[str], limit: int = 8) -> list[str]:
    bi = Counter()
    uni = Counter()
    for t in titles:
        for p in _bigrams(t):
            bi[p] += 1
        for w in _tokenize(t):
            uni[w] += 1
    chosen: list[str] = []
    seen: set[str] = set()
    for phrase, _ in bi.most_common(20):
        if phrase not in seen:
            seen.add(phrase)
            chosen.append(phrase)
        if len(chosen) >= limit:
            return chosen
    for word, _ in uni.most_common(30):
        if word not in seen:
            seen.add(word)
            chosen.append(word)
        if len(chosen) >= limit:
            break
    return chosen[:limit]


def _pick_competitor_titles(items: list[dict], limit: int = 3) -> list[str]:
    """从高库存商品标题中选结构完整的作参考（非外部竞品，是同店爆款句式）。"""
    ranked = sorted(items, key=lambda x: (-x["stock"], -len(x["title"])))
    out: list[str] = []
    seen: set[str] = set()
    for it in ranked:
        t = re.sub(r"\s+", " ", (it["title"] or "").strip())
        if len(t) < 40 or len(t) > 255:
            continue
        key = t.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(t[:255])
        if len(out) >= limit:
            break
    return out


def _collect_products(conn) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT s.region, p.product_id, MAX(p.product_name) AS title, SUM(p.stock) AS stock
            FROM products p
            JOIN shops s ON s.cipher = p.shop_cipher
            WHERE p.status = 'ACTIVATE' AND p.product_name != ''
            GROUP BY s.region, p.product_id
            """
        ).fetchall()
    ]


def build_region_csv(region: str, rows: list[dict], cat_map: dict) -> list[dict]:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["region"] != region:
            continue
        cat = _infer_category(region, r["product_id"], r["title"] or "", cat_map)
        by_cat[cat].append({"title": r["title"], "stock": int(r["stock"] or 0)})

    csv_rows: list[dict] = []
    all_titles = [x["title"] for items in by_cat.values() for x in items if x["title"]]

    for cat, items in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        if len(items) < 2 and cat.startswith(("Home", "Other", "Trang", "ของ")):
            continue
        kws = _extract_keywords([x["title"] for x in items])
        comps = _pick_competitor_titles(items)
        if not kws and not comps:
            continue
        csv_rows.append({
            "category_leaf": cat,
            "hot_keywords": KEYWORD_SEP.join(kws),
            "competitor_titles": COMPETITOR_SEP.join(comps),
        })

    default_kws = _extract_keywords(all_titles, limit=10)
    csv_rows.append({
        "category_leaf": "*",
        "hot_keywords": KEYWORD_SEP.join(default_kws),
        "competitor_titles": COMPETITOR_SEP.join(_pick_competitor_titles(
            [{"title": t, "stock": 1} for t in all_titles[:50]], limit=2
        )),
    })
    return csv_rows


def write_csv(region: str, csv_rows: list[dict], out_dir: Path | None = None) -> Path:
    out_dir = out_dir or _keywords_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{region.upper()}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["category_leaf", "hot_keywords", "competitor_titles"])
        w.writeheader()
        w.writerows(csv_rows)
    return path


def build_all(regions: list[str] | None = None) -> dict[str, Path]:
    init_db()
    conn = connect()
    cat_map = _load_category_map(conn)
    products = _collect_products(conn)
    conn.close()

    found = sorted({p["region"] for p in products if p.get("region")})
    targets = regions or found or ["MY", "VN", "TH", "PH"]
    out: dict[str, Path] = {}
    for region in targets:
        rows = build_region_csv(region, products, cat_map)
        out[region] = write_csv(region, rows)
    return out


if __name__ == "__main__":
    paths = build_all()
    for reg, p in paths.items():
        print(f"  ✅ {reg}: {p} ({sum(1 for _ in open(p)) - 1} 行)")
