"""Behavioral recovery fixtures: no live clients, credentials or provider requests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.sourcing import image_generation_checkpoint as cp
from modules.sourcing import localized_image_lingshi_generation as localized
from modules.sourcing import brand_image_lingshi_generation as brand
from modules.sourcing.lingshi_client import LingshiClientError


def png(color="white"):
    output = BytesIO()
    Image.new("RGB", (16, 16), color).save(output, format="PNG")
    return output.getvalue()


class Client:
    def __init__(self, *, failure=None, poll_failure=None, task_id=42):
        self.failure, self.poll_failure, self.task_id = failure, poll_failure, task_id
        self.created, self.queried = [], []

    def create_media_generation(self, **kwargs):
        self.created.append(kwargs)
        if self.failure:
            raise self.failure
        return {"code": 200, "data": {"task_id": self.task_id}}

    def get_media_task(self, task_id):
        self.queried.append(task_id)
        if self.poll_failure:
            raise self.poll_failure
        return {"is_final": True, "result_url": "https://assets.example/result.png"}


def args(tmp_path, client):
    return dict(source_url="https://assets.example/source.png", source_bytes=png(), locale="th-TH",
                translations=[{"source_text": "CLEAN", "translated_text": "สะอาด"}],
                checkpoint_dir=tmp_path, client=client, result_loader=lambda _url: png())


def path_for(tmp_path):
    return next(tmp_path.glob("lingshi-localized-v2-*.json"))


def evidence(path, outcome="task_verified_for_request", task_id=42):
    return {**cp.inspect_image_checkpoint(path), "outcome": outcome, "task_id": task_id,
            "charge_status": "none" if outcome == "no_task_no_charge" else "previous_task_recorded",
            "verified_at": datetime.now(timezone.utc).isoformat(), "verified_by": "fixture-upstream-reviewer",
            "evidence_ref": "fixture://provider-reconciliation/42", "evidence_sha256": "sha256:" + "a" * 64}


def unknown(tmp_path):
    client = Client(failure=LingshiClientError("fixture submission timeout"))
    kwargs = args(tmp_path, client)
    with pytest.raises(LingshiClientError):
        localized.generate_localized_reference_image(**kwargs)
    return client, kwargs, path_for(tmp_path)


def test_attach_verified_task_only_queries_and_does_not_create(tmp_path):
    client, kwargs, path = unknown(tmp_path)
    after = cp.reconcile_image_checkpoint(path, evidence=evidence(path))
    assert after["status"] == "SUBMITTED" and after["task_id"] == 42
    assert len(client.created) == 1 and client.queried == []
    result = localized.generate_localized_reference_image(**kwargs)
    assert result["receipt"]["task_id"] == 42
    assert len(client.created) == 1 and client.queried == [42]


def test_no_task_no_charge_requires_explicit_matching_next_attempt(tmp_path):
    client, kwargs, path = unknown(tmp_path)
    proof = evidence(path, "no_task_no_charge", None)
    after = cp.reconcile_image_checkpoint(path, evidence=proof)
    assert after["status"] == "READY" and after["attempt"] == 1
    assert len(client.created) == 1
    with pytest.raises(RuntimeError, match="attempt number"):
        localized.generate_localized_reference_image(**kwargs)
    client.failure = None
    result = localized.generate_localized_reference_image(**kwargs, retry_attempt=1)
    assert result["receipt"]["retry_attempt"] == 1 and len(client.created) == 2
    with pytest.raises(ValueError, match="current checkpoint"):
        cp.reconcile_image_checkpoint(path, evidence=proof)


@pytest.mark.parametrize("field", ["business_digest", "request_digest", "provider", "checkpoint_digest",
    "identity_digest", "journal_digest", "revision", "attempt", "status", "known_task_ids",
    "output_digest", "legacy_checkpoint_digests", "verified_at", "evidence_sha256", "evidence_ref"])
def test_recovery_rejects_wrong_or_stale_evidence_without_mutating_state(tmp_path, field):
    client, kwargs, path = unknown(tmp_path)
    proof = evidence(path)
    proof[field] = "" if field in {"verified_at", "evidence_sha256", "evidence_ref"} else "wrong-request"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        cp.reconcile_image_checkpoint(path, evidence=proof)
    assert path.read_bytes() == before
    assert len(client.created) == 1


def test_verified_boolean_alone_is_not_reconciliation(tmp_path):
    client, kwargs, path = unknown(tmp_path)
    with pytest.raises(ValueError):
        cp.reconcile_image_checkpoint(path, evidence={"verified": True})
    assert len(client.created) == 1


def test_known_task_cannot_be_erased_by_no_charge_claim(tmp_path):
    client = Client(poll_failure=TimeoutError("fixture poll timeout"))
    kwargs = args(tmp_path, client)
    with pytest.raises(TimeoutError):
        localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    with pytest.raises(ValueError, match="known task"):
        cp.reconcile_image_checkpoint(path, evidence=evidence(path, "no_task_no_charge", None))
    with pytest.raises(ValueError, match="conflicts"):
        cp.reconcile_image_checkpoint(path, evidence=evidence(path, task_id=999))
    client.poll_failure = None
    localized.generate_localized_reference_image(**kwargs)
    assert len(client.created) == 1 and client.queried == [42, 42]


def test_task_id_takes_priority_over_non_success_response_envelope(tmp_path):
    class NonSuccessClient(Client):
        def create_media_generation(self, **kwargs):
            self.created.append(kwargs)
            return {"code": 500, "data": {"task_id": "42"}}
    client = NonSuccessClient(poll_failure=TimeoutError("fixture read failure"))
    kwargs = args(tmp_path, client)
    with pytest.raises(TimeoutError):
        localized.generate_localized_reference_image(**kwargs)
    assert cp.inspect_image_checkpoint(path_for(tmp_path))["task_id"] == 42
    client.poll_failure = None
    localized.generate_localized_reference_image(**kwargs)
    assert len(client.created) == 1 and client.queried == [42, 42]


def test_structured_rejection_does_not_persist_provider_prose_or_authorize_retry(tmp_path):
    class RejectedClient(Client):
        def create_media_generation(self, **kwargs):
            self.created.append(kwargs)
            return {"code": 422, "msg": "fixture-private-provider-prose"}
    client = RejectedClient()
    kwargs = args(tmp_path, client)
    with pytest.raises(cp.ImageSubmissionRejected):
        localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    assert cp.inspect_image_checkpoint(path)["status"] == "REJECTED_BEFORE_TASK"
    assert b"fixture-private-provider-prose" not in path.read_bytes() + path.with_suffix(".events.jsonl").read_bytes()
    with pytest.raises(cp.CheckpointRecoveryRequired):
        localized.generate_localized_reference_image(**kwargs, retry_attempt=1)
    assert len(client.created) == 1


@pytest.mark.parametrize("status", ["SUBMITTED", "COMPLETED", "SUBMISSION_UNKNOWN"])
def test_legacy_v1_task_requires_binding_then_resumes_without_replacing_old_files(tmp_path, status):
    old = tmp_path / ("lingshi-" + "a" * 64 + ".json")
    old.write_text(json.dumps({"status": status, "task_id": 42, "identity_digest": "old-v1"}), encoding="utf-8")
    old_output = old.with_suffix(".png")
    old_output.write_bytes(png("red"))
    saved = old.read_bytes(), old_output.read_bytes()
    client = Client()
    kwargs = args(tmp_path, client)
    with pytest.raises(cp.CheckpointRecoveryRequired, match="legacy v1"):
        localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    assert not client.created
    cp.reconcile_image_checkpoint(path, evidence=evidence(path))
    result = localized.generate_localized_reference_image(**kwargs)
    assert result["image_bytes"] != saved[1]
    assert not client.created and client.queried == [42]
    assert (old.read_bytes(), old_output.read_bytes()) == saved


def test_legacy_unknown_without_task_requires_no_task_no_charge_evidence(tmp_path):
    old = tmp_path / "lingshi-old.json"
    old.write_text('{"status":"SUBMISSION_UNKNOWN","task_id":null}', encoding="utf-8")
    client = Client()
    kwargs = args(tmp_path, client)
    with pytest.raises(cp.CheckpointRecoveryRequired):
        localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    cp.reconcile_image_checkpoint(path, evidence=evidence(path, "no_task_no_charge", None))
    result = localized.generate_localized_reference_image(**kwargs, retry_attempt=1)
    assert result["receipt"]["retry_attempt"] == 1 and len(client.created) == 1
    assert old.read_text(encoding="utf-8").startswith('{"status":"SUBMISSION_UNKNOWN"')


def test_legacy_receipt_task_id_cannot_be_lost_when_top_level_task_is_missing(tmp_path):
    old = tmp_path / "lingshi-old.json"
    old.write_text('{"status":"COMPLETED","receipt":{"task_id":42}}', encoding="utf-8")
    client = Client()
    kwargs = args(tmp_path, client)
    with pytest.raises(cp.CheckpointRecoveryRequired):
        localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    with pytest.raises(ValueError, match="known task"):
        cp.reconcile_image_checkpoint(path, evidence=evidence(path, "no_task_no_charge", None))
    cp.reconcile_image_checkpoint(path, evidence=evidence(path))
    localized.generate_localized_reference_image(**kwargs)
    assert client.created == [] and client.queried == [42]


def test_corrupt_checkpoint_is_preserved_and_explicitly_repaired(tmp_path):
    client, kwargs, path = unknown(tmp_path)
    broken = b'{"partial record"'
    path.write_bytes(broken)
    with pytest.raises(cp.CheckpointRecoveryRequired):
        localized.generate_localized_reference_image(**kwargs)
    assert path.read_bytes() == broken and len(client.created) == 1
    assert cp.inspect_image_checkpoint(path)["record_valid"] is False
    cp.reconcile_image_checkpoint(path, evidence=evidence(path))
    localized.generate_localized_reference_image(**kwargs)
    assert broken in [item.read_bytes() for item in tmp_path.glob("preserved-*.blob")]
    assert len(client.created) == 1 and client.queried == [42]


def test_partial_journal_keeps_known_task_and_requires_recovery(tmp_path):
    client = Client(poll_failure=TimeoutError("fixture read timeout"))
    kwargs = args(tmp_path, client)
    with pytest.raises(TimeoutError):
        localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    journal = path.with_suffix(".events.jsonl")
    journal.write_bytes(journal.read_bytes() + b'{"partial')
    with pytest.raises(cp.CheckpointRecoveryRequired):
        localized.generate_localized_reference_image(**kwargs)
    assert cp.inspect_image_checkpoint(path)["known_task_ids"] == [42]
    with pytest.raises(ValueError, match="known task"):
        cp.reconcile_image_checkpoint(path, evidence=evidence(path, "no_task_no_charge", None))
    cp.reconcile_image_checkpoint(path, evidence=evidence(path))
    client.poll_failure = None
    localized.generate_localized_reference_image(**kwargs)
    assert len(client.created) == 1


@pytest.mark.parametrize("status, expected_creates", [("SUBMITTING", 0), ("SUBMITTED", 1)])
def test_atomic_snapshot_failure_does_not_lose_submission_or_task_identity(tmp_path, monkeypatch, status, expected_creates):
    original = cp.atomic_json
    failed = False
    def fail_once(path, value):
        nonlocal failed
        if value.get("status") == status and not failed:
            failed = True
            raise OSError("fixture atomic replacement failure")
        return original(path, value)
    monkeypatch.setattr(cp, "atomic_json", fail_once)
    client = Client()
    kwargs = args(tmp_path, client)
    with pytest.raises(cp.CheckpointPersistenceError) as error:
        localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    assert len(client.created) == expected_creates
    observed = cp.inspect_image_checkpoint(path)
    assert observed["status"] == status
    if status == "SUBMITTING":
        with pytest.raises(cp.CheckpointRecoveryRequired):
            localized.generate_localized_reference_image(**kwargs)
        cp.reconcile_image_checkpoint(path, evidence=evidence(path, "no_task_no_charge", None))
        kwargs["retry_attempt"] = 1
    else:
        assert error.value.task_id == observed["task_id"] == 42
    localized.generate_localized_reference_image(**kwargs)
    assert len(client.created) == 1


def test_missing_output_fetches_same_task_and_corrupt_output_is_not_overwritten(tmp_path):
    client = Client()
    kwargs = args(tmp_path, client)
    localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    output = path.with_suffix(".png")
    output.unlink()
    localized.generate_localized_reference_image(**kwargs)
    assert len(client.created) == 1 and client.queried == [42, 42]
    output.write_bytes(b"corrupt")
    with pytest.raises(cp.CheckpointRecoveryRequired):
        localized.generate_localized_reference_image(**kwargs)
    assert output.read_bytes() == b"corrupt" and len(client.created) == 1


def test_refetched_completed_output_must_match_recorded_digest_on_every_retry(tmp_path):
    client = Client()
    kwargs = args(tmp_path, client)
    localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    before = path.read_bytes()
    path.with_suffix(".png").unlink()
    kwargs["result_loader"] = lambda _url: png("red")
    for _ in range(2):
        with pytest.raises(cp.CheckpointRecoveryRequired, match="digest"):
            localized.generate_localized_reference_image(**kwargs)
    assert path.read_bytes() == before
    assert len(client.created) == 1


def test_repair_after_attempt_one_keeps_the_attempt_identity(tmp_path):
    client, kwargs, path = unknown(tmp_path)
    cp.reconcile_image_checkpoint(path, evidence=evidence(path, "no_task_no_charge", None))
    client.failure = None
    client.poll_failure = TimeoutError("fixture read failure")
    with pytest.raises(TimeoutError):
        localized.generate_localized_reference_image(**kwargs, retry_attempt=1)
    path.with_suffix(".events.jsonl").write_bytes(path.with_suffix(".events.jsonl").read_bytes() + b"partial")
    assert cp.inspect_image_checkpoint(path)["attempt"] == 1
    recovered = cp.reconcile_image_checkpoint(path, evidence=evidence(path))
    assert recovered["attempt"] == 1
    client.poll_failure = None
    result = localized.generate_localized_reference_image(**kwargs, retry_attempt=1)
    assert result["receipt"]["retry_attempt"] == 1
    assert len(client.created) == 2


@pytest.mark.parametrize("reason", ["qa_rejected", "user_requested_rework"])
def test_authorized_rework_binds_old_output_preserves_it_and_opens_one_next_attempt(tmp_path, reason):
    client = Client()
    kwargs = args(tmp_path, client)
    first = localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    proof = evidence(path, "authorized_rework")
    proof.update(rework_reason=reason, authorization_ref="fixture://approved-rework/1",
                 authorization_sha256="sha256:" + "b" * 64, qa_review_sha256="sha256:" + "c" * 64)
    after = cp.reconcile_image_checkpoint(path, evidence=proof)
    assert after["status"] == "READY" and after["attempt"] == 1 and len(client.created) == 1
    assert list(tmp_path.glob("attempt-*.png"))[0].read_bytes() == first["image_bytes"]
    with pytest.raises(cp.CheckpointRecoveryRequired):
        localized.generate_localized_reference_image(**kwargs)
    client.task_id = 43
    second = localized.generate_localized_reference_image(**kwargs, retry_attempt=1)
    assert len(client.created) == 2 and second["receipt"]["task_id"] == 43
    assert second["receipt"]["client_business_id"] != first["receipt"]["client_business_id"]
    assert second["receipt"]["request_digest"] == first["receipt"]["request_digest"]
    with pytest.raises(ValueError):
        cp.reconcile_image_checkpoint(path, evidence=proof)


def test_prior_success_does_not_erase_unknown_rework_or_block_its_verified_no_charge_recovery(tmp_path):
    client = Client()
    kwargs = args(tmp_path, client)
    localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    proof = evidence(path, "authorized_rework")
    proof.update(rework_reason="user_requested_rework", authorization_ref="audit://rework/1",
                 authorization_sha256="sha256:" + "b" * 64)
    cp.reconcile_image_checkpoint(path, evidence=proof)
    client.failure = LingshiClientError("fixture rework timeout")
    with pytest.raises(LingshiClientError):
        localized.generate_localized_reference_image(**kwargs, retry_attempt=1)
    assert cp.inspect_image_checkpoint(path)["known_task_ids"] == []
    with pytest.raises(cp.CheckpointRecoveryRequired):
        localized.generate_localized_reference_image(**kwargs, retry_attempt=2)
    cp.reconcile_image_checkpoint(path, evidence=evidence(path, "no_task_no_charge", None))
    client.failure, client.task_id = None, 43
    result = localized.generate_localized_reference_image(**kwargs, retry_attempt=2)
    assert result["receipt"]["retry_attempt"] == 2 and result["receipt"]["task_id"] == 43
    assert len(client.created) == 3


def test_unknown_business_blocks_changed_prompt_until_original_task_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(brand, "_download_result", lambda _url: png())
    client = Client(failure=LingshiClientError("fixture timeout"))
    kwargs = dict(offer_id="fixture-product", brand_id="fixture-brand", brand_label="Fixture", positioning="plain",
                  role="cover", brief="verified object", product_identity="verified object", source_urls=["https://assets.example/ref.png"],
                  checkpoint_dir=tmp_path, client=client)
    with pytest.raises(LingshiClientError):
        brand.generate_brand_image(**kwargs)
    original_path = next(tmp_path.glob("lingshi-brand-v2-*.json"))
    with pytest.raises(cp.CheckpointRecoveryRequired, match="same business"):
        brand.generate_brand_image(**{**kwargs, "product_identity": "changed verified object"})
    assert len(client.created) == 1
    cp.reconcile_image_checkpoint(original_path, evidence=evidence(original_path))
    first = brand.generate_brand_image(**kwargs)
    client.failure = None
    second = brand.generate_brand_image(**{**kwargs, "product_identity": "changed verified object"})
    assert len(client.created) == 2 and first["receipt"]["request_digest"] != second["receipt"]["request_digest"]


@pytest.mark.parametrize("field,changed", [("brand_label", "New label"), ("positioning", "new positioning"),
    ("brief", "new verified brief"), ("product_identity", "changed identity")])
def test_entire_brand_prompt_changes_request_identity_after_terminal_completion(tmp_path, monkeypatch, field, changed):
    monkeypatch.setattr(brand, "_download_result", lambda _url: png())
    client = Client()
    kwargs = dict(offer_id="fixture-product", brand_id="fixture-brand", brand_label="Fixture", positioning="plain",
                  role="cover", brief="verified object", product_identity="verified object",
                  source_urls=["https://assets.example/ref.png"], checkpoint_dir=tmp_path, client=client)
    first = brand.generate_brand_image(**kwargs)
    second = brand.generate_brand_image(**{**kwargs, field: changed})
    assert first["receipt"]["request_digest"] != second["receipt"]["request_digest"]
    assert first["receipt"]["business_digest"] == second["receipt"]["business_digest"]
    assert len(client.created) == 2


def test_url_only_provenance_is_explicit_and_supplied_content_digest_is_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(brand, "_download_result", lambda _url: png())
    client = Client()
    kwargs = dict(offer_id="fixture-product", brand_id="fixture-brand", brand_label="Fixture", positioning="plain",
                  role="cover", brief="verified object", product_identity="verified object",
                  source_urls=["https://assets.example/ref.png"], checkpoint_dir=tmp_path, client=client)
    url_only = brand.generate_brand_image(**kwargs)
    with_digest = brand.generate_brand_image(**kwargs, source_url_digests={"https://assets.example/ref.png": "a" * 64})
    changed_digest = brand.generate_brand_image(**kwargs, source_url_digests={"https://assets.example/ref.png": "b" * 64})
    assert url_only["receipt"]["source_identity_complete"] is False
    assert with_digest["receipt"]["source_identity_complete"] is True
    assert len({row["receipt"]["request_digest"] for row in (url_only, with_digest, changed_digest)}) == 3


def test_same_reference_url_with_changed_source_bytes_is_a_new_localization_request(tmp_path):
    client = Client()
    kwargs = args(tmp_path, client)
    first = localized.generate_localized_reference_image(**kwargs)
    second = localized.generate_localized_reference_image(**{**kwargs, "source_bytes": png("red")})
    assert first["receipt"]["business_digest"] == second["receipt"]["business_digest"]
    assert first["receipt"]["request_digest"] != second["receipt"]["request_digest"]
    assert len(client.created) == 2


def test_short_filename_collision_rejects_full_identity_mismatch(tmp_path):
    checkpoint = cp.ImageCheckpoint(tmp_path, kind="localized", business_identity={"product": "fixture"},
                                    request_identity={"prompt": "first"}, model="fixture-model", source_identity_complete=True)
    with cp.business_lock(tmp_path, checkpoint.business_digest):
        checkpoint.open_for_execution()
    checkpoint.request_digest = checkpoint.request_digest[:24] + "b" * 40
    with pytest.raises(cp.CheckpointRecoveryRequired, match="checkpoint"):
        checkpoint.open_for_execution()


def test_missing_checkpoint_still_retains_known_task_from_journal(tmp_path):
    client = Client(poll_failure=TimeoutError("fixture timeout"))
    kwargs = args(tmp_path, client)
    with pytest.raises(TimeoutError):
        localized.generate_localized_reference_image(**kwargs)
    path = path_for(tmp_path)
    path.unlink()
    with pytest.raises(cp.CheckpointRecoveryRequired, match="orphaned"):
        localized.generate_localized_reference_image(**kwargs)
    assert cp.inspect_image_checkpoint(path)["known_task_ids"] == [42]
    cp.reconcile_image_checkpoint(path, evidence=evidence(path))
    client.poll_failure = None
    localized.generate_localized_reference_image(**kwargs)
    assert len(client.created) == 1


def test_malformed_legacy_record_is_kept_and_blocks_submission(tmp_path):
    old = tmp_path / "lingshi-old.json"
    old.write_bytes(b"partial legacy")
    client = Client()
    with pytest.raises(cp.CheckpointRecoveryRequired, match="legacy"):
        localized.generate_localized_reference_image(**args(tmp_path, client))
    assert old.read_bytes() == b"partial legacy" and client.created == []


def test_thread_concurrency_creates_one_task(tmp_path):
    client = Client()
    kwargs = args(tmp_path, client)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: localized.generate_localized_reference_image(**kwargs), range(4)))
    assert len(client.created) == 1 and client.queried == [42]
    assert all(row["receipt"] == results[0]["receipt"] for row in results)


def _child(mode, directory):
    def guard(event, values):
        if event in {"socket.connect", "socket.getaddrinfo", "sqlite3.connect"}:
            raise RuntimeError("offline worker boundary")
    sys.addaudithook(guard)
    os.environ["LINGSHI_API_KEY"] = ""
    os.environ["LK888_API_KEY"] = ""
    if mode == "crash-lock":
        with cp.business_lock(directory, "a" * 64):
            os._exit(23)
    class ProcessClient(Client):
        def create_media_generation(self, **kwargs):
            with (directory / "creates.txt").open("a", encoding="utf-8") as stream:
                stream.write("fixture-create\n")
            if mode == "crash-submission":
                os._exit(24)
            time.sleep(0.1)
            return super().create_media_generation(**kwargs)
    localized.generate_localized_reference_image(**args(directory, ProcessClient()))
    print("fixture-completed")


def test_process_concurrency_and_crash_released_kernel_lock(tmp_path):
    command = [sys.executable, "-B", str(Path(__file__).resolve())]
    options = dict(cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    processes = [subprocess.Popen([*command, "generate", str(tmp_path)], **options) for _ in range(2)]
    outputs = [process.communicate(timeout=30) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], outputs
    assert (tmp_path / "creates.txt").read_text(encoding="utf-8").splitlines() == ["fixture-create"]
    crashed = subprocess.run([*command, "crash-lock", str(tmp_path)], timeout=10, **options)
    assert crashed.returncode == 23
    with cp.business_lock(tmp_path, "a" * 64, timeout=0.2):
        assert (tmp_path / (".lingshi-" + "a" * 24 + ".lock")).exists()
    assert cp.inspect_image_checkpoint(path_for(tmp_path))["status"] == "COMPLETED"


def test_process_crash_after_submitting_marker_cannot_resubmit_on_restart(tmp_path):
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "crash-submission", str(tmp_path)]
    crashed = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=10,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert crashed.returncode == 24, crashed.stderr
    assert cp.inspect_image_checkpoint(path_for(tmp_path))["status"] == "SUBMITTING"
    client = Client()
    with pytest.raises(cp.CheckpointRecoveryRequired, match="unknown"):
        localized.generate_localized_reference_image(**args(tmp_path, client), retry_attempt=1)
    assert client.created == []
    assert (tmp_path / "creates.txt").read_text(encoding="utf-8").splitlines() == ["fixture-create"]


if __name__ == "__main__":
    _child(sys.argv[1], Path(sys.argv[2]))
