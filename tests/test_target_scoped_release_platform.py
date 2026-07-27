from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request

import pytest

from domains.channel_operations.release_executor import (
    AdapterExecutionResult,
    AdapterRegistration,
)
from modules.products import server as product_server
from shared_platform import release_store
from shared_platform.release_store import (
    ReleaseAuthorizationError,
    ReleaseStore,
    ReleaseStoreError,
)
from shared_platform.target_scoped_release_contracts import (
    OfficialTargetProof,
    TargetScopedContractError,
    TargetScopedOperationRequest,
    TargetScopedOperationResult,
    operation_kind_for_target,
)


def _plan(target_label: str = "shopee:MY") -> dict:
    return {
        "plan_id": "omnichannel:target-scoped-platform",
        "product_id": "3838616043",
        "seller_sku": "0954",
        "product_package_id": "product:3838616043:0954:r1",
        "content_package_id": "content:3838616043:r1",
        "targets": [target_label],
        "product_revision": 41,
        "omnichannel_scope_digest": "scope-0954",
    }


def _failed_store(tmp_path, target_label: str = "shopee:MY"):
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(_plan(target_label))
    store.approve_plan(
        plan["plan_id"],
        confirmation_token=plan["confirmation_token"],
        approved_by="Kyle",
        user_approved=True,
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], target_label)
    store.record_target_failure(
        run["run_id"],
        target_label,
        error="official pre-submit validation failed; no external write",
        failure_evidence={
            "phase": "pre_submit",
            "pre_submit_failure": True,
            "external_writes_performed": [],
        },
    )
    return store, store.get_plan(plan["plan_id"]), store.get_run(run["run_id"])


def _request(store: ReleaseStore, plan: dict, target_label: str):
    context = store.target_scoped_action_context(
        plan_id=plan["plan_id"],
        target_label=target_label,
    )
    payload = plan["payload"]
    return TargetScopedOperationRequest(
        plan_id=plan["plan_id"],
        confirmation_token=plan["confirmation_token"],
        approval_scope_digest=payload["omnichannel_scope_digest"],
        product_id=plan["product_id"],
        seller_sku=plan["seller_sku"],
        product_package_id=plan["product_package_id"],
        content_package_id=plan["content_package_id"],
        run_id=context["run_id"],
        target_label=target_label,
        operation_kind=context["operation_kind"],
        product_revision=context["product_revision"],
        payload_digest=context["payload_digest"],
        preflight_digest=context["preflight_digest"],
        failure_attempt=context["failure_attempt"],
        failure_digest=context["failure_digest"],
        target_idempotency_key=context["target_idempotency_key"],
    )


def _proof_value(request: TargetScopedOperationRequest, **overrides):
    now = datetime.now(timezone.utc)
    value = {
        "schema_version": "official-target-proof/v1",
        "operation_kind": request.operation_kind,
        "plan_id": request.plan_id,
        "run_id": request.run_id,
        "target_label": request.target_label,
        "product_revision": request.product_revision,
        "payload_digest": request.payload_digest,
        "preflight_digest": request.preflight_digest,
        "failure_attempt": request.failure_attempt,
        "failure_digest": request.failure_digest,
        "provided_by": "03",
        "allow_refresh": False,
        "observed_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "checks": {
            "official_identity_exact": True,
            "duplicate_absent_or_existing_exact": True,
            "credential_or_tenant_ready": True,
        },
        "semantic_evidence": {
            "source": "official_platform_read",
            "target": request.target_label,
            "result": "safe",
        },
        "redacted_summary": {
            "target": request.target_label,
            "status": "safe",
        },
        "external_writes_performed": [],
    }
    value.update(overrides)
    return value


def _proof(request: TargetScopedOperationRequest, **overrides):
    return OfficialTargetProof.from_value(
        _proof_value(request, **overrides),
        request=request,
    )


def _success_result(external_reference: str = "item-0954"):
    return TargetScopedOperationResult.from_value(
        {
            "succeeded": True,
            "readback_verified": True,
            "detail": "single target write matched official readback",
            "external_reference": external_reference,
            "submission_accepted": False,
            "evidence": {
                "verified": True,
                "checks": {"identity": True, "payload": True},
                "external_writes_performed": ["shopee:regional_publish"],
            },
        }
    )


@pytest.mark.parametrize(
    ("target_label", "operation_kind"),
    [
        ("shopee:MY", "shopee_safe_pre_submit_retry_v1"),
        ("shopee:VN", "shopee_safe_pre_submit_retry_v1"),
        ("ozon:RU", "ozon_existing_product_stock_reconciliation_v1"),
    ],
)
def test_operation_kind_is_server_derived(target_label, operation_kind):
    assert operation_kind_for_target(target_label) == operation_kind
    with pytest.raises(TargetScopedContractError):
        operation_kind_for_target("shopee:PH")


def test_verified_adapter_result_can_truthfully_record_submission_acceptance():
    result = TargetScopedOperationResult.from_value(
        AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail="official create readback matched",
            external_reference="item-0954",
            readback_evidence={
                "verified": True,
                "external_writes_performed": ["shopee:regional_publish"],
            },
            submission_accepted=True,
        )
    )

    assert result.outcome == "SUCCEEDED"
    assert result.submission_accepted is True


def test_atomic_claim_consumes_proof_without_making_target_pending(tmp_path):
    store, plan, run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)

    claim = store.claim_target_scoped_operation(
        request=request,
        proof=proof,
    )

    assert claim["action"] == "claimed"
    asserted = store.get_run(run["run_id"])
    target = asserted["targets"][0]
    assert target["storage_status"] == "FAILED"
    assert target["status"] == "RUNNING"
    assert target["attempts"] == request.failure_attempt + 1
    assert target["target_scoped_operation"]["status"] == "RUNNING"
    with sqlite3.connect(store.path) as connection:
        proof_row = connection.execute(
            """
            SELECT status, operation_digest
            FROM release_target_retry_proofs
            WHERE proof_digest = ?
            """,
            (proof.proof_digest,),
        ).fetchone()
        physical = connection.execute(
            """
            SELECT status FROM release_target_runs
            WHERE run_id = ? AND target_label = 'shopee:MY'
            """,
            (run["run_id"],),
        ).fetchone()
    assert proof_row[0] == "CONSUMED"
    assert proof_row[1] == claim["operation"]["operation_digest"]
    assert physical[0] == "FAILED"


def test_concurrent_claim_has_exactly_one_winner(tmp_path):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)

    def claim():
        try:
            return store.claim_target_scoped_operation(
                request=request,
                proof=proof,
            )["action"]
        except ReleaseStoreError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted([future.result() for future in [pool.submit(claim), pool.submit(claim)]])

    assert outcomes == ["claimed", "rejected"]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 1


def test_success_is_atomic_and_exact_claim_replay_is_zero_write(tmp_path):
    store, plan, run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)
    claim = store.claim_target_scoped_operation(request=request, proof=proof)

    completed = store.record_target_scoped_success(
        claim["operation"]["operation_digest"],
        result=_success_result(),
    )
    assert completed["targets"][0]["status"] == "SUCCEEDED"
    assert completed["targets"][0]["external_id"] == "item-0954"

    replay = store.claim_target_scoped_operation(request=request, proof=proof)
    assert replay["action"] == "already_succeeded"
    assert replay["operation"]["status"] == "SUCCEEDED"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "target_label",
    ["shopee:MY", "shopee:VN", "ozon:RU"],
)
def test_pre_submit_failure_stays_failed_and_generic_retry_is_forbidden(
    tmp_path,
    target_label,
):
    store, plan, run = _failed_store(tmp_path, target_label)
    request = _request(store, plan, target_label)
    claim = store.claim_target_scoped_operation(
        request=request,
        proof=_proof(request),
    )
    result = TargetScopedOperationResult.from_value(
        {
            "succeeded": False,
            "readback_verified": False,
            "detail": "logistics changed before submission",
            "external_reference": None,
            "submission_accepted": False,
            "evidence": {
                "pre_submit_failure": True,
                "external_writes_performed": [],
            },
        }
    )

    failed = store.record_target_scoped_pre_submit_failure(
        claim["operation"]["operation_digest"],
        result=result,
    )
    target = failed["targets"][0]
    assert target["status"] == "FAILED"
    assert target["storage_status"] == "FAILED"
    assert target["target_scoped_operation"]["status"] == "FAILED_PRE_SUBMIT"
    with pytest.raises(ReleaseAuthorizationError, match="target-scoped"):
        store.retry_failed_targets(run["run_id"], [target_label])


def test_reconciliation_preserves_truthful_write_and_blocks_replay(tmp_path):
    store, plan, run = _failed_store(tmp_path, "ozon:RU")
    request = _request(store, plan, "ozon:RU")
    claim = store.claim_target_scoped_operation(
        request=request,
        proof=_proof(request),
    )
    result = TargetScopedOperationResult.from_value(
        {
            "succeeded": False,
            "readback_verified": False,
            "detail": "stock write accepted; readback timed out",
            "external_reference": "5687436857",
            "submission_accepted": False,
            "evidence": {
                "durable_state_uncertain": True,
                "external_writes_performed": ["ozon:stock:update"],
            },
        }
    )
    reconciled = store.record_target_scoped_reconciliation(
        claim["operation"]["operation_digest"],
        result=result,
    )

    target = reconciled["targets"][0]
    assert target["status"] == "RECONCILIATION_REQUIRED"
    assert target["storage_status"] == "FAILED"
    assert target["external_id"] == "5687436857"
    assert target["target_scoped_operation"]["result"][
        "external_writes_performed"
    ] == ["ozon:stock:update"]
    with pytest.raises(ReleaseStoreError, match="terminal"):
        store.claim_target_scoped_operation(
            request=request,
            proof=_proof(request),
        )


def test_claim_fails_closed_on_token_proof_and_failure_identity_drift(tmp_path):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    wrong_token = TargetScopedOperationRequest(
        **{
            **request.__dict__,
            "confirmation_token": "PUBLISH-WRONG",
        }
    )
    with pytest.raises(ReleaseAuthorizationError, match="authority"):
        store.claim_target_scoped_operation(
            request=wrong_token,
            proof=_proof(wrong_token),
        )

    wrong_proof = _proof_value(request, failure_attempt=request.failure_attempt + 1)
    with pytest.raises(TargetScopedContractError, match="identity"):
        OfficialTargetProof.from_value(wrong_proof, request=request)

    store.retry_failed_targets  # keep the store instance live for coverage
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE release_target_runs SET error = 'failure drifted'
            WHERE run_id = ? AND target_label = ?
            """,
            (request.run_id, request.target_label),
        )
        connection.commit()
    with pytest.raises(ReleaseStoreError, match="failure identity"):
        store.claim_target_scoped_operation(
            request=request,
            proof=_proof(request),
        )


def _resolved_gate(store, plan, request):
    run = store.get_run(request.run_id)
    operation = store.get_target_scoped_operation(
        run_id=request.run_id,
        target_label=request.target_label,
    )
    return {
        "gate": {
            "plan": plan,
            "payload": plan["payload"],
            "run": run,
            "dashboard": {},
            "registry": {},
            "target_rows": [],
        },
        "operation_kind": request.operation_kind,
        "existing_operation": operation,
        "request": None if operation else request,
        "context": None,
        "gate_data": {},
    }, None


def _post_body(request, proof):
    return {
        "offer_id": request.product_id,
        "seller_sku": request.seller_sku,
        "publication_targets": [request.target_label],
        "target_label": request.target_label,
        "plan_id": request.plan_id,
        "confirmation_token": request.confirmation_token,
        "expected_revision": request.product_revision,
        "failure_attempt": request.failure_attempt,
        "payload_digest": request.payload_digest,
        "preflight_digest": request.preflight_digest,
        "proof_digest": proof.proof_digest,
        "approved_by": "Kyle",
        "confirm_target_scoped_action": True,
    }


def test_preview_is_readonly_redacted_and_never_refreshes(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)
    calls = []
    adapter = SimpleNamespace(
        build_official_target_proof=lambda req, *, allow_refresh: (
            calls.append((req.target_label, allow_refresh))
            or proof.durable_payload()
        )
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )

    status, payload = product_server._preview_target_scoped_release_action(
        offer_id=request.product_id,
        target_label=request.target_label,
    )

    assert status == 200
    assert payload["summary"] == {"target": "shopee:MY", "status": "safe"}
    assert "confirmation_token" not in payload
    assert calls == [("shopee:MY", False)]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_proofs"
        ).fetchone()[0] == 0


def test_post_exact_single_target_success_and_replay_call_nothing(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)
    proof_calls = []
    execute_calls = []
    adapter = SimpleNamespace(
        build_official_target_proof=lambda req, *, allow_refresh: (
            proof_calls.append((req.target_label, allow_refresh))
            or proof.durable_payload()
        ),
        execute_target_scoped_operation=lambda req, supplied_proof: (
            execute_calls.append(
                (req.target_label, supplied_proof.proof_digest)
            )
            or _success_result()
        ),
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )
    body = _post_body(request, proof)

    status, first = product_server._execute_target_scoped_release_action(body)
    replay_status, replay = (
        product_server._execute_target_scoped_release_action(body)
    )

    assert status == 200
    assert first["external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    assert replay_status == 200
    assert replay["idempotent"] is True
    assert proof_calls == [("shopee:MY", False)]
    assert len(execute_calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved_by", "NotKyle"),
        ("confirm_target_scoped_action", False),
        ("expected_revision", 42),
        ("failure_attempt", 99),
        ("payload_digest", "wrong"),
        ("preflight_digest", "wrong"),
        ("confirmation_token", "wrong"),
    ],
)
def test_post_drift_fails_before_adapter_or_claim(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)
    calls = []
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_proof=lambda *_args, **_kwargs: calls.append(
                "proof"
            ),
            execute_target_scoped_operation=lambda *_args: calls.append(
                "execute"
            ),
        ),
    )
    body = {**_post_body(request, proof), field: value}

    status, _payload = product_server._execute_target_scoped_release_action(
        body
    )

    assert status in {400, 409}
    assert calls == []
    assert store.get_target_scoped_operation(
        run_id=request.run_id,
        target_label=request.target_label,
    ) is None


def test_post_proof_drift_fails_before_claim_or_execute(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    preview_proof = _proof(request)
    changed_proof = _proof(
        request,
        semantic_evidence={
            "source": "official_platform_read",
            "target": request.target_label,
            "result": "safe",
            "observation": "changed after preview",
        },
    )
    execute_calls = []
    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            changed_proof.durable_payload()
        ),
        execute_target_scoped_operation=lambda *_args: execute_calls.append(
            "execute"
        ),
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )

    status, payload = product_server._execute_target_scoped_release_action(
        _post_body(request, preview_proof)
    )

    assert status == 409
    assert payload["code"] == "official_target_proof_drift"
    assert execute_calls == []
    assert store.get_target_scoped_operation(
        run_id=request.run_id,
        target_label=request.target_label,
    ) is None


def test_adapter_unknown_after_write_is_truthful_and_replay_calls_nothing(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path, "ozon:RU")
    request = _request(store, plan, "ozon:RU")
    proof = _proof(request)
    calls = []

    class AmbiguousWriteError(RuntimeError):
        external_reference = "5687436857"
        external_write_evidence = {
            "external_writes_performed": ["ozon:stock:update"],
            "submission_accepted": True,
        }

    def execute(*_args):
        calls.append("execute")
        raise AmbiguousWriteError("official readback timed out")

    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            calls.append("proof") or proof.durable_payload()
        ),
        execute_target_scoped_operation=execute,
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )
    body = _post_body(request, proof)

    status, payload = product_server._execute_target_scoped_release_action(
        body
    )
    replay_status, replay = (
        product_server._execute_target_scoped_release_action(body)
    )

    assert status == 409
    assert payload["reconciliation_required"] is True
    assert payload["durable_state_uncertain"] is True
    assert payload["external_writes_performed"] == ["ozon:stock:update"]
    assert replay_status == 409
    assert replay["operation_status"] == "RECONCILIATION_REQUIRED"
    assert calls == ["proof", "execute"]


def test_post_receipt_failure_becomes_truthful_reconciliation(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path, "ozon:RU")
    request = _request(store, plan, "ozon:RU")
    proof = _proof(request)
    result = TargetScopedOperationResult.from_value(
        {
            "succeeded": True,
            "readback_verified": True,
            "detail": "stock update and readback succeeded",
            "external_reference": "5687436857",
            "submission_accepted": False,
            "evidence": {
                "verified": True,
                "external_writes_performed": ["ozon:stock:update"],
            },
        }
    )
    original_success = store.record_target_scoped_success

    def fail_receipt(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected receipt failure")

    store.record_target_scoped_success = fail_receipt
    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            proof.durable_payload()
        ),
        execute_target_scoped_operation=lambda *_args: result,
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )

    status, payload = product_server._execute_target_scoped_release_action(
        _post_body(request, proof)
    )

    assert status == 409
    assert payload["reconciliation_required"] is True
    assert payload["external_writes_performed"] == ["ozon:stock:update"]
    operation = store.get_target_scoped_operation(
        run_id=request.run_id,
        target_label=request.target_label,
    )
    assert operation["status"] == "RECONCILIATION_REQUIRED"
    assert operation["result"]["external_writes_performed"] == [
        "ozon:stock:update"
    ]
    store.record_target_scoped_success = original_success


def test_double_receipt_failure_reports_write_and_prevents_redispatch(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path, "ozon:RU")
    request = _request(store, plan, "ozon:RU")
    proof = _proof(request)
    result = TargetScopedOperationResult.from_value(
        {
            "succeeded": True,
            "readback_verified": True,
            "detail": "stock update and readback succeeded",
            "external_reference": "5687436857",
            "submission_accepted": False,
            "evidence": {
                "verified": True,
                "external_writes_performed": ["ozon:stock:update"],
            },
        }
    )
    execute_calls = []
    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            proof.durable_payload()
        ),
        execute_target_scoped_operation=lambda *_args: (
            execute_calls.append("execute") or result
        ),
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )
    monkeypatch.setattr(
        store,
        "record_target_scoped_success",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("injected success receipt failure")
        ),
    )
    monkeypatch.setattr(
        store,
        "record_target_scoped_reconciliation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("injected reconciliation failure")
        ),
    )
    body = _post_body(request, proof)

    status, payload = product_server._execute_target_scoped_release_action(
        body
    )
    replay_status, replay = (
        product_server._execute_target_scoped_release_action(body)
    )

    assert status == 500
    assert payload["code"] == "target_scoped_durable_receipt_uncertain"
    assert payload["durable_state_uncertain"] is True
    assert payload["external_writes_performed"] == ["ozon:stock:update"]
    assert replay_status == 409
    assert replay["operation_status"] == "RUNNING"
    assert execute_calls == ["execute"]


def test_generic_publish_never_resets_or_dispatches_a_failed_target(
    monkeypatch,
):
    run = {
        "run_id": "run-1",
        "status": "FAILED",
        "targets": [
            {
                "target_label": "shopee:MY",
                "status": "FAILED",
                "storage_status": "FAILED",
            }
        ],
    }

    class StoreSpy:
        retry_calls = 0
        begin_calls = 0

        def get_run(self, _run_id):
            return run

        def retry_failed_targets(self, *_args, **_kwargs):
            self.retry_calls += 1
            raise AssertionError("generic retry must not be called")

        def begin_target(self, *_args, **_kwargs):
            self.begin_calls += 1
            raise AssertionError("FAILED target must not begin")

    store = StoreSpy()
    gate = {
        "dashboard": {},
        "payload": {"product_id": "1", "targets": ["shopee:MY"]},
        "run": run,
        "registry": {},
        "target_rows": [],
    }
    monkeypatch.setattr(
        release_store, "default_release_store", lambda: store
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda *_args, **_kwargs: (gate, None),
    )

    status, payload = product_server._publish_selected_release(
        {
            "confirm_publish": True,
            "plan_id": "plan",
            "confirmation_token": "token",
        }
    )

    assert status == 409
    assert payload["code"] == "target_scoped_action_required"
    assert store.retry_calls == 0
    assert store.begin_calls == 0


def test_generic_publish_still_executes_a_first_pending_target(monkeypatch):
    run = {
        "run_id": "run-first",
        "status": "PENDING",
        "targets": [
            {
                "target_label": "shopee:MY",
                "status": "PENDING",
                "storage_status": "PENDING",
                "idempotency_key": "publish:shopee:MY:first",
            }
        ],
    }
    calls = []

    class StoreSpy:
        def get_run(self, _run_id):
            return run

        def begin_target(self, _run_id, label):
            calls.append(("begin", label))
            run["targets"][0]["status"] = "RUNNING"

        def record_target_success(
            self,
            _run_id,
            label,
            *,
            external_id,
            readback_evidence,
        ):
            calls.append(("success", label, external_id))
            run["targets"][0].update(
                {
                    "status": "SUCCEEDED",
                    "storage_status": "SUCCEEDED",
                    "external_id": external_id,
                    "readback": {"evidence": readback_evidence},
                }
            )
            run["status"] = "SUCCEEDED"

    store = StoreSpy()
    registration = AdapterRegistration(
        adapter_name="shopee-adapter",
        execute=lambda request: (
            calls.append(("execute", request.target_label))
            or AdapterExecutionResult(
                True,
                True,
                "first target succeeded",
                "item-0954",
                {"verified": True, "external_writes_performed": ["write"]},
            )
        ),
        consumes_unified_plan=True,
        validates_confirmation_token=True,
        preserves_idempotency_key=True,
        verifies_readback=True,
    )
    gate = {
        "dashboard": {},
        "payload": {
            "plan_id": "plan",
            "product_id": "3838616043",
            "seller_sku": "0954",
            "product_package_id": "product",
            "content_package_id": "content",
            "targets": ["shopee:MY"],
            "omnichannel_scope_digest": "scope",
        },
        "run": run,
        "registry": {"shopee-adapter": registration},
        "target_rows": [
            {
                "channel": "shopee",
                "site": "MY",
                "adapter": "shopee-adapter",
            }
        ],
    }
    monkeypatch.setattr(
        release_store, "default_release_store", lambda: store
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda *_args, **_kwargs: (gate, None),
    )

    status, payload = product_server._publish_selected_release(
        {
            "confirm_publish": True,
            "plan_id": "plan",
            "confirmation_token": "token",
        }
    )

    assert status == 200
    assert payload["completed"] is True
    assert calls == [
        ("begin", "shopee:MY"),
        ("execute", "shopee:MY"),
        ("success", "shopee:MY", "item-0954"),
    ]


@pytest.fixture
def target_scoped_http_server():
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


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    content_type: str = "application/json",
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_routes_are_exact_single_target_and_json_only(
    target_scoped_http_server,
    monkeypatch,
):
    calls = []

    def preview(*, offer_id, target_label):
        calls.append(("preview", offer_id, target_label))
        return 200, {
            "ok": True,
            "preview": True,
            "available": True,
            "target_label": target_label,
            "external_writes_performed": [],
        }

    def execute(data):
        calls.append(("execute", dict(data)))
        return 409, {
            "ok": False,
            "code": "target_scoped_reconciliation_required",
            "target_label": data["target_label"],
            "external_writes_performed": ["shopee:regional_publish"],
        }

    monkeypatch.setattr(
        product_server,
        "_preview_target_scoped_release_action",
        preview,
    )
    monkeypatch.setattr(
        product_server,
        "_execute_target_scoped_release_action",
        execute,
    )
    query = urllib.parse.urlencode(
        {"offer_id": "3838616043", "target_label": "shopee:MY"}
    )
    status, preview_payload = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/"
        + "target-scoped-action-preview?"
        + query
    )
    post_body = {
        "target_label": "shopee:MY",
        "confirm_target_scoped_action": True,
    }
    post_status, post_payload = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/target-scoped-action",
        method="POST",
        payload=post_body,
    )
    media_status, media_payload = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/target-scoped-action",
        method="POST",
        payload=post_body,
        content_type="text/plain",
    )

    assert status == 200
    assert preview_payload["external_writes_performed"] == []
    assert post_status == 409
    assert post_payload["code"] == "target_scoped_reconciliation_required"
    assert media_status == 415
    assert "application/json" in media_payload["error"]
    assert calls == [
        ("preview", "3838616043", "shopee:MY"),
        ("execute", post_body),
    ]
