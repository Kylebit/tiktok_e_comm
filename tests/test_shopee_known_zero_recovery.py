import json, sqlite3
from copy import deepcopy

import pytest
import shared_platform.operations_domain_guard as domain_guard
from shared_platform.shopee_known_zero_recovery import (
    ShopeeKnownZeroRecoveryError, ShopeeKnownZeroRecoveryStore,
    build_known_zero_receipt, validate_known_zero_receipt,
)

RUN = "product-center-shopee-373ea0658320f643b4634fb6d6ce671b"
LABELS = ["shopee:MY", "shopee:TH", "shopee:VN"]


def _facts(tmp_path):
    manifest = {
        "schema_version":"shopee-reportless-recovery-execution-manifest/v1",
        "manifest_digest":"sha256:"+"d"*64, "offer_id":"3956742887", "plan_id":"plan",
        "execution_snapshot_digest":"sha256:"+"s"*64,
        "direct_predecessor_run_id":"old", "source_receipt_digest":"sha256:"+"r"*64,
        "preflight_digest":"sha256:"+"p"*64, "evidence_relocation_digest":"sha256:"+"e"*64,
        "target_labels":LABELS, "continuation":{"schema_version":"shopee-recovery-continuation/v1",
            "receipt_digest":"sha256:"+"p"*64, "target_scope":LABELS,
            "exact_remaining_differences":[{"target_label":x,"differences":["item.status"]} for x in LABELS],
            "action_budgets":{x:["list_existing_item"] for x in LABELS}},
        "targets":[{"target_label":x,"model_sku":"1234"} for x in LABELS],
    }
    request_identity = {"kind":"SHOPEE_RECOVERY_CONTINUATION","authority_digest":manifest["manifest_digest"],
        "predecessor_run_id":"old","source_receipt_digest":manifest["source_receipt_digest"],
        "preflight_digest":manifest["preflight_digest"],"evidence_relocation_digest":manifest["evidence_relocation_digest"]}
    run = {"run_id":RUN,"report_id":"publication-report:"+RUN,"offer_id":"3956742887",
        "plan_id":"plan","snapshot_digest":manifest["execution_snapshot_digest"],"target_count":3,
        "state":"COMPLETED","final_report_id":"publication-report:"+RUN,"request_identity":request_identity}
    report = {"schema_version":"product-publication-report/v2","run_id":RUN,
        "report_id":"publication-report:"+RUN,"offer_id":"3956742887","plan_id":"plan",
        "snapshot":{"digest":manifest["execution_snapshot_digest"]},"status":"FAILED",
        "recovery_authorization":deepcopy(manifest),
        "summary":{"overall_status":"FAILED","evidence":{"dispatch_attempted":False,
            "external_write_count":0,"snapshot_verified":True,"readback_completed":True},
            "platforms":[{"platform":"SHOPEE","status":"FAILED","target_count":3,
                "verified_count":0,"processing_count":0,"failed_count":3}]},
        "mutation_budgets":[{"platform":"SHOPEE","reservations":[],
            "attempts":{"total":0,"shared":0,"per_target":{x:0 for x in LABELS}}}],
        "targets":[{"target_label":x,"status":"FAILED","evidence":{"target_label":x,
            "status":"FAILED","provider_identity_bound":True,"stage":"PREFLIGHT",
            "request_attempted":False,"outcome_unknown":False,"external_write_count":0,
            "provider_code":"shopee_recovery_preflight_failed"}} for x in LABELS]}
    db=tmp_path/"runs.db"; conn=sqlite3.connect(db)
    conn.executescript("CREATE TABLE product_publication_run_events(run_id TEXT,sequence INTEGER,state TEXT,final_report_id TEXT,failure_code TEXT,event_digest TEXT);CREATE TABLE product_publication_reports(run_id TEXT,report_digest TEXT,envelope_digest TEXT);")
    conn.executemany("INSERT INTO product_publication_run_events VALUES(?,?,?,?,?,?)",[
        (RUN,1,"QUEUED",None,None,"a"*64),(RUN,2,"RUNNING",None,None,"b"*64),
        (RUN,3,"COMPLETED","publication-report:"+RUN,None,"c"*64)])
    conn.execute("INSERT INTO product_publication_reports VALUES(?,?,?)",(RUN,"f"*64,"g"*64));conn.commit();conn.close()
    class Runs:
        path=db
        def get_run_by_id(self,*,run_id): return deepcopy(run) if run_id==RUN else None
    class Reports:
        def get_report_by_run(self,*,run_id): return deepcopy(report) if run_id==RUN else None
    validation={"run_store":Runs(),"report_store":Reports(),"manifest_validator":lambda value:deepcopy(dict(value))}
    return manifest,run,report,validation


def test_known_zero_receipt_rebuild_store_and_tamper(tmp_path):
    manifest,run,report,validation=_facts(tmp_path)
    receipt=build_known_zero_receipt(run_id=RUN,manifest=manifest,registered_by="test",**validation)
    assert receipt["provider_mutation_dispatch_attempted"] is False and receipt["external_write_count"]==0
    assert receipt["new_manifest"]["authorized"] is False
    store=ShopeeKnownZeroRecoveryStore(tmp_path/"receipts",allowed_root=tmp_path,**validation)
    assert store.register(receipt)==receipt and store.register(receipt)==receipt
    assert store.get(run_id=RUN)==receipt
    tampered=deepcopy(receipt);tampered["external_write_count"]=1
    with pytest.raises(ShopeeKnownZeroRecoveryError): validate_known_zero_receipt(tampered,**validation)
    report["targets"][0]["evidence"]["request_attempted"]=True
    with pytest.raises(ShopeeKnownZeroRecoveryError): build_known_zero_receipt(
        run_id=RUN,manifest=manifest,registered_by="test",**validation)


def test_known_zero_store_never_overwrites_foreign_final_file(tmp_path):
    manifest,_run,_report,validation=_facts(tmp_path)
    receipt=build_known_zero_receipt(run_id=RUN,manifest=manifest,registered_by="test",**validation)
    store=ShopeeKnownZeroRecoveryStore(tmp_path/"receipts",allowed_root=tmp_path,**validation)
    final=store._path(RUN);final.parent.mkdir(parents=True);final.write_bytes(b"partial")
    with pytest.raises(ShopeeKnownZeroRecoveryError): store.register(receipt)
    assert final.read_bytes()==b"partial"


def test_known_zero_closure_uses_exact_continuation_operation(monkeypatch,tmp_path):
    manifest,_run,_report,validation=_facts(tmp_path)
    receipt=build_known_zero_receipt(run_id=RUN,manifest=manifest,registered_by="test",**validation)
    class Release:
        def approved_publication_snapshot(self,**kwargs): return {"plan_id":"plan"}
    class Engine:
        def complete_domain_operation_exact(self,operation,*,skus,shops,provider_readback_ref):
            self.operation=operation;self.ref=provider_readback_ref;self.skus=skus;self.shops=shops
    engine=Engine();monkeypatch.setattr(domain_guard,"engine_for",lambda root:engine)
    result=domain_guard.finish_known_zero_shopee_recovery(
        Release(),"3956742887",manifest["execution_snapshot_digest"],tmp_path,
        receipt=receipt,target_scope=LABELS,receipt_validation=validation)
    retry={"run_id":RUN,"retry_of_run_id":"old","source_evidence_digest":manifest["preflight_digest"]}
    expected=domain_guard._publication_operation_id("plan",LABELS,retry_attempt=retry,
        recovery_continuation=manifest["continuation"])
    assert result["operation_id"]==expected==engine.operation
    assert engine.skus==["1234"] and engine.shops==LABELS
    assert result["new_manifest"]["authorized"] is False


@pytest.mark.parametrize("skus", [["1234", "9999", "1234"], ["", "", ""]])
def test_known_zero_closure_rejects_ambiguous_or_empty_manifest_sku(monkeypatch,tmp_path,skus):
    manifest,_run,report,validation=_facts(tmp_path)
    manifest["targets"]=[{**row,"model_sku":sku} for row,sku in zip(manifest["targets"],skus)]
    report["recovery_authorization"]=deepcopy(manifest)
    receipt=build_known_zero_receipt(run_id=RUN,manifest=manifest,registered_by="test",**validation)
    class Release:
        def approved_publication_snapshot(self,**kwargs): return {"plan_id":"plan"}
    with pytest.raises(ValueError, match="SKU identity conflicts"):
        domain_guard.finish_known_zero_shopee_recovery(
            Release(),"3956742887",manifest["execution_snapshot_digest"],tmp_path,
            receipt=receipt,target_scope=LABELS,receipt_validation=validation)
