from copy import deepcopy
import hashlib
import json
import sqlite3
import pytest
from test_tiktok_lineage_protocol import _completion_manifest, RECOVERY, TIKTOK_TARGET_ORDER
import shared_platform.tiktok_lineage_recovery as protocol
from shared_platform.product_publication_reports import ProductPublicationReportStore, validate_publication_report


def _zero_report(manifest=None,run_id='product-center-tiktok-9714f9311f80a6675653430d54c96783'):
    manifest=manifest or _completion_manifest()
    rows=[]
    for label in TIKTOK_TARGET_ORDER:
        selected=label in RECOVERY
        rows.append({'target_label':label,'status':'FAILED' if selected else 'PUBLISHED' if label=='tiktok:LH_PH' else 'PROCESSING',
                     'stage':'VERIFY' if selected else 'BLOCKED','attempts':{'save_draft':0,'publish_target':0,'official_readback':0},
                     'confirmed_write_count':0,'unknown_write_count':0,'reason':'continuation_target_exception' if selected else 'outside_recovery_subset'})
    evidence={'schema_version':protocol.RESULT_SCHEMA_VERSION,'manifest_digest':manifest['manifest_digest'],
              'lineage':deepcopy(manifest['lineage']),'targets':rows,'reservations':[],
              'confirmed_write_count':0,'unknown_write_count':0,'remaining_target_labels':[label for label in TIKTOK_TARGET_ORDER if label!='tiktok:LH_PH'],
              'automatic_retry_performed':False,'no_scope_expansion':True}
    evidence['result_digest']=protocol._canonical_digest(evidence)
    public_order=['tiktok:LH_PH','tiktok:LH_MY','tiktok:LH_TH','tiktok:LH_VN','tiktok:HB_PH','tiktok:HB_MY','tiktok:HB_TH','tiktok:HB_VN','tiktok:MX','tiktok:GB']
    by_label={row['target_label']:row for row in rows}
    return {'schema_version':'product-publication-report/v2','report_id':'publication-report:'+run_id,'run_id':run_id,
            'offer_id':manifest['lineage']['offer_id'],'revision':5,'plan_id':manifest['lineage']['plan_id'],
            'snapshot':{'schema_version':'approved-publication-snapshot/v4','digest':manifest['lineage']['snapshot_digest']},
            'status':'PARTIAL','execution_identity':{'skill_digest':'1'*64,'git_commit':'2'*40,'code_digest':'3'*64},
            'targets':[{'target_label':label,'status':by_label[label]['status'],'evidence':None} for label in public_order],
            'summary':{'schema_version':'product-publication-summary/v1','overall_status':'PARTIAL',
                       'platforms':[{'platform':'TIKTOK','status':'PARTIAL','target_count':10,'verified_count':1,'processing_count':1,'failed_count':8}],
                       'evidence':{'snapshot_verified':True,'dispatch_attempted':False,'readback_completed':True,'external_write_count':0},
                       'requires_human_action':False},
            'release_authorization':{key:manifest['lineage'][key].removeprefix('sha256:') for key in ['candidate_digest','approval_digest']},
            'mutation_budgets':[{'schema_version':'publication-mutation-budget/v1','platform':'TIKTOK',
                                'limits':{'shared_maximum':0,'per_target_maximum':2},
                                'attempts':{'shared':0,'per_target':{label:0 for label in TIKTOK_TARGET_ORDER},'total':0},'reservations':[]}],
            'continuation_evidence':evidence}


def _rehost_receipt(manifest):
    url='https://cdn.example/new.png';digest='sha256:'+'4'*64
    value={'schema_version':protocol.EXACT_ASSET_REHOST_RECEIPT_SCHEMA_VERSION,'target_label':'tiktok:HB_TH',
           'offer_id':manifest['lineage']['offer_id'],'seller_sku':'0988','source':{'sha256':digest,'content_modified':False},
           'transport':{'direct_url':url,'readbacks':[{'ordinal':ordinal,'http_status':200,'final_url':url,'sha256':digest} for ordinal in [1,2]]},
           'external_writes':{'neutral_cdn_upload':1,'miaoshou':0,'marketplace':0}}
    value['receipt_digest']=protocol._canonical_digest(value);return value


def test_report_roundtrip_retains_strict_continuation_and_original_envelope(tmp_path):
    report=_zero_report();store=ProductPublicationReportStore(tmp_path/'reports.db',reports_root=tmp_path/'assets')
    record=store.store_report(report)
    before=hashlib.sha256(store.path.read_bytes()).hexdigest()
    read=store.get_report(report_id=record.report_id,offer_id=report['offer_id'])
    assert read['continuation_evidence']==report['continuation_evidence']
    assert hashlib.sha256(store.path.read_bytes()).hexdigest()==before


def test_v2_zero_write_retry_preserves_approval_scope_and_facts():
    manifest=_completion_manifest();report=_zero_report(manifest)
    result=protocol.compile_tiktok_approved_first_completion_zero_write_retry(manifest,report,transport_rehost_receipt=_rehost_receipt(manifest))
    assert result['schema_version']==protocol.APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION
    assert result['lineage']==manifest['lineage']
    assert result['approved_target_labels']==list(TIKTOK_TARGET_ORDER)
    assert result['completion_target_labels']==manifest['completion_target_labels']
    assert result['zero_write_retry']['automatic_retry'] is False


def _depth2_inputs():
    original=_completion_manifest()
    predecessor=protocol.compile_tiktok_approved_first_completion_zero_write_retry(original,_zero_report(original),transport_rehost_receipt=_rehost_receipt(original))
    report=_zero_report(predecessor,run_id='synthetic-v2-zero-write')
    binding=predecessor['zero_write_retry']
    request={'kind':'TIKTOK_FIRST_COMPLETION_ZERO_WRITE_RETRY','authority_digest':predecessor['authority_addendum_digest'],
             'manifest_digest':predecessor['manifest_digest'],'retry_of_run_id':binding['source_run_id'],
             'source_report_digest':binding['source_report_digest'],'source_result_digest':binding['source_result_digest'],
             'source_manifest_digest':binding['source_manifest_digest']}
    report['continuation_evidence']['retry_source']={**request,'binding_digest':protocol._canonical_digest(request)}
    run={'run_id':report['run_id'],'report_id':report['report_id'],'final_report_id':report['report_id'],'state':'COMPLETED',
         'offer_id':report['offer_id'],'revision':report['revision'],'plan_id':report['plan_id'],
         'snapshot_schema_version':report['snapshot']['schema_version'],'snapshot_digest':report['snapshot']['digest'],
         'platform_scope':['TIKTOK'],'target_count':10,'execution_identity':deepcopy(report['execution_identity']),
         'request_identity':request}
    return predecessor,report,run


def test_v3_zero_write_retry_binds_exact_predecessor_without_new_approval():
    predecessor,report,run=_depth2_inputs()
    result=protocol.compile_tiktok_approved_first_completion_zero_write_retry_depth2(predecessor,report,source_run=run)
    assert result['schema_version']==protocol.APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION
    assert result['lineage']==predecessor['lineage']
    assert result['commands']==predecessor['commands']
    assert result['zero_write_retry']['automatic_retry'] is False
    assert protocol.predecessor_tiktok_approved_completion_retry_manifest(result)==predecessor
    with pytest.raises(protocol.TikTokLineageRecoveryError):
        protocol.compile_tiktok_approved_first_completion_zero_write_retry_depth2(result,report,source_run=run)


@pytest.mark.parametrize('tamper',['unknown','processing','writes','identity'])
def test_v3_rejects_unknown_processing_writes_or_other_run(tamper):
    predecessor,report,run=_depth2_inputs()
    if tamper=='identity':run['plan_id']='unapproved-plan'
    elif tamper=='writes':report['continuation_evidence']['unknown_write_count']=1
    else:report['continuation_evidence']['targets'][1]['status']=tamper.upper()
    evidence=report['continuation_evidence'];body={k:v for k,v in evidence.items() if k not in {'result_digest','retry_source'}}
    evidence['result_digest']=protocol._canonical_digest(body)
    with pytest.raises(protocol.TikTokLineageRecoveryError):
        protocol.compile_tiktok_approved_first_completion_zero_write_retry_depth2(predecessor,report,source_run=run)


def test_continuation_and_legacy_recovery_authorization_coexist(tmp_path):
    from shared_platform.shopee_regional_recovery import _digest
    report=_zero_report()
    legacy={'schema_version':'shopee-regional-recovery/v1','offer_id':report['offer_id']}
    legacy['manifest_digest']=_digest(legacy)
    report['recovery_authorization']=legacy
    old_run='synthetic-shopee-failure';receipt='sha256:'+'e'*64
    failed={'run_id':old_run,'report_id':'publication-report:'+old_run,'offer_id':report['offer_id'],
            'revision':5,'plan_id':report['plan_id'],'snapshot_digest':report['snapshot']['digest'],
            'platform_scope':['SHOPEE'],'target_count':3,'execution_identity':deepcopy(report['execution_identity']),
            'request_identity':{'kind':'SHOPEE_RECOVERY','authority_digest':legacy['manifest_digest']}}
    report['recovery_retry_authorization']={'schema_version':'shopee-recovery-retry-authorization/v1',
            'retry_of_run_id':old_run,'receipt_digest':receipt,'manifest_digest':legacy['manifest_digest'],
            'failed_run_identity':failed,'successor_request_identity':{'kind':'SHOPEE_RECOVERY_RETRY',
            'authority_digest':legacy['manifest_digest'],'reconciliation_receipt_digest':receipt,'retry_of_run_id':old_run}}
    store=ProductPublicationReportStore(tmp_path/'reports.db',reports_root=tmp_path/'assets')
    record=store.store_report(report)
    result=store.get_report(report_id=record.report_id,offer_id=report['offer_id'])
    assert result['recovery_authorization']==legacy
    assert result['continuation_evidence']==report['continuation_evidence']
    assert result['recovery_retry_authorization']==report['recovery_retry_authorization']
    drift=deepcopy(report);drift['recovery_retry_authorization']['failed_run_identity']['plan_id']='other-plan'
    with pytest.raises(ValueError):validate_publication_report(drift)


def test_continuation_nested_verification_and_retry_digests_are_enforced():
    report=_zero_report();evidence=report['continuation_evidence']
    verification={'schema_version':'tiktok-scope-verification/v1','status':'FAILED','target_count':8,'unique_asset_count':2,
                  'failures':[{'target_label':RECOVERY[0],'stage':'VERIFY','code':'ASSET_READ_UNAVAILABLE'}]}
    verification['verification_digest']=protocol._canonical_digest(verification)
    evidence['verification_summary']=verification
    body=dict(evidence);body.pop('result_digest');evidence['result_digest']=protocol._canonical_digest(body)
    assert validate_publication_report(report)['continuation_evidence']==evidence
    changed=deepcopy(report);changed['continuation_evidence']['verification_summary']['failures'][0]['code']='UNTRUSTED_ERROR'
    with pytest.raises(ValueError):validate_publication_report(changed)
    _,retry_report,_=_depth2_inputs()
    retry_report['continuation_evidence']['retry_source']['binding_digest']='sha256:'+'f'*64
    with pytest.raises(ValueError):validate_publication_report(retry_report)


@pytest.mark.parametrize('field',['unknown','extra','digest','attempt','retry_source'])
def test_malformed_continuation_rejected(field):
    report=_zero_report();e=report['continuation_evidence']
    if field=='unknown':e['automatic_retry_performed']=True
    elif field=='extra':e['unsupported']=True
    elif field=='digest':e['result_digest']='sha256:'+'0'*64
    elif field=='attempt':e['targets'][0]['attempts']['publish_target']=True
    else:e['retry_source']={'kind':'TIKTOK_FIRST_COMPLETION_ZERO_WRITE_RETRY'}
    with pytest.raises((TypeError,ValueError)):validate_publication_report(report)
