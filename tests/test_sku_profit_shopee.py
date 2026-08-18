from datetime import date

from modules.finance import sku_profit_shopee
from modules.finance.sku_profit_model import enrich_comp, mark_outliers


def _weekly_data(*, settlement: float, order_sn: str = "order-1") -> dict:
    return {
        "headers": [
            {"name": "Order SN"},
            {"name": "SKU"},
            {"name": "Release Time"},
        ],
        "rows": [
            {
                "region": "TH",
                "cells": [order_sn, "0021", "2026-07-20 08:00"],
                "subtotal": 100,
                "settlement": settlement,
                "product_cost": 5,
            }
        ],
    }


def test_weekly_comps_deduplicate_overlapping_snapshots(monkeypatch, tmp_path):
    older = tmp_path / "weekly_shopee_profit_20260701_20260720.html"
    newer = tmp_path / "weekly_shopee_profit_20260708_20260727.html"
    older.write_text("older", encoding="utf-8")
    newer.write_text("newer", encoding="utf-8")
    payloads = {
        older.name: _weekly_data(settlement=50),
        newer.name: _weekly_data(settlement=60),
    }
    monkeypatch.setattr(sku_profit_shopee, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        sku_profit_shopee,
        "_extract_data",
        lambda path: payloads[path.name],
    )
    monkeypatch.setattr(sku_profit_shopee, "date", _FixedDate)

    same, all_th = sku_profit_shopee.load_weekly_comps(
        "990021",
        cost_cny=5,
        fx=0.2,
        ad_rate=0.22,
        lookback_days=45,
    )

    assert len(same) == 1
    assert len(all_th) == 1
    assert same[0]["settlement_local"] == 60
    assert same[0]["source"].endswith(newer.name)


def test_store_pool_is_prior_only_not_same_sku_posterior(monkeypatch):
    pool = mark_outliers(
        [
            enrich_comp(
                order_id=f"pool-{index}",
                statement_date=f"2026-07-{20 - index:02d}",
                sale_local=100,
                settlement_local=50 + index,
                cost_cny=5,
                fx=0.2,
            )
            for index in range(8)
        ]
    )
    monkeypatch.setattr(
        sku_profit_shopee,
        "resolve_product",
        lambda _sku: {
            "seller_sku": "0021",
            "sale_local": 100,
            "cost_cny": 5,
            "cost_source": "sku_costs_via_tk_seller_sku_tail4",
        },
    )
    monkeypatch.setattr(
        sku_profit_shopee,
        "_live_fx",
        lambda **_kwargs: {"THB": 0.2, "rates": {"THB": 0.2}},
    )
    monkeypatch.setattr(
        sku_profit_shopee,
        "load_weekly_comps",
        lambda *_args, **_kwargs: ([], pool),
    )
    monkeypatch.setattr(sku_profit_shopee, "get", lambda _key: {})

    result = sku_profit_shopee.estimate("0021")

    assert result["ok"] is True
    assert result["posterior"]["comps_same_sku"] == 0
    assert result["scenarios"]["sample_counts"]["all_usable"] == 0
    assert result["main"]["confidence"] == "prior_or_sparse"
    assert result["main"]["label"] == "高费情景（结算比分位推断）"


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 25)
