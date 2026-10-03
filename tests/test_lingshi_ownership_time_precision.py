"""Deterministic real ownership binding at the timestamp format's precision."""
from datetime import datetime,timedelta,timezone
import json
import os

import pytest
from modules.sourcing import image_generation_checkpoint as cp

VERIFIED=datetime(2026,9,5,8,0,0,123456,tzinfo=timezone.utc)
EPOCH=datetime(1970,1,1,tzinfo=timezone.utc)
VERIFIED_US=(VERIFIED-EPOCH)//timedelta(microseconds=1)
IDENTITY={'offer_id':'A','brand_id':'brand','role':'cover'}

def prepare(tmp_path,monkeypatch,*,offset_ns=900,verified=VERIFIED,now=None):
    a=cp.ImageCheckpoint(tmp_path,kind='brand',business_identity=IDENTITY,
        request_identity={'prompt':'A','model':'fixture'},model='fixture',source_identity_complete=True)
    a.persist(a.open_for_execution(),status='SUBMISSION_UNKNOWN')
    a.path.write_bytes(b'damaged snapshot')
    a.identity_path.write_bytes(b'damaged identity')
    mtime_ns=VERIFIED_US*1000+offset_ns
    for path in (a.path,a.identity_path,a.journal_path):
        os.utime(path,ns=(mtime_ns,mtime_ns))
        assert path.stat().st_mtime_ns==mtime_ns
    proof={**cp.inspect_checkpoint_ownership(a.path),'business_digest':a.business_digest,'request_digest':a.request_digest,
        'provider':cp.PROVIDER,'verified_at':verified.isoformat(),'verified_by':'fixture-verifier',
        'evidence_ref':'fixture://time/ownership','evidence_sha256':'sha256:'+'e'*64}
    class FixedDateTime(datetime):
        @classmethod
        def now(cls,tz=None):return (now or VERIFIED+timedelta(seconds=1)).astimezone(tz)
    monkeypatch.setattr(cp,'datetime',FixedDateTime)
    return a,proof

def bind(a,proof):
    return cp.bind_checkpoint_ownership(a.path,business_identity=IDENTITY,request_digest=a.request_digest,evidence=proof)

@pytest.mark.parametrize('offset_ns',[100,900])
@pytest.mark.parametrize('zone',[timezone.utc,timezone(timedelta(hours=8))])
def test_same_microsecond_source_and_later_verification_bind_without_refunding_unknown(tmp_path,monkeypatch,offset_ns,zone):
    a,proof=prepare(tmp_path,monkeypatch,offset_ns=offset_ns,verified=VERIFIED.astimezone(zone))
    original={p:p.read_bytes() for p in (a.path,a.identity_path,a.journal_path)}
    row=bind(a,proof)
    assert row['authority']=='OWNERSHIP_ONLY_NO_TASK_OR_CHARGE_FINDING'
    assert {p:p.read_bytes() for p in original}==original
    with pytest.raises(cp.CheckpointRecoveryRequired):a.open_for_execution()
    b=cp.ImageCheckpoint(tmp_path,kind='brand',business_identity={**IDENTITY,'offer_id':'B'},
        request_identity={'prompt':'B','model':'fixture'},model='fixture',source_identity_complete=True)
    assert b.open_for_execution()['status']=='READY'

@pytest.mark.parametrize('age_us',[1,1_000_000])
def test_truly_previous_microsecond_or_old_verification_is_rejected_with_exact_diagnostics(tmp_path,monkeypatch,age_us):
    a,proof=prepare(tmp_path,monkeypatch,verified=VERIFIED-timedelta(microseconds=age_us))
    with pytest.raises(ValueError) as caught:bind(a,proof)
    error=caught.value
    assert error.reason=='VERIFICATION_PREDATES_SOURCE'
    assert error.diagnostics['precision']=='utc-microsecond-floor/v1'
    assert error.diagnostics['verified_epoch_us']==VERIFIED_US-age_us
    assert error.diagnostics['newest_source_epoch_us']==VERIFIED_US
    assert error.diagnostics['newest_source_mtime_ns']==VERIFIED_US*1000+900
    assert error.diagnostics['action']=='REINSPECT_SOURCE_AND_VERIFY_CURRENT_BYTES'

def test_future_verification_one_microsecond_is_distinct_and_rejected(tmp_path,monkeypatch):
    a,proof=prepare(tmp_path,monkeypatch,now=VERIFIED-timedelta(microseconds=1))
    with pytest.raises(ValueError) as caught:bind(a,proof)
    assert caught.value.reason=='FUTURE_VERIFICATION_TIME'
    assert caught.value.diagnostics['observed_epoch_us']==VERIFIED_US-1
    assert caught.value.diagnostics['verified_epoch_us']==VERIFIED_US

def test_source_in_the_next_microsecond_is_not_hidden_by_a_tolerance(tmp_path,monkeypatch):
    a,proof=prepare(tmp_path,monkeypatch,offset_ns=1000)
    with pytest.raises(ValueError) as caught:bind(a,proof)
    assert caught.value.reason=='VERIFICATION_PREDATES_SOURCE'
    assert caught.value.diagnostics['newest_source_epoch_us']==VERIFIED_US+1


@pytest.mark.parametrize('invalid',[None,'not-a-time','2026-09-05T08:00:00.123456'])
def test_missing_malformed_or_naive_verification_is_distinct_and_does_not_echo_input(tmp_path,monkeypatch,invalid):
    a,proof=prepare(tmp_path,monkeypatch)
    if invalid is None:proof.pop('verified_at')
    else:proof['verified_at']=invalid
    with pytest.raises(ValueError) as caught:bind(a,proof)
    assert caught.value.reason=='INVALID_VERIFICATION_TIME'
    assert caught.value.diagnostics['verified_at'] is None
    assert 'not-a-time' not in str(caught.value)

def test_time_precision_does_not_allow_changed_source_hash_in_same_microsecond(tmp_path,monkeypatch):
    a,proof=prepare(tmp_path,monkeypatch)
    a.path.write_bytes(b'changed after inspection')
    ns=VERIFIED_US*1000+900;os.utime(a.path,ns=(ns,ns))
    with pytest.raises(ValueError,match='every current source hash'):bind(a,proof)

def test_existing_ownership_receipt_without_time_diagnostics_is_reused_byte_for_byte(tmp_path,monkeypatch):
    a,proof=prepare(tmp_path,monkeypatch)
    bind(a,proof)
    path=cp._ownership_path(a.path)
    old=json.loads(path.read_text(encoding='utf-8'));old.pop('time_validation',None);cp.atomic_json(path,cp._sealed(old))
    raw=path.read_bytes()
    old_proof={**proof,**cp.inspect_checkpoint_ownership(a.path)}
    assert bind(a,old_proof)==json.loads(path.read_text(encoding='utf-8'))
    assert path.read_bytes()==raw
    with pytest.raises(cp.CheckpointRecoveryRequired):a.open_for_execution()

def test_ownership_for_a_cannot_be_rebound_to_b_at_the_same_time(tmp_path,monkeypatch):
    a,proof=prepare(tmp_path,monkeypatch);bind(a,proof)
    proof={**proof,**cp.inspect_checkpoint_ownership(a.path)}
    other={**IDENTITY,'offer_id':'B'}
    proof['business_digest']=cp.digest({'kind':'brand',**other})
    with pytest.raises(ValueError,match='different business/request'):
        cp.bind_checkpoint_ownership(a.path,business_identity=other,request_digest=a.request_digest,evidence=proof)

def test_legacy_receipt_still_rejects_other_identity_and_source_hash_change(tmp_path,monkeypatch):
    a,proof=prepare(tmp_path,monkeypatch);bind(a,proof)
    receipt_path=cp._ownership_path(a.path)
    legacy=json.loads(receipt_path.read_text(encoding='utf-8'));legacy.pop('time_validation')
    cp.atomic_json(receipt_path,cp._sealed(legacy));raw=receipt_path.read_bytes()
    other={**IDENTITY,'offer_id':'B'}
    other_proof={**proof,**cp.inspect_checkpoint_ownership(a.path),'business_digest':cp.digest({'kind':'brand',**other})}
    with pytest.raises(ValueError,match='different business/request'):
        cp.bind_checkpoint_ownership(a.path,business_identity=other,request_digest=a.request_digest,evidence=other_proof)
    assert receipt_path.read_bytes()==raw
    a.path.write_bytes(b'new source despite old ownership')
    ns=VERIFIED_US*1000+900;os.utime(a.path,ns=(ns,ns))
    assert cp.inspect_checkpoint_ownership(a.path)['existing_ownership'] is None
    with pytest.raises(cp.CheckpointRecoveryRequired):a.open_for_execution()
    b=cp.ImageCheckpoint(tmp_path,kind='brand',business_identity=other,
        request_identity={'prompt':'B','model':'fixture'},model='fixture',source_identity_complete=True)
    with pytest.raises(cp.CheckpointRecoveryRequired):b.open_for_execution()
    assert receipt_path.read_bytes()==raw


def test_validation_uses_integer_microseconds_without_datetime_timestamp_float(tmp_path,monkeypatch):
    a,proof=prepare(tmp_path,monkeypatch)
    current=cp.datetime
    class NoTimestampFloat(current):
        def timestamp(self):raise AssertionError('binary float timestamp used')
    monkeypatch.setattr(cp,'datetime',NoTimestampFloat)
    assert bind(a,proof)['authority']=='OWNERSHIP_ONLY_NO_TASK_OR_CHARGE_FINDING'
