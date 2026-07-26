from domains.channel_operations import build_channel_pricing_preview
from modules.sourcing.new_product_workbench import price_review


def _row(target_id, shop, region, currency, price):
    return {
        "id": target_id,
        "shop": shop,
        "shop_id": 1,
        "region": region,
        "currency": currency,
        "list_price": price,
        "sale_after_discount_local": price * 0.65,
        "estimated_profit_cny": 5.2,
        "profit_margin_on_sale_pct": 15.0,
        "minimum_profit_cny": 5.0,
        "min_profit_adjusted": True,
        "status": "ok",
        "commission_local": 1.2,
        "header_meta": {"commission_rate": 6.2},
    }


def test_pricing_preview_preserves_store_rows_and_derivation_semantics():
    legacy = {
        "input": {"cost_cny": 4.4, "weight_kg": 0.2},
        "rates": {"PHP": 0.11, "MYR": 1.6},
        "sea": [
            _row("lh_ph", "LivelyHive", "PH", "PHP", 100),
            _row("hb_ph", "HomeBloom", "PH", "PHP", 90),
            _row("lh_my", "LivelyHive", "MY", "MYR", 20),
        ],
        "mx": _row("mx", "LivelyHive", "MX", "MXN", 200),
        "uk": _row("gb", "LivelyHive", "GB", "GBP", 15),
        "audit": {"sections": [{"section": "PH"}]},
    }

    result = build_channel_pricing_preview(
        legacy,
        selected_site_keys=["lh_ph", "hb_ph", "lh_my"],
        shopee_exchange_rates={"PHP": 0.118, "MYR": 1.75},
        ozon_exchange_rates={"PHP": 0.118, "MYR": 1.75},
    )

    assert result["status"] == "ready"
    assert len(result["all_legacy_store_prices"]) == 5
    assert [
        row["target_key"] for row in result["selected_store_prices"]
    ] == ["lh_ph", "hb_ph", "lh_my"]
    assert len(result["target_pricing"]["tiktok:PH"]["store_prices"]) == 2
    shopee_ph = result["target_pricing"]["shopee:PH"]
    assert shopee_ph["derived_preview"] == {
        "global_original_price_cny": 11.8,
        "local_original_price": 100.0,
        "source_currency": "PHP",
        "exchange_rate_cny_per_local": 0.118,
    }
    assert shopee_ph["status"] == "awaiting_tiktok_readback"
    assert result["target_pricing"]["ozon:RU"]["derived_preview"] == {
        "price_cny": 12,
        "old_price_cny": 16,
        "source_currency": "PHP",
        "exchange_rate_cny_per_local": 0.118,
    }
    assert result["external_calls_performed"] == []


def test_pricing_preview_blocks_unknown_or_missing_store_without_inventing_price():
    result = build_channel_pricing_preview(
        {"input": {}, "rates": {}, "sea": [], "audit": {"sections": []}},
        selected_site_keys=["lh_ph"],
        shopee_exchange_rates={},
        ozon_exchange_rates={},
    )

    assert result["status"] == "blocked"
    assert result["selected_store_prices"] == []
    assert result["master_price_source"] is None
    assert result["target_pricing"]["ozon:RU"]["status"] == "blocked"
    assert any("lh_ph" in blocker for blocker in result["blockers"])


def test_real_legacy_formula_exposes_all_eight_sea_stores_plus_mx_and_gb():
    legacy = price_review(
        7.36,
        0.2,
        [20, 20, 3],
        fx_rates={
            "PHP": 0.118,
            "MYR": 1.75,
            "THB": 0.2218,
            "VND": 0.000266,
        },
    )
    selected = [
        f"{prefix}_{region.lower()}"
        for prefix in ("lh", "hb")
        for region in ("PH", "MY", "TH", "VN")
    ] + ["mx", "gb"]

    result = build_channel_pricing_preview(
        legacy,
        selected_site_keys=selected,
        shopee_exchange_rates={
            "PHP": 0.118,
            "MYR": 1.75,
            "THB": 0.2218,
            "VND": 0.000266,
            "GBP": 9.15,
        },
        ozon_exchange_rates={
            "PHP": 0.118,
            "MYR": 1.75,
            "THB": 0.2218,
            "VND": 0.000266,
            "GBP": 9.15,
        },
    )

    assert len(result["all_legacy_store_prices"]) == 10
    assert len(result["selected_store_prices"]) == 10
    assert {
        (row["shop"], row["region"])
        for row in result["all_legacy_store_prices"]
    } >= {
        (shop, region)
        for shop in ("LivelyHive", "HomeBloom")
        for region in ("PH", "MY", "TH", "VN")
    }
    assert {row["region"] for row in result["all_legacy_store_prices"]} == {
        "PH",
        "MY",
        "TH",
        "VN",
        "MX",
        "GB",
    }
    assert len(result["target_pricing"]["tiktok:PH"]["store_prices"]) == 2
    assert all(
        row["minimum_profit_cny"] == 5.0
        for row in result["all_legacy_store_prices"]
    )
