from copy import deepcopy
import hashlib, json
from pathlib import Path
import pytest

from shared_platform.shopee_recovery_reconciliations import (
    ShopeeRecoveryReconciliationError, ShopeeRecoveryReconciliationStore,
    build_reconciliation_receipt, reconciliation_gate,
    validate_reconciliation_receipt,
)

def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
def dig(v): return "sha256:"+hashlib.sha256(canon(v).encode()).hexdigest()
def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True); raw=(canon(value)+"\n").encode(); path.write_bytes(raw)
    return {"path":str(path),"sha256":"sha256:"+hashlib.sha256(raw).hexdigest()}

def bundle(root:Path,states=("MATCH","MATCH"),at="2026-09-13T02:00:00+00:00"):
    labels=["shopee:MY","shopee:TH"]
    targets=[]
    for i,label in enumerate(labels,1):
        targets.append({"target_label":label,"region":label[-2:],"shop_id":str(10+i),
            "item_id":str(20+i),"global_item_id":"30","model_id":str(40+i),
            "global_model_id":"50","model_sku":"0988","category_id":"101157",
            "local_original_price":{"amount":"1","currency":"X"},
            "approved_copy":{"title":"","description":""},
            "approved_image_route":{"existing_media_ids":[]},"enabled_logistics_ids":[1],
            "allowed_actions":["update_copy_and_description_media"],
            "mutation_budget":{"shared_maximum":0,"target_maximum":3}})
    manifest={"schema_version":"shopee-regional-recovery/v1","status":"READY_ZERO_WRITE_PREFLIGHT",
        "offer_id":"3956742887","product_revision":5,"plan_id":"plan",
        "execution_snapshot_digest":"sha256:"+"a"*64,"business_snapshot_digest":"sha256:"+"b"*64,
        "candidate_digest":"c"*64,"approval_digest":"d"*64,"prior_run_id":"prior",
        "prior_report_digest":"sha256:"+"e"*64,"target_labels":labels,"targets":targets}
    manifest["manifest_digest"]=dig(manifest)
    attempt={"schema_version":"product-publication-report/v2","run_id":"recovery-run",
        "report_id":"publication-report:recovery-run","offer_id":"3956742887","revision":5,
        "plan_id":"plan","snapshot":{"digest":manifest["execution_snapshot_digest"]},
        "status":"PROCESSING","updated_at":"2026-09-13T01:59:00+00:00","targets":[],
        "recovery_authorization":deepcopy(manifest)}
    for i,label in enumerate(labels):
        unknown=i==1
        attempt["targets"].append({"target_label":label,"status":"PROCESSING" if unknown else "PUBLISHED",
            "evidence":{"request_attempted":True,"outcome_unknown":unknown,
            "external_write_count":None if unknown else 2}})
    source_files={"attempt":put(root/"sources"/"attempt.json",attempt),
                  "manifest":put(root/"sources"/"manifest.json",manifest)}
    rows=[]
    for target,state in zip(targets,states):
        q={k:target[k] for k in ("shop_id","item_id","global_item_id","model_id","global_model_id","model_sku")}
        raw={
          "item":{"shop_id":target["shop_id"],"item_id":target["item_id"],"status":"NORMAL",
            "category_id":"101157","title":"","description":"","description_type":"extended",
            "gallery_image_ids":[],"description_image_ids":[],"enabled_logistics_ids":[1]},
          "models":[{"shop_id":target["shop_id"],"item_id":target["item_id"],"model_id":target["model_id"],
            "model_sku":"0988","status":"MODEL_NORMAL","price":"1","currency":"X"}],
          "global_linkage":{"shop_id":target["shop_id"],"item_id":target["item_id"],"global_item_id":"30"},
          "global_item":{"global_item_id":"30","status":"NORMAL"},
          "global_models":[{"global_item_id":"30","global_model_id":"50","model_sku":"0988","status":"MODEL_NORMAL"}]}
        if state=="DIFF": raw["item"]["description"]="drift"
        reads={}
        for name,response in raw.items():
            complete=state!="UNKNOWN"
            envelope={"schema_version":"shopee-official-direct-id-get-evidence/v1",
              "request":{"method":"GET","mode":"DIRECT_ID","resource":name,**q},
              "complete":complete,"observed_at":at,"raw_response":response if complete else None}
            meta=put(root/"gets"/target["target_label"].replace(":","-")/(name+".json"),envelope)
            reads[name]={"complete":complete,"response_ref":meta["path"],
                         "response_digest":meta["sha256"],"observed_at":at}
        row={"target_label":target["target_label"],"query":{"method":"GET","mode":"DIRECT_ID",**q},"reads":reads}
        row["target_evidence_digest"]=dig(row); rows.append(row)
    rb={"schema_version":"shopee-recovery-official-direct-id-readback/v1","authority":"SHOPEE_OFFICIAL_API",
        "run_id":attempt["run_id"],"report_id":attempt["report_id"],"manifest_digest":manifest["manifest_digest"],
        "observed_at":at,"product_writes":0,"targets":rows}
    rb["evidence_digest"]=dig(rb)
    return attempt,manifest,rb,source_files

def build(root,states=("MATCH","MATCH"),at="2026-09-13T02:00:00+00:00"):
    a,m,r,s=bundle(root,states,at)
    receipt=build_reconciliation_receipt(attempt=a,manifest=m,recovery_authorization=m,
        official_readback=r,source_files=s,allowed_evidence_roots=[root])
    return receipt,a,m,r,s

@pytest.mark.parametrize("states,result,closed,locked",[
    (("MATCH","MATCH"),"CONVERGED",True,False),
    (("MATCH","DIFF"),"REMAINING_DIFF",True,False),
    (("MATCH","UNKNOWN"),"UNKNOWN",False,True)])
def test_three_states_are_derived_from_raw_gets(tmp_path,states,result,closed,locked):
    receipt,_,m,_,_=build(tmp_path,states)
    assert (receipt["result"],receipt["attempt_closed"],receipt["mutation_lock"])==(result,closed,locked)
    assert validate_reconciliation_receipt(receipt,allowed_evidence_roots=[tmp_path])==receipt
    assert reconciliation_gate(receipt,run_id="recovery-run",report_id="publication-report:recovery-run",
        manifest_digest=m["manifest_digest"],allowed_evidence_roots=[tmp_path])["result"]==result
    assert receipt["new_manifest"]["authorized"] is False
    if result=="REMAINING_DIFF":
        assert receipt["new_manifest"]["target_scope"]==["shopee:TH"]
        assert receipt["new_manifest"]["exact_remaining_differences"]==[
            {"target_label":"shopee:TH","differences":["copy.description"]}]

def test_raw_bytes_and_immutable_attempt_source_are_authoritative(tmp_path):
    _,a,m,r,s=build(tmp_path,("MATCH","DIFF"))
    p=Path(r["targets"][1]["reads"]["item"]["response_ref"]); body=json.loads(p.read_text())
    body["raw_response"]["description"]=""; p.write_text(canon(body)+"\n",encoding="utf-8")
    with pytest.raises(ShopeeRecoveryReconciliationError,match="bytes digest"):
        build_reconciliation_receipt(attempt=a,manifest=m,recovery_authorization=m,
            official_readback=r,source_files=s,allowed_evidence_roots=[tmp_path])
    p=Path(s["attempt"]["path"]); p.write_text("{}\n",encoding="utf-8")
    with pytest.raises(ShopeeRecoveryReconciliationError,match="source bytes"):
        build_reconciliation_receipt(attempt=a,manifest=m,recovery_authorization=m,
            official_readback=r,source_files=s,allowed_evidence_roots=[tmp_path])

def test_non_get_wrong_target_and_known_target_diff_are_blocked(tmp_path):
    _,a,m,r,s=build(tmp_path/"method")
    r["targets"][0]["query"]["method"]="POST"; row=deepcopy(r["targets"][0]); row.pop("target_evidence_digest")
    r["targets"][0]["target_evidence_digest"]=dig(row); r.pop("evidence_digest"); r["evidence_digest"]=dig(r)
    with pytest.raises(ShopeeRecoveryReconciliationError,match="GET-only"):
        build_reconciliation_receipt(attempt=a,manifest=m,recovery_authorization=m,official_readback=r,
            source_files=s,allowed_evidence_roots=[tmp_path])
    a,m,r,s=bundle(tmp_path/"known",("DIFF","MATCH"))
    with pytest.raises(ShopeeRecoveryReconciliationError,match="must MATCH"):
        build_reconciliation_receipt(attempt=a,manifest=m,recovery_authorization=m,official_readback=r,
            source_files=s,allowed_evidence_roots=[tmp_path])

def test_forged_self_digest_scope_difference_and_attempt_cannot_pass_gate(tmp_path):
    receipt,_,m,_,_=build(tmp_path,("MATCH","DIFF"))
    mutations=[lambda v:v["new_manifest"].update(target_scope=["shopee:MY"]),
      lambda v:v["new_manifest"]["exact_remaining_differences"][0].update(differences=["item.status"]),
      lambda v:v["attempt"].update(run_id="forged")]
    for mutate in mutations:
        forged=deepcopy(receipt); mutate(forged); forged.pop("receipt_digest"); forged["receipt_digest"]=dig(forged)
        with pytest.raises(ShopeeRecoveryReconciliationError,match="semantics"):
            reconciliation_gate(forged,run_id="recovery-run",report_id="publication-report:recovery-run",
                manifest_digest=m["manifest_digest"],allowed_evidence_roots=[tmp_path])

def test_replay_requires_every_get_newer_and_terminal_closes_attempt(tmp_path):
    evidence=tmp_path/"evidence"; first,*_=build(evidence,("MATCH","UNKNOWN"))
    store=ShopeeRecoveryReconciliationStore(tmp_path/"receipts",allowed_evidence_roots=[evidence])
    store.register(first)
    stale,*_=build(evidence/"stale",("MATCH","MATCH"))
    with pytest.raises(ShopeeRecoveryReconciliationError,match="every official GET"): store.register(stale)
    second,*_=build(evidence/"newer",("MATCH","MATCH"),"2026-09-13T02:01:00+00:00")
    store.register(second)
    third,*_=build(evidence/"later",("MATCH","DIFF"),"2026-09-13T02:02:00+00:00")
    with pytest.raises(ShopeeRecoveryReconciliationError,match="already closed"): store.register(third)

def test_top_timestamp_cannot_freshen_old_gets_and_gets_must_postdate_attempt(tmp_path):
    _,a,m,r,s=build(tmp_path)
    r["observed_at"]="2026-09-13T03:00:00+00:00"; r.pop("evidence_digest"); r["evidence_digest"]=dig(r)
    with pytest.raises(ShopeeRecoveryReconciliationError,match="not derived"):
        build_reconciliation_receipt(attempt=a,manifest=m,recovery_authorization=m,official_readback=r,
            source_files=s,allowed_evidence_roots=[tmp_path])
    a,m,r,s=bundle(tmp_path/"old"); a["updated_at"]="2026-09-13T02:00:00+00:00"
    s["attempt"]=put(tmp_path/"old"/"sources"/"attempt-new.json",a)
    with pytest.raises(ShopeeRecoveryReconciliationError,match="predates"):
        build_reconciliation_receipt(attempt=a,manifest=m,recovery_authorization=m,official_readback=r,
            source_files=s,allowed_evidence_roots=[tmp_path])
