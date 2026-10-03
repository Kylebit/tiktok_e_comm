import io
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from modules.products import server
from test_r3_closure_service import synthetic_closure_context, declare_private_closure_observation
from test_r3_status_projection import approved_context,seed_historical_report

PREFIX='/api/product-workspace/publication-closure'


def request(action,body=None,*,method=None,headers=None,raw_body=None,extra_headers=()):
    data=raw_body if raw_body is not None else b'' if body is None else json.dumps(body).encode()
    method=method or ('GET' if body is None and raw_body is None else 'POST')
    fields={'Host':'127.0.0.1:8765','Content-Type':'application/json','Content-Length':str(len(data)),'Connection':'close'}
    fields.update(headers or {})
    raw=(f'{method} {PREFIX}/{action} HTTP/1.1\r\n'+''.join(f'{k}: {v}\r\n' for k,v in [*fields.items(),*extra_headers])+'\r\n').encode()+data
    class Connection:
        def __init__(self):self.output=io.BytesIO()
        def makefile(self,*args,**kwargs):return io.BytesIO(raw)
        def sendall(self,value):self.output.write(value)
    connection=Connection()
    server.Handler(connection,('127.0.0.1',50001),SimpleNamespace(server_address=('127.0.0.1',8765)))
    head,response=connection.output.getvalue().split(b'\r\n\r\n',1)
    try:payload=json.loads(response)
    except ValueError:payload={'raw':response.decode()}
    return int(head.split(b' ')[1]),payload


def test_handler_exposes_bound_readonly_prepare(tmp_path,monkeypatch):
    _,_,io_calls,market,_,_,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    status,result=request('prepare',inputs)
    assert status==200,result
    assert result['status']=='READY_TO_RECORD'
    assert [row['target_label'] for row in result['target_results']]==market['targets']
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert io_calls.mutations==1


def latest(data,**kwargs):
    return request('latest?'+urlencode({key:data[key] for key in ('offer_id','plan_id')}),**kwargs)


def test_handler_record_idempotent_and_shared_report_root_readable(tmp_path,monkeypatch):
    _,data,io_calls,_,_,service,inputs=synthetic_closure_context(tmp_path,monkeypatch,'PROCESSING')
    status,empty=latest(data)
    assert status==200 and empty['status']=='NOT_RECORDED'
    _,prepared=request('prepare',inputs)
    body={**inputs,'input_digest':prepared['input_digest']}
    status,recorded=request('record',body)
    assert status==200 and recorded['status']=='RECORDED'
    assert recorded['closure']['closure_status']=='CLOSED_WITH_OPEN_ITEMS'
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    status,repeated=request('record',body)
    assert status==200 and repeated==recorded
    status,stored=latest(data)
    assert status==200 and stored['closure']==recorded['closure']
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert len(service.report_store.list_reports(offer_id=data['offer_id']))==2
    assert service.report_store.get_report_by_run(run_id='first')['status']=='PUBLISHED'
    assert len(list(service.report_store.reports_root.rglob('closure-report.json')))==1
    assert io_calls.mutations==1


@pytest.mark.parametrize('change',['new_failure','unknown','missing_report','input','bad_history'])
def test_http_stale_or_corrupt_evidence_never_closes_or_falls_back(tmp_path,monkeypatch,change):
    store,data,_,market,snapshot,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    _,prepared=request('prepare',inputs);body={**inputs,'input_digest':prepared['input_digest']}
    if change=='bad_history':
        assert request('record',body)[0]==200
        folder=service.report_store.reports_root/data['offer_id']/str(snapshot['product_revision'])/'closure-bad'
        folder.mkdir();(folder/'closure-report.json').write_text('{broken')
        status,result=latest(data)
        assert status==409 and result['code']=='CLOSURE_EVIDENCE_CONFLICT'
        assert 'closure' not in result
        return
    if change in {'new_failure','unknown'}:
        seed_historical_report(store,data,market,snapshot,run_id='later',state='FAILED',unknown=change=='unknown')
    elif change=='missing_report':
        report=service.report_store.get_report_by_run(run_id='first')
        (service.report_store.reports_root/report['report_path']).unlink()
    else:body['recorded_by']='Different reviewer'
    status,result=request('record',body)
    assert status==409 and not result['ok']
    assert not list(service.report_store.reports_root.rglob('closure-report.json'))


def test_http_not_run_returns_full_scope_and_no_closure(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    _,data,_,market,_=approved_context(tmp_path,monkeypatch)
    body={**data,'recorded_by':'Synthetic reviewer','recorded_at':datetime.now(timezone.utc).isoformat()}
    body={key:body[key] for key in ('offer_id','plan_id','recorded_by','recorded_at')}
    status,result=request('prepare',body)
    assert status==409 and result['status']=='BLOCKED' and result['closure'] is None
    assert [row['target_label'] for row in result['target_results']]==market['targets']
    assert result['blockers'] and result['writes_performed']==[]
    assert 'business_complete' not in result


@pytest.mark.parametrize('field',['root','authority_root','policy_path','candidate','closure','source_reports','success_count'])
def test_http_rejects_client_authority_overrides(tmp_path,monkeypatch,field):
    _,_,_,_,_,_,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    status,result=request('prepare',{**inputs,field:'untrusted'})
    assert status==400 and result['code']=='INVALID_CLOSURE_REQUEST'


@pytest.mark.parametrize('case',['missing','foreign_offer','foreign_plan','unknown_target','huge_offer','unicode_digit'])
def test_http_identity_and_required_input_boundaries(tmp_path,monkeypatch,case):
    _,_,_,_,_,_,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    if case=='missing':inputs.pop('plan_id')
    elif case=='foreign_offer':inputs['offer_id']='999999999'
    elif case=='foreign_plan':inputs['plan_id']='foreign-plan'
    elif case=='huge_offer':inputs['offer_id']='9'*5000
    elif case=='unicode_digit':inputs['offer_id']='²'
    else:inputs['manual_handoffs']={'tiktok:foreign':{'accepted_by':'Owner','accepted_at':inputs['recorded_at'],'note':'Foreign'}}
    status,_=request('prepare',inputs)
    assert status=={'missing':400,'foreign_offer':409,'foreign_plan':404,'unknown_target':409,'huge_offer':400,'unicode_digit':400}[case]


@pytest.mark.parametrize('host',['127.0.0.1:8765','localhost:8765','[::1]:8765'])
def test_bound_loopback_hosts_are_accepted(tmp_path,monkeypatch,host):
    _,data,_,_,_,_,_=synthetic_closure_context(tmp_path,monkeypatch)
    assert latest(data,headers={'Host':host})[0]==200


@pytest.mark.parametrize('host',['localhost.evil:8765','127.0.0.1:80','evil:8765','localhost:8765,evil:8765','[::1]:8766',''])
def test_forged_hosts_are_rejected_without_storage(host):
    assert request('prepare',{},headers={'Host':host})[0]==403


@pytest.mark.parametrize('action',['prepare','record'])
def test_origin_guard_applies_to_both_post_actions(action):
    assert request(action,{},headers={'Origin':'https://evil.example'})[0]==403


@pytest.mark.parametrize('headers,raw,expected',[
    ({'Content-Type':'text/plain'},b'{}',415),
    ({'Content-Length':'-1'},b'{}',400),
    ({'Content-Length':'bad'},b'{}',400),
    ({'Content-Length':str(server.PRODUCT_APPROVAL_BODY_LIMIT+1)},b'{}',413),
    ({'Content-Length':'30'},b'{}',400),
    ({},b'{bad',400),({},b'[]',400),({},b'\xff',400),
    ({},b'{"offer_id":"1","offer_id":"2"}',400),
    ({'Transfer-Encoding':'chunked'},b'{}',400),
])
def test_request_framing_before_business_reads(headers,raw,expected):
    assert request('record',raw_body=raw,headers=headers)[0]==expected


@pytest.mark.parametrize('extra,expected',[
    (('Host','localhost:8765'),403),(('Content-Length','2'),400),
    (('Origin','http://localhost:8765'),400),
])
def test_duplicate_headers_rejected(extra,expected):
    headers={'Origin':'http://localhost:8765'} if extra[0]=='Origin' else {}
    assert request('record',{},headers=headers,extra_headers=[extra])[0]==expected


@pytest.mark.parametrize('action,method,expected',[
    ('prepare','GET',405),('record','GET',405),('latest','POST',405),
    ('missing','GET',404),('missing','POST',404),('prepare?root=x','POST',400),
    ('latest?offer_id=1','GET',400),('latest?offer_id=1&plan_id=x&plan_id=y','GET',400),
    ('latest?offer_id=1&plan_id=x&root=y','GET',400),
])
def test_method_and_query_boundaries(action,method,expected):
    assert request(action,{} if method=='POST' else None,method=method)[0]==expected


def test_source_run_name_does_not_make_it_a_broken_closure(tmp_path,monkeypatch):
    store,data,_,market,snapshot,_,_=synthetic_closure_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot,run_id='closure-source',state='PUBLISHED')
    status,result=latest(data)
    assert status==200 and result['status']=='NOT_RECORDED',result


@pytest.mark.parametrize('case',['present_closure','corrupt_closure','forged_source','damaged_source'])
def test_shared_root_never_hides_closure_or_unverified_source(tmp_path,monkeypatch,case):
    from shared_platform import product_publication_closure as closure
    store,data,_,market,snapshot,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    receipt=seed_historical_report(store,data,market,snapshot,run_id='closure-source',state='PUBLISHED')
    folder=service.report_store.reports_root/receipt.stored.report_path
    folder=folder.parent
    if case=='present_closure':
        declare_private_closure_observation(service,receipt,fixture_status='PUBLISHED')
        document=service.prepare(**inputs)['closure']
        document['closure_id']='closure:source'
        document['closure_digest']=closure._sha256({k:v for k,v in document.items() if k!='closure_digest'})
        closure.store_publication_closure(document,root=service.report_store.reports_root)
        # Structural history recognizes the actual closure, even beside an
        # independently indexed source report. The bound HTTP reader rejects its
        # altered closure ID instead of silently treating the folder as source.
        assert closure.latest_publication_closure(data['offer_id'],plan_id=data['plan_id'],
            root=service.report_store.reports_root,source_report_store=service.report_store)==document
    elif case=='corrupt_closure':(folder/'closure-report.json').write_text('{bad')
    elif case=='damaged_source':(folder/'report.json').write_text('{bad')
    else:
        fake=folder.parent/'closure-fake';fake.mkdir()
        (fake/'report.json').write_bytes((folder/'report.json').read_bytes())
    status,result=latest(data)
    assert status==409 and 'closure' not in result


def test_http_manual_handoff_is_explicit_and_keeps_provider_failure(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    _,data,_,market,_,_,inputs=synthetic_closure_context(tmp_path,monkeypatch,'FAILED')
    handoff={'accepted_by':'Explicit synthetic reviewer','accepted_at':datetime.now(timezone.utc).isoformat(),
             'note':'Explicit target-bound disposition'}
    inputs['manual_handoffs']={market['targets'][1]:handoff}
    status,prepared=request('prepare',inputs,headers={'Origin':'http://localhost:8765'})
    assert status==200
    status,recorded=request('record',{**inputs,'input_digest':prepared['input_digest']},
                            headers={'Origin':'http://127.0.0.1:8765'})
    assert status==200
    row=recorded['closure']['targets'][1]
    assert row['source_status']=='FAILED' and row['manual_handoff']==handoff
    assert row['resolution']=='MANUAL_HANDOFF_ACCEPTED'
    assert recorded['closure']['summary']['verified_count']==1
    assert latest(data)[1]['closure']==recorded['closure']


def test_record_requires_digest_and_get_never_creates_local_closure(tmp_path,monkeypatch):
    _,data,_,_,_,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    assert request('record',inputs)[0]==400
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert latest(data)[1]['status']=='NOT_RECORDED'
    assert request('record',method='GET')[0]==405
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert not list(service.report_store.reports_root.rglob('closure-report.json'))


def test_missing_final_approval_keeps_full_target_blockers(tmp_path,monkeypatch):
    from shared_platform import publication_autopilot as authority
    _,_,_,market,_,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    authority.final_approval_receipt_path(market['candidate'],reports_root=service.authority_root).unlink()
    status,result=request('prepare',inputs)
    assert status==409
    assert [row['target_label'] for row in result['target_results']]==market['targets']
    assert result['blockers'] and result['closure'] is None
