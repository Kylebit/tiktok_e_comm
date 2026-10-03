from copy import deepcopy
import json
import pytest
from modules.products import server
from shared_platform import release_store
from shared_platform.round1_category_evidence import CategoryEvidenceError
from test_round1_category_ui_backend import prepared,get
from test_round1_category_observer_http import request
from test_round1_category_observations import prepare_module
COUNTS={}

def initial(tmp_path,monkeypatch):
    body,preview,fake,store,_=prepared(tmp_path,monkeypatch)
    body.pop('category_id');body.pop('selected_attributes')
    body['schema_version']='round1-category-options-request/v1'
    body['request_id']='options1'
    return body,preview,fake,store

def options(body):
    status,result=request('options',body)
    assert status==200 and result['status']=='SUCCEEDED',result
    assert result['options']['selected_category_identity'] is None
    assert result['options']['selected_attributes']==[]
    return result

def choice(body,result,index=0):
    projection=result['options'];option=projection['options'][index];selections=[]
    for attr in option['attributes']:
        if attr['kind']=='TEXT':
            selections.append(dict(attribute_identity_digest=attr['attribute_identity_digest'],text_value='Explicit text'))
        elif attr['required']:
            values=attr['values'] if attr['kind']=='MULTI_SELECT' else attr['values'][:1]
            selections.append(dict(attribute_identity_digest=attr['attribute_identity_digest'],option_identity_digests=[v['option_identity_digest'] for v in values]))
    return dict(body,schema_version='round1-category-capture-request/v3',request_id='selection1',
        options_reference=projection['options_reference'],options_digest=projection['options_digest'],
        selected_category_identity=option['category_identity_digest'],attribute_selections=selections)

def test_empty_intent_options_route(tmp_path,monkeypatch):
    body,_,fake,store=initial(tmp_path,monkeypatch)
    original=fake.merchant_get
    def measured(path,params):
        progress=store.category_request_progress(body['request_id'])
        assert progress['attempted']==len(fake.calls)+1 and progress['completed']==len(fake.calls)
        assert progress['stage']=='STARTED:'+path
        return original(path,params)
    fake.merchant_get=measured
    assert 'category_id' not in body and 'selected_attributes' not in body
    result=options(body)
    assert len(fake.calls)==5
    assert result['progress']['purpose']=='OPTIONS'

def test_full_empty_intent_to_freeze_and_lost_response_recovery(tmp_path,monkeypatch):
    body,preview,fake,store=initial(tmp_path,monkeypatch)
    result=options(body)
    assert result['progress']['attempted']==result['progress']['completed']==5
    assert request('options',body)==(200,result) and len(fake.calls)==5
    assert get('capture-status',{'offer_id':body['offer_id'],'request_id':body['request_id']})[1]==result
    selected=choice(body,result)
    status,captured=request('capture',selected)
    assert status==200 and captured['status']=='SUCCEEDED',captured
    assert captured['progress']['purpose']=='CAPTURE'
    assert captured['progress']['attempted']==captured['progress']['completed']==2
    assert len(fake.calls)==7
    receipt=captured['receipt']
    assert receipt['selected_attributes'][0]['attribute_value_list'][0]['value_id']==0
    module=prepare_module()
    plan=dict(schema_version='first-review-image-plan/v1',status='PROPOSED',source_actions=[],generated_assets=[],summary=dict(translation_positions=[],localized_output_count=0,net_new_output_count=0,paid_generation_required=False))
    packet=module.prepare_offer(offer_id=body['offer_id'],requested_targets=body['requested_targets'],preview_builder=lambda _:preview,
        category_source_region='MY',category_observation=receipt['observer_reference'],category_account_digest=body['account_identity_digest'],image_execution_plan=plan)
    from shared_platform.publication_rounds import build_round1_snapshot
    snapshot=build_round1_snapshot(first_review=packet,state=dict(_revision=8,review={'selected_sites':[]},product_approval=dict(status='approved',approved_by='Kyle',approval_id='fixture',input_fingerprint='fixture')),approved_by='Kyle',report_directory=tmp_path)
    assert snapshot['fact_snapshot']['category_evidence_binding']['receipt']==receipt
    assert request('capture',selected)==(200,captured) and len(fake.calls)==7
    COUNTS['full_chain']={'options_gets':5,'capture_gets':2,'repeat_status_prepare_freeze_additional_gets':0}

def test_candidate_limit_has_one_get_and_no_options(tmp_path,monkeypatch):
    body,_,fake,_=initial(tmp_path,monkeypatch);original=fake.merchant_get
    def large(path,params):
        raw=original(path,params)
        if path.endswith('category_recommend'):raw['response']['category_id_list']=list(range(1,12))
        return raw
    fake.merchant_get=large
    status,result=request('options',body)
    assert status==200 and result['status']=='FAILED' and result['code']=='CATEGORY_CANDIDATE_TECHNICAL_LIMIT'
    assert result['progress']['attempted']==result['progress']['completed']==1 and len(fake.calls)==1
    assert result['options_reference'] is None

def test_partial_get_failure_and_restart_no_replay(tmp_path,monkeypatch):
    body,_,fake,store=initial(tmp_path,monkeypatch);original=fake.merchant_get
    def broken(path,params):
        if len(fake.calls)==2:raise TimeoutError('synthetic timeout')
        return original(path,params)
    fake.merchant_get=broken
    _,result=request('options',body)
    assert result['status'] in {'UNKNOWN','FAILED'}
    assert result['progress']['attempted']==3 and result['progress']['completed']==2
    monkeypatch.setattr(server,'_ROUND1_CATEGORY_INSTANCE','restarted')
    assert request('options',body)[1]['status']==result['status']
    assert len(fake.calls)==2
    COUNTS['partial_failure']={'attempted':3,'returned':2,'repeat_additional_gets':0}

def test_unknown_process_request_retains_purpose_and_no_get(tmp_path,monkeypatch):
    from shared_platform.round1_category_evidence import digest
    body,_,fake,store=initial(tmp_path,monkeypatch)
    store.begin_category_capture(dict(body,_ui_request_digest=digest(body)),'previous-instance',purpose='OPTIONS')
    result=request('options',body)[1]
    assert result['status']=='UNKNOWN' and result['progress']['purpose']=='OPTIONS' and not fake.calls

@pytest.mark.parametrize('case',['category','attribute','cross-candidate','region','account','input','tree','path'])
def test_selection_boundaries_and_recheck(tmp_path,monkeypatch,case):
    body,preview,fake,store=initial(tmp_path,monkeypatch);result=options(body);selected=choice(body,result)
    if case=='category':selected['selected_category_identity']='sha256:'+'0'*64
    elif case=='attribute':selected['attribute_selections'][0]['attribute_identity_digest']='sha256:'+'0'*64
    elif case=='cross-candidate':selected['attribute_selections'][0]['attribute_identity_digest']=result['options']['options'][1]['attributes'][0]['attribute_identity_digest']
    elif case=='region':selected['source_region']='PH'
    elif case=='account':selected['account_identity_digest']='sha256:'+'0'*64
    elif case=='input':preview['review']['title']='changed'
    elif case=='tree':
        fake.attribute_rows_by_category[101].append(dict(attribute_id=99,original_attribute_name='Optional',is_mandatory=False,input_type='SINGLE_SELECT',attribute_value_list=[dict(value_id=1,original_value_name='One')]))
    else:
        original=fake.merchant_get
        def changed(path,params):
            raw=original(path,params)
            if path.endswith('get_category'):raw['response']['category_list'][-1]['original_category_name']='Changed path'
            return raw
        fake.merchant_get=changed
    status,failed=request('capture',selected)
    assert status==409 or failed['status']=='FAILED'
    if case in {'tree','path'}:assert failed['code']=='RECHECK_REQUIRED' and failed['observer_reference'] is None

@pytest.mark.parametrize('case',['depth','entries','bytes'])
def test_response_technical_limits(case):
    from shared_platform.round1_category_observations import bounded_official_response
    value={}
    if case=='depth':
        for _ in range(34):value={'nested':value}
    elif case=='entries':value=[None]*50001
    else:value='x'*(4*1024*1024+1)
    with pytest.raises(CategoryEvidenceError,match='CATEGORY_RESPONSE_LIMIT'):bounded_official_response(value)

def test_options_cannot_freeze_as_complete_receipt(tmp_path,monkeypatch):
    body,_,_,store=initial(tmp_path,monkeypatch);result=options(body)
    record=store.category_options_record(result['options_reference'],body['offer_id'])
    from shared_platform.round1_category_evidence import validate_receipt
    with pytest.raises(CategoryEvidenceError):validate_receipt(record['review_input'],record,observation_resolver=lambda _:record)

def test_concurrent_options_same_id_one_producer(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    body,_,fake,_=initial(tmp_path,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:server._round1_category_request('options',deepcopy(body)),range(2)))
    assert all(status==200 for status,_ in results) and len(fake.calls)==5
    changed=dict(body,schema_version='round1-category-capture-request/v2',category_id=101,selected_attributes=[])
    assert request('capture',changed)[0]==409 and len(fake.calls)==5

def test_options_atomic_result_rollback(tmp_path,monkeypatch):
    body,_,fake,store=initial(tmp_path,monkeypatch)
    original=store.finish_category_capture
    def finish(*args,**kwargs):
        if kwargs.get('options') is not None:
            import sqlite3
            with sqlite3.connect(store.path) as connection:
                connection.execute("CREATE TRIGGER fixture_fail BEFORE UPDATE ON round1_category_capture_requests BEGIN SELECT RAISE(ABORT,'fixture atomic rollback'); END")
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'finish_category_capture',finish)
    status,result=request('options',body)
    assert status==409 and result['status']=='UNKNOWN'
    import sqlite3
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM round1_category_options').fetchone()[0]==0
    assert len(fake.calls)==5

def test_ten_candidates_supported_without_truncation(tmp_path,monkeypatch):
    body,_,fake,_=initial(tmp_path,monkeypatch);original=fake.merchant_get
    def ten(path,params):
        raw=original(path,params)
        if path.endswith('category_recommend'):raw['response']['category_id_list']=list(range(101,111))
        return raw
    fake.merchant_get=ten
    result=options(body)
    assert len(result['options']['options'])==10 and result['progress']['completed']==21

def test_response_limits_accept_exact_entry_and_byte_values():
    from shared_platform.round1_category_observations import bounded_official_response
    assert bounded_official_response([None]*49999)==[None]*49999
    value='x'*(4*1024*1024-2)
    assert bounded_official_response(value)==value

def test_legacy_request_row_without_progress_does_not_claim_zero(tmp_path,monkeypatch):
    body,_,_,store=initial(tmp_path,monkeypatch)
    from shared_platform.round1_category_evidence import digest
    store.begin_category_capture(dict(body,_ui_request_digest=digest(body)),server._ROUND1_CATEGORY_INSTANCE,purpose='OPTIONS')
    import sqlite3
    with sqlite3.connect(store.path) as connection:
        connection.execute('DELETE FROM round1_category_request_progress')
    assert store.category_request_progress(body['request_id'])['attempted'] is None

def test_actual_loopback_options_without_intent(tmp_path,monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request,build_opener,ProxyHandler
    body,_,fake,_=initial(tmp_path,monkeypatch)
    http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        req=Request(f'http://127.0.0.1:{http.server_port}/api/product-workspace/round1-category/options',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with build_opener(ProxyHandler({})).open(req,timeout=5) as response:
            result=json.loads(response.read())
            assert result['status']=='SUCCEEDED' and result['options']['selected_category_identity'] is None
        assert len(fake.calls)==5
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5)
