"""从 TikTok Shop UK 佣金 PDF 导入 config/uk_commission_rates.json。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader

CATEGORIES = [
    "Computers & Office Equipment",
    "Phones & Electronics",
    "Automotive & Motorcycle",
    "Beauty & Personal Care",
    "Jewelry Accessories & Derivatives",
    "Books, Magazines & Audio",
    "Textiles & Soft Furnishings",
    "Sports & Outdoor",
    "Home Improvement",
    "Household Appliances",
    "Womenswear & Underwear",
    "Menswear & Underwear",
    "Fashion Accessories",
    "Baby & Maternity",
    "Muslim Fashion",
    "Kids' Fashion",
    "Food & Beverages",
    "Luggage & Bags",
    "Pet Supplies",
    "Kitchenware",
    "Collectibles",
    "Home Supplies",
    "Furniture",
    "Health",
    "Tools & Hardware",
    "Toys & Hobbies",
    "Shoes",
    "Pre-Owned",
]
CATEGORIES.sort(key=len, reverse=True)


def parse_pdf(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path))
    rows = []
    for page in reader.pages:
        for line in (page.extract_text() or "").splitlines():
            line = line.strip()
            if not line or line.startswith("Category"):
                continue
            m = re.search(r"(\d+)%\s*$", line)
            if not m:
                continue
            rate = float(m.group(1))
            rest = line[: m.start()].strip()
            if rest.endswith(" All"):
                cat, sub = rest[:-4].strip(), "All"
            else:
                cat = sub = None
                for c in CATEGORIES:
                    if rest.startswith(c + " "):
                        cat, sub = c, rest[len(c) + 1 :].strip()
                        break
                if not cat:
                    continue
            rows.append({"category": cat, "sub_category": sub, "commission_pct": rate})
    cat_all = {r["category"]: r["commission_pct"] for r in rows if r["sub_category"] == "All"}
    return {
        "source": pdf_path.name,
        "vat_rate_pct": 20,
        "default_commission_pct": 9,
        "category_all_rates": cat_all,
        "entries": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "pdf",
        nargs="?",
        default=str(Path.home() / "Downloads" / "TikTok Shop UK - Commission Rates by Product Category.pdf"),
    )
    ap.add_argument(
        "--out",
        default=str(ROOT / "config" / "uk_commission_rates.json"),
    )
    args = ap.parse_args()
    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"PDF not found: {pdf}")
        return 1
    payload = parse_pdf(pdf)
    out = Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"entries={len(payload['entries'])} category_all={len(payload['category_all_rates'])} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
