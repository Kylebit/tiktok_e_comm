import json
import hashlib
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from shared_platform.shopee_reportless_recovery_reconciliations import (
    ShopeeReportlessRecoveryReconciliationStore,
    build_reportless_continuation_preflight,
    build_reportless_continuation_manifest,
    build_reportless_reconciliation_receipt,
    validate_reportless_continuation_preflight,
    validate_reportless_continuation_manifest,
)
from shared_platform.shopee_recovery_reconciliations import ShopeeRecoveryReconciliationError
from shared_platform.operations_domain_guard import (
    finish_reportless_shopee_recovery_reconciliation,
)
from tests.test_shopee_recovery_reconciliations import bundle, canon, dig, put


FAILING = "d4d105e2408df9f89126ec4f76759b22929e86bd"
FIXED = "3b20719a3a8a201aaad456229dc3e2d2c736b1e7"

REAL_REPO = Path(__file__).parents[1]
REAL_OPS = Path(r"D:\OrbitHive\runtime\operations-stable-1c5f5260")
REAL_EXTERNAL = Path(r"D:\OrbitEvidence\operations-runtime-20260909\image-approval-395")
REAL_MANIFEST = REAL_REPO / r"reports\product-preparation\3956742887\shopee-recovery-candidates\769c8e77d8748eeceb3d11ee77082689bbda5f5d2e6455da84e251d4ae401f36.json"
REAL_TOP = REAL_EXTERNAL / r"sr4bc\sr\05d20ddb59d8\8d960fabb01d\top-0148b73df075d7623037dd95e629c65115adfc391e3f4fbadf77fd628f514b45.json"
REAL_AUTHORITY = REAL_EXTERNAL / r"sr4bc\sr\05d20ddb59d8\8d960fabb01d\authority-3ca7bbf52d2098ba912b9b939e4399ef1133ef7b9c715bb8033d287e155c016e.json"
REAL_INCIDENT = REAL_OPS / r"sr-reportless\4bc\incident.json"
REAL_CLOSURE = REAL_OPS / r"sr-reportless\4bc\closure-31a5a24765cf5b7888f7d55e1194a112eff6ec317117eea348d38ca3e7029bdc.json"
REAL_CONTINUATION = REAL_OPS / r"sr-reportless\4bc\continuation-590e07b7687dc2cb112928f725368f9cc12d157df156dd05609cd4153e87ea1e.json"
REAL_FRESH_TOP = REAL_EXTERNAL / r"sr590e\sr\05d20ddb59d8\1da6935aaf42\top-9057b93dcf6f643437c4d0a60ba3d01298dfdbcdfd0a6371a2b4ad54ce9f4423.json"
REAL_FRESH_AUTHORITY = REAL_EXTERNAL / r"sr590e\sr\05d20ddb59d8\1da6935aaf42\authority-5a296c8a6e49d6cac1bcc19c534a24d172e8cebabac701fd6dc359900b09bbe1.json"


class RunStore:
    def __init__(self, path, run):
        self.path = path; self.run = run
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE product_publication_run_events (sequence INTEGER,state TEXT,final_report_id TEXT,failure_code TEXT,created_at TEXT,run_identity_digest TEXT,event_digest TEXT,run_id TEXT)")
            for sequence, state in enumerate(("QUEUED", "RUNNING", "FAILED"), 1):
                conn.execute("INSERT INTO product_publication_run_events VALUES (?,?,?,?,?,?,?,?)",
                             (sequence, state, None, "RUNNER_INFRASTRUCTURE_FAILED" if state == "FAILED" else None,
                              ("2026-09-13T01:51:00+00:00", "2026-09-13T01:52:00+00:00", run["updated_at"])[sequence-1],
                              "identity", f"event-{sequence}", run["run_id"]))
    def get_run_by_id(self, *, run_id):
        return self.run if run_id == self.run["run_id"] else None


class ReportStore:
    def get_report_by_run(self, *, run_id): return None


def fixture(tmp_path, monkeypatch):
    _, manifest, readback, _ = bundle(tmp_path / "raw", ("DIFF", "DIFF"), "2026-09-13T02:00:00+00:00")
    for row in readback["targets"]:
        item_meta = row["reads"]["item"]
        path = Path(item_meta["response_ref"]); env = json.loads(path.read_text())
        item = env["raw_response"]
        item.update(status="UNLIST", title="old", description="old", description_type="normal",
                    description_image_ids=["old"])
        raw = (canon(env) + "\n").encode(); path.write_bytes(raw)
        import hashlib
        item_meta["response_digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
        body = dict(row); body.pop("target_evidence_digest"); row["target_evidence_digest"] = dig(body)
    readback.pop("evidence_digest"); readback["evidence_digest"] = dig(readback)
    retry = {"receipt_digest": "sha256:" + "f" * 64,
             "run_identity": {"run_id": "pre-running"}}
    run_id = "reportless-run"
    readback["run_id"] = run_id
    readback["report_id"] = "publication-report:" + run_id
    readback.pop("evidence_digest"); readback["evidence_digest"] = dig(readback)
    run = {"run_id": run_id, "report_id": "publication-report:" + run_id,
           "offer_id": manifest["offer_id"], "revision": manifest["product_revision"],
           "plan_id": manifest["plan_id"], "snapshot_digest": manifest["execution_snapshot_digest"],
           "platform_scope": ["SHOPEE"], "target_count": 2, "state": "FAILED",
           "final_report_id": None, "failure_code": "RUNNER_INFRASTRUCTURE_FAILED",
           "created_at": "2026-09-13T01:50:00+00:00", "updated_at": "2026-09-13T01:59:00+00:00",
           "execution_identity": {"git_commit": FAILING},
           "request_identity": {"kind": "SHOPEE_RECOVERY_RETRY",
             "authority_digest": manifest["manifest_digest"],
             "reconciliation_receipt_digest": retry["receipt_digest"],
             "retry_of_run_id": "pre-running"}}
    monkeypatch.setattr("shared_platform.shopee_regional_recovery.validate_recovery_manifest",
                        lambda value, **kwargs: dict(value))
    monkeypatch.setattr("shared_platform.shopee_recovery_run_reconciliations.ShopeeRecoveryRunReconciliationStore.get",
                        lambda self, **kwargs: retry)
    store = RunStore(tmp_path / "runs.db", run)
    events=[]
    with sqlite3.connect(store.path) as conn:
        conn.row_factory=sqlite3.Row
        for row in conn.execute("SELECT * FROM product_publication_run_events ORDER BY sequence"):
            events.append({key:row[key] for key in ("sequence","state","final_report_id","failure_code","created_at","event_digest")})
    manifest_ref=put(tmp_path/"sources"/"manifest.json",manifest)
    authority={"schema_version":"shopee-get-only-collector-authority/v1",
               "authorized_method":"GET","run":run,"ordered_events":events,
               "manifest":manifest_ref,"manifest_digest":manifest["manifest_digest"],
               "expected_report_id":run["report_id"],"immutable_report_present":False,
               "product_writes":0,"observed_at":readback["observed_at"]}
    authority["authority_digest"] = dig(authority)
    incident={"schema_version":"shopee-recovery-runtime-failure/v1","run_id":run_id,
              "failure_code":"RUNNER_INFRASTRUCTURE_FAILED","error_type":"NameError",
              "missing_symbol":"deepcopy","failing_commit":FAILING,"fix_commit":FIXED,
              "log_line":f"publication background run failed run_id={run_id} error_type=NameError"}
    refs={"manifest_ref":manifest_ref,
          "official_readback_ref":put(tmp_path/"sources"/"top.json",readback),
          "authority_ref":put(tmp_path/"sources"/"authority.json",authority),
          "incident_ref":put(tmp_path/"sources"/"incident.json",incident)}
    return store, ReportStore(), retry, refs


def test_reportless_receipt_rebuilds_raw_gets_and_preserves_unknown_count(tmp_path, monkeypatch):
    run_store, report_store, retry, refs = fixture(tmp_path, monkeypatch)
    kwargs = dict(run_store=run_store, report_store=report_store, run_id="reportless-run",
                  snapshot={}, candidate={}, approval={},
                  recovery_retry_receipt_digest=retry["receipt_digest"], **refs,
                  evidence_root=tmp_path, allowed_evidence_roots=[tmp_path],
                  repo_root=Path(__file__).parents[1])
    receipt = build_reportless_reconciliation_receipt(**kwargs)
    assert receipt["result"] == "REMAINING_DIFF"
    assert receipt["attempt"]["provider_mutation_attempts"] == "UNKNOWN"
    assert receipt["attempt"]["external_write_count"] is None
    assert receipt["new_manifest"]["authorized"] is False
    assert set(receipt["new_manifest"]["forbidden_actions"]) >= {
        "create_publish_task", "upload_images", "global_mutation", "update_price"
    }
    durable = ShopeeReportlessRecoveryReconciliationStore(
        tmp_path / "receipts", run_store=run_store, report_store=report_store,
        snapshot={}, candidate={}, approval={}, allowed_evidence_roots=[tmp_path])
    assert durable.register(receipt) == receipt
    assert durable.get(run_id="reportless-run", receipt_digest=receipt["receipt_digest"]) == receipt

    receipt_path = durable._path("reportless-run")
    assert receipt_path.name == "receipt.json"

    readback=json.loads(Path(refs["official_readback_ref"]["path"]).read_text())
    raw = Path(readback["targets"][0]["reads"]["item"]["response_ref"])
    raw.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ShopeeRecoveryReconciliationError, match="bytes digest"):
        durable.get(run_id="reportless-run", receipt_digest=receipt["receipt_digest"])


def test_reportless_receipt_rejects_run_or_fix_provenance_drift(tmp_path, monkeypatch):
    run_store, report_store, retry, refs = fixture(tmp_path, monkeypatch)
    base = dict(run_store=run_store, report_store=report_store, run_id="reportless-run",
                snapshot={}, candidate={}, approval={},
                recovery_retry_receipt_digest=retry["receipt_digest"], **refs,
                evidence_root=tmp_path, allowed_evidence_roots=[tmp_path],
                repo_root=Path(__file__).parents[1])
    run_store.run["final_report_id"] = "publication-report:reportless-run"
    with pytest.raises(ShopeeRecoveryReconciliationError, match="run identity"):
        build_reportless_reconciliation_receipt(**base)
    run_store.run["final_report_id"] = None
    incident=json.loads(Path(refs["incident_ref"]["path"]).read_text())
    incident["fix_commit"] = FAILING
    base["incident_ref"] = put(tmp_path/"sources"/"bad-incident.json",incident)
    with pytest.raises(ShopeeRecoveryReconciliationError, match="provenance"):
        build_reportless_reconciliation_receipt(**base)


def test_authority_and_event_chain_are_exact_and_future_reads_fail(tmp_path, monkeypatch):
    run_store, report_store, retry, refs = fixture(tmp_path, monkeypatch)
    base = dict(run_store=run_store, report_store=report_store, run_id="reportless-run",
                snapshot={}, candidate={}, approval={},
                recovery_retry_receipt_digest=retry["receipt_digest"], **refs,
                evidence_root=tmp_path, allowed_evidence_roots=[tmp_path],
                repo_root=Path(__file__).parents[1])
    authority = json.loads(Path(refs["authority_ref"]["path"]).read_text())
    authority["authorized_method"] = "POST"
    body = dict(authority); body.pop("authority_digest")
    authority["authority_digest"] = dig(body)
    changed = dict(base)
    changed["authority_ref"] = put(tmp_path/"sources"/"post-authority.json", authority)
    with pytest.raises(ShopeeRecoveryReconciliationError, match="authority"):
        build_reportless_reconciliation_receipt(**changed)

    with sqlite3.connect(run_store.path) as conn:
        conn.execute("UPDATE product_publication_run_events SET final_report_id='forged' WHERE sequence=2")
    with pytest.raises(ShopeeRecoveryReconciliationError, match="lifecycle"):
        build_reportless_reconciliation_receipt(**base)


class _ReleaseStore:
    def __init__(self, snapshot): self.snapshot = snapshot
    def approved_publication_snapshot(self, **kwargs): return self.snapshot


class _Engine:
    def __init__(self, *, fail=False): self.calls=[]; self.fail=fail
    def complete_domain_operation(self, operation, *, provider_readback_ref):
        self.calls.append((operation, provider_readback_ref))
        if self.fail: raise RuntimeError("transaction refused")


def test_domain_closure_rebuilds_receipt_before_one_exact_completion(tmp_path, monkeypatch):
    run_store, report_store, retry, refs = fixture(tmp_path, monkeypatch)
    receipt = build_reportless_reconciliation_receipt(
        run_store=run_store, report_store=report_store, run_id="reportless-run",
        snapshot={}, candidate={}, approval={}, recovery_retry_receipt_digest=retry["receipt_digest"],
        **refs, evidence_root=tmp_path, allowed_evidence_roots=[tmp_path],
        repo_root=Path(__file__).parents[1])
    manifest = json.loads(Path(refs["manifest_ref"]["path"]).read_text())
    snapshot = {"plan_id": manifest["plan_id"]}
    engine = _Engine()
    monkeypatch.setattr("shared_platform.operations_domain_guard.engine_for", lambda root: engine)
    kwargs = dict(release_store=_ReleaseStore(snapshot), offer_id=manifest["offer_id"],
                  snapshot_digest=manifest["execution_snapshot_digest"], root=tmp_path,
                  receipt=receipt, target_scope=manifest["target_labels"], run_store=run_store,
                  report_store=report_store, snapshot={}, candidate={}, approval={},
                  allowed_evidence_roots=[tmp_path])
    result = finish_reportless_shopee_recovery_reconciliation(**kwargs)
    assert result["result"] == "REMAINING_DIFF"
    assert len(engine.calls) == 1

    forged = json.loads(json.dumps(receipt)); forged["authority_identity"]["offer_id"] = "other"
    forged.pop("receipt_digest"); forged["receipt_digest"] = dig(forged)
    kwargs["receipt"] = forged
    with pytest.raises(ShopeeRecoveryReconciliationError):
        finish_reportless_shopee_recovery_reconciliation(**kwargs)
    assert len(engine.calls) == 1

    kwargs["receipt"] = receipt; kwargs["target_scope"] = list(reversed(manifest["target_labels"]))
    with pytest.raises(ValueError, match="domain scope"):
        finish_reportless_shopee_recovery_reconciliation(**kwargs)
    assert len(engine.calls) == 1

    failing = _Engine(fail=True)
    monkeypatch.setattr("shared_platform.operations_domain_guard.engine_for", lambda root: failing)
    kwargs["target_scope"] = manifest["target_labels"]
    with pytest.raises(RuntimeError, match="transaction refused"):
        finish_reportless_shopee_recovery_reconciliation(**kwargs)
    assert len(failing.calls) == 1


@pytest.mark.skipif(not all(path.is_file() for path in (
    REAL_MANIFEST, REAL_TOP, REAL_AUTHORITY, REAL_INCIDENT, REAL_CLOSURE,
    REAL_CONTINUATION, REAL_FRESH_TOP, REAL_FRESH_AUTHORITY, REAL_OPS/"tasks.db",
    REAL_REPO / "data" / "orbit_platform.db",
    Path(r"D:\OrbitHive\candidate-20260908\frozen-shopee-master-price\data\orbit_platform.db"),
)), reason="exact immutable 4bc reconciliation evidence is unavailable")
def test_real_4bc_evidence_rebuilds_exact_remaining_diff(tmp_path):
    from shared_platform.product_publication_runs import ProductPublicationRunStore
    from shared_platform.product_publication_reports import ProductPublicationReportStore
    from shared_platform.release_store import ReleaseStore
    from shared_platform.product_publication_runner import prepare_product_publication_run
    from shared_platform.publication_autopilot import resolve_persisted_execution_authority
    from shared_platform.shopee_recovery_run_reconciliations import ShopeeRecoveryRunReconciliationStore

    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    release = ReleaseStore(r"D:\OrbitHive\candidate-20260908\frozen-shopee-master-price\data\orbit_platform.db")
    prepared = prepare_product_publication_run(
        release_store=release, offer_id=manifest["offer_id"],
        snapshot_digest=manifest["execution_snapshot_digest"], platform_scope=("SHOPEE",),
        target_scope=tuple(manifest["target_labels"]))
    candidate, approval = resolve_persisted_execution_authority(
        snapshot=prepared.snapshot, platform_scope=("SHOPEE",),
        target_labels=prepared.target_labels_by_platform["SHOPEE"],
        reports_root=REAL_REPO/"reports"/"product-preparation",
        candidate_digest=manifest["candidate_digest"])
    run_store = ProductPublicationRunStore(REAL_REPO/"data"/"orbit_platform.db")
    report_store = ProductPublicationReportStore(
        REAL_REPO/"data"/"orbit_platform.db", reports_root=REAL_REPO/"reports"/"product-publication")
    retry = ShopeeRecoveryRunReconciliationStore(run_store.path).get(
        run_id="product-center-shopee-1e6e675a650ad03208a6f8d8996408a6",
        allowed_evidence_roots=(REAL_OPS,), snapshot=prepared.snapshot,
        candidate=candidate, approval=approval)
    def ref(path):
        return {"path": str(path), "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
    roots = (REAL_REPO/"reports"/"product-publication",
             REAL_REPO/"reports"/"product-preparation", REAL_OPS, REAL_EXTERNAL, tmp_path)
    receipt = build_reportless_reconciliation_receipt(
        run_store=run_store, report_store=report_store,
        run_id="product-center-shopee-4bc697d923e8cbb569e8533a3c63e932",
        manifest_ref=ref(REAL_MANIFEST), snapshot=prepared.snapshot,
        candidate=candidate, approval=approval,
        recovery_retry_receipt_digest=retry["receipt_digest"],
        official_readback_ref=ref(REAL_TOP), authority_ref=ref(REAL_AUTHORITY),
        incident_ref=ref(REAL_INCIDENT), evidence_root=REAL_OPS/"sr-reportless",
        allowed_evidence_roots=roots,
        repo_root=REAL_REPO)
    assert receipt["result"] == "REMAINING_DIFF"
    assert [row["target_label"] for row in receipt["new_manifest"]["exact_remaining_differences"]] \
        == ["shopee:MY", "shopee:TH", "shopee:VN"]
    assert all(set(row["differences"]) == {
        "item.status", "copy.title", "copy.description", "description.type", "description.images"
    } for row in receipt["new_manifest"]["exact_remaining_differences"])
    preflight = build_reportless_continuation_preflight(
        receipt=receipt, run_store=run_store, report_store=report_store,
        snapshot=prepared.snapshot, candidate=candidate, approval=approval,
        closure_ref=ref(REAL_CLOSURE), continuation_ref=ref(REAL_CONTINUATION),
        fresh_readback_ref=ref(REAL_FRESH_TOP), fresh_authority_ref=ref(REAL_FRESH_AUTHORITY),
        operations_db=REAL_OPS/"tasks.db",
        allowed_evidence_roots=roots)
    assert preflight["preflight_digest"] == \
        "sha256:879766dea25c24b0136248542ae52e59a8972dbbf85d873bd2026fc4c7057c72"
    assert preflight["authorized"] is True
    assert preflight["zero_write_preflight"]["external_write_count"] == 0
    assert validate_reportless_continuation_preflight(
        preflight, receipt=receipt, run_store=run_store, report_store=report_store,
        snapshot=prepared.snapshot, candidate=candidate, approval=approval,
        operations_db=REAL_OPS/"tasks.db",
        allowed_evidence_roots=roots,
    ) == preflight
    preflight_path = tmp_path/"preflight.json"
    preflight_ref = put(preflight_path, preflight)
    execution_manifest = build_reportless_continuation_manifest(
        preflight_ref=preflight_ref, receipt=receipt, run_store=run_store,
        report_store=report_store, snapshot=prepared.snapshot, candidate=candidate,
        approval=approval, operations_db=REAL_OPS/"tasks.db", allowed_evidence_roots=roots)
    assert validate_reportless_continuation_manifest(
        execution_manifest, receipt=receipt, run_store=run_store, report_store=report_store,
        snapshot=prepared.snapshot, candidate=candidate, approval=approval,
        operations_db=REAL_OPS/"tasks.db", allowed_evidence_roots=roots) == execution_manifest
    assert execution_manifest["direct_predecessor_run_id"].endswith("4bc697d923e8cbb569e8533a3c63e932")
    assert all(row["allowed_actions"] == ["update_copy_and_description_media", "list_existing_item"]
               and row["mutation_budget"] == {"shared_maximum": 0, "target_maximum": 2}
               for row in execution_manifest["targets"])
    changed = json.loads(json.dumps(execution_manifest))
    changed["targets"][0]["allowed_actions"].append("update_images_existing_media")
    changed.pop("manifest_digest"); changed["manifest_digest"] = dig(changed)
    with pytest.raises(ShopeeRecoveryReconciliationError, match="semantics"):
        validate_reportless_continuation_manifest(
            changed, receipt=receipt, run_store=run_store, report_store=report_store,
            snapshot=prepared.snapshot, candidate=candidate, approval=approval,
            operations_db=REAL_OPS/"tasks.db", allowed_evidence_roots=roots)
    frozen = json.loads(REAL_FRESH_TOP.read_text(encoding="utf-8"))
    for field, wrong in (("closure_digest", "sha256:"+"0"*64),
                         ("closure_observed_at", "2026-09-13T05:49:55+00:00"),
                         ("continuation_digest", "sha256:"+"1"*64)):
        changed = json.loads(json.dumps(frozen)); changed[field] = wrong
        changed.pop("evidence_digest"); changed["evidence_digest"] = dig(changed)
        changed_ref = put(tmp_path/(field+".json"), changed)
        with pytest.raises(ShopeeRecoveryReconciliationError, match="lineage"):
            build_reportless_continuation_preflight(
                receipt=receipt, run_store=run_store, report_store=report_store,
                snapshot=prepared.snapshot, candidate=candidate, approval=approval,
                closure_ref=ref(REAL_CLOSURE), continuation_ref=ref(REAL_CONTINUATION),
                fresh_readback_ref=changed_ref, fresh_authority_ref=ref(REAL_FRESH_AUTHORITY),
                operations_db=REAL_OPS/"tasks.db", allowed_evidence_roots=roots)


def test_store_fixed_run_slot_is_atomic_for_concurrent_receipts(tmp_path, monkeypatch):
    class Empty: pass
    store = ShopeeReportlessRecoveryReconciliationStore(
        tmp_path, run_store=Empty(), report_store=Empty(), snapshot={}, candidate={},
        approval={}, allowed_evidence_roots=[tmp_path])
    monkeypatch.setattr(store, "_validate", lambda value: json.loads(json.dumps(value)))
    first = {"attempt": {"run_id": "same-run"}, "receipt_digest": "sha256:" + "1"*64,
             "marker": "first"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda value: _capture_register(store, value), (first, first)))
    successes = [value for ok, value in outcomes if ok]
    failures = [value for ok, value in outcomes if not ok]
    assert len(successes) == 2
    assert failures == []
    assert store.register(first) == first
    second = {"attempt": {"run_id": "same-run"}, "receipt_digest": "sha256:" + "2"*64,
              "marker": "second"}
    with pytest.raises(ShopeeRecoveryReconciliationError, match="already has"):
        store.register(second)


def _capture_register(store, value):
    try:
        return True, store.register(value)
    except ShopeeRecoveryReconciliationError as error:
        return False, str(error)
