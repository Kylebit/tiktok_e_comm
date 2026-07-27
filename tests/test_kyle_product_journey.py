from __future__ import annotations

import copy
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sqlite3
from threading import Thread
import urllib.error
import urllib.request

import pytest

from modules.products import server as product_server
from modules.sourcing import new_product_workbench
from domains import content_operations
from shared_platform import release_control


ROOT = Path(__file__).resolve().parents[1]
OFFER_ID = "3828540231"


def _preview() -> dict:
    return {
        "ok": True,
        "offer_id": OFFER_ID,
        "mode": "first_review_miaoshou_common_collect_detail",
        "source": {
            "title_source": "Watercolour butterfly wall decal",
            "category": {"id": "wall-decals", "name": "Wall decals"},
            "cost_cny": 8.1,
            "weight_kg": 0.14,
            "package_cm": [30.0, 3.0, 3.0],
            "images": [
                {
                    "url": "https://assets.example/source-main.jpg",
                    "action": "keep",
                }
            ],
            "skus": [
                {
                    "key": "30x90-2pcs",
                    "name": "30*90cm*2pcs; translucent",
                    "price": 8.1,
                },
                {
                    "key": "30x90-custom",
                    "name": "30*90cm*2pcs; custom material",
                    "price": 8.2,
                },
            ],
        },
        "review": {
            "title": "Watercolour butterfly wall decal",
            "seller_sku": "",
            "category": {"id": "wall-decals", "name": "Wall decals"},
            "cost_cny": 8.1,
            "weight_kg": 0.14,
            "package_cm": [30.0, 3.0, 3.0],
            "selected_sites": ["lh_th"],
            "selected_sku_keys": ["30x90-2pcs"],
            "image_actions": [
                {
                    "url": "https://assets.example/source-main.jpg",
                    "action": "keep",
                }
            ],
            "image_order": [],
            "fields_locked": False,
        },
    }


def _dashboard(revision: int) -> dict:
    return {
        "ok": True,
        "product": {
            "offer_id": OFFER_ID,
            "revision": revision,
            "seller_sku_candidate": "0952",
            "source_skus": _preview()["source"]["skus"],
            "actual_product_approved": False,
        },
        "content": {
            "approved": False,
            "images": [],
            "blockers": ["final content approval is required"],
        },
        "approval_rehearsal": {"ready": False, "blockers": []},
        "publication_rehearsal": {"ready": False, "drafts": []},
        "publication_scope": {"selected_labels": []},
        "pricing_review": {"status": "blocked", "target_pricing": {}},
        "omnichannel_preview": {"available": False, "targets": [], "blockers": []},
        "actual_release_gate": {"ready": False, "blockers": []},
    }


def _memory_workbench(monkeypatch, initial: dict) -> tuple[dict, list[dict]]:
    state = copy.deepcopy(initial)
    saves: list[dict] = []

    def load_state(_offer_id: str) -> dict:
        return copy.deepcopy(state)

    def save_state(_offer_id: str, next_state: dict) -> dict:
        expected = int(next_state.get("_revision") or 0)
        current = int(state.get("_revision") or 0)
        if expected != current:
            raise RuntimeError("state revision changed")
        saves.append(copy.deepcopy(next_state))
        state.clear()
        state.update(copy.deepcopy(next_state))
        state["offer_id"] = OFFER_ID
        state["_revision"] = current + 1
        return copy.deepcopy(state)

    monkeypatch.setattr(new_product_workbench, "load_state", load_state)
    monkeypatch.setattr(new_product_workbench, "save_state", save_state)
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: _dashboard(int(state.get("_revision") or 0)),
    )
    monkeypatch.setattr(product_server, "_product_workspace_view", lambda value: value)
    return state, saves


def test_collect_creates_the_first_local_revision_from_one_read_only_miaoshou_fetch(
    monkeypatch,
):
    state, saves = _memory_workbench(monkeypatch, {"_revision": 0})
    external_reads: list[str] = []

    def precollect(offer_id: str, **_kwargs) -> dict:
        external_reads.append(offer_id)
        return _preview()

    monkeypatch.setattr(new_product_workbench, "precollect_preview", precollect)

    status, payload = product_server._collect_product_workspace_locally(
        {"offer_id": OFFER_ID}
    )

    assert status == 201
    assert payload["ok"] is True
    assert payload["idempotent"] is False
    assert payload["source_read_performed"] is True
    assert payload["external_writes_performed"] == []
    assert external_reads == [OFFER_ID]
    assert len(saves) == 1
    assert state["_revision"] == 1
    assert state["offer_id"] == OFFER_ID
    assert state["review"]["title"] == "Watercolour butterfly wall decal"
    assert state["review"]["cost_cny"] == 8.1
    assert state["review"]["weight_kg"] == 0.14
    assert state["review"]["package_cm"] == [30.0, 3.0, 3.0]
    assert state["review"]["selected_sku_keys"] == ["30x90-2pcs"]
    assert "product_approval" not in state
    assert payload["dashboard"]["product"]["revision"] == 1


def test_collect_is_idempotent_and_does_not_repeat_the_external_read(monkeypatch):
    existing = {
        "offer_id": OFFER_ID,
        "_revision": 4,
        "review": copy.deepcopy(_preview()["review"]),
        "unrelated": {"keep": True},
    }
    state, saves = _memory_workbench(monkeypatch, existing)
    monkeypatch.setattr(
        new_product_workbench,
        "precollect_preview",
        lambda *_args, **_kwargs: pytest.fail(
            "an existing local workbench must not repeat the external Miaoshou read"
        ),
    )

    status, payload = product_server._collect_product_workspace_locally(
        {"offer_id": OFFER_ID}
    )

    assert status == 200
    assert payload["idempotent"] is True
    assert payload["source_read_performed"] is False
    assert payload["external_writes_performed"] == []
    assert saves == []
    assert state == existing
    assert payload["dashboard"]["product"]["revision"] == 4


def test_product_workbench_locks_are_keyed_per_offer():
    first = product_server._product_workbench_lock("3828540231")
    same_product = product_server._product_workbench_lock("3828540231")
    other_product = product_server._product_workbench_lock("3828811808")

    assert first is same_product
    assert first is not other_product


def test_facts_save_updates_one_revision_and_preserves_unrelated_state(monkeypatch):
    initial = {
        "offer_id": OFFER_ID,
        "_revision": 4,
        "review": copy.deepcopy(_preview()["review"]),
        "content_package": {"collect_box_id": OFFER_ID},
        "unrelated": {"keep": True},
    }
    state, saves = _memory_workbench(monkeypatch, initial)
    monkeypatch.setattr(
        new_product_workbench,
        "_source_summary",
        lambda *_args, **_kwargs: _preview()["source"],
    )

    status, payload = product_server._save_product_workspace_facts_locally(
        {
            "offer_id": OFFER_ID,
            "expected_revision": 4,
            "title": "Edited butterfly wall decal",
            "cost_cny": 9.25,
            "weight_kg": 0.18,
            "package_cm": [31.0, 4.0, 3.0],
            "selected_sku_keys": ["30x90-custom"],
            "sku_label_overrides": {
                "30x90-custom": "30 x 90 cm, Custom Material",
            },
        }
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["persisted"] is True
    assert payload["external_writes_performed"] == []
    assert len(saves) == 1
    assert state["_revision"] == 5
    assert state["review"]["title"] == "Edited butterfly wall decal"
    assert state["review"]["cost_cny"] == 9.25
    assert state["review"]["weight_kg"] == 0.18
    assert state["review"]["package_cm"] == [31.0, 4.0, 3.0]
    assert state["review"]["selected_sku_keys"] == ["30x90-custom"]
    assert state["review"]["sku_label_overrides"] == {
        "30x90-custom": "30 x 90 cm, Custom Material",
    }
    assert state["content_package"] == initial["content_package"]
    assert state["unrelated"] == initial["unrelated"]
    assert payload["dashboard"]["product"]["revision"] == 5


def test_facts_save_returns_pricing_recalculated_from_the_new_revision(monkeypatch):
    initial = {
        "offer_id": OFFER_ID,
        "_revision": 4,
        "review": copy.deepcopy(_preview()["review"]),
    }
    state, _saves = _memory_workbench(monkeypatch, initial)
    monkeypatch.setattr(
        new_product_workbench,
        "_source_summary",
        lambda *_args, **_kwargs: _preview()["source"],
    )

    def dashboard_from_current_state(**_kwargs) -> dict:
        review = state["review"]
        dashboard = _dashboard(int(state["_revision"]))
        dashboard["pricing_review"] = {
            "input_cost_cny": review["cost_cny"],
            "input_weight_kg": review["weight_kg"],
            "input_package_cm": list(review["package_cm"]),
            "target_pricing": {
                "tiktok:LH_TH": {
                    "list_price": (
                        review["cost_cny"] * 10
                        + review["weight_kg"] * 100
                        + sum(review["package_cm"])
                    )
                }
            },
        }
        return dashboard

    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        dashboard_from_current_state,
    )

    status, payload = product_server._save_product_workspace_facts_locally(
        {
            "offer_id": OFFER_ID,
            "expected_revision": 4,
            "title": "Edited butterfly wall decal",
            "cost_cny": 9.25,
            "weight_kg": 0.18,
            "package_cm": [31.0, 4.0, 3.0],
            "selected_sku_keys": ["30x90-2pcs"],
        }
    )

    assert status == 200
    assert payload["revision"] == 5
    pricing = payload["dashboard"]["pricing_review"]
    assert pricing["input_cost_cny"] == 9.25
    assert pricing["input_weight_kg"] == 0.18
    assert pricing["input_package_cm"] == [31.0, 4.0, 3.0]
    assert pricing["target_pricing"]["tiktok:LH_TH"]["list_price"] == 148.5
    assert payload["external_writes_performed"] == []


def test_title_draft_uses_model_once_and_only_persists_local_candidates(monkeypatch):
    initial = {
        "offer_id": OFFER_ID,
        "_revision": 4,
        "review": {
            **copy.deepcopy(_preview()["review"]),
            "sku_label_overrides": {
                "30x90-2pcs": "30 x 90 cm, 2 Pieces",
            },
        },
    }
    state, saves = _memory_workbench(monkeypatch, initial)
    monkeypatch.setattr(
        new_product_workbench,
        "_source_summary",
        lambda *_args, **_kwargs: {
            **_preview()["source"],
            "title_source": "水彩复古花卉蝴蝶墙贴",
            "attributes": {"材质": "PVC"},
        },
    )
    calls = []

    def generate(facts):
        calls.append(copy.deepcopy(facts))
        return {
            "schema_version": "listing-title-candidates-v2",
            "provider": "toapi",
            "status": "draft_pending_kyle_review",
            "semantic_master_en": "Watercolour Floral Butterfly PVC Wall Decal",
            "candidates": [
                {
                    "channel": "tiktok",
                    "site": "PH",
                    "language": "English",
                    "limit": 255,
                    "title": "Watercolour Floral Butterfly PVC Wall Decal",
                }
            ],
            "input_signature": "sha256:test",
            "policy_version": "listing-title-candidates-v2",
            "model": "test-model",
        }

    monkeypatch.setattr(content_operations, "generate_title_candidates", generate)

    status, payload = product_server._generate_product_workspace_title_draft(
        {"offer_id": OFFER_ID, "expected_revision": 4}
    )

    assert status == 200
    assert calls[0]["source_title_zh"] == "水彩复古花卉蝴蝶墙贴"
    assert calls[0]["selected_skus"][0]["key"] == "30x90-2pcs"
    assert calls[0]["selected_skus"][0]["label"] == "30 x 90 cm, 2 Pieces"
    assert len(saves) == 1
    assert state["listing_copy"]["semantic_master_en"].startswith("Watercolour")
    assert payload["language_model_request_performed"] is True
    assert payload["marketplace_writes_performed"] == []
    assert state["review"]["title"] == _preview()["review"]["title"]


def test_title_draft_model_failure_is_visible_and_does_not_write_state(monkeypatch):
    initial = {
        "offer_id": OFFER_ID,
        "_revision": 4,
        "review": copy.deepcopy(_preview()["review"]),
    }
    state, saves = _memory_workbench(monkeypatch, initial)
    monkeypatch.setattr(
        new_product_workbench,
        "_source_summary",
        lambda *_args, **_kwargs: {
            **_preview()["source"],
            "title_source": "水彩复古花卉蝴蝶墙贴",
        },
    )
    monkeypatch.setattr(
        content_operations,
        "generate_title_candidates",
        lambda _facts: (_ for _ in ()).throw(RuntimeError("model auth failed")),
    )

    status, payload = product_server._generate_product_workspace_title_draft(
        {"offer_id": OFFER_ID, "expected_revision": 4}
    )

    assert status == 502
    assert payload["model_request_failed"] is True
    assert payload["marketplace_writes_performed"] == []
    assert saves == []
    assert "listing_copy" not in state


@pytest.mark.parametrize(
    "locked_state",
    [
        {
            "review": {"fields_locked": True},
        },
        {
            "review": {"fields_locked": False},
            "product_approval": {"status": "approved"},
        },
    ],
)
def test_facts_save_refuses_silent_mutation_after_approval(
    monkeypatch,
    locked_state,
):
    initial = {
        "offer_id": OFFER_ID,
        "_revision": 4,
        "review": {
            **copy.deepcopy(_preview()["review"]),
            **locked_state["review"],
        },
        **{
            key: copy.deepcopy(value)
            for key, value in locked_state.items()
            if key != "review"
        },
    }
    _state, saves = _memory_workbench(monkeypatch, initial)

    status, payload = product_server._save_product_workspace_facts_locally(
        {
            "offer_id": OFFER_ID,
            "expected_revision": 4,
            "title": "This must not be silently accepted",
            "cost_cny": 9.25,
            "weight_kg": 0.18,
            "package_cm": [31.0, 4.0, 3.0],
            "selected_sku_keys": ["30x90-custom"],
        }
    )

    assert status == 409
    assert "locked" in payload["error"].lower() or "approved" in payload["error"].lower()
    assert saves == []


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_dashboard_opens_before_any_ai_suite_exists(tmp_path):
    state = {
        "offer_id": OFFER_ID,
        "_revision": 1,
        "updated_at": "2026-07-26T13:00:00+08:00",
        "source": copy.deepcopy(_preview()["source"]),
        "review": copy.deepcopy(_preview()["review"]),
    }
    _write_json(
        tmp_path / "data" / "new_product_workbench" / f"{OFFER_ID}.json",
        state,
    )
    database = tmp_path / "data" / "shop.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE products (seller_sku TEXT)")
        connection.execute("CREATE TABLE shopee_products (seller_sku TEXT)")
        connection.execute("INSERT INTO products VALUES ('0951')")

    result = release_control.build_release_dashboard(
        root=tmp_path,
        database_path=database,
        report_store_path=tmp_path / "data" / "missing-orbit.db",
        offer_id=OFFER_ID,
    )

    assert result["ok"] is True
    assert result["product"]["offer_id"] == OFFER_ID
    assert result["product"]["revision"] == 1
    assert result["product"]["seller_sku_candidate"] == "0952"
    assert result["product"]["source_skus"] == [
        {
            "key": "30x90-2pcs",
                "label": "30*90cm*2pcs; translucent",
                "name": "30*90cm*2pcs; translucent",
                "source_label": "30*90cm*2pcs; translucent",
                "label_overridden": False,
                "price_cny": 8.1,
        },
        {
            "key": "30x90-custom",
                "label": "30*90cm*2pcs; custom material",
                "name": "30*90cm*2pcs; custom material",
                "source_label": "30*90cm*2pcs; custom material",
                "label_overridden": False,
                "price_cny": 8.2,
        },
    ]
    assert isinstance(result["content"]["images"], list)
    assert result["safety"]["external_writes_performed"] == []


@pytest.fixture
def product_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_formal_http_routes_collect_then_save_facts(
    product_http_server,
    monkeypatch,
):
    calls: list[tuple[str, dict]] = []

    def collect(data: dict) -> tuple[int, dict]:
        calls.append(("collect", data))
        return 200, {"ok": True, "dashboard": _dashboard(1)}

    def save(data: dict) -> tuple[int, dict]:
        calls.append(("facts", data))
        return 200, {"ok": True, "dashboard": _dashboard(2)}

    monkeypatch.setattr(product_server, "_collect_product_workspace_locally", collect)
    monkeypatch.setattr(
        product_server,
        "_save_product_workspace_facts_locally",
        save,
    )

    status, payload = _post(
        product_http_server + "/api/product-workspace/collect",
        {"offer_id": OFFER_ID},
    )
    assert status == 200
    assert payload["dashboard"]["product"]["revision"] == 1

    facts = {
        "offer_id": OFFER_ID,
        "expected_revision": 1,
        "title": "Edited title",
        "cost_cny": 9.25,
        "weight_kg": 0.18,
        "package_cm": [31.0, 4.0, 3.0],
        "selected_sku_keys": ["30x90-custom"],
    }
    status, payload = _post(
        product_http_server + "/api/product-workspace/facts",
        facts,
    )
    assert status == 200
    assert payload["dashboard"]["product"]["revision"] == 2
    assert calls == [
        ("collect", {"offer_id": OFFER_ID}),
        ("facts", facts),
    ]


def test_facts_http_route_is_same_origin_json_protected(
    product_http_server,
    monkeypatch,
):
    monkeypatch.setattr(
        product_server,
        "_save_product_workspace_facts_locally",
        lambda _data: pytest.fail("cross-origin request must not reach the writer"),
    )

    status, payload = _post(
        product_http_server + "/api/product-workspace/facts",
        {
            "offer_id": OFFER_ID,
            "expected_revision": 1,
            "title": "Rejected",
            "cost_cny": 9.25,
            "weight_kg": 0.18,
            "package_cm": [31.0, 4.0, 3.0],
            "selected_sku_keys": ["30x90-custom"],
        },
        # Keep the negative origin local so Windows endpoint protection does
        # not abort the loopback socket before the handler can return its 403.
        headers={"Origin": "http://127.0.0.1:1"},
    )

    assert status == 403
    assert payload["error"] == "cross-origin product workflow write rejected"


def test_formal_frontend_collects_first_and_has_an_inline_facts_editor():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(encoding="utf-8")

    assert 'id="productFactsForm"' in html
    assert 'id="productFactsPanel"' in html
    assert "商品事实 · 直接编辑" in html
    assert "核对并编辑商品" not in html
    assert 'name="title"' in html
    assert 'name="cost_cny"' in html
    assert 'name="weight_kg"' in html
    assert 'name="package_length_cm"' in html
    assert 'name="package_width_cm"' in html
    assert 'name="package_height_cm"' in html
    assert 'id="productSpecGrid"' in html
    assert "sku-label-input" in script
    assert "sku_label_overrides" in script
    assert 'id="factsEditCostSource"' in html
    assert 'id="factsEditWeightSource"' in html
    assert 'id="factsEditPackageSource"' in html
    assert "保存并确认商品事实 · 刷新全部售价" in html
    assert "/api/product-workspace/collect" in script
    assert "/api/product-workspace/facts" in script
    assert "/api/product-workspace/title-adopt" in script
    assert "采用并废止旧审批 / 发布计划" in script
    assert "旧商品审批、旧发布计划及未完成运行已废止" in script
    assert "expected_revision" in script
    assert "selected_sku_keys" in script
    assert "请先进入 AI 图片工作室" not in script
    assert "AI 图片工作室" in html
    assert 'target="_blank"' in html
    save_start = script.index("async function submitFactsEdit()")
    save_end = script.index("function approvalEligible", save_start)
    save_flow = script[save_start:save_end]
    assert "render(data);" in save_flow
    assert "全部国家与店铺售价、费用审计和渠道预检已按新值刷新" in save_flow
    render_start = script.index("function render(data)")
    render_end = script.index("function clearCurrentApprovalContext", render_start)
    assert "renderPricingReview(" in script[render_start:render_end]
