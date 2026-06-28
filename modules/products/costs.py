"""SKU 采购成本：从 CURSOR 导入、读写 SQLite。"""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path

from core.config import ROOT
from core.db import connect, init_db

CURSOR_COSTS = ROOT / "CURSOR" / "product_cost" / "sku_costs.csv"


def norm_sku(raw: str) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == "/":
        return None
    if s.startswith('="') and s.endswith('"') and len(s) > 4:
        s = s[2:-1].replace('""', '"').strip()
    m = re.search(r"\d{10,}", s)
    if m:
        return m.group(0)
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return s


def load_csv_costs(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 2:
                continue
            sku = norm_sku(row[0])
            if not sku:
                continue
            try:
                cost = float(str(row[1]).strip().replace(",", ""))
            except ValueError:
                continue
            if cost > 0:
                out[sku] = cost
    return out


def import_from_cursor(path: Path | None = None) -> int:
    """将 CURSOR/product_cost/sku_costs.csv 导入 sku_costs 表。"""
    init_db()
    src = path or CURSOR_COSTS
    costs = load_csv_costs(src)
    if not costs:
        print(f"  ⚠️  未从 {src} 读到成本数据")
        return 0
    conn = connect()
    now = int(time.time())
    n = 0
    for sku_id, cost in costs.items():
        conn.execute(
            """INSERT INTO sku_costs (sku_id, cost_cny, note, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(sku_id) DO UPDATE SET
                 cost_cny=excluded.cost_cny,
                 note=CASE WHEN sku_costs.note='' OR sku_costs.note IS NULL
                      THEN excluded.note ELSE sku_costs.note END,
                 updated_at=excluded.updated_at""",
            (sku_id, cost, "import:CURSOR", now),
        )
        n += 1
    conn.commit()
    conn.close()
    print(f"  ✅ 已从 CURSOR 导入 {n} 条 SKU 成本（{src.name}）")
    return n


def get_all_costs() -> dict[str, float]:
    init_db()
    conn = connect()
    rows = conn.execute("SELECT sku_id, cost_cny FROM sku_costs").fetchall()
    conn.close()
    return {r["sku_id"]: float(r["cost_cny"]) for r in rows}


def save_cost(sku_id: str, cost_cny: float, note: str = "") -> None:
    init_db()
    conn = connect()
    conn.execute(
        """INSERT INTO sku_costs (sku_id, cost_cny, note, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(sku_id) DO UPDATE SET cost_cny=excluded.cost_cny,
           note=excluded.note, updated_at=excluded.updated_at""",
        (sku_id, cost_cny, note, int(time.time())),
    )
    conn.commit()
    conn.close()


def save_costs_bulk(items: list[dict]) -> int:
    n = 0
    for item in items:
        sku = norm_sku(item.get("sku_id", ""))
        if not sku:
            continue
        try:
            cost = float(item.get("cost_cny", 0))
        except (TypeError, ValueError):
            continue
        if cost <= 0:
            continue
        save_cost(sku, cost, item.get("note", ""))
        n += 1
    return n


def export_csv(path: Path) -> int:
    init_db()
    conn = connect()
    rows = conn.execute(
        "SELECT sku_id, cost_cny FROM sku_costs WHERE cost_cny > 0 ORDER BY sku_id"
    ).fetchall()
    conn.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SKU ID", "采购成本"])
        for r in rows:
            w.writerow([f'="{r["sku_id"]}"', r["cost_cny"]])
    return len(rows)
