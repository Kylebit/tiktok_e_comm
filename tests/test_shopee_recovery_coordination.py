import json
from copy import deepcopy
from pathlib import Path

import pytest
from types import SimpleNamespace

from shared_platform.shopee_recovery_coordination import (
    OfficialShopeeRecoveryGetter,
    collect_direct_id_readback,
    continuation_identity,
    reconcile_recovery_attempt,
)
from shared_platform.shopee_recovery_reconciliations import (
    ShopeeRecoveryReconciliationError,
    build_reconciliation_receipt,
    reconciliation_gate,
)
from tests.test_shopee_recovery_reconciliations import bundle, build


def _getter_from_bundle(readback):
    by_shop = {row["query"]["shop_id"]: row for row in readback["targets"]}

    def get(resource, query):
        row = by_shop[query["shop_id"]]
        envelope = json.loads(Path(row["reads"][resource]["response_ref"]).read_text(encoding="utf-8"))
        return envelope["raw_response"]

    return get


def test_controller_loads_durable_sources_and_registers_short_append_only_gets(tmp_path):
    attempt, manifest, source_readback, sources = bundle(tmp_path / "source", ("MATCH", "DIFF"))
    receipt = reconcile_recovery_attempt(
        attempt_path=sources["attempt"]["path"], manifest_path=sources["manifest"]["path"],
        evidence_root=tmp_path / "runtime", receipt_root=tmp_path / "runtime" / "receipts",
        allowed_evidence_roots=[tmp_path], manifest_validator=lambda value: deepcopy(dict(value)),
        getter=_getter_from_bundle(source_readback), observed_at="2026-09-13T02:01:00+00:00",
    )
    assert reconciliation_gate(receipt, run_id=attempt["run_id"], report_id=attempt["report_id"],
                               manifest_digest=manifest["manifest_digest"],
                               allowed_evidence_roots=[tmp_path])["result"] == "REMAINING_DIFF"
    files = list((tmp_path / "runtime" / "sr").rglob("*.json"))
    assert len(files) == 10
    assert max(len(str(path)) for path in files) < 240
    before = {path: path.read_bytes() for path in files}
    # A later observation is a new immutable directory and leaves prior receipt bytes valid.
    reconcile_recovery_attempt(
        attempt_path=sources["attempt"]["path"], manifest_path=sources["manifest"]["path"],
        evidence_root=tmp_path / "runtime", receipt_root=tmp_path / "runtime" / "receipts2",
        allowed_evidence_roots=[tmp_path], manifest_validator=lambda value: deepcopy(dict(value)),
        getter=_getter_from_bundle(source_readback), observed_at="2026-09-13T02:02:00+00:00",
    )
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_deep_manifest_gate_and_output_roots_fail_before_any_get(tmp_path):
    attempt, manifest, _readback, _sources = bundle(tmp_path / "source")
    calls = []

    def getter(resource, query):
        calls.append((resource, query))
        return {}

    with pytest.raises(ShopeeRecoveryReconciliationError, match="outside allowed"):
        collect_direct_id_readback(
            attempt=attempt, manifest=manifest, evidence_root=tmp_path.parent / "escape",
            getter=getter, observed_at="2026-09-13T02:01:00+00:00",
            manifest_validator=lambda value: value, allowed_evidence_roots=[tmp_path],
        )
    assert calls == []
    with pytest.raises(RuntimeError, match="deep blocked"):
        collect_direct_id_readback(
            attempt=attempt, manifest=manifest, evidence_root=tmp_path,
            getter=getter, observed_at="2026-09-13T02:01:00+00:00",
            manifest_validator=lambda value: (_ for _ in ()).throw(RuntimeError("deep blocked")),
            allowed_evidence_roots=[tmp_path],
        )
    assert calls == []


def test_get_exception_becomes_unknown_and_never_retries(tmp_path):
    attempt, manifest, source_readback, _sources = bundle(tmp_path / "source")
    getter = _getter_from_bundle(source_readback)
    counts = {}

    def flaky(resource, query):
        key = (query["shop_id"], resource)
        counts[key] = counts.get(key, 0) + 1
        if query["shop_id"] == "12" and resource == "models":
            raise TimeoutError("provider timeout")
        return getter(resource, query)

    readback = collect_direct_id_readback(
        attempt=attempt, manifest=manifest, evidence_root=tmp_path / "runtime",
        getter=flaky, observed_at="2026-09-13T02:01:00+00:00",
        manifest_validator=lambda value: deepcopy(dict(value)), allowed_evidence_roots=[tmp_path],
    )
    receipt = build_reconciliation_receipt(
        attempt=attempt, manifest=manifest, recovery_authorization=manifest,
        official_readback=readback,
        source_files={"attempt": _sources["attempt"], "manifest": _sources["manifest"]},
        allowed_evidence_roots=[tmp_path],
    )
    assert receipt["result"] == "UNKNOWN"
    assert all(value == 1 for value in counts.values())
    assert len(counts) == 10


def test_continuation_is_derived_from_validated_terminal_receipt(tmp_path):
    receipt, _attempt, manifest, _readback, _sources = build(tmp_path, ("MATCH", "DIFF"))
    identity = continuation_identity(
        receipt, run_id="recovery-run", report_id="publication-report:recovery-run",
        manifest_digest=manifest["manifest_digest"], allowed_evidence_roots=[tmp_path],
        target_scope=["shopee:TH"],
        action_budgets={"shopee:TH": ["update_copy_and_description_media"]},
    )
    assert identity["receipt_digest"] == receipt["receipt_digest"]
    with pytest.raises(ShopeeRecoveryReconciliationError, match="scope conflicts"):
        continuation_identity(
            receipt, run_id="recovery-run", report_id="publication-report:recovery-run",
            manifest_digest=manifest["manifest_digest"], allowed_evidence_roots=[tmp_path],
            target_scope=["shopee:MY"], action_budgets={"shopee:MY": ["list_existing_item"]},
        )


def test_description_only_and_gallery_action_names_match_executor(tmp_path):
    receipt, _attempt, manifest, _readback, _sources = build(tmp_path, ("MATCH", "DIFF"))
    changed = deepcopy(receipt)
    changed["new_manifest"]["exact_remaining_differences"][0]["differences"] = [
        "description.images", "gallery.images", "item.status"
    ]
    # Self-edited gate is rejected before action mapping is considered.
    with pytest.raises(ShopeeRecoveryReconciliationError):
        continuation_identity(
            changed, run_id="recovery-run", report_id="publication-report:recovery-run",
            manifest_digest=manifest["manifest_digest"], allowed_evidence_roots=[tmp_path],
            target_scope=["shopee:TH"], action_budgets={"shopee:TH": [
                "update_description_media", "update_images_existing_media", "list_existing_item"
            ]},
        )


def test_official_adapter_rejects_every_identity_drift_and_uses_get_seams_only(tmp_path, monkeypatch):
    _attempt, manifest, _readback, _sources = bundle(tmp_path)
    target = manifest["targets"][0]
    calls = []

    class Runtime:
        def context(self, region):
            row = next(value for value in manifest["targets"] if value["region"] == region)
            return SimpleNamespace(shop_id=int(row["shop_id"]), merchant_id=9,
                                   shop_token="s", merchant_token="m")

        def regional_item(self, context, item_id):
            calls.append(("regional_item", item_id)); return None

        def regional_models(self, context, item_id):
            calls.append(("regional_models", item_id)); return []

        def resolved_global_item_id(self, context, item_id):
            calls.append(("resolved_global_item_id", item_id)); return target["global_item_id"]

        def global_models(self, context, global_item_id):
            calls.append(("global_models", global_item_id)); return []

    monkeypatch.setattr("modules.shopee.client.merchant_get", lambda *args, **kwargs: (
        calls.append(("merchant_get", args[0])) or {"response": {"global_item_list": []}}
    ))
    getter = OfficialShopeeRecoveryGetter(manifest, Runtime())
    query = {key: str(target[key]) for key in (
        "shop_id", "item_id", "global_item_id", "model_id", "global_model_id", "model_sku"
    )}
    for resource in ("item", "models", "global_linkage", "global_item", "global_models"):
        getter(resource, query)
    assert [row[0] for row in calls] == [
        "regional_item", "regional_models", "resolved_global_item_id", "merchant_get", "global_models"
    ]
    before = list(calls)
    for key in query:
        drift = dict(query); drift[key] = "999"
        with pytest.raises(ShopeeRecoveryReconciliationError, match="official GET"):
            getter("item", drift)
    assert calls == before


def test_terminal_receipt_closes_exact_original_domain_operation(monkeypatch, tmp_path):
    from shared_platform.operations_domain_guard import (
        _publication_operation_id, finish_snapshot_recovery_reconciliation,
    )
    completed = []

    class Engine:
        def complete_domain_operation(self, operation_id, *, provider_readback_ref):
            completed.append((operation_id, provider_readback_ref))

    class Release:
        def approved_publication_snapshot(self, **kwargs):
            return {"plan_id": "plan", "publication_targets": [
                {"target_label": "shopee:MY"}, {"target_label": "shopee:TH"}]}

    monkeypatch.setattr("shared_platform.operations_domain_guard.engine_for", lambda root: Engine())
    receipt, attempt, manifest, _readback, _sources = build(tmp_path, ("MATCH", "DIFF"))
    retry = {"run_id": attempt["run_id"], "retry_of_run_id": manifest["prior_run_id"],
             "source_evidence_digest": manifest["prior_report_digest"]}
    result = finish_snapshot_recovery_reconciliation(
        Release(), attempt["offer_id"], manifest["execution_snapshot_digest"], ".",
        target_scope=["shopee:MY", "shopee:TH"], retry_attempt=retry, receipt=receipt,
        run_id=attempt["run_id"], report_id=attempt["report_id"],
        manifest_digest=manifest["manifest_digest"], allowed_evidence_roots=[tmp_path])
    assert result["operation_id"] == _publication_operation_id(
        "plan", ["shopee:MY", "shopee:TH"], retry)
    assert completed[0][1].startswith("shopee-recovery-reconciliation:" + receipt["receipt_digest"].removeprefix("sha256:"))
    for key in retry:
        changed = dict(retry); changed[key] = "wrong"
        before = len(completed)
        with pytest.raises(ValueError, match="domain attempt conflicts"):
            finish_snapshot_recovery_reconciliation(
                Release(), attempt["offer_id"], manifest["execution_snapshot_digest"], ".",
                target_scope=["shopee:MY", "shopee:TH"], retry_attempt=changed,
                receipt=receipt, run_id=attempt["run_id"], report_id=attempt["report_id"],
                manifest_digest=manifest["manifest_digest"], allowed_evidence_roots=[tmp_path])
        assert len(completed) == before
    for offer_id, snapshot_digest, scope in (
        ("other-offer", manifest["execution_snapshot_digest"], ["shopee:MY", "shopee:TH"]),
        (attempt["offer_id"], "sha256:" + "9" * 64, ["shopee:MY", "shopee:TH"]),
        (attempt["offer_id"], manifest["execution_snapshot_digest"], ["shopee:TH"]),
        (attempt["offer_id"], manifest["execution_snapshot_digest"], ["shopee:TH", "shopee:MY"]),
    ):
        before = len(completed)
        with pytest.raises(ValueError, match="scope|coordination"):
            finish_snapshot_recovery_reconciliation(
                Release(), offer_id, snapshot_digest, ".", target_scope=scope,
                retry_attempt=retry, receipt=receipt, run_id=attempt["run_id"],
                report_id=attempt["report_id"], manifest_digest=manifest["manifest_digest"],
                allowed_evidence_roots=[tmp_path])
        assert len(completed) == before
    forged = deepcopy(receipt); forged["result"] = "CONVERGED"
    with pytest.raises(ShopeeRecoveryReconciliationError):
        finish_snapshot_recovery_reconciliation(
            Release(), attempt["offer_id"], manifest["execution_snapshot_digest"], ".",
            target_scope=["shopee:MY"], retry_attempt=retry,
            receipt=forged, run_id=attempt["run_id"], report_id=attempt["report_id"],
            manifest_digest=manifest["manifest_digest"], allowed_evidence_roots=[tmp_path])


def test_server_reconciliation_default_off_is_zero_write(monkeypatch):
    from modules.products import server
    monkeypatch.delenv("ORBIT_SHOPEE_RECOVERY_ENABLED", raising=False)
    status, body = server._reconcile_shopee_recovery({
        "run_id": "run", "recovery_manifest_digest": "sha256:" + "a" * 64})
    assert status == 409
    assert body["external_write_count"] == 0


@pytest.mark.parametrize("states,expected,closed", [
    (("MATCH", "UNKNOWN"), "UNKNOWN", False),
    (("MATCH", "DIFF"), "REMAINING_DIFF", True),
])
def test_server_enabled_path_loads_durable_files_and_only_terminal_closes(
        tmp_path, monkeypatch, states, expected, closed):
    from modules.products import server
    attempt, manifest, source_readback, _sources = bundle(tmp_path / "seed", states)
    reports_root = tmp_path / "reports" / "product-publication"
    report_path = reports_root / "attempt.json"
    report_path.parent.mkdir(parents=True)
    attempt["release_authorization"] = {"candidate_digest": "c" * 64, "approval_digest": "d" * 64}
    attempt["report_path"] = str(report_path)
    report_path.write_text(json.dumps(attempt), encoding="utf-8")
    preparation = reports_root.parent / "product-preparation"
    manifest_path = preparation / attempt["offer_id"] / "shopee-recovery-candidates" / (
        manifest["manifest_digest"].removeprefix("sha256:") + ".json")
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    operations = tmp_path / "operations"; operations.mkdir()

    class Reports:
        def __init__(self): self.reports_root = reports_root
        def get_report_by_run(self, *, run_id):
            assert run_id == attempt["run_id"]
            return deepcopy(attempt)

    class Release:
        def approved_publication_snapshot(self, **kwargs): return {"snapshot_digest": kwargs["snapshot_digest"]}

    getter_calls = []
    source_getter = _getter_from_bundle(source_readback)
    def counted(resource, query):
        getter_calls.append((resource, query["shop_id"])); return source_getter(resource, query)
    closed_calls = []
    monkeypatch.setenv("ORBIT_SHOPEE_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("ORBIT_OPERATIONS_DATA_ROOT", str(operations))
    monkeypatch.setattr(server, "_product_publication_report_store", lambda: Reports())
    monkeypatch.setattr(server, "_release_store", lambda: Release())
    monkeypatch.setattr("shared_platform.publication_autopilot.load_release_candidate", lambda *a, **k: {})
    monkeypatch.setattr("shared_platform.publication_autopilot.load_final_approval_receipt", lambda *a, **k: {})
    monkeypatch.setattr("shared_platform.shopee_regional_recovery.validate_recovery_manifest",
                        lambda value, **kwargs: deepcopy(dict(value)))
    monkeypatch.setattr("shared_platform.shopee_recovery_coordination.OfficialShopeeRecoveryGetter",
                        lambda manifest, runtime: counted)
    monkeypatch.setattr("shared_platform.operations_domain_guard.finish_snapshot_recovery_reconciliation",
                        lambda *a, **k: closed_calls.append(k))
    status, body = server._reconcile_shopee_recovery({
        "run_id": attempt["run_id"], "recovery_manifest_digest": manifest["manifest_digest"]})
    assert status == 200 and body["result"] == expected and body["external_write_count"] == 0
    assert len(getter_calls) == len(manifest["target_labels"]) * 5
    assert bool(closed_calls) is closed
    if closed:
        assert closed_calls[0]["receipt"]["result"] == expected
