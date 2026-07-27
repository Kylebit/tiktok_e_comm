import pytest

from domains.channel_operations.release_executor import AdapterExecutionRequest
from domains.channel_operations.release_executor import AdapterExecutionResult
from modules.products import release_adapters
from modules.products import server as product_server
from shared_platform import release_store
from shared_platform.release_store import ReleaseStore


def _request(region="PH"):
    return AdapterExecutionRequest(
        plan_id="omnichannel:test",
        confirmation_token="PUBLISH-TEST",
        approval_scope_digest="scope",
        product_id="3838616043",
        seller_sku="0954",
        product_package_id="product:3838616043:0954",
        content_package_id="content:3838616043",
        channel="shopee",
        site=region,
        target_label=f"shopee:{region}",
        idempotency_key=f"publish:shopee:{region}:test",
    )


def _payload(region="PH"):
    local, cny, rate, currency = (
        (414, 48.85, 0.118, "PHP")
        if region == "PH"
        else (265, 58.78, 0.2218, "THB")
    )
    return {
        "seller_sku": "0954",
        "product_facts": {
            "title": "Approved master",
            "package_cm": [40, 3, 3],
        },
        "listing_copy": {
            "shopee_description_en": "Approved description. " * 30,
            "candidates": [
                {
                    "channel": "shopee",
                    "site": "CNSC",
                    "title": "Approved Shopee title",
                    "policy_check": "passed",
                }
            ],
        },
        "pricing": {
            "selected_targets": {
                f"shopee:{region}": {
                    "target_site": region,
                    "derived_preview": {
                        "global_original_price_cny": cny,
                        "local_original_price": local,
                        "source_currency": currency,
                        "exchange_rate_cny_per_local": rate,
                    },
                }
            }
        },
    }


def _context(region="PH", status="FAILED"):
    return {
        "payload": _payload(region),
        "images": ["https://img/1.jpg", "https://img/2.jpg"],
        "target": {
            "status": status,
            "external_id": "56164935203" if region == "PH" else "51564925929",
        },
        "run": {"run_id": "release-run:test"},
    }


def _evidence(
    region="PH",
    *,
    local=None,
    sip=None,
    nonprice_failure=None,
    price_check=True,
    price_issues=None,
):
    expected_local = 414 if region == "PH" else 265
    currency = "PHP" if region == "PH" else "THB"
    cny = 48.85 if region == "PH" else 58.78
    actual = expected_local + 10 if local is None else local
    checks = {
        "seller_sku": True,
        "model_sku": True,
        "localized_title": True,
        "rich_localized_description": True,
        "price": price_check,
        "image_count": True,
        "all_applicable_logistics": True,
        "status": True,
    }
    if nonprice_failure:
        checks[nonprice_failure] = False
    return {
        "verified": all(checks.values()),
        "checks": checks,
        "observed_price_fields": [
            {
                "scope": "model",
                "model_id": "90001",
                "currency": currency,
                "current_price": actual,
                "original_price": actual,
                "sip_item_price": cny if sip is None else sip,
            }
        ],
        "price_issues": list(price_issues or ()),
    }


def test_price_repair_preflight_requires_price_only_drift(monkeypatch):
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(),
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: (True, _evidence()),
    )

    preview = release_adapters.preflight_shopee_price_repair(_request())

    operation = preview["operation"]
    assert operation["external_id"] == "56164935203"
    assert operation["model_id"] == "90001"
    assert operation["expected_local_price"] == "414.0"
    assert operation["currency"] == "PHP"
    assert preview["evidence"]["external_writes_performed"] == []

    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: (
            False,
            _evidence(nonprice_failure="image_count"),
        ),
    )
    with pytest.raises(RuntimeError, match="non-price drift"):
        release_adapters.preflight_shopee_price_repair(_request())


@pytest.mark.parametrize(
    ("region", "local", "sip", "expected"),
    [
        ("PH", 868, 81.69, "414.0"),
        ("TH", 546, 75.05, "265.0"),
    ],
)
def test_price_repair_preflight_accepts_real_local_and_sip_double_drift(
    monkeypatch,
    region,
    local,
    sip,
    expected,
):
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(region),
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: (
            False,
            _evidence(
                region,
                local=local,
                sip=sip,
                price_check=False,
                price_issues=[
                    "sip_item_price_does_not_match_immutable_cny_price"
                ],
            ),
        ),
    )

    preview = release_adapters.preflight_shopee_price_repair(
        _request(region)
    )

    assert preview["operation"]["expected_local_price"] == expected
    assert preview["operation"]["currency"] == (
        "PHP" if region == "PH" else "THB"
    )


def test_price_repair_preflight_rejects_other_price_ambiguity(monkeypatch):
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(),
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: (
            False,
            _evidence(
                price_check=False,
                price_issues=[
                    "sip_item_price_does_not_match_immutable_cny_price",
                    "target_currency_price_row_is_not_unique",
                ],
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="ambiguous price semantics"):
        release_adapters.preflight_shopee_price_repair(_request())


def test_price_repair_posts_once_and_requires_exact_bounded_readback(monkeypatch):
    from modules.shopee import client

    states = iter([_evidence(), _evidence(), _evidence(local=414)])
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(
            status="FAILED"
            if not hasattr(_request, "_repair_running")
            else "RUNNING"
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: (True, next(states)),
    )
    preview = release_adapters.preflight_shopee_price_repair(_request())
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(status="RUNNING"),
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback_credentials",
        lambda *_args, **_kwargs: (123, "existing-token"),
    )
    posts = []
    monkeypatch.setattr(
        client,
        "shop_post",
        lambda path, shop_id, token, body: posts.append(
            (path, shop_id, token, body)
        )
        or {"error": ""},
    )

    result = release_adapters.execute_shopee_price_repair(
        _request(),
        expected_preflight_digest=preview["operation"]["preflight_digest"],
    )

    assert result.succeeded is True
    assert result.readback_evidence["local_price_exact"] is True
    assert result.readback_evidence["sip_cny_exact"] is True
    assert posts == [
        (
            "/api/v2/product/update_price",
            123,
            "existing-token",
            {
                "item_id": 56164935203,
                "price_list": [
                    {"model_id": 90001, "original_price": 414.0}
                ],
            },
        )
    ]


def test_price_repair_unknown_response_is_terminal_and_never_reposts(
    monkeypatch,
):
    from modules.shopee import client

    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(status="RUNNING"),
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: (True, _evidence()),
    )
    preflight = release_adapters._shopee_price_repair_preflight(
        _request(),
        allowed_statuses=frozenset({"RUNNING"}),
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback_credentials",
        lambda *_args, **_kwargs: (123, "existing-token"),
    )
    posts = []

    def timeout(*args):
        posts.append(args)
        raise TimeoutError("unknown")

    monkeypatch.setattr(client, "shop_post", timeout)

    with pytest.raises(
        release_adapters.ShopeePriceRepairReconciliationError
    ) as captured:
        release_adapters.execute_shopee_price_repair(
            _request(),
            expected_preflight_digest=preflight["operation"][
                "preflight_digest"
            ],
        )

    assert len(posts) == 1
    assert (
        captured.value.external_write_evidence["reconciliation_required"]
        is True
    )


@pytest.mark.parametrize("failure_attempt", [1, 2])
def test_price_repair_accepted_then_readback_error_is_truthful_and_one_post(
    monkeypatch,
    failure_attempt,
):
    from modules.shopee import client

    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(status="RUNNING"),
    )
    calls = {"readback": 0, "post": 0, "post_readback": 0}

    def readback(**_kwargs):
        calls["readback"] += 1
        if calls["post"]:
            calls["post_readback"] += 1
        # For failure_attempt=2, let one post-dispatch GET return a
        # non-converged row before the next GET raises.
        if calls["post_readback"] == failure_attempt:
            raise TimeoutError("sensitive transport detail")
        return True, _evidence()

    monkeypatch.setattr(release_adapters, "_shopee_readback", readback)
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback_credentials",
        lambda *_args, **_kwargs: (123, "existing-token"),
    )

    def accepted(*_args):
        calls["post"] += 1
        return {"error": ""}

    monkeypatch.setattr(client, "shop_post", accepted)
    preflight = release_adapters._shopee_price_repair_preflight(
        _request(),
        allowed_statuses=frozenset({"RUNNING"}),
    )

    with pytest.raises(
        release_adapters.ShopeePriceRepairReconciliationError
    ) as captured:
        release_adapters.execute_shopee_price_repair(
            _request(),
            expected_preflight_digest=preflight["operation"][
                "preflight_digest"
            ],
        )

    assert calls["post"] == 1
    evidence = captured.value.external_write_evidence
    assert evidence["reconciliation_required"] is True
    assert evidence["dispatch_outcome"] == "accepted_readback_unknown"
    assert evidence["error_type"] == "TimeoutError"
    assert evidence["external_writes_performed"] == ["shopee:update_price"]
    assert "sensitive transport detail" not in str(evidence)


class _ServerRepairStore:
    def __init__(self):
        self.claims = []
        self.successes = []

    def claim_failed_target_repair(self, **kwargs):
        self.claims.append(kwargs)
        return {"action": "claimed", "operation_digest": "repair-digest"}

    def record_target_repair_success(self, digest, *, readback_evidence):
        self.successes.append((digest, readback_evidence))
        return {"run_id": "release-run:test", "status": "SUCCEEDED"}


def _server_repair_gate(*, repair=None, target_status="FAILED"):
    plan = {
        "plan_id": "omnichannel:test",
        "confirmation_token": "PUBLISH-TEST",
        "product_id": "3838616043",
        "seller_sku": "0954",
        "product_package_id": "product:3838616043:0954",
        "content_package_id": "content:3838616043",
        "payload_digest": "d" * 64,
        "payload": {
            "product_revision": 31,
            "omnichannel_scope_digest": "scope",
        },
        "approval": {
            "status": "APPROVED",
            "approved_by": "Kyle",
            "user_approved": True,
        },
    }
    run = {
        "run_id": "release-run:test",
        "status": "PARTIAL_FAILED",
        "targets": [
            {
                "target_label": "shopee:PH",
                "status": target_status,
                "attempts": 1,
                "external_id": "56164935203",
                "idempotency_key": "publish:shopee:PH:test",
                "repair": repair,
            }
        ],
    }
    return {
        "dashboard": {"product": {"revision": 31}},
        "plan": plan,
        "run": run,
        "payload": {"omnichannel_scope_digest": "scope"},
    }


def _server_repair_data(**overrides):
    data = {
        "offer_id": "3838616043",
        "seller_sku": "0954",
        "publication_targets": ["shopee:PH"],
        "plan_id": "omnichannel:test",
        "confirmation_token": "PUBLISH-TEST",
        "expected_revision": 31,
        "payload_digest": "d" * 64,
        "preflight_digest": "p" * 64,
        "target_label": "shopee:PH",
        "confirm_shopee_price_repair": True,
        "approved_by": "Kyle",
    }
    data.update(overrides)
    return data


def _install_server_repair_contract(monkeypatch, store, *, execute=None):
    monkeypatch.setattr(
        "shared_platform.release_store.default_release_store",
        lambda: store,
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (_server_repair_gate(), None),
    )
    monkeypatch.setattr(
        release_adapters,
        "preflight_shopee_price_repair",
        lambda _request: {
            "operation": {
                "kind": "shopee_original_price_repair_v1",
                "plan_id": "omnichannel:test",
                "run_id": "release-run:test",
                "target_label": "shopee:PH",
                "external_id": "56164935203",
                "preflight_digest": "p" * 64,
            }
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "execute_shopee_price_repair",
        execute
        or (
            lambda _request, *, expected_preflight_digest: AdapterExecutionResult(
                succeeded=True,
                readback_verified=True,
                detail="exact",
                external_reference="56164935203",
                readback_evidence={
                    "verified": True,
                    "reconciliation_required": False,
                    "external_writes_performed": ["shopee:update_price"],
                },
            )
        ),
    )


def test_server_price_repair_requires_exact_gate_and_records_one_success(
    monkeypatch,
):
    store = _ServerRepairStore()
    plan = {
        "plan_id": "omnichannel:test",
        "confirmation_token": "PUBLISH-TEST",
        "product_id": "3838616043",
        "seller_sku": "0954",
        "product_package_id": "product:3838616043:0954",
        "content_package_id": "content:3838616043",
        "payload_digest": "d" * 64,
        "payload": {
            "product_revision": 31,
            "omnichannel_scope_digest": "scope",
        },
        "approval": {
            "status": "APPROVED",
            "approved_by": "Kyle",
            "user_approved": True,
        },
    }
    run = {
        "run_id": "release-run:test",
        "targets": [
            {
                "target_label": "shopee:PH",
                "status": "FAILED",
                "external_id": "56164935203",
                "idempotency_key": "publish:shopee:PH:test",
                "repair": None,
            }
        ],
    }
    monkeypatch.setattr(
        "shared_platform.release_store.default_release_store",
        lambda: store,
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": plan,
                "run": run,
                "payload": {"omnichannel_scope_digest": "scope"},
            },
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "preflight_shopee_price_repair",
        lambda _request: {
            "operation": {
                "kind": "shopee_original_price_repair_v1",
                "plan_id": "omnichannel:test",
                "run_id": "release-run:test",
                "target_label": "shopee:PH",
                "external_id": "56164935203",
                "preflight_digest": "p" * 64,
            }
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "execute_shopee_price_repair",
        lambda _request, *, expected_preflight_digest: AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail="exact",
            external_reference="56164935203",
            readback_evidence={
                "verified": True,
                "reconciliation_required": False,
                "external_writes_performed": ["shopee:update_price"],
            },
        ),
    )
    data = {
        "offer_id": "3838616043",
        "seller_sku": "0954",
        "publication_targets": ["shopee:PH"],
        "plan_id": "omnichannel:test",
        "confirmation_token": "PUBLISH-TEST",
        "expected_revision": 31,
        "payload_digest": "d" * 64,
        "preflight_digest": "p" * 64,
        "target_label": "shopee:PH",
        "confirm_shopee_price_repair": True,
        "approved_by": "Kyle",
    }

    status, response = product_server._repair_existing_shopee_target_price(
        data
    )

    assert status == 200
    assert response["external_writes_performed"] == ["shopee:update_price"]
    assert len(store.claims) == 1
    assert store.claims[0]["operation"]["expected_revision"] == 31
    assert len(store.successes) == 1

    status, response = product_server._repair_existing_shopee_target_price(
        {**data, "expected_revision": 30}
    )
    assert status == 409
    assert response["external_writes_performed"] == []
    assert len(store.claims) == 1


def test_server_price_repair_rejects_generic_confirmation_without_mutation(
    monkeypatch,
):
    store_calls = []
    adapter_calls = []
    monkeypatch.setattr(
        "shared_platform.release_store.default_release_store",
        lambda: store_calls.append("store") or pytest.fail(
            "generic confirmation must not open the release store"
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "execute_shopee_price_repair",
        lambda *_args, **_kwargs: adapter_calls.append("post")
        or pytest.fail("generic confirmation must not call Shopee"),
    )

    status, response = product_server._repair_existing_shopee_target_price(
        {
            "confirm": True,
            "approved_by": "Kyle",
            "offer_id": "3838616043",
            "target_label": "shopee:PH",
            "plan_id": "omnichannel:test",
            "confirmation_token": "PUBLISH-TEST",
            "expected_revision": 31,
            "payload_digest": "d" * 64,
            "preflight_digest": "p" * 64,
            "publication_targets": ["shopee:PH"],
        }
    )

    assert status == 400
    assert "confirm_shopee_price_repair=true" in response["error"]
    assert response["external_writes_performed"] == []
    assert store_calls == []
    assert adapter_calls == []


class _FaultingRepairStore(_ServerRepairStore):
    def __init__(self, *, fail_reconciliation=False):
        super().__init__()
        self.fail_reconciliation = fail_reconciliation
        self.reconciliations = []
        self.latest = {
            "run_id": "secret-run",
            "status": "RUNNING",
            "targets": [
                {
                    "target_label": "shopee:PH",
                    "status": "RUNNING",
                    "attempts": 2,
                    "external_id": "secret-item-id",
                    "seller_sku": "secret-sku",
                    "repair": {
                        "status": "RUNNING",
                        "operation_digest": "secret-operation",
                        "result": {"model_id": "secret-model"},
                    },
                }
            ],
        }

    def record_target_repair_success(self, digest, *, readback_evidence):
        raise OSError("durable success write failed")

    def record_target_repair_reconciliation(
        self,
        digest,
        *,
        error,
        evidence,
    ):
        self.reconciliations.append((digest, error, evidence))
        if self.fail_reconciliation:
            raise OSError("durable reconciliation write failed")
        self.latest["status"] = "PARTIAL_FAILED"
        self.latest["targets"][0]["status"] = "RECONCILIATION_REQUIRED"
        self.latest["targets"][0]["repair"]["status"] = (
            "RECONCILIATION_REQUIRED"
        )
        return self.latest

    def get_run(self, _run_id):
        return self.latest


@pytest.mark.parametrize(
    ("fail_reconciliation", "expected_status"),
    [(False, 409), (True, 502)],
)
def test_success_receipt_failure_is_truthful_and_never_leaks_raw_run(
    monkeypatch,
    fail_reconciliation,
    expected_status,
):
    store = _FaultingRepairStore(
        fail_reconciliation=fail_reconciliation
    )
    _install_server_repair_contract(monkeypatch, store)

    status, response = product_server._repair_existing_shopee_target_price(
        _server_repair_data()
    )

    assert status == expected_status
    assert response["reconciliation_required"] is True
    assert response["durable_state_uncertain"] is True
    assert response["external_writes_performed"] == ["shopee:update_price"]
    assert response["repair_status"]["target"]["target_label"] == "shopee:PH"
    encoded = str(response)
    for secret in (
        "secret-run",
        "secret-item-id",
        "secret-sku",
        "secret-model",
        "secret-operation",
        "56164935203",
        "0954",
    ):
        assert secret not in encoded
    assert len(store.claims) == 1
    assert len(store.reconciliations) == 1


@pytest.mark.parametrize(
    ("fail_reconciliation", "expected_status"),
    [(False, 409), (True, 502)],
)
def test_accepted_readback_unknown_stays_truthful_through_durable_failure(
    monkeypatch,
    fail_reconciliation,
    expected_status,
):
    store = _FaultingRepairStore(
        fail_reconciliation=fail_reconciliation
    )
    adapter_calls = []

    def accepted_then_unknown(_request, *, expected_preflight_digest):
        adapter_calls.append(expected_preflight_digest)
        raise release_adapters.ShopeePriceRepairReconciliationError(
            "accepted but official readback unknown",
            external_reference="56164935203",
            evidence={
                "verified": False,
                "reconciliation_required": True,
                "dispatch_outcome": "accepted_readback_unknown",
                "error_type": "TimeoutError",
                "external_writes_performed": ["shopee:update_price"],
            },
        )

    _install_server_repair_contract(
        monkeypatch,
        store,
        execute=accepted_then_unknown,
    )

    status, response = product_server._repair_existing_shopee_target_price(
        _server_repair_data()
    )

    assert status == expected_status
    assert adapter_calls == ["p" * 64]
    assert response["reconciliation_required"] is True
    assert response["durable_state_uncertain"] is True
    assert response["external_writes_performed"] == ["shopee:update_price"]
    assert len(store.claims) == 1
    assert len(store.reconciliations) == 1


class _IdempotentRepairStore:
    def __init__(self):
        self.match_calls = []

    def target_repair_confirmation_matches(self, **kwargs):
        self.match_calls.append(kwargs)
        return {
            "matches": kwargs["preflight_digest"] == "p" * 64,
            "status": "SUCCEEDED",
            "operation_digest": "stored",
        }


def test_success_idempotency_requires_exact_stored_confirmation_identity(
    monkeypatch,
):
    store = _IdempotentRepairStore()
    adapter_calls = []
    monkeypatch.setattr(
        "shared_platform.release_store.default_release_store",
        lambda: store,
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            _server_repair_gate(
                repair={"status": "SUCCEEDED"},
                target_status="SUCCEEDED",
            ),
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "preflight_shopee_price_repair",
        lambda *_args, **_kwargs: adapter_calls.append("preflight")
        or pytest.fail("idempotent result must not call official GET"),
    )
    monkeypatch.setattr(
        release_adapters,
        "execute_shopee_price_repair",
        lambda *_args, **_kwargs: adapter_calls.append("post")
        or pytest.fail("idempotent result must not POST"),
    )

    status, response = product_server._repair_existing_shopee_target_price(
        _server_repair_data(preflight_digest="wrong")
    )
    assert status == 409
    assert response["external_writes_performed"] == []
    assert adapter_calls == []

    status, response = product_server._repair_existing_shopee_target_price(
        _server_repair_data()
    )
    assert status == 200
    assert response["idempotent"] is True
    assert response["external_writes_performed"] == []
    assert adapter_calls == []


class _TrackingLock:
    def __init__(self, name, events):
        self.name = name
        self.events = events

    def __enter__(self):
        self.events.append(f"{self.name}:enter")

    def __exit__(self, *_args):
        self.events.append(f"{self.name}:exit")


def test_price_repair_uses_release_then_product_lock_and_drift_posts_zero(
    monkeypatch,
):
    store = _ServerRepairStore()
    calls = []
    events = []
    monkeypatch.setattr(
        "shared_platform.release_store.default_release_store",
        lambda: store,
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_lock",
        _TrackingLock("release", events),
    )
    monkeypatch.setattr(
        product_server,
        "_product_workbench_lock",
        lambda offer_id: (
            events.append(f"product-key:{offer_id}")
            or _TrackingLock("product", events)
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                **_server_repair_gate(),
                "dashboard": {"product": {"revision": 32}},
            },
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "preflight_shopee_price_repair",
        lambda *_args, **_kwargs: calls.append("preflight"),
    )
    monkeypatch.setattr(
        release_adapters,
        "execute_shopee_price_repair",
        lambda *_args, **_kwargs: calls.append("post"),
    )

    status, response = product_server._repair_existing_shopee_target_price(
        _server_repair_data()
    )

    assert status == 409
    assert response["external_writes_performed"] == []
    assert calls == []
    assert store.claims == []
    assert events == [
        "release:enter",
        "product-key:3838616043",
        "product:enter",
        "product:exit",
        "release:exit",
    ]


def test_price_repair_preview_is_redacted_and_does_not_mutate_store(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(
        {
            "plan_id": "omnichannel:preview",
            "product_id": "3838616043",
            "seller_sku": "0954",
            "product_package_id": "product:3838616043:0954",
            "content_package_id": "content:3838616043",
            "targets": ["shopee:PH"],
            "product_revision": 31,
            "omnichannel_scope_digest": "scope-secret",
        }
    )
    store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], "shopee:PH")
    store.record_target_failure(
        run["run_id"],
        "shopee:PH",
        error="official price mismatch",
        external_id="56164935203",
        failure_evidence={"price": False},
    )
    durable_plan = store.get_plan(plan["plan_id"])
    durable_run = store.get_run(run["run_id"])
    before = store.path.read_bytes()
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": durable_plan,
                "run": durable_run,
                "payload": durable_plan["payload"],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "preflight_shopee_price_repair",
        lambda _request: {
            "operation": {"preflight_digest": "p" * 64}
        },
    )

    status, response = product_server._preview_existing_shopee_target_price(
        offer_id="3838616043",
        target_label="shopee:PH",
    )

    assert status == 200
    assert response == {
        "ok": True,
        "repair_allowed": True,
        "plan_id": "omnichannel:preview",
        "target_label": "shopee:PH",
        "expected_revision": 31,
        "payload_digest": plan["payload_digest"],
        "preflight_digest": "p" * 64,
        "external_writes_performed": [],
        "state_mutations_performed": [],
    }
    encoded = str(response)
    for sensitive in (
        plan["confirmation_token"],
        "0954",
        "56164935203",
        "scope-secret",
        "model_id",
        "price",
        "shop_id",
    ):
        assert sensitive not in encoded
    assert store.path.read_bytes() == before
    unchanged = store.get_run(run["run_id"])["targets"][0]
    assert unchanged["status"] == "FAILED"
    assert unchanged["attempts"] == 1
