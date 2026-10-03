import hashlib
import json
from copy import deepcopy

import pytest

from shared_platform import operations_domain_guard as guard
from shared_platform.workbench_engine import WorkbenchEngine


ALL = ["shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"]
RECOVERY = ALL[1:]
SNAPSHOT_DIGEST = "sha256:" + "a" * 64


class Store:
    def approved_publication_snapshot(self, **_kwargs):
        return {
            "offer_id": "3956742887", "plan_id": "plan", "product_revision": 5,
            "snapshot_digest": SNAPSHOT_DIGEST,
            "skus": [{"seller_sku": "0988"}],
            "publication_targets": [{"target_label": label} for label in ALL],
        }


def _source(tmp_path, name, value):
    path = tmp_path / f"{name}.json"
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return str(path), "sha256:" + hashlib.sha256(raw).hexdigest()


def _facts(tmp_path):
    prior = {
        "schema_version": "product-publication-report/v2", "status": "PARTIAL",
        "offer_id": "3956742887", "plan_id": "plan", "revision": 5,
        "run_id": "prior", "report_id": "publication-report:prior",
        "snapshot": {"digest": SNAPSHOT_DIGEST},
        "targets": [{"target_label": label,
                     "status": "PUBLISHED" if label == "shopee:PH" else "FAILED"}
                    for label in ALL],
    }
    ids = {
        "shopee:MY": ("22", "33", "44"),
        "shopee:TH": ("55", "66", "77"),
        "shopee:VN": ("88", "99", "111"),
    }
    global_readback = {"response": {"response": {"published_item": [
        {"shop_region": "PH", "shop_id": 11, "item_id": 12, "item_status": 1},
        *[{"shop_region": label.split(":")[1], "shop_id": int(values[0]),
           "item_id": int(values[1]), "item_status": 8}
          for label, values in ids.items()],
    ]}}}
    regional = {"targets": [
        {"target_label": label, "shop_id": values[0], "item_id": values[1],
         "item_status": "UNLIST", "models": [{"model_id": values[2],
             "model_sku": "0988", "model_status": "MODEL_NORMAL"}]}
        for label, values in ids.items()
    ]}
    sources = {"prior_report": prior, "global_readback": global_readback,
               "regional_readback": regional, "global_model_readback": {},
               "media_binding_receipt": {}}
    paths, digests = {}, {}
    for name, value in sources.items():
        paths[name], digests[name] = _source(tmp_path, name, value)
    manifest = {
        "schema_version": "shopee-regional-recovery/v1",
        "status": "READY_ZERO_WRITE_PREFLIGHT", "offer_id": "3956742887",
        "plan_id": "plan", "execution_snapshot_digest": SNAPSHOT_DIGEST,
        "prior_run_id": "prior", "prior_report_digest": "sha256:" + "b" * 64,
        "manifest_digest": "sha256:" + "c" * 64,
        "source_file_paths": paths, "source_file_sha256": digests,
        "target_labels": list(RECOVERY),
        "forbidden_operations": ["create_publish_task", "upload_image", "global_mutation"],
        "zero_write_preflight": {"completed": True, "external_write_count": 0},
        "targets": [{"target_label": label, "shop_id": values[0],
                     "item_id": values[1], "model_id": values[2]}
                    for label, values in ids.items()],
    }
    retry = {"run_id": "successor", "retry_of_run_id": "prior",
             "source_evidence_digest": manifest["prior_report_digest"]}
    return manifest, retry, sources


def _setup(tmp_path, monkeypatch):
    engine = WorkbenchEngine(tmp_path / "tasks.db", {"code_version": "test"})
    monkeypatch.setattr(guard, "engine_for", lambda _root: engine)
    monkeypatch.setattr(
        "shared_platform.shopee_regional_recovery.validate_recovery_manifest",
        lambda manifest, **_kwargs: manifest)
    old = guard._publication_operation_id("plan", ALL)
    assert engine.begin_domain_operation(old, skus=["0988"], shops=ALL)["acquired"]
    return engine, old


def _begin(tmp_path, monkeypatch, *, mutate=None):
    engine, old = _setup(tmp_path, monkeypatch)
    manifest, retry, sources = _facts(tmp_path)
    if mutate:
        mutate(manifest, retry, sources)
        for name in ("prior_report", "global_readback", "regional_readback"):
            path, digest = _source(tmp_path, name, sources[name])
            manifest["source_file_paths"][name] = path
            manifest["source_file_sha256"][name] = digest
    result = guard.begin_snapshot_publication(
        Store(), "3956742887", SNAPSHOT_DIGEST, "SHOPEE", tmp_path,
        retry_attempt=retry, target_scope=RECOVERY,
        recovery_authorization=manifest, recovery_candidate={}, recovery_approval={},
        recovery_source_roots=(tmp_path,), expected_run_id="successor")
    return engine, old, result, manifest, retry


def test_exact_first_recovery_handover_is_atomic_and_idempotent(tmp_path, monkeypatch):
    engine, old, result, manifest, retry = _begin(tmp_path, monkeypatch)
    assert result[2]["acquired"] is True
    with engine.transaction() as conn:
        assert conn.execute("SELECT state FROM workbench_domain_operations WHERE operation_id=?",
                            (old,)).fetchone()["state"] == "completed"
        locks = conn.execute("SELECT resource,operation_id FROM workbench_domain_locks").fetchall()
    assert len(locks) == 3 and {row["operation_id"] for row in locks} == {result[1]}
    replay = guard.begin_snapshot_publication(
        Store(), "3956742887", SNAPSHOT_DIGEST, "SHOPEE", tmp_path,
        retry_attempt=retry, target_scope=RECOVERY, recovery_authorization=manifest,
        recovery_candidate={}, recovery_approval={}, recovery_source_roots=(tmp_path,),
        expected_run_id="successor")
    assert replay[2]["acquired"] is False


@pytest.mark.parametrize("drift", ["prior_run", "prior_status", "ph_status",
                                    "regional_status", "item_id", "scope",
                                    "duplicate_region", "wrong_run"])
def test_handover_drift_rolls_back_without_releasing_old(tmp_path, monkeypatch, drift):
    def mutate(manifest, retry, sources):
        if drift == "prior_run": manifest["prior_run_id"] = "other"
        elif drift == "prior_status": sources["prior_report"]["targets"][1]["status"] = "PUBLISHED"
        elif drift == "ph_status": sources["global_readback"]["response"]["response"]["published_item"][0]["item_status"] = 8
        elif drift == "regional_status": sources["regional_readback"]["targets"][0]["item_status"] = "NORMAL"
        elif drift == "item_id": sources["regional_readback"]["targets"][0]["item_id"] = "999"
        elif drift == "scope": manifest["target_labels"] = ["shopee:MY", "shopee:TH"]
        elif drift == "duplicate_region":
            sources["global_readback"]["response"]["response"]["published_item"][1]["shop_region"] = "PH"
        elif drift == "wrong_run": retry["run_id"] = "another-successor"
    engine, old = _setup(tmp_path, monkeypatch)
    manifest, retry, sources = _facts(tmp_path)
    mutate(manifest, retry, sources)
    for name in ("prior_report", "global_readback", "regional_readback"):
        path, digest = _source(tmp_path, name, sources[name])
        manifest["source_file_paths"][name] = path
        manifest["source_file_sha256"][name] = digest
    with pytest.raises(ValueError):
        guard.begin_snapshot_publication(
            Store(), "3956742887", SNAPSHOT_DIGEST, "SHOPEE", tmp_path,
            retry_attempt=retry, target_scope=RECOVERY,
            recovery_authorization=manifest, recovery_candidate={}, recovery_approval={},
            recovery_source_roots=(tmp_path,), expected_run_id="successor")
    with engine.transaction() as conn:
        assert conn.execute("SELECT state FROM workbench_domain_operations WHERE operation_id=?",
                            (old,)).fetchone()["state"] == "inflight"
        assert conn.execute("SELECT COUNT(*) n FROM workbench_domain_operations").fetchone()["n"] == 1


def test_engine_rejects_extra_old_lock_and_rolls_back(tmp_path):
    engine = WorkbenchEngine(tmp_path / "tasks.db", {"code_version": "test"})
    assert engine.begin_domain_operation("old", skus=["0988"], shops=ALL)["acquired"]
    with engine.transaction() as conn:
        conn.execute("INSERT INTO workbench_domain_locks VALUES(?,?)",
                     ('product:["shopee:SG","0988"]', "old"))
    with pytest.raises(ValueError, match="lock set drifted"):
        engine.supersede_domain_operation(
            "old", "new", previous_skus=["0988"], previous_shops=ALL,
            skus=["0988"], shops=RECOVERY, reconciliation_ref="receipt")
    with engine.transaction() as conn:
        assert conn.execute("SELECT state FROM workbench_domain_operations WHERE operation_id='old'").fetchone()["state"] == "inflight"
        assert conn.execute("SELECT 1 FROM workbench_domain_operations WHERE operation_id='new'").fetchone() is None


def test_engine_rolls_back_when_successor_insert_fails_after_old_update(tmp_path):
    engine = WorkbenchEngine(tmp_path / "tasks.db", {"code_version": "test"})
    assert engine.begin_domain_operation("old", skus=["0988"], shops=ALL)["acquired"]
    with engine.transaction() as conn:
        conn.execute("""
            CREATE TRIGGER fail_successor BEFORE INSERT ON workbench_domain_operations
            WHEN NEW.operation_id='new' BEGIN SELECT RAISE(ABORT, 'forced'); END
        """)
    with pytest.raises(Exception, match="forced"):
        engine.supersede_domain_operation(
            "old", "new", previous_skus=["0988"], previous_shops=ALL,
            skus=["0988"], shops=RECOVERY, reconciliation_ref="receipt")
    with engine.transaction() as conn:
        old = conn.execute("SELECT * FROM workbench_domain_operations WHERE operation_id='old'").fetchone()
        locks = conn.execute("SELECT * FROM workbench_domain_locks WHERE operation_id='old'").fetchall()
        assert old["state"] == "inflight" and old["readback_ref"] == ""
        assert len(locks) == 4
        assert conn.execute("SELECT 1 FROM workbench_domain_operations WHERE operation_id='new'").fetchone() is None


@pytest.mark.parametrize("successor_drift", ["missing_lock", "completed_state"])
def test_idempotent_handover_rejects_successor_state_or_lock_drift(
        tmp_path, monkeypatch, successor_drift):
    engine, _old, result, manifest, retry = _begin(tmp_path, monkeypatch)
    with engine.transaction() as conn:
        if successor_drift == "missing_lock":
            conn.execute("DELETE FROM workbench_domain_locks WHERE resource=("
                         "SELECT resource FROM workbench_domain_locks WHERE operation_id=? LIMIT 1)",
                         (result[1],))
        else:
            conn.execute("UPDATE workbench_domain_operations SET state='completed' WHERE operation_id=?",
                         (result[1],))
            conn.execute("DELETE FROM workbench_domain_locks WHERE operation_id=?", (result[1],))
    with pytest.raises(ValueError, match="lock state drifted"):
        guard.begin_snapshot_publication(
            Store(), "3956742887", SNAPSHOT_DIGEST, "SHOPEE", tmp_path,
            retry_attempt=retry, target_scope=RECOVERY, recovery_authorization=manifest,
            recovery_candidate={}, recovery_approval={}, recovery_source_roots=(tmp_path,),
            expected_run_id="successor")
