from datetime import date


def test_build_coverage_separates_settled_cancelled_and_unsettled():
    from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage

    result = build_coverage(
        orders=[
            {"order_id": "A", "order_created_at": "2026-07-01T01:00:00+07:00", "order_status": "COMPLETED"},
            {"order_id": "B", "order_created_at": "2026-07-02T01:00:00+07:00", "order_status": "CANCELLED"},
            {"order_id": "C", "order_created_at": "2026-07-03T01:00:00+07:00", "order_status": "DELIVERED"},
            {"order_id": "D", "order_created_at": "2026-07-04T01:00:00+07:00", "order_status": "CANCELLED"},
        ],
        settled_order_ids={"A", "D"},
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        as_of=date(2026, 8, 15),
        settlement_snapshot_id="snapshot-1",
    )

    assert result["status"] == "needs_review"
    assert result["counts"] == {
        "created_orders": 4,
        "settled_orders": 2,
        "cancelled_orders": 2,
        "cancelled_with_settlement": 1,
        "cancelled_without_settlement": 1,
        "unsettled_non_cancelled": 1,
    }
    assert [row["order_id"] for row in result["unsettled_non_cancelled_orders"]] == ["C"]
    assert result["receipt"]["external_writes_performed"] == []


def test_build_coverage_is_ready_when_every_non_cancelled_order_is_settled():
    from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage

    result = build_coverage(
        orders=[
            {"order_id": "A", "order_created_at": "2026-07-01T01:00:00+07:00", "order_status": "COMPLETED"},
            {"order_id": "B", "order_created_at": "2026-07-02T01:00:00+07:00", "order_status": "CANCELLED"},
        ],
        settled_order_ids={"A"},
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        as_of=date(2026, 8, 15),
        settlement_snapshot_id="snapshot-1",
    )

    assert result["status"] == "ready"
    assert result["counts"]["unsettled_non_cancelled"] == 0
