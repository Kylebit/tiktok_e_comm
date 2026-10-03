"""Actual Handler and temporary service store; no real credentials/provider."""
import io
import json
from types import SimpleNamespace
from modules.products import server
import pytest
from test_round1_category_observations import context


def request(action, body, *, headers=None, raw_body=None, extra_headers=(), peer='127.0.0.1'):
    data=json.dumps(body).encode() if raw_body is None else raw_body
    fields={'Host':'127.0.0.1:8765','Content-Type':'application/json','Content-Length':str(len(data)),'Connection':'close'}
    fields.update(headers or {})
    raw=(f'POST /api/product-workspace/round1-category/{action} HTTP/1.1\r\n'
         + ''.join(f'{k}: {v}\r\n' for k,v in [*fields.items(),*extra_headers])+'\r\n').encode()+data
    class Connection:
        def __init__(self):self.output=io.BytesIO()
        def makefile(self,*args,**kwargs):return io.BytesIO(raw)
        def sendall(self,value):self.output.write(value)
    connection=Connection()
    server.Handler(connection,(peer,50001),SimpleNamespace(server_address=('127.0.0.1',8765)))
    head,response=connection.output.getvalue().split(b'\r\n\r\n',1)
    try:payload=json.loads(response)
    except ValueError:payload={'non_json_response':True}
    return int(head.split(b' ')[1]),payload


def test_capture_route_rejects_untrusted_observation_before_any_read():
    status,result=request('capture',{'observation': {'authority':'shopee_official_category_get'}})
    assert status==400
    assert result['code']=='INVALID_CATEGORY_CAPTURE_REQUEST'


def test_actual_handler_capture_then_readonly_resolve(tmp_path,monkeypatch):
    data,_,fake,_=context(tmp_path,monkeypatch)
    status,result=request('capture',data)
    assert status==200,result
    resolved={key:value for key,value in data.items() if key not in {'category_id','selected_attributes'}}
    resolved['observer_reference']=result['receipt']['observer_reference']
    status,readback=request('resolve',resolved)
    assert status==200 and readback['receipt']==result['receipt']
    assert len(fake.calls)==2


def test_controlled_actual_handler_port(tmp_path,monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request,build_opener,ProxyHandler
    data,_,fake,_=context(tmp_path,monkeypatch)
    http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        req=Request(f'http://127.0.0.1:{http.server_port}/api/product-workspace/round1-category/capture',
                    data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        with build_opener(ProxyHandler({})).open(req,timeout=5) as response:
            assert response.status==200
            assert json.loads(response.read())['status']=='CAPTURED'
        assert len(fake.calls)==2
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5)


@pytest.mark.parametrize('headers,raw,expected',[
    ({'Host':'evil:8765'},b'{}',403),({'Origin':'https://evil.example'},b'{}',403),
    ({'Content-Length':'-1'},b'{}',400),({'Content-Length':'30'},b'{}',400),
    ({'Transfer-Encoding':'chunked'},b'{}',400),({'Content-Type':'text/plain'},b'{}',415),
    ({},b'{"offer_id":"1","offer_id":"2"}',400),({},b'[]',400),({},b'{bad',400),
])
def test_handler_framing_rejected_before_service_reads(headers,raw,expected,monkeypatch):
    monkeypatch.setattr(server,'_round1_category_review',lambda _:pytest.fail('must reject before reading product'))
    assert request('capture',{},headers=headers,raw_body=raw)[0]==expected


@pytest.mark.parametrize('extra',[('Host','127.0.0.1:8765'),('Content-Length','2'),('Content-Type','application/json')])
def test_duplicate_headers_rejected(extra):
    assert request('capture',{},extra_headers=[extra])[0] in {400,403}


def test_remote_peer_and_query_are_rejected():
    assert request('capture',{},peer='192.0.2.1')[0]==403
    assert request('capture?root=foreign',{})[0]==400
