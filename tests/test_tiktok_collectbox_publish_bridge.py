"""Regression gate for collect-box preparation -> TikTok publication.

This test intentionally crosses the real HTTP handler, durable collect-box
ledger, durable ReleaseStore/one-click ledger, production adapter registry,
and worker.  The only substituted boundary is the Miaoshou transport.
"""

from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
import sqlite3
import threading
import urllib.error
import urllib.request

from domains.channel_operations.oneclick_release_adapters import (
    production_adapter_registry,
)
from modules.miaoshou import oneclick_release as miaoshou
from modules.miaoshou.tiktok_publisher import (
    PUBLISH_PATH as INDEPENDENT_PUBLISH_PATH,
    READ_SHOP_DRAFT_PATH,
    READ_SITE_DRAFT_PATH,
    MiaoshouTikTokTransport,
)
from modules.products import server as product_server
from shared_platform import release_store as release_store_module
from shared_platform.collectbox_action import (
    ALREADY_PRESENT,
    RECONCILIATION_REQUIRED,
    SUCCEEDED,
    CollectBoxActionStore,
    CollectBoxPlatformResult,
    CollectBoxTargetDetailIdentity,
    CollectBoxTargetOutcome,
    approved_plan_identity,
)
from shared_platform.oneclick_release_controlplane import (
    OneClickReleaseStore,
    OneClickReleaseWorker,
)
from shared_platform.release_store import ReleaseStore
from tests.test_oneclick_release_controlplane import (
    _plan_payload as _controlplane_plan_payload,
)
from tests.test_oneclick_miaoshou_direct_store import (
    _plan_payload as _miaoshou_plan_payload,
    _tiktok_category_decisions,
)
from tests.test_tiktok_independent_publisher import (
    FakeLowestTransport,
    _publisher as _independent_publisher,
)
from domains.channel_operations.tiktok_publisher import TikTokPublisher


TIKTOK_TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
)
EXPECTED_SHOP_IDS = {
    "tiktok:LH_PH": 7676267,
    "tiktok:LH_MY": 13295169,
    "tiktok:LH_TH": 13295228,
    "tiktok:LH_VN": 13295291,
    "tiktok:MX": 16265910,
    "tiktok:GB": 10204699,
}


def _approved_tiktok_context(tmp_path):
    payload = _controlplane_plan_payload(targets=list(TIKTOK_TARGETS))
    representative = _miaoshou_plan_payload("tiktok:LH_PH")
    payload["product_facts"] = representative["product_facts"]
    payload["listing_copy"] = {
        **representative["listing_copy"],
        "candidates": [
            _miaoshou_plan_payload(target)["listing_copy"]["candidates"][0]
            for target in TIKTOK_TARGETS
        ],
    }
    payload["pricing"] = {
        "selected_targets": {
            target: _miaoshou_plan_payload(target)["pricing"][
                "selected_targets"
            ][target]
            for target in TIKTOK_TARGETS
        }
    }
    payload["approved_tiktok_category_decisions"] = (
        _tiktok_category_decisions(TIKTOK_TARGETS)
    )
    release = ReleaseStore(tmp_path / "release.db")
    created = release.create_plan(payload)
    release.approve_plan(
        created["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=created["confirmation_token"],
    )
    plan = release.get_plan(created["plan_id"])
    release.start_run(created["plan_id"])
    return release, plan


def _post_json(url: str, payload: dict[str, object]):
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


def _persist_collectbox_result(
    store: CollectBoxActionStore,
    plan,
    *,
    include_details: bool = True,
    omit_detail_targets: tuple[str, ...] = (),
    restart_request_id: str | None = None,
):
    def adapter(request):
        if request.platform == "SHOPEE":
            return CollectBoxPlatformResult(
                status=SUCCEEDED,
                outcome=ALREADY_PRESENT,
                platform_detail_id="88002",
                external_writes=(),
                external_write_count=0,
            )
        outcomes = tuple(
            CollectBoxTargetOutcome(
                target_label=label,
                status=("FAILED" if label == "tiktok:GB" else SUCCEEDED),
                error_code=(
                    "target_preparation_failed"
                    if label == "tiktok:GB"
                    else None
                ),
                detail_digest=("f" * 64 if label == "tiktok:GB" else None),
            )
            for label in TIKTOK_TARGETS
        )
        writes = (
            "miaoshou:collectbox:claim:tiktok",
            *(
                f"miaoshou:collectbox:tiktok:detail:update:{label}"
                for label in TIKTOK_TARGETS
            ),
        )
        return CollectBoxPlatformResult(
            status=RECONCILIATION_REQUIRED,
            external_writes=writes,
            external_write_count=len(writes),
            target_outcomes=outcomes,
            target_detail_identities=(
                tuple(
                    CollectBoxTargetDetailIdentity(
                        target_label=label,
                        detail_id=str(91000 + index),
                        shop_id=str(EXPECTED_SHOP_IDS[label]),
                    )
                    for index, label in enumerate(TIKTOK_TARGETS, start=1)
                    if label not in omit_detail_targets
                )
                if include_details
                else ()
            ),
            error_category="CHANNEL",
            error_code="collectbox_platform_preparation_partial",
            error_detail="GB is terminal while five approved drafts are exact",
        )

    projection = store.start(
        plan=plan,
        common_collect_box_detail_id=plan["payload"]["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
        restart_existing=restart_request_id is not None,
        restart_request_id=restart_request_id,
    )
    tiktok = next(
        row
        for row in projection["action"]["platforms"]
        if row["platform"] == "TIKTOK"
    )
    if restart_request_id is None:
        assert product_server._collectbox_platform_row_publishable(
            tiktok, "TIKTOK"
        ) is True
    return projection


def _start_tiktok_through_handler(
    release,
    plan,
    monkeypatch,
    *,
    publisher_factory=None,
):
    woken: list[str] = []
    monkeypatch.setattr(
        release_store_module, "default_release_store", lambda: release
    )
    monkeypatch.setattr(
        product_server,
        "_wake_oneclick_worker",
        lambda job_id: woken.append(job_id),
    )
    identity = approved_plan_identity(plan)
    request_body = {
        "offer_id": identity["offer_id"],
        "plan_id": identity["plan_id"],
        "product_revision": identity["product_revision"],
        "payload_digest": identity["payload_digest"],
        "targets_digest": identity["targets_digest"],
        "confirmation_token": plan["confirmation_token"],
        "publication_targets": list(plan["targets"]),
        "confirm_publish": True,
    }
    transport = None
    try:
        snapshot = product_server._build_approved_tiktok_publish_snapshot(
            request_body
        )
    except ValueError:
        monkeypatch.setattr(
            product_server,
            "_tiktok_publisher",
            lambda: pytest.fail("invalid snapshot must not reach publisher"),
        )
    else:
        factory = publisher_factory or _independent_publisher
        publisher, transport = factory(snapshot)
        monkeypatch.setattr(
            product_server, "_tiktok_publisher", lambda: publisher
        )
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _post_json(
            (
                f"http://127.0.0.1:{server.server_address[1]}"
                "/api/product-workspace/publish-tiktok"
            ),
            request_body,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return status, body, woken, transport


def test_persisted_collectbox_drafts_dispatch_six_tiktok_publish_tasks(
    tmp_path, monkeypatch
):
    release, plan = _approved_tiktok_context(tmp_path)
    collectbox = CollectBoxActionStore(release.path)
    projection = _persist_collectbox_result(collectbox, plan)
    public_text = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    assert all(str(91000 + index) not in public_text for index in range(1, 7))

    status, body, woken, transport = _start_tiktok_through_handler(
        release, plan, monkeypatch
    )

    assert status == 200
    assert body["success"] is True
    assert body["platform"] == "TIKTOK"
    assert body["successful_target_count"] == 6
    assert woken == []
    assert body["external_write_count"] == 7
    assert body["write_request_count"] == 7
    assert transport is not None
    read_calls = [
        call
        for call in transport.calls
        if call[0] in {READ_SITE_DRAFT_PATH, READ_SHOP_DRAFT_PATH}
    ]
    publish_calls = [
        body
        for path, body in transport.calls
        if path == INDEPENDENT_PUBLISH_PATH
    ]
    assert len(read_calls) == 6
    assert len(publish_calls) == 6
    assert {
        (int(body["detailIds"][0]), int(body["shopIds"][0]))
        for body in publish_calls
    } == {
        (91000 + index, EXPECTED_SHOP_IDS[label])
        for index, label in enumerate(TIKTOK_TARGETS, start=1)
    }


def test_six_tiktok_publish_calls_each_target_once_without_oneclick_job(
    tmp_path, monkeypatch
):
    release, plan = _approved_tiktok_context(tmp_path)
    _persist_collectbox_result(CollectBoxActionStore(release.path), plan)
    status, body, woken, transport = _start_tiktok_through_handler(
        release, plan, monkeypatch
    )

    assert status == 200
    assert body["success"] is True
    assert body["successful_target_count"] == 6
    assert woken == []
    assert transport is not None
    assert len(
        [call for call in transport.calls if call[0] == INDEPENDENT_PUBLISH_PATH]
    ) == 6
    assert OneClickReleaseStore(release.path).get_job(plan_id=plan["plan_id"]) is None


def test_old_collectbox_receipt_without_internal_proof_is_409_zero_mutation(
    tmp_path, monkeypatch
):
    release, plan = _approved_tiktok_context(tmp_path)
    _persist_collectbox_result(
        CollectBoxActionStore(release.path),
        plan,
        include_details=False,
    )
    with sqlite3.connect(release.path) as connection:
        before_runs = connection.execute(
            "SELECT COUNT(*) FROM release_runs WHERE plan_id = ?",
            (plan["plan_id"],),
        ).fetchone()[0]

    status, body, woken, _transport = _start_tiktok_through_handler(
        release, plan, monkeypatch
    )

    assert status == 409
    assert body["error"]["code"] == "tiktok_approved_snapshot_invalid"
    assert body["external_write_count"] == 0
    assert woken == []
    with sqlite3.connect(release.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_runs WHERE plan_id = ?",
            (plan["plan_id"],),
        ).fetchone()[0] == before_runs
    control = OneClickReleaseStore(release.path)
    assert control.get_job(plan_id=plan["plan_id"]) is None


def test_publish_transport_ambiguity_records_one_possible_write_per_target(
    tmp_path, monkeypatch
):
    release, plan = _approved_tiktok_context(tmp_path)
    _persist_collectbox_result(CollectBoxActionStore(release.path), plan)
    def ambiguous_factory(snapshot):
        fake = FakeLowestTransport(snapshot)

        def ambiguous_post(path, payload):
            response = fake(path, payload)
            if path == INDEPENDENT_PUBLISH_PATH:
                raise TimeoutError("response lost after submission")
            return response

        return TikTokPublisher(
            transport=MiaoshouTikTokTransport(post=ambiguous_post)
        ), fake

    status, body, woken, transport = _start_tiktok_through_handler(
        release, plan, monkeypatch, publisher_factory=ambiguous_factory
    )
    assert status == 200
    assert body["success"] is False
    assert body["successful_target_count"] == 0
    assert body["unknown_target_count"] == 6
    assert body["external_write_count"] is None
    assert body["write_request_count"] == 7
    assert woken == []
    assert transport is not None
    assert len(
        [call for call in transport.calls if call[0] == INDEPENDENT_PUBLISH_PATH]
    ) == 6
    assert OneClickReleaseStore(release.path).get_job(plan_id=plan["plan_id"]) is None


def test_missing_gb_detail_proof_blocks_exact_six_target_snapshot_prewrite(
    tmp_path, monkeypatch
):
    release, plan = _approved_tiktok_context(tmp_path)
    _persist_collectbox_result(
        CollectBoxActionStore(release.path),
        plan,
        omit_detail_targets=("tiktok:GB",),
    )
    status, body, woken, transport = _start_tiktok_through_handler(
        release, plan, monkeypatch
    )
    assert status == 409
    assert body["success"] is False
    assert body["error"]["code"] == "tiktok_approved_snapshot_invalid"
    assert body["external_write_count"] == 0
    assert woken == []
    assert transport is None
    assert OneClickReleaseStore(release.path).get_job(plan_id=plan["plan_id"]) is None


def test_prepare_job_pins_one_collectbox_receipt_during_concurrent_reimport(
    tmp_path, monkeypatch
):
    release, plan = _approved_tiktok_context(tmp_path)
    collectbox = CollectBoxActionStore(release.path)
    _persist_collectbox_result(collectbox, plan)
    pinned = collectbox.internal_tiktok_publish_contexts(
        plan_id=plan["plan_id"]
    )
    pinned_action = pinned["tiktok:LH_PH"]["action_id"]

    registry = production_adapter_registry()
    original_contexts = CollectBoxActionStore.internal_tiktok_publish_contexts
    context_reads = 0

    def contexts_with_reimport(self, *, plan_id):
        nonlocal context_reads
        contexts = original_contexts(self, plan_id=plan_id)
        context_reads += 1
        _persist_collectbox_result(
            collectbox,
            plan,
            restart_request_id="00000000-0000-4000-8000-000000000002",
        )
        return contexts

    monkeypatch.setattr(
        CollectBoxActionStore,
        "internal_tiktok_publish_contexts",
        contexts_with_reimport,
    )
    control = OneClickReleaseStore(release.path)
    run = release.start_run(plan["plan_id"])
    job = control.ensure_job(
        plan=release.get_plan(plan["plan_id"]),
        run=run,
        product_revision=plan["payload"]["product_revision"],
        registry=registry,
    )
    prepared = control.prepare_job(job["job_id"], registry)
    with sqlite3.connect(control.path) as connection:
        prerequisite_actions = [
            json.loads(row[0])["prerequisite"]["action_id"]
            for row in connection.execute(
                """
                SELECT command_json
                FROM oneclick_release_targets
                WHERE job_id = ? AND target_label LIKE 'tiktok:%'
                """,
                (job["job_id"],),
            ).fetchall()
            if row[0]
        ]
    latest = original_contexts(
        collectbox,
        plan_id=plan["plan_id"]
    )["tiktok:LH_PH"]

    assert context_reads == 1
    assert len(prerequisite_actions) == 6, [
        (row["target_label"], row["status"], row.get("reason"))
        for row in prepared["targets"]
    ]
    assert set(prerequisite_actions) == {pinned_action}
    assert latest["action_id"] != pinned_action
