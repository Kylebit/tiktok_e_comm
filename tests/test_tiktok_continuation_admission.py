"""Admission only: synthetic identities, temporary policy files, zero providers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
import shared_platform.tiktok_continuation_admission as admission
from test_tiktok_lineage_protocol import _manifest, _completion_manifest, TIKTOK_TARGET_ORDER


def fixture(completion=False):
    manifest = _completion_manifest() if completion else _manifest()
    lineage = manifest['lineage']
    data = {k: lineage[k] for k in ('offer_id','plan_id','snapshot_digest','candidate_digest')}
    data['continuation_manifest'] = manifest
    policy = {'schema_version':'tiktok-continuation-scope-policy/v1',
              'lineage':{k:lineage[k] for k in ('offer_id','revision','plan_id','snapshot_digest','candidate_digest','approval_digest')},
              'approved_target_labels':list(TIKTOK_TARGET_ORDER), 'retired_target_labels':[],
              'authorization_evidence_digest':manifest.get('authority_addendum_digest',manifest.get('authority_receipt_digest')),
              'manifest_digest':manifest['manifest_digest']}
    policy['policy_digest'] = admission._digest(policy)
    snapshot = {k:lineage[k] for k in ('offer_id','plan_id','snapshot_digest')}
    snapshot['product_revision'] = int(lineage['revision'])
    trusted = dict(policy=policy,snapshot=snapshot,
                   candidate={'candidate_digest':lineage['candidate_digest'],'target_labels':list(TIKTOK_TARGET_ORDER)},
                   approval={'approval_digest':lineage['approval_digest']},
                   asset_digest_reader=lambda _: 'sha256:'+'a'*64)
    return data,trusted


@pytest.mark.parametrize('completion',[False,True])
def test_valid_admission_never_queues_or_grants_execution(monkeypatch,completion):
    data,trusted=fixture(completion)
    seen=[]
    monkeypatch.setattr('shared_platform.publication_autopilot.validate_release_candidate_for_execution',lambda c,**kw:c)
    monkeypatch.setattr('shared_platform.publication_autopilot.validate_final_approval_receipt',lambda a,c,**kw:a)
    monkeypatch.setattr(admission,'assert_manifest_matches_approved_snapshot',lambda m,s,**kw:seen.append(m['approved_target_labels']))
    status,payload=admission.endpoint(data,completion=completion,context=lambda _:trusted)
    assert status==503 and payload['queued'] is False
    assert payload['admission']['approved_target_labels']==list(TIKTOK_TARGET_ORDER)
    assert payload['execution_authority'] is False and seen==[list(TIKTOK_TARGET_ORDER)]


@pytest.mark.parametrize('field',['allowlist','policy_path','retired_target_labels','dispatch'])
def test_client_cannot_select_policy_or_scope(field):
    data,trusted=fixture();data[field]=[]
    with pytest.raises(admission.ContinuationAdmissionError):
        admission.validate_admission(data,completion=False,**trusted)


@pytest.mark.parametrize('field',['offer_id','plan_id','snapshot_digest','candidate_digest'])
def test_request_identity_tamper_rejected(field):
    data,trusted=fixture();data[field]='wrong'
    with pytest.raises(ValueError): admission.validate_admission(data,completion=False,**trusted)


def test_retired_scope_rejected_before_asset_or_provider():
    data,trusted=fixture();p=trusted['policy']
    p['retired_target_labels']=[data['continuation_manifest']['recovery_target_labels'][-1]]
    p['policy_digest']=admission._digest({k:v for k,v in p.items() if k!='policy_digest'})
    trusted['asset_digest_reader']=lambda _:pytest.fail('retired target must precede callbacks')
    with pytest.raises(ValueError,match='retired'):admission.validate_admission(data,completion=False,**trusted)


@pytest.mark.parametrize('policy',[None,{}, {'retired_target_labels':[]}])
def test_missing_policy_is_not_an_empty_retired_set(policy):
    data,trusted=fixture();trusted['policy']=policy
    with pytest.raises(ValueError):admission.validate_admission(data,completion=False,**trusted)


def test_late_projection_failure_rejects_entire_admission(monkeypatch):
    data,trusted=fixture()
    monkeypatch.setattr('shared_platform.publication_autopilot.validate_release_candidate_for_execution',lambda c,**kw:c)
    monkeypatch.setattr('shared_platform.publication_autopilot.validate_final_approval_receipt',lambda a,c,**kw:a)
    def reject(*a,**kw):raise ValueError('last target projection differs')
    monkeypatch.setattr(admission,'assert_manifest_matches_approved_snapshot',reject)
    code,result=admission.endpoint(data,completion=False,context=lambda _:trusted)
    assert code==409 and result['external_write_count']==0


def test_pinned_policy_load_and_missing_does_not_create(tmp_path):
    _,trusted=fixture();path=tmp_path/'policy.json'
    raw=json.dumps(trusted['policy']).encode();path.write_bytes(raw)
    digest=hashlib.sha256(raw).hexdigest()
    assert admission.load_scope_policy(path,expected_sha256=digest,root=tmp_path)==trusted['policy']
    with pytest.raises(ValueError):admission.load_scope_policy(path,expected_sha256='0'*64,root=tmp_path)
    missing=tmp_path/'missing.json'
    with pytest.raises(FileNotFoundError):admission.load_scope_policy(missing,expected_sha256=digest,root=tmp_path)
    assert not missing.exists()
    (tmp_path/'child').mkdir()
    with pytest.raises(ValueError):admission.load_scope_policy(path,expected_sha256=digest,root=tmp_path/'child')


def test_unconfigured_route_reports_not_available():
    assert admission.endpoint({},completion=False)[0]==503
    assert admission.endpoint({},completion=True)[1]['code']=='CONTINUATION_ADMISSION_NOT_CONFIGURED'


def test_both_real_handler_routes_are_present_without_runner_bypass():
    source=(Path(__file__).parents[1]/'modules/products/server.py').read_text(encoding='utf8')
    for route in ('publish-tiktok-continuation','publish-tiktok-approved-completion'):
        assert source.count('/api/product-workspace/'+route)>=2
    assert 'return endpoint(data, completion=completion)' in source


def test_self_declared_digest_fields_do_not_replace_persisted_validation(monkeypatch):
    data,trusted=fixture()
    trusted['candidate']['status']='UNAPPROVED'
    # Digest strings still equal the approved manifest. Real candidate validator
    # must reject the incomplete/unapproved document before assets are read.
    called=[]
    monkeypatch.setattr(admission,'assert_manifest_matches_approved_snapshot',lambda *a,**kw:called.append(True))
    with pytest.raises(ValueError):admission.validate_admission(data,completion=False,**trusted)
    assert called==[]
