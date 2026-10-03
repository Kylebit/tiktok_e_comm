"""S03 uses synthetic roots, real processes and actual authority consumers."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from shared_platform import publication_autopilot as authority
from shared_platform.publication_quality_evidence import load_publication_quality_evidence, PublicationQualityEvidenceError
from test_b4b_release_compiler import policy, INCIDENTS
from test_b4b_ozon_partial_budget import ozon_snapshot


def packet():
    snapshot = ozon_snapshot()
    candidate = authority.compile_release_candidate(
        snapshot, policy=policy(3), incident_registry=INCIDENTS, platform_scope=("OZON",)
    )
    assert candidate["status"] == "READY_FOR_FINAL_REVIEW", candidate["blockers"]
    approval = authority.build_final_approval_receipt(candidate, approved_by="Kyle")
    return snapshot, candidate, approval


def encoded(document):
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def worker(tmp_path, name, *, root, candidate, approval, kind, **options):
    config_path = tmp_path / (name + ".json")
    marker = tmp_path / (name + ".marker")
    config_path.write_text(json.dumps({
        "root": str(root), "candidate": candidate, "approval": approval,
        "kind": kind, "marker": str(marker), **options,
    }), encoding="utf-8")
    runner = os.environ.get("S03_BOUND_RUNNER")
    if runner:
        command = [sys.executable, "-I", "-S", "-B", runner, "--worker", str(config_path)]
    else:
        command = [sys.executable, "-B", str(Path(__file__).parent / "helpers/s03_authority_worker.py"), str(config_path)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    return process, marker


def await_marker(process, marker):
    deadline = time.monotonic() + 20
    while not marker.exists():
        if process.poll() is not None:
            raise AssertionError(process.communicate())
        if time.monotonic() >= deadline:
            process.kill()
            raise AssertionError("worker did not reach its file boundary")
        time.sleep(0.01)


@pytest.mark.parametrize("kind", ["candidate", "approval"])
def test_interrupted_persist_never_exposes_partial_final_and_restart_succeeds(tmp_path, kind):
    snapshot, candidate, approval = packet()
    root = tmp_path / "reports"
    if kind == "approval":
        authority.persist_release_candidate(candidate, reports_root=root)
    final = (authority.release_candidate_path if kind == "candidate" else authority.final_approval_receipt_path)(candidate, reports_root=root)
    process, marker = worker(tmp_path, "interrupted", root=root, candidate=candidate, approval=approval, kind=kind, interrupt=True)
    try:
        await_marker(process, marker)
        observed = json.loads(marker.read_text(encoding="utf-8"))
        process.kill()
        process.communicate(timeout=10)
        partial = Path(observed["partial_path"]).read_bytes()
        (tmp_path / "preserved-partial.bin").write_bytes(partial)
        full = encoded(candidate if kind == "candidate" else approval)
        assert observed["expected_sha256"] == hashlib.sha256(full).hexdigest()
        assert partial and partial != full
        assert not final.exists(), "a killed writer exposed partial final authority"
        if kind == "candidate":
            authority.persist_release_candidate(candidate, reports_root=root)
        else:
            authority.persist_final_approval_receipt(approval, candidate, reports_root=root)
        assert final.read_bytes() == full
        assert (tmp_path / "preserved-partial.bin").read_bytes() == partial
        (tmp_path / "interruption-observation.json").write_text(json.dumps({
            **observed, "partial_sha256": hashlib.sha256(partial).hexdigest(),
            "exit_code": process.returncode, "partial_final_exposed": False,
            "restarted_final_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        }), encoding="utf-8")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize("kind", ["candidate", "approval"])
@pytest.mark.parametrize("different", [False, True])
def test_real_process_competition_keeps_digest_identity(tmp_path, kind, different):
    _snapshot, candidate, approval = packet()
    candidate2, approval2 = deepcopy(candidate), deepcopy(approval)
    if different:
        if kind == "candidate":
            candidate2["fixture_note"] = "another immutable packet"
            candidate2.pop("candidate_digest")
            candidate2["candidate_digest"] = authority._canonical_digest(candidate2)
        else:
            approval2["approved_at"] = "2026-09-06T00:00:00+00:00"
            approval2.pop("approval_digest")
            approval2["approval_digest"] = authority._canonical_digest(approval2)
    root, barrier = tmp_path / "reports", tmp_path / "barrier"
    workers = [worker(tmp_path, str(i), root=root, candidate=c, approval=a, kind=kind, barrier=str(barrier))
               for i, (c, a) in enumerate(((candidate, approval), (candidate2, approval2)))]
    try:
        for proc, marker in workers:
            await_marker(proc, marker)
        barrier.write_text("go", encoding="utf-8")
        results = [proc.communicate(timeout=20) for proc, _marker in workers]
        codes = sorted(proc.returncode for proc, _marker in workers)
        assert codes == ([0, 3] if different and kind == "approval" else [0, 0]), results
        documents = [candidate, candidate2] if kind == "candidate" else [approval, approval2]
        paths = {(authority.release_candidate_path if kind == "candidate" else authority.final_approval_receipt_path)(c, reports_root=root)
                 for c in (candidate, candidate2)}
        for path in paths:
            assert path.read_bytes() in [encoded(document) for document in documents]
        (tmp_path / "competition-observation.json").write_text(json.dumps({
            "kind": kind, "different": different, "exit_codes": codes, "worker_outputs": results,
            "finals": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        }), encoding="utf-8")
    finally:
        for proc, _marker in workers:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)


@pytest.mark.parametrize("mode", ["file_link", "directory_link", "dangling", "malformed", "root_link", "foreign_offer", "junction"])
def test_quality_sidecar_rejects_unsafe_or_corrupt_before_read(tmp_path, monkeypatch, mode):
    reports = tmp_path / "reports"
    folder = reports / "123456"
    folder.mkdir(parents=True)
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text('{"synthetic_sentinel":true}', encoding="utf-8")
    target = folder / "workflow-handoff.json"
    if mode == "malformed":
        target.write_text("{partial", encoding="utf-8")
    elif mode == "foreign_offer":
        target.write_text('{"offer_id":"999999"}', encoding="utf-8")
    elif mode == "root_link":
        folder.rmdir()
        reports.rmdir()
        reports.symlink_to(tmp_path, target_is_directory=True)
    elif mode in {"directory_link", "junction"}:
        folder.rmdir()
        (tmp_path / "workflow-handoff.json").write_text('{"synthetic_sentinel":true}', encoding="utf-8")
        if mode == "junction":
            if os.name != "nt":
                pytest.skip("Windows junction surface only")
            import _winapi
            _winapi.CreateJunction(str(tmp_path), str(folder))
        else:
            folder.symlink_to(tmp_path, target_is_directory=True)
    else:
        target.symlink_to(sentinel if mode == "file_link" else tmp_path / "absent")
    reads = []
    original = Path.open
    def observe(path, *args, **kwargs):
        if path == target:
            reads.append(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observe)
    with pytest.raises(PublicationQualityEvidenceError, match="sidecar") as caught:
        load_publication_quality_evidence("123456", reports_root=reports)
    assert caught.value.code == ({"malformed": "MALFORMED", "foreign_offer": "OFFER_ID_CONFLICT"}.get(mode, "UNSAFE_PATH"))
    if mode not in {"malformed", "foreign_offer"}:
        assert reads == []
    (tmp_path / "sidecar-read-observation.json").write_text(json.dumps({
        "mode": mode, "error_code": caught.value.code, "observed_opens": reads,
        "sidecar": str(target), "synthetic_sentinel": str(sentinel),
    }), encoding="utf-8")


def test_quality_legal_missing_remains_unavailable(tmp_path):
    assert load_publication_quality_evidence("123456", reports_root=tmp_path) is None


@pytest.mark.parametrize("kind", ["candidate", "approval"])
def test_original_evidence_recovery_preserves_approval_and_real_consumer(tmp_path, kind):
    from shared_platform.immutable_approval_files import inspect_approval_recovery, recover_approval_file
    from shared_platform.product_publication_runner import ProductPublicationRunner
    from test_product_publication_runner import _SnapshotStore, _report_store
    snapshot, candidate, approval = packet()
    root = tmp_path / "reports"
    authority.persist_release_candidate(candidate, reports_root=root)
    authority.persist_final_approval_receipt(approval, candidate, reports_root=root)
    path = (authority.release_candidate_path if kind == "candidate" else authority.final_approval_receipt_path)(candidate, reports_root=root)
    evidence = tmp_path / "original-evidence.json"
    full = path.read_bytes()
    evidence.write_bytes(full)
    damaged = full[:31]
    path.write_bytes(damaged)
    kwargs = dict(kind=kind, candidate=candidate, reports_root=root)
    before = sorted(str(x) for x in tmp_path.rglob("*"))
    blocked = inspect_approval_recovery(**kwargs)
    assert blocked["status"] == "CORRUPT_BLOCKED"
    assert blocked["missing_evidence"] == ["evidence_path", "evidence_root", "expected_evidence_sha256", "expected_document_digest"]
    assert sorted(str(x) for x in tmp_path.rglob("*")) == before
    with pytest.raises(ValueError):
        authority.resolve_persisted_execution_authority(snapshot=snapshot, platform_scope=("OZON",), target_labels=("ozon:RU",), reports_root=root)
    kwargs.update(evidence_path=evidence, evidence_root=tmp_path,
                  expected_evidence_sha256=hashlib.sha256(full).hexdigest(),
                  expected_document_digest=(candidate["candidate_digest"] if kind == "candidate" else approval["approval_digest"]))
    preview = inspect_approval_recovery(**kwargs)
    assert preview["status"] == "RECOVERABLE_SAME_CONTENT"
    assert preview["writes_performed"] == []
    assert path.read_bytes() == damaged
    with pytest.raises(ValueError, match="changed since inspect"):
        recover_approval_file(expected_damaged_sha256="0" * 64, **kwargs)
    assert path.read_bytes() == damaged
    result = recover_approval_file(expected_damaged_sha256=hashlib.sha256(damaged).hexdigest(), **kwargs)
    assert result["status"] == "RESTORED_ORIGINAL_BYTES"
    (tmp_path / "recovery-observation.json").write_text(json.dumps(result), encoding="utf-8")
    assert Path(result["backup"]).read_bytes() == damaged
    assert path.read_bytes() == full
    assert authority.load_final_approval_receipt(candidate, reports_root=root) == approval
    resolved = authority.resolve_persisted_execution_authority(snapshot=snapshot, platform_scope=("OZON",), target_labels=("ozon:RU",), reports_root=root)
    assert resolved == (candidate, approval)
    reports = _report_store(tmp_path / "consumer")
    calls = []
    def execute(request):
        calls.append(request)
        return {"schema_version": "product-publication-platform-result/v1", "platform": "OZON",
                "targets": [{"target_label": "ozon:RU", "status": "FAILED"}],
                "dispatch_attempted": False, "readback_completed": False,
                "external_write_count": 0, "requires_human_action": True}
    runner = ProductPublicationRunner(release_store=_SnapshotStore(snapshot), report_store=reports)
    result = runner.run(run_id="restored-original", offer_id=snapshot["offer_id"], plan_id=snapshot["plan_id"],
                        platform_scope=("OZON",), platform_executors={"OZON": execute},
                        release_candidate=resolved[0], final_approval=resolved[1])
    assert len(calls) == 1
    assert result.report["release_authorization"] == {"candidate_digest": candidate["candidate_digest"], "approval_digest": approval["approval_digest"]}


@pytest.mark.parametrize("drift", ["evidence_hash", "document_digest", "foreign_candidate", "complete_conflict"])
def test_recovery_cannot_create_or_replace_approval_authority(tmp_path, drift):
    from shared_platform.immutable_approval_files import inspect_approval_recovery, recover_approval_file
    _snapshot, candidate, approval = packet()
    root = tmp_path / "reports"
    authority.persist_release_candidate(candidate, reports_root=root)
    path = authority.persist_final_approval_receipt(approval, candidate, reports_root=root)
    evidence = tmp_path / "original.json"
    evidence.write_bytes(path.read_bytes())
    path.write_bytes(b"{partial")
    kwargs = dict(kind="approval", candidate=candidate, reports_root=root, evidence_path=evidence, evidence_root=tmp_path,
                  expected_evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(), expected_document_digest=approval["approval_digest"])
    if drift == "evidence_hash":
        kwargs["expected_evidence_sha256"] = "0" * 64
    elif drift == "document_digest":
        kwargs["expected_document_digest"] = "0" * 64
    elif drift == "foreign_candidate":
        kwargs["candidate"] = {**candidate, "plan_id": "foreign"}
    else:
        other = {**approval, "approved_at": "2026-09-06T00:00:00+00:00"}
        other.pop("approval_digest")
        other["approval_digest"] = authority._canonical_digest(other)
        path.write_bytes(encoded(other))
        assert inspect_approval_recovery(**kwargs)["status"] == "COMPLETE_CONFLICT_BLOCKED"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        recover_approval_file(expected_damaged_sha256=hashlib.sha256(before).hexdigest(), **kwargs)
    assert path.read_bytes() == before


def test_recovery_cli_defaults_to_read_only_inspect_in_real_process(tmp_path):
    _snapshot, candidate, approval = packet()
    root = tmp_path / "reports"
    path = authority.persist_release_candidate(candidate, reports_root=root)
    path.write_bytes(b"{partial")
    before = sorted(str(x) for x in root.rglob("*"))
    proc, _marker = worker(tmp_path, "cli", root=root, candidate=candidate, approval=approval, kind="candidate",
                          action="recovery_cli", argv=["--kind", "candidate", "--reports-root", str(root),
                          "--offer-id", candidate["offer_id"], "--candidate-digest", candidate["candidate_digest"]])
    stdout, stderr = proc.communicate(timeout=20)
    assert proc.returncode == 0, stderr
    assert json.loads(stdout)["status"] == "CORRUPT_BLOCKED"
    assert sorted(str(x) for x in root.rglob("*")) == before
    assert path.read_bytes() == b"{partial"


@pytest.mark.parametrize("kind", ["candidate", "approval"])
def test_crash_after_atomic_publication_replays_exact_file(tmp_path, kind):
    _snapshot, candidate, approval = packet()
    root = tmp_path / "reports"
    proc, marker = worker(tmp_path, "after-link", root=root, candidate=candidate, approval=approval,
                          kind=kind, interrupt_after_link=True)
    try:
        await_marker(proc, marker)
        proc.kill()
        proc.communicate(timeout=10)
        path = Path(json.loads(marker.read_text(encoding="utf-8"))["published_path"])
        expected = encoded(candidate if kind == "candidate" else approval)
        assert path.read_bytes() == expected
        before = path.stat().st_ino
        if kind == "candidate":
            authority.persist_release_candidate(candidate, reports_root=root)
        else:
            authority.persist_final_approval_receipt(approval, candidate, reports_root=root)
        assert path.read_bytes() == expected
        assert path.stat().st_ino == before
        (tmp_path / "published-crash-observation.json").write_text(json.dumps({
            "path": str(path), "pid": proc.pid, "exit_code": proc.returncode,
            "before_inode": before, "after_inode": path.stat().st_ino,
            "replayed_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }), encoding="utf-8")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


@pytest.mark.parametrize("failure", ["fsync", "link"])
def test_atomic_failure_preserves_primary_error_and_never_exposes_final(tmp_path, monkeypatch, failure):
    import errno
    from shared_platform import immutable_approval_files as files
    _snapshot, candidate, _approval = packet()
    root = tmp_path / "reports"
    error = OSError(errno.EXDEV if failure == "link" else errno.EIO, "synthetic persistence failure")
    def fail(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(files.os, failure if failure == "fsync" else "link", fail)
    with pytest.raises(OSError) as caught:
        authority.persist_release_candidate(candidate, reports_root=root)
    assert caught.value is error
    path = authority.release_candidate_path(candidate, reports_root=root)
    assert not path.exists()
    assert len(list(path.parent.glob(".authority-*.tmp"))) == 1
