"""Owned same-claim recovery through the real signed client; no provider IO."""
import base64
import gzip
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from modules.products import server
from modules.products import release_adapters
from modules.miaoshou import client
from shared_platform import native_common_edit_boundary as boundary
from shared_platform import native_common_technical_execution as technical
from shared_platform.release_store import ReleaseAuthorizationError, ReleaseStore
from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup
from test_native_common_service_facts import _installed, _closed_signed_transport
from test_common_detail_observation import CONFIG, LocalResponse


def _accepted_then_bad_read(live, monkeypatch, tmp_path, compressed=False):
    store, plan, payload, task = _setup(live, monkeypatch, tmp_path)
    _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload)
    original = client.urllib.request.urlopen
    business = b'{"result" : "success", "data" : {}}\n'
    wire = gzip.compress(business, mtime=0) if compressed else business
    failed = [False]
    def closed(request, timeout):
        value = original(request, timeout)
        if request.full_url.endswith(boundary.EDIT_PATH):
            return LocalResponse(wire, 'gzip' if compressed else 'identity', request.full_url)
        if boundary.EDIT_PATH in calls and not failed[0]:
            with store._connect_readonly() as db:
                row = db.execute('SELECT * FROM release_target_submissions').fetchone()
                assert row is not None  # COMMITTED before this original READ.
                receipt = json.loads(row['evidence_json'])
                assert base64.b64decode(receipt['edit_wire']['wire_base64']) == wire
                assert db.execute('SELECT technical_execution_state FROM release_runs').fetchone()[0] == 'UNKNOWN'
                assert db.execute('SELECT COUNT(*) FROM release_target_readbacks').fetchone()[0] == 0
            failed[0] = True
            raise OSError('owned READ lost after known EDIT acceptance')
        return value
    monkeypatch.setattr(client.urllib.request, 'urlopen', closed)
    monkeypatch.setattr(client.urllib.request, 'build_opener', lambda *handlers:SimpleNamespace(open=closed))
    request = {'offer_id':payload['product_id'], 'plan_id':plan['plan_id'], 'confirm_miaoshou_write':True}
    code, result = server._prepare_miaoshou_release(request)
    assert code == 502 and result['state'] == 'UNKNOWN' and result['edit_acceptance_retained'] is True, result
    return store, plan, payload, calls, request, result, wire, business


@pytest.mark.parametrize('compressed', [False, True], ids=['identity','gzip'])
def test_original_accepted_wire_commits_before_read_and_restart_closes_same_attempt_only(
        live, monkeypatch, tmp_path, compressed):
    store, plan, payload, calls, request, first, wire, business = _accepted_then_bad_read(
        live, monkeypatch, tmp_path, compressed)
    run_id = first['native_run_id']
    with store._connect_readonly() as db:
        stored = db.execute('SELECT * FROM release_target_submissions').fetchone()
        packet = json.loads(stored['evidence_json'])['edit_wire']
        assert base64.b64decode(packet['business_base64']) == business
        assert packet['wire_sha256'] == sha256(wire).hexdigest()
        assert packet['business_sha256'] == sha256(business).hexdigest()
        failure = json.loads(db.execute('SELECT evidence_json FROM release_target_failure_events').fetchone()[0])
        assert failure['readback_verified'] is False and failure['confirmed_write_count'] == 'UNKNOWN'
        assert db.execute('SELECT attempts FROM release_target_runs').fetchone()[0] == 1
    public = json.dumps(store.get_run(run_id))
    assert 'wire_base64' not in public and 'request_base64' not in public
    assert CONFIG['app_id'] not in public and CONFIG['app_secret'] not in public
    # A new transport/boundary does not recreate the original consumed attempt.
    boundary.install_service_boundary(boundary.service_boundary()._config)
    code, result = server._prepare_miaoshou_release(request)
    assert code == 200 and result['mode'] == 'same_attempt_readonly_recovery', result
    assert result['native_run']['run_id'] == run_id and result['native_run']['state'] == 'CONFIRMED_WRITE'
    assert result['external_writes_performed'] == [] and calls.count(boundary.EDIT_PATH) == 1
    assert store.get_run(run_id)['targets'][0]['status'] == 'SUCCEEDED'
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 1
        assert db.execute('SELECT attempts FROM release_target_runs').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
        assert db.execute('SELECT evidence_json FROM release_target_submissions').fetchone()[0] == stored['evidence_json']
        read = json.loads(db.execute('SELECT evidence_json FROM release_target_readbacks').fetchone()[0])
        assert read['prior_external_write_evidence_digest'] == stored['evidence_digest']
    before = list(calls)
    code, again = server._prepare_miaoshou_release(request)
    assert code == 200 and again['idempotent'] is True and calls == before


def test_lost_edit_reply_has_no_acceptance_and_never_retries_or_reads_matching_content(live, monkeypatch, tmp_path):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload, fail_edit=True)
    request = {'offer_id':payload['product_id'],'plan_id':plan['plan_id'],'confirm_miaoshou_write':True}
    code, first = server._prepare_miaoshou_release(request)
    assert code == 502 and first['edit_acceptance_retained'] is False
    before = list(calls)
    code, again = server._prepare_miaoshou_release(request)
    assert code == 409 and again['state'] == 'UNKNOWN', again
    assert again['recovery_reason'] == 'COMMON_TECHNICAL_ACCEPTED_EDIT_RECEIPT_MISSING'
    assert calls == before and calls.count(boundary.EDIT_PATH) == 1
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_target_submissions').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_target_readbacks').fetchone()[0] == 0


@pytest.mark.parametrize('damage', ['wire','attempt','account'], ids=['wire-bytes','cross-attempt','cross-account'])
def test_rehashed_receipt_drift_cannot_enable_read_or_confirm_write(live, monkeypatch, tmp_path, damage):
    store, plan, payload, calls, request, first, _, _ = _accepted_then_bad_read(live, monkeypatch, tmp_path)
    with technical._existing_transaction(store) as db:
        value = json.loads(db.execute('SELECT evidence_json FROM release_target_submissions').fetchone()[0])
        if damage == 'wire':value['edit_wire']['wire_base64'] = base64.b64encode(b'{"result":"success"}').decode()
        elif damage == 'attempt':value['attempt'] += 1
        else:value['edit_wire']['credential_scope_digest'] = 'f'*64
        raw = technical._bytes(value).decode()
        db.execute('UPDATE release_target_submissions SET evidence_json=?,evidence_digest=?',
                   (raw,sha256(raw.encode()).hexdigest()))
    before = list(calls)
    code, result = server._prepare_miaoshou_release(request)
    assert code == 409 and result['state'] == 'UNKNOWN', result
    assert calls == before and technical.inspect_existing(store,first['native_run_id'])['state'] == 'UNKNOWN'


def test_normalized_caller_response_cannot_create_receipt_or_missing_database(tmp_path):
    store = ReleaseStore(tmp_path/'never-created.sqlite3')
    with pytest.raises(ReleaseAuthorizationError, match='EDIT_CAPTURE_REQUIRED'):
        technical._retain_edit_acceptance(store,'r3-common:caller','caller-run',1,{}, {'result':'success'})
    assert not store.path.exists()


def test_current_signing_change_preserves_unknown_without_second_edit_or_read(live, monkeypatch, tmp_path):
    store, plan, payload, calls, request, first, _, _ = _accepted_then_bad_read(live, monkeypatch, tmp_path)
    config = boundary.service_boundary()._config
    path = config.root/'config/miaoshou.local.json'
    path.write_text(json.dumps({**CONFIG,'app_id':CONFIG['app_id']+'-foreign'}),encoding='utf-8')
    before = list(calls)
    code, result = server._prepare_miaoshou_release(request)
    assert code == 409 and result['state'] == 'UNKNOWN'
    assert result['recovery_reason'] == 'COMMON_SIGNING_CURRENT_CONTEXT_CHANGED'
    assert calls == before and technical.inspect_existing(store,first['native_run_id'])['state'] == 'UNKNOWN'


@pytest.mark.parametrize('damage',['title','sku','oss','extra'],
    ids=['frozen-title','frozen-sku-logistics','invalid-oss-md5','extra-request-field'])
def test_fully_rehashed_accepted_request_must_still_equal_exact_frozen_mutation(live, monkeypatch, tmp_path, damage):
    store, plan, payload, calls, request, first, _, _ = _accepted_then_bad_read(live,monkeypatch,tmp_path)
    with technical._existing_transaction(store) as db:
        value=json.loads(db.execute('SELECT evidence_json FROM release_target_submissions').fetchone()[0])
        packet=value['edit_wire']
        body=json.loads(base64.b64decode(packet['request_base64']))
        if damage=='title':body['editCommonCollectBoxDetail']['title']='different frozen business title'
        elif damage=='sku':next(iter(body['editCommonCollectBoxDetail']['skuMap'].values()))['weight']=998
        elif damage=='oss':body['ossMd5']=None
        else:body['anotherMutation']={'title':'not in the exact endpoint schema'}
        raw=technical._bytes(body)
        packet['request_base64']=base64.b64encode(raw).decode()
        packet['request_sha256']=sha256(raw).hexdigest()
        value['request_canonical_sha256']=sha256(raw).hexdigest()
        encoded=technical._bytes(value).decode()
        db.execute('UPDATE release_target_submissions SET evidence_json=?,evidence_digest=?',
                   (encoded,sha256(encoded.encode()).hexdigest()))
    before=list(calls)
    code,result=server._prepare_miaoshou_release(request)
    assert code==409 and result['state']=='UNKNOWN',result
    expected=('COMMON_TECHNICAL_ACCEPTED_MUTATION_CHANGED' if damage in {'title','sku'} else
              'COMMON_TECHNICAL_ACCEPTED_REQUEST_SCHEMA_CHANGED')
    assert result['recovery_reason']==expected
    assert calls==before and technical.inspect_existing(store,first['native_run_id'])['state']=='UNKNOWN'


@pytest.mark.parametrize('damage',['missing-acceptance','wrong-reference'])
def test_later_completion_and_census_cannot_forget_original_accepted_attempt(live,monkeypatch,tmp_path,damage):
    store,plan,payload,calls,request,first,_,_=_accepted_then_bad_read(live,monkeypatch,tmp_path)
    code,result=server._prepare_miaoshou_release(request)
    assert code==200 and result['native_run']['state']=='CONFIRMED_WRITE',result
    run_id=first['native_run_id']
    with technical._existing_transaction(store) as db:
        if damage=='missing-acceptance':db.execute('DELETE FROM release_target_submissions')
        else:
            value=json.loads(db.execute('SELECT evidence_json FROM release_target_readbacks').fetchone()[0])
            rebound=release_adapters.bind_native_common_readback(value,{**value,
                'prior_external_write_evidence_digest':'f'*64})
            rebound.pop('stored_common_lineage')
            target=dict(db.execute('SELECT * FROM release_target_runs').fetchone())
            rebound=store._validated_common_observation(db,target,rebound)
            raw=technical._bytes(rebound).decode()
            db.execute('UPDATE release_target_readbacks SET evidence_json=?,evidence_digest=?',
                       (raw,sha256(raw.encode()).hexdigest()))
    from shared_platform.native_common_retained_completion import read_retained_completion
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.native_common_budget_facts import census_same_snapshot
    before=list(calls)
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        reader=NativeCommonSourceReader(store)
        error=('ACCEPTED_EDIT_RECEIPT_MISSING' if damage=='missing-acceptance' else 'ACCEPTANCE_REFERENCE_CHANGED')
        with pytest.raises(ReleaseAuthorizationError,match=error):
            read_retained_completion(reader,db,plan['plan_id'],run_id)
        source=reader.read_source_facts(db,plan['plan_id'])
        census=census_same_snapshot(store,db,payload,source)
        assert census['local_observed_confirmed_writes']==0
        assert any(row['run_id']==run_id for row in census['unclassified_history'])
    assert calls==before


@pytest.mark.parametrize('damage',['missing-acceptance','changed-accepted-attempt'])
def test_native_reuse_census_rechecks_predecessor_accepted_proof(live,monkeypatch,tmp_path,damage):
    from test_native_common_technical_execution import _successor
    from shared_platform.native_common_budget_facts import census_same_snapshot
    from shared_platform.native_common_retained_completion import read_retained_completion
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    store,plan,payload,calls,request,first,_,_=_accepted_then_bad_read(live,monkeypatch,tmp_path)
    code,confirmed=server._prepare_miaoshou_release(request)
    assert code==200 and confirmed['native_run']['state']=='CONFIRMED_WRITE',confirmed
    successor,next_payload,_=_successor(store,payload,'real-readonly-reuse-proof')
    code,reused=server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':successor['plan_id'],'reuse_miaoshou_readback':True})
    assert code==200 and reused['native_run']['state']=='READONLY_REUSE',reused
    before=list(calls)
    with technical._existing_transaction(store) as db:
        if damage=='missing-acceptance':
            db.execute('DELETE FROM release_target_submissions WHERE run_id=?',(first['native_run_id'],))
        else:
            row=db.execute('SELECT evidence_json FROM release_target_submissions WHERE run_id=?',
                           (first['native_run_id'],)).fetchone()
            value=json.loads(row[0]);value['attempt']+=1
            raw=technical._bytes(value).decode()
            db.execute('UPDATE release_target_submissions SET evidence_json=?,evidence_digest=? WHERE run_id=?',
                (raw,sha256(raw.encode()).hexdigest(),first['native_run_id']))
    with store._connect_readonly() as db:
        db.execute('BEGIN');reader=NativeCommonSourceReader(store)
        source=reader.read_source_facts(db,successor['plan_id'])
        census=census_same_snapshot(store,db,next_payload,source)
        assert census['local_observed_confirmed_writes']==0
        assert census['local_observed_readonly_reuses']==0
        assert {r['run_id'] for r in census['unclassified_history']} >= {
            first['native_run_id'],reused['native_run']['run_id']}
        with pytest.raises(ReleaseAuthorizationError,match='ACCEPTED_EDIT'):
            read_retained_completion(reader,db,successor['plan_id'],reused['native_run']['run_id'])
    assert calls==before and calls.count(boundary.EDIT_PATH)==1
