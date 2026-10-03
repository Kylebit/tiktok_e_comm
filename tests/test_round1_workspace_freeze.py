"""Actual dashboard, state CAS, Handler approval and immutable snapshot consumer."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import subprocess
import os
from urllib.parse import urlencode

import pytest
from modules.products import server
from modules.sourcing import new_product_workbench as workbench
from modules.shopee import oneclick_release as shopee
from shared_platform import release_control, release_store, publication_rounds, report_store
from test_release_control import _release_fixture
from test_round1_category_observations import context
from test_round1_initial_options import choice


@pytest.fixture
def live(tmp_path, monkeypatch):
    root, _ = _release_fixture(tmp_path)
    actual_dashboard = release_control.build_release_dashboard
    _, _, fake, store = context(tmp_path, monkeypatch)
    monkeypatch.setattr(release_control, 'build_release_dashboard', actual_dashboard)
    monkeypatch.setattr(actual_dashboard, '__kwdefaults__', {**actual_dashboard.__kwdefaults__, 'root':root})
    monkeypatch.setattr(server, 'ROOT', root)
    monkeypatch.setattr(workbench, 'ROOT', root)
    monkeypatch.setattr(workbench, 'STATE_DIR', root/'data/new_product_workbench')
    monkeypatch.setattr(publication_rounds, 'REPORTS_ROOT', root/'reports/product-preparation')
    monkeypatch.setattr(release_store, 'DEFAULT_RELEASE_STORE_PATH', store.path)
    # An actual full application store exists before auxiliary R3 readers run.
    # No fake R3 result or product approval is seeded.
    with store._connect() as connection:
        store._ensure_schema(connection)
    monkeypatch.setattr(report_store, 'DEFAULT_REPORT_STORE_PATH', root/'data/reports.db')
    from shared_platform import publication_r3_image_bridge
    from shared_platform.publication_runtime_config import capture_startup_config
    monkeypatch.setattr(publication_r3_image_bridge,'REPORTS_ROOT',root/'reports/product-preparation')
    monkeypatch.setattr(server,'R3_STARTUP_CONFIG',capture_startup_config(root=root,environ={}))
    (root/'shared_platform').mkdir()
    (root/'shared_platform/entry_catalog.json').write_bytes((Path(__file__).resolve().parents[1]/'shared_platform/entry_catalog.json').read_bytes())
    monkeypatch.setattr(shopee, '_current_credentials', lambda _: shopee._prepare_transport_factory('MY').credentials)
    offer='3828811808'
    path=root/'data/new_product_workbench'/f'{offer}.json'
    state=json.loads(path.read_text(encoding='utf-8'))
    state['review']['selected_sites']=['lh_ph','lh_my','mx','gb']
    state['review']['category']={'name':'Home decor'}
    path.write_text(json.dumps(state),encoding='utf-8')
    second=deepcopy(state);second['offer_id']='3828811809';second['review']['title']='Second synthetic product'
    second['content_package']['collect_box_id']='3828811809'
    (root/'data/new_product_workbench/3828811809.json').write_text(json.dumps(second),encoding='utf-8')
    second_package=root/'outputs/image_suite_from_miaoshou/3828811809';second_package.mkdir()
    package=json.loads((root/'outputs/image_suite_from_miaoshou'/offer/'review_package.json').read_text(encoding='utf-8'))
    package['collect_box'].update(detail_id=3828811809,source_item_id='16882',source_title='Second synthetic product')
    (second_package/'review_package.json').write_text(json.dumps(package),encoding='utf-8')
    plan={'schema_version':'first-review-image-plan/v1','status':'PROPOSED','source_actions':[], 'generated_assets':[],
          'summary':{'translation_positions':[],'localized_output_count':0,'net_new_output_count':0,'paid_generation_required':False}}
    directory=publication_rounds.report_dir(offer);directory.mkdir(parents=True)
    (directory/'first-review.json').write_text(json.dumps({'offer_id':offer,'product_center_revision':7,'image_execution_plan':plan}),encoding='utf-8')
    events=[]
    # The browser loads the task panel as well as R1. Keep that GET backed by
    # an isolated web-only engine: the synthetic product root is intentionally
    # not a Git checkout, and must never start an operations worker.
    from shared_platform.operations_runtime import OperationsWorker, RuntimeProfile
    from shared_platform.workbench_engine import WorkbenchEngine
    operations_profile = RuntimeProfile(
        root=root, data_root=root/'data/operations/stable',
        environment='stable', version='synthetic-r1-http',
    )
    operations_engine = WorkbenchEngine(
        operations_profile.data_root/'tasks.db',
        {'code_version': operations_profile.version, 'environment': operations_profile.environment},
    )
    operations_worker = OperationsWorker(operations_engine, operations_profile, {})
    operations_runtime = SimpleNamespace(
        engine=operations_engine, profile=operations_profile,
        worker=operations_worker, worker_enabled=False,
    )
    class FixtureHandler(server.Handler):
        def _handle_product_flow_proxy(self,method):
            if self.path.startswith('/api/product-flow/'):
                current=workbench.load_state(offer)
                self._json(200,{'ok':True,'offer_id':offer,'revision':current['_revision'],'source':{},'review':current['review'],'items':[],'groups':[]})
                return True
            return False
        def do_GET(self):
            if self.path.startswith('/api/proxy-image'):
                return self._bytes(200,b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="blue"/></svg>','image/svg+xml')
            if self.path == f'/api/product-workspace/publication-history?offer_id={offer}':
                # The maintenance launcher owns this unrelated optional route.
                # R1's isolated HTTP fixture has no historical publication store.
                return self._json(200,{'ok':True,'offer_id':offer,'items':[],'count':0,
                                       'display_mode':'HISTORICAL_READ_ONLY',
                                       'execution_authority':False,'history_status':'UNAVAILABLE'})
            return super().do_GET()
    httpd=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
    httpd.operations_runtime=operations_runtime
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    def restart():
        nonlocal httpd,thread
        port=httpd.server_port
        httpd.shutdown();httpd.server_close();thread.join(5)
        monkeypatch.setattr(release_store,'default_release_store',lambda:release_store.ReleaseStore(store.path))
        monkeypatch.setattr(server,'_ROUND1_CATEGORY_INSTANCE','reopened-http-service')
        httpd=ThreadingHTTPServer(('127.0.0.1',port),FixtureHandler)
        httpd.operations_runtime=operations_runtime
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    def call(action, body, method='POST'):
        route='/api/product-workspace/approve' if action=='approve' else '/api/product-workspace/round1-category/'+action
        if method=='GET':route+='?'+urlencode(body)
        conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=15)
        try:
            conn.request(method,route,body=json.dumps(body) if method=='POST' else None,headers={'Content-Type':'application/json'})
            response=conn.getresponse();value=json.loads(response.read());events.append({'action':action,'http':response.status,'value':value})
            return response.status,value
        finally:conn.close()
    try:yield {'root':root,'offer':offer,'fake':fake,'store':store,'call':call,'port':httpd.server_port,'events':events,'restart':restart,'operations_runtime':operations_runtime}
    finally:
        httpd.shutdown();httpd.server_close();thread.join(5)
        operations_worker.close()
        (tmp_path/'r1-http-events.json').write_text(json.dumps(events,indent=2),encoding='utf-8')


def prepared(live):
    offer=live['offer'];call=live['call']
    dashboard=release_control.build_release_dashboard(offer_id=offer)
    scope={'offer_id':offer,'product_center_revision':7,'requested_targets':dashboard['publication_scope']['selected_labels'],'source_region':'MY'}
    from test_round1_category_observations import prepare_module
    checked=prepare_module().prepare_offer(offer_id=offer,requested_targets=scope['requested_targets'],preview_builder=lambda _:dashboard)
    assert all(code=='CATEGORY_RECEIPT_UNAVAILABLE' for code in checked['blockers']), {
        'blockers':checked['blockers'],'facts':checked['product_facts'],
        'prices':{row['target']:row['price'] for row in checked['targets']}}
    server._round1_category_context(scope)  # Expose fixture input failures before HTTP's safe error envelope.
    code,ctx=call('context',dict(scope,requested_targets=json.dumps(scope['requested_targets'])),'GET')
    assert code==200,ctx
    body=dict(scope,schema_version='round1-category-options-request/v1',request_id='r1-options',context_digest=ctx['context_digest'],account_identity_digest=ctx['source_account']['account_identity_digest'])
    code,result=call('options',body);assert code==200 and result['status']=='SUCCEEDED',result
    code,capture=call('capture',choice(body,result));assert code==200 and capture['status']=='SUCCEEDED',capture
    request=dict(scope,context_digest=ctx['context_digest'],account_identity_digest=ctx['source_account']['account_identity_digest'],observer_reference=capture['observer_reference'],request_id='r1-prepare')
    code,packet=call('prepare',request);assert code==200 and packet['status']=='PREPARED',packet
    return packet,request


def test_actual_http_approval_persist_and_restart_readback(live,monkeypatch):
    packet,request=prepared(live);call=live['call'];offer=live['offer']
    before=workbench.load_state(offer);assert not before.get('product_approval')
    assert len(live['fake'].calls)==7
    body={'offer_id':offer,'prepared_reference':packet['prepared_reference'],'user_approved':True,'approved_by':packet['approval_actor']}
    code,result=call('approve',body)
    assert code==200 and result['status']=='FROZEN',result
    after=workbench.load_state(offer);assert after['_revision']==8 and after['product_approval']['status']=='approved'
    assert result['persisted_readback'] is True
    assert call('approve',body)[1]['snapshot']==result['snapshot'] and workbench.load_state(offer)==after
    live['restart']()
    paths=[live['store'].path,live['root']/'data/new_product_workbench'/f'{offer}.json',
           publication_rounds.report_dir(offer)/'round1-approved-snapshot.json']
    frozen_bytes={str(path):path.read_bytes() for path in paths}
    code,recovered=call('prepare-status',{'offer_id':offer,'request_id':request['request_id']},'GET')
    assert code==200 and recovered['snapshot']==result['snapshot'] and recovered['status']=='FROZEN'
    assert {str(path):path.read_bytes() for path in paths}==frozen_bytes
    disk=json.loads((publication_rounds.report_dir(offer)/'round1-approved-snapshot.json').read_text(encoding='utf-8'))
    assert disk==result['snapshot'] and len(live['fake'].calls)==7


def test_partial_persistence_recovery_never_reapproves(live,monkeypatch):
    packet,request=prepared(live);call=live['call'];offer=live['offer']
    original=publication_rounds.persist_round1_snapshot
    monkeypatch.setattr(publication_rounds,'persist_round1_snapshot',lambda _:(_ for _ in ()).throw(OSError('synthetic disk failure')))
    code,value=call('approve',{'offer_id':offer,'prepared_reference':packet['prepared_reference'],'user_approved':True,'approved_by':packet['approval_actor']})
    assert value['status']=='APPROVED_NOT_FROZEN' and not value['ok'],value
    after=workbench.load_state(offer)
    assert call('prepare-status',{'offer_id':offer,'request_id':request['request_id']},'GET')[1]['status']=='APPROVED'
    monkeypatch.setattr(publication_rounds,'persist_round1_snapshot',original)
    monkeypatch.setattr(workbench,'save_state',lambda *_:pytest.fail('recovery cannot repeat approval'))
    assert call('freeze',{'offer_id':offer,'prepared_reference':packet['prepared_reference']})[1]['status']=='FROZEN'
    assert workbench.load_state(offer)==after and len(live['fake'].calls)==7


@pytest.mark.parametrize('case',['actor','confirmation','facts','image','revoke','revision'])
def test_freeze_rejects_changed_or_untrusted_scope(live,case):
    packet,_=prepared(live);call=live['call'];offer=live['offer']
    body={'offer_id':offer,'prepared_reference':packet['prepared_reference'],'user_approved':True,'approved_by':packet['approval_actor']}
    if case=='actor':body['approved_by']='forged-browser-subject'
    elif case=='confirmation':body['user_approved']=False
    elif case=='image':
        path=publication_rounds.report_dir(offer)/'first-review.json';p=json.loads(path.read_text(encoding='utf-8'));p['image_execution_plan']['summary']['paid_generation_required']=True;path.write_text(json.dumps(p),encoding='utf-8')
    elif case=='revoke':live['store'].invalidate_round1_category_observation(packet['packet']['category_evidence_binding']['receipt']['observer_reference'],'SOURCE_REVOKED')
    else:
        state=workbench.load_state(offer)
        if case=='facts':state['review']['title']='Changed'
        workbench.save_state(offer,state)
    code,result=call('approve',body)
    assert code==409 and result['status']!='FROZEN',result
    assert not workbench.load_state(offer).get('product_approval')
    assert not (publication_rounds.report_dir(offer)/'round1-approved-snapshot.json').exists()


@pytest.mark.parametrize('width',[1440,390])
def test_browser_actual_r1_approval_chain(live,width):
    output=live['root']/'browser';output.mkdir()
    (output/'scenario.json').write_text(json.dumps({'base':f"http://127.0.0.1:{live['port']}",'offer':live['offer'],'width':width}),encoding='utf-8')
    result=subprocess.run([os.environ['ORBIT_NODE_BIN'],str(Path(__file__).parent/'browser/r1_workspace.cjs'),str(output)],capture_output=True,text=True,encoding='utf-8',timeout=100)
    assert result.returncode==0,result.stdout+result.stderr
    assert len(live['fake'].calls)==7
    assert workbench.load_state(live['offer'])['_revision']==8
    assert (publication_rounds.report_dir(live['offer'])/'round1-approved-snapshot.json').is_file()


@pytest.mark.parametrize('mode,expected_gets',[
    ('lost-options',5),('lost-capture',7),('unknown',2),('late',5),
    ('selection',5),('failed',1),('not-started',0),('legacy',5),
    ('history',7),('invalid-history',7),('malformed',5),('unknown409',5),('labels',5),('lost-approval',7)])
def test_browser_r1_reconciliation_boundaries(live,monkeypatch,mode,expected_gets):
    if mode in {'history','invalid-history'}:
        packet,_=prepared(live)
        if mode=='history':
            def unavailable(_):raise shopee.ShopeeOneClickPreDispatchError('synthetic unprepared credentials')
            monkeypatch.setattr(shopee,'_current_credentials',unavailable)
        else:
            live['store'].invalidate_round1_category_observation(packet['packet']['category_evidence_binding']['receipt']['observer_reference'],'SOURCE_REVOKED')
    if mode=='unknown409':
        def rollback(*_,**__):
            monkeypatch.setattr(server,'_ROUND1_CATEGORY_INSTANCE','synthetic-restarted-during-response')
            raise RuntimeError('synthetic result transaction failure')
        monkeypatch.setattr(live['store'],'finish_category_capture',rollback)
    if mode in {'unknown','failed','labels'}:
        original=live['fake'].merchant_get
        def fault(path,params):
            if mode=='unknown' and len(live['fake'].calls)==2:raise TimeoutError('synthetic third GET response lost')
            value=original(path,params)
            if mode=='failed' and path.endswith('category_recommend'):value['response']['category_id_list']=list(range(1,12))
            if mode=='labels' and path.endswith('get_category'):
                for row in value['response']['category_list']:
                    row['original_category_name']='<img src=x onerror="window.fixtureInjected=true">'
            return value
        live['fake'].merchant_get=fault
    output=live['root']/'browser';output.mkdir()
    (output/'scenario.json').write_text(json.dumps({'base':f"http://127.0.0.1:{live['port']}",'offer':live['offer'],'width':390,'mode':mode}),encoding='utf-8')
    result=subprocess.run([os.environ['ORBIT_NODE_BIN'],str(Path(__file__).parent/'browser/r1_workspace.cjs'),str(output)],capture_output=True,text=True,encoding='utf-8',timeout=100)
    assert result.returncode==0,result.stdout+result.stderr
    assert len(live['fake'].calls)==expected_gets
    if mode=='lost-approval':assert workbench.load_state(live['offer'])['_revision']==8
    else:assert not workbench.load_state(live['offer']).get('product_approval')


@pytest.mark.parametrize('category',[{}, {'name':''},{'name':'  '},{'name':12},{'unexpected':'Home'}, [],42])
def test_real_category_projection_rejects_missing_semantics(category):
    from test_round1_category_observations import prepare_module
    from shared_platform.round1_category_evidence import input_digest,CategoryEvidenceError
    packet=prepare_module().prepare_offer(offer_id='12345',requested_targets=['shopee:MY'],category_source_region='MY',
        preview_builder=lambda _:{'ok':True,'product':{'title':'Fixture','revision':7,'category':category},'publication_scope':{'selected_labels':['shopee:MY']}})
    with pytest.raises(CategoryEvidenceError,match='CATEGORY_INPUT_FACTS_REQUIRED'):input_digest(packet)


@pytest.mark.parametrize('category',['Home',{'name':' Home '}])
def test_category_projection_preserves_legal_semantic_strings(category):
    from test_round1_category_observations import prepare_module
    assert prepare_module()._safe_product_facts({'product':{'category':category}})['category_semantic']=='Home'


def test_concurrent_approval_has_one_state_cas(live,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    packet,_=prepared(live);original=workbench.save_state;saves=[]
    def save(*args):saves.append(args[0]);return original(*args)
    monkeypatch.setattr(workbench,'save_state',save)
    body={'offer_id':live['offer'],'prepared_reference':packet['prepared_reference'],'user_approved':True,'approved_by':packet['approval_actor']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:live['call']('approve',body),range(2)))
    assert all(code==200 and value['status']=='FROZEN' for code,value in results),results
    assert saves==[live['offer']] and len(live['fake'].calls)==7


def test_prepare_replay_conflict_and_scope_changed(live):
    packet,request=prepared(live);call=live['call']
    assert call('prepare',request)[1]['prepared_reference']==packet['prepared_reference']
    assert call('prepare',dict(request,observer_reference='category-observation:forged'))[0]==409
    state=workbench.load_state(live['offer']);state['review']['cost_cny']=9;workbench.save_state(live['offer'],state)
    assert call('prepare',request)[0]==409


def test_freeze_requires_actual_approval_and_reference_only(live):
    packet,_=prepared(live);body={'offer_id':live['offer'],'prepared_reference':packet['prepared_reference']}
    assert live['call']('freeze',body)[1]['code']=='R1_APPROVAL_REQUIRED'
    assert live['call']('freeze',dict(body,snapshot=packet['packet']))[0]==409
    assert not workbench.load_state(live['offer']).get('product_approval')


def test_recovery_rejects_post_approval_fact_change(live,monkeypatch):
    packet,_=prepared(live);original=publication_rounds.persist_round1_snapshot
    monkeypatch.setattr(publication_rounds,'persist_round1_snapshot',lambda _:(_ for _ in ()).throw(OSError('synthetic disk')))
    live['call']('approve',{'offer_id':live['offer'],'prepared_reference':packet['prepared_reference'],'user_approved':True,'approved_by':packet['approval_actor']})
    state=workbench.load_state(live['offer']);state['review']['weight_kg']=2;workbench.save_state(live['offer'],state)
    monkeypatch.setattr(publication_rounds,'persist_round1_snapshot',original)
    assert live['call']('freeze',{'offer_id':live['offer'],'prepared_reference':packet['prepared_reference']})[0]==409
    assert not (publication_rounds.report_dir(live['offer'])/'round1-approved-snapshot.json').exists()


@pytest.mark.parametrize('version',[2,3])
def test_transport_unknown_is_not_replayed_across_capture_versions(live,version):
    packet,request=prepared(live)
    ctx=server._round1_category_context(request)
    body={k:request[k] for k in ('offer_id','product_center_revision','requested_targets','source_region','account_identity_digest','context_digest')}
    body.update(schema_version=f'round1-category-capture-request/v{version}',request_id='uncertain-capture')
    receipt=packet['packet']['category_evidence_binding']['receipt']
    if version==2:body.update(category_id=receipt['category']['id'],selected_attributes=receipt['selected_attributes'])
    else:
        options=live['call']('capture-status',{'offer_id':live['offer'],'request_id':'r1-options'},'GET')[1]
        body=choice(body,options);body['request_id']='uncertain-capture'
    def timeout(*_):raise TimeoutError('synthetic transport unavailable')
    live['fake'].merchant_get=timeout
    code,result=live['call']('capture',body)
    assert code==200 and result['status']=='UNKNOWN',result
    assert result['progress']['attempted']==1 and result['progress']['completed']==0
    assert live['call']('capture',body)[1]==result
    live['restart']()
    assert live['call']('capture-status',{'offer_id':live['offer'],'request_id':body['request_id']},'GET')[1]==result
    assert live['call']('capture',body)[1]==result and len(live['fake'].calls)==7


def test_account_drift_blocks_prepared_approval(live,monkeypatch):
    from dataclasses import replace
    packet,_=prepared(live)
    credentials=shopee._current_credentials('MY')
    monkeypatch.setattr(shopee,'_current_credentials',lambda _:replace(credentials,shop_id=credentials.shop_id+1))
    code,result=live['call']('approve',{'offer_id':live['offer'],'prepared_reference':packet['prepared_reference'],'user_approved':True,'approved_by':packet['approval_actor']})
    assert code==409 and result['code']=='R1_ACCOUNT_CHANGED'
    assert not workbench.load_state(live['offer']).get('product_approval')
