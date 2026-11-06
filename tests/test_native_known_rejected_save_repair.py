"""Owned fixtures exercise compensation, not LIVE actor admission."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from shared_platform import native_known_rejected_save_repair as repair
from shared_platform.native_final_mutation_budget import SCHEMA_SQL, _DurableLedger, _json, _sha, NativeMutationBudgetHold
from shared_platform.publication_provider_journal import ProviderResponseJournal
from shared_platform.release_store import ReleaseStore
from modules.miaoshou.tiktok_v4_drafts import (EXPECTED_SHOP_ID_BY_TARGET, SAVE_SITE_DRAFT_PATH,
    MiaoshouOpenApiTikTokV4DraftTransport, _draft_payload)
from shared_platform.product_publication_live_dependencies import _ResumableTikTokDraftTransport
from test_miaoshou_tiktok_v4_drafts import _snapshot, CategoryResolver, _warehouse_payload


def _pin(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw=_json(value).encode('utf-8');path.write_bytes(raw)
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}


def _owned_initial_repair(tmp_path):
    # A different Offer and a formerly excluded VN target establish that this
    # is native frozen-identity admission, not another per-product constant.
    label='tiktok:LH_VN';other='tiktok:HB_PH';offer='777001122';shop=EXPECTED_SHOP_ID_BY_TARGET[label]
    plan='r3-marketplace:'+offer+':'+'a'*24;run='release-run:'+'b'*24
    decision='native-final-decision:'+'c'*64;snapshot='sha256:'+'d'*64;candidate='e'*64
    binding={'plan_id':plan,'snapshot_digest':snapshot,'platform':'TIKTOK','target_labels':[label,other],
        'shared_maximum':10,'per_target_maximum':3,'decision_id':decision,'candidate_digest':candidate}
    scope=_sha(_json({'plan_id':plan,'snapshot_digest':snapshot,'platform':'TIKTOK'}))
    identity={'target_label':label,'detail_id':'99887766','shop_id':shop}
    events=[{**identity,'operation':op,'outcome':outcome} for op,outcome in
        [('CREATE_DRAFT','ACCEPTED'),('CLAIM_TO_SHOP','ACCEPTED'),('SAVE_DRAFT','REJECTED')]]
    events[-1].update(provider_code='fail',provider_reason=repair.REASON)
    evidence={'schema_version':'native-scoped-v4-target-result/v1','target_label':label,'status':'FAILED',
        'verified':False,'request_attempted':False,'outcome_unknown':False,'reconciliation_required':False,
        'canonical_platform_result':{'platform':'TIKTOK','dispatch_attempted':False,'readback_completed':False,
            'targets':[{'target_label':label,'status':'FAILED','evidence':{'provider_code':'fail',
                'provider_reason':repair.REASON,'outcome_unknown':False,'request_attempted':True,'stage':'PREPARATION'}}]},
        'write_budget':{'unrelated_original_publish':{'target_label':other,'outcome':'UNKNOWN'}}}
    body={'schema':repair.GENERIC_SCHEMA,'offer_id':offer,'candidate_digest':candidate,
        'plan_id':plan,'run_id':run,'decision_id':decision,'snapshot_digest':snapshot,'scope_key':scope,
        **identity,'failure_evidence_digest':_sha(_json(evidence)),'checkpoint_events_digest':_sha(_json(events)),
        'extra_save_maximum':1,'base_target_maximum':3,'combined_target_maximum':4}
    provider_identity=repair._provider_identity(body)
    root=tmp_path/'runtime';cp_path=root/offer/'1'/provider_identity['run_id']/'tiktok-draft-checkpoint.json'
    checkpoint={'schema_version':'tiktok-v4-draft-checkpoint/v1','offer_id':offer,'snapshot_digest':snapshot,
        'run_id':provider_identity['run_id'],'events':events}
    checkpoint['checkpoint_digest']='sha256:'+_sha(_json(checkpoint))
    body['checkpoint_ref']=_pin(cp_path,checkpoint)
    journal=ProviderResponseJournal(root,provider_identity);journal.initialize()
    original_request={'provider':'miaoshou','method':'POST','path':SAVE_SITE_DRAFT_PATH,
        'account':{'app_id':'synthetic-owned-fixture'},'request_body_sha256':'f'*64}
    key,_=journal.claim(original_request)
    raw=_json({'result':'fail','code':'fail','message':repair.REASON}).encode('utf-8')
    journal.retain(key,200,raw,{'Content-Type':'application/json'})
    body['provider_rejection']={'path':str(journal.path),'key':key,'response_sha256':hashlib.sha256(raw).hexdigest(),
        'original_execution_digest':_sha(_json(provider_identity))}
    store=ReleaseStore(tmp_path/'owned.db')
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row;db.executescript(SCHEMA_SQL+repair.SCHEMA+'''
            CREATE TABLE release_runs(run_id TEXT PRIMARY KEY,plan_id TEXT,status TEXT DEFAULT 'RUNNING',updated_at TEXT,completed_at TEXT);
            CREATE TABLE release_target_runs(run_id TEXT,target_label TEXT,status TEXT,attempts INTEGER,external_id TEXT,error TEXT,completed_at TEXT,updated_at TEXT);
            CREATE TABLE release_target_submissions(run_id TEXT,target_label TEXT);
            CREATE TABLE release_target_readbacks(run_id TEXT,target_label TEXT);
            CREATE TABLE release_target_repairs(run_id TEXT,target_label TEXT);
            CREATE TABLE release_target_retry_operations(run_id TEXT,target_label TEXT);
            CREATE TABLE release_target_failure_events(run_id TEXT,target_label TEXT,attempt INTEGER,evidence_json TEXT,evidence_digest TEXT);
            CREATE TABLE native_sole_final_decisions(decision_id TEXT,plan_id TEXT,snapshot_digest TEXT,review_json TEXT);
            CREATE TABLE approved_publication_snapshots(plan_id TEXT,snapshot_json TEXT);
        ''')
        db.execute('INSERT INTO release_runs(run_id,plan_id) VALUES(?,?)',(run,plan))
        db.execute('INSERT INTO release_target_runs(run_id,target_label,status,attempts,external_id,error,completed_at) VALUES(?,?,?,?,NULL,?,NULL)',(run,label,'FAILED',1,'original rejected SAVE'))
        db.execute('INSERT INTO native_sole_final_decisions VALUES(?,?,?,?)',(decision,plan,snapshot,_json({'candidate_digest':candidate})))
        db.execute('INSERT INTO approved_publication_snapshots VALUES(?,?)',(plan,_json({'offer_id':offer,'plan_id':plan,'snapshot_digest':snapshot})))
        db.execute('INSERT INTO release_target_failure_events VALUES(?,?,?,?,?)',(run,label,1,_json(evidence),body['failure_evidence_digest']))
        db.execute('INSERT INTO native_final_budget_scopes VALUES(?,?,?,?,?,?)',(scope,plan,snapshot,'TIKTOK',_json(binding),_sha(_json(binding))))
        for seq,budget_scope,operation,outcome in [(1,'shared','create_platform_draft','ACCEPTED'),
                (2,'target','claim_draft','ACCEPTED'),(3,'target','save_draft','REJECTED'),(4,'target','publish_target',None)]:
            owner=other if seq==4 else label
            db.execute('INSERT INTO native_final_budget_reservations VALUES(?,?,?,?,?,?,?)',(scope,seq,owner,budget_scope,operation,1,'UNKNOWN'))
            db.execute('INSERT INTO native_final_budget_dispatches VALUES(?,?,?)',(scope,seq,str(seq)*64))
            if outcome:db.execute('INSERT INTO native_final_budget_outcomes VALUES(?,?,?)',(scope,seq,outcome))
        rows=repair._base_rows(db,scope,label);body['original_reservations']=rows
        body['original_reservations_digest']=_sha(_json(rows))
        # Preserve the native decision, original failure and unrelated UNKNOWN.
        protected={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in
            ('native_sole_final_decisions','approved_publication_snapshots','release_target_failure_events',
             'native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes')}
    pins=_pin(tmp_path/'source-pins.json',{'shared_platform/native_known_rejected_save_repair.py':
        {'after_sha256':hashlib.sha256(Path(repair.__file__).read_bytes()).hexdigest()}})
    body['root_addendum_ref']=_pin(tmp_path/'technical-addendum.json',{
        'schema':'root-native-rejected-save-technical-addendum/v2','status':'TECHNICAL_CONTINUATION_ADMITTED',
        'purpose':'EXTRA_ONE_SAVE_ONLY_RETAIN_ORIGINAL_PRODUCT_DECISION',
        'authority':'ROOT_ENGINEERING_CONTINUATION_OF_ORIGINAL_NATIVE_DECISION','new_user_approval':False,
        'offer_id':offer,'candidate_digest':candidate,'plan_id':plan,'run_id':run,'decision_id':decision,
        'snapshot_digest':snapshot,'target_labels':[label],'extra_save_maximum':1,
        'grant_core_digests':[_sha(_json(body))],'source_pins_ref':pins})
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
        wrong=deepcopy(body);wrong['candidate_digest']='0'*64
        with pytest.raises(NativeMutationBudgetHold,match='APPROVAL_CHANGED'):
            repair.validate_original(db,wrong,target_status='FAILED',attempt=1)
        unknown=deepcopy(events);unknown[-1]['outcome']='UNKNOWN'
        with pytest.raises(NativeMutationBudgetHold,match='UNKNOWN_NO_REPLAY'):repair.validate_events(unknown,body)
        with sqlite3.connect(journal.path) as response_db:
            response_db.execute("UPDATE provider_response_intents SET phase='UNKNOWN'")
        with pytest.raises(NativeMutationBudgetHold,match='UNKNOWN_OR_PUBLISH_NO_REPLAY'):repair.validate_provider_rejection(body)
        with sqlite3.connect(journal.path) as response_db:
            response_db.execute("UPDATE provider_response_intents SET phase='RESPONSE_RETAINED'")
        grant_id=repair.intake_grant(db,body,checkpoint)
        repair._activate_grant(db,body,grant_id)
        db.execute("UPDATE release_target_runs SET status='RUNNING',attempts=2 WHERE target_label=?",(label,))
    base=_DurableLedger(store=store,binding=binding,target_label=label)
    with base._connect(readonly=True) as db:grant=dict(db.execute('SELECT * FROM native_save_repair_grants').fetchone())
    ledger=repair._RepairLedger(base,grant,body)
    return SimpleNamespace(base=base,ledger=ledger,grant=grant,body=body,events=events,identity=identity,journal=journal,
        provider_identity=provider_identity,root=root,protected=protected,shop=shop,label=label,other=other)


def test_generic_native_known_rejection_gets_one_fresh_save_without_resetting_base_or_unknown(tmp_path):
    case=_owned_initial_repair(tmp_path)
    base,ledger,grant,body,events,identity,journal,provider_identity,root,protected,shop,label,other=[getattr(case,name) for name in
        ('base','ledger','grant','body','events','identity','journal','provider_identity','root','protected','shop','label','other')]
    # The existing checkpoint consumer must invoke the real SDK's fresh read,
    # not repeat its old rejected event or reuse a stale prepared MD5.
    frozen=_snapshot();target=frozen['publication_targets'][0]
    draft=_draft_payload(frozen,target=target,category=CategoryResolver().resolve(target=target,product=frozen['product'],skus=frozen['skus']))
    original_draft=deepcopy(draft);calls=[]
    def post(path,payload):
        calls.append((path,deepcopy(payload)))
        if path.endswith('get_site_collect_item_info'):
            return {'result':'success','data':{'ossMd5':'CURRENT-READ-MD5','siteCollectItemInfo':{
                'providerRequired':'keep','collectBoxDetailShopList':[{'shopId':shop}],
                'skuPropertyList':[{'attrName':'Specification','attrValueList':[
                    {'attrValueId':str(row['variant_key']),'attrValue':next(iter(row['specification'].values()))}
                    for row in draft['skus']]}],
                'skuMap':{str(row['variant_key']):{'itemNum':row['model_sku'],'stock':300,
                    'shopIdToWarehouseIdAndStockMap':{shop:{'warehouse-'+shop:'300'}}} for row in draft['skus']}}}}
        if path.endswith('get_shop_warehouse_list'):return _warehouse_payload(shop)
        assert path==SAVE_SITE_DRAFT_PATH
        assert payload['ossMd5']=='CURRENT-READ-MD5' and payload['siteCollectItemInfo']['title']==draft['title']
        assert payload['siteCollectItemInfo']['imgUrls']==draft['images']
        return {'result':'success','data':{}}
    sdk=MiaoshouOpenApiTikTokV4DraftTransport(common_detail_id='111',post=ledger.wrap_post(post,provider='miaoshou'),
        read_attempts=1,before_mutation=lambda owner,op:ledger.reserve_target(owner,op),
        fact_observer=lambda owner,fact:ledger.complete_reserved('save_draft',fact.outcome))
    request=SimpleNamespace(snapshot={'snapshot_digest':body['snapshot_digest'],'plan_id':body['plan_id']},run_id=provider_identity['run_id'],
        report_id=provider_identity['report_id'],checkpoint_root=root,write_budget_ledger=ledger)
    transport=_ResumableTikTokDraftTransport(request=request,
        store=SimpleNamespace(target_events=lambda request,label:deepcopy(events)),transport=sdk)
    prepared=transport.prepare_save_draft(identity=identity,draft=draft)
    assert prepared['body']['ossMd5']=='CURRENT-READ-MD5' and draft==original_draft
    assert transport.save_prepared_draft(identity=identity,prepared=prepared).outcome=='ACCEPTED'
    assert [path for path,_ in calls]==[
        '/open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info',
        '/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list',SAVE_SITE_DRAFT_PATH]
    with pytest.raises(NativeMutationBudgetHold):ledger.reserve_target(label,'publish_target')
    ledger.retain_post_save_match({**identity,'expected_images':draft['images']},{'official':True},True)
    ledger.reserve_target(label,'publish_target')
    ledger.dispatch_reserved('publish_target',lambda body:None,{'same_approved_target':label})
    ledger.complete_reserved('publish_target','ACCEPTED')
    snapshot_after=ledger.snapshot()
    assert snapshot_after['limits']['per_target_maximum']==3
    assert snapshot_after['base_attempts']['per_target'][label]==3
    assert snapshot_after['repair_attempts']['per_target'][label]==1
    assert snapshot_after['attempts']['per_target'][label]==4
    assert any(row['outcome']=='UNKNOWN' and row['mutation_target_label']==other for row in snapshot_after['reservations'])
    with pytest.raises(NativeMutationBudgetHold):ledger.reserve_target(label,'save_draft')
    with pytest.raises(NativeMutationBudgetHold):ledger.dispatch_reserved('save_draft',post,prepared['path'],prepared['body'])
    with base._connect(readonly=True) as db:
        for table,prior in protected.items():
            current=[tuple(r) for r in db.execute('SELECT * FROM '+table)]
            assert current[:len(prior)]==prior
        assert db.execute('SELECT count(*) FROM native_save_repair_reservations').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM native_save_repair_dispatches').fetchone()[0]==1
    # Even an integrity-consistent accepted original response is ineligible.
    accepted=_json({'result':'success','data':{}}).encode();bad=deepcopy(body)
    bad['provider_rejection']['response_sha256']=hashlib.sha256(accepted).hexdigest()
    with sqlite3.connect(journal.path) as db:
        db.execute('UPDATE provider_response_intents SET response_body=?,response_sha256=?',(accepted,bad['provider_rejection']['response_sha256']))
    with pytest.raises(NativeMutationBudgetHold,match='VERSION_REJECTION_REQUIRED'):repair.validate_provider_rejection(bad)


def test_retained_completion_reuses_original_slots_and_preserves_other_unknown(tmp_path,monkeypatch):
    """NRE failure shapes: retained success, unentered slot, unused old grant.

    Only actor/approval and owned minimal-schema transaction boundaries are
    substituted; actual proof, claim, repair ledger, SDK and checkpoint counts
    run unchanged. No provider or production database is opened.
    """
    from contextlib import contextmanager
    from shared_platform import native_provider_continuation as continuation
    from shared_platform.product_publication_live_dependencies import (DurableTikTokV4DraftPreparer,
        TikTokV4DraftCheckpointStore)
    from shared_platform.release_store import ReleaseStoreError
    for mode in ('REPAIR_ACCEPTED','REPAIR_RESERVED','REPAIR_UNRESERVED'):
        case=_owned_initial_repair(tmp_path/mode)
        base,body,label=case.base,case.body,case.label;store=base.store
        own=deepcopy(case.events)
        if mode!='REPAIR_UNRESERVED':
            own.append({**case.identity,'operation':'SAVE_DRAFT',
                'outcome':'ACCEPTED' if mode=='REPAIR_ACCEPTED' else 'UNKNOWN'})
        cp={'schema_version':'tiktok-v4-draft-checkpoint/v1','offer_id':body['offer_id'],
            'run_id':case.provider_identity['run_id'],'snapshot_digest':body['snapshot_digest'],'events':own}
        cp['checkpoint_digest']='sha256:'+_sha(_json(cp));Path(body['checkpoint_ref']['path']).write_text(_json(cp),encoding='utf-8')
        with base._connect() as db:
            db.execute("UPDATE release_target_runs SET status='FAILED',error='local completion interrupted' WHERE target_label=?",(label,))
            failure={'stage':'PREPARATION','target_label':label,'local_interruption':True}
            db.execute('INSERT INTO release_target_failure_events VALUES(?,?,?,?,?)',
                (body['run_id'],label,2,_json(failure),_sha(_json(failure))))
            if mode!='REPAIR_UNRESERVED':
                db.execute("INSERT INTO native_save_repair_reservations VALUES(?,'save_draft',1)",(case.grant['grant_id'],))
            if mode=='REPAIR_ACCEPTED':
                db.execute('INSERT INTO native_save_repair_dispatches VALUES(?,?)',(case.grant['grant_id'],'9'*64))
        if mode=='REPAIR_ACCEPTED':
            request={'provider':'miaoshou','method':'POST','path':SAVE_SITE_DRAFT_PATH,
                'account':{'app_id':'synthetic-owned-fixture'},'request_body_sha256':'8'*64}
            key,_=case.journal.claim(request)
            case.journal.retain(key,200,_json({'result':'success','data':{}}).encode(),{'Content-Type':'application/json'})
        def decision(db,decision_id,**kwargs):
            assert decision_id==body['decision_id']
            assert db.execute('SELECT 1 FROM native_sole_final_decisions WHERE decision_id=?',(decision_id,)).fetchone()
        active=SimpleNamespace(store=store,plan_id=body['plan_id'],decision_id=body['decision_id'],owner_sid='owned',
            runtime=SimpleNamespace(supervisor=SimpleNamespace(root=case.root)),service=SimpleNamespace(_decision=decision))
        monkeypatch.setattr(continuation,'_tiktok_active',lambda current:current is active,raising=False)
        binding={k:body[k] for k in ('plan_id','run_id','decision_id','snapshot_digest','candidate_digest','offer_id')}
        binding['product_revision']=1
        @contextmanager
        def transaction():
            with base._connect() as db:
                db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        monkeypatch.setattr(store,'_transaction',transaction)
        monkeypatch.setattr(store,'_require_active_run',lambda db,rid:decision(db,body['decision_id']))
        with base._connect(readonly=True) as db:
            facts=continuation._tiktok_continuation_facts(active,db,binding,label)
            assert facts['mode']==mode
        proof=continuation._NativeTikTokContinuationProof(active,_json(facts))
        monkeypatch.setattr(continuation,'_tiktok_active',lambda current:False)
        with pytest.raises(ReleaseStoreError,match='CURRENT_NATIVE_TIKTOK_ACTOR_REQUIRED'):
            store.begin_target(body['run_id'],label,native_tiktok_continuation_proof=proof)
        monkeypatch.setattr(continuation,'_tiktok_active',lambda current:current is active)
        tampered=deepcopy(facts);tampered['target']['attempts']=0
        with pytest.raises(ReleaseStoreError,match='TARGET_CHANGED'):
            store.begin_target(body['run_id'],label,native_tiktok_continuation_proof=continuation._NativeTikTokContinuationProof(active,_json(tampered)))
        claimed=store.begin_target(body['run_id'],label,native_tiktok_continuation_proof=proof)
        assert claimed['status']=='RUNNING' and claimed['attempts']==3
        with pytest.raises(ReleaseStoreError):store.begin_target(body['run_id'],label,native_tiktok_continuation_proof=proof)
        ledger=repair._RepairLedger(base,case.grant,body,continuation=facts)
        request=SimpleNamespace(snapshot={'snapshot_digest':body['snapshot_digest'],'plan_id':body['plan_id']},
            run_id=case.provider_identity['run_id'],report_id=case.provider_identity['report_id'],
            checkpoint_root=case.root,write_budget_ledger=ledger,target_labels=(label,))
        calls=[];draft={'same_approved':'bytes'}
        class SDK:
            def prepare_save_draft(self,*,identity,draft):
                calls.append('fresh MD5 READ')
                return {'path':SAVE_SITE_DRAFT_PATH,'body':{'detailId':body['detail_id'],'site':'VN','ossMd5':'CURRENT'}}
            def save_prepared_draft(self,*,identity,prepared):
                ledger.reserve_target(label,'save_draft')
                ledger.dispatch_reserved('save_draft',lambda *args:calls.append('SAVE'),prepared['path'],prepared['body'])
                ledger.complete_reserved('save_draft','ACCEPTED')
                from modules.miaoshou.tiktok_v4_drafts import DraftWriteFact
                return DraftWriteFact('SAVE_DRAFT','ACCEPTED',detail_id=body['detail_id'],shop_id=body['shop_id'])
        transport=_ResumableTikTokDraftTransport(request=request,
            store=SimpleNamespace(target_events=lambda *args:deepcopy(own)),transport=SDK())
        prepared=transport.prepare_save_draft(identity=case.identity,draft=draft)
        assert transport.save_prepared_draft(identity=case.identity,prepared=prepared).outcome=='ACCEPTED'
        assert calls==([] if mode=='REPAIR_ACCEPTED' else ['fresh MD5 READ','SAVE'])
        if mode!='REPAIR_ACCEPTED':own.append({**case.identity,'operation':'SAVE_DRAFT','outcome':'ACCEPTED'})
        unrelated={'target_label':case.other,'operation':'PUBLISH','outcome':'UNKNOWN'}
        global_cp={'events':own+[unrelated]};saved=deepcopy(global_cp)
        preparer=object.__new__(DurableTikTokV4DraftPreparer)
        preparer._store=SimpleNamespace(_write_count=TikTokV4DraftCheckpointStore._write_count)
        assert preparer._request_write_count(request,global_cp)==3
        assert TikTokV4DraftCheckpointStore._write_count(global_cp['events']) is None and global_cp==saved
        with base._connect(readonly=True) as db:
            assert db.execute('SELECT count(*) FROM native_save_repair_reservations').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM native_save_repair_dispatches').fetchone()[0]==1
            assert db.execute('SELECT outcome FROM native_save_repair_outcomes').fetchone()[0]=='ACCEPTED'
            assert [r[0] for r in db.execute('SELECT attempt FROM release_target_failure_events ORDER BY attempt')]==[1,2]
            assert db.execute('SELECT target_attempt FROM native_save_repair_starts').fetchone()[0]==2
            for table,prior in case.protected.items():
                assert [tuple(r) for r in db.execute('SELECT * FROM '+table)][:len(prior)]==prior
        # An extra uncertain wire invalidates the same proof; it cannot be
        # interpreted as another unused slot or a known successful completion.
        unknown={'provider':'miaoshou','method':'POST','path':SAVE_SITE_DRAFT_PATH,
            'account':{'app_id':'synthetic-owned-fixture'},'request_body_sha256':'7'*64}
        case.journal.claim(unknown)
        with base._connect() as db:
            db.execute("UPDATE release_target_runs SET status='FAILED',attempts=2 WHERE target_label=?",(label,))
        with base._connect(readonly=True) as db:
            with pytest.raises(ValueError,match='UNKNOWN_OR_PUBLISHED'):
                continuation._tiktok_continuation_facts(active,db,binding,label)
    # Original claim ACK persisted before the native outcome append. Reuse
    # that CP and append only the matching sequence's known completion.
    case=_owned_initial_repair(tmp_path/'claim-original');original=case.base
    claim_store=ReleaseStore(tmp_path/'retained-claim.db')
    with original._connect(readonly=True) as source,sqlite3.connect(claim_store.path) as db:
        tables=source.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()
        for name,sql in tables:db.execute(sql)
        for name,_ in tables:
            if name.startswith('native_save_repair_'):continue
            for row in source.execute('SELECT * FROM '+name):
                value=dict(row)
                if name in {'native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes'}:
                    if value['sequence']==3 or name=='native_final_budget_outcomes' and value['sequence']==2:continue
                if name=='release_target_runs':value.update(status='FAILED',attempts=1)
                db.execute('INSERT INTO '+name+' VALUES('+','.join('?' for _ in value)+')',tuple(value.values()))
        for row in source.execute("SELECT sql FROM sqlite_master WHERE type='trigger'"):db.execute(row[0])
    base=_DurableLedger(store=claim_store,binding=original.binding,target_label=case.label);body=case.body
    root=tmp_path/'retained-claim-runtime';events=deepcopy(case.events[:2])
    cp={'schema_version':'tiktok-v4-draft-checkpoint/v1','offer_id':body['offer_id'],
        'run_id':case.provider_identity['run_id'],'snapshot_digest':body['snapshot_digest'],'events':events}
    cp['checkpoint_digest']='sha256:'+_sha(_json(cp))
    _pin(root/body['offer_id']/'1'/case.provider_identity['run_id']/'tiktok-draft-checkpoint.json',cp)
    journal=ProviderResponseJournal(root,case.provider_identity);journal.initialize()
    for endpoint in ('/open/v1/product/common_collect_box/common_collect_box/claimed',
                     '/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop'):
        key,_=journal.claim({'provider':'miaoshou','method':'POST','path':endpoint,
            'account':{'app_id':'owned-fixture'},'request_body_sha256':_sha(endpoint)})
        journal.retain(key,200,_json({'result':'success','data':{}}).encode(),{})
    active=SimpleNamespace(store=claim_store,plan_id=body['plan_id'],decision_id=body['decision_id'],owner_sid='owned',
        runtime=SimpleNamespace(supervisor=SimpleNamespace(root=root)),service=SimpleNamespace(_decision=decision))
    monkeypatch.setattr(continuation,'_tiktok_active',lambda current:current is active,raising=False)
    binding={k:body[k] for k in ('plan_id','run_id','decision_id','snapshot_digest','candidate_digest','offer_id')};binding['product_revision']=1
    with base._connect(readonly=True) as db:
        facts=continuation._tiktok_continuation_facts(active,db,binding,case.label)
        before=[tuple(r) for r in db.execute('SELECT * FROM native_final_budget_reservations')]
    assert facts['mode']=='RETAINED_CLAIM'
    monkeypatch.setattr(claim_store,'_transaction',transaction)
    monkeypatch.setattr(claim_store,'_require_active_run',lambda db,rid:decision(db,body['decision_id']))
    result=claim_store.begin_target(body['run_id'],case.label,
        native_tiktok_continuation_proof=continuation._NativeTikTokContinuationProof(active,_json(facts)))
    assert result['attempts']==2
    with base._connect(readonly=True) as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM native_final_budget_reservations')]==before
        assert db.execute('SELECT outcome FROM native_final_budget_outcomes WHERE sequence=2').fetchone()[0]=='ACCEPTED'
        assert db.execute('SELECT count(*) FROM native_save_repair_reservations').fetchone()[0]==0
    transport=_ResumableTikTokDraftTransport(request=SimpleNamespace(),
        store=SimpleNamespace(target_events=lambda *args:deepcopy(events)),transport=None)
    reused=transport.claim_or_create(target={'target_label':case.label},ordinal=1)
    assert reused.outcome=='ACCEPTED' and reused.detail_id==body['detail_id'] and reused.shop_id==body['shop_id']
    # Same original consumer also admits the precise second local-lock shape,
    # not an arbitrary repeated FAILED target or an attempted regional write.
    from test_native_provider_response_recovery import _regional_unsent_claim_case
    _regional_unsent_claim_case(tmp_path/'regional',monkeypatch,second_local_lock=True)
    test_generic_native_known_rejection_gets_one_fresh_save_without_resetting_base_or_unknown(tmp_path/'actual-sdk')


def test_accepted_save_keeps_original_prewire_slot_and_one_corrective_save(tmp_path,monkeypatch):
    """Original seq12 is reserved but unentered; current draft needs one SAVE."""
    from contextlib import contextmanager
    from shared_platform import native_accepted_save_core_repair as core,native_provider_continuation as continuation
    from modules.miaoshou.tiktok_publisher import PUBLISH_PATH
    case=_owned_initial_repair(tmp_path/'prior');source_base=case.base
    store=ReleaseStore(tmp_path/'current.db')
    with source_base._connect(readonly=True) as source,sqlite3.connect(store.path) as db:
        for name,sql in source.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"):db.execute(sql)
        for name, in source.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if name.startswith('native_save_repair_'):continue
            for row in source.execute('SELECT * FROM '+name):
                value=dict(row)
                if name=='native_final_budget_outcomes' and value['sequence']==3:value['outcome']='ACCEPTED'
                if name=='release_target_runs':value.update(status='FAILED',attempts=1)
                db.execute('INSERT INTO '+name+' VALUES('+','.join('?' for _ in value)+')',tuple(value.values()))
        db.execute('INSERT INTO native_final_budget_reservations VALUES(?,?,?,?,?,?,?)',
            (case.body['scope_key'],12,case.label,'target','publish_target',1,'UNKNOWN'))
        for row in source.execute("SELECT sql FROM sqlite_master WHERE type='trigger'"):db.execute(row[0])
        db.executescript(core.SCHEMA)
    base=_DurableLedger(store=store,binding=source_base.binding,target_label=case.label)
    identity=case.provider_identity;root=tmp_path/'runtime';events=deepcopy(case.events)
    events[-1]={**case.identity,'operation':'SAVE_DRAFT','outcome':'ACCEPTED'}
    cp={'schema_version':'tiktok-v4-draft-checkpoint/v1','offer_id':case.body['offer_id'],
        'run_id':identity['run_id'],'snapshot_digest':case.body['snapshot_digest'],'events':events}
    cp['checkpoint_digest']='sha256:'+_sha(_json(cp))
    cp_path=root/case.body['offer_id']/'1'/identity['run_id']/'tiktok-draft-checkpoint.json'
    _pin(cp_path,cp);cp_ref=_pin(tmp_path/'frozen-cp.json',cp)
    journal=ProviderResponseJournal(root,identity);journal.initialize()
    requests=[]
    for endpoint in ('/open/v1/product/common_collect_box/common_collect_box/claimed',
                     '/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop',SAVE_SITE_DRAFT_PATH):
        req={'provider':'miaoshou','method':'POST','path':endpoint,
            'account':{'app_id':'owned-fixture'},'request_body_sha256':_sha(endpoint)};requests.append(req)
        key,_=journal.claim(req);journal.retain(key,200,_json({'result':'success','data':{}}).encode(),{})
    body={k:case.body[k] for k in ('offer_id','candidate_digest','plan_id','run_id','decision_id','snapshot_digest',
        'scope_key','target_label','detail_id','shop_id')}
    with base._connect(readonly=True) as db:
        rows=repair._base_rows(db,body['scope_key'],case.label)
        failures=[r[0] for r in db.execute('SELECT evidence_digest FROM release_target_failure_events')]
    body.update(schema='native-accepted-save-prewire-grant/v1',sequence=12,
        original_reservations_digest=_sha(_json(rows)),failure_evidence_digests=failures,
        checkpoint_events_digest=_sha(_json(events)),checkpoint_ref=cp_ref,canonical_checkpoint_path=str(cp_path),
        zero_dispatch_ref=_pin(tmp_path/'prewire.json',{
            'status':'ORIGINAL_NATIVE_PUBLISH_NOT_DISPATCHED_KEEP_RESERVATION_UNKNOWN',
            'offer_id':body['offer_id'],'target_label':case.label,'original_run_id':body['run_id'],
            'proof':{'retained_journal_rows':[{**{k:r[k] for k in ('key','phase','http_status','response_sha256')},
                'path':r['request']['path']} for r in continuation.retained_tiktok_rows(root,identity)],
                'reservation':{'scope_key':body['scope_key'],'sequence':12,'target_label':case.label,
                'operation':'publish_target','dispatch':None,'outcome_record':None}}}))
    pins=_pin(tmp_path/'core-source.json',{'shared_platform/native_accepted_save_core_repair.py':
        {'after_sha256':hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest()}})
    body['root_addendum_ref']=_pin(tmp_path/'core-root.json',{
        'schema':'root-native-accepted-save-prewire-addendum/v1','status':'TECHNICAL_CONTINUATION_ADMITTED',
        'authority':'ROOT_ENGINEERING_CONTINUATION_OF_ORIGINAL_NATIVE_DECISION','new_user_approval':False,
        **{k:body[k] for k in ('offer_id','candidate_digest','plan_id','run_id','decision_id','snapshot_digest')},
        'target_labels':[case.label],'base_target_maximum':3,'extra_save_maximum':1,
        'combined_target_maximum':4,'new_publish_reservations':0,
        'grant_core_digests':[_sha(_json(body))],'source_pins_ref':pins})
    with base._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Already dispatched or ambiguous historical publish cannot enter.
        db.execute('SAVEPOINT dispatched')
        db.execute('INSERT INTO native_final_budget_dispatches VALUES(?,?,?)',(body['scope_key'],12,'f'*64))
        with pytest.raises(NativeMutationBudgetHold,match='EXACT_UNDISPATCHED_SLOT'):
            core.intake_generic(db,body,cp)
        db.execute('ROLLBACK TO dispatched');db.execute('RELEASE dispatched')
        # Missing native dispatch alone is insufficient when a provider
        # publish intent exists. Even an explicit technical addendum refuses.
        unknown_root=tmp_path/'unknown-wire';unknown_journal=ProviderResponseJournal(unknown_root,identity)
        unknown_journal.initialize()
        for req in requests:
            key,_=unknown_journal.claim(req)
            unknown_journal.retain(key,200,_json({'result':'success','data':{}}).encode(),{})
        unknown_journal.claim({'provider':'miaoshou','method':'POST','path':PUBLISH_PATH,
            'account':{'app_id':'owned-fixture'},'request_body_sha256':'a'*64})
        ambiguous=deepcopy(body)
        ambiguous['canonical_checkpoint_path']=str(unknown_root/body['offer_id']/'1'/identity['run_id']/'tiktok-draft-checkpoint.json')
        authority=json.loads(Path(body['root_addendum_ref']['path']).read_bytes())
        authority['grant_core_digests']=[_sha(_json({k:v for k,v in ambiguous.items() if k!='root_addendum_ref'}))]
        ambiguous['root_addendum_ref']=_pin(tmp_path/'unknown-root.json',authority)
        with pytest.raises(NativeMutationBudgetHold,match='UNKNOWN_OR_PUBLISH_JOURNAL_NO_REPLAY'):
            core.intake_generic(db,ambiguous,cp)
        gid=core.intake_generic(db,body,cp)
    def decision(db,did,**kwargs):assert did==body['decision_id']
    active=SimpleNamespace(store=store,plan_id=body['plan_id'],decision_id=body['decision_id'],owner_sid='owned',
        runtime=SimpleNamespace(supervisor=SimpleNamespace(root=root)),service=SimpleNamespace(_decision=decision))
    monkeypatch.setattr(continuation,'_tiktok_active',lambda current:current is active,raising=False)
    binding={k:body[k] for k in ('offer_id','plan_id','run_id','decision_id','snapshot_digest','candidate_digest')};binding['product_revision']=1
    with base._connect(readonly=True) as db:
        facts=continuation._tiktok_continuation_facts(active,db,binding,case.label)
        grant=dict(db.execute('SELECT * FROM native_accepted_core_grants').fetchone())
        original_budget={name:[tuple(r) for r in db.execute('SELECT * FROM '+name)] for name in
            ('native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes')}
    assert facts['mode']=='CORE_UNDISPATCHED'
    @contextmanager
    def transaction():
        with base._connect() as db:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
    monkeypatch.setattr(store,'_transaction',transaction)
    monkeypatch.setattr(store,'_require_active_run',lambda *args:None)
    claimed=store.begin_target(body['run_id'],case.label,
        native_tiktok_continuation_proof=continuation._NativeTikTokContinuationProof(active,_json(facts)))
    assert claimed['attempts']==2
    target={**case.identity,'expected_images':['frozen-image-'+str(i) for i in range(7)]}
    frozen=deepcopy(target);ledger=core._CoreLedger(active,base,grant,body,target);calls=[]
    held=ledger.reserve_target(case.label,'publish_target')
    assert held['sequence']==12
    with pytest.raises(NativeMutationBudgetHold,match='FRESH_EXACT_POST_SAVE_READ'):
        ledger.dispatch_reserved('publish_target',lambda *args:calls.append('publish'),
            PUBLISH_PATH,{'detailIds':[int(body['detail_id'])],'shopIds':[body['shop_id']]})
    prepared={'path':SAVE_SITE_DRAFT_PATH,'body':{'detailId':body['detail_id'],'site':'VN',
        'ossMd5':'CURRENT-JIT-MD5','siteCollectItemInfo':{'imgUrls':target['expected_images'],'price':'frozen','warehouse':'frozen'}}}
    ledger.bind_prepared(target,{'oss_md5':'CURRENT-JIT-MD5'},prepared)
    ledger.reserve_target(case.label,'save_draft')
    ledger.dispatch_reserved('save_draft',lambda *args:calls.append('SAVE'),prepared['path'],prepared['body'])
    ledger.complete_reserved('save_draft','ACCEPTED')
    ledger.retain_match(target,{'official_frozen_match':True},True)
    ledger.dispatch_reserved('publish_target',lambda *args:calls.append('publish'),
        PUBLISH_PATH,{'detailIds':[int(body['detail_id'])],'shopIds':[body['shop_id']]})
    ledger.complete_reserved('publish_target','ACCEPTED')
    assert calls==['SAVE','publish'] and target==frozen and json.loads(cp_path.read_bytes())==cp
    with pytest.raises(NativeMutationBudgetHold):ledger.reserve_target(case.label,'save_draft')
    with pytest.raises(NativeMutationBudgetHold):ledger.reserve_target(case.label,'publish_target')
    with base._connect(readonly=True) as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM native_final_budget_reservations')]==original_budget['native_final_budget_reservations']
        assert db.execute('SELECT count(*) FROM native_final_budget_dispatches WHERE sequence=12').fetchone()[0]==1
        assert db.execute('SELECT outcome FROM native_final_budget_outcomes WHERE sequence=12').fetchone()[0]=='ACCEPTED'
        assert db.execute('SELECT count(*) FROM native_accepted_core_reservations').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM native_save_repair_reservations').fetchone()[0]==0
        assert repair._base_rows(db,body['scope_key'],case.other)[-1]['outcome']=='UNKNOWN'
    snapshot=ledger.snapshot()
    assert snapshot['limits']['per_target_maximum']==3 and snapshot['attempts']['per_target'][case.label]==4
