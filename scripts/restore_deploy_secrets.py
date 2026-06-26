"""从 Mac deploy-secrets 备份恢复 sku_costs 与 Ozon 数据目录。"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "backups" / "deploy-secrets-20260625"
OLD_DB = BACKUP / "main" / "data" / "shop.db"
NEW_DB = ROOT / "data" / "shop.db"
OZON_SRC = BACKUP / "ozon-data" / "data"
OZON_DST = (ROOT / ".." / "ozon" / "webapp" / "data").resolve()
OZON_LOCAL = ROOT / "config" / "ozon.local.json"
CREDS = OZON_SRC / "credentials.local.json"


def merge_sku_costs() -> dict:
    if not OLD_DB.is_file():
        raise FileNotFoundError(OLD_DB)
    if not NEW_DB.is_file():
        raise FileNotFoundError(NEW_DB)

    old = sqlite3.connect(OLD_DB)
    new = sqlite3.connect(NEW_DB)
    old.row_factory = sqlite3.Row

    rows = old.execute(
        "SELECT sku_id, cost_cny, note, updated_at FROM sku_costs WHERE cost_cny > 0"
    ).fetchall()
    product_ids = {
        r[0]
        for r in new.execute("SELECT sku_id FROM products").fetchall()
    }

    merged = inserted = updated = skipped = 0
    for row in rows:
        sku_id = row["sku_id"]
        if sku_id not in product_ids:
            skipped += 1
            continue
        existing = new.execute(
            "SELECT cost_cny FROM sku_costs WHERE sku_id = ?", (sku_id,)
        ).fetchone()
        new.execute(
            """
            INSERT INTO sku_costs (sku_id, cost_cny, note, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sku_id) DO UPDATE SET
                cost_cny = excluded.cost_cny,
                note = COALESCE(excluded.note, sku_costs.note),
                updated_at = excluded.updated_at
            """,
            (row["sku_id"], row["cost_cny"], row["note"], row["updated_at"]),
        )
        merged += 1
        if existing:
            updated += 1
        else:
            inserted += 1

    new.commit()
    total = new.execute(
        "SELECT COUNT(*) FROM sku_costs WHERE cost_cny > 0"
    ).fetchone()[0]
    old.close()
    new.close()
    return {
        "source_rows": len(rows),
        "merged": merged,
        "inserted": inserted,
        "updated": updated,
        "skipped_orphan": skipped,
        "total_with_cost": total,
    }


def copy_ozon_data() -> dict:
    if not OZON_SRC.is_dir():
        raise FileNotFoundError(OZON_SRC)
    OZON_DST.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in OZON_SRC.iterdir():
        if not src.is_file():
            continue
        dst = OZON_DST / src.name
        shutil.copy2(src, dst)
        copied += 1
    return {"dest": str(OZON_DST), "files": copied}


def write_ozon_local() -> bool:
    if not CREDS.is_file():
        return False
    raw = json.loads(CREDS.read_text(encoding="utf-8"))
    cid = str(raw.get("client_id") or "").strip()
    key = str(raw.get("api_key") or "").strip()
    if not cid or not key:
        return False
    OZON_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    OZON_LOCAL.write_text(
        json.dumps({"client_id": cid, "api_key": key}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    return True


def archive_tarballs() -> list[str]:
    downloads = Path.home() / "Downloads"
    dest = BACKUP / "archives"
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for name in (
        "deploy-secrets-20260625.tar.gz",
        "deploy-secrets-20260625-ozon-data.tar.gz",
    ):
        src = downloads / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            saved.append(str(dest / name))
    return saved


def main() -> None:
    costs = merge_sku_costs()
    ozon = copy_ozon_data()
    creds_ok = write_ozon_local()
    archives = archive_tarballs()

    manifest = {
        "sku_costs": costs,
        "ozon_data": ozon,
        "ozon_local_json": creds_ok,
        "archives": archives,
    }
    out = BACKUP / "RESTORE_MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
