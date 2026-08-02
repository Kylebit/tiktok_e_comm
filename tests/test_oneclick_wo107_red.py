"""WO-107 red tests for the last known one-click compatibility gaps.

These tests intentionally describe the required product behaviour before the
production fix exists.  Keep them in the permanent regression suite.
"""

import sqlite3

import pytest

from modules.products import server as product_server
from shared_platform.oneclick_release_controlplane import (
    OneClickReleaseStore,
    OneClickReleaseWorker,
    SystemicIdentityError,
)
from tests.test_oneclick_release_controlplane import (
    _approved_context,
    _mvp_miaoshou_registry,
)


@pytest.mark.parametrize(
    ("starter", "legacy_targets"),
    [
        (
            "_start_shopee_global_release",
            ["shopee:MY", "shopee:VN", "shopee:promotion:MY"],
        ),
        ("_start_ozon_release", ["ozon:promotion:RU"]),
    ],
)
def test_legacy_job_requires_explicit_successor_instead_of_silent_pending(
    monkeypatch,
    starter,
    legacy_targets,
):
    """A persisted pre-isolation job must never look newly accepted."""

    plan = {"plan_id": "omnichannel:legacy-job"}
    wakes = []

    class ReleaseStore:
        def start_run(self, plan_id):
            assert plan_id == plan["plan_id"]
            return {"run_id": "release-run:legacy"}

        def get_plan(self, plan_id):
            assert plan_id == plan["plan_id"]
            return plan

    class ControlStore:
        def ensure_job(self, **_kwargs):
            return {
                "job_id": "oneclick-job:legacy",
                "phase": "PENDING",
                "targets": [
                    {"target_label": label} for label in legacy_targets
                ],
            }

    context = {
        "plan": plan,
        "payload": {"product_revision": 31},
        "store": ReleaseStore(),
    }
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data: (context, None),
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_adapter_registry",
        lambda: {"miaoshou-direct-store/v1": object()},
    )
    monkeypatch.setattr(
        product_server, "_oneclick_control_store", lambda: ControlStore()
    )
    monkeypatch.setattr(
        product_server,
        "_wake_oneclick_worker",
        lambda job_id: wakes.append(job_id),
    )

    status, body = getattr(product_server, starter)(
        {"confirm_publish": True, "plan_id": plan["plan_id"]}
    )

    assert status == 409
    assert body["error"]["code"] == "legacy_oneclick_job_requires_successor"
    assert body["canonical_next_action"]["action"] == (
        "create_platform_successor_job"
    )
    assert wakes == []


def test_empty_platform_scope_never_appends_zero_target_batch_event(tmp_path):
    """A shared-control-only legacy job is not an executable batch."""

    release, plan, run = _approved_context(
        tmp_path,
        targets=["miaoshou:COMMON"],
    )
    registry = _mvp_miaoshou_registry(("miaoshou:COMMON",))
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )

    with pytest.raises(
        Exception, match="explicit batch target scope is empty"
    ):
        control.start_explicit_batch(job["job_id"])

    with sqlite3.connect(control.path) as connection:
        rows = connection.execute(
            """
            SELECT event_json FROM oneclick_release_events
            WHERE job_id = ? AND event_type = 'EXPLICIT_BATCH_STARTED'
            """,
            (job["job_id"],),
        ).fetchall()
    assert rows == []


def test_worker_prepare_systemic_identity_error_is_durable_and_public():
    """Prepare-time systemic failure must not escape or disappear."""

    recorded = []

    class Store:
        def get_job(self, *, job_id):
            return {"job_id": job_id, "phase": "PENDING"}

        def prepare_job(self, job_id, registry):
            assert registry == {}
            raise SystemicIdentityError("fixture immutable identity drift")

        def record_systemic_stop(self, job_id, error):
            recorded.append((job_id, str(error)))

    worker = OneClickReleaseWorker(
        Store(),
        lambda: {},
        dispatch_enabled=lambda: True,
    )

    assert worker.advance_once("oneclick-job:systemic") is True
    assert recorded == [
        ("oneclick-job:systemic", "fixture immutable identity drift")
    ]


def test_tiktok_partial_collectbox_allows_five_successes_and_gb_waiver(
    monkeypatch,
):
    """GB title/attribute mismatch is waived for this round, not batch-fatal."""

    class CollectboxStore:
        @staticmethod
        def status(*, plan_id):
            assert plan_id == "omnichannel:tiktok-partial"
            successes = [
                "tiktok:LH_PH",
                "tiktok:LH_MY",
                "tiktok:LH_TH",
                "tiktok:LH_VN",
                "tiktok:MX",
            ]
            return {
                "action": {
                    "status": "PARTIAL_FAILED",
                    "platforms": [
                        {
                            "platform": "TIKTOK",
                            "status": "PARTIAL_FAILED",
                            "target_outcomes": [
                                *(
                                    {
                                        "target_label": label,
                                        "status": "SUCCEEDED",
                                        "error_code": None,
                                        "detail_digest": None,
                                    }
                                    for label in successes
                                ),
                                {
                                    "target_label": "tiktok:GB",
                                    "status": "FAILED",
                                    "error_code": (
                                        "approved_detail_readback_mismatch"
                                    ),
                                    "detail_digest": "a" * 64,
                                },
                            ],
                        }
                    ],
                }
            }

    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: CollectboxStore(),
    )

    assert product_server._collectbox_platform_succeeded(
        "omnichannel:tiktok-partial",
        "TIKTOK",
    ) is True
