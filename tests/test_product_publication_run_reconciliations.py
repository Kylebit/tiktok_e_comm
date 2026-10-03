from copy import deepcopy
import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from domains.product_operations import build_approved_publication_snapshot
from shared_platform.product_publication_run_reconciliations import (
    READBACK_SCHEMA_VERSION,
    ProductPublicationRunReconciliationError,
    ProductPublicationRunReconciliationStore,
)
from shared_platform.product_publication_runs import ProductPublicationRunStore
from shared_platform.publication_status_projection import (
    _strict_zero_dispatch_without_target_evidence,
    project_execution,
)
from test_approved_publication_snapshot import _approved_plan, _rebind


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def make_failed(tmp_path, run_id="run-zero-write", platform="SHOPEE", labels=None):
    labels = labels or ["shopee:PH", "shopee:MY"]
    store = ProductPublicationRunStore(tmp_path / "orbit.db")
    store.create_run(run_id=run_id, offer_id="3956742887", revision=4, plan_id="plan-1",
                     snapshot_digest="a" * 64, platform_scope=[platform], target_count=len(labels),
                     execution_identity={"skill_digest": "b" * 64, "git_commit": "c" * 40,
                                         "code_digest": "d" * 64})
    store.mark_failed(run_id=run_id, failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    return store, store.get_run_by_id(run_id=run_id), labels


def kwargs(run, labels):
    observed = (datetime.fromisoformat(run["updated_at"]) + timedelta(seconds=1)).isoformat()
    platform = run["platform_scope"][0]
    rb = {"schema_version": READBACK_SCHEMA_VERSION,
          "authority": "OZON_OFFICIAL_SELLER_API" if platform == "OZON" else "SHOPEE_OFFICIAL_SHOP_API",
          "observed_at": observed, "seller_sku": "0988",
          "targets": [{"target_label": label, "classification": "NOT_FOUND", "complete": True,
                       "scopes": ["ALL"] if platform == "OZON" else ["NORMAL", "UNLIST", "BANNED"],
                       "evidence_digest": "e" * 64}
                      for label in labels]}
    rb["evidence_digest"] = digest(rb)
    identity = {key: run[key] for key in ("run_id", "report_id", "offer_id", "revision", "plan_id",
                "snapshot_digest", "platform_scope", "target_count", "execution_identity")}
    return {"run_id": run["run_id"], "identity": identity, "ordered_target_labels": labels,
            "official_readback": rb, "evidence_digests": ["f" * 64], "registered_by": "incident-review"}


def valid_shopee_snapshot(labels=("shopee:PH", "shopee:MY")):
    plan = _approved_plan()
    payload = plan["payload"]
    payload["targets"] = [
        target for target in payload["targets"] if not target.startswith("shopee:")
    ]
    prices = payload["pricing"]["selected_targets"]
    categories = payload["product_facts"]["categories_by_target"]
    source_prices = deepcopy(prices.pop("shopee:PH"))
    source_category = deepcopy(categories.pop("shopee:PH"))
    currencies = {"PH": "PHP", "MY": "MYR", "TH": "THB", "VN": "VND"}
    for label in labels:
        site = label.split(":", 1)[1]
        payload["targets"].append(label)
        prices[label] = deepcopy(source_prices)
        prices[label]["source"] = {"region": site, "target_key": site.lower()}
        for row in prices[label]["sku_prices"]:
            row["currency"] = currencies[site]
        categories[label] = deepcopy(source_category)
        categories[label].update(
            target_label=label, platform="shopee", site=site, store=site
        )
    _rebind(plan)
    return build_approved_publication_snapshot(plan).payload()


class SnapshotStore:
    def __init__(self, snapshot):
        self.snapshot = deepcopy(snapshot)

    def approved_publication_snapshot(self, **kwargs):
        if (
            kwargs.get("offer_id") == self.snapshot["offer_id"]
            and kwargs.get("snapshot_digest") == self.snapshot["snapshot_digest"]
        ):
            return deepcopy(self.snapshot)
        return None


def test_register_is_append_only_and_digest_verified(tmp_path):
    run_store, run, labels = make_failed(tmp_path)
    store = ProductPublicationRunReconciliationStore(run_store.path)
    receipt = store.register(**kwargs(run, labels))
    assert receipt["provider_request_attempted"] is False
    assert receipt["external_write_count"] == 0
    assert receipt["failure_boundary"] == "PRE_RUNNING"
    assert store.register(**kwargs(run, labels)) == receipt
    changed = kwargs(run, labels); changed["registered_by"] = "someone-else"
    with pytest.raises(ProductPublicationRunReconciliationError, match="conflicts"):
        store.register(**changed)


def test_rejects_running_history_and_incomplete_readback(tmp_path):
    run_store, run, labels = make_failed(tmp_path)
    data = kwargs(run, labels); data["official_readback"]["targets"][0]["complete"] = False
    body = dict(data["official_readback"]); body.pop("evidence_digest")
    data["official_readback"]["evidence_digest"] = digest(body)
    with pytest.raises(ValueError, match="complete absence"):
        ProductPublicationRunReconciliationStore(run_store.path).register(**data)
    other = ProductPublicationRunStore(tmp_path / "running.db")
    other.create_run(run_id="ran", offer_id="3956742887", revision=4, plan_id="plan-1",
                     snapshot_digest="a"*64, platform_scope=["SHOPEE"], target_count=2)
    other.mark_running(run_id="ran"); other.mark_failed(run_id="ran", failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    ran = other.get_run_by_id(run_id="ran")
    with pytest.raises(ValueError, match="directly from QUEUED"):
        ProductPublicationRunReconciliationStore(other.path).register(**kwargs(ran, labels))


class Reports:
    def list_report_refs_for_plan(self, **_): return []
    def get_report_by_run(self, **_): return None


def test_projection_marks_failed_reconciled_and_does_not_poison_newer_run(tmp_path):
    run_store, run, labels = make_failed(tmp_path)
    ProductPublicationRunReconciliationStore(run_store.path).register(**kwargs(run, labels))
    run_store.create_run(run_id="new-run", offer_id="3956742887", revision=4, plan_id="plan-1",
                         snapshot_digest="a"*64, platform_scope=["SHOPEE"], target_count=2)
    plan = {"product_id": "3956742887", "plan_id": "plan-1", "targets": labels,
            "approval": {"approved_at": "2020-01-01T00:00:00+00:00"}}
    projected = project_execution(plan=plan,
        snapshot={"product_revision": 4, "snapshot_digest": "a"*64}, candidate={}, approval={},
        run_store=run_store, report_store=Reports())
    assert projected["status"] == "PROCESSING"
    assert all("EARLIER_RUN_UNRESOLVED" not in row["blockers"] for row in projected["target_results"])
    history = projected["target_results"][0]["history"]
    assert history[1]["external_write_count"] == 0
    assert projected["execution_summary"]["official_success_count"] == 0


def test_projection_never_promotes_reconciliation_to_success_or_retry(tmp_path):
    run_store, run, labels = make_failed(tmp_path)
    ProductPublicationRunReconciliationStore(run_store.path).register(**kwargs(run, labels))
    projected = project_execution(
        plan={"product_id": "3956742887", "plan_id": "plan-1", "targets": labels,
              "approval": {"approved_at": "2020-01-01T00:00:00+00:00"}},
        snapshot={"product_revision": 4, "snapshot_digest": "a"*64}, candidate={}, approval={},
        run_store=run_store, report_store=Reports())
    assert projected["status"] == "FAILED"
    assert {row["lifecycle"] for row in projected["target_results"]} == {"RECONCILED_ZERO_WRITE"}
    assert {row["next_action"] for row in projected["target_results"]} == {"READ_EXISTING_RECONCILIATION"}
    assert all(row["official_success"] is False for row in projected["target_results"])
    assert projected["execution_summary"]["external_write_count"] == 0


def test_cli_preview_is_read_only(tmp_path):
    run_store, run, labels = make_failed(tmp_path)
    before = hashlib.sha256(run_store.path.read_bytes()).hexdigest()
    payload = {"schema_version": "product-publication-zero-write-reconciliation-input/v1",
               **kwargs(run, labels)}
    input_path = tmp_path / "receipt-input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    script = "skills/publish-approved-product/scripts/register_zero_write_reconciliation.py"
    result = subprocess.run([sys.executable, script, "--db", str(run_store.path),
                             "--input", str(input_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["persisted"] is False
    assert hashlib.sha256(run_store.path.read_bytes()).hexdigest() == before
    assert ProductPublicationRunReconciliationStore(run_store.path).get(run_id=run["run_id"]) is None


def test_legacy_7e_report_is_diagnostic_zero_dispatch_not_unresolved():
    labels = ["shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"]
    report = {
        "status": "FAILED",
        "summary": {"evidence": {"dispatch_attempted": False,
                                "external_write_count": 0},
                    "platforms": [{"platform": "SHOPEE"}]},
        "targets": [{"target_label": label, "status": "FAILED", "evidence": None}
                    for label in labels],
        "mutation_budgets": [{"platform": "SHOPEE", "reservations": [],
                              "attempts": {"shared": 0, "total": 0,
                                           "per_target": {label: 0 for label in labels}}}],
    }
    assert _strict_zero_dispatch_without_target_evidence(report, report["targets"]) is True
    for mutation in (
        lambda r: r["summary"]["evidence"].update(dispatch_attempted=None),
        lambda r: r["mutation_budgets"][0]["attempts"].update(total=1),
        lambda r: r["mutation_budgets"][0]["reservations"].append({}),
    ):
        damaged = json.loads(json.dumps(report)); mutation(damaged)
        assert _strict_zero_dispatch_without_target_evidence(damaged, damaged["targets"]) is False


@pytest.mark.parametrize("platform,labels", [
    ("SHOPEE", ["shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"]),
    ("OZON", ["ozon:RU"]),
])
def test_explicit_exact_retry_accepts_only_matching_reconciled_failed_run(tmp_path, platform, labels):
    from shared_platform.product_publication_runner import claim_product_publication_request

    run_store, prior, _ = make_failed(tmp_path, run_id="incident", platform=platform, labels=labels)
    ProductPublicationRunReconciliationStore(run_store.path).register(**kwargs(prior, labels))
    prepared = SimpleNamespace(offer_id=prior["offer_id"], revision=prior["revision"],
        plan_id=prior["plan_id"], snapshot_digest=prior["snapshot_digest"],
        platform_scope=(platform,), target_labels_by_platform={platform: tuple(labels)})
    reports = SimpleNamespace(get_report_by_run=lambda **_: None)
    release = SimpleNamespace(approved_publication_snapshot=lambda **_: None)
    result = claim_product_publication_request(prepared=prepared, platform=platform,
        execution_identity=prior["execution_identity"], run_store=run_store,
        report_store=reports, release_store=release, retry_of_run_id=prior["run_id"])
    assert result.created is True and result.run_id != prior["run_id"]
    assert run_store.get_run_by_id(run_id=result.run_id)["target_count"] == len(labels)


def test_reconciled_retry_rejects_missing_receipt_and_target_expansion(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:PH", "shopee:MY"]
    run_store, prior, _ = make_failed(tmp_path, labels=labels)
    reports = SimpleNamespace(get_report_by_run=lambda **_: None)
    release = SimpleNamespace(approved_publication_snapshot=lambda **_: None)
    def claim(selected):
        return claim_product_publication_request(prepared=SimpleNamespace(
            offer_id=prior["offer_id"], revision=prior["revision"], plan_id=prior["plan_id"],
            snapshot_digest=prior["snapshot_digest"], platform_scope=("SHOPEE",),
            target_labels_by_platform={"SHOPEE": tuple(selected)}), platform="SHOPEE",
            execution_identity=prior["execution_identity"], run_store=run_store,
            report_store=reports, release_store=release, retry_of_run_id=prior["run_id"])
    with pytest.raises(ValueError, match="reconciled zero-write"):
        claim(labels)
    ProductPublicationRunReconciliationStore(run_store.path).register(**kwargs(prior, labels))
    with pytest.raises(ValueError, match="reconciled zero-write"):
        claim([labels[0]])
    with pytest.raises(ValueError, match="reconciled zero-write"):
        claim(list(reversed(labels)))


def test_fresh_successor_accepts_exact_reconciled_zero_write_predecessor(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:PH", "shopee:MY"]
    historical = valid_shopee_snapshot(labels)
    runs = ProductPublicationRunStore(tmp_path / "orbit.db")
    runs.create_run(
        run_id="run-zero-write", offer_id=historical["offer_id"],
        revision=historical["product_revision"], plan_id=historical["plan_id"],
        snapshot_digest=historical["snapshot_digest"], platform_scope=["SHOPEE"],
        target_count=len(labels), execution_identity={"skill_digest": "b" * 64,
        "git_commit": "c" * 40, "code_digest": "d" * 64},
    )
    runs.mark_failed(run_id="run-zero-write", failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    prior = runs.get_run_by_id(run_id="run-zero-write")
    ProductPublicationRunReconciliationStore(runs.path).register(**kwargs(prior, labels))
    prepared = SimpleNamespace(
        offer_id=prior["offer_id"], revision=5, plan_id="plan-successor",
        snapshot_digest="sha256:" + "e" * 64, platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": tuple(labels)},
    )
    result = claim_product_publication_request(
        prepared=prepared, platform="SHOPEE",
        execution_identity=prior["execution_identity"], run_store=runs,
        report_store=SimpleNamespace(get_report_by_run=lambda **_: None),
        release_store=SnapshotStore(historical),
    )
    assert result.created is True
    successor = runs.get_run_by_id(run_id=result.run_id)
    assert successor["plan_id"] == "plan-successor"
    assert successor["target_count"] == len(labels)


def test_fresh_successor_still_rejects_unreconciled_failed_predecessor(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:PH", "shopee:MY"]
    historical = valid_shopee_snapshot(labels)
    runs = ProductPublicationRunStore(tmp_path / "orbit.db")
    runs.create_run(
        run_id="run-zero-write", offer_id=historical["offer_id"],
        revision=historical["product_revision"], plan_id=historical["plan_id"],
        snapshot_digest=historical["snapshot_digest"], platform_scope=["SHOPEE"],
        target_count=len(labels), execution_identity={"skill_digest": "b" * 64,
        "git_commit": "c" * 40, "code_digest": "d" * 64},
    )
    runs.mark_failed(run_id="run-zero-write", failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    prior = runs.get_run_by_id(run_id="run-zero-write")
    prepared = SimpleNamespace(
        offer_id=prior["offer_id"], revision=5, plan_id="plan-successor",
        snapshot_digest="sha256:" + "e" * 64, platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": tuple(labels)},
    )
    with pytest.raises(ValueError, match="overlapping publication target"):
        claim_product_publication_request(
            prepared=prepared, platform="SHOPEE",
            execution_identity=prior["execution_identity"], run_store=runs,
            report_store=SimpleNamespace(get_report_by_run=lambda **_: None),
            release_store=SnapshotStore(historical),
        )


@pytest.mark.parametrize(
    "receipt_labels,run_id,target_count",
    [
        (["shopee:MY", "shopee:PH"], "run-zero-write", 2),
        (["shopee:PH", "shopee:TH"], "run-zero-write", 2),
        (["shopee:PH"], "run-zero-write", 1),
        (["shopee:PH", "shopee:MY"], "different-run", 2),
    ],
)
def test_fresh_successor_rejects_reconciled_history_not_bound_to_snapshot_targets(
    tmp_path, receipt_labels, run_id, target_count
):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:PH", "shopee:MY"]
    historical = valid_shopee_snapshot(labels)
    runs = ProductPublicationRunStore(tmp_path / "orbit.db")
    runs.create_run(
        run_id="run-zero-write", offer_id=historical["offer_id"],
        revision=historical["product_revision"], plan_id=historical["plan_id"],
        snapshot_digest=historical["snapshot_digest"], platform_scope=["SHOPEE"],
        target_count=target_count, execution_identity={"skill_digest": "b" * 64,
        "git_commit": "c" * 40, "code_digest": "d" * 64},
    )
    runs.mark_failed(run_id="run-zero-write", failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    prior = runs.get_run_by_id(run_id="run-zero-write")
    if run_id == prior["run_id"]:
        ProductPublicationRunReconciliationStore(runs.path).register(
            **kwargs(prior, receipt_labels)
        )
    else:
        runs.create_run(
            run_id=run_id, offer_id=historical["offer_id"],
            revision=historical["product_revision"], plan_id=historical["plan_id"],
            snapshot_digest=historical["snapshot_digest"], platform_scope=["SHOPEE"],
            target_count=len(receipt_labels), execution_identity=prior["execution_identity"],
        )
        runs.mark_failed(run_id=run_id, failure_code="RUNNER_INFRASTRUCTURE_FAILED")
        different = runs.get_run_by_id(run_id=run_id)
        ProductPublicationRunReconciliationStore(runs.path).register(
            **kwargs(different, receipt_labels)
        )
    prepared = SimpleNamespace(
        offer_id=prior["offer_id"], revision=prior["revision"] + 1,
        plan_id="plan-successor", snapshot_digest="sha256:" + "e" * 64,
        platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": tuple(labels)},
    )
    with pytest.raises(ValueError, match="reconciliation|target count"):
        claim_product_publication_request(
            prepared=prepared, platform="SHOPEE",
            execution_identity=prior["execution_identity"], run_store=runs,
            report_store=SimpleNamespace(get_report_by_run=lambda **_: None),
            release_store=SnapshotStore(historical),
        )


def test_retry_chain_transparently_keeps_strict_zero_write_history(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"]
    runs = ProductPublicationRunStore(tmp_path / "chain.db")
    identity = {"skill_digest": "b"*64, "git_commit": "c"*40, "code_digest": "d"*64}
    def create(run_id):
        return runs.create_run(run_id=run_id, offer_id="3956742887", revision=4,
            plan_id="plan-1", snapshot_digest="a"*64, platform_scope=["SHOPEE"],
            target_count=4, execution_identity=identity)
    first = create("run-7e"); runs.mark_running(run_id=first.run_id)
    runs.mark_completed(run_id=first.run_id, final_report_id=first.report_id)
    report = {"offer_id": "3956742887", "plan_id": "plan-1",
        "snapshot": {"digest": "sha256:" + "a"*64}, "report_id": first.report_id,
        "status": "FAILED", "summary": {"evidence": {"dispatch_attempted": False,
        "external_write_count": 0}, "platforms": [{"platform": "SHOPEE"}]},
        "targets": [{"target_label": label, "status": "FAILED", "evidence": None}
                    for label in labels],
        "mutation_budgets": [{"platform": "SHOPEE", "reservations": [],
            "attempts": {"shared": 0, "total": 0,
                         "per_target": {label: 0 for label in labels}}}]}
    for run_id in ("run-bb7", "run-377"):
        create(run_id); runs.mark_failed(run_id=run_id,
            failure_code="RUNNER_INFRASTRUCTURE_FAILED")
        current = runs.get_run_by_id(run_id=run_id)
        ProductPublicationRunReconciliationStore(runs.path).register(**kwargs(current, labels))
    c6 = create("run-c6"); runs.mark_running(run_id=c6.run_id)
    runs.mark_completed(run_id=c6.run_id, final_report_id=c6.report_id)
    c6_report = {**report, "report_id": c6.report_id}
    prepared = SimpleNamespace(offer_id="3956742887", revision=4, plan_id="plan-1",
        snapshot_digest="sha256:" + "a"*64, platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": tuple(labels)})
    reports_by_run = {"run-7e": report, "run-c6": c6_report}
    reports = SimpleNamespace(get_report_by_run=lambda run_id: reports_by_run.get(run_id))
    arguments = dict(prepared=prepared, platform="SHOPEE",
        execution_identity=identity, run_store=runs, report_store=reports,
        release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
        retry_of_run_id="run-c6")
    result = claim_product_publication_request(**arguments)
    repeated = claim_product_publication_request(**arguments)
    assert result.created is True
    assert repeated.created is False
    assert repeated.run_id == result.run_id


def test_retry_history_rejects_unreconciled_older_run(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:PH"]
    runs, older, _ = make_failed(tmp_path, run_id="older", labels=labels)
    runs.create_run(run_id="latest", offer_id=older["offer_id"], revision=older["revision"],
        plan_id=older["plan_id"], snapshot_digest=older["snapshot_digest"],
        platform_scope=["SHOPEE"], target_count=1, execution_identity=older["execution_identity"])
    runs.mark_failed(run_id="latest", failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    latest = runs.get_run_by_id(run_id="latest")
    ProductPublicationRunReconciliationStore(runs.path).register(**kwargs(latest, labels))
    with pytest.raises(ValueError, match="reconciliation"):
        claim_product_publication_request(prepared=SimpleNamespace(
            offer_id=latest["offer_id"], revision=latest["revision"], plan_id=latest["plan_id"],
            snapshot_digest=latest["snapshot_digest"], platform_scope=("SHOPEE",),
            target_labels_by_platform={"SHOPEE": tuple(labels)}), platform="SHOPEE",
            execution_identity=latest["execution_identity"], run_store=runs,
            report_store=SimpleNamespace(get_report_by_run=lambda **_: None),
            release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
            retry_of_run_id="latest")


def test_completed_zero_dispatch_source_rejects_damaged_older_report(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:PH"]
    runs = ProductPublicationRunStore(tmp_path / "damaged-chain.db")
    identity = {"skill_digest": "b"*64, "git_commit": "c"*40, "code_digest": "d"*64}
    def complete(run_id):
        created = runs.create_run(run_id=run_id, offer_id="3956742887", revision=4,
            plan_id="plan-1", snapshot_digest="a"*64, platform_scope=["SHOPEE"],
            target_count=1, execution_identity=identity)
        runs.mark_running(run_id=run_id)
        runs.mark_completed(run_id=run_id, final_report_id=created.report_id)
        return runs.get_run_by_id(run_id=run_id)
    older = complete("run-older-damaged")
    source = complete("run-source-zero")
    def report(run, reservations):
        return {"offer_id": run["offer_id"], "plan_id": run["plan_id"],
            "snapshot": {"digest": run["snapshot_digest"]}, "report_id": run["report_id"],
            "status": "FAILED", "summary": {"evidence": {"dispatch_attempted": False,
            "external_write_count": 0}, "platforms": [{"platform": "SHOPEE"}]},
            "targets": [{"target_label": labels[0], "status": "FAILED", "evidence": None}],
            "mutation_budgets": [{"platform": "SHOPEE", "reservations": reservations,
                "attempts": {"shared": 0, "total": 0, "per_target": {labels[0]: 0}}}]}
    reports_by_run = {
        older["run_id"]: report(older, [{"sequence": 1}]),
        source["run_id"]: report(source, []),
    }
    prepared = SimpleNamespace(offer_id=source["offer_id"], revision=source["revision"],
        plan_id=source["plan_id"], snapshot_digest=source["snapshot_digest"],
        platform_scope=("SHOPEE",), target_labels_by_platform={"SHOPEE": tuple(labels)})
    with pytest.raises(ValueError, match="history requires reconciliation"):
        claim_product_publication_request(prepared=prepared, platform="SHOPEE",
            execution_identity=identity, run_store=runs,
            report_store=SimpleNamespace(get_report_by_run=lambda run_id: reports_by_run.get(run_id)),
            release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
            retry_of_run_id=source["run_id"])


def test_recovery_continuation_claim_binds_exact_direct_predecessor_report(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request
    from shared_platform.shopee_recovery_coordination import continuation_identity
    from tests.test_shopee_recovery_reconciliations import build as recovery_receipt

    receipt, report, old_manifest, _readback, _sources = recovery_receipt(
        tmp_path / "receipt", ("MATCH", "DIFF"))
    labels = ["shopee:TH"]
    runs = ProductPublicationRunStore(tmp_path / "continuation.db")
    identity = {"skill_digest": "b" * 64, "git_commit": "c" * 40, "code_digest": "d" * 64}
    prior = runs.create_run(run_id=report["run_id"], offer_id=report["offer_id"], revision=report["revision"],
                            plan_id=report["plan_id"], snapshot_digest=report["snapshot"]["digest"],
                            platform_scope=["SHOPEE"], target_count=2, execution_identity=identity)
    runs.mark_running(run_id=prior.run_id)
    runs.mark_completed(run_id=prior.run_id, final_report_id=prior.report_id)
    report_digest = "sha256:" + hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    continuation = continuation_identity(
        receipt, run_id=report["run_id"], report_id=report["report_id"],
        manifest_digest=old_manifest["manifest_digest"], allowed_evidence_roots=[tmp_path],
        target_scope=labels, action_budgets={labels[0]: ["update_copy_and_description_media"]})
    continuation.update({
        "direct_predecessor": {"run_id": report["run_id"], "report_id": report["report_id"],
                               "report_digest": report_digest, "manifest_digest": old_manifest["manifest_digest"]},
        "original_ancestor": {"run_id": "original", "report_digest": "sha256:" + "1" * 64,
                              "candidate_digest": "2" * 64, "approval_digest": "3" * 64},
    })
    prepared = SimpleNamespace(offer_id=report["offer_id"], revision=report["revision"], plan_id=report["plan_id"],
        snapshot_digest=report["snapshot"]["digest"], platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": tuple(labels)})
    kwargs_ = dict(prepared=prepared, platform="SHOPEE", execution_identity=identity,
        run_store=runs, report_store=SimpleNamespace(get_report_by_run=lambda run_id: report),
        release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
        recovery_manifest_digest="sha256:" + "4" * 64, recovery_continuation=continuation,
        recovery_reconciliation_receipt=receipt, recovery_evidence_roots=[tmp_path])
    tampered = deepcopy(continuation)
    tampered["direct_predecessor"]["report_digest"] = "sha256:" + "9" * 64
    with pytest.raises(ValueError, match="continuation|predecessor"):
        claim_product_publication_request(**{**kwargs_, "recovery_continuation": tampered,
                                             "recovery_manifest_digest": "sha256:" + "5" * 64})
    created = claim_product_publication_request(**kwargs_)
    assert created.created is True and created.run_id != prior.run_id


def test_first_recovery_claim_does_not_reuse_same_snapshot_original_run(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:MY", "shopee:TH", "shopee:VN"]
    runs = ProductPublicationRunStore(tmp_path / "first-recovery.db")
    identity = {"skill_digest": "b" * 64, "git_commit": "c" * 40, "code_digest": "d" * 64}
    original = runs.create_run(
        run_id="original-shopee-run", offer_id="3956742887", revision=5,
        plan_id="plan-1", snapshot_digest="a" * 64, platform_scope=["SHOPEE"],
        target_count=4, execution_identity=identity,
    )
    runs.mark_running(run_id=original.run_id)
    runs.mark_completed(run_id=original.run_id, final_report_id=original.report_id)
    original_report = {
        "offer_id": "3956742887", "plan_id": "plan-1",
        "snapshot": {"digest": "sha256:" + "a" * 64},
        "report_id": original.report_id, "status": "PARTIAL",
        "summary": {"evidence": {"external_write_count": 9}},
        "targets": [
            {"target_label": "shopee:PH", "status": "PUBLISHED", "evidence": {}},
            *[{"target_label": label, "status": "FAILED", "evidence": {}}
              for label in labels],
        ],
    }
    prepared = SimpleNamespace(
        offer_id="3956742887", revision=5, plan_id="plan-1",
        snapshot_digest="sha256:" + "a" * 64, platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": tuple(labels)},
    )
    created = claim_product_publication_request(
        prepared=prepared, platform="SHOPEE", execution_identity=identity,
        run_store=runs,
        report_store=SimpleNamespace(get_report_by_run=lambda run_id: original_report),
        release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
        recovery_manifest_digest="sha256:" + "4" * 64,
    )
    assert created.created is True
    assert created.run_id != original.run_id
    replayed = claim_product_publication_request(
        prepared=prepared, platform="SHOPEE", execution_identity=identity,
        run_store=runs,
        report_store=SimpleNamespace(get_report_by_run=lambda run_id: original_report),
        release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
        recovery_manifest_digest="sha256:" + "4" * 64,
    )
    assert replayed.created is False and replayed.run_id == created.run_id
    with pytest.raises(ValueError, match="prior publication|reconciliation"):
        claim_product_publication_request(
            prepared=prepared, platform="SHOPEE", execution_identity=identity,
            run_store=runs,
            report_store=SimpleNamespace(get_report_by_run=lambda run_id: original_report),
            release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
            recovery_manifest_digest="sha256:" + "5" * 64,
        )


def test_standard_same_snapshot_still_reuses_original_run(tmp_path):
    from shared_platform.product_publication_runner import claim_product_publication_request

    labels = ["shopee:PH"]
    runs = ProductPublicationRunStore(tmp_path / "standard-dedupe.db")
    identity = {"skill_digest": "b" * 64, "git_commit": "c" * 40, "code_digest": "d" * 64}
    original = runs.create_run(
        run_id="standard-original", offer_id="3956742887", revision=5,
        plan_id="plan-1", snapshot_digest="a" * 64, platform_scope=["SHOPEE"],
        target_count=1, execution_identity=identity,
    )
    prepared = SimpleNamespace(
        offer_id="3956742887", revision=5, plan_id="plan-1",
        snapshot_digest="sha256:" + "a" * 64, platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": tuple(labels)},
    )
    replayed = claim_product_publication_request(
        prepared=prepared, platform="SHOPEE", execution_identity=identity,
        run_store=runs, report_store=SimpleNamespace(get_report_by_run=lambda **_: None),
        release_store=SimpleNamespace(approved_publication_snapshot=lambda **_: None),
    )
    assert replayed.created is False and replayed.run_id == original.run_id
