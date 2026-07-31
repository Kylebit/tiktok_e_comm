import json
import json
import sqlite3
import threading

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


def test_tiktok_reconciliation_does_not_block_shopee_in_same_batch(tmp_path):
    store = CollectBoxActionStore(tmp_path / "platform.db")
    plan = _plan()
    seen = []

    def adapter(request):
        seen.append(request.platform)
        if request.platform == "TIKTOK":
            return CollectBoxPlatformResult(
                status=RECONCILIATION_REQUIRED,
                external_writes=("miaoshou:collectbox:claim:tiktok",),
                external_write_count=1,
                error_category="UNKNOWN",
                error_code="tiktok_result_unknown",
                error_detail="TikTok result is unknown",
            )
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id="71002",
            external_writes=("miaoshou:collectbox:claim:shopee",),
            external_write_count=1,
            receipt_evidence={"checks": {"readback_exact": True}},
        )

    projection = store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )

    assert seen == ["TIKTOK", "SHOPEE"]
    assert projection["action"]["status"] == "PARTIAL_FAILED"
    assert projection["action"]["terminal"] is True
    assert projection["action"]["start_allowed"] is True
    assert projection["canonical_next_action"] == {
        "action": "restart_collectbox_action",
        "target_focus": None,
    }


def test_terminal_action_can_restart_full_batch_and_preserves_history(tmp_path):
    path = tmp_path / "platform.db"
    store = CollectBoxActionStore(path)
    plan = _plan()
    seen = []

    def adapter(request):
        seen.append(request.platform)
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id=(
                "71001" if request.platform == "TIKTOK" else "71002"
            ),
            external_writes=(
                f"miaoshou:collectbox:claim:{request.platform.lower()}",
            ),
            external_write_count=1,
            receipt_evidence={"checks": {"readback_exact": True}},
        )

    first = store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    with sqlite3.connect(path) as connection:
        legacy_before = {
            "action": connection.execute(
                "SELECT * FROM collectbox_actions"
            ).fetchall(),
            "platforms": connection.execute(
                """
                SELECT * FROM collectbox_action_platforms
                ORDER BY platform
                """
            ).fetchall(),
        }
    request_id = "11111111-1111-4111-8111-111111111111"
    second = store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 200.0,
        wait=lambda _seconds: None,
        restart_existing=True,
        restart_request_id=request_id,
    )
    replay = store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 300.0,
        wait=lambda _seconds: None,
        restart_existing=True,
        restart_request_id=request_id,
    )

    assert seen == ["TIKTOK", "SHOPEE", "TIKTOK", "SHOPEE"]
    assert first["action"]["status"] == "SUCCEEDED"
    assert second["action"]["status"] == "SUCCEEDED"
    assert replay == second
    assert all(
        row["attempt_count"] == 1
        for row in second["action"]["platforms"]
    )
    with sqlite3.connect(path) as connection:
        batches = connection.execute(
            """
            SELECT batch_sequence, reimport_request_id
            FROM collectbox_action_batches
            WHERE plan_id = ?
            ORDER BY batch_sequence
            """,
            (plan["plan_id"],),
        ).fetchall()
        legacy_after = {
            "action": connection.execute(
                "SELECT * FROM collectbox_actions"
            ).fetchall(),
            "platforms": connection.execute(
                """
                SELECT * FROM collectbox_action_platforms
                ORDER BY platform
                """
            ).fetchall(),
        }
    assert batches == [(2, request_id)]
    assert legacy_after == legacy_before


def test_same_reimport_request_concurrent_start_has_one_batch_and_one_execution(
    tmp_path,
    monkeypatch,
):
    """Two exact simultaneous clicks must join one idempotent execution."""

    path = tmp_path / "platform.db"
    store = CollectBoxActionStore(path)
    plan = _plan()
    calls = []
    calls_lock = threading.Lock()
    winner_started = threading.Event()
    release_winner = threading.Event()
    duplicate_waiting = threading.Event()
    exercise_concurrency = threading.Event()

    def adapter(request):
        with calls_lock:
            calls.append(request.platform)
        if (
            exercise_concurrency.is_set()
            and request.platform == "TIKTOK"
            and not winner_started.is_set()
        ):
            winner_started.set()
            assert release_winner.wait(timeout=5)
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id=(
                "71001" if request.platform == "TIKTOK" else "71002"
            ),
            external_writes=(
                f"miaoshou:collectbox:claim:{request.platform.lower()}",
            ),
            external_write_count=1,
            receipt_evidence={"checks": {"readback_exact": True}},
        )

    store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    calls.clear()
    exercise_concurrency.set()
    winner_started.clear()
    original_wait = store._wait_for_restart_batch

    def observed_wait(action_id, **kwargs):
        duplicate_waiting.set()
        return original_wait(action_id, **kwargs)

    monkeypatch.setattr(store, "_wait_for_restart_batch", observed_wait)
    request_id = "44444444-4444-4444-8444-444444444444"
    results = []
    errors = []

    def invoke_start():
        try:
            results.append(
                store.start(
                    plan=plan,
                    common_collect_box_detail_id=plan["product_id"],
                    adapter=adapter,
                    now=lambda: 200.0,
                    wait=lambda _seconds: None,
                    restart_existing=True,
                    restart_request_id=request_id,
                )
            )
        except Exception as error:  # captured for the desired assertion
            errors.append(error)

    threads = [threading.Thread(target=invoke_start) for _ in range(2)]
    threads[0].start()
    assert winner_started.wait(timeout=5)
    threads[1].start()
    assert duplicate_waiting.wait(timeout=5)
    release_winner.set()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    with sqlite3.connect(path) as connection:
        batch_count = connection.execute(
            """
            SELECT COUNT(*) FROM collectbox_action_batches
            WHERE plan_id = ? AND reimport_request_id = ?
            """,
            (plan["plan_id"], request_id),
        ).fetchone()[0]

    assert batch_count == 1
    assert calls.count("TIKTOK") == 1
    assert calls.count("SHOPEE") == 1
    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert results[0]["action"]["status"] == "SUCCEEDED"


def test_creating_batch_three_does_not_mutate_batch_two_receipt_rows(tmp_path):
    path = tmp_path / "platform.db"
    store = CollectBoxActionStore(path)
    plan = _plan()

    def adapter(request):
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id=(
                "71001" if request.platform == "TIKTOK" else "71002"
            ),
            external_writes=(
                f"miaoshou:collectbox:claim:{request.platform.lower()}",
            ),
            external_write_count=1,
            receipt_evidence={"checks": {"readback_exact": True}},
        )

    store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 200.0,
        wait=lambda _seconds: None,
        restart_existing=True,
        restart_request_id="55555555-5555-4555-8555-555555555555",
    )

    def batch_two_snapshot():
        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                """
                SELECT platform, receipt_json, receipt_digest,
                       external_writes_json, external_write_count
                FROM collectbox_action_batch_platforms
                WHERE action_id = (
                    SELECT action_id FROM collectbox_action_batches
                    WHERE plan_id = ? AND batch_sequence = 2
                )
                ORDER BY platform
                """,
                (plan["plan_id"],),
            ).fetchall()
        return rows, json.dumps(rows, separators=(",", ":")).encode()

    rows_before, bytes_before = batch_two_snapshot()
    store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 300.0,
        wait=lambda _seconds: None,
        restart_existing=True,
        restart_request_id="66666666-6666-4666-8666-666666666666",
    )
    rows_after, bytes_after = batch_two_snapshot()

    assert rows_before == rows_after
    assert bytes_before == bytes_after


def test_interrupted_reimport_batch_recovers_without_redispatch(tmp_path):
    path = tmp_path / "platform.db"
    store = CollectBoxActionStore(path)
    plan = _plan()

    def adapter(request):
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id=(
                "71001" if request.platform == "TIKTOK" else "71002"
            ),
            external_writes=(
                f"miaoshou:collectbox:claim:{request.platform.lower()}",
            ),
            external_write_count=1,
            receipt_evidence={"checks": {"readback_exact": True}},
        )

    store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    store.start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 200.0,
        wait=lambda _seconds: None,
        restart_existing=True,
        restart_request_id="33333333-3333-4333-8333-333333333333",
    )
    with sqlite3.connect(path) as connection:
        batch_id = connection.execute(
            """
            SELECT action_id FROM collectbox_action_batches
            WHERE plan_id = ? AND batch_sequence = 2
            """,
            (plan["plan_id"],),
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE collectbox_action_batch_platforms
            SET status = 'RUNNING', receipt_json = NULL,
                receipt_digest = NULL, error_json = NULL
            WHERE action_id = ? AND platform = 'TIKTOK'
            """,
            (batch_id,),
        )
        connection.execute(
            """
            UPDATE collectbox_action_batches
            SET status = 'RUNNING', completed_at = NULL
            WHERE action_id = ?
            """,
            (batch_id,),
        )
        connection.commit()

    assert store.recover_interrupted(now=lambda: 300.0) == 1
    recovered = store.status(plan_id=plan["plan_id"])
    assert recovered["action"]["status"] == "PARTIAL_FAILED"
    assert recovered["action"]["start_allowed"] is True
    assert recovered["canonical_next_action"] == {
        "action": "restart_collectbox_action",
        "target_focus": None,
    }
    tiktok = recovered["action"]["platforms"][0]
    assert tiktok["status"] == RECONCILIATION_REQUIRED
    assert tiktok["external_writes"]["count"] is None


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


def test_partial_action_survives_restart_and_reimports_both_platforms(
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
    assert first["action"]["retry_allowed"] is False
    assert first["action"]["terminal"] is True
    assert first["canonical_next_action"] == {
        "action": "restart_collectbox_action",
        "target_focus": None,
    }
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
        restart_existing=True,
        restart_request_id="22222222-2222-4222-8222-222222222222",
    )
    assert retry_calls == [("TIKTOK", 103.0), ("SHOPEE", 106.0)]
    assert second["action"]["status"] == "SUCCEEDED"
    assert second["action"]["terminal"] is True
    assert second["canonical_next_action"] == {
        "action": "restart_collectbox_action",
        "target_focus": None,
    }
    assert {
        row["platform"]: row["attempt_count"]
        for row in second["action"]["platforms"]
    } == {"TIKTOK": 1, "SHOPEE": 1}

    replay = restarted.start(
        plan=plan,
        common_collect_box_detail_id=common_id,
        adapter=lambda _request: pytest.fail("exact success replay called adapter"),
        now=now,
        wait=wait,
        restart_existing=True,
        restart_request_id="22222222-2222-4222-8222-222222222222",
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
