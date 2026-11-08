"""One real native/OneClick/default-SDK consumer contract; HTTP is synthetic.

Only actor/R1/R2 authority is an explicit fixture. No transport factory, shop
resolver, promotion prepare/dispatch or original job consumer is replaced.
"""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import socket
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import urllib.request

import pytest

from shared_platform import native_companion_promotions as native
from shared_platform.native_sole_final_execution import _ACTIVE, _NativeExecution
from shared_platform.oneclick_release_controlplane import OneClickReleaseStore, _SCHEMA
from shared_platform.postpublish_promotions import build_approved_postpublish_promotion_policy
from shared_platform.release_store import ReleaseStore
from tests.test_oneclick_release_controlplane import _plan_payload


def test_original_unused_tiktok_batch_joins_current_provider_identities_and_never_replays(tmp_path, monkeypatch):
    network=[]
    def no_network(*a,**k):network.append(True);pytest.fail('real network forbidden')
    monkeypatch.setattr(socket.socket,'connect',no_network)
    token=tmp_path/'token.json'
    token.write_text(json.dumps({'access_token':'SYNTHETIC_ONLY','access_token_expire_in':int(time.time())+3600}))
    settings=tmp_path/'settings.json'
    settings.write_text(json.dumps({'app_key':'SYNTHETIC_ONLY','app_secret':'SYNTHETIC_ONLY','token_file':str(token)}))
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS',str(settings))
    from modules.miaoshou import client as erp
    credentials=tmp_path/'erp.json';credentials.write_text(json.dumps({'app_id':'SYNTHETIC_ONLY','app_secret':'SYNTHETIC_ONLY'}))
    monkeypatch.setattr(erp,'CONFIG_CANDIDATES',(credentials,));monkeypatch.setattr(erp,'OPEN_REQUEST_INTERVAL_SECONDS',0)
    from modules.tiktok import oneclick_promotion as promotion
    from core import api_client
    promotion.configure_tiktok_promotion_transport_factory(None)
    identities={'TH':('13295228','90001','product-TH','THB'),'VN':('13295291','90002','product-VN','VND')}
    sh_labels=tuple('promotion:shopee:'+r for r in ('MY','PH','TH','VN'))
    actions=('promotion:tiktok:LH_TH','promotion:tiktok:LH_VN')
    targets=[a.removeprefix('promotion:') for a in (*sh_labels,*actions)]
    payload=_plan_payload(targets=targets)
    payload['r3_marketplace_binding']={'technical_category_metadata':{'tiktok:LH_'+region:{'shop_id':values[0],'site':region}
        for region,values in identities.items()}}
    payload['approved_postpublish_promotion_policy']=build_approved_postpublish_promotion_policy(approval_reference='SYNTHETIC_original_approval')
    payload['pricing']={'selected_targets':{label:{'store_prices':[{'list_price':100,'sale_after_discount':65 if label.startswith('tiktok:') else 70,
        'discount_reserve_pct':35 if label.startswith('tiktok:') else 30,
        'currency':({'MY':'MYR','PH':'PHP','TH':'THB','VN':'VND'})[label.rsplit('_',1)[-1].split(':')[-1]]}]} for label in targets}}
    release=ReleaseStore(tmp_path/'release.db');created=release.create_plan(payload)
    release.approve_plan(created['plan_id'],approved_by='Kyle',user_approved=True,confirmation_token=created['confirmation_token'])
    plan=release.get_plan(created['plan_id']);run=release.start_run(plan['plan_id'])
    with sqlite3.connect(release.path) as db:
        db.executescript(_SCHEMA)
        for label in targets:
            is_sh=label.startswith('shopee:');region=label.rsplit('_',1)[-1].split(':')[-1]
            external='shopee:101:item:201' if is_sh else identities[region][2]
            detail={'outcome_unknown':False}
            if is_sh:detail.update(target_label=label,stage='READBACK',provider_identity_bound=True,provider_code='shopee_official_readback_verified')
            evidence={'schema_version':'native-scoped-v4-target-result/v1','target_label':label,'verified':True,'status':'PUBLISHED',
                'outcome_unknown':False,'reconciliation_required':False,'canonical_platform_result':{'readback_completed':True,
                    'platform':'SHOPEE' if is_sh else 'TIKTOK','targets':[{'target_label':label,'status':'PUBLISHED','evidence':detail}]}}
            db.execute("UPDATE release_target_runs SET status='SUCCEEDED',external_id=? WHERE run_id=? AND target_label=?",(external,run['run_id'],label))
            db.execute('INSERT INTO release_target_readbacks VALUES(?,?,?,?,?)',(run['run_id'],label,json.dumps(evidence),native._hash(evidence),'before'))
    original_run=release.get_run(run['run_id'])
    active=object.__new__(_NativeExecution)
    active.store,active.plan_id,active.decision_id,active.owner_sid=release,plan['plan_id'],'SYNTHETIC_decision','SYNTHETIC_owner'
    active.runtime=SimpleNamespace(_operations=SimpleNamespace(new_task_worker=object()))
    active.gate=lambda data,*,store:({'plan':plan,'run':active.store.get_run(run['run_id'])},None)
    from shared_platform import native_new_task_v4_targets
    def source_boundary(a,p):assert a is active and p==plan
    monkeypatch.setattr(native_new_task_v4_targets,'scope',source_boundary)
    from domains.channel_operations.oneclick_release_adapters import production_adapter_registry
    control=OneClickReleaseStore(release.path)
    marker=_ACTIVE.set(active)
    try:
        with native._execution_scope(active,plan,original_run,native.selected_targets(sh_labels)):
            job=control.ensure_job(plan=plan,run=original_run,product_revision=31,registry=production_adapter_registry(),active_target_labels=native.selected_targets(sh_labels))
            control.start_explicit_batch(job['job_id'],target_labels=native.selected_targets(sh_labels))
    finally:_ACTIVE.reset(marker)
    job_id=job['job_id']
    # Explicit pre-existing synthetic SH completion, outside the defect under
    # test. Its literal terminal rows/receipts/occurrences/history must survive.
    with sqlite3.connect(release.path) as db:
        for action in sh_labels:
            classes=['shopee:promotion:discount'];encoded=json.dumps(classes,separators=(',',':'))
            result={'canonical_status':'SUCCEEDED','external_write_count':1,'external_write_classes':classes,'readback_verified':True}
            db.execute("UPDATE oneclick_release_targets SET status='SUCCEEDED',dispatch_count=1,result_json=?,cumulative_external_writes_json=?,cumulative_external_writes_digest=?,cumulative_external_write_count=1,cumulative_external_write_lower_bound=1,cumulative_external_write_upper_bound=1 WHERE job_id=? AND target_label=?",(json.dumps(result),encoded,native._hash(classes),job_id,action))
            receipt=json.dumps({'schema_version':'release-outcome-receipt/v1','outcome':{'class':'SUCCESS'},'dispatch':{'boundary':'ACCEPTED','external_write_count':1}})
            db.execute("INSERT INTO oneclick_release_outcomes(job_id,target_label,attempt,receipt_json,receipt_digest,created_at,updated_at) VALUES(?,?,1,?,?,'before','before')",(job_id,action,receipt,hashlib.sha256(receipt.encode()).hexdigest()))
            db.execute("INSERT INTO oneclick_release_write_occurrences(job_id,target_label,attempt,occurrence_id,intended_classes_json,intended_classes_digest,prior_classes_json,prior_classes_digest,confirmed_lower_bound,possible_upper_bound,status,resolution_count,created_at,resolved_at) VALUES(?,?,1,'original-sh',?,?,'[]',?,1,1,'CONFIRMED',1,'before','before')",(job_id,action,encoded,native._hash(classes),native._hash([])))
        db.execute("UPDATE oneclick_release_jobs SET status='SUCCEEDED',completed_at='before' WHERE job_id=?",(job_id,))
    def rows(path,table):
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE job_id=? ORDER BY rowid',(job_id,))]
    initial={t:rows(release.path,t) for t in ('oneclick_release_targets','oneclick_release_events','oneclick_release_outcomes','oneclick_release_write_occurrences')}
    pristine=tmp_path/'pristine.db'
    with sqlite3.connect(release.path) as src,sqlite3.connect(pristine) as dest:src.backup(dest)
    mode={'value':'valid'};calls=[];members=set();lookups={'TH':0,'VN':0}
    class Response(BytesIO):
        status=200;headers={}
    def provider(request,**kwargs):
        u=urlsplit(request.full_url);q=parse_qs(u.query);path=u.path;method=request.get_method()
        if u.hostname=='openapi-erp.91miaoshou.com':
            assert path=='/open/v1/product/shop/shop/get_shop_list' and method=='POST'
            region=json.loads(request.data)['site'];lookups[region]+=1
            internal,official,product,currency=identities[region]
            if mode['value']=='drift_before_intent' and lookups[region]>=3:official+='-new'
            row={'shopId':internal,'shopName':'LivelyHive','platformShopName':'LivelyHive','site':region,'platform':'TIKTOK',
                'platformShopId':official,'tiktokShopId':official}
            if mode['value']=='explicit_conflict':row['tiktokShopId']='other'
            if mode['value']=='internal_alias_conflict':row['id']='other'
            if mode['value']=='erp_name_conflict':row['name']='Other'
            data=[row]
            if mode['value']=='duplicate_erp':data.append(dict(row,shopId='other'))
            result={'result':'success','data':{'list':data,'total':2 if mode['value']=='partial_erp' else len(data)}}
        else:
            assert u.hostname=='open-api.tiktokglobalshop.com'
            assert kwargs.get('attempts')==1 and kwargs.get('allow_curl_fallback') is False
            if path=='/authorization/202309/shops':
                data=[]
                for region,(_,official,product,currency) in identities.items():
                    if mode['value']=='drift_before_intent' and lookups[region]>=3:official+='-new'
                    row={'id':official,'shop_id':official,'name':'LivelyHive','region':region,'cipher':'cipher-'+official}
                    if mode['value']=='official_alias_conflict':row['shop_id']='other'
                    data.append(row)
                    if mode['value']=='duplicate_official':data.append(dict(row,id=official+'-other'))
                result={'code':0,'data':{'shops':data}}
            else:
                region=next(r for r,v in identities.items() if q.get('shop_cipher')==['cipher-'+v[1]])
                internal,official,product,currency=identities[region]
                if path=='/promotion/202309/activities/search':
                    result={'code':0,'data':{'activities':[{'id':'activity-'+region,'activity_type':'DIRECT_DISCOUNT','status':'ONGOING'}], 'total_count':1,'next_page_token':''}}
                elif path=='/promotion/202309/activities/activity-'+region:
                    result={'code':0,'data':{'id':'activity-'+region,'activity_type':'DIRECT_DISCOUNT','status':'ONGOING','begin_time':1,'end_time':4102444800,
                        'products':[{'id':product,'discount':'35'}] if region in members else []}}
                elif path=='/product/202309/products/'+product:
                    result={'code':0,'data':{'id':product,'status':'ACTIVATE','skus':[{'seller_sku':'0954','price':{'sale_price':'100','currency':currency}}]}}
                elif path=='/promotion/202309/activities/activity-'+region+'/products':
                    assert method=='PUT' and region not in members
                    assert json.loads(request.data)['products']==[{'id':product,'discount':'35','quantity_limit':-1,'quantity_per_user':-1}]
                    members.add(region)
                    if mode['value']=='unknown_put':raise OSError('synthetic unknown transport')
                    result={'code':0,'data':{}}
                else:pytest.fail('unexpected official endpoint')
        calls.append((method,path));return Response(json.dumps(result).encode())
    monkeypatch.setattr(api_client,'urlopen_retry',provider);monkeypatch.setattr(urllib.request,'urlopen',provider)
    def consume():
        mark=_ACTIVE.set(active)
        try:return native.execute_active(active,{},actions)
        finally:_ACTIVE.reset(mark)
    result=consume()
    current=rows(release.path,'oneclick_release_targets')
    assert all(r['status']=='SUCCEEDED' and r['dispatch_count']==1 for r in current if r['target_label'] in actions),result
    assert sum(method=='PUT' for method,path in calls)==2 and all(v>=3 for v in lookups.values())
    def sh_semantics(value):return [{k:v for k,v in r.items() if k not in {'active_for_batch','updated_at'}} for r in value if r['target_label'] in sh_labels]
    assert sh_semantics(current)==sh_semantics(initial['oneclick_release_targets'])
    for table in ('oneclick_release_outcomes','oneclick_release_write_occurrences'):
        assert [r for r in rows(release.path,table) if r['target_label'] in sh_labels]==initial[table]
    assert release.get_run(run['run_id'])==original_run and release.get_plan(plan['plan_id'])==plan
    count=len(calls);consume();assert len(calls)==count
    with sqlite3.connect(release.path) as db:
        assert db.execute('SELECT COUNT(*) FROM oneclick_release_jobs').fetchone()[0]==1
        assert db.execute('SELECT batch_sequence FROM oneclick_release_jobs').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM oneclick_release_events WHERE event_id<=?',(initial['oneclick_release_events'][-1]['event_id'],)).fetchone()[0]==len(initial['oneclick_release_events'])
    for bad in ('explicit_conflict','internal_alias_conflict','erp_name_conflict','duplicate_erp','partial_erp','official_alias_conflict','duplicate_official','drift_before_intent','unknown_put'):
        cloned=tmp_path/(bad+'.db')
        with sqlite3.connect(pristine) as src,sqlite3.connect(cloned) as dest:src.backup(dest)
        mode['value']=bad;calls.clear();members.clear();lookups.update(TH=0,VN=0);active.store=ReleaseStore(cloned)
        consume();after=rows(cloned,'oneclick_release_targets');tk=[r for r in after if r['target_label'] in actions]
        if bad=='unknown_put':
            assert all(r['status']=='RECONCILIATION_REQUIRED' and r['pending_write_intent_json'] for r in tk)
            prior=len(calls);consume();assert len(calls)==prior and len(members)==2
        else:
            assert not any(method=='PUT' for method,path in calls) and not members
            assert all(r['status'] in {'BLOCKED_CAPABILITY','FAILED_PRE_SUBMIT'} for r in tk)
            assert all(r['pending_write_intent_json'] is None and r['cumulative_external_write_upper_bound']==0 for r in tk)
            if bad=='drift_before_intent':assert all(v>=3 for v in lookups.values())
        assert sh_semantics(after)==sh_semantics(initial['oneclick_release_targets'])
    assert network==[]
