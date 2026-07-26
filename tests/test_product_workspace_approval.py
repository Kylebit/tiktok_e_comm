from __future__ import annotations

import copy
import json
from http.server import ThreadingHTTPServer
from threading import Thread
import urllib.error
import urllib.request

import pytest

from modules.products.server import Handler, _approve_product_workspace_locally
from modules.sourcing import new_product_workbench
from shared_platform import release_control


def _dashboard(
    state: dict,
    *,
    content_approved: bool = True,
    preview_ready: bool = True,
    blockers: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> dict:
    approval = state.get("product_approval") or {}
    actual_approved = bool(
        approval.get("status") == "approved"
        and approval.get("input_fingerprint") == "fingerprint-123"
    )
    proposed = {
        "approval_id": "simulation",
        "package_id": "product:3828540231:0947",
        "status": "approved",
        "subject_type": "product",
        "subject_id": "3828540231",
        "seller_sku": "0947",
        "content_package_id": "content:3828540231",
        "content_approval_id": "content-approval-1",
        "input_fingerprint": "fingerprint-123",
        "approved_by": "simulation",
        "approved_at": "2026-07-25T00:00:00+00:00",
        "source_reference": "simulation",
        "approval_input_facts": {
            "cost_cny": 8.5,
            "weight_kg": 0.2,
            "package_cm": [30, 20, 2],
            "selected_sites": ["lh_th"],
            "selected_sku_keys": ["default"],
        },
    }
    return {
        "ok": True,
        "product": {
            "offer_id": "3828540231",
            "seller_sku_candidate": "0947",
            "revision": state["_revision"],
            "actual_product_approved": actual_approved,
        },
        "content": {
            "approved": content_approved,
            "blockers": [] if content_approved else ["final content approval is required"],
        },
        "approval_rehearsal": {
            "ready": preview_ready,
            "blockers": list(blockers),
            "warnings": list(warnings),
            "state_patch_preview": {
                "review": {
                    **state["review"],
                    "seller_sku": "0947",
                    "fields_locked": True,
                },
                "product_approval": proposed,
            }
            if preview_ready
            else {},
        },
        "publication_rehearsal": {"ready": False, "drafts": []},
        "actual_release_gate": {"ready": False, "blockers": []},
    }


@pytest.fixture
def approval_state(monkeypatch):
    state = {
        "offer_id": "3828540231",
        "_revision": 7,
        "review": {
            "title": "Watercolour floral wall decal",
            "cost_cny": 8.5,
            "image_order": ["https://assets.example/one.jpg"],
        },
        "content_package": {"collect_box_id": "3828540231"},
        "unrelated": {"must": "remain unchanged"},
    }
    saves: list[dict] = []

    def load_state(_offer_id):
        return copy.deepcopy(state)

    def save_state(_offer_id, next_state):
        assert next_state["_revision"] == state["_revision"]
        saves.append(copy.deepcopy(next_state))
        state.clear()
        state.update(copy.deepcopy(next_state))
        state["_revision"] += 1
        return copy.deepcopy(state)

    monkeypatch.setattr(new_product_workbench, "load_state", load_state)
    monkeypatch.setattr(new_product_workbench, "save_state", save_state)
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: _dashboard(state),
    )
    return state, saves


def _approval_request(**overrides) -> dict:
    return {
        "offer_id": "3828540231",
        "seller_sku": "0947",
        "expected_revision": 7,
        "approved_by": "Kyle",
        "user_approved": True,
        **overrides,
    }


def test_local_approval_only_locks_review_and_persists_approval_fact(approval_state):
    state, saves = approval_state
    original = copy.deepcopy(state)

    status, payload = _approve_product_workspace_locally(_approval_request())

    assert status == 200
    assert payload["persisted"] is True
    assert payload["external_writes_performed"] == []
    assert len(saves) == 1
    saved = saves[0]
    assert saved["unrelated"] == original["unrelated"]
    assert saved["content_package"] == original["content_package"]
    assert saved["review"] == {
        **original["review"],
        "seller_sku": "0947",
        "fields_locked": True,
    }
    assert saved["product_approval"]["approved_by"] == "Kyle"
    assert saved["product_approval"]["subject_id"] == "3828540231"
    assert saved["product_approval"]["content_package_id"] == "content:3828540231"
    assert saved["product_approval"]["input_fingerprint"] == "fingerprint-123"
    assert payload["dashboard"]["product"]["actual_product_approved"] is True


def test_local_approval_persists_reviewable_warnings_without_blocking(
    approval_state,
    monkeypatch,
):
    state, saves = approval_state
    warnings = (
        "当前商品标题仍含中文或缺少英文字母；可以先锁定事实，但发布前建议采用平台标题候选",
        "cost_cny does not match the selected SKU price: 9 CNY vs 8.1 CNY",
    )
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: _dashboard(state, warnings=warnings),
    )

    status, payload = _approve_product_workspace_locally(_approval_request())

    assert status == 200
    assert payload["approval_warnings_acknowledged"] == list(warnings)
    assert saves[0]["product_approval"]["approval_warnings_acknowledged"] == list(
        warnings
    )


def test_local_approval_rejects_stale_revision_before_preview_or_write(
    approval_state, monkeypatch
):
    _, saves = approval_state
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: pytest.fail("stale request must not build approval preview"),
    )

    status, payload = _approve_product_workspace_locally(
        _approval_request(expected_revision=6)
    )

    assert status == 409
    assert payload["error"] == "state revision is stale"
    assert payload["current_revision"] == 7
    assert saves == []


def test_local_approval_rejects_an_untrusted_approval_actor(approval_state):
    _, saves = approval_state

    status, payload = _approve_product_workspace_locally(
        _approval_request(approved_by="browser-supplied-user")
    )

    assert status == 400
    assert payload["error"] == "approved_by must be Kyle for this local approval surface"
    assert saves == []


def test_local_approval_rejects_a_stale_browser_sku_candidate(approval_state):
    _, saves = approval_state

    status, payload = _approve_product_workspace_locally(
        _approval_request(seller_sku="0946")
    )

    assert status == 409
    assert payload["error"] == (
        "automatic Seller SKU candidate changed; refresh before approval"
    )
    assert payload["seller_sku"] == "0947"
    assert saves == []


@pytest.mark.parametrize(
    ("content_approved", "preview_ready", "blockers", "expected_error"),
    [
        (
            True,
            False,
            ("seller_sku is already present in the catalog",),
            "product approval preview is not ready",
        ),
    ],
)
def test_local_approval_blocks_readonly_sku_conflicts(
    approval_state,
    monkeypatch,
    content_approved,
    preview_ready,
    blockers,
    expected_error,
):
    state, saves = approval_state
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: _dashboard(
            state,
            content_approved=content_approved,
            preview_ready=preview_ready,
            blockers=blockers,
        ),
    )

    status, payload = _approve_product_workspace_locally(_approval_request())

    assert status == 409
    assert payload["error"] == expected_error
    assert saves == []
    if blockers:
        assert payload["blockers"] == list(blockers)


def test_local_product_facts_approval_is_independent_of_content_approval(
    approval_state,
    monkeypatch,
):
    state, saves = approval_state
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: _dashboard(
            state,
            content_approved=False,
            preview_ready=True,
        ),
    )

    status, payload = _approve_product_workspace_locally(_approval_request())

    assert status == 200
    assert payload["persisted"] is True
    assert len(saves) == 1


def test_identical_current_approval_is_idempotent_without_rewriting(approval_state):
    state, saves = approval_state
    state["review"]["seller_sku"] = "0947"
    state["review"]["fields_locked"] = True
    state["product_approval"] = {
        "status": "approved",
        "input_fingerprint": "fingerprint-123",
    }

    status, payload = _approve_product_workspace_locally(_approval_request())

    assert status == 200
    assert payload["idempotent"] is True
    assert payload["persisted"] is False
    assert payload["external_writes_performed"] == []
    assert saves == []


@pytest.fixture
def product_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(url: str, payload: dict, *, headers: dict[str, str]) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_product_workspace_dashboard_forwards_repeated_publication_targets(
    product_server,
    monkeypatch,
):
    captured = {}

    def build_dashboard(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "publication_scope": {"selected_labels": []}}

    monkeypatch.setattr(release_control, "build_release_dashboard", build_dashboard)

    status, payload = _get(
        product_server
        + "/api/product-workspace/dashboard"
        + "?offer_id=3828540231"
        + "&target=miaoshou%3ACOMMON&target=tiktok%3ATH&target=shopee%3ATH"
    )

    assert status == 200
    assert payload["workspace_mode"] == "formal_v1"
    assert captured["publication_targets"] == [
        "miaoshou:COMMON",
        "tiktok:TH",
        "shopee:TH",
    ]
    assert "seller_sku" not in captured


def test_product_approval_http_requires_same_origin_json_and_explicit_confirmation(
    product_server,
):
    status, payload = _post(
        product_server + "/api/product-workspace/approve",
        _approval_request(),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://attacker.example",
        },
    )
    assert status == 403
    assert payload["error"] == "cross-origin product workflow write rejected"

    status, payload = _post(
        product_server + "/api/product-workspace/approve",
        _approval_request(user_approved=False),
        headers={"Content-Type": "application/json"},
    )
    assert status == 400
    assert payload["error"] == "explicit user_approved=true is required"
