import json
import sqlite3

import pytest

from shared_platform.collectbox_action import (
    ALREADY_PRESENT,
    FAILED_RETRYABLE,
    IMPORTED,
    RECONCILIATION_REQUIRED,
    CollectBoxActionStore,
    CollectBoxPlatformResult,
    approved_plan_identity,
    common_collectbox_identity_digest,
)


def _plan():
    return {
        "plan_id": "omnichannel:approved",
        "product_id": "3846511157",
        "payload_digest": "a" * 64,
        "payload": {"product_revision": 31},
        "targets": ["tiktok:MX", "shopee:MY", "ozon:RU"],
        "status": "APPROVED",
        "approval": {"status": "APPROVED", "approved_by": "Kyle"},
    }


def test_platform_request_carries_exact_approved_payload_and_targets(tmp_path):
    store = CollectBoxActionStore(tmp_path / "platform.db")
    plan = _plan()
    plan["payload"] = {
        "product_revision": 31,
        "listing_copy": {
            "shopee_description_en": "Product overview\n\nVerified details"
        },
        "pricing": {
            "selected_targets": {
                "tiktok:MX": {
                    "store_prices": [
                        {
                            "target_key": "mx",
                            "list_price": "286",
                            "currency": "MXN",
                        }
                    ]
                }
            }
        },
    }
    seen = []

    def adapter(request):
        seen.append(request)
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=ALREADY_PRESENT,
            platform_detail_id="71001",
            external_writes=(),
            external_write_count=0,
        )

    store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )

    assert len(seen) == 2
    assert all(request.approved_plan_payload == plan["payload"] for request in seen)
    assert all(request.approved_targets == tuple(plan["targets"]) for request in seen)


def test_platform_multi_write_success_is_persisted_without_reconciliation(
    tmp_path,
):
    store = CollectBoxActionStore(tmp_path / "platform.db")
    plan = _plan()

    def adapter(request):
        platform = request.platform.lower()
        target = "tiktok:MX" if platform == "tiktok" else "shopee:MY"
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id="71001",
            external_writes=(
                f"miaoshou:collectbox:claim:{platform}",
                f"miaoshou:collectbox:{platform}:detail:update:{target}",
            ),
            external_write_count=2,
            receipt_evidence={
                "schema_version": "collectbox-platform-preparation-evidence/v1",
                "checks": {"readback_exact": True},
            },
        )

    projection = store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )

    assert projection["action"]["status"] == "SUCCEEDED"
    assert projection["action"]["terminal"] is True
    assert projection["external_write_count"] == 4
    assert all(
        row["external_writes"]["count"] == 2
        for row in projection["action"]["platforms"]
    )


@pytest.mark.parametrize(
    ("platform", "invalid_write"),
    [
        (
            "TIKTOK",
            "miaoshou:collectbox:tiktok:detail:update:",
        ),
        (
            "TIKTOK",
            "miaoshou:collectbox:tiktok:detail:update:shopee:MY",
        ),
        (
            "TIKTOK",
            "miaoshou:collectbox:tiktok:detail:update:tiktok:UNKNOWN",
        ),
        (
            "SHOPEE",
            "miaoshou:collectbox:shopee:detail:update:tiktok:MX",
        ),
    ],
)
def test_platform_result_rejects_non_allowlisted_write_class(
    tmp_path,
    platform,
    invalid_write,
):
    store = CollectBoxActionStore(tmp_path / "platform.db")
    plan = _plan()

    def adapter(request):
        if request.platform != platform:
            return CollectBoxPlatformResult(
                status="SUCCEEDED",
                outcome=ALREADY_PRESENT,
                platform_detail_id="71000",
                external_writes=(),
                external_write_count=0,
            )
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id="71001",
            external_writes=(
                f"miaoshou:collectbox:claim:{platform.lower()}",
                invalid_write,
            ),
            external_write_count=2,
            receipt_evidence={
                "schema_version": "collectbox-platform-preparation-evidence/v1",
                "checks": {"readback_exact": True},
            },
        )

    projection = store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )

    row = next(
        item
        for item in projection["action"]["platforms"]
        if item["platform"] == platform
    )
    assert row["status"] == RECONCILIATION_REQUIRED
    assert row["external_writes"] == {
        "count": None,
        "classes": [f"miaoshou:collectbox:claim:{platform.lower()}"],
    }


def test_collectbox_preview_is_pure_and_does_not_expose_raw_detail_id(
    tmp_path,
):
    store = CollectBoxActionStore(tmp_path / "platform.db")
    plan = _plan()
    common_id = plan["product_id"]
    projection = store.preview(
        plan=plan,
        common_collectbox_identity_digest=common_collectbox_identity_digest(
            plan["plan_id"],
            common_id,
        ),
    )

    assert projection["schema_version"] == "collectbox-action-status/v1"
    assert projection["persisted"] is False
    assert projection["action"]["status"] == "READY"
    assert projection["action"]["start_allowed"] is True
    assert projection["canonical_next_action"] == {
        "action": "start_collectbox_action",
        "target_focus": None,
    }
    assert [row["platform"] for row in projection["action"]["platforms"]] == [
        "TIKTOK",
        "SHOPEE",
    ]
    assert all(
        row["status"] == "PENDING"
        and row["outcome"] is None
        and row["platform_detail_id_digest"] is None
        for row in projection["action"]["platforms"]
    )
    assert common_id not in json.dumps(projection)
    assert store.status(plan_id=plan["plan_id"]) is None


def test_partial_action_survives_restart_and_retries_only_failed_platform(
    tmp_path,
):
    path = tmp_path / "platform.db"
    store = CollectBoxActionStore(path)
    plan = _plan()
    common_id = plan["product_id"]
    calls = []
    clock = [100.0]
    waits = []

    def now():
        return clock[0]

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds

    first_results = {
        "TIKTOK": CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id="9001",
            external_writes=("miaoshou:collectbox:claim:tiktok",),
            external_write_count=1,
            receipt_evidence={"checks": {"claim_accepted": True}},
        ),
        "SHOPEE": CollectBoxPlatformResult(
            status=FAILED_RETRYABLE,
            error_category="CAPABILITY",
            error_code="fixture_safe_failure",
            error_detail="fixture safe failure",
            external_writes=(),
            external_write_count=0,
        ),
    }

    def first_adapter(request):
        calls.append((request.platform, now()))
        return first_results[request.platform]

    first = store.start(
        plan=plan,
        common_collect_box_detail_id=common_id,
        adapter=first_adapter,
        now=now,
        wait=wait,
    )

    assert calls == [("TIKTOK", 100.0), ("SHOPEE", 103.0)]
    assert waits == [3.0]
    assert first["action"]["status"] == "PARTIAL_FAILED"
    assert first["action"]["retry_allowed"] is True
    assert first["external_writes_performed"] == [
        "miaoshou:collectbox:claim:tiktok"
    ]
    assert first["external_write_count"] == 1
    by_platform = {
        row["platform"]: row for row in first["action"]["platforms"]
    }
    assert by_platform["TIKTOK"]["outcome"] == IMPORTED
    assert by_platform["TIKTOK"]["platform_detail_id_digest"]
    assert by_platform["SHOPEE"]["status"] == FAILED_RETRYABLE
    assert by_platform["SHOPEE"]["retry_allowed"] is True
    serialized = json.dumps(first)
    assert common_id not in serialized
    assert "9001" not in serialized

    restarted = CollectBoxActionStore(path)
    persisted = restarted.status(plan_id=plan["plan_id"])
    assert persisted == first

    retry_calls = []

    def retry_adapter(request):
        retry_calls.append((request.platform, now()))
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=ALREADY_PRESENT,
            platform_detail_id=7002,
            external_writes=(),
            external_write_count=0,
            receipt_evidence={"checks": {"already_present_exact": True}},
        )

    second = restarted.start(
        plan=plan,
        common_collect_box_detail_id=common_id,
        adapter=retry_adapter,
        now=now,
        wait=wait,
    )
    assert retry_calls == [("SHOPEE", 106.0)]
    assert second["action"]["status"] == "SUCCEEDED"
    assert second["action"]["terminal"] is True
    assert second["canonical_next_action"] is None
    assert {
        row["platform"]: row["attempt_count"]
        for row in second["action"]["platforms"]
    } == {"TIKTOK": 1, "SHOPEE": 2}

    replay = restarted.start(
        plan=plan,
        common_collect_box_detail_id=common_id,
        adapter=lambda _request: pytest.fail("exact success replay called adapter"),
        now=now,
        wait=wait,
    )
    assert replay == second


def test_adapter_exception_after_invocation_is_reconciliation_not_retry(
    tmp_path,
):
    store = CollectBoxActionStore(tmp_path / "platform.db")

    def adapter(_request):
        raise RuntimeError("raw transport detail must not be public")

    projection = store.start(
        plan=_plan(),
        common_collect_box_detail_id=_plan()["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )

    first = projection["action"]["platforms"][0]
    assert first["status"] == RECONCILIATION_REQUIRED
    assert first["retry_allowed"] is False
    assert first["external_writes"]["count"] is None
    assert "raw transport detail" not in json.dumps(projection)


def test_plan_identity_and_result_contracts_fail_closed(tmp_path):
    plan = _plan()
    identity = approved_plan_identity(plan)
    assert identity["plan_id"] == plan["plan_id"]
    assert identity["targets_digest"]

    for malformed in (
        {**plan, "payload": {}, "product_revision": 31},
        {**plan, "payload_digest": "bad"},
        {**plan, "targets": ["tiktok:MX", "tiktok:MX"]},
        {**plan, "approval": {"status": "APPROVED", "approved_by": "Other"}},
    ):
        with pytest.raises(ValueError):
            approved_plan_identity(malformed)

    with pytest.raises(ValueError):
        CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id="detail",
            external_writes=(),
            external_write_count=0,
        )

    path = tmp_path / "platform.db"
    store = CollectBoxActionStore(path)
    store.preview(
        plan=plan,
        common_collectbox_identity_digest=common_collectbox_identity_digest(
            plan["plan_id"],
            plan["product_id"],
        ),
    )
    assert path.exists() is False


def test_get_purity_with_preexisting_unrelated_sqlite_database(tmp_path):
    path = tmp_path / "platform.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('preserve')")
    before = path.read_bytes()

    store = CollectBoxActionStore(path)
    assert store.status(plan_id=_plan()["plan_id"]) is None
    projection = store.preview(
        plan=_plan(),
        common_collectbox_identity_digest=common_collectbox_identity_digest(
            _plan()["plan_id"], _plan()["product_id"]
        ),
    )

    assert projection["persisted"] is False
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ] == ["unrelated"]


def test_startup_recovery_terminalizes_interrupted_dispatch_without_retry(
    tmp_path,
):
    path = tmp_path / "platform.db"
    store = CollectBoxActionStore(path)

    def interrupted(_request):
        raise KeyboardInterrupt("simulated process death")

    with pytest.raises(KeyboardInterrupt):
        store.start(
            plan=_plan(),
            common_collect_box_detail_id=_plan()["product_id"],
            adapter=interrupted,
            now=lambda: 100.0,
            wait=lambda _seconds: None,
        )
    before = store.status(plan_id=_plan()["plan_id"])
    assert before["action"]["status"] == "RUNNING"

    restarted = CollectBoxActionStore(path)
    assert restarted.recover_interrupted(now=lambda: 200.0) == 1
    recovered = restarted.status(plan_id=_plan()["plan_id"])
    first = recovered["action"]["platforms"][0]
    assert recovered["action"]["status"] == "PARTIAL_FAILED"
    assert recovered["action"]["terminal"] is True
    assert first["status"] == RECONCILIATION_REQUIRED
    assert first["external_writes"] == {
        "count": None,
        "classes": ["miaoshou:collectbox:claim:tiktok"],
    }
    assert restarted.start(
        plan=_plan(),
        common_collect_box_detail_id=_plan()["product_id"],
        adapter=lambda _request: pytest.fail("recovery replay invoked adapter"),
        now=lambda: 201.0,
        wait=lambda _seconds: None,
    ) == recovered


@pytest.mark.parametrize(
    "detail_id",
    [True, 0, -1, 1.0, "", "01", "not-numeric"],
)
def test_success_rejects_noncanonical_platform_detail_identity(detail_id):
    with pytest.raises(ValueError):
        CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id=detail_id,
            external_writes=("miaoshou:collectbox:claim:tiktok",),
            external_write_count=1,
        )
