from datetime import date

from modules.shopee import orders_pull, profit_settlement


def _detail() -> dict:
    return {
        "order_sn": "ORDER-2",
        "order_income": {
            "escrow_amount": 80,
            "items": [
                {
                    "model_sku": "SKU-2",
                    "item_name": "Two-pack order",
                    "discounted_price": 50,
                    "quantity_purchased": 2,
                }
            ],
        },
    }


class _MemoryFile:
    def __init__(self, name: str, files: dict[str, str]):
        self.name = name
        self._files = files

    def write_text(self, text: str, **_kwargs) -> int:
        self._files[self.name] = text
        return len(text)


class _MemoryOutputDir:
    def __init__(self):
        self.files: dict[str, str] = {}

    def mkdir(self, **_kwargs) -> None:
        return None

    def __truediv__(self, name: str) -> _MemoryFile:
        return _MemoryFile(name, self.files)


class _MemoryReportDir:
    def __init__(self, report: _MemoryFile):
        self._report = report

    def is_dir(self) -> bool:
        return True

    def glob(self, _pattern: str) -> list[_MemoryFile]:
        return [self._report]


def test_normalize_and_html_row_keep_quantity_and_total_line_cost(monkeypatch):
    monkeypatch.setattr(orders_pull, "_cost_by_seller_sku", lambda _sku: 12.5)
    norms = orders_pull._normalize_detail(
        region="TH",
        currency="THB",
        list_row={"order_sn": "ORDER-2", "escrow_release_time": 1_700_000_000},
        detail=_detail(),
    )

    assert len(norms) == 1
    norm = norms[0]
    assert norm["quantity"] == 2
    assert norm["unit_cost_cny"] == 12.5
    assert norm["product_cost_cny"] == 25.0
    assert norm["product_cost"] == 25.0  # compatibility: total order-line cost

    html_row = orders_pull._row_to_html_row(norm)
    assert html_row["quantity"] == 2
    assert html_row["unit_cost_cny"] == 12.5
    assert html_row["product_cost_cny"] == 25.0
    assert html_row["product_cost"] == 25.0
    assert html_row["cells"][6] == 2


def test_snapshot_and_profit_settlement_use_explicit_total_cost(monkeypatch):
    output_dir = _MemoryOutputDir()
    monkeypatch.setattr(orders_pull, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(orders_pull, "_cost_by_seller_sku", lambda _sku: 12.5)

    norms = orders_pull._normalize_detail(
        region="TH",
        currency="THB",
        list_row={"order_sn": "ORDER-2", "escrow_release_time": 1_700_000_000},
        detail=_detail(),
    )
    orders_pull.write_json_snapshot(
        region="TH", start=date(2024, 1, 1), end=date(2024, 1, 7), norms=norms
    )
    snapshot_text = output_dir.files["shopee_escrow_TH_20240101_20240107.json"]
    assert '"quantity": 2' in snapshot_text
    assert '"unit_cost_cny": 12.5' in snapshot_text
    assert '"product_cost_cny": 25.0' in snapshot_text

    orders_pull.write_weekly_html(
        region="TH", start=date(2023, 11, 14), end=date(2023, 11, 14), norms=norms
    )
    report = _MemoryFile("weekly_shopee_profit_20231114_20231114.html", output_dir.files)
    monkeypatch.setattr(profit_settlement, "OUTPUT_DIR", _MemoryReportDir(report))
    monkeypatch.setattr(profit_settlement, "_extract_data", lambda _path: {
        "headers": orders_pull._headers(),
        "rates": {"TH": 1.0},
        "rows": [orders_pull._row_to_html_row(norms[0])],
    })
    monkeypatch.setattr(profit_settlement, "_fx_payload", lambda _rates: ({"TH": 1.0}, {"live": False}))
    monkeypatch.setattr(profit_settlement, "list_reports", lambda: [])
    summary = profit_settlement.settlement_summary(date(2023, 11, 14), date(2023, 11, 14))

    assert summary["summary"]["product_cost_cny"] == 25.0
    assert summary["summary"]["profit_cny"] == 55.0
    assert summary["orders"][0]["product_cost_cny"] == 25.0
