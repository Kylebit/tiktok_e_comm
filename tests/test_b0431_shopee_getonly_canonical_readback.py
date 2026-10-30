"""Original post-R79 clone and captured VN READs, with explicit source/actor boundaries.

The prior official Global association is an offline response fixture; this is
not a new live GET or a genuine current source/Windows helper acceptance test.
No mutation, credential write or schema installation is permitted.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from shared_platform import native_sole_final_readback as readback
from shared_platform import native_approved_discount_prices as prices
from shared_platform import native_companion_promotions as promotions
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService
from shared_platform.native_sole_final_execution import _NativeExecution,_ACTIVE
from shared_platform.native_sole_final_service import NativeSoleFinalService
from shared_platform.release_store import ReleaseStore


def load(path,expected=None):
    raw=Path(path).read_bytes()
    if expected:assert hashlib.sha256(raw).hexdigest()==expected
    return json.loads(raw)


def tables(store):
    with store._connect_readonly() as db:
        return {name:[dict(r) for r in db.execute('SELECT * FROM '+name)] for name in (
            'release_target_failure_events','release_approvals','native_sole_final_decisions',
            'release_target_submissions','approved_publication_snapshots',
            'native_final_budget_scopes','native_final_budget_reservations',
            'native_final_budget_dispatches','native_final_budget_outcomes')}


@pytest.fixture
def captured(tmp_path,monkeypatch):
    supplied=os.environ.get('ORBIT_B0431_VN_INPUTS')
    if not supplied:pytest.skip('explicit post-R79 private clone/captures not supplied')
    nb=Path(supplied)
    folder=nb/'targets/native-ozon-shopee-frozen-completion-source-20261028'
    source=folder/'post-r79-idle-20261028T084837486761Z/ORIGINAL_RELEASE_STORE_POST_R79_FULL.private.db'
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    assert before=='85d3087383ced7f9a750360b3452d0e7640cf699e65bbea72981ae2e6cd7241f'
    shutil.copy2(source,tmp_path/'private-original-full-clone.db')
    store=ReleaseStore(tmp_path/'private-original-full-clone.db')
    plan=store.get_plan(prices.B_PLAN)
    with store._connect_readonly() as db:
        owner=db.execute('SELECT owner_sid FROM native_sole_final_decisions WHERE decision_id=?',(prices.B_DECISION,)).fetchone()[0]
        facts=readback._vn_facts(db)
    assert facts['target']['status']=='FAILED' and facts['target']['attempts']==4 and len(facts['ledger'])==49
    service=NativeSoleFinalDecisionService(store,lambda:{})
    monkeypatch.setattr(service,'_actor',lambda:{'owner_sid':owner})  # Explicit existing same-SID actor boundary.
    @contextmanager
    def operation():yield service
    runtime=object.__new__(NativeSoleFinalService)
    runtime.store=store;runtime.operation=operation
    runtime._require_original_decision_scope=lambda decision:None  # Current installed source is A's independent boundary.
    runtime._execution_lock=threading.RLock();runtime._closed=runtime._stopping=False
    runtime._execution_queue=deque();runtime._execution_busy_decision=None
    runtime._readback_schedule={};runtime._pending_count=lambda:0
    starts=[];runtime._start_owned_worker=lambda:starts.append('READ_FIFO_ONLY')
    gate={'plan':plan,'payload':plan['payload'],'run':store.get_run(prices.B_RUN)}
    def current(active,data,*,store):
        assert active.decision_id==prices.B_DECISION and store is runtime.store
        assert data['plan_id']==prices.B_PLAN
        with store._connect_readonly() as db:service._decision(db,prices.B_DECISION,owner_sid=owner)
        return {**gate,'run':store.get_run(prices.B_RUN)},None
    monkeypatch.setattr(_NativeExecution,'gate',current)  # No current helper/provider/source installation in this offline test.
    doc=load(nb/'SHOPEE_FOUR_EXISTING_ITEM_FROZEN_COMPLETION_TECHNICAL_AUTHORITY_R79.json',
             'cbdbe8b22dfd17965ebf629a8c7484302570f72fac283d4376fee4aca87025cb')
    original_ref=doc['official_item_refs']['shopee:VN']['models_ref']
    original=load(original_ref['path'],original_ref['sha256'])['response']['model']
    material={'source_digest':'a'*64,'image_ids':doc['approved_image_ids']['shopee:VN'],'original_models':original}
    monkeypatch.setattr(readback,'_vn_source',lambda active,plan:deepcopy(material))
    actual=folder/'actual-deployment-001'
    responses=[
        {'path':'/api/v2/product/get_item_base_info','params':{'item_id_list':'52420043241'},'response':
            load(actual/'SHOPEE_VN_52420043241_ITEM.response.private.json','e9188fdb9f9d04920bbc350cb0ed37fa2fad83e963668f0b2728a90d2fdc1034')},
        {'path':'/api/v2/product/get_model_list','params':{'item_id':52420043241},'response':
            load(actual/'SHOPEE_VN_52420043241_MODELS.response.private.json','6dcb6fe89c367b8a76f425d18f6a90457a966edb39d285e0fba797477cbe7ecf')},
        # Reconstruct the already officially proven link as a named GET fixture.
        # Runtime must obtain this association afresh from its exact merchant GET.
        {'path':'/api/v2/global_product/get_global_item_id','params':{'shop_id':1723948773,'item_id_list':'52420043241'},
            'response':{'error':'','response':{'item_id_map':[{'item_id':52420043241,'global_item_id':43135542690}]}}},
        {'path':'/api/v2/global_product/get_global_model_list','params':{'global_item_id':43135542690},'response':
            load(nb/'targets/native-stock-category-source-fix-20261028/actual-deployment-20261028-001/SHOPEE_GLOBAL_43135542690_MODELS.response.private.json',
                 'b3394116e75041620caca521c3bbc6d52bbc4f6d6538a330bdb939d98e9ed7fd')},
    ]
    # Exercise the real four-GET factory and per-call actor/auth pin guards.
    from modules.shopee import client,auth
    from shared_platform import native_canonical_provider_edges as edges
    tokenfile=tmp_path/'EXISTING_AUTH_PIN_FIXTURE';tokenfile.write_bytes(b'OFFLINE_ONLY_NO_CREDENTIAL')
    monkeypatch.setattr(auth,'token_path',lambda:tokenfile)
    monkeypatch.setattr(edges,'_existing_shopee_context',lambda region:SimpleNamespace(
        shop_id=1723948773,merchant_id=4970102,shop_token='OFFLINE',merchant_token='OFFLINE'))
    reads=[]
    def get(path,identity,token,params,**kwargs):
        index=len(reads);assert index<4
        row=responses[index];assert (path,params)==(row['path'],row['params'])
        if kwargs.get('call_guard'):kwargs['call_guard']()
        reads.append((path,identity,deepcopy(params)))
        return deepcopy(row['response'])
    monkeypatch.setattr(client,'shop_get',get);monkeypatch.setattr(client,'merchant_get',get)
    for name in ('shop_post','merchant_post'):
        monkeypatch.setattr(client,name,lambda *a,**k:pytest.fail('Merchant mutation forbidden'),raising=False)
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('Network forbidden'))
    yield SimpleNamespace(store=store,service=service,runtime=runtime,plan=plan,facts=facts,gate=gate,
        material=material,responses=responses,reads=reads,starts=starts,owner=owner,nb=nb,tmp=tmp_path)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


def test_original_failed4_public_queue_observer_CAS_then_original_discount_quote(captured,monkeypatch):
    c=captured;before=tables(c.store);target=next(t for t in c.gate['run']['targets'] if t['target_label']=='shopee:VN')
    with pytest.raises(ApprovalBlocked,match='VERIFIED_CANONICAL_READBACK_REQUIRED'):
        promotions.canonical_prerequisite(target,'shopee:VN')
    # Preserve the old public rejection on the same actual FAILED4 rows.
    root=Path(__file__).resolve().parents[1]
    raw=subprocess.check_output(['git','-C',str(root),'show','78ca73694d68a17ae62350b05e4c4731211e9686:shared_platform/native_sole_final_service.py'])
    namespace={'__name__':'shared_platform.OLD_VN_GATE'};exec(compile(raw,'ORIGINAL_78CA_READ_QUEUE','exec'),namespace)
    with pytest.raises(ApprovalBlocked,match='ACCEPTED_SUBMISSION_REQUIRED'):
        namespace['NativeSoleFinalService'].submit_readback(c.runtime,prices.B_DECISION)
    assert c.reads==[] and tables(c.store)==before
    code,receipt=c.runtime.submit_readback(prices.B_DECISION)
    assert code==202 and receipt['readonly_recheck'] is True and c.starts==['READ_FIFO_ONLY']
    result=readback.observe_submissions(c.runtime,prices.B_DECISION)
    assert result['pending'] is False and result['observations']==[{
        'target_label':'shopee:VN','verified':True,'source':'official_shopee_shop_api','external_writes_performed':[]}]
    assert len(c.reads)==4 and tables(c.store)==before
    run=c.store.get_run(prices.B_RUN);vn=next(t for t in run['targets'] if t['target_label']=='shopee:VN')
    assert vn['status']=='SUCCEEDED' and vn['attempts']==4 and vn['idempotency_key']==target['idempotency_key']
    proof=promotions.canonical_prerequisite(vn,'shopee:VN')
    evidence=vn['readback']['evidence']
    assert len(evidence['checks'])==15 and all(evidence['checks'].values())
    assert evidence['external_writes_performed']==[] and evidence['native_shopee_failed_readback']['new_merchant_writes']==0
    assert evidence['canonical_platform_result']['external_write_count']==3  # Preserve historical accepted effects.
    assert len(evidence['canonical_platform_result']['targets'][0]['evidence']['official_catalog_rows'])==3
    assert proof['external_id']=='shopee:1723948773:item:52420043241'
    # A second explicit READ round is not queued once this exact canonical row exists.
    with pytest.raises(ApprovalBlocked,match='ACCEPTED_SUBMISSION_REQUIRED'):c.runtime.submit_readback(prices.B_DECISION)
    assert len(c.reads)==4
    # Connect the persisted real strict result to the unchanged B4 quote consumer.
    from shared_platform import agent_prepared_v4_targets as adopted
    decision_path=c.nb/'USER_SHOPEE_DISCOUNT30_DECISION_R81.json'
    ref={'path':str(decision_path),'sha256':prices.B_DECISION_SHA}
    doc=prices.read_b0431_discount_decision(ref)
    taskdb=c.tmp/'EXPLICIT_SOURCE_BOUNDARY.db';record={'b0431_discount_decision_ref':ref,'b0431_discount_decision_digest':prices._digest(doc)}
    encoded=json.dumps(record,sort_keys=True,separators=(',',':'))
    with sqlite3.connect(taskdb) as db:
        db.execute('CREATE TABLE workbench_events(task_id TEXT,event_type TEXT,detail_json TEXT)')
        db.execute('INSERT INTO workbench_events VALUES(?,?,?)',(prices.B_TASK,prices.B_EVENT,
            json.dumps({'record_json':encoded,'record_digest':hashlib.sha256(encoded.encode()).hexdigest(),'signature':'SOURCE_BOUNDARY_ONLY'})))
    bound=SimpleNamespace(engine=SimpleNamespace(store=SimpleNamespace(path=taskdb)))
    monkeypatch.setattr(adopted,'_scope',lambda a,p:(bound,{'task_id':prices.B_TASK},{'owner_sid':c.owner}))
    active=_NativeExecution(c.service,prices.B_DECISION,c.runtime);active.plan_id=prices.B_PLAN
    token=_ACTIVE.set(active)
    try:
        with promotions._execution_scope(active,c.plan,run,promotions.selected_targets(['promotion:shopee:VN'])):
            quote=prices.retained_discount_price(c.plan['payload'],'shopee:VN')
            assert quote['sale_after_discount']=='205800' and quote['model_skus']==prices.B_MODELS
        tk=next(t for t in run['targets'] if t['target_label']=='tiktok:LH_MY')
        with pytest.raises(ApprovalBlocked,match='VERIFIED_CANONICAL_READBACK_REQUIRED'):
            promotions.canonical_prerequisite(tk,'tiktok:LH_MY')
    finally:_ACTIVE.reset(token)


def test_wrong_or_missing_official_facts_and_unowned_proof_never_persist(captured):
    c=captured;before=tables(c.store)
    changes=[lambda r:r[0]['response']['response']['item_list'][0].update(item_id=1),
        lambda r:r[1]['response']['response']['model'].pop(),
        lambda r:r[2]['response']['response']['item_id_map'][0].update(global_item_id=1),
        lambda r:r[0]['response']['response']['item_list'][0].update(weight='0.3'),
        lambda r:r[1]['response']['response']['model'][0]['stock_info_v2']['seller_stock'].pop(),
        lambda r:r[0].update(params={'item_id_list':'WRONG'})]
    for change in changes:
        altered=deepcopy(c.responses);change(altered)
        with pytest.raises((ApprovalBlocked,ValueError,KeyError,RuntimeError)):
            readback._vn_verified_evidence(c.facts,c.material,altered)
    active=_NativeExecution(c.service,prices.B_DECISION,c.runtime);active.plan_id=prices.B_PLAN
    token=_ACTIVE.set(active)
    try:
        fake=readback._ShopeeFailedReadback(readback._ozon_sha(c.facts),'a'*64,'{}')
        with pytest.raises(ApprovalBlocked,match='NONCALLER_PROOF_REQUIRED'):
            c.store._record_native_shopee_failed_readback(fake)
        with pytest.raises(ApprovalBlocked,match='MUTATION_FORBIDDEN'):readback._VNGetOnly(active).list_item()
    finally:_ACTIVE.reset(token)
    assert tables(c.store)==before and c.reads==[]


def test_postread_actor_change_and_same_connection_ledger_CAS_hold(captured,monkeypatch):
    c=captured;before=tables(c.store)
    # A changed current gate after all four GETs cannot mint the Store proof.
    gates=[]
    def changed(active,data,*,store):
        gates.append(True)
        return (c.gate,None) if len(gates)==1 else (None,(409,{'error':'CURRENT_SOURCE_CHANGED'}))
    monkeypatch.setattr(_NativeExecution,'gate',changed)
    result=readback.observe_submissions(c.runtime,prices.B_DECISION)
    assert result['observations'][0]['verified'] is False and len(c.reads)==4 and tables(c.store)==before
    # A caller/competing ledger change between READ and the writer CAS also fails closed.
    original=c.store._record_native_shopee_failed_readback
    def corrupt_then_commit(proof):
        with sqlite3.connect(c.store.path) as db:
            db.execute("INSERT INTO native_final_budget_reservations VALUES(?,23,'shopee:VN','target','list_regional_item',2,'UNKNOWN')",
                (next(r['scope_key'] for r in c.facts['ledger'] if r['target_label']=='shopee:VN'),))
        return original(proof)
    monkeypatch.setattr(c.store,'_record_native_shopee_failed_readback',corrupt_then_commit)
    monkeypatch.setattr(readback._VNGetOnly,'observe',lambda self:deepcopy(c.responses))
    monkeypatch.setattr(_NativeExecution,'gate',lambda a,d,store:(c.gate,None))
    result=readback.observe_submissions(c.runtime,prices.B_DECISION)
    assert result['observations'][0]['verified'] is False
    with c.store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_target_readbacks WHERE run_id=? AND target_label=?',(prices.B_RUN,'shopee:VN')).fetchone()[0]==0
        assert tuple(db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?',(prices.B_RUN,'shopee:VN')).fetchone())==('FAILED',4)
