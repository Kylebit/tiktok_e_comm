"""Offline real Store/HTTP/runner/TikTok adapter contracts; synthetic I/O only."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import ThreadingHTTPServer
import threading
import json
import subprocess
import sys
import sqlite3

import pytest

from domains.product_operations import ModelSkuAssignment, SkuAssignment, finalize_new_source_sku_reservation, resolve_sku_lineage_reservation, resolve_source_product_identity, build_approved_publication_snapshot_inputs
from domains.channel_operations.tiktok_v4_execution import project_tiktok_v4_execution_plan
from domains.channel_operations.tiktok_publisher import TikTokPublisher
from modules.products import server as product_server
from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport, PUBLISH_PATH, READ_SITE_DRAFT_PATH, READ_SHOP_DRAFT_PATH
from shared_platform import release_store as release_store_module
from shared_platform.release_store import ReleaseStore
from shared_platform.collectbox_action import CollectBoxActionStore, approved_plan_identity
from shared_platform.approved_publication_snapshot_projection import project_release_plan_for_publication_snapshot
from shared_platform.product_publication_executors import build_tiktok_v4_executor
from shared_platform.product_publication_live_dependencies import OfficialMiaoshouTikTokCategoryResolver, TikTokUnavailableStorefrontReadback, CATEGORY_TREE_PATH
from shared_platform.product_publication_reports import ProductPublicationReportStore
from shared_platform.product_publication_runs import ProductPublicationRunStore
from test_approved_publication_snapshot_inputs import _raw_approval_inputs
from test_product_publication_live_dependencies import _category_post
from test_tiktok_collectbox_publish_bridge import _approved_tiktok_context, _persist_collectbox_result, _post_json, TIKTOK_TARGETS
from test_tiktok_independent_publisher import FakeLowestTransport, PRICE_BY_TARGET


def _approved_v4(tmp_path, *, store=None, targets=TIKTOK_TARGETS, revision=42):
    dashboard, payload = _raw_approval_inputs(sku_count=1)
    payload["product_revision"] = revision
    payload["plan_id"] = "release-plan:" + payload["product_id"] + ":r" + str(revision)
    dashboard["product"]["revision"] = revision
    payload["targets"] = list(targets)
    dashboard["publication_scope"]["selected_labels"] = list(targets)
    payload["pricing"]["selected_targets"] = {
        label: {"sku_prices": [{"model_sku": "0958", "list_price": PRICE_BY_TARGET[label][0], "currency": PRICE_BY_TARGET[label][1]}]}
        for label in targets
    }
    source = resolve_source_product_identity(collect_box={"source_item_id": payload["product_id"]}, source_authority="1688").identity
    assignment = SkuAssignment(seller_sku=payload["seller_sku"], model_skus=tuple(ModelSkuAssignment(**row) for row in payload["sku_lineage"]["assignment"]["model_skus"]))
    lineage = resolve_sku_lineage_reservation(source_identity=source, predecessor_records=[])
    reservation = finalize_new_source_sku_reservation(source_identity=source, assignment=assignment)
    payload["source_product_identity"] = source.payload()
    payload["sku_lineage"] = {**lineage.payload(), "assignment": assignment.payload(), "reservation": reservation.reservation.payload()}
    inputs = build_approved_publication_snapshot_inputs(dashboard=dashboard, release_plan_payload=payload)
    projected = project_release_plan_for_publication_snapshot(payload, approved_inputs=inputs)
    assert projected.ready, projected.missing_fields
    store = store or ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(projected.payload)
    # Synthetic approval in this fixture's temporary DB, using the production contract.
    store.approve_plan(plan["plan_id"], approved_by="Kyle", user_approved=True, confirmation_token=plan["confirmation_token"])
    plan = store.get_plan(plan["plan_id"])
    return store, plan, store.approved_publication_snapshot(offer_id=plan["product_id"], plan_id=plan["plan_id"])


@contextmanager
def _http():
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _runtime(tmp_path, monkeypatch, *, omit=(), category_fail=(), unknown=(), readback=None):
    from shared_platform import operations_domain_guard

    store, plan, snapshot = _approved_v4(tmp_path)
    ledger = CollectBoxActionStore(store.path)
    _persist_collectbox_result(ledger, plan, omit_detail_targets=omit)
    post, category_calls = _category_post()
    def category_post(path, body):
        if path == CATEGORY_TREE_PATH and ("*" in category_fail or body["site"] in category_fail):
            raise TimeoutError("synthetic category unavailable")
        return post(path, body)
    resolver = OfficialMiaoshouTikTokCategoryResolver(post=category_post)
    contexts = ledger.internal_tiktok_publish_contexts(plan_id=plan["plan_id"])
    execution = project_tiktok_v4_execution_plan(snapshot, collectbox_contexts=contexts, category_resolver=OfficialMiaoshouTikTokCategoryResolver(post=post))
    rows = [row["publisher_snapshot"]["targets"][0] for row in execution["targets"]]
    fake = FakeLowestTransport({"targets": rows})
    def lowest_post(path, body):
        result = fake(path, body)
        if path in {READ_SITE_DRAFT_PATH, READ_SHOP_DRAFT_PATH}:
            row = fake.rows_by_detail[str(body["detailId"])]
            info = result["data"].get("siteCollectItemInfo") or result["data"]["shopCollectItemInfo"]
            specs = row["expected_variant_specifications"]
            info["skuPropertyList"] = [{"attrName": "Specification", "attrValueList": [{"attrValueId": key, "attrValue": spec["option"]} for key, spec in specs.items()]}]
            for key, spec in specs.items():
                info["skuMap"][key]["specification"] = deepcopy(spec)
        if path == PUBLISH_PATH and any(row["target_label"] in unknown and int(row["detail_id"]) in body["detailIds"] for row in rows):
            raise TimeoutError("synthetic response lost after submission")
        return result
    executor = build_tiktok_v4_executor(collectbox_context_resolver=lambda request: CollectBoxActionStore(store.path).internal_tiktok_publish_contexts(plan_id=request.snapshot["plan_id"]), category_resolver=resolver, publisher=TikTokPublisher(MiaoshouTikTokTransport(post=lowest_post)), storefront_readback=readback or TikTokUnavailableStorefrontReadback())
    reports = ProductPublicationReportStore(tmp_path / "reports.db", reports_root=tmp_path / "report-files")
    runs = ProductPublicationRunStore(tmp_path / "runs.db")
    monkeypatch.setattr(release_store_module, "default_release_store", lambda: store)
    monkeypatch.setattr(product_server, "_product_publication_report_store", lambda: reports)
    monkeypatch.setattr(product_server, "_product_publication_run_store", lambda: runs)
    monkeypatch.setattr(product_server, "_product_publication_platform_executors", lambda: {"TIKTOK": executor})
    monkeypatch.setattr(
        operations_domain_guard, "begin_snapshot_publication", lambda *args, **kwargs: None
    )
    # Scheduling is synchronous; approval, runner, platform adapter and storage are real.
    monkeypatch.setattr(product_server, "_launch_product_publication_background", lambda target: target())
    return store, plan, snapshot, fake, reports, runs


def test_v4_six_stores_actual_http_runner_and_lowest_transport(tmp_path, monkeypatch):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch)
    with _http() as url:
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]})
    assert status == 202, body
    report = reports.get_report_by_run(run_id=body["run_id"])
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 6, str(report)
    assert report["status"] == "PROCESSING", report


@pytest.mark.parametrize("unknown", [(), ("tiktok:LH_PH",), TIKTOK_TARGETS])
def test_same_approved_v4_request_never_redispatches_including_unknown(tmp_path, monkeypatch, record_property, unknown):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, unknown=unknown)
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]}
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        second = _post_json(url + "/api/product-workspace/publish-tiktok", request)
    count = len([call for call in fake.calls if call[0] == PUBLISH_PATH])
    record_property("synthetic_publish_request_count", count)
    record_property("same_run_id", first[1].get("run_id") == second[1].get("run_id"))
    assert first[0] == second[0] == 202
    assert count == 6
    assert first[1]["run_id"] == second[1]["run_id"]


def _legacy_request(plan):
    identity = approved_plan_identity(plan)
    return {**{key: identity[key] for key in ("offer_id", "plan_id", "product_revision", "payload_digest", "targets_digest")}, "confirmation_token": plan["confirmation_token"], "publication_targets": list(plan["targets"]), "confirm_publish": True}


@pytest.mark.parametrize("unknown", [False, True])
def test_retired_legacy_route_never_dispatches_or_retries(tmp_path, monkeypatch, record_property, unknown):
    store, plan = _approved_tiktok_context(tmp_path)
    _persist_collectbox_result(CollectBoxActionStore(store.path), plan)
    monkeypatch.setattr(release_store_module, "default_release_store", lambda: store)
    request = _legacy_request(plan)
    snapshot = product_server._build_approved_tiktok_publish_snapshot(request)
    fake = FakeLowestTransport(snapshot)
    def post(path, body):
        result = fake(path, body)
        if unknown and path == PUBLISH_PATH:
            raise TimeoutError("synthetic legacy response lost")
        return result
    monkeypatch.setattr(product_server, "_tiktok_publisher", lambda: TikTokPublisher(MiaoshouTikTokTransport(post=post)))
    with _http() as url:
        responses = [_post_json(url + "/api/product-workspace/publish", request) for _ in range(2)]
    count = len([call for call in fake.calls if call[0] == PUBLISH_PATH])
    record_property("synthetic_publish_request_count", count)
    record_property("http_statuses", str([status for status, _ in responses]))
    assert count == 0
    assert [status for status, _ in responses] == [410, 410]
    assert all(body["migration"]["required_snapshot_schema"] == "approved-publication-snapshot/v4" for _, body in responses)


def test_legacy_only_plan_on_v4_route_is_explicit_zero_write_migration(tmp_path, monkeypatch):
    store, plan = _approved_tiktok_context(tmp_path)
    monkeypatch.setattr(release_store_module, "default_release_store", lambda: store)
    monkeypatch.setattr(product_server, "_product_publication_report_store", lambda: ProductPublicationReportStore(tmp_path / "reports.db", reports_root=tmp_path / "report-files"))
    monkeypatch.setattr(product_server, "_product_publication_run_store", lambda: ProductPublicationRunStore(tmp_path / "runs.db"))
    monkeypatch.setattr(product_server, "_product_publication_platform_executors", lambda: {"TIKTOK": lambda _request: pytest.fail("legacy plan must not execute")})
    with _http() as url:
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", _legacy_request(plan))
    assert status == 409
    assert body["code"] == "approved_publication_snapshot_required"
    assert body["external_write_count"] == 0


@pytest.mark.parametrize("options,expected", [({"omit": ("tiktok:GB",)}, 5), ({"omit": TIKTOK_TARGETS}, 0), ({"category_fail": ("PH",)}, 5)])
def test_v4_missing_identity_or_category_preserves_other_stores(tmp_path, monkeypatch, options, expected):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, **options)
    with _http() as url:
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]})
    assert status == 202
    report = reports.get_report_by_run(run_id=body["run_id"])
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == expected
    assert len(report["targets"]) == 6
    assert sum(row["status"] == "FAILED" for row in report["targets"]) == 6 - expected
    assert report["status"] == ("PARTIAL" if expected else "FAILED")


def test_concurrent_http_requests_claim_one_run(tmp_path, monkeypatch):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, unknown=("tiktok:LH_PH",))
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]}
    with _http() as url, ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _post_json(url + "/api/product-workspace/publish-tiktok", request), range(4)))
    assert all(status == 202 for status, body in results), results
    assert len({body["run_id"] for _, body in results}) == 1
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 6


@pytest.mark.parametrize("state", ["QUEUED", "RUNNING"])
def test_registered_or_mid_submission_crash_never_relaunches(tmp_path, monkeypatch, state):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch)
    launches = []
    monkeypatch.setattr(product_server, "_launch_product_publication_background", launches.append)
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]}
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        if state == "RUNNING":
            runs.mark_running(run_id=first[1]["run_id"])
        second = _post_json(url + "/api/product-workspace/publish-tiktok", request)
    assert first[0] == second[0] == 202
    assert first[1]["run_id"] == second[1]["run_id"]
    assert len(launches) == 1
    assert fake.calls == []


@pytest.mark.parametrize("field,value", [("offer_id", "9999999999"), ("plan_id", "wrong-plan"), ("snapshot_digest", "sha256:" + "0" * 64), ("publication_targets", ["tiktok:LH_PH"]), ("tiktok_target_scope", ["tiktok:LH_PH"])])
def test_wrong_identity_or_unapproved_scope_is_zero_write(tmp_path, monkeypatch, field, value):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch)
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"], field: value}
    with _http() as url:
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", request)
    assert status == 409, body
    assert body["external_write_count"] == 0
    assert fake.calls == []


def test_target_order_cannot_create_new_request(tmp_path, monkeypatch):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, unknown=TIKTOK_TARGETS)
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"], "publication_targets": list(TIKTOK_TARGETS)}
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        request["publication_targets"].reverse()
        second = _post_json(url + "/api/product-workspace/publish-tiktok", request)
    assert first[0] == second[0] == 202
    assert first[1]["run_id"] == second[1]["run_id"]
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 6


def test_explicit_zero_write_retry_is_new_run_and_idempotent(tmp_path, monkeypatch):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, omit=TIKTOK_TARGETS)
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]}
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        retry = {
            **request,
            "retry_of_run_id": first[1]["run_id"],
            "target_scope": [TIKTOK_TARGETS[0]],
        }
        second = _post_json(url + "/api/product-workspace/publish-tiktok", retry)
        third = _post_json(url + "/api/product-workspace/publish-tiktok", retry)
    assert first[0] == second[0] == third[0] == 202
    assert first[1]["run_id"] != second[1]["run_id"] == third[1]["run_id"]
    report = reports.get_report_by_run(run_id=second[1]["run_id"])
    assert [row["target_label"] for row in report["targets"]] == [TIKTOK_TARGETS[0]]
    assert runs.get_run_by_id(run_id=second[1]["run_id"])["target_count"] == 1
    assert fake.calls == []


def test_unknown_result_cannot_use_zero_write_retry(tmp_path, monkeypatch):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, unknown=TIKTOK_TARGETS)
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]}
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", {
            **request,
            "retry_of_run_id": first[1]["run_id"],
            "target_scope": [TIKTOK_TARGETS[0]],
        })
    assert status == 409
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 6


def test_new_subset_approval_cannot_reopen_unresolved_target(tmp_path, monkeypatch):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, unknown=TIKTOK_TARGETS)
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]})
        _, smaller, _ = _approved_v4(tmp_path, store=store, targets=("tiktok:LH_PH",), revision=43)
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": smaller["product_id"], "plan_id": smaller["plan_id"]})
    assert first[0] == 202
    assert status == 409, body
    assert "reconciliation" in body["error"]
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 6


def test_durable_random_id_from_previous_server_is_also_reused(tmp_path, monkeypatch):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch)
    prior = runs.create_run(run_id="historical-random-id", offer_id=plan["product_id"], plan_id=plan["plan_id"], revision=snapshot["product_revision"], snapshot_digest=snapshot["snapshot_digest"], platform_scope=("TIKTOK",), target_count=6)
    with _http() as url:
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]})
    assert status == 202
    assert body["run_id"] == prior.run_id
    assert fake.calls == []


def test_two_independent_processes_atomically_claim_one_approved_request(tmp_path):
    path = tmp_path / "claims.db"
    code = '''import json, sys
from shared_platform.product_publication_runs import ProductPublicationRunStore
value = ProductPublicationRunStore(sys.argv[1]).create_run(
 run_id="same-approved-request", offer_id="3838616043", revision=42,
 plan_id="plan:r42", snapshot_digest="a"*64, platform_scope=("TIKTOK",),
 target_count=6, approved_request_guard=lambda prior: None)
print(json.dumps({"created":value.created,"run_id":value.run_id}))
'''
    processes = [subprocess.Popen([sys.executable, "-B", "-c", code, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        results.append(json.loads(stdout))
    assert sum(row["created"] for row in results) == 1
    assert {row["run_id"] for row in results} == {"same-approved-request"}


def test_explicit_retry_after_category_recovers_can_dispatch_once(tmp_path, monkeypatch):
    unavailable = ["*"]
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, category_fail=unavailable)
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]}
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        assert fake.calls == []
        assert reports.get_report_by_run(run_id=first[1]["run_id"])["summary"]["evidence"]["external_write_count"] == 0
        unavailable.clear()
        request["retry_of_run_id"] = first[1]["run_id"]
        request["target_scope"] = [TIKTOK_TARGETS[0]]
        second = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        third = _post_json(url + "/api/product-workspace/publish-tiktok", request)
    assert second[0] == third[0] == 202
    assert second[1]["run_id"] == third[1]["run_id"] != first[1]["run_id"]
    publishes = [call for call in fake.calls if call[0] == PUBLISH_PATH]
    report = reports.get_report_by_run(run_id=second[1]["run_id"])
    assert len(publishes) == 1, json.dumps(report, ensure_ascii=False)
    assert [row["target_label"] for row in report["targets"]] == [TIKTOK_TARGETS[0]]


def test_retry_scope_contract_rejects_missing_or_unbound_scope_without_provider_access(
    tmp_path, monkeypatch
):
    store, plan, snapshot, fake, reports, runs = _runtime(
        tmp_path, monkeypatch, omit=TIKTOK_TARGETS
    )
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]}
    with _http() as url:
        status, body = _post_json(
            url + "/api/product-workspace/publish-tiktok",
            {**request, "target_scope": [TIKTOK_TARGETS[0]]},
        )
        assert status == 409 and body["external_write_count"] == 0
        first = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        before = list(fake.calls)
        status, body = _post_json(
            url + "/api/product-workspace/publish-tiktok",
            {**request, "retry_of_run_id": first[1]["run_id"]},
        )
    assert status == 409 and body["external_write_count"] == 0
    assert fake.calls == before == []


def test_retry_scope_cannot_expand_beyond_immediately_prior_failed_run(
    tmp_path, monkeypatch
):
    store, plan, snapshot, fake, reports, runs = _runtime(
        tmp_path, monkeypatch, omit=TIKTOK_TARGETS
    )
    request = {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]}
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", request)
        second = _post_json(
            url + "/api/product-workspace/publish-tiktok",
            {
                **request,
                "retry_of_run_id": first[1]["run_id"],
                "target_scope": [TIKTOK_TARGETS[0]],
            },
        )
        status, body = _post_json(
            url + "/api/product-workspace/publish-tiktok",
            {
                **request,
                "retry_of_run_id": second[1]["run_id"],
                "target_scope": [TIKTOK_TARGETS[1]],
            },
        )
    assert first[0] == second[0] == 202
    assert status == 409 and body["external_write_count"] == 0
    assert "prior failed run" in body["error"]
    assert fake.calls == []


class SyntheticStorefrontReadback:
    """Provider readback boundary fixture; these are not live official facts."""
    def __init__(self, *, exact=True, wrong_target=False):
        self.exact, self.wrong_target = exact, wrong_target
        self.calls = []

    def readback(self, *, command, dispatch):
        self.calls.append(command["target_label"])
        return {"target_label": "tiktok:wrong" if self.wrong_target else command["target_label"], "authority": "OFFICIAL_STOREFRONT", "status": "VERIFIED", "exact": self.exact}


class SyntheticCatalogStorefrontReadback(SyntheticStorefrontReadback):
    """Offline official-readback shape: no provider client or external I/O."""

    def __init__(self, *, corruption=None):
        super().__init__()
        self.corruption = corruption

    def readback(self, *, command, dispatch):
        value = super().readback(command=command, dispatch=dispatch)
        sku = command["skus"][0]
        value["official_catalog_rows"] = [{
            "authority": "OFFICIAL",
            "verified": True,
            "target_label": command["target_label"],
            "model_sku": sku["model_sku"],
            "variant_key": sku["variant_key"],
            "identity": {
                "platform": "tiktok",
                "shop_key": command["control"]["shop_id"],
                "product_id": "8201",
                "variant_id": "7201",
                "seller_sku": sku["model_sku"],
            },
            "listing": {
                "name": command["product"]["title"],
                "variant_name": sku["specification"]["option"],
                "price": sku["price"],
                "currency": sku["currency"],
                "status": "ACTIVATE",
            },
        }]
        row = value["official_catalog_rows"][0]
        if self.corruption == "wrong_target":
            row["target_label"] = "tiktok:wrong"
        elif self.corruption == "wrong_shop":
            row["identity"]["shop_key"] = "wrong-shop"
        elif self.corruption == "wrong_variant":
            row["variant_key"] = "previous-variant"
        return value


@pytest.mark.parametrize("exact,wrong_target,expected", [(True, False, "PUBLISHED"), (False, False, "PROCESSING"), (True, True, "PROCESSING")])
def test_published_requires_exact_target_official_readback_boundary(tmp_path, monkeypatch, exact, wrong_target, expected):
    readback = SyntheticStorefrontReadback(exact=exact, wrong_target=wrong_target)
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, readback=readback)
    with _http() as url:
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]})
    assert status == 202
    assert reports.get_report_by_run(run_id=body["run_id"])["status"] == expected
    assert set(readback.calls) == set(TIKTOK_TARGETS)


def test_verified_tiktok_readback_projects_exact_sku_and_preserves_manual_cost(tmp_path, monkeypatch):
    """The publication adapter supplies facts; the catalog sink owns the write."""
    from core import config, db
    from shared_platform.catalog_cost_projection import read_cost, save_manual
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    from shared_platform.product_publication_runner import PublicationPlatformRequest

    catalog = tmp_path / "catalog.db"
    monkeypatch.setattr(config, "_cache", {"database": str(catalog)})
    db.init_db()
    sync = CatalogPublicationSync(catalog, tmp_path / "catalog-outbox")
    monkeypatch.setattr(product_server, "_catalog_publication_sync", lambda: sync)
    readback = SyntheticCatalogStorefrontReadback()
    store, plan, snapshot, fake, reports, runs = _runtime(
        tmp_path, monkeypatch, readback=readback
    )
    with _http() as url:
        status, body = _post_json(
            url + "/api/product-workspace/publish-tiktok",
            {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]},
        )
    assert status == 202, body
    assert reports.get_report_by_run(run_id=body["run_id"])["status"] == "PUBLISHED"
    identity = {
        "platform": "tiktok", "shop_key": "7676267", "product_id": "8201",
        "variant_id": "7201", "seller_sku": "0958",
    }
    with sqlite3.connect(catalog) as connection:
        connection.row_factory = sqlite3.Row
        stored = read_cost(connection, identity)
        assert stored is not None, sync.status()
        assert stored["amount"] == "8"
    save_manual(catalog, identity, "19", 1)
    first_receipt = next(
        row["receipt_id"] for row in sync.status()
        if row["result"] and any(
            item.get("identity") == identity for item in row["result"].get("rows", [])
        )
    )
    observations = json.loads(sync._path(first_receipt).read_text(encoding="utf-8"))["packet"]["rows"]
    # A later independently read official row never replaces the current CAS
    # cost; it becomes a local review state while the listing ID remains bound.
    result = sync.capture(
        PublicationPlatformRequest(
            "catalog-cost-preservation", "synthetic-report", "TIKTOK",
            tuple(TIKTOK_TARGETS), snapshot, catalog_sink=sync,
        ),
        deepcopy(observations),
    )
    assert result["state"] == "NEEDS_REVIEW", result
    assert any(
        row.get("status") == "COST_CONFLICT_PRESERVED" and row.get("identity") == identity
        for row in result["result"]["rows"]
    )
    with sqlite3.connect(catalog) as connection:
        connection.row_factory = sqlite3.Row
        assert read_cost(connection, identity)["amount"] == "19"


@pytest.mark.parametrize("corruption", ["wrong_target", "wrong_shop", "wrong_variant"])
def test_invalid_tiktok_catalog_row_never_reaches_catalog_projection(
    tmp_path, monkeypatch, corruption
):
    """Only the pre-dispatch intent exists; invalid rows create no observation."""
    from core import config, db
    from shared_platform.catalog_publication_sync import CatalogPublicationSync

    catalog = tmp_path / "catalog.db"
    monkeypatch.setattr(config, "_cache", {"database": str(catalog)})
    db.init_db()
    sync = CatalogPublicationSync(catalog, tmp_path / "catalog-outbox")
    monkeypatch.setattr(product_server, "_catalog_publication_sync", lambda: sync)
    store, plan, snapshot, fake, reports, runs = _runtime(
        tmp_path, monkeypatch,
        readback=SyntheticCatalogStorefrontReadback(corruption=corruption),
    )
    with _http() as url:
        status, body = _post_json(
            url + "/api/product-workspace/publish-tiktok",
            {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]},
        )
    assert status == 202, body
    assert reports.get_report_by_run(run_id=body["run_id"])["status"] == "PROCESSING"
    states = sync.status()
    assert len(states) == 1
    assert states[0]["state"] == "PENDING_OFFICIAL_EVIDENCE"
    assert states[0]["observation_receipt_id"] is None
    with sqlite3.connect(catalog) as connection:
        assert connection.execute("SELECT count(*) FROM products").fetchone()[0] == 0


def test_new_explicit_subset_approval_allowed_after_verified_previous_run(tmp_path, monkeypatch):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch, readback=SyntheticStorefrontReadback())
    with _http() as url:
        first = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]})
        assert reports.get_report_by_run(run_id=first[1]["run_id"])["status"] == "PUBLISHED"
        _, smaller, _ = _approved_v4(tmp_path, store=store, targets=("tiktok:LH_PH",), revision=43)
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": smaller["product_id"], "plan_id": smaller["plan_id"]})
    assert status == 202, body
    assert body["run_id"] != first[1]["run_id"]
    # This new fixture has no matching draft identity: it is accepted as a new
    # authorization, then truthfully fails before write; no old proof is reused.
    assert reports.get_report_by_run(run_id=body["run_id"])["status"] == "FAILED"
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 6


@pytest.mark.parametrize("corruption", ["schema", "digest", "target"])
def test_corrupted_durable_snapshot_is_rejected_before_provider_access(tmp_path, monkeypatch, corruption):
    store, plan, snapshot, fake, reports, runs = _runtime(tmp_path, monkeypatch)
    damaged = deepcopy(snapshot)
    if corruption == "schema":
        damaged["schema_version"] = "approved-publication-snapshot/v1"
    elif corruption == "digest":
        damaged["snapshot_digest"] = "sha256:" + "0" * 64
    else:
        damaged["publication_targets"][0]["target_label"] = "tiktok:wrong"
    # Fault injection affects this synthetic temporary database only.
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER trg_approved_publication_snapshot_immutable")
        connection.execute("UPDATE approved_publication_snapshots SET snapshot_json = ?", (json.dumps(damaged),))
    with _http() as url:
        status, body = _post_json(url + "/api/product-workspace/publish-tiktok", {"offer_id": plan["product_id"], "plan_id": plan["plan_id"]})
    assert status == 409, body
    assert fake.calls == []
