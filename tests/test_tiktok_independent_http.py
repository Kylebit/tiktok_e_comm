import hashlib
import json
from http.server import ThreadingHTTPServer
import threading
import urllib.error
import urllib.request

import pytest

from modules.miaoshou.oneclick_release import approved_tiktok_category_decisions
from modules.products import server as product_server
from shared_platform.collectbox_action import (
    CollectBoxTargetDetailIdentity,
    approved_plan_identity,
)


TIKTOK_TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
)
PRICES = {
    "tiktok:LH_PH": ("lh_ph", 523, "PHP"),
    "tiktok:LH_MY": ("lh_my", 46, "MYR"),
    "tiktok:LH_TH": ("lh_th", 386, "THB"),
    "tiktok:LH_VN": ("lh_vn", 408000, "VND"),
    "tiktok:MX": ("mx", 286, "MXN"),
    "tiktok:GB": ("gb", 15, "GBP"),
}
SHOP_IDS = {
    "tiktok:LH_PH": "7676267",
    "tiktok:LH_MY": "13295169",
    "tiktok:LH_TH": "13295228",
    "tiktok:LH_VN": "13295291",
    "tiktok:MX": "16265910",
    "tiktok:GB": "10204699",
}


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _approved_plan():
    targets = [
        "miaoshou:COMMON",
        *TIKTOK_TARGETS,
        "shopee:PH",
        "shopee:MY",
        "shopee:TH",
        "shopee:VN",
        "ozon:RU",
    ]
    category = {"name": "贴饰 > 墙贴", "id": "", "confidence": "approved"}
    decisions = approved_tiktok_category_decisions(
        category,
        targets=tuple(targets),
    )
    assert decisions is not None
    payload = {
        "product_revision": 15,
        "targets": targets,
        "product_facts": {"category": category},
        "approved_tiktok_category_decisions": decisions,
        "pricing": {
            "selected_targets": {
                target: {
                    "store_prices": [{
                        "target_key": target_key,
                        "list_price": price,
                        "currency": currency,
                    }],
                }
                for target, (target_key, price, currency) in PRICES.items()
            }
        },
    }
    return {
        "plan_id": "omnichannel:tiktok-independent",
        "product_id": "3846511157",
        "targets": targets,
        "payload": payload,
        "payload_digest": _digest(payload),
        "confirmation_token": "approved-token",
        "status": "APPROVED",
        "approval": {"status": "APPROVED", "approved_by": "Kyle"},
    }


def _publish_contexts(plan):
    identity = approved_plan_identity(plan)
    contexts = {}
    for sequence, target in enumerate(TIKTOK_TARGETS, 1):
        detail = CollectBoxTargetDetailIdentity(
            target_label=target,
            detail_id=str(71000 + sequence),
            shop_id=SHOP_IDS[target],
        ).internal_payload()
        context = {
            "schema_version": "collectbox-tiktok-publish-context/v1",
            "plan_id": identity["plan_id"],
            "offer_id": identity["offer_id"],
            "product_revision": identity["product_revision"],
            "payload_digest": identity["payload_digest"],
            "targets_digest": identity["targets_digest"],
            "action_id": "collectbox-action:test",
            "platform": "TIKTOK",
            "common_identity_digest": "c" * 64,
            "receipt_digest": chr(96 + sequence) * 64,
            "target_detail_identity": detail,
        }
        context["publish_identity_digest"] = _digest(context)
        contexts[target] = context
    return contexts


def _request_body(plan):
    identity = approved_plan_identity(plan)
    return {
        "confirm_publish": True,
        "offer_id": identity["offer_id"],
        "plan_id": identity["plan_id"],
        "product_revision": identity["product_revision"],
        "payload_digest": identity["payload_digest"],
        "targets_digest": identity["targets_digest"],
        "confirmation_token": plan["confirmation_token"],
        "publication_targets": list(plan["targets"]),
    }


class _ReleaseStore:
    def __init__(self, plan):
        self.plan = plan

    def get_plan(self, plan_id):
        return self.plan if plan_id == self.plan["plan_id"] else None


class _CollectBoxStore:
    def __init__(self, contexts):
        self.contexts = contexts

    def internal_tiktok_publish_contexts(self, *, plan_id):
        assert plan_id == "omnichannel:tiktok-independent"
        return self.contexts


class _Publisher:
    def __init__(self, receipt=None):
        self.snapshots = []
        self.preflights = []
        self.receipt = receipt

    def preflight(self, snapshot):
        self.snapshots.append(snapshot)
        return {
            "schema_version": "tiktok-publish-preflight/v1",
            "offer_id": snapshot["offer_id"],
            "plan_id": snapshot["plan_id"],
            "snapshot_digest": _digest(snapshot),
            "targets": [
                {"target_label": row["target_label"], "status": "READY"}
                for row in snapshot["targets"]
            ],
        }

    def publish(self, snapshot, preflight):
        self.preflights.append(preflight)
        if self.receipt is not None:
            return self.receipt
        return {
            "schema_version": "tiktok-publish-receipt/v1",
            "offer_id": snapshot["offer_id"],
            "plan_id": snapshot["plan_id"],
            "snapshot_digest": preflight["snapshot_digest"],
            "accepted_target_count": 6,
            "rejected_target_count": 0,
            "unknown_target_count": 0,
            "not_attempted_target_count": 0,
            "targets": [
                {
                    "target_label": row["target_label"],
                    "outcome": "ACCEPTED",
                    "provider_code": "200",
                    "provider_reason": "Success",
                    "external_write_count": 1,
                    "write_request_count": 1,
                }
                for row in snapshot["targets"]
            ],
        }


@pytest.fixture
def product_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _install_server_fakes(monkeypatch, plan, contexts, publisher):
    monkeypatch.setattr(
        product_server,
        "_tiktok_release_store",
        lambda: _ReleaseStore(plan),
        raising=False,
    )
    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: _CollectBoxStore(contexts),
    )
    monkeypatch.setattr(
        product_server,
        "_tiktok_publisher",
        lambda: publisher,
        raising=False,
    )
    monkeypatch.setattr(
        product_server,
        "_start_oneclick_release",
        lambda *_args, **_kwargs: pytest.fail("legacy one-click path was called"),
    )


def test_http_builds_exact_six_target_snapshot_without_common(
    monkeypatch,
    product_http_server,
):
    plan = _approved_plan()
    contexts = _publish_contexts(plan)
    publisher = _Publisher()
    _install_server_fakes(monkeypatch, plan, contexts, publisher)

    status, response = _post(
        product_http_server + "/api/product-workspace/publish-tiktok",
        _request_body(plan),
    )

    assert status == 200
    assert response["success"] is True
    assert len(publisher.snapshots) == 1
    snapshot = publisher.snapshots[0]
    assert snapshot["schema_version"] == "approved-tiktok-publish-snapshot/v1"
    assert [row["target_label"] for row in snapshot["targets"]] == list(
        TIKTOK_TARGETS
    )
    assert "miaoshou:COMMON" not in json.dumps(snapshot)
    assert {
        row["target_label"]: (
            row["expected_price"],
            row["expected_currency"],
        )
        for row in snapshot["targets"]
    } == {
        target: (str(price), currency)
        for target, (_key, price, currency) in PRICES.items()
    }
    assert all(row["expected_category_id"].isdigit() for row in snapshot["targets"])
    assert all(len(row["category_evidence_digest"]) == 64 for row in snapshot["targets"])
    assert all(len(row["target_identity_digest"]) == 64 for row in snapshot["targets"])
    assert all(len(row["publish_identity_digest"]) == 64 for row in snapshot["targets"])
    assert all(len(row["receipt_digest"]) == 64 for row in snapshot["targets"])


def test_missing_collectbox_target_fails_before_publisher_call(
    monkeypatch,
    product_http_server,
):
    plan = _approved_plan()
    contexts = _publish_contexts(plan)
    contexts.pop("tiktok:GB")
    publisher = _Publisher()
    _install_server_fakes(monkeypatch, plan, contexts, publisher)

    status, response = _post(
        product_http_server + "/api/product-workspace/publish-tiktok",
        _request_body(plan),
    )

    assert status == 409
    assert response["success"] is False
    assert response["error"]["code"] == "tiktok_approved_snapshot_invalid"
    assert response["external_write_count"] == 0
    assert publisher.snapshots == []


def test_provider_rejection_reason_survives_http_projection(
    monkeypatch,
    product_http_server,
):
    plan = _approved_plan()
    contexts = _publish_contexts(plan)
    accepted = [
        {
            "target_label": target,
            "outcome": "ACCEPTED",
            "provider_code": "200",
            "provider_reason": "Success",
            "external_write_count": 1,
            "write_request_count": 1,
        }
        for target in TIKTOK_TARGETS[:-1]
    ]
    rejected = {
        "target_label": "tiktok:GB",
        "outcome": "REJECTED",
        "provider_code": "category_required",
        "provider_reason": "GB category attribute is required",
        "external_write_count": 0,
        "write_request_count": 1,
    }
    publisher = _Publisher({
        "schema_version": "tiktok-publish-receipt/v1",
        "offer_id": plan["product_id"],
        "plan_id": plan["plan_id"],
        "snapshot_digest": "f" * 64,
        "accepted_target_count": 5,
        "rejected_target_count": 1,
        "unknown_target_count": 0,
        "not_attempted_target_count": 0,
        "targets": [*accepted, rejected],
    })
    _install_server_fakes(monkeypatch, plan, contexts, publisher)

    status, response = _post(
        product_http_server + "/api/product-workspace/publish-tiktok",
        _request_body(plan),
    )

    assert status == 200
    assert response["success"] is False
    assert response["failed_targets"] == ["tiktok:GB"]
    assert response["error"]["provider_code"] == "category_required"
    assert response["error"]["provider_reason"] == (
        "GB category attribute is required"
    )
    assert "GB category attribute is required" in response["message"]
