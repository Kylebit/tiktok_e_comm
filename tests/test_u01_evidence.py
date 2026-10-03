import json
from pathlib import Path
import hashlib

from shared_platform.product_workspace_evidence import history, product_evidence
from shared_platform.product_publication_closure import build_publication_closure, store_publication_closure


def put(root,name,value):
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    return p


def test_wrong_product_round_and_history_are_not_adopted(tmp_path):
    put(tmp_path,'data/new_product_workbench/9001.json',{'offer_id':'9002','_revision':9})
    put(tmp_path,'reports/product-preparation/9001/first-review.json',{'offer_id':'9002','status':'APPROVED'})
    listing=history(root=tmp_path)
    assert listing['items']==[] and len(listing['errors'])==1
    evidence=product_evidence('9001',root=tmp_path)
    assert evidence['rounds'][0]['status']=='INVALID'
    assert not evidence['execution_authority']


def test_historical_approval_is_only_a_versioned_read_index(tmp_path):
    put(tmp_path,'data/new_product_workbench/9001.json',{'offer_id':'9001','_revision':9,
        'source':{'source_authority':'1688'},'review':{'title':'Old title'},
        'product_approval':{'status':'approved','approved_by':'Kyle'}})
    value=history(root=tmp_path)['items'][0]
    assert value['revision']==9 and not value['execution_authority']
    assert 'product_approval' not in value


def test_closure_without_target_readback_cannot_be_displayed_as_verified(tmp_path):
    report={'offer_id':'9001','run_id':'run:one','plan_id':'plan:one','status':'PARTIAL'}
    source_path='reports/product-publication/9001/report.json'
    report_path=put(tmp_path,source_path,report)
    digest='sha256:'+hashlib.sha256(json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    closure=build_publication_closure(offer_id='9001',revision=9,plan_id='plan:one',snapshot_digest='sha256:'+'1'*64,
        recorded_at='2026-09-05T00:00:00+00:00',recorded_by='offline fixture',
        targets=[{'target_label':'tiktok:LH_PH','source_status':'PUBLISHED','resolution':'OFFICIAL_READBACK_VERIFIED',
            'evidence_code':'VERIFIED','source_run_id':'run:one','platform_identity_bound':True,'manual_handoff':None},
            {'target_label':'shopee:PH','source_status':'BLOCKED','resolution':'MANUAL_HANDOFF_ACCEPTED',
            'evidence_code':'HANDOFF','source_run_id':'run:one','platform_identity_bound':False,
            'manual_handoff':{'accepted_by':'offline fixture','accepted_at':'2026-09-05T00:00:00+00:00','note':'Manual action pending'}}],
        source_reports=[{'run_id':'run:one','report_path':source_path,'report_digest':digest}])
    store_publication_closure(closure,root=tmp_path/'reports/product-publication')
    result=product_evidence('9001',root=tmp_path)['closures'][0]
    assert result['status']=='RECONCILIATION_REQUIRED'
    assert result['closure']['summary']['verified_count']==1
    assert result['closure']['summary']['manual_handoff_count']==1
    report_path.unlink()
    result=product_evidence('9001',root=tmp_path)['closures'][0]
    assert result['status']=='INVALID' and 'closure' not in result


def test_linked_round_evidence_is_not_read(tmp_path):
    outside=put(tmp_path,'unrelated.json',{'offer_id':'9001','status':'APPROVED'})
    path=tmp_path/'reports/product-preparation/9001/first-review.json'
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    assert product_evidence('9001',root=tmp_path)['rounds'][0]['status']=='INVALID'
