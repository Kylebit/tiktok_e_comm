"""Actual context/correlated capture/store/resolve/prepare/freeze consumers."""
from pathlib import Path
import http.client,json,threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from modules.products import server
from modules.shopee import oneclick_release as shopee
from shared_platform import release_store
from shared_platform.publication_rounds import build_round1_snapshot
from test_round1_category_observations import context,prepare_module

def test_correlated_actual_tcp_capture_to_offline_freeze(tmp_path,monkeypatch):
    data,preview,fake,store=context(tmp_path,monkeypatch)
    monkeypatch.setattr(shopee,'_current_credentials',lambda _:fake.transport().credentials)
    events=[]
    httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    def call(method,action,body):
        path='/api/product-workspace/round1-category/'+action
        if method=='GET':path+='?'+urlencode(body)
        conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
        try:
            conn.request(method,path,body=json.dumps(body) if method=='POST' else None,headers={'Content-Type':'application/json'})
            response=conn.getresponse();value=json.loads(response.read());events.append({'method':method,'action':action,'status':response.status,'value':value})
            assert response.status==200,value
            return value
        finally:conn.close()
    try:
        query={k:data[k] for k in ('offer_id','product_center_revision','source_region')};query['requested_targets']=json.dumps(data['requested_targets'])
        local=call('GET','context',query);assert not store.path.exists() and not fake.calls
        request=dict(data,schema_version='round1-category-capture-request/v2',request_id='combined-r1',context_digest=local['context_digest'])
        captured=call('POST','capture',request);assert captured['status']=='SUCCEEDED'
        assert call('POST','capture',request)==captured and len(fake.calls)==2
        assert call('GET','capture-status',{'offer_id':data['offer_id'],'request_id':'combined-r1'})==captured
        reopened=release_store.ReleaseStore(store.path);monkeypatch.setattr(release_store,'default_release_store',lambda:reopened)
        monkeypatch.setattr(shopee,'_prepare_transport_factory',lambda _:(_ for _ in ()).throw(AssertionError('offline consumer cannot capture')))
        identity={k:v for k,v in data.items() if k not in {'category_id','selected_attributes'}}
        resolved=call('POST','resolve',dict(identity,observer_reference=captured['observer_reference']))
        receipt=resolved['receipt'];assert receipt==captured['receipt']
        image_plan={'schema_version':'first-review-image-plan/v1','status':'PROPOSED','source_actions':[],'generated_assets':[],
                    'summary':{'translation_positions':[],'localized_output_count':0,'net_new_output_count':0,'paid_generation_required':False}}
        packet=prepare_module().prepare_offer(offer_id=data['offer_id'],requested_targets=data['requested_targets'],
            preview_builder=lambda _:preview,image_execution_plan=image_plan,category_source_region='MY',
            category_observation=receipt['observer_reference'],category_account_digest=data['account_identity_digest'])
        assert packet['status']=='FIRST_REVIEW_READY'
        frozen=build_round1_snapshot(first_review=packet,state={'_revision':8,'review':{'selected_sites':[]},
            'product_approval':{'status':'approved','approved_by':'Kyle','approval_id':'synthetic-local','input_fingerprint':'synthetic'}},
            approved_by='Kyle',report_directory=tmp_path)
        assert frozen['fact_snapshot']['category_evidence_binding']['receipt']==receipt and len(fake.calls)==2
        (tmp_path/'r1-tcp-chain.json').write_text(json.dumps({'events':events,'synthetic_official_gets':len(fake.calls),'packet':packet,'snapshot':frozen,'initial_product_facts':'synthetic preview fixture; downstream Handler/store/prepare/freeze actual'},indent=2),encoding='utf-8')
    finally:httpd.shutdown();httpd.server_close();thread.join(5)
