from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from shared_platform.report_store import ReportRunStore
from shared_platform.weekly_profit_runner import (
    build_weekly_profit_preview,
    load_catalog_profit_inputs,
    persist_weekly_profit_preview,
    previous_complete_week,
)


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "data").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "CURSOR" / "Income_Data").mkdir(parents=True)
    (root / "outputs").mkdir()
    (root / "config" / "synthetic-fx.json").write_text(
        json.dumps({"exchange_rates": {"THB": "0.2"}}),
        encoding="utf-8",
    )
    connection = sqlite3.connect(root / "data" / "shop.db")
    connection.executescript(
        """
        CREATE TABLE products (
            sku_id TEXT,
            shop_cipher TEXT,
            seller_sku TEXT,
            currency TEXT
        );
        CREATE TABLE sku_costs (
            sku_id TEXT,
            cost_cny NUMERIC,
            updated_at INTEGER
        );
        INSERT INTO products VALUES ('PLATFORM-1', 'TH-SHOP', '990017', 'THB');
        INSERT INTO sku_costs VALUES ('PLATFORM-1', 3, 100);
        ALTER TABLE products ADD COLUMN product_id TEXT;
        UPDATE products SET product_id='PRODUCT-1';
        CREATE TABLE shops(cipher TEXT,shop_id TEXT,region TEXT);
        INSERT INTO shops VALUES ('TH-SHOP','TH-SHOP','TH');
        CREATE TABLE shopee_shops(shop_id TEXT,region TEXT);
        CREATE TABLE shopee_products(model_id TEXT,shop_id TEXT,item_id TEXT,seller_sku TEXT,currency TEXT,price REAL);
        CREATE TABLE product_analytics(product_id TEXT,shop_cipher TEXT);
        CREATE TABLE sku_logistics_weights(seller_sku TEXT);

        """
    )
    connection.commit()
    connection.close()
    (root / "CURSOR" / "Income_Data" / "income_TH_fixture.csv").write_text(
        "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Currency,Quantity,shop_id\n"
        "Order,O-1,PLATFORM-1,2026-07-20,20,THB,2,TH-SHOP\n",
        encoding="utf-8",
    )
    return root


def test_previous_complete_week_is_monday_through_sunday():
    assert previous_complete_week(date(2026, 7, 25)) == (
        date(2026, 7, 13),
        date(2026, 7, 19),
    )
    assert previous_complete_week(date(2026, 7, 27)) == (
        date(2026, 7, 20),
        date(2026, 7, 26),
    )


def test_runner_builds_auditable_dry_run_without_creating_orbit_store(tmp_path):
    root = _fixture_root(tmp_path)

    preview = build_weekly_profit_preview(
        period_start=date(2026, 7, 20),
        period_end=date(2026, 7, 26),
        root=root,
        settings_path=root / "config" / "synthetic-fx.json",
        generated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        code_version="test-code",
    )

    assert preview.report.status == "needs_review"
    assert {issue.code for issue in preview.report.quality_issues} == {
        "upstream:missing_ad_spend"
    }
    realized = preview.report.realized_by_sku[0]
    assert realized["sku_id"] == "990017"
    assert realized["settlement_cny"] == Decimal("4.0")
    assert realized["cost_cny"] == Decimal("6")
    assert realized["profit_cny"] == Decimal("-2.0")
    metadata = preview.report.payload()["input_snapshot"]["source_metadata"]
    assert metadata["adapter_row_counts"] == {"raw": 1, "normalized": 1, "rejected": 0}
    assert metadata["source_files"][0]["name"] == "income_TH_fixture.csv"
    assert not (root / "data" / "orbit_platform.db").exists()


def test_explicit_persistence_is_idempotent_and_survives_reopen(tmp_path):
    root = _fixture_root(tmp_path)
    preview = build_weekly_profit_preview(
        period_start=date(2026, 7, 20),
        period_end=date(2026, 7, 26),
        root=root,
        settings_path=root / "config" / "synthetic-fx.json",
        generated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        code_version="test-code",
    )
    path = root / "data" / "orbit_platform.db"
    store = ReportRunStore(path)

    first = persist_weekly_profit_preview(preview, store=store)
    repeated = persist_weekly_profit_preview(preview, store=ReportRunStore(path))

    assert first.report_created is True
    assert repeated.report_created is False
    assert ReportRunStore(path).list_report_runs()[0]["status"] == "needs_review"


def test_catalog_reader_blocks_conflicting_costs_for_internal_sku_aliases(tmp_path):
    root = _fixture_root(tmp_path)
    connection = sqlite3.connect(root / "data" / "shop.db")
    connection.execute(
        "INSERT INTO products VALUES ('PLATFORM-2', 'OTHER', '17', 'PHP', 'PRODUCT-2')"
    )
    connection.execute("INSERT INTO sku_costs VALUES ('PLATFORM-2', 4, 50)")
    connection.execute("INSERT INTO shops VALUES ('OTHER','OTHER','PH')")
    connection.commit()
    connection.close()

    catalog = load_catalog_profit_inputs(root / "data" / "shop.db")

    assert catalog.seller_sku_by_platform_sku["PLATFORM-1"] == "990017"
    assert catalog.seller_sku_by_platform_sku["PLATFORM-2"] == "17"
    assert not {"990017", "17", "0017"} & set(catalog.costs_by_sku)
    conflicts = {
        issue.record_id
        for issue in catalog.issues
        if issue.code == "conflicting_cost"
    }
    assert conflicts == {"990017", "17"}


def test_scheduled_launcher_targets_local_orbit_runner():
    launcher = (
        Path(__file__).resolve().parents[1] / "scripts" / "weekly_profit_push.bat"
    ).read_text(encoding="utf-8")

    assert "-m shared_platform.weekly_profit_runner --persist-local" in launcher
    assert "--push" not in launcher
    assert "feishu" not in launcher.lower()
