from __future__ import annotations

from modules.products import server as product_server
from modules.products import release_adapters
from modules.sourcing import new_product_workbench
from domains.channel_operations.release_executor import (
    AdapterExecutionResult,
    AdapterRegistration,
)
from shared_platform import release_control, release_store
from shared_platform.release_store import ReleaseStore


def _dashboard() -> dict:
    targets = ["miaoshou:COMMON", "tiktok:MX"]
    return {
        "ok": True,
        "product": {
            "offer_id": "3828540231",
            "seller_sku_candidate": "0952",
            "revision": 41,
            "actual_product_approved": True,
            "actual_approval": {
                "approval_id": "product-approval:v1",
                "package_id": "product:3828540231:0952",
                "input_fingerprint": "facts-fingerprint",
            },
        },
        "content": {
            "approved": True,
            "approval_status": "approved",
            "package_id": "content:3828540231:v1",
            "strategy": "source_only",
            "images": [
                {
                    "position": 1,
                    "image_url": "https://assets.example/main.jpg",
                    "artifact_id": "source-1",
                    "audit_id": "review-1",
                    "asset_type": "source",
                    "decision_source": "review.image_actions",
                }
            ],
            "video_urls": ["https://assets.example/main.mp4"],
        },
        "publication_scope": {"selected_labels": targets},
        "pricing_review": {
            "status": "ready",
            "schema_version": "pricing-v1",
            "target_pricing": {
                "miaoshou:COMMON": {"status": "ready"},
                "tiktok:MX": {"status": "ready"},
            },
            "workbench_exchange_rates": {"MXN": 2.1},
            "shopee_exchange_rates": {},
            "ozon_exchange_rates": {},
        },
        "omnichannel_preview": {
            "available": True,
            "plan_id": "omnichannel:v1-test",
            "approval_summary": {"approval_scope_digest": "scope-digest"},
            "targets": [
                {
                    "channel": "miaoshou",
                    "site": "COMMON",
                    "adapter": "new_product_workbench_miaoshou_commit",
                    "preflights": [
                        {
                            "code": "audited_adapter_site",
                            "passed": True,
                            "detail": "legacy path found",
                        }
                    ],
                },
                {
                    "channel": "tiktok",
                    "site": "MX",
                    "adapter": "miaoshou_tiktok_publish",
                    "preflights": [
                        {
                            "code": "audited_adapter_site",
                            "passed": True,
                            "detail": "legacy path found",
                        }
                    ],
                },
            ],
            "blockers": [],
        },
        "actual_release_gate": {"ready": True, "blockers": []},
    }


def _request(view: dict, **extra) -> dict:
    plan = view["release_v1"]["plan"]
    return {
        "offer_id": "3828540231",
        "seller_sku": "0952",
        "publication_targets": ["miaoshou:COMMON", "tiktok:MX"],
        "plan_id": plan["plan_id"],
        "confirmation_token": plan["confirmation_token"],
        **extra,
    }


def test_formal_v1_preview_is_write_free_and_reports_executable_registry(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)

    view = product_server._product_workspace_view(_dashboard())

    assert view["workspace_mode"] == "formal_v1"
    assert view["release_v1"]["eligible_for_plan_approval"] is True
    assert view["release_v1"]["plan"]["targets"] == [
        "miaoshou:COMMON",
        "tiktok:MX",
    ]
    assert view["release_v1"]["adapter_blockers"] == []
    assert not store.path.exists()


def test_release_plan_approval_and_miaoshou_prepare_are_exact_and_durable(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: _dashboard(),
    )
    writes: list[str] = []

    def fake_write(offer_id: str) -> dict:
        writes.append(offer_id)
        return {
            "written_to_miaoshou": True,
            "verified": True,
            "offer_id": offer_id,
        }

    monkeypatch.setattr(new_product_workbench, "write_miaoshou_draft", fake_write)
    view = product_server._product_workspace_view(_dashboard())
    status, approval = product_server._approve_release_plan_locally(
        _request(view, approved_by="Kyle", user_approved=True)
    )

    assert status == 200
    assert approval["external_writes_performed"] == []
    assert store.get_plan(view["release_v1"]["plan"]["plan_id"])["status"] == "APPROVED"

    status, prepared = product_server._prepare_miaoshou_release(
        _request(view, confirm_miaoshou_write=True)
    )

    assert status == 200
    assert writes == ["3828540231"]
    common = next(
        row
        for row in prepared["run"]["targets"]
        if row["target_label"] == "miaoshou:COMMON"
    )
    assert common["status"] == "SUCCEEDED"
    assert common["attempts"] == 1

    status, repeated = product_server._prepare_miaoshou_release(
        _request(view, confirm_miaoshou_write=True)
    )
    assert status == 200
    assert repeated["idempotent"] is True
    assert writes == ["3828540231"]


def test_publish_endpoint_executes_unified_adapter_and_persists_readback(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        new_product_workbench,
        "write_miaoshou_draft",
        lambda offer_id: {
            "written_to_miaoshou": True,
            "verified": True,
            "offer_id": offer_id,
        },
    )
    view = product_server._product_workspace_view(_dashboard())
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    # External readback evidence advances the workbench document revision in
    # production. It must not invalidate otherwise identical approved facts.
    dashboard["product"]["revision"] += 1
    calls = []

    def execute(req):
        calls.append(req)
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail="verified",
            external_reference="mx-product-1",
            readback_evidence={
                "source": "fake-official-api",
                "verified": True,
                "title": "approved title",
            },
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: {
            "new_product_workbench_miaoshou_commit": AdapterRegistration(
                adapter_name="new_product_workbench_miaoshou_commit",
                execute=lambda _req: AdapterExecutionResult(
                    True,
                    True,
                    "common",
                ),
                consumes_unified_plan=True,
                validates_confirmation_token=True,
                preserves_idempotency_key=True,
                verifies_readback=True,
            ),
            "miaoshou_tiktok_publish": AdapterRegistration(
                adapter_name="miaoshou_tiktok_publish",
                execute=execute,
                consumes_unified_plan=True,
                validates_confirmation_token=True,
                preserves_idempotency_key=True,
                verifies_readback=True,
            ),
        },
    )

    status, payload = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 200
    assert payload["external_writes_performed"] == ["tiktok:MX"]
    assert len(calls) == 1
    tiktok = next(
        row
        for row in payload["run"]["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert tiktok["status"] == "SUCCEEDED"
    assert tiktok["attempts"] == 1
    assert tiktok["readback"]["evidence"]["source"] == "fake-official-api"
