import json
from http.server import ThreadingHTTPServer
from threading import Thread
import urllib.error
import urllib.request

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


def test_regional_price_contract_separates_listing_gate_from_sip_observation():
    expectation = release_adapters._shopee_price_expectation(
        _payload("PH")["pricing"]["selected_targets"]["shopee:PH"],
        region="PH",
    )
    row = {
        "scope": "model",
        "model_id": "90001",
        "currency": "PHP",
        "current_price": 414,
        "original_price": 414,
        "sip_item_price": 35.28,
    }

    verified, issues = release_adapters._verify_shopee_price_rows(
        [row],
        expectation,
        initial_issues=[],
    )
    observation, warning = (
        release_adapters._shopee_platform_derived_price_observation(
            row,
            expectation,
        )
    )

    assert verified is True
    assert issues == []
    assert observation["writable"] is False
    assert observation["authority"] == "shopee"
    assert observation["observed"] == "35.28"
    assert observation["reference"] == "48.85"
    assert observation["delta"] == "-13.57"
    assert observation["pct"] == "-27.78"
    assert observation["source"] == "official_shopee_partner_api"
    assert observation["observed_at"]
    assert len(observation["evidence_digest"]) == 64
    assert warning["code"] == "shopee_sip_platform_derived_variance"

    verified, issues = release_adapters._verify_shopee_price_rows(
        [{**row, "current_price": 413}],
        expectation,
        initial_issues=[],
    )
    assert verified is False
    assert "current_price_does_not_match_approved_local_price" in issues


def test_price_reconciliation_adapter_is_get_only_and_keeps_sip_warning(
    monkeypatch,
):
    from modules.shopee import client

    calls = []
    monkeypatch.setattr(
        client,
        "shop_post",
        lambda *_args, **_kwargs: pytest.fail(
            "GET-only reconciliation must never call shop_post"
        ),
    )
    context = _context(status="RECONCILIATION_REQUIRED")
    context["target"]["repair"] = {"status": "RECONCILIATION_REQUIRED"}
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **kwargs: calls.append(kwargs)
        or (True, _evidence(local=414, sip=35.28)),
    )
    operation = {
        "kind": "shopee_original_price_repair_v1",
        "plan_id": "omnichannel:test",
        "run_id": "release-run:test",
        "target_label": "shopee:PH",
        "external_id": "56164935203",
        "model_id": "90001",
        "seller_sku": "0954",
        "expected_local_price": "414.0",
        "currency": "PHP",
        "expected_sip_cny": "48.85",
    }

    result = release_adapters.reconcile_shopee_price_repair(
        _request(),
        operation=operation,
    )

    assert result.succeeded is True
    assert result.readback_evidence["external_writes_performed"] == []
    assert result.readback_evidence["listing_price_verified"] is True
    assert result.readback_evidence["derived_price_status"] == "warning"
    assert result.readback_evidence["profit_status"] == "unverified"
    assert calls[0]["allow_token_refresh"] is False
    assert calls[0]["require_model_sku"] is True
    assert calls[0]["require_all_logistics"] is True


@pytest.mark.parametrize(
    ("region", "final_sip"),
    [("PH", 35.28), ("TH", 44.10)],
)
def test_price_repair_posts_once_and_accepts_platform_derived_sip_variance(
    monkeypatch,
    region,
    final_sip,
):
    from modules.shopee import client

    expected_local = 414 if region == "PH" else 265
    expected_item = 56164935203 if region == "PH" else 51564925929
    states = iter(
        [
            _evidence(region),
            _evidence(region),
            _evidence(region, local=expected_local, sip=final_sip),
        ]
    )
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(
            region,
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
    preview = release_adapters.preflight_shopee_price_repair(
        _request(region)
    )
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(region, status="RUNNING"),
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
        _request(region),
        expected_preflight_digest=preview["operation"]["preflight_digest"],
    )

    assert result.succeeded is True
    assert result.readback_evidence["local_price_exact"] is True
    assert result.readback_evidence["sip_cny_exact"] is False
    assert result.readback_evidence["write_status"] == "verified"
    assert result.readback_evidence["listing_price_verified"] is True
    assert result.readback_evidence["derived_price_status"] == "warning"
    assert result.readback_evidence["profit_status"] == "unverified"
    observation = result.readback_evidence[
        "platform_derived_observation"
    ]
    assert observation["writable"] is False
    assert observation["authority"] == "shopee"
    assert observation["observed"] == str(final_sip)
    assert observation["evidence_digest"]
    assert posts == [
        (
            "/api/v2/product/update_price",
            123,
            "existing-token",
            {
                "item_id": expected_item,
                "price_list": [
                    {
                        "model_id": 90001,
                        "original_price": float(expected_local),
                    }
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


def _real_sqlite_repair_contract(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(
        {
            "plan_id": "omnichannel:persisted-approval",
            "product_id": "3838616043",
            "seller_sku": "0954",
            "product_package_id": "product:3838616043:0954",
            "content_package_id": "content:3838616043",
            "targets": ["shopee:PH", "shopee:TH"],
            "product_revision": 31,
            "omnichannel_scope_digest": "scope",
        }
    )
    approval = store.approve_plan(
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
        error="official readback price mismatch",
        external_id="56164935203",
        failure_evidence={"price": False, "identity": True},
    )
    return store, plan, approval, run


def _reconciliation_sqlite_contract(tmp_path):
    store, plan, approval, run = _real_sqlite_repair_contract(tmp_path)
    operation = {
        "kind": "shopee_original_price_repair_v1",
        "plan_id": plan["plan_id"],
        "run_id": run["run_id"],
        "target_label": "shopee:PH",
        "external_id": "56164935203",
        "model_id": "90001",
        "seller_sku": "0954",
        "expected_local_price": "414.0",
        "currency": "PHP",
        "expected_sip_cny": "48.85",
        "observed_local_price_digest": "o" * 64,
        "preflight_digest": "p" * 64,
        "expected_revision": 31,
        "payload_digest": plan["payload_digest"],
    }
    claim = store.claim_failed_target_repair(
        plan_id=plan["plan_id"],
        run_id=run["run_id"],
        target_label="shopee:PH",
        external_id="56164935203",
        operation=operation,
    )
    store.record_target_repair_reconciliation(
        claim["operation_digest"],
        error="accepted but official readback did not converge",
        evidence={
            "verified": False,
            "reconciliation_required": True,
            "durable_state_uncertain": False,
            "dispatch_outcome": "accepted_readback_unknown",
            "local_price_exact": True,
            "sip_cny_exact": False,
            "external_writes_performed": ["shopee:update_price"],
        },
    )
    return store, plan, approval, run, operation, claim["operation_digest"]


def _reconciliation_readback(*, exact=True):
    checks = {
        "seller_sku": True,
        "model_sku": True,
        "localized_title": True,
        "rich_localized_description": True,
        "price": exact,
        "image_count": True,
        "all_applicable_logistics": True,
        "status": True,
    }
    return AdapterExecutionResult(
        succeeded=exact,
        readback_verified=exact,
        detail="GET-only fixture",
        external_reference="56164935203",
        readback_evidence={
            "verified": exact,
            "reconciliation_required": not exact,
            "write_status": "verified" if exact else "unverified",
            "listing_price_verified": exact,
            "derived_price_status": "warning",
            "profit_status": "unverified",
            "financial_verification_status": (
                "price_verified_profit_unverified"
                if exact
                else "price_unverified_profit_unverified"
            ),
            "source": "official_shopee_partner_api",
            "checks": checks,
            "local_price_exact": exact,
            "sip_cny_exact": False,
            "platform_derived_observation": {
                "kind": "platform_derived_observation",
                "writable": False,
                "authority": "shopee",
                "observed": "35.28",
                "reference": "48.85",
                "delta": "-13.57",
                "pct": "-27.78",
                "source": "official_shopee_partner_api",
                "observed_at": "2026-07-28T00:00:00+00:00",
                "evidence_digest": "e" * 64,
            },
            "variance_warning": {
                "code": "shopee_sip_platform_derived_variance",
                "writable": False,
                "authority": "shopee",
            },
            "external_writes_performed": [],
        },
    )


def _post_json(url, payload):
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


def test_persisted_sqlite_approval_reaches_http_claim_and_adapter_seam(
    tmp_path,
    monkeypatch,
):
    store, plan, approval, run = _real_sqlite_repair_contract(tmp_path)
    durable_plan = store.get_plan(plan["plan_id"])
    durable_run = store.get_run(run["run_id"])
    adapter_calls = []

    assert approval["user_approved"] is True
    assert durable_plan["approval"]["user_approved"] is True
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
            "operation": {
                "kind": "shopee_original_price_repair_v1",
                "plan_id": plan["plan_id"],
                "run_id": run["run_id"],
                "target_label": "shopee:PH",
                "external_id": "56164935203",
                "preflight_digest": "p" * 64,
            }
        },
    )

    def execute(_request, *, expected_preflight_digest):
        adapter_calls.append(expected_preflight_digest)
        return AdapterExecutionResult(
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

    monkeypatch.setattr(
        release_adapters,
        "execute_shopee_price_repair",
        execute,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, response = _post_json(
            (
                f"http://127.0.0.1:{server.server_port}"
                "/api/product-workspace/release-target/shopee-price-repair"
            ),
            {
                "offer_id": "3838616043",
                "seller_sku": "0954",
                "publication_targets": ["shopee:PH", "shopee:TH"],
                "plan_id": plan["plan_id"],
                "confirmation_token": plan["confirmation_token"],
                "expected_revision": 31,
                "payload_digest": plan["payload_digest"],
                "preflight_digest": "p" * 64,
                "target_label": "shopee:PH",
                "confirm_shopee_price_repair": True,
                "approved_by": "Kyle",
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert response["ok"] is True
    assert adapter_calls == ["p" * 64]
    repaired = store.get_run(run["run_id"])
    target = next(
        row
        for row in repaired["targets"]
        if row["target_label"] == "shopee:PH"
    )
    assert target["repair"]["status"] == "SUCCEEDED"


@pytest.mark.parametrize(
    "user_approved",
    [False, None, 2, "1", "true"],
)
def test_price_repair_rejects_nonliteral_approval_values_before_claim_or_adapter(
    monkeypatch,
    user_approved,
):
    store = _ServerRepairStore()
    adapter_calls = []
    gate = _server_repair_gate()
    gate["plan"]["approval"]["user_approved"] = user_approved
    monkeypatch.setattr(
        release_store,
        "default_release_store",
        lambda: store,
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (gate, None),
    )
    monkeypatch.setattr(
        release_adapters,
        "preflight_shopee_price_repair",
        lambda *_args, **_kwargs: adapter_calls.append("preflight"),
    )
    monkeypatch.setattr(
        release_adapters,
        "execute_shopee_price_repair",
        lambda *_args, **_kwargs: adapter_calls.append("post"),
    )

    status, response = product_server._repair_existing_shopee_target_price(
        _server_repair_data()
    )

    assert status == 409
    assert response["external_writes_performed"] == []
    assert store.claims == []
    assert adapter_calls == []


def test_partial_target_list_does_not_match_full_immutable_payload(tmp_path):
    store, plan, _approval, _run = _real_sqlite_repair_contract(tmp_path)
    persisted = store.get_plan(plan["plan_id"])
    partial_preview = store.preview_plan(
        {
            **persisted["payload"],
            "targets": ["shopee:PH"],
        }
    )

    assert persisted["targets"] == ["shopee:PH", "shopee:TH"]
    assert partial_preview["targets"] == ["shopee:PH"]
    assert (
        product_server._approved_plan_matches_current_payload(
            persisted,
            partial_preview,
        )
        is False
    )


def test_reconciliation_get_only_closes_truthful_prior_write_and_is_idempotent(
    tmp_path,
    monkeypatch,
):
    (
        store,
        plan,
        _approval,
        run,
        operation,
        operation_digest,
    ) = _reconciliation_sqlite_contract(tmp_path)
    adapter_calls = []
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)

    def gate(_data, *, store):
        return (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": store.get_plan(plan["plan_id"]),
                "run": store.get_run(run["run_id"]),
                "payload": store.get_plan(plan["plan_id"])["payload"],
            },
            None,
        )

    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        gate,
    )
    monkeypatch.setattr(
        release_adapters,
        "reconcile_shopee_price_repair",
        lambda _request, *, operation: adapter_calls.append(operation)
        or _reconciliation_readback(),
    )
    data = {
        "offer_id": "3838616043",
        "seller_sku": "0954",
        "publication_targets": ["shopee:PH", "shopee:TH"],
        "plan_id": plan["plan_id"],
        "confirmation_token": plan["confirmation_token"],
        "expected_revision": 31,
        "payload_digest": plan["payload_digest"],
        "preflight_digest": operation["preflight_digest"],
        "operation_digest": operation_digest,
        "target_label": "shopee:PH",
        "confirm_shopee_price_reconciliation": True,
        "approved_by": "Kyle",
    }

    status, response = (
        product_server._reconcile_existing_shopee_price_repair(data)
    )

    assert status == 200
    assert response["external_writes_performed"] == []
    assert response["listing_price_verified"] is True
    assert response["derived_price_status"] == "warning"
    assert response["profit_status"] == "unverified"
    assert len(adapter_calls) == 1
    completed = store.get_run(run["run_id"])
    ph = next(
        row
        for row in completed["targets"]
        if row["target_label"] == "shopee:PH"
    )
    assert ph["status"] == "SUCCEEDED"
    assert ph["repair"]["status"] == "SUCCEEDED"
    assert ph["repair"]["result"]["external_writes_performed"] == [
        "shopee:update_price"
    ]
    assert ph["repair"]["result"][
        "reconciliation_external_writes_performed"
    ] == []
    assert ph["repair"]["result"]["derived_price_status"] == "warning"

    status, response = (
        product_server._reconcile_existing_shopee_price_repair(data)
    )
    assert status == 200
    assert response["idempotent"] is True
    assert response["external_writes_performed"] == []
    assert len(adapter_calls) == 1


def test_reconciliation_preview_is_readonly_and_returns_exact_durable_identity(
    tmp_path,
    monkeypatch,
):
    (
        store,
        plan,
        _approval,
        run,
        operation,
        operation_digest,
    ) = _reconciliation_sqlite_contract(tmp_path)
    before = store.path.read_bytes()
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": store.get_plan(plan["plan_id"]),
                "run": store.get_run(run["run_id"]),
                "payload": store.get_plan(plan["plan_id"])["payload"],
            },
            None,
        ),
    )

    status, response = (
        product_server._preview_shopee_price_reconciliation(
            offer_id="3838616043",
            target_label="shopee:PH",
        )
    )

    assert status == 200
    assert response["reconciliation_allowed"] is True
    assert response["mode"] == "official_get_only_durable_close"
    assert response["preflight_digest"] == operation["preflight_digest"]
    assert response["operation_digest"] == operation_digest
    assert response["external_writes_performed"] == []
    assert response["state_mutations_performed"] == []
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confirmation_token", "wrong"),
        ("expected_revision", 30),
        ("payload_digest", "wrong"),
        ("preflight_digest", "wrong"),
        ("operation_digest", "wrong"),
        ("approved_by", "not-Kyle"),
    ],
)
def test_reconciliation_identity_drift_fails_before_get_or_local_mutation(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    (
        store,
        plan,
        _approval,
        run,
        operation,
        operation_digest,
    ) = _reconciliation_sqlite_contract(tmp_path)
    before = store.path.read_bytes()
    calls = []
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": store.get_plan(plan["plan_id"]),
                "run": store.get_run(run["run_id"]),
                "payload": store.get_plan(plan["plan_id"])["payload"],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "reconcile_shopee_price_repair",
        lambda *_args, **_kwargs: calls.append("get"),
    )
    data = {
        "offer_id": "3838616043",
        "seller_sku": "0954",
        "publication_targets": ["shopee:PH", "shopee:TH"],
        "plan_id": plan["plan_id"],
        "confirmation_token": plan["confirmation_token"],
        "expected_revision": 31,
        "payload_digest": plan["payload_digest"],
        "preflight_digest": operation["preflight_digest"],
        "operation_digest": operation_digest,
        "target_label": "shopee:PH",
        "confirm_shopee_price_reconciliation": True,
        "approved_by": "Kyle",
        field: value,
    }

    status, response = (
        product_server._reconcile_existing_shopee_price_repair(data)
    )

    assert status in {400, 409}
    assert response["external_writes_performed"] == []
    assert calls == []
    assert store.path.read_bytes() == before


def test_reconciliation_local_mismatch_keeps_durable_state_unchanged(
    tmp_path,
    monkeypatch,
):
    (
        store,
        plan,
        _approval,
        run,
        operation,
        operation_digest,
    ) = _reconciliation_sqlite_contract(tmp_path)
    before = store.path.read_bytes()
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": store.get_plan(plan["plan_id"]),
                "run": store.get_run(run["run_id"]),
                "payload": store.get_plan(plan["plan_id"])["payload"],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "reconcile_shopee_price_repair",
        lambda *_args, **_kwargs: _reconciliation_readback(exact=False),
    )

    status, response = (
        product_server._reconcile_existing_shopee_price_repair(
            {
                "offer_id": "3838616043",
                "seller_sku": "0954",
                "publication_targets": ["shopee:PH", "shopee:TH"],
                "plan_id": plan["plan_id"],
                "confirmation_token": plan["confirmation_token"],
                "expected_revision": 31,
                "payload_digest": plan["payload_digest"],
                "preflight_digest": operation["preflight_digest"],
                "operation_digest": operation_digest,
                "target_label": "shopee:PH",
                "confirm_shopee_price_reconciliation": True,
                "approved_by": "Kyle",
            }
        )
    )

    assert status == 409
    assert response["external_writes_performed"] == []
    assert response["state_mutations_performed"] == []
    assert store.path.read_bytes() == before


def test_reconciliation_adapter_error_is_redacted_and_does_not_mutate(
    tmp_path,
    monkeypatch,
):
    (
        store,
        plan,
        _approval,
        run,
        operation,
        operation_digest,
    ) = _reconciliation_sqlite_contract(tmp_path)
    before = store.path.read_bytes()
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": store.get_plan(plan["plan_id"]),
                "run": store.get_run(run["run_id"]),
                "payload": store.get_plan(plan["plan_id"])["payload"],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "reconcile_shopee_price_repair",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("SECRET token/item/raw-response")
        ),
    )

    status, response = (
        product_server._reconcile_existing_shopee_price_repair(
            {
                "offer_id": "3838616043",
                "seller_sku": "0954",
                "publication_targets": ["shopee:PH", "shopee:TH"],
                "plan_id": plan["plan_id"],
                "confirmation_token": plan["confirmation_token"],
                "expected_revision": 31,
                "payload_digest": plan["payload_digest"],
                "preflight_digest": operation["preflight_digest"],
                "operation_digest": operation_digest,
                "target_label": "shopee:PH",
                "confirm_shopee_price_reconciliation": True,
                "approved_by": "Kyle",
            }
        )
    )

    assert status == 409
    assert "TimeoutError" in response["error"]
    assert "SECRET" not in str(response)
    assert response["external_writes_performed"] == []
    assert response["state_mutations_performed"] == []
    assert store.path.read_bytes() == before


def test_reconciliation_durable_close_failure_is_redacted_and_retryable(
    tmp_path,
    monkeypatch,
):
    (
        store,
        plan,
        _approval,
        run,
        operation,
        operation_digest,
    ) = _reconciliation_sqlite_contract(tmp_path)
    before = store.path.read_bytes()
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": store.get_plan(plan["plan_id"]),
                "run": store.get_run(run["run_id"]),
                "payload": store.get_plan(plan["plan_id"])["payload"],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "reconcile_shopee_price_repair",
        lambda *_args, **_kwargs: _reconciliation_readback(),
    )
    monkeypatch.setattr(
        store,
        "record_target_repair_reconciled_success",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("SECRET sqlite path")
        ),
    )

    status, response = (
        product_server._reconcile_existing_shopee_price_repair(
            {
                "offer_id": "3838616043",
                "seller_sku": "0954",
                "publication_targets": ["shopee:PH", "shopee:TH"],
                "plan_id": plan["plan_id"],
                "confirmation_token": plan["confirmation_token"],
                "expected_revision": 31,
                "payload_digest": plan["payload_digest"],
                "preflight_digest": operation["preflight_digest"],
                "operation_digest": operation_digest,
                "target_label": "shopee:PH",
                "confirm_shopee_price_reconciliation": True,
                "approved_by": "Kyle",
            }
        )
    )

    assert status == 502
    assert "OSError" in response["error"]
    assert "SECRET" not in str(response)
    assert response["external_writes_performed"] == []
    assert response["state_mutations_performed"] == []
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("user_approved", [False, 2, "1", "true", None])
def test_reconciliation_fake_persisted_approval_fails_before_get(
    tmp_path,
    monkeypatch,
    user_approved,
):
    (
        store,
        plan,
        _approval,
        run,
        operation,
        operation_digest,
    ) = _reconciliation_sqlite_contract(tmp_path)
    calls = []
    durable_plan = store.get_plan(plan["plan_id"])
    durable_plan["approval"]["user_approved"] = user_approved
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {"product": {"revision": 31}},
                "plan": durable_plan,
                "run": store.get_run(run["run_id"]),
                "payload": durable_plan["payload"],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "reconcile_shopee_price_repair",
        lambda *_args, **_kwargs: calls.append("get"),
    )

    status, response = (
        product_server._reconcile_existing_shopee_price_repair(
            {
                "offer_id": "3838616043",
                "seller_sku": "0954",
                "publication_targets": ["shopee:PH", "shopee:TH"],
                "plan_id": plan["plan_id"],
                "confirmation_token": plan["confirmation_token"],
                "expected_revision": 31,
                "payload_digest": plan["payload_digest"],
                "preflight_digest": operation["preflight_digest"],
                "operation_digest": operation_digest,
                "target_label": "shopee:PH",
                "confirm_shopee_price_reconciliation": True,
                "approved_by": "Kyle",
            }
        )
    )

    assert status == 409
    assert response["external_writes_performed"] == []
    assert calls == []


def test_price_reconciliation_http_route_is_dedicated_post(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        product_server,
        "_reconcile_existing_shopee_price_repair",
        lambda data: calls.append(data) or (
            200,
            {
                "ok": True,
                "external_writes_performed": [],
                "state_mutations_performed": [
                    "release_target_repair:SUCCEEDED"
                ],
            },
        ),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = {
        "confirm_shopee_price_reconciliation": True,
        "approved_by": "Kyle",
    }
    try:
        status, response = _post_json(
            (
                f"http://127.0.0.1:{server.server_port}"
                "/api/product-workspace/release-target/"
                "shopee-price-reconciliation"
            ),
            body,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert response["external_writes_performed"] == []
    assert calls == [body]


def test_price_reconciliation_rejects_generic_confirmation_before_store(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        release_store,
        "default_release_store",
        lambda: calls.append("store"),
    )

    status, response = (
        product_server._reconcile_existing_shopee_price_repair(
            {
                "confirm": True,
                "approved_by": "Kyle",
            }
        )
    )

    assert status == 400
    assert "confirm_shopee_price_reconciliation=true" in response["error"]
    assert response["external_writes_performed"] == []
    assert calls == []


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
