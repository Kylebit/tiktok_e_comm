import re
from pathlib import Path


DASHBOARD = (
    Path(__file__).resolve().parents[1]
    / "domains"
    / "supply_chain_operations"
    / "dashboard"
)


def test_every_dashboard_sku_has_a_local_main_image():
    data = (DASHBOARD / "data.js").read_text(encoding="utf-8")
    skus = re.findall(r'skuFact\("(\d{4})"', data)

    assert len(skus) == 24
    assert len(set(skus)) == len(skus)
    assert all((DASHBOARD / "assets" / f"sku-{sku}.jpg").is_file() for sku in skus)


def test_dashboard_loads_facts_before_calculation_code():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    assert html.index('src="./data.js"') < html.index('src="./app.js"')


def test_dashboard_contains_no_remote_main_image_dependency():
    data = (DASHBOARD / "data.js").read_text(encoding="utf-8")
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")

    assert "http://" not in data
    assert "https://" not in data
    assert "./assets/sku-" in app
