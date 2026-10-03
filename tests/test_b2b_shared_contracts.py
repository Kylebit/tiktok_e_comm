"""Offline B2B regressions across real Store, projection and dashboard seams."""
from copy import deepcopy
import json
import sqlite3

import pytest

from domains.product_operations import (
    ModelSkuAssignment, SkuAssignment, finalize_new_source_sku_reservation,
    resolve_sku_lineage_reservation, resolve_source_product_identity,
    build_approved_publication_snapshot_inputs,
)
from modules.products import server as product_server
from shared_platform import release_control
from shared_platform.approved_publication_snapshot_projection import project_release_plan_for_publication_snapshot
from shared_platform.collectbox_action import CollectBoxTargetDetailIdentity, approved_plan_identity
from shared_platform.product_snapshot import build_approved_tiktok_publish_snapshot, _digest
from shared_platform.release_store import ImmutableReleaseError, ReleaseStore, SkuReservationConflict
from test_approved_publication_snapshot import _warehouse_allocations
from test_approved_publication_snapshot_inputs import _raw_approval_inputs
from test_approved_publication_snapshot_integration import _production_dashboard_with_exact_v4_inputs
from test_release_control import _release_fixture
from test_shopee_global_master_snapshot import _external_variant_bindings
from test_tiktok_collectbox_publish_bridge import _approved_tiktok_context, EXPECTED_SHOP_IDS


def _identity(offer):
    result = resolve_source_product_identity(collect_box={"source_item_id": offer}, source_authority="1688")
    assert result.ready
    return result.identity


def _source_plan(source, sku, *, suffix="a", model=None):
    assignment = SkuAssignment(seller_sku=sku, model_skus=(ModelSkuAssignment(variant_key="size-large", model_sku=model or sku),))
    lineage = resolve_sku_lineage_reservation(source_identity=source, predecessor_records=[])
    reserved = finalize_new_source_sku_reservation(source_identity=source, assignment=assignment)
    return {
        "plan_id": "fixture:" + suffix, "product_id": "fixture-product:" + suffix,
        "seller_sku": sku, "product_package_id": "product:" + suffix,
        "content_package_id": "content:" + suffix, "targets": ["tiktok:LH_PH"],
        "product_revision": 1, "source_product_identity": source.payload(),
        "sku_lineage": {**lineage.payload(), "assignment": assignment.payload(), "reservation": reserved.reservation.payload()},
    }


def _context(store, source):
    return store.source_sku_lineage_context(source_offer_id=source.source_offer_id, source_authority=source.source_authority, source_identity_digest=source.identity_digest)


def _approve(store, payload):
    plan = store.create_plan(payload)
    store.approve_plan(plan["plan_id"], approved_by="Kyle", user_approved=True, confirmation_token=plan["confirmation_token"])
    return store.get_plan(plan["plan_id"])


def test_governed_namespace_survives_store_and_same_tail_legacy_owner(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    first = store.create_plan(_source_plan(_identity("168800001"), "990003", model="990004"))
    second = store.create_plan(_source_plan(_identity("168800002"), "770003", suffix="b"))
    assert first["sku_key"] == "990003"
    assert second["sku_key"] == "0003"
    assert ReleaseStore(store.path).active_reserved_sku_keys() == ("0003", "990003", "990004")


@pytest.mark.parametrize("owner_mode", ["NEW_SOURCE", "INHERITED_PREDECESSOR"])
def test_context_exposes_other_source_claim_without_breaking_inherited_parser(tmp_path, owner_mode):
    store = ReleaseStore(tmp_path / "release.db")
    source_a, source_b = _identity("168800001"), _identity("168800002")
    first = _source_plan(source_a, "0946")
    if owner_mode == "NEW_SOURCE":
        store.create_plan(first)
    else:
        _approve(store, first)
        context = _context(store, source_a)
        inherited = resolve_sku_lineage_reservation(source_identity=source_a, **context_for_resolver(context))
        assert inherited.ready
        successor = _source_plan(source_a, "0946", suffix="inherited")
        successor["sku_lineage"] = inherited.payload()
        store.create_plan(successor)
    context = _context(ReleaseStore(store.path), source_b)
    # NEW rows cannot enter this old parser: they do not have predecessors.
    unresolved = resolve_sku_lineage_reservation(source_identity=source_b, **context_for_resolver(context))
    assert unresolved.ready and unresolved.lineage_mode == "NEW_SOURCE"
    desired = SkuAssignment(seller_sku="0946", model_skus=(ModelSkuAssignment(variant_key="size-large", model_sku="0946"),))
    result = finalize_new_source_sku_reservation(source_identity=source_b, assignment=desired, existing_reservations=context.get("active_reservation_claims", context["existing_reservations"]))
    assert not result.ready
    with pytest.raises(SkuReservationConflict, match="another canonical source"):
        store.create_plan(_source_plan(source_b, "0946", suffix="conflict"))
    assert store.get_plan("fixture:conflict") is None


def context_for_resolver(context):
    return {key: context[key] for key in ("predecessor_records", "existing_reservations")}


def test_real_dashboard_consumes_cross_source_conflict_and_keeps_unrelated_claims_clear(tmp_path, monkeypatch):
    # SKU ownership is independent of live or credential-backed FX settings.
    monkeypatch.setattr(release_control, "exchange_rate_for", lambda _currency: 1.0)
    monkeypatch.setattr(release_control, "ozon_exchange_rates", lambda: {"CNY": 1.0, "RUB": 12.0, "USD": 0.14})
    root, database = _release_fixture(tmp_path)
    store = ReleaseStore(root / "data" / "release.db")
    store.create_plan(_source_plan(_identity("168800001"), "0946"))
    before = store.path.read_bytes()
    dashboard = release_control.build_release_dashboard(root=root, database_path=database, report_store_path=store.path, seller_sku="0946")
    assert dashboard["sku_lineage"]["status"] == "BLOCKED_SKU_LINEAGE"
    assert dashboard["sku_lineage"]["ready"] is False
    assert dashboard["sku_lineage"]["assignment"] is None
    other = release_control.build_release_dashboard(root=root, database_path=database, report_store_path=store.path, seller_sku="0948")
    assert other["sku_lineage"]["ready"] is True
    assert store.path.read_bytes() == before


def test_same_source_claim_is_idempotent_and_inheritable(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    source = _identity("168800001")
    first = _source_plan(source, "0946")
    store.create_plan(first)
    context = _context(store, source)
    assignment = SkuAssignment(seller_sku="0946", model_skus=(ModelSkuAssignment(variant_key="size-large", model_sku="0946"),))
    replay = finalize_new_source_sku_reservation(source_identity=source, assignment=assignment, existing_reservations=context.get("active_reservation_claims", context["existing_reservations"]))
    assert replay.ready and replay.reservation.idempotent
    _approve(store, first)
    inherited = resolve_sku_lineage_reservation(source_identity=source, **context_for_resolver(_context(store, source)))
    assert inherited.ready and inherited.lineage_mode == "INHERITED_PREDECESSOR"


def test_real_dashboard_inherits_same_source_with_unrelated_active_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(release_control, "exchange_rate_for", lambda _currency: 1.0)
    monkeypatch.setattr(release_control, "ozon_exchange_rates", lambda: {"CNY": 1.0})
    root, database = _release_fixture(tmp_path)
    store = ReleaseStore(root / "data" / "release.db")
    _approve(store, _source_plan(_identity("16881"), "0956", model="0957"))
    store.create_plan(_source_plan(_identity("168800002"), "0946", suffix="other"))
    before = store.path.read_bytes()
    result = release_control.build_release_dashboard(root=root, database_path=database, report_store_path=store.path)
    assert result["sku_lineage"]["ready"] is True
    assert result["sku_lineage"]["lineage_mode"] == "INHERITED_PREDECESSOR"
    assert result["product"]["seller_sku_candidate"] == "0956"
    assert result["sku_lineage"]["assignment"]["model_skus"] == [{"variant_key": "size-large", "model_sku": "0957"}]
    assert store.path.read_bytes() == before


def test_projection_copies_warehouse_before_digest_and_detaches_inputs():
    dashboard, payload = _raw_approval_inputs()
    allocation = _warehouse_allocations()
    dashboard["product"]["warehouse_inventory_by_target"] = deepcopy(allocation)
    payload["product_facts"]["warehouse_inventory_by_target"] = deepcopy(allocation)
    inputs = build_approved_publication_snapshot_inputs(dashboard=dashboard, release_plan_payload=payload)
    payload["product_facts"].pop("warehouse_inventory_by_target")
    result = project_release_plan_for_publication_snapshot(payload, approved_inputs=inputs)
    assert result.ready, result.missing_fields
    assert result.payload["product_facts"]["warehouse_inventory_by_target"] == allocation
    inputs["warehouse_inventory_by_target"]["tiktok:LH_PH"]["warehouses"][0]["stock"] = 0
    assert result.payload["product_facts"]["warehouse_inventory_by_target"] == allocation


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_existing_shopee_master_contract_roundtrips_store_without_field_loss(tmp_path, version):
    dashboard, payload = _raw_approval_inputs()
    source = _identity(payload["product_id"])
    assignment = SkuAssignment(
        seller_sku=payload["seller_sku"],
        model_skus=tuple(ModelSkuAssignment(**row) for row in payload["sku_lineage"]["assignment"]["model_skus"]),
    )
    lineage = resolve_sku_lineage_reservation(source_identity=source, predecessor_records=[])
    reservation = finalize_new_source_sku_reservation(source_identity=source, assignment=assignment)
    payload["source_product_identity"] = source.payload()
    payload["sku_lineage"] = {**lineage.payload(), "assignment": assignment.payload(), "reservation": reservation.reservation.payload()}
    if version == "v2":
        payload["product_facts"].pop("shopee_global_variant_image_positions")
        payload["product_facts"]["shopee_global_variant_image_bindings"] = _external_variant_bindings(payload["product_id"])
    payload["pricing"]["master_price_source"] = {"region": "PH", "target_key": "lh_ph"}
    payload["pricing"]["selected_targets"]["shopee:PH"]["source"] = {"region": "PH", "target_key": "lh_ph"}
    for index, row in enumerate(payload["pricing"]["selected_targets"]["shopee:PH"]["sku_prices"]):
        row["global_original_price_cny"] = str(40 + index)
    inputs = build_approved_publication_snapshot_inputs(dashboard=dashboard, release_plan_payload=payload)
    expected = deepcopy(inputs["shopee_global_master"])
    projected = project_release_plan_for_publication_snapshot(payload, approved_inputs=inputs)
    assert projected.ready, projected.missing_fields
    assert projected.payload["shopee_global_master"] == expected
    assert expected["schema_version"] == "shopee-global-master/" + version
    inputs["shopee_global_master"]["policy"]["stock"]["quantity"] = 1
    assert projected.payload["shopee_global_master"] == expected
    store = ReleaseStore(tmp_path / "release.db")
    plan = _approve(store, projected.payload)
    frozen = ReleaseStore(store.path).approved_publication_snapshot(offer_id=plan["product_id"], plan_id=plan["plan_id"])
    assert frozen["shopee_global_master"] == expected


@pytest.mark.parametrize("bad", [None, [], "warehouse", 1])
def test_projection_does_not_silently_discard_malformed_warehouse(bad):
    dashboard, payload = _raw_approval_inputs()
    inputs = build_approved_publication_snapshot_inputs(dashboard=dashboard, release_plan_payload=payload)
    inputs["warehouse_inventory_by_target"] = bad
    result = project_release_plan_for_publication_snapshot(payload, approved_inputs=inputs)
    assert result.ready is False


def _allocation(shop_id):
    return {"schema_version": "miaoshou-tiktok-warehouse-allocation/v1", "shop_id": str(shop_id), "warehouses": [{"warehouse_id": "1001", "warehouse_name": "Approved warehouse", "stock": 200}], "total_stock": 200, "source": "CONVERSATION_APPROVAL", "approved_by": "Kyle", "approved_at": "2026-09-05T00:00:00+00:00"}


def test_server_bridge_warehouse_reaches_immutable_store_and_reopen(tmp_path):
    dashboard = _production_dashboard_with_exact_v4_inputs()
    dashboard.pop("_approved_publication_snapshot_inputs")
    allocation = {"tiktok:MX": _allocation("16265910")}
    dashboard["product"]["warehouse_inventory_by_target"] = deepcopy(allocation)
    payload, blockers = product_server._release_plan_payload_from_dashboard(dashboard)
    assert blockers == []
    assert payload["product_facts"]["warehouse_inventory_by_target"] == allocation
    store = ReleaseStore(tmp_path / "release.db")
    plan = _approve(store, payload)
    frozen = store.approved_publication_snapshot(offer_id=plan["product_id"], plan_id=plan["plan_id"])
    assert frozen["product"]["warehouse_inventory_by_target"] == allocation
    dashboard["product"]["warehouse_inventory_by_target"]["tiktok:MX"]["warehouses"][0]["stock"] = 1
    assert ReleaseStore(store.path).approved_publication_snapshot(offer_id=plan["product_id"], plan_id=plan["plan_id"]) == frozen
    assert store.get_run("release-run:" + plan["plan_id"]) is None


def test_frozen_warehouse_tamper_cannot_reopen_or_reapprove(tmp_path):
    dashboard = _production_dashboard_with_exact_v4_inputs()
    dashboard.pop("_approved_publication_snapshot_inputs")
    dashboard["product"]["warehouse_inventory_by_target"] = {"tiktok:MX": _allocation("16265910")}
    payload, blockers = product_server._release_plan_payload_from_dashboard(dashboard)
    assert blockers == []
    store = ReleaseStore(tmp_path / "release.db")
    plan = _approve(store, payload)
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE approved_publication_snapshots SET snapshot_json = '{}'")
        connection.rollback()
        # Fault injection stays inside this disposable, synthetic test database.
        connection.execute("DROP TRIGGER trg_approved_publication_snapshot_immutable")
        raw = connection.execute("SELECT snapshot_json FROM approved_publication_snapshots").fetchone()[0]
        document = json.loads(raw)
        document["product"]["warehouse_inventory_by_target"]["tiktok:MX"]["warehouses"][0]["stock"] = 1
        connection.execute("UPDATE approved_publication_snapshots SET snapshot_json = ?", (json.dumps(document),))
    with pytest.raises(ImmutableReleaseError):
        ReleaseStore(store.path).approved_publication_snapshot(offer_id=plan["product_id"], plan_id=plan["plan_id"])
    with pytest.raises(ImmutableReleaseError):
        _approve(store, payload)


def _consumer_input(tmp_path):
    _store, plan = _approved_tiktok_context(tmp_path)
    allocation = {label: _allocation(shop) for label, shop in EXPECTED_SHOP_IDS.items()}
    plan = deepcopy(plan)
    plan["payload"]["product_facts"]["warehouse_inventory_by_target"] = deepcopy(allocation)
    plan["payload_digest"] = _digest(plan["payload"])
    identity = approved_plan_identity(plan)
    contexts = {}
    for label, shop in EXPECTED_SHOP_IDS.items():
        context = {"schema_version": "collectbox-tiktok-publish-context/v1", **identity, "action_id": "fixture-action", "platform": "TIKTOK", "common_identity_digest": "a" * 64, "receipt_digest": "b" * 64, "target_detail_identity": CollectBoxTargetDetailIdentity(target_label=label, detail_id="2001", shop_id=str(shop)).internal_payload()}
        context["publish_identity_digest"] = _digest(context)
        contexts[label] = context
    return plan, contexts, allocation


def test_tiktok_consumer_projects_exact_warehouse_and_retains_unavailable_target(tmp_path):
    plan, contexts, allocation = _consumer_input(tmp_path)
    contexts.pop("tiktok:GB")
    result = build_approved_tiktok_publish_snapshot(plan, collectbox_contexts=contexts)
    for row in result["targets"]:
        assert row["expected_warehouse_inventory"] == allocation[row["target_label"]]
    assert result["unavailable_targets"] == [{"target_label": "tiktok:GB", "reason_code": "draft_identity_unavailable"}]
    plan["payload"]["product_facts"]["warehouse_inventory_by_target"]["tiktok:LH_PH"]["warehouses"][0]["stock"] = 1
    assert result["targets"][0]["expected_warehouse_inventory"]["total_stock"] == 200
    assert result["targets"][0]["expected_warehouse_inventory"]["warehouses"][0]["stock"] == 200


@pytest.mark.parametrize("tamper", ["shop", "missing_target", "duplicate", "bool_stock", "negative_stock", "bool_total", "total", "actor", "time", "naive_time"])
def test_tiktok_consumer_rejects_warehouse_drift(tmp_path, tamper):
    plan, contexts, allocation = _consumer_input(tmp_path)
    value = plan["payload"]["product_facts"]["warehouse_inventory_by_target"]
    row = value["tiktok:LH_PH"]
    if tamper == "shop": row["shop_id"] = "999"
    elif tamper == "missing_target": value.pop("tiktok:LH_PH")
    elif tamper == "duplicate": row["warehouses"].append(deepcopy(row["warehouses"][0]))
    elif tamper == "bool_stock": row["warehouses"][0]["stock"] = True
    elif tamper == "negative_stock": row["warehouses"][0]["stock"] = -1
    elif tamper == "bool_total": row["total_stock"] = True
    elif tamper == "total": row["total_stock"] = 201
    elif tamper == "actor": row["approved_by"] = " "
    elif tamper == "time": row["approved_at"] = "invalid"
    elif tamper == "naive_time": row["approved_at"] = "2026-09-05T00:00:00"
    with pytest.raises(ValueError, match="warehouse"):
        build_approved_tiktok_publish_snapshot(plan, collectbox_contexts=contexts)


def test_tiktok_consumer_keeps_legacy_absence_compatible(tmp_path):
    plan, contexts, allocation = _consumer_input(tmp_path)
    plan["payload"]["product_facts"].pop("warehouse_inventory_by_target")
    result = build_approved_tiktok_publish_snapshot(plan, collectbox_contexts=contexts)
    assert all("expected_warehouse_inventory" not in row for row in result["targets"])
