"""Business isolation with preserved v1 bytes and bounded offline ownership evidence."""
from datetime import datetime, timezone
import hashlib
import json

import pytest
from modules.sourcing import image_generation_checkpoint as cp


def business(root, offer, prompt=None):
    return cp.ImageCheckpoint(root, kind="brand", business_identity={"offer_id": offer, "brand_id": "brand", "role": "cover"},
        request_identity={"prompt": prompt or offer, "model": "fixture"}, model="fixture", source_identity_complete=True)


@pytest.mark.parametrize("damaged", ["identity", "snapshot"])
def test_valid_independent_record_scopes_damaged_business_to_its_owner(tmp_path, damaged):
    a, b = business(tmp_path, "A"), business(tmp_path, "B")
    state = a.open_for_execution()
    a.persist(state, status="SUBMISSION_UNKNOWN")
    path = a.identity_path if damaged == "identity" else a.path
    path.write_bytes(b"damaged")
    assert b.open_for_execution()["status"] == "READY"
    with pytest.raises(cp.CheckpointRecoveryRequired):
        a.open_for_execution()
    with pytest.raises(cp.CheckpointRecoveryRequired):
        business(tmp_path, "A", "new prompt").open_for_execution()
    assert path.read_bytes() == b"damaged"


def test_conflicting_valid_snapshot_and_identity_cannot_be_scoped_away(tmp_path):
    a, b = business(tmp_path, "A"), business(tmp_path, "B")
    a.open_for_execution()
    row = json.loads(a.path.read_text())
    row["business_digest"] = b.business_digest
    cp.atomic_json(a.path, cp._sealed(row))
    with pytest.raises(cp.CheckpointRecoveryRequired):
        b.open_for_execution()


def old_checkpoint(root, offer, task):
    identity = {"schema_version": "lingshi-brand-image/v1", "offer_id": offer, "brand_id": "brand", "role": "cover",
        "brief": "original", "source_identities": ["https://fixture.example/source.png"],
        "product_reference_count": 1, "model": "fixture", "size": "2048x2048", "quality": "medium"}
    old_digest = cp.digest(identity)
    path = root / f"lingshi-brand-{old_digest}.json"
    cp.atomic_json(path, {"schema_version": "brand-image-lingshi-checkpoint/v1", "identity_digest": old_digest,
        "client_business_id": f"brand-{old_digest[:40]}", "status": "SUBMITTED", "task_id": task})
    return path, identity


def binding_evidence(path, target):
    return {**cp.inspect_checkpoint_ownership(path), "business_digest": target.business_digest,
        "request_digest": target.request_digest, "provider": cp.PROVIDER,
        "verified_at": datetime.now(timezone.utc).isoformat(), "verified_by": "fixture-verifier",
        "evidence_ref": "fixture://ownership/verified", "evidence_sha256": "sha256:" + "e" * 64}


def attach_evidence(target, task):
    return {**cp.inspect_image_checkpoint(target.path), "outcome": "task_verified_for_request", "task_id": task,
        "charge_status": "previous_task_recorded", "verified_at": datetime.now(timezone.utc).isoformat(),
        "verified_by": "fixture-verifier", "evidence_ref": "fixture://task/verified", "evidence_sha256": "sha256:" + "f" * 64}


def bind(path, identity, target, **overrides):
    proof = binding_evidence(path, target)
    proof.update(overrides)
    return cp.bind_checkpoint_ownership(path, business_identity={"offer_id": identity["offer_id"], "brand_id": "brand", "role": "cover"},
        request_digest=target.request_digest, evidence=proof, legacy_identity=identity)


def test_two_actual_legacy_identities_migrate_separately_without_changing_old_bytes(tmp_path):
    pa, ia = old_checkpoint(tmp_path, "A", 101)
    pb, ib = old_checkpoint(tmp_path, "B", 202)
    original = {p: p.read_bytes() for p in (pa, pb)}
    a, b = business(tmp_path, "A"), business(tmp_path, "B")
    for target in (a, b):
        with pytest.raises(cp.CheckpointRecoveryRequired): target.open_for_execution()
    bind(pa, ia, a)
    bind(pb, ib, b)
    assert set(a.legacy_records()) == {pa.name}
    assert set(b.legacy_records()) == {pb.name}
    assert cp.reconcile_image_checkpoint(a.path, evidence=attach_evidence(a, 101))["task_id"] == 101
    assert cp.reconcile_image_checkpoint(b.path, evidence=attach_evidence(b, 202))["task_id"] == 202
    polls=[]
    def poll(task):
        polls.append(task)
        return {"result_url":f"https://fixture.example/{task}.png"}
    def submit(): raise AssertionError("ownership migration must never create a provider task")
    for target,task in ((a,101),(b,202)):
        result=cp.execute_image_checkpoint(target,retry_attempt=0,submit=submit,poll=poll,
            load_result=lambda url:url.encode(),normalize_image=lambda raw:raw)
        assert result["receipt"]["task_id"]==task
    assert polls==[101,202]
    assert {p: p.read_bytes() for p in original} == original


def test_unassigned_legacy_record_still_blocks_every_possible_owner(tmp_path):
    old_checkpoint(tmp_path, "A", 101)
    for offer in ("A", "B"):
        with pytest.raises(cp.CheckpointRecoveryRequired): business(tmp_path, offer).open_for_execution()


@pytest.mark.parametrize("part", ["snapshot", "identity", "both"])
def test_lost_peer_files_cannot_hide_same_business_unknown(tmp_path, part):
    a = business(tmp_path, "A")
    state = a.open_for_execution()
    a.persist(state, status="SUBMISSION_UNKNOWN")
    if part in {"snapshot", "both"}: a.path.unlink()
    if part in {"identity", "both"}: a.identity_path.unlink()
    with pytest.raises(cp.CheckpointRecoveryRequired): business(tmp_path, "A", "new prompt").open_for_execution()
    if part == "both":
        with pytest.raises(cp.CheckpointRecoveryRequired): business(tmp_path, "B").open_for_execution()
    else:
        assert business(tmp_path, "B").open_for_execution()["status"] == "READY"


def unknown_owner(tmp_path):
    a = business(tmp_path, "A")
    a.open_for_execution()
    a.path.write_bytes(b"damaged snapshot")
    a.identity_path.write_bytes(b"damaged identity")
    return a


def bind_unknown(a):
    return cp.bind_checkpoint_ownership(a.path,
        business_identity={"offer_id": "A", "brand_id": "brand", "role": "cover"},
        request_digest=a.request_digest, evidence=binding_evidence(a.path, a))


def test_explicit_owner_of_unattributable_data_does_not_repair_or_release_owner(tmp_path):
    a = unknown_owner(tmp_path)
    original = {p:p.read_bytes() for p in (a.path,a.identity_path)}
    with pytest.raises(cp.CheckpointRecoveryRequired): business(tmp_path,"B").open_for_execution()
    row = bind_unknown(a)
    assert row["authority"] == "OWNERSHIP_ONLY_NO_TASK_OR_CHARGE_FINDING"
    assert business(tmp_path,"B").open_for_execution()["status"] == "READY"
    with pytest.raises(cp.CheckpointRecoveryRequired): a.open_for_execution()
    with pytest.raises(cp.CheckpointRecoveryRequired): business(tmp_path,"A","changed").open_for_execution()
    assert {p:p.read_bytes() for p in original} == original


@pytest.mark.parametrize("suffix", [".json", ".identity", ".events.jsonl", ".png", ".json.tmp"])
def test_any_bound_source_change_invalidates_ownership_receipt(tmp_path, suffix):
    a = unknown_owner(tmp_path)
    row = bind_unknown(a)
    receipt_path = cp._ownership_path(a.path)
    original_receipt = receipt_path.read_bytes()
    a.path.with_suffix(suffix).write_bytes(b"changed source bytes")
    with pytest.raises(cp.CheckpointRecoveryRequired): business(tmp_path,"B").open_for_execution()
    assert receipt_path.read_bytes() == original_receipt
    assert cp.inspect_checkpoint_ownership(a.path)["existing_ownership"] is None


@pytest.mark.parametrize("field", ["request_digest", "business_digest", "provider", "source_binding", "verified_at", "evidence_ref", "evidence_sha256"])
def test_ownership_evidence_mismatch_is_rejected_without_receipt(tmp_path, field):
    a = unknown_owner(tmp_path)
    proof = binding_evidence(a.path, a)
    proof[field] = "wrong"
    with pytest.raises((ValueError,cp.CheckpointRecoveryRequired)):
        cp.bind_checkpoint_ownership(a.path,business_identity={"offer_id":"A","brand_id":"brand","role":"cover"},
            request_digest=a.request_digest,evidence=proof)
    assert not list(tmp_path.glob("owner-*.json"))


@pytest.mark.parametrize("kind", ["identity", "target", "source", "task_conflict", "provider"])
def test_legacy_mismatch_cannot_create_ownership_receipt(tmp_path, kind):
    path, identity = old_checkpoint(tmp_path, "A", 101)
    a = business(tmp_path,"A")
    with pytest.raises(cp.CheckpointRecoveryRequired): a.open_for_execution()
    proof = binding_evidence(path,a)
    target = a
    if kind == "identity": identity["brief"] = "wrong original brief"
    if kind == "target":
        target = business(tmp_path,"B")
        with pytest.raises(cp.CheckpointRecoveryRequired): target.open_for_execution()
        proof = binding_evidence(path,target)
    if kind == "source": path.write_bytes(path.read_bytes()+b" ")
    if kind in {"task_conflict","provider"}:
        row=json.loads(path.read_text())
        row["receipt"]={"task_id":202} if kind=="task_conflict" else {"provider":"retired"}
        cp.atomic_json(path,row)
        proof=binding_evidence(path,a)
    before=path.read_bytes()
    with pytest.raises((ValueError,cp.CheckpointRecoveryRequired)):
        cp.bind_checkpoint_ownership(path,business_identity={"offer_id":"A","brand_id":"brand","role":"cover"},
            request_digest=target.request_digest,evidence=proof,legacy_identity=identity)
    assert path.read_bytes()==before and not list(tmp_path.glob("owner-*.json"))


def test_localized_v1_identity_maps_by_source_locale_without_reusing_output(tmp_path):
    identity={"schema_version":"lingshi-localized-image/v1","source_url":"https://fixture.example/A.png",
        "source_digest":"a"*64,"locale":"th-TH","translations":[{"source_text":"A","translated_text":"ข"}],
        "model":"fixture","retry_attempt":0}
    old_digest=cp.digest(identity)
    path=tmp_path/f"lingshi-{old_digest}.json"
    cp.atomic_json(path,{"schema_version":"localized-image-lingshi-checkpoint/v1","identity_digest":old_digest,
        "client_business_id":f"localized-{old_digest[:40]}","task_id":101,"status":"SUBMITTED"})
    original=path.read_bytes()
    stable={"source_url":identity["source_url"],"locale":identity["locale"]}
    a=cp.ImageCheckpoint(tmp_path,kind="localized",business_identity=stable,
        request_identity={"prompt":"full new prompt"},model="fixture",source_identity_complete=True)
    with pytest.raises(cp.CheckpointRecoveryRequired):a.open_for_execution()
    cp.bind_checkpoint_ownership(path,business_identity=stable,request_digest=a.request_digest,
        legacy_identity=identity,evidence=binding_evidence(path,a))
    assert cp.inspect_image_checkpoint(a.path)["task_id"] is None
    result=cp.reconcile_image_checkpoint(a.path,evidence=attach_evidence(a,101))
    assert result["status"]=="SUBMITTED" and result["task_id"]==101
    assert path.read_bytes()==original


def test_corrupt_ownership_receipt_is_not_ignored(tmp_path):
    a=unknown_owner(tmp_path)
    bind_unknown(a)
    receipt=cp._ownership_path(a.path)
    receipt.write_bytes(b"damaged ownership")
    with pytest.raises(cp.CheckpointRecoveryRequired):business(tmp_path,"B").open_for_execution()
    assert receipt.read_bytes()==b"damaged ownership"


def test_ownership_repetition_is_idempotent_and_same_business_peer_stays_pending(tmp_path):
    path,identity=old_checkpoint(tmp_path,"A",101)
    a=business(tmp_path,"A")
    with pytest.raises(cp.CheckpointRecoveryRequired):a.open_for_execution()
    first=bind(path,identity,a)
    second=bind(path,identity,a)
    assert first==second and len(list(tmp_path.glob("owner-*.json")))==1
    with pytest.raises(cp.CheckpointRecoveryRequired):business(tmp_path,"A","different prompt").open_for_execution()


def test_removing_mapped_target_cannot_hide_unresolved_legacy_task(tmp_path):
    path,identity=old_checkpoint(tmp_path,"A",101)
    a=business(tmp_path,"A")
    with pytest.raises(cp.CheckpointRecoveryRequired):a.open_for_execution()
    bind(path,identity,a)
    for artifact in (a.path,a.identity_path,a.journal_path):
        if artifact.exists():artifact.unlink()
    with pytest.raises(cp.CheckpointRecoveryRequired):business(tmp_path,"A","new prompt").open_for_execution()
    assert path.exists()


@pytest.mark.parametrize("field,value",[("model","different"),("source_identity_complete",False)])
def test_sealed_peer_metadata_conflict_is_not_silently_ignored(tmp_path,field,value):
    a=business(tmp_path,"A")
    a.open_for_execution()
    row=json.loads(a.path.read_text())
    row[field]=value
    cp.atomic_json(a.path,cp._sealed(row))
    with pytest.raises(cp.CheckpointRecoveryRequired):business(tmp_path,"B").open_for_execution()


def test_brand_wrapper_allows_b_but_preserves_a_unknown_and_bad_sidecar(tmp_path,monkeypatch):
    from io import BytesIO
    from PIL import Image
    from modules.sourcing import brand_image_lingshi_generation as brand
    raw=BytesIO()
    Image.new("RGB",(16,16),"white").save(raw,format="PNG")
    monkeypatch.setattr(brand,"_download_result",lambda url:raw.getvalue())
    class Client:
        def __init__(self,fail=False):self.fail=fail;self.creates=0
        def create_media_generation(self,**kwargs):
            self.creates+=1
            if self.fail:raise TimeoutError("fixture unknown")
            return {"code":200,"data":{"task_id":202}}
        def get_media_task(self,task):return {"is_final":True,"result_url":"https://fixture.example/B.png"}
    def call(offer,client):
        return brand.generate_brand_image(offer_id=offer,brand_id="brand",brand_label="Brand",positioning="fixture",
            role="cover",brief="fixture",product_identity="fixture product",source_urls=["https://fixture.example/source.png"],
            checkpoint_dir=tmp_path,client=client)
    a,b=Client(True),Client()
    with pytest.raises(TimeoutError):call("A",a)
    path=next(tmp_path.glob("*.identity"));path.write_bytes(b"damaged A")
    assert call("B",b)["receipt"]["task_id"]==202
    with pytest.raises(cp.CheckpointRecoveryRequired):call("A",a)
    assert a.creates==b.creates==1 and path.read_bytes()==b"damaged A"
