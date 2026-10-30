"""Exact retained494 prices; source/actor and official responses are named fixtures.

No real source event, published state, credential, transport or authority is
installed. Canonical fixtures are memory-only; the original494 image is RO.
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
from types import SimpleNamespace

import pytest

from shared_platform import native_approved_discount_prices as prices
from shared_platform import native_companion_promotions as promotions
from shared_platform.postpublish_promotions import approved_promotion_action_policy
from shared_platform.final_review_server_admission import ApprovalBlocked


@pytest.fixture
def captured(tmp_path,monkeypatch):
    supplied=os.environ.get('ORBIT_B0431_DISCOUNT_INPUTS')
    if not supplied:pytest.skip('explicit private494 capture not supplied')
    source=Path(supplied)/'ORIGINAL_RELEASE_STORE_POST_R74_FULL.private.db'
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    assert before=='494befe1ce4083693d4b552a231ff1d03c393a16c2181123c6f12553f612439a'
    shutil.copy2(source,tmp_path/'original-whole-clone.db')
    from shared_platform.release_store import ReleaseStore
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService
    from shared_platform.native_sole_final_execution import _NativeExecution,_ACTIVE
    from shared_platform import agent_prepared_v4_targets
    store=ReleaseStore(tmp_path/'original-whole-clone.db')
    plan=store.get_plan(prices.B_PLAN);run=store.get_run(prices.B_RUN)
    with store._connect_readonly() as db:
        owner=db.execute('SELECT owner_sid FROM native_sole_final_decisions WHERE decision_id=?',
                         (prices.B_DECISION,)).fetchone()[0]
    service=NativeSoleFinalDecisionService(store,lambda:{})
    monkeypatch.setattr(service,'_actor',lambda:{'owner_sid':owner})
    active=object.__new__(_NativeExecution)
    active.store,active.service,active.plan_id=store,service,prices.B_PLAN
    active.decision_id,active.owner_sid=prices.B_DECISION,owner
    refpath=Path(os.environ['ORBIT_B0431_DISCOUNT_DECISION'])
    ref={'path':str(refpath),'sha256':hashlib.sha256(refpath.read_bytes()).hexdigest()}
    doc=prices.read_b0431_discount_decision(ref)
    taskdb=tmp_path/'SOURCE_BOUNDARY_FIXTURE.db'
    record={'b0431_discount_decision_ref':ref,'b0431_discount_decision_digest':prices._digest(doc)}
    raw=json.dumps(record,sort_keys=True,separators=(',',':'))
    with sqlite3.connect(taskdb) as db:
        db.execute('CREATE TABLE workbench_events(task_id TEXT,event_type TEXT,detail_json TEXT)')
        db.execute('INSERT INTO workbench_events VALUES(?,?,?)',(prices.B_TASK,prices.B_EVENT,
            json.dumps({'record_json':raw,'record_digest':hashlib.sha256(raw.encode()).hexdigest(),
                        'signature':'EXPLICIT_GENUINE_SOURCE_BOUNDARY_FIXTURE_ONLY'})))
    bound=SimpleNamespace(engine=SimpleNamespace(store=SimpleNamespace(path=taskdb)))
    # The source author's independent Git/HMAC/CAS tests own this boundary.
    monkeypatch.setattr(agent_prepared_v4_targets,'_scope',lambda a,p:
        (bound,{'task_id':prices.B_TASK},{'owner_sid':owner}) if a is active and p['plan_id']==prices.B_PLAN
        else pytest.fail('Wrong protected context'))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**kw:pytest.fail('Network forbidden'))
    token=_ACTIVE.set(active)
    try:yield SimpleNamespace(active=active,store=store,plan=plan,run=run,ref=ref,doc=doc,tmp=tmp_path)
    finally:_ACTIVE.reset(token)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


def published_fixture(run):
    """Synthetic later official gate only; never persisted over real FAILED rows."""
    result=deepcopy(run)
    for row in result['targets']:
        label=row['target_label']
        if label not in prices.B_TARGETS:continue
        evidence={'outcome_unknown':False}
        if label.startswith('shopee:'):
            evidence.update(target_label=label,stage='READBACK',provider_identity_bound=True,
                            provider_code='shopee_official_readback_verified')
            shop,item,*_=prices.B_SH[label];row['external_id']=f'shopee:{shop}:item:{item}'
        else:row['external_id']='OFFLINE_PRODUCT_'+label
        canonical={'readback_completed':True,'platform':'SHOPEE' if label.startswith('shopee:') else 'TIKTOK',
                   'targets':[{'target_label':label,'status':'PUBLISHED','evidence':evidence}]}
        e={'schema_version':'native-scoped-v4-target-result/v1','target_label':label,'verified':True,
           'status':'PUBLISHED','outcome_unknown':False,'reconciliation_required':False,
           'canonical_platform_result':canonical}
        row.update(status='SUCCEEDED',readback={'evidence':e,'evidence_digest':promotions._hash(e)})
    return result


@contextmanager
def ready(captured,labels=None):
    labels=promotions.selected_targets(labels or ['promotion:'+t for t in prices.B_TARGETS])
    with promotions._execution_scope(captured.active,captured.plan,published_fixture(captured.run),labels) as scope:
        yield scope


def test_true494_old_consumer_rejects_then_same_quotes_use_protected_scope(captured):
    source=Path(__file__).resolve().parents[1]
    original=subprocess.check_output(['git','-C',str(source),'show',
        '78ca73694d68a17ae62350b05e4c4731211e9686:shared_platform/native_approved_discount_prices.py'])
    old={};exec(compile(original,'ORIGINAL_78CA_CONSUMER','exec'),old)
    payload=captured.plan['payload'];assert prices.read_b0431_discount_decision(captured.ref)==captured.doc
    with pytest.raises(ValueError,match='EXACT_ORIGINAL_PRODUCT_REQUIRED'):
        old['retained_discount_price'](payload,'tiktok:LH_MY')
    with pytest.raises(ValueError,match='PROTECTED_SOURCE_CONTEXT_REQUIRED'):
        prices.retained_discount_price(payload,'tiktok:LH_MY')
    with pytest.raises(ApprovalBlocked,match='VERIFIED_CANONICAL_READBACK_REQUIRED'):
        with promotions._execution_scope(captured.active,captured.plan,captured.run,
                promotions.selected_targets(['promotion:tiktok:LH_MY'])):pytest.fail('Actual FAILED opened')
    expected={'tiktok:LH_MY':'22.1','tiktok:LH_PH':'286.65','tiktok:LH_TH':'163.15','tiktok:LH_VN':'191100',
              'shopee:MY':'23.8','shopee:PH':'308.7','shopee:TH':'175.7','shopee:VN':'205800'}
    with ready(captured) as scope:
        for label in prices.B_TARGETS:
            q=prices.retained_discount_price(payload,label)
            assert q['source_event_record_digest']==scope['b0431_discount_source_digest']
            assert q['model_skus']==prices.B_MODELS and q['sale_after_discount']==expected[label]
            assert q['discount_percent']==(30 if label.startswith('shopee:') else 35)
            policy=approved_promotion_action_policy(payload,'promotion:'+label)
            assert policy['retained_discount_price_binding_digest']==q['binding_digest']
            if label.startswith('tiktok:'):
                from modules.tiktok.oneclick_promotion import _approved_price
                assert _approved_price(payload,label)==(q['list_price'],q['currency'])
    assert promotions._SCOPE.get() is None


def test_wrong_target_payload_currency_intent_and_legacy_stay_closed(captured):
    payload=captured.plan['payload']
    with ready(captured):
        for changed in [dict(payload,product_id='other'),deepcopy(payload)]:
            if changed['product_id']!='other':changed['pricing']['selected_targets']['tiktok:LH_MY']['sku_prices'].pop()
            with pytest.raises(ValueError):prices.retained_discount_price(changed,'tiktok:LH_MY')
        with pytest.raises(ValueError,match='PAYLOAD_TARGET_REQUIRED'):
            prices.retained_discount_price(payload,'tiktok:HB_MY')
        with pytest.raises(ValueError,match='REVIEWED_PRICE_CHANGED'):
            prices.retained_discount_price(payload,'tiktok:LH_MY',expected_currency='JPY')
        # The original403 path is still its unchanged exact-payload gate,
        # never the new B's scope or new R81 rate.
        with pytest.raises(ValueError,match='IMMUTABLE_PAYLOAD_CHANGED'):
            prices.retained_discount_price({'product_id':'4039931504','seller_sku':'0989','plan_id':prices.PLAN},'shopee:MY')
    bad=deepcopy(captured.doc);bad['targets']['shopee:PH']['models'][0]['exact_sale_price']='308'
    path=captured.tmp/'TAMPERED_NOT_AUTHORITY.json';path.write_text(json.dumps(bad),encoding='utf-8')
    with pytest.raises(ValueError,match='DECISION_CHANGED'):
        prices.read_b0431_discount_decision({'path':str(path),'sha256':prices.B_DECISION_SHA})


def test_sh_original_consumer_full_three_models_decimals_and_once_unknown(captured,monkeypatch):
    from modules.shopee import oneclick_promotion as sh
    from modules.tiktok.oneclick_promotion import TikTokPromotionDispatchError,TikTokPromotionBlocked
    with ready(captured) as scope:
        action='promotion:shopee:PH';label='shopee:PH';proof=scope['proofs'][action]
        state={'written':False,'models':prices.B_MODELS.copy(),'body':None,'mutations':0}
        class Transport:
            def __call__(self,path,params):
                if path==sh.MODEL:return {'model':[{'model_id':i+1,'model_sku':sku,
                    'price_info':[{'currency':'PHP','original_price':'441'}]} for i,sku in enumerate(state['models'])]}
                if path==sh.LIST:return {'more':False,'discount_list':[{'discount_id':77,'start_time':1,'end_time':4102444800,'discount_status':'ongoing'}]}
                if path==sh.DETAIL:return {'more':False,'item_list':([{'item_id':47970054181,'model_list':
                    [{'model_id':i+1,'model_promotion_price':'308.70'} for i in range(3)]}] if state['written'] else [])}
                pytest.fail('Unexpected endpoint')
            def add(self,body):
                state['mutations']+=1;state['body']=body
                raise TimeoutError('Offline transport unknown, never actually sent')
        transport=Transport();monkeypatch.setattr(sh,'_transport',lambda a,p:transport)
        request=SimpleNamespace(target_label=action,immutable_plan_payload=captured.plan['payload'],
                                prerequisite_context=proof,idempotency_key='OFFLINE_B_ONLY')
        prepared=sh.prepare_postpublish_promotion(request);command=prepared['command']
        assert command['expected']['seller_skus']==prices.B_MODELS
        body=sh._body(command);assert len(body['item_list'][0]['model_list'])==3
        assert all(x['model_promotion_price']==308.7 for x in body['item_list'][0]['model_list'])
        # The real current adapter rejects incomplete official SKU coverage.
        state['models'].pop()
        with pytest.raises(TikTokPromotionBlocked,match='complete_sku_set_changed'):
            sh.prepare_postpublish_promotion(request)
        state['models']=prices.B_MODELS.copy()
        wrong=SimpleNamespace(**vars(request))
        wrong.prerequisite_context={**proof,'external_id':'shopee:1527371343:item:999'}
        with pytest.raises(TikTokPromotionBlocked,match='original_item_changed'):
            sh.prepare_postpublish_promotion(wrong)
        intents=[]
        def progress(*args,**kwargs):intents.append(kwargs['write_boundary'])
        dispatch=SimpleNamespace(**vars(request),command={'payload':command},proof={'payload':prepared['proof']},
                                 prepared_command_digest=sh._digest(command),progress_recorder=progress)
        with pytest.raises(TikTokPromotionDispatchError):sh.dispatch_postpublish_promotion(dispatch)
        assert intents==['PRE_INVOCATION_INTENT'] and state['mutations']==1
        assert state['body']==body  # No integer truncation and no second POST.


def test_existing_durable_OneClick_unknown_never_replays(tmp_path):
    # Execute the existing real OneClick claim/intent/restart consumer test;
    # its product/actor fixture is separate from the actual494 price evidence.
    from tests.test_oneclick_postpublish_promotions import (
        test_promotion_open_intent_restart_requires_reconciliation_and_replay_zero,
    )
    test_promotion_open_intent_restart_requires_reconciliation_and_replay_zero(tmp_path)
