from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domains.channel_operations.tiktok_publisher import (
    APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA,
    TIKTOK_PREFLIGHT_RECEIPT_SCHEMA,
    TIKTOK_PUBLISH_RECEIPT_SCHEMA,
    TikTokPublishContractError,
    TikTokPublisher,
)
from modules.miaoshou.tiktok_publisher import (
    EXPECTED_SHOP_ID_BY_TARGET,
    MiaoshouTikTokTransport,
    PUBLISH_PATH,
    READ_SHOP_DRAFT_PATH,
    READ_SITE_DRAFT_PATH,
    SAVE_SHOP_DRAFT_PATH,
    SAVE_SITE_DRAFT_PATH,
)
from modules.miaoshou.client import MiaoshouBusinessRejectedError


TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
)
PRICE_BY_TARGET = {
    "tiktok:LH_PH": ("523", "PHP"),
    "tiktok:LH_MY": ("46", "MYR"),
    "tiktok:LH_TH": ("386", "THB"),
    "tiktok:LH_VN": ("408000", "VND"),
    "tiktok:MX": ("286", "MXN"),
    "tiktok:GB": ("15", "GBP"),
}
APPROVED_CATEGORY_ID = "600338"
CATEGORY_METADATA_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata"
)


def _gb_metadata_response() -> dict:
    return {
        "result": "success",
        "data": {
            "categoryMetadata": {
                "categoryProductAttrList": [
                    {
                        "attrId": "102255",
                        "name": "Batch Number",
                        "attributeNameAlias": "Batch Number",
                        "isMandatory": True,
                        "values": [
                            {
                                "id": "1000256",
                                "name": "1",
                                "valueNameAlias": "1",
                            }
                        ],
                    }
                ]
            }
        },
    }


def _snapshot(*, targets: tuple[str, ...] = TARGETS) -> dict:
    rows = []
    for index, target in enumerate(targets, start=1):
        price, currency = PRICE_BY_TARGET[target]
        rows.append(
            {
                "target_label": target,
                "detail_id": str(3249695000 + index),
                "shop_id": str(EXPECTED_SHOP_ID_BY_TARGET[target]),
                "expected_price": price,
                "expected_currency": currency,
                # Category is approved product evidence, never a platform/site constant.
                "expected_category_id": APPROVED_CATEGORY_ID,
                "category_evidence_digest": "c" * 64,
                "target_identity_digest": "d" * 64,
                "publish_identity_digest": "e" * 64,
                "receipt_digest": "f" * 64,
            }
        )
    return {
        "schema_version": APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA,
        "offer_id": "3846511157",
        "plan_id": "omnichannel:" + "a" * 64,
        "product_revision": 15,
        "payload_digest": "b" * 64,
        "targets": rows,
    }


def _draft_response(target: str, row: dict) -> dict:
    price = row["expected_price"]
    detail = {
        "detailId": int(row["detail_id"]),
        "cid": row["expected_category_id"],
        "skuMap": {
            "default": {
                "price": price,
                "priceIncludeVat": price,
            }
        },
    }
    if target in {
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
    }:
        return {
            "result": "success",
            "data": {"siteCollectItemInfo": detail, "ossMd5": "x" * 32},
        }
    detail["shopId"] = int(row["shop_id"])
    return {
        "result": "success",
        "data": {"shopCollectItemInfo": detail, "ossMd5": "x" * 32},
    }


class FakeLowestTransport:
    def __init__(self, snapshot: dict, *, reject_target: str | None = None):
        self.snapshot = snapshot
        self.reject_target = reject_target
        self.calls: list[tuple[str, dict]] = []
        self.rows_by_detail = {
            str(row["detail_id"]): row for row in snapshot["targets"]
        }

    def __call__(self, path: str, body: dict) -> dict:
        self.calls.append((path, body))
        if path == CATEGORY_METADATA_PATH:
            return _gb_metadata_response()
        detail_id = str(body["detailIds"][0] if "detailIds" in body else body["detailId"])
        row = self.rows_by_detail[detail_id]
        target = row["target_label"]
        if path in {SAVE_SITE_DRAFT_PATH, SAVE_SHOP_DRAFT_PATH}:
            return {"result": "success", "code": "200", "message": "Success"}
        if path == PUBLISH_PATH:
            if target == self.reject_target:
                raise MiaoshouBusinessRejectedError(
                    "category is incomplete", code="categoryInvalid"
                )
            return {"result": "success", "code": "200", "message": "Success"}
        return _draft_response(target, row)


def _publisher(snapshot: dict, *, reject_target: str | None = None):
    fake = FakeLowestTransport(snapshot, reject_target=reject_target)
    transport = MiaoshouTikTokTransport(post=fake)
    return TikTokPublisher(transport=transport), fake


def test_l1_preflight_reads_exact_six_drafts_and_never_writes():
    snapshot = _snapshot()
    publisher, fake = _publisher(snapshot)

    receipt = publisher.preflight(snapshot)

    assert receipt["schema_version"] == TIKTOK_PREFLIGHT_RECEIPT_SCHEMA
    assert receipt["offer_id"] == "3846511157"
    assert [row["target_label"] for row in receipt["targets"]] == list(TARGETS)
    assert all(row["status"] == "READY" for row in receipt["targets"])
    assert len(fake.calls) == 6
    assert all(path != PUBLISH_PATH for path, _body in fake.calls)


def test_l1_one_rejection_does_not_stop_later_tiktok_targets():
    snapshot = _snapshot()
    publisher, fake = _publisher(snapshot, reject_target="tiktok:LH_MY")
    preflight = publisher.preflight(snapshot)

    receipt = publisher.publish(snapshot, preflight)

    assert receipt["schema_version"] == TIKTOK_PUBLISH_RECEIPT_SCHEMA
    outcomes = {row["target_label"]: row for row in receipt["targets"]}
    assert outcomes["tiktok:LH_MY"] == {
        "target_label": "tiktok:LH_MY",
        "outcome": "REJECTED",
        "provider_code": "categoryInvalid",
        "provider_reason": "category is incomplete",
        "external_write_count": 0,
        "write_request_count": 1,
    }
    assert outcomes["tiktok:LH_TH"]["outcome"] == "ACCEPTED"
    assert outcomes["tiktok:GB"]["outcome"] == "ACCEPTED"
    publish_calls = [call for call in fake.calls if call[0] == PUBLISH_PATH]
    assert len(publish_calls) == 6
    assert receipt["accepted_target_count"] == 5
    assert receipt["rejected_target_count"] == 1


def test_l1_publish_uses_exact_endpoint_and_per_target_identity():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:MX", "tiktok:GB"))
    publisher, fake = _publisher(snapshot)
    preflight = publisher.preflight(snapshot)

    publisher.publish(snapshot, preflight)

    calls = [body for path, body in fake.calls if path == PUBLISH_PATH]
    assert calls == [
        {
            "detailIds": [int(row["detail_id"])],
            "shopIds": [
                str(row["shop_id"])
                if row["target_label"] == "tiktok:LH_PH"
                else int(row["shop_id"])
            ],
        }
        for row in snapshot["targets"]
    ]


def test_l1_direct_production_publish_reads_each_target_once():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:MX", "tiktok:GB"))
    publisher, fake = _publisher(snapshot)

    receipt = publisher.publish(snapshot)

    assert receipt["accepted_target_count"] == 3
    read_paths = {READ_SITE_DRAFT_PATH, READ_SHOP_DRAFT_PATH}
    assert len([call for call in fake.calls if call[0] in read_paths]) == 3
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 3


def test_l1_mismatched_target_is_repaired_then_submitted_and_other_targets_continue():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:LH_MY"))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        response = fake(path, body)
        if path == READ_SITE_DRAFT_PATH and body.get("site") == "PH":
            response["data"]["siteCollectItemInfo"]["skuMap"]["default"]["price"] = "1"
        return response

    publisher = TikTokPublisher(
        transport=MiaoshouTikTokTransport(post=post)
    )
    preflight = publisher.preflight(snapshot)
    assert [row["status"] for row in preflight["targets"]] == [
        "REPAIR_REQUIRED",
        "READY",
    ]

    receipt = publisher.publish(snapshot, preflight)

    outcomes = {row["target_label"]: row for row in receipt["targets"]}
    assert outcomes["tiktok:LH_PH"]["outcome"] == "ACCEPTED"
    assert outcomes["tiktok:LH_PH"]["external_write_count"] == 2
    assert outcomes["tiktok:LH_PH"]["write_request_count"] == 2
    assert outcomes["tiktok:LH_MY"]["outcome"] == "ACCEPTED"
    publish_calls = [call for call in fake.calls if call[0] == PUBLISH_PATH]
    assert len(publish_calls) == 2
    save_calls = [call for call in fake.calls if call[0] == SAVE_SITE_DRAFT_PATH]
    assert len(save_calls) == 1
    saved = save_calls[0][1]["siteCollectItemInfo"]
    assert saved["cid"] == APPROVED_CATEGORY_ID
    assert saved["skuMap"]["default"]["price"] == 523.0
    assert saved["skuMap"]["default"]["priceIncludeVat"] == 523.0


def test_l1_repair_rejection_is_reported_and_does_not_stop_later_target():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:LH_MY"))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        response = fake(path, body)
        if path == READ_SITE_DRAFT_PATH and body.get("site") == "PH":
            response["data"]["siteCollectItemInfo"]["cid"] = "999"
        if path == SAVE_SITE_DRAFT_PATH:
            raise MiaoshouBusinessRejectedError(
                "category is incomplete", code="categoryInvalid"
            )
        return response

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    receipt = publisher.publish(snapshot, publisher.preflight(snapshot))
    outcomes = {row["target_label"]: row for row in receipt["targets"]}

    assert outcomes["tiktok:LH_PH"]["outcome"] == "REJECTED"
    assert outcomes["tiktok:LH_PH"]["provider_code"] == "categoryInvalid"
    assert outcomes["tiktok:LH_PH"]["provider_reason"] == "category is incomplete"
    assert outcomes["tiktok:LH_PH"]["external_write_count"] == 0
    assert outcomes["tiktok:LH_PH"]["write_request_count"] == 1
    assert outcomes["tiktok:LH_MY"]["outcome"] == "ACCEPTED"
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 1


@pytest.mark.parametrize("forbidden", ("miaoshou:COMMON", "shopee:VN", "ozon:RU"))
def test_l1_snapshot_rejects_non_tiktok_targets(forbidden: str):
    snapshot = _snapshot(targets=("tiktok:GB",))
    snapshot["targets"].append(
        {
            "target_label": forbidden,
            "detail_id": "1",
            "shop_id": "1",
            "expected_price": "1",
            "expected_currency": "GBP",
            "expected_category_id": "1",
        }
    )
    publisher, fake = _publisher(_snapshot(targets=("tiktok:GB",)))

    with pytest.raises(TikTokPublishContractError):
        publisher.preflight(snapshot)

    assert fake.calls == []


def test_l1_production_module_has_no_oneclick_shopee_or_ozon_imports():
    paths = (
        Path("domains/channel_operations/tiktok_publisher.py"),
        Path("modules/miaoshou/tiktok_publisher.py"),
    )
    imported = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not any(
        token in module
        for module in imported
        for token in ("oneclick", "shopee", "ozon")
    )


def test_l1_publish_rejects_tampered_or_incomplete_preflight():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:LH_MY"))
    publisher, fake = _publisher(snapshot)
    preflight = publisher.preflight(snapshot)
    preflight["targets"] = preflight["targets"][:-1]

    with pytest.raises(TikTokPublishContractError):
        publisher.publish(snapshot, preflight)

    assert not [call for call in fake.calls if call[0] == PUBLISH_PATH]


def test_l1_provider_reason_is_redacted_before_receipt():
    snapshot = _snapshot(targets=("tiktok:GB",))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        fake.calls.append((path, body))
        if path == PUBLISH_PATH:
            raise MiaoshouBusinessRejectedError(
                "Authorization Bearer super-secret-token "
                "https://provider.example/items/123456789012",
                code="rejected",
            )
        if path == CATEGORY_METADATA_PATH:
            return _gb_metadata_response()
        row = snapshot["targets"][0]
        return _draft_response("tiktok:GB", row)

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    result = publisher.publish(snapshot, publisher.preflight(snapshot))["targets"][0]

    assert result["outcome"] == "REJECTED"
    assert "super-secret-token" not in result["provider_reason"]
    assert "provider.example" not in result["provider_reason"]
    assert "123456789012" not in result["provider_reason"]


def test_l1_transport_unknown_does_not_claim_confirmed_external_writes():
    snapshot = _snapshot(targets=("tiktok:GB",))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        fake.calls.append((path, body))
        if path == PUBLISH_PATH:
            raise TimeoutError("after dispatch")
        if path == CATEGORY_METADATA_PATH:
            return _gb_metadata_response()
        if path == SAVE_SHOP_DRAFT_PATH:
            return {"result": "success", "code": "200", "message": "Success"}
        return _draft_response("tiktok:GB", snapshot["targets"][0])

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    result = publisher.publish(snapshot, publisher.preflight(snapshot))["targets"][0]

    assert result["outcome"] == "UNKNOWN"
    assert result["external_write_count"] is None
    assert result["write_request_count"] == 2


def test_red_gb_applies_approved_category_draft_before_submit_without_readback_gate():
    snapshot = _snapshot(targets=("tiktok:GB",))

    class RecordingTransport:
        def __init__(self):
            self.calls = []

        def read_draft(self, target):
            self.calls.append("read")
            return {"info": {"cid": "", "skuMap": {"default": {}}}}

        def draft_matches(self, target, draft):
            self.calls.append("match")
            return False

        def save_approved_draft(self, target, draft):
            self.calls.append("save")
            return {"result": "success", "code": "200", "message": "Success"}

        def submit(self, target):
            self.calls.append("submit")
            return {"result": "success", "code": "200", "message": "Success"}

    transport = RecordingTransport()
    publisher = TikTokPublisher(transport=transport)
    preflight = publisher.preflight(snapshot)
    transport.calls.clear()

    result = publisher.publish(snapshot, preflight)["targets"][0]

    assert transport.calls == ["read", "save", "submit"]
    assert result["outcome"] == "ACCEPTED"
    assert result["external_write_count"] == 2


def test_red_gb_save_uses_official_required_category_attribute():
    snapshot = _snapshot(targets=("tiktok:GB",))
    row = snapshot["targets"][0]
    calls = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == READ_SHOP_DRAFT_PATH:
            return {
                "result": "success",
                "data": {
                    "ossMd5": "gb-md5",
                    "shopCollectItemInfo": {
                        "detailId": int(row["detail_id"]),
                        "cid": "",
                        "isCodOpen": "1",
                        "sizeChart": "https://provider.example/size-chart.gif",
                        "sizeChartType": "image",
                        "deliveryOptionSetType": "",
                        "skuMap": {
                            "default": {"price": 1.1, "priceIncludeVat": 1.1}
                        },
                    },
                },
            }
        if path == CATEGORY_METADATA_PATH:
            assert body == {"site": "GB", "cid": 600338, "shopIds": [10204699]}
            return {
                "result": "success",
                "data": {
                    "categoryMetadata": {
                        "categoryProductAttrList": [
                            {
                                "attrId": "102255",
                                "name": "Batch Number",
                                "attributeNameAlias": "Batch Number",
                                "isMandatory": True,
                                "values": [
                                    {
                                        "id": "1000256",
                                        "name": "1",
                                        "valueNameAlias": "1",
                                    }
                                ],
                            }
                        ]
                    }
                },
            }
        if path == SAVE_SHOP_DRAFT_PATH:
            return {"result": "success", "code": "200", "message": "Success"}
        raise AssertionError(path)

    transport = MiaoshouTikTokTransport(post=post)
    draft = transport.read_draft(row)
    transport.save_approved_draft(row, draft)
    saved = next(body for path, body in calls if path == SAVE_SHOP_DRAFT_PATH)

    assert saved["shopCollectItemInfo"]["cid"] == "600338"
    assert saved["shopCollectItemInfo"]["isCodOpen"] == "0"
    assert saved["shopCollectItemInfo"]["deliveryOptionSetType"] == "default"
    assert saved["shopCollectItemInfo"]["sizeChart"] == ""
    assert saved["shopCollectItemInfo"]["sizeChartType"] == ""
    assert saved["shopCollectItemInfo"]["productAttributes"] == [
        {
            "attributeId": "102255",
            "attributeName": "Batch Number",
            "attributeNameAlias": "Batch Number",
            "attributeValues": [
                {"valueName": "1", "valueId": "1000256", "valueNameAlias": "1"}
            ],
        }
    ]


def test_l1_gb_save_rejection_never_calls_publish():
    snapshot = _snapshot(targets=("tiktok:GB",))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        response = fake(path, body)
        if path == SAVE_SHOP_DRAFT_PATH:
            raise MiaoshouBusinessRejectedError(
                "delivery option is invalid", code="fail"
            )
        return response

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    receipt = publisher.publish(snapshot, publisher.preflight(snapshot))
    result = receipt["targets"][0]

    assert result["outcome"] == "REJECTED"
    assert result["provider_code"] == "fail"
    assert result["external_write_count"] == 0
    assert result["write_request_count"] == 1
    assert not [call for call in fake.calls if call[0] == PUBLISH_PATH]
