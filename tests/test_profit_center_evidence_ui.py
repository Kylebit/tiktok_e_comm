"""Current captured behavior; assertion migration is documented.

Image/prices/lineage, waterfall and sample display regressions now live in the
existing test_u05_sku_evidence, test_u05_waterfall, test_u05_sample_audit and real
finance browser tests. Do not duplicate these with implementation-string checks.
"""
from pathlib import Path
from html.parser import HTMLParser
import hashlib
from modules.finance import sku_profit_shopee, sku_profit_tk
from modules.finance.sku_profit_model import enrich_comp, mark_outliers
from test_u05_sku_evidence import scenario,request

ROOT=Path(__file__).resolve().parents[1]

def test_captured_sku_review_does_not_write_input_evidence(tmp_path):
    profile,_,identity=scenario(tmp_path,'ready')
    before={p.relative_to(tmp_path):hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    status,value=request(profile,identity)
    assert status==200 and value['estimate'] and value['approval_status']=='ESTIMATE_ONLY'
    after={p.relative_to(tmp_path):hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    assert before==after,'Captured review must not write cost, price or source inputs'

def test_legacy_probe_escape_hatch_is_absent():
    # Inspect real links; current browser also checks the served DOM.
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.hrefs=[]
        def handle_starttag(self,tag,attrs):
            if tag=='a':self.hrefs.append(dict(attrs).get('href'))
    parser=Links();parser.feed((ROOT/'web/profit_center.html').read_text(encoding='utf-8'))
    assert '/sku-profit' not in parser.hrefs,'Legacy SKU probe must not be offered by the current finance page'

def test_both_profit_probes_return_every_collected_posterior_sample(monkeypatch):
    samples = mark_outliers(
        [
            enrich_comp(
                order_id=f"order-{index:02d}",
                statement_date="2026-07-24",
                sale_local=100,
                settlement_local=60 + index / 10,
                cost_cny=5,
                fx=0.2,
                source=f"fixture-{index:02d}",
            )
            for index in range(25)
        ]
    )

    monkeypatch.setattr(
        sku_profit_tk,
        "resolve_product",
        lambda _sku: {
            "sku_id": "platform-0021",
            "seller_sku": "0021",
            "product_name": "Fixture product",
            "cost_cny": 5,
            "cost_source": "sku_costs",
            "is_th_listing": True,
        },
    )
    monkeypatch.setattr(
        sku_profit_tk,
        "_live_fx",
        lambda **_kwargs: {"THB": 0.2, "as_of": "2026-07-24"},
    )
    monkeypatch.setattr(
        sku_profit_tk,
        "fetch_live_price_and_weight",
        lambda _product: {
            "list_price_local": 100,
            "weight_kg": 0.2,
            "price_source": "fixture",
            "weight_source": "fixture",
            "warnings": [],
        },
    )
    monkeypatch.setattr(sku_profit_tk, "load_csv_comps", lambda *_args: samples)
    monkeypatch.setattr(sku_profit_tk, "get", lambda _key: {})

    monkeypatch.setattr(
        sku_profit_shopee,
        "resolve_product",
        lambda _sku: {
            "model_id": "model-0021",
            "seller_sku": "0021",
            "model_name": "Fixture model",
            "sale_local": 100,
            "cost_cny": 5,
            "cost_source": "sku_costs_via_tk_seller_sku_tail4",
        },
    )
    monkeypatch.setattr(
        sku_profit_shopee,
        "_live_fx",
        lambda **_kwargs: {"THB": 0.2, "as_of": "2026-07-24"},
    )
    monkeypatch.setattr(
        sku_profit_shopee,
        "load_weekly_comps",
        lambda *_args, **_kwargs: (samples, samples),
    )
    monkeypatch.setattr(sku_profit_shopee, "get", lambda _key: {})

    tiktok = sku_profit_tk.estimate("0021", lookback_days=14)
    shopee = sku_profit_shopee.estimate("0021", lookback_days=45)

    assert tiktok["posterior"]["comps_in_window"] == 25
    assert len(tiktok["posterior"]["recent_comps"]) == 25
    assert shopee["posterior"]["comps_same_sku"] == 25
    assert len(shopee["posterior"]["recent_comps"]) == 25
