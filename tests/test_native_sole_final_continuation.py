"""Explicit same-service FIFO and bounded READ, using owned real native rows.

Closed marketplace responses below test persistence/scheduling only. They are
not official live publication outcomes or production account permissions.
"""
from copy import deepcopy
from dataclasses import replace
import json
import threading
from types import SimpleNamespace

import pytest

from modules.products import server, release_adapters as adapters
from modules.miaoshou import client
from shared_platform import native_sole_final_service as channel
from shared_platform import native_sole_final_readback as readonly
from shared_platform import publication_rounds
from shared_platform import release_store
from domains.channel_operations.release_executor import AdapterExecutionResult
from test_round1_workspace_freeze import live
from test_native_sole_final_service import _installed, _registry, _http, _post


def _second_completed(live, monkeypatch, installed, tmp_path):
    from test_native_common_retained_review_graph import _propose_market_inputs, _market
    from test_native_common_write_census import _origin
    from test_native_common_service_facts import _REAL_POST_OPEN, CONFIG, LocalResponse
    from test_service_common_observation import ClosedCommonTransport
    # A second actual product is already supplied by the original owned live
    # fixture. Build its own R1/R2 and immutable source, never clone frozen facts.
    second={**live,'offer':'3828811809'}
    directory=publication_rounds.report_dir(second['offer'])
    directory.mkdir(parents=True,exist_ok=True)
    directory.joinpath('first-review.json').write_text(json.dumps({'offer_id':second['offer'],
        'product_center_revision':7,'image_execution_plan':{'schema_version':'first-review-image-plan/v1',
        'status':'PROPOSED','source_actions':[],'generated_assets':[],'summary':{}}}),encoding='utf-8')
    first_post=client.urllib.request.urlopen
    monkeypatch.setattr(server,'_NATIVE_FINAL_SERVICE',None)
    _propose_market_inputs(second,monkeypatch)
    store,plan,payload=_origin(second,monkeypatch)
    assert store is installed.store
    monkeypatch.setattr(client,'post_open',_REAL_POST_OPEN)
    detail=ClosedCommonTransport.detail_for(payload)
    def closed(request,timeout):
        path=request.full_url[len(client.OPEN_BASE_URL):]
        raw=request.data.decode();body=json.loads(raw)
        if str(body.get('commonCollectBoxDetailId'))!=second['offer']:
            return first_post(request,timeout)
        assert path in {client.COMMON_DETAIL_OBSERVATION_PATH,
            '/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail'}
        assert request.get_header('X-app-key')==CONFIG['app_id'] and timeout==30
        assert request.get_header('X-sign')==client._open_sign(CONFIG['app_secret'],path,
            int(request.get_header('X-timestamp')),CONFIG['app_id'],raw)
        if path==client.COMMON_DETAIL_OBSERVATION_PATH:
            value={'result':'success','data':{'editCommonCollectBoxDetail':deepcopy(detail),'ossMd5':'owned-second-md5'}}
        else:
            with store._connect_readonly() as db:
                row=db.execute('SELECT * FROM release_runs WHERE plan_id=?',(plan['plan_id'],)).fetchone()
                assert row['approval_id'] is None and row['technical_execution_state']=='UNKNOWN'
            value={'result':'success','data':{}}
        return LocalResponse(json.dumps(value,ensure_ascii=False).encode(),url=request.full_url)
    monkeypatch.setattr(client.urllib.request,'urlopen',closed)
    monkeypatch.setattr(client.urllib.request,'build_opener',lambda *handlers:SimpleNamespace(open=closed))
    code,result=server._prepare_miaoshou_release({'offer_id':second['offer'],
        'plan_id':plan['plan_id'],'confirm_miaoshou_write':True})
    assert code==200 and result['native_run']['state']=='CONFIRMED_WRITE',result
    market=_market(store,plan,result['native_run']['run_id'])
    monkeypatch.setattr(server,'_NATIVE_FINAL_SERVICE',installed)
    return market


def _join(installed):
    thread=installed._execution_thread
    assert thread is not None
    thread.join(timeout=40)
    assert not thread.is_alive(),installed._execution_results


def _one_accepted_target(monkeypatch, *, late=False, wrong=False):
    original=adapters.production_adapter_registry()
    calls=[]
    def dispatch(request):
        calls.append(request)
        if request.target_label=='tiktok:LH_PH':
            return AdapterExecutionResult(True,False,'owned accepted submission','7:8',
                {'source':'miaoshou_open_api','accepted':True,'detail_id':7,'shop_id':8,
                 'write_outcome':'submission_accepted','external_writes_performed':['owned:submission']},True)
        return AdapterExecutionResult(True,True,'owned original verified target','owned:'+request.target_label,
            {'source':'OWNED_UNIT_READBACK','verified':True,'target_label':request.target_label})
    monkeypatch.setattr(adapters,'production_adapter_registry',lambda:{name:replace(item,execute=dispatch,
        blocker=None,automatic_first_attempt_mode='ENABLED') for name,item in original.items()})
    reads=[]
    def search(path,token,params,body):
        assert path==adapters.TIKTOK_SEARCH_PATH and body['seller_skus']
        reads.append(body['seller_skus'][0])
        if late and len(reads)==1:return {'code':0,'data':{'products':[]}}
        return {'code':0,'data':{'products':[{'id':'91','seller_sku':body['seller_skus'][0]}]}}
    expectation={}
    def detail(path,token,params):
        assert path==adapters.TIKTOK_DETAIL_PATH.format(product_id='91')
        return {'code':0,'data':{'title':'wrong' if wrong else expectation['title'],
            'skus':[{'seller_sku':expectation['seller_sku'],'price':{'sale_price':expectation['price']}}],
            'main_images':[{'urls':['https://owned.invalid/'+str(i)]} for i in range(expectation['images'])],
            'category_id':'600338','status':'ACTIVATE'}}
    monkeypatch.setattr(adapters,'_tiktok_shop',lambda region:('owned-token',{'id':'8','cipher':'owned-cipher'}))
    monkeypatch.setattr(adapters,'tiktok_post',search)
    monkeypatch.setattr(adapters,'tiktok_get',detail)
    monkeypatch.setattr(adapters,'_cache_verified_tiktok_listing',lambda **_:pytest.fail('observer must not cache/write'))
    monkeypatch.setattr(adapters,'_repair_tiktok_title',lambda **_:pytest.fail('READ observer must not repair'))
    return calls,reads,expectation


@pytest.mark.parametrize('submitted',[False,True],ids=['all-verified','read-scheduled-after-next-decision'])
def test_two_real_offers_queue_after_one_approval_each_and_dispatch_in_order_without_busy_loss(live,monkeypatch,tmp_path,submitted):
    installed,first,*_=_installed(live,monkeypatch,tmp_path)
    second=_second_completed(live,monkeypatch,installed,tmp_path)
    started,proceed=threading.Event(),threading.Event()
    calls=_registry(monkeypatch,verified=not submitted,started=started,proceed=proceed)
    original_observer=readonly._official_observation
    read_order=[]
    def observe(payload,target):
        read_order.append((payload['product_id'],len(calls)))
        return original_observer(payload,target)
    monkeypatch.setattr(readonly,'_official_observation',observe)
    http,thread=_http(installed)
    try:
        prepared=[]
        for plan in (first,second):
            code,value=_post(http,channel.PREFIX+'prepare',{'plan_id':plan['plan_id']})
            assert code==200,value
            prepared.append(value)
        bodies=[{'nonce':value['nonce'],'review_digest':value['review_digest']} for value in prepared]
        code,one=_post(http,channel.PREFIX+'decision',bodies[0]);assert code==202,one
        assert started.wait(timeout=15)
        code,two=_post(http,channel.PREFIX+'decision',bodies[1]);assert code==202,two
        code,repeated=_post(http,channel.PREFIX+'decision',bodies[1]);assert code==202,repeated
        assert repeated['decision']['decision_id']==two['decision']['decision_id']
        assert len(calls)==1 and installed._pending_count()==2
        assert list(installed._execution_queue)==[two['decision']['decision_id']]
        proceed.set();_join(installed)
        assert [request.plan_id for request in calls]==[first['plan_id']]*len(first['targets'])+[second['plan_id']]*len(second['targets'])
        for value in (one,two):
            assert installed._execution_results[value['decision']['decision_id']][1]['completed'] is (not submitted)
        if submitted:
            assert read_order and all(count==len(calls) for _,count in read_order)
            assert {offer for offer,_ in read_order}=={first['product_id'],second['product_id']}
        with installed.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0]==2
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0]==2
            assert db.execute("SELECT COUNT(*) FROM release_runs WHERE technical_execution_state='CONFIRMED_WRITE'").fetchone()[0]==2
        assert all(row['attempts']==1 for plan in (first,second)
            for row in installed.store.get_run('release-run:'+plan['payload_digest'][:24])['targets'])
    finally:
        proceed.set()
        if installed._execution_thread:installed._execution_thread.join(timeout=40)
        http.shutdown();http.server_close();thread.join(timeout=5);installed.close()


def test_full_explicit_queue_does_not_consume_real_nonce_or_persist_an_unserviceable_approval(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    http,thread=_http(installed)
    try:
        value=installed.prepare(market['plan_id'])
        # Capacity mechanics only; these identifiers are never processed or used
        # as native identity/approval/permission facts.
        installed._execution_queue.extend('owned-capacity-'+str(i) for i in range(32))
        before=installed.store.path.read_bytes()
        code,result=_post(http,channel.PREFIX+'decision',{'nonce':value['nonce'],'review_digest':value['review_digest']})
        assert code==409 and result['error']=='NATIVE_EXPLICIT_DECISION_QUEUE_FULL_BEFORE_APPROVAL'
        assert installed.store.path.read_bytes()==before
        assert installed.store.get_plan(market['plan_id'])['status']==release_store.PLAN_PENDING_APPROVAL
        with installed.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0]==0
    finally:
        installed._execution_queue.clear()
        http.shutdown();http.server_close();thread.join(timeout=5);installed.close()


@pytest.mark.parametrize('wrong',[False,True],ids=['matching-sku-unbound-shop','mismatching-sku-unbound-shop'])
def test_accepted_target_gets_bounded_official_read_only_rounds_without_another_write(live,monkeypatch,tmp_path,wrong):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    calls,reads,expected=_one_accepted_target(monkeypatch,late=True,wrong=wrong)
    # This capability negative must never call the mock official reader. Its
    # expected detail is deliberately unpopulated: a region/first-shop fixture
    # is no frozen cross-domain mapping and cannot establish success.
    try:
        value=installed.prepare(market['plan_id'])
        code,accepted=installed.approve_and_submit(nonce=value['nonce'],review_digest=value['review_digest'])
        assert code==202,accepted
        _join(installed)
        outcome=installed._execution_results[accepted['decision']['decision_id']][1]
        run=installed.store.get_run('release-run:'+market['payload_digest'][:24])
        target=next(row for row in run['targets'] if row['target_label']=='tiktok:LH_PH')
        assert len(calls)==len(market['targets']) and target['attempts']==1
        # Region/first-shop mocks cannot establish a cross-domain frozen shop
        # mapping, even when their SKU/title matches. No official READ occurs.
        assert reads==[]
        assert target['submission']['evidence']['accepted'] is True
        assert target['status']=='SUBMITTED_UNVERIFIED' and not target['readback']
        assert outcome['readback_pending'] is True and outcome['completed'] is False
        assert outcome['automatic_read_round']==1
        observation=outcome['readonly_observation']
        reason=next(row for row in observation['observations'] if row['target_label']=='tiktok:LH_PH')
        assert reason['capability']=='FROZEN_OFFICIAL_SHOP_MAPPING_UNAVAILABLE'
        assert reason['external_writes_performed']==[]
        before=len(calls)
        assert installed.submit_readback(accepted['decision']['decision_id'])[0]==202
        _join(installed)
        assert len(calls)==before and reads==[]
        with installed._operations.engine.transaction() as db:
            assert db.execute("SELECT state FROM workbench_execution WHERE template='publication'").fetchone()[0]!='completed'
    finally:installed.close()


def test_sites_without_official_reader_and_unbound_submission_never_call_a_provider(monkeypatch):
    monkeypatch.setattr(adapters,'_tiktok_readback',lambda **_:pytest.fail('capability/identity missing must not read'))
    for label in ('tiktok:MX','tiktok:GB','shopee:MY','ozon:RU','tiktok:LH_PH'):
        result=readonly._official_observation({}, {'target_label':label,'external_id':'owned',
            'submission':{'evidence':{'accepted':True}}})
        assert result['verified'] is False and result['capability']
        assert result['external_writes_performed']==[]


def test_unknown_target_does_not_get_readback_or_a_second_dispatch_from_duplicate_post(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    calls=_registry(monkeypatch,unknown=True)
    monkeypatch.setattr(readonly,'observe_submissions',lambda *a:pytest.fail('unbound UNKNOWN is not a submission'))
    try:
        value=installed.prepare(market['plan_id'])
        code,accepted=installed.approve_and_submit(nonce=value['nonce'],review_digest=value['review_digest'])
        assert code==202
        _join(installed);count=len(calls);assert count>0
        code,again=installed.approve_and_submit(nonce=value['nonce'],review_digest=value['review_digest'])
        assert code==200 and not again.get('completed')
        assert len(calls)==count
        assert installed._readback_schedule=={} and not installed._worker_running
        actual=installed.store.get_run('release-run:'+market['payload_digest'][:24])
        assert all(row['attempts']==1 for row in actual['targets'])
    finally:installed.close()


def test_typed_readback_without_actual_native_execution_scope_cannot_close_a_target(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    try:
        before=installed.store.path.read_bytes()
        proof=readonly._SubmissionReadback('caller-run','tiktok:LH_PH','caller-idempotency',1,'7:8','0'*64,
            json.dumps({'source':'official_tiktok_shop_api','verified':True,'checks':{'anything':True},'external_writes_performed':[]}))
        with pytest.raises(release_store.ReleaseAuthorizationError,match='source recheck'):
            installed.store._record_native_submission_readback(proof)
        assert installed.store.path.read_bytes()==before
    finally:installed.close()
