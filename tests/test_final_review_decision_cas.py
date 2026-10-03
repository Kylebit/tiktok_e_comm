from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from copy import deepcopy

import pytest

from shared_platform.publication_r3_image_bridge import TARGET_LOCALE
from shared_platform.release_store import (
    ImmutableReleaseError,
    ReleaseAuthorizationError,
    ReleaseStore,
    preview_release_plan,
)


def _canonical(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value) -> str:
    raw = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _synthetic_r2_identity(offer_id="3956742887"):
    identity = {
        "schema_version": "publication-r2-identity/v1",
        "offer_id": offer_id,
        "round1_snapshot_digest": "sha256:" + "0" * 64,
        "first_review_digest": "sha256:" + "4" * 64,
        "generation_identity_digest": "sha256:" + "5" * 64,
        "generation_digest": "sha256:" + "6" * 64,
        "translation_plan_digest": "sha256:" + "7" * 64,
        "translation_result_digest": "sha256:" + "8" * 64,
        "qa_digest": "sha256:" + "9" * 64,
        "artifact_digests": ["sha256:" + "2" * 64, "sha256:" + "3" * 64],
    }
    return {**identity, "identity_digest": _digest(identity)}


def _payload(*, suffix="v1", offer_id="3956742887", seller_sku="0952"):
    return {
        "plan_id": f"common:{offer_id}:{suffix}",
        "product_id": offer_id,
        "seller_sku": seller_sku,
        "product_package_id": f"product:{offer_id}:{suffix}",
        "content_package_id": f"content:{offer_id}:{suffix}",
        "product_revision": 5,
        "targets": ["miaoshou:COMMON"],
        "product_facts": {
            "title": "Synthetic exact product",
            "cost_cny": "18.40",
            "selected_sku_price": "399.00",
        },
        "r3_stage_binding": {
            "schema_version": "r3-common-stage/v1",
            "execution_scope": ["miaoshou:COMMON"],
            "image_approval_scope": "ROUND2_IMAGES_ONLY",
            "write_approval_source": "ReleaseStore",
            "marketplace_targets": ["tiktok:LH_PH", "ozon:RU"],
            "round1_snapshot_digest": "sha256:" + "0" * 64,
            "r2_identity": _synthetic_r2_identity(offer_id),
        },
    }


def _contract(payload, *, business_facts=None, targets=None):
    preview = preview_release_plan(payload)
    targets = list(targets or ["tiktok:LH_PH", "ozon:RU"])
    identity = deepcopy(payload["r3_stage_binding"]["r2_identity"])
    master_digest = "sha256:" + "2" * 64
    localized_digest = "sha256:" + "3" * 64
    images = [
        {
            "kind": "master",
            "review_number": 1,
            "brand_id": "livelyhive-sea",
            "role": "hero",
            "locale": None,
            "artifact_digest": master_digest,
        },
        {
            "kind": "localized",
            "review_number": 1,
            "brand_id": "livelyhive-sea",
            "role": "hero",
            "locale": "en-PH",
            "artifact_digest": localized_digest,
        },
    ]
    routes = {
        target: [
            {
                "position": 1,
                "brand_id": "livelyhive-sea",
                "role": "hero",
                "artifact_digest": master_digest,
                "source_review_number": 1,
                "locale": TARGET_LOCALE[target],
            }
        ]
        for target in targets
    }
    business_facts = business_facts or {
        "cost_cny": "18.40",
        "selected_sku_price": "399.00",
    }
    return {
        "schema_version": "publication-final-review-preview/v1",
        "status": "UNAPPROVED_PREVIEW",
        "offer_id": payload["product_id"],
        "product_revision": payload["product_revision"],
        "seller_sku": payload["seller_sku"],
        "round1_snapshot_digest": "sha256:" + "0" * 64,
        "round1_source_approval": {
            "actor": "Kyle",
            "authority": "EXPLICIT_CONVERSATION_APPROVAL",
            "human_approval": True,
        },
        "r2_identity": identity,
        "r2_images": images,
        "r2_target_image_routes": routes,
        "current_business_facts_digest": _digest(business_facts),
        "marketplace_targets": targets,
        "common": {
            "plan_id": preview["plan_id"],
            "payload": preview["payload"],
            "payload_digest": preview["payload_digest"],
            "confirmation_token_digest": _digest(
                preview["confirmation_token"].encode("utf-8")
            ),
        },
        "expected_write_scope": [
            {
                "stage": "R3_COMMON",
                "target": "miaoshou:COMMON",
                "operation": "common_draft_sync",
            },
            *[
                {
                    "stage": "R3_MARKETPLACE",
                    "target": target,
                    "operation": "target_publication",
                }
                for target in targets
            ],
        ],
        "execution_authority": False,
        "external_writes_performed": [],
    }


def _prepared(tmp_path, *, payload=None):
    store = ReleaseStore(tmp_path / "release.db")
    payload = payload or _payload()
    plan = store.create_plan(payload)
    return store, plan, _contract(payload)


def _record(store, contract):
    return store.cas_final_review_decision(
        contract,
        approved_by="Kyle",
        user_approved=True,
    )


def test_cas_accepts_canonical_preview_builder_output(tmp_path, monkeypatch):
    from tests.test_publication_final_review_preview import build, candidate

    documents, dashboard, store, view = candidate(tmp_path, monkeypatch)
    contract = build(documents, dashboard, view).as_dict()
    plan = store.create_plan(view["common"]["plan"]["payload"])

    receipt = _record(store, contract)

    assert receipt["created"] is True
    assert receipt["decision"]["common_plan_id"] == plan["plan_id"]
    assert receipt["decision"]["contract"] == contract
    assert receipt["execution_authority"] is False


@pytest.mark.parametrize(
    "actor,authority,flag",
    [
        ("Kyle", "EXPLICIT_CONVERSATION_APPROVAL", 1),
        ("Kyle", "EXPLICIT_CONVERSATION_APPROVAL", 1.0),
        ("product-publication-autopilot", "ACTIVE_AUTOPILOT_POLICY", 0),
        ("product-publication-autopilot", "ACTIVE_AUTOPILOT_POLICY", 0.0),
    ],
)
def test_cas_requires_literal_r1_source_approval_bool(
    tmp_path, actor, authority, flag
):
    store, plan, contract = _prepared(tmp_path)
    contract["round1_source_approval"] = {
        "actor": actor,
        "authority": authority,
        "human_approval": flag,
    }

    with pytest.raises(ValueError, match="R1 source approval"):
        _record(store, contract)
    assert store.get_final_review_decision(common_plan_id=plan["plan_id"]) is None


def test_cas_accepts_literal_autopilot_and_target_localized_route(tmp_path):
    store, plan, contract = _prepared(tmp_path)
    contract["round1_source_approval"] = {
        "actor": "product-publication-autopilot",
        "authority": "ACTIVE_AUTOPILOT_POLICY",
        "human_approval": False,
    }
    contract["r2_target_image_routes"]["tiktok:LH_PH"][0]["artifact_digest"] = (
        contract["r2_images"][1]["artifact_digest"]
    )

    receipt = _record(store, contract)
    assert receipt["decision"]["common_plan_id"] == plan["plan_id"]
    assert receipt["execution_authority"] is False


@pytest.mark.parametrize("target", ["tiktok:LH_PH", "ozon:RU"])
@pytest.mark.parametrize("locale", ["unexpected-locale", "ms-MY"])
def test_cas_binds_master_route_locale_to_exact_target(tmp_path, target, locale):
    store, plan, contract = _prepared(tmp_path)
    contract["r2_target_image_routes"][target][0]["locale"] = locale

    with pytest.raises(ValueError, match="route locale"):
        _record(store, contract)
    assert store.get_final_review_decision(common_plan_id=plan["plan_id"]) is None


@pytest.mark.parametrize(
    "mutant",
    [
        "missing_r1_source_approval",
        "invalid_r1_source_approval",
        "r2_other_offer",
        "r2_extra_revision",
        "r2_identity_digest",
        "missing_image_identity",
        "missing_route_identity",
        "extra_common_field",
        "common_r2_binding",
    ],
)
def test_cas_rejects_noncanonical_or_cross_bound_preview_v1(
    tmp_path, monkeypatch, mutant
):
    from tests.test_publication_final_review_preview import build, candidate

    documents, dashboard, store, view = candidate(tmp_path, monkeypatch)
    contract = build(documents, dashboard, view).as_dict()
    plan = store.create_plan(view["common"]["plan"]["payload"])
    if mutant == "missing_r1_source_approval":
        del contract["round1_source_approval"]
    elif mutant == "invalid_r1_source_approval":
        contract["round1_source_approval"]["human_approval"] = False
    elif mutant == "r2_other_offer":
        contract["r2_identity"]["offer_id"] = "9999999999"
    elif mutant == "r2_extra_revision":
        contract["r2_identity"]["product_revision"] = 999
    elif mutant == "r2_identity_digest":
        contract["r2_identity"]["identity_digest"] = "sha256:" + "f" * 64
    elif mutant == "missing_image_identity":
        contract["r2_images"][0].pop("brand_id")
        contract["r2_images"][0].pop("review_number")
    elif mutant == "missing_route_identity":
        first = contract["marketplace_targets"][0]
        contract["r2_target_image_routes"][first][0].pop("position")
        contract["r2_target_image_routes"][first][0].pop("brand_id")
    elif mutant == "extra_common_field":
        contract["common"]["raw_confirmation_token"] = "must-not-be-accepted"
    else:
        contract["common"]["payload"]["r3_stage_binding"]["r2_identity"] = {
            **contract["r2_identity"],
            "offer_id": "9999999999",
        }

    with pytest.raises((ValueError, TypeError, ReleaseAuthorizationError)):
        _record(store, contract)
    assert store.get_final_review_decision(common_plan_id=plan["plan_id"]) is None


def test_cas_records_decision_and_inert_provenance_without_approving_plan(tmp_path):
    store, plan, contract = _prepared(tmp_path)

    receipt = _record(store, contract)
    repeated = _record(store, deepcopy(contract))

    assert receipt["created"] is True
    assert repeated["created"] is False
    assert repeated["decision"] == receipt["decision"]
    assert repeated["derived_stage_authorization"] == receipt[
        "derived_stage_authorization"
    ]
    assert receipt["plan_status"] == "PENDING_APPROVAL"
    assert receipt["execution_authority"] is False
    assert receipt["external_facts_verified"] is False
    assert receipt["caller_revalidation_required"] is True
    assert receipt["derived_stage_authorization"]["status"] == (
        "RECORDED_NOT_EXECUTABLE"
    )
    assert receipt["derived_stage_authorization"]["execution_authority"] is False
    assert store.get_plan(plan["plan_id"])["status"] == "PENDING_APPROVAL"
    assert store.get_plan(plan["plan_id"])["approval"] is None
    with pytest.raises(ReleaseAuthorizationError, match="final review decision"):
        store.start_run(plan["plan_id"])
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM release_approvals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM release_runs").fetchone()[0] == 0


def test_read_interfaces_never_invent_execution_authority(tmp_path):
    store, plan, contract = _prepared(tmp_path)
    receipt = _record(store, contract)

    decision = store.get_final_review_decision(common_plan_id=plan["plan_id"])
    stage = store.get_final_review_stage_authorization(plan_id=plan["plan_id"])

    assert decision == receipt["decision"]
    assert decision["execution_authority"] is False
    assert stage == receipt["derived_stage_authorization"]
    assert stage["execution_authority"] is False
    assert stage["binding"]["execution_authority"] is False
    assert stage["provenance"]["execution_authority"] is False


@pytest.mark.parametrize("user_approved", [False, 1, "true", None])
def test_cas_requires_literal_kyle_decision(tmp_path, user_approved):
    store, _plan, contract = _prepared(tmp_path)
    with pytest.raises(ReleaseAuthorizationError):
        store.cas_final_review_decision(
            contract,
            approved_by="Kyle",
            user_approved=user_approved,
        )


@pytest.mark.parametrize(
    "drift",
    ["offer", "revision", "targets", "images", "cost", "selected_price", "common"],
)
def test_existing_decision_rejects_identity_content_and_business_drift(
    tmp_path, drift
):
    store, _plan, contract = _prepared(tmp_path)
    _record(store, contract)
    changed = deepcopy(contract)
    if drift == "offer":
        changed["offer_id"] = "3956742888"
    elif drift == "revision":
        changed["product_revision"] = 6
    elif drift == "targets":
        changed = _contract(_payload(), targets=["ozon:RU", "tiktok:LH_PH"])
    elif drift == "images":
        new_digest = "sha256:" + "3" * 64
        changed["r2_images"][0]["artifact_digest"] = new_digest
        for rows in changed["r2_target_image_routes"].values():
            rows[0]["artifact_digest"] = new_digest
    elif drift in {"cost", "selected_price"}:
        facts = {"cost_cny": "18.40", "selected_sku_price": "399.00"}
        facts["cost_cny" if drift == "cost" else "selected_sku_price"] = "999.00"
        changed["current_business_facts_digest"] = _digest(facts)
    else:
        changed_payload = deepcopy(changed["common"]["payload"])
        changed_payload["product_facts"]["title"] = "Drifted title"
        changed = _contract(changed_payload)

    with pytest.raises((ImmutableReleaseError, ReleaseAuthorizationError, ValueError)):
        _record(store, changed)


def test_concurrent_identical_cas_has_one_durable_record(tmp_path):
    path = tmp_path / "release.db"
    store = ReleaseStore(path)
    payload = _payload()
    store.create_plan(payload)
    contract = _contract(payload)
    barrier = threading.Barrier(2)
    outcomes = []
    failures = []
    lock = threading.Lock()

    def worker():
        try:
            barrier.wait()
            result = _record(ReleaseStore(path), deepcopy(contract))
            with lock:
                outcomes.append(result)
        except Exception as error:  # pragma: no cover - asserted below
            with lock:
                failures.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert sorted(result["created"] for result in outcomes) == [False, True]
    assert len({result["decision"]["decision_id"] for result in outcomes}) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_final_review_decisions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM release_final_review_stage_authorizations"
        ).fetchone()[0] == 1


def test_concurrent_conflicting_cas_fails_closed(tmp_path):
    path = tmp_path / "release.db"
    store = ReleaseStore(path)
    payload = _payload()
    store.create_plan(payload)
    contracts = [
        _contract(payload),
        _contract(
            payload,
            business_facts={"cost_cny": "19.00", "selected_sku_price": "399.00"},
        ),
    ]
    barrier = threading.Barrier(2)
    outcomes = []
    lock = threading.Lock()

    def worker(contract):
        try:
            barrier.wait()
            result = _record(ReleaseStore(path), contract)
            value = ("ok", result["created"])
        except Exception as error:
            value = ("error", type(error))
        with lock:
            outcomes.append(value)

    threads = [
        threading.Thread(target=worker, args=(deepcopy(contract),))
        for contract in contracts
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(kind == "ok" for kind, _ in outcomes) == 1
    assert sum(
        kind == "error" and issubclass(value, ImmutableReleaseError)
        for kind, value in outcomes
    ) == 1


def test_second_insert_failure_rolls_back_decision_and_retry_is_safe(tmp_path):
    store, _plan, contract = _prepared(tmp_path)
    # Initialize the package-1 schema, then inject a local crash boundary.
    assert store.get_final_review_decision(common_plan_id=contract["common"]["plan_id"]) is None
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER synthetic_abort_stage_insert
            BEFORE INSERT ON release_final_review_stage_authorizations
            BEGIN SELECT RAISE(ABORT, 'synthetic stage crash'); END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic stage crash"):
        _record(store, contract)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_final_review_decisions"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM release_final_review_stage_authorizations"
        ).fetchone()[0] == 0
        connection.execute("DROP TRIGGER synthetic_abort_stage_insert")

    first = _record(store, contract)
    lost_response_retry = _record(store, contract)
    assert first["created"] is True
    assert lost_response_retry["created"] is False


def test_decision_and_provenance_are_database_immutable(tmp_path):
    store, _plan, contract = _prepared(tmp_path)
    receipt = _record(store, contract)
    with sqlite3.connect(store.path) as connection:
        for statement, message in [
            (
                "UPDATE release_final_review_decisions SET status='RECORDED'",
                "final review decisions are immutable",
            ),
            (
                "DELETE FROM release_final_review_decisions",
                "final review decisions are append-only",
            ),
            (
                "UPDATE release_final_review_stage_authorizations SET status='RECORDED_NOT_EXECUTABLE'",
                "final review stage provenance is immutable",
            ),
            (
                "DELETE FROM release_final_review_stage_authorizations",
                "final review stage provenance is append-only",
            ),
        ]:
            with pytest.raises(sqlite3.IntegrityError, match=message):
                connection.execute(statement)
            connection.rollback()
    assert store.get_final_review_decision(
        decision_id=receipt["decision"]["decision_id"]
    ) == receipt["decision"]


@pytest.mark.parametrize("recursive_triggers", [False, True])
@pytest.mark.parametrize(
    "table,conflict_key,changed_field",
    [
        ("release_final_review_decisions", "primary_key", "approved_at"),
        ("release_final_review_decisions", "contract_digest", "approved_at"),
        ("release_final_review_decisions", "common_plan_id", "approved_at"),
        ("release_final_review_stage_authorizations", "primary_key", "created_at"),
        ("release_final_review_stage_authorizations", "decision_stage", "created_at"),
        ("release_final_review_stage_authorizations", "plan_stage", "created_at"),
    ],
)
def test_insert_or_replace_cannot_overwrite_append_only_rows(
    tmp_path, table, conflict_key, changed_field, recursive_triggers
):
    store, _plan, contract = _prepared(tmp_path)
    receipt = _record(store, contract)
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            f"PRAGMA recursive_triggers={'ON' if recursive_triggers else 'OFF'}"
        )
        fields = [
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        ]
        select = list(fields)
        select[fields.index(changed_field)] = "'2099-01-01T00:00:00Z'"
        if conflict_key in {"contract_digest", "common_plan_id"}:
            select[fields.index("decision_id")] = "'final-review-decision:replacement'"
        if conflict_key == "common_plan_id":
            select[fields.index("contract_digest")] = "'sha256:" + "e" * 64 + "'"
        if conflict_key in {"decision_stage", "plan_stage"}:
            select[fields.index("authorization_id")] = "'final-review-stage:replacement'"
        statement = (
            f"INSERT OR REPLACE INTO {table} ({','.join(fields)}) "
            f"SELECT {','.join(select)} FROM {table} LIMIT 1"
        )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
            connection.execute(statement)

    assert store.get_final_review_decision(
        decision_id=receipt["decision"]["decision_id"]
    )["approved_at"] == receipt["decision"]["approved_at"]
    assert store.get_final_review_stage_authorization(
        authorization_id=receipt["derived_stage_authorization"]["authorization_id"]
    )["created_at"] == receipt["derived_stage_authorization"]["created_at"]


def test_controlled_store_connections_enable_recursive_triggers(tmp_path):
    store, _plan, _contract_value = _prepared(tmp_path)
    with store._connect() as connection:
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    with store._connect_readonly() as connection:
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 1


def test_existing_release_store_without_package1_tables_migrates_cleanly(tmp_path):
    path = tmp_path / "legacy-release.db"
    payload = _payload()
    store = ReleaseStore(path)
    original = store.create_plan(payload)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE release_final_review_stage_authorizations")
        connection.execute("DROP TABLE release_final_review_decisions")

    restarted = ReleaseStore(path)
    repeated = restarted.create_plan(payload)
    receipt = _record(restarted, _contract(payload))

    assert repeated["created"] is False
    assert repeated["payload_digest"] == original["payload_digest"]
    assert receipt["created"] is True
    assert receipt["plan_status"] == "PENDING_APPROVAL"
    assert restarted.get_plan(original["plan_id"])["approval"] is None
    assert restarted.database_health()["integrity_check"] == "ok"


def test_legacy_approval_does_not_flow_into_successor_decision(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    old_payload = _payload(suffix="old")
    old = store.create_plan(old_payload)
    old_approval = store.approve_plan(
        old["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=old["confirmation_token"],
    )
    new_payload = _payload(suffix="new")
    new = store.create_plan(new_payload, supersedes_plan_id=old["plan_id"])

    assert old_approval["status"] == "APPROVED"
    assert new["status"] == "PENDING_APPROVAL"
    assert store.get_final_review_decision(common_plan_id=new["plan_id"]) is None
    receipt = _record(store, _contract(new_payload))

    assert receipt["plan_status"] == "PENDING_APPROVAL"
    assert receipt["execution_authority"] is False
    assert store.get_plan(new["plan_id"])["approval"] is None
    with pytest.raises(ReleaseAuthorizationError, match="final review decision"):
        store.start_run(new["plan_id"])
    assert store.database_health()["integrity_check"] == "ok"
    assert store.database_health()["foreign_key_violations"] == []
