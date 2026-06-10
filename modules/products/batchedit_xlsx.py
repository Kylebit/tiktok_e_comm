"""生成 TikTok Seller Center 批量编辑 xlsx（基于官方模板结构）。"""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from core import auth
from core.api_client import get as api_get
from core.config import ROOT, get
from core.db import connect, init_db

TEMPLATE_GLOB = "Tiktoksellercenter_batchedit_*_all_information_template_*.csv"
REGION_TEMPLATE = {
    "MY": None,
    "VN": "vn",
    "TH": "TH",
    "PH": "PH",
    "SG": "SG",
}


def _clean(text) -> str:
    s = "" if text is None else str(text)
    return ILLEGAL_CHARACTERS_RE.sub("", s)


def _first_url(images) -> str:
    for img in images or []:
        for key in ("urls", "thumb_urls"):
            urls = img.get(key) or []
            if urls:
                return urls[0]
    return ""


def _sku_name(sku: dict) -> str:
    parts = [
        a.get("value_name") or a.get("name") or ""
        for a in sku.get("sales_attributes") or []
    ]
    name = " / ".join(p for p in parts if p)
    return name or sku.get("seller_sku") or ""


def _weight_g(sku: dict, product: dict) -> str:
    w = sku.get("sku_weight") or product.get("package_weight") or {}
    val = float(w.get("value") or 0)
    unit = (w.get("unit") or "").upper()
    if unit == "KILOGRAM":
        val *= 1000
    return str(int(round(val)))


def _dim_cm(sku: dict, product: dict, key: str) -> str:
    d = sku.get("sku_dimensions") or product.get("package_dimensions") or {}
    return str(d.get(key) or "")


def _category_label(product: dict) -> str:
    chain = product.get("category_chains") or []
    if not chain:
        return ""
    leaf = chain[-1]
    return f"{leaf.get('local_name', '')} ({leaf.get('id', '')})"


def _find_template(region: str) -> Path | None:
    folder = ROOT / "CURSOR" / "product_cost"
    token = REGION_TEMPLATE.get(region.upper())
    if token:
        matches = sorted(folder.glob(f"*{token}*.csv"))
        if matches:
            return matches[0]
    return next(iter(sorted(folder.glob(TEMPLATE_GLOB))), None)


def _read_template_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    data_start = 0
    for i, row in enumerate(rows):
        if row and row[0].isdigit() and len(row[0]) >= 10:
            data_start = i
            break
    return rows[:data_start] if data_start else rows[:4]


def _headers_with_warehouse(header_rows: list[list[str]], warehouse_id: str) -> list[list[str]]:
    out = [list(r) for r in header_rows]
    wh_col = f"warehouse_quantity/{warehouse_id}"
    for row in out:
        for i, cell in enumerate(row):
            if cell.startswith("warehouse_quantity/"):
                row[i] = wh_col
    return out


def _product_row(header: list[str], product: dict, sku: dict, seller_sku: str) -> list[str]:
    row_map = {
        "product_id": str(product.get("id", "")),
        "category": _category_label(product),
        "product_name": product.get("title", ""),
        "sku_id": str(sku.get("id", "")),
        "variation_value": _sku_name(sku),
        "product_description": product.get("description") or "",
        "brand": "",
        "price": str((sku.get("price") or {}).get("sale_price") or ""),
        "seller_sku": seller_sku,
        "parcel_weight": _weight_g(sku, product),
        "parcel_length": _dim_cm(sku, product, "length"),
        "parcel_width": _dim_cm(sku, product, "width"),
        "parcel_height": _dim_cm(sku, product, "height"),
        "cod": "Y" if product.get("is_cod_allowed") else "N",
        "main_image": _first_url(product.get("main_images")),
    }
    inv = sku.get("inventory") or []
    if inv:
        wh = inv[0].get("warehouse_id")
        if wh:
            row_map[f"warehouse_quantity/{wh}"] = str(inv[0].get("quantity") or 0)

    imgs = []
    for img in product.get("main_images") or []:
        url = _first_url([img])
        if url:
            imgs.append(url)
    for n in range(2, 10):
        row_map[f"image_{n}"] = imgs[n - 1] if len(imgs) >= n else ""

    for attr in product.get("product_attributes") or []:
        aid = attr.get("id")
        if not aid:
            continue
        vals = attr.get("values") or []
        names = ";".join(v.get("name") or "" for v in vals if v.get("name"))
        row_map[f"product_property/{aid}"] = names

    out = []
    for col in header:
        if col.startswith("warehouse_quantity/"):
            wh = col.split("/", 1)[1]
            out.append(row_map.get(col, row_map.get(f"warehouse_quantity/{wh}", "")))
        else:
            out.append(row_map.get(col, ""))
    return [_clean(v) for v in out]


def _fetch_product(token: str, cipher: str, product_id: str) -> dict:
    result = api_get(f"/product/202309/products/{product_id}", token, {"shop_cipher": cipher})
    if result.get("code") != 0:
        raise RuntimeError(result.get("message", f"商品详情失败 {product_id}"))
    return result.get("data") or {}


def export_batchedit_xlsx(
    region: str | None = None,
    sku_id: str | None = None,
    limit: int | None = None,
    path: Path | None = None,
) -> Path:
    init_db()
    conn = connect()
    sql = """
        SELECT p.sku_id, p.product_id, p.seller_sku, p.shop_cipher, s.region
        FROM products p
        JOIN shops s ON s.cipher = p.shop_cipher
        WHERE p.seller_sku != ''
    """
    params: list = []
    if region:
        sql += " AND s.region = ?"
        params.append(region.upper())
    if sku_id:
        sql += " AND p.sku_id = ?"
        params.append(sku_id)
    sql += " ORDER BY s.region, p.product_id, p.sku_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        raise RuntimeError("没有可导出的 SKU")

    token = auth.access_token()
    region = rows[0]["region"]
    template = _find_template(region)
    if not template:
        raise RuntimeError(f"未找到 {region} 批量编辑模板 CSV，请先从 Seller Center 下载")

    header_rows = _read_template_rows(template)
    english_header = header_rows[0]

    product_cache: dict[tuple[str, str], dict] = {}
    data_rows: list[list[str]] = []
    warehouse_id = ""

    for row in rows:
        key = (row["shop_cipher"], row["product_id"])
        if key not in product_cache:
            product_cache[key] = _fetch_product(token, row["shop_cipher"], row["product_id"])
            time.sleep(0.1)
        product = product_cache[key]
        sku = next((s for s in product.get("skus") or [] if str(s.get("id")) == row["sku_id"]), None)
        if not sku:
            continue
        inv = sku.get("inventory") or []
        if inv and not warehouse_id:
            warehouse_id = str(inv[0].get("warehouse_id") or "")
        data_rows.append(_product_row(english_header, product, sku, row["seller_sku"]))

    if warehouse_id:
        header_rows = _headers_with_warehouse(header_rows, warehouse_id)

    exports = ROOT / get("exports_dir", "exports")
    exports.mkdir(parents=True, exist_ok=True)
    if path is None:
        suffix = f"test_{region}_1sku" if len(data_rows) == 1 else f"{region}_{len(data_rows)}sku"
        path = exports / f"seller_sku_batchedit_{suffix}_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Template"
    for r in header_rows:
        ws.append([_clean(c) for c in r])
    for r in data_rows:
        ws.append(r)
    wb.save(path)
    return path
