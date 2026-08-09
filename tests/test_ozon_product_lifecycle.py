from __future__ import annotations

from modules.ozon.product_lifecycle import ensure_offer_reset


def test_transitional_validated_offer_is_kept() -> None:
    calls: list[tuple[str, dict]] = []

    def post(path: str, payload: dict) -> dict:
        calls.append((path, payload))
        return {
            "items": [
                {
                    "id": 123,
                    "description_category_id": 17027905,
                    "type_id": 92008,
                    "is_archived": False,
                    "statuses": {
                        "status": "offer_validated",
                        "validation_status": "success",
                        "is_created": False,
                        "status_failed": "",
                    },
                }
            ]
        }

    result = ensure_offer_reset(
        post,
        "0952",
        category_id=17027905,
        type_id=92008,
    )

    assert result["action"] == "keep"
    assert [path for path, _ in calls] == ["/v3/product/info/list"]


def test_explicit_validation_failure_is_deleted() -> None:
    def post(path: str, payload: dict) -> dict:
        if path == "/v3/product/info/list":
            return {
                "items": [
                    {
                        "id": 123,
                        "description_category_id": 17027905,
                        "type_id": 92008,
                        "is_archived": True,
                        "statuses": {
                            "validation_status": "failed",
                            "is_created": False,
                            "status_failed": "invalid attributes",
                        },
                    }
                ]
            }
        assert path == "/v2/products/delete"
        return {"status": [{"is_deleted": True}]}

    result = ensure_offer_reset(
        post,
        "0952",
        category_id=17027905,
        type_id=92008,
    )

    assert result["action"] == "deleted"


def test_moderation_decline_on_created_card_is_repaired_in_place() -> None:
    calls: list[tuple[str, dict]] = []

    def post(path: str, payload: dict) -> dict:
        calls.append((path, payload))
        return {
            "items": [
                {
                    "id": 123,
                    "description_category_id": 17027905,
                    "type_id": 92008,
                    "is_archived": False,
                    "statuses": {
                        "validation_status": "success",
                        "moderate_status": "declined",
                        "status_failed": "declined",
                        "is_created": True,
                    },
                }
            ]
        }

    result = ensure_offer_reset(
        post,
        "0952",
        category_id=17027905,
        type_id=92008,
    )

    assert result["action"] == "keep"
    assert [path for path, _ in calls] == ["/v3/product/info/list"]


def test_wrong_category_declined_card_is_archived_before_permanent_delete() -> None:
    """Ozon rejects /v2/products/delete until the current product is archived."""

    calls: list[tuple[str, dict]] = []
    archived = False

    def post(path: str, payload: dict) -> dict:
        nonlocal archived
        calls.append((path, payload))
        if path == "/v3/product/info/list":
            return {
                "items": [
                    {
                        "id": 5839891278,
                        "offer_id": "0963",
                        "description_category_id": 17027906,
                        "type_id": 91971,
                        "is_archived": archived,
                        "statuses": {
                            "validation_status": "success",
                            "moderate_status": "declined",
                            "status_failed": "declined",
                            "is_created": False,
                        },
                    }
                ]
            }
        if path == "/v1/product/archive":
            assert payload == {"product_id": [5839891278]}
            archived = True
            return {"result": True}
        if path == "/v2/products/delete":
            assert archived is True
            assert payload == {"products": [{"offer_id": "0963"}]}
            return {"status": [{"offer_id": "0963", "is_deleted": True, "error": ""}]}
        raise AssertionError(path)

    result = ensure_offer_reset(
        post,
        "0963",
        category_id=17028730,
        type_id=92692,
    )

    assert result["action"] == "deleted"
    assert [path for path, _ in calls] == [
        "/v3/product/info/list",
        "/v1/product/archive",
        "/v3/product/info/list",
        "/v2/products/delete",
    ]
