import io
import json
from types import SimpleNamespace
import pytest
from modules.products import server
from test_publication_r2_review import registered,view,request as decision_request
from test_publication_r2_candidate import candidate
from test_publication_takeover import packet


def request(path,body=None,headers=None,raw=None,extra=()):
    raw=raw if raw is not None else (json.dumps(body).encode() if body is not None else b'')
    method='POST' if body is not None or raw else 'GET'
    fields={'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765','Content-Type':'application/json','Content-Length':str(len(raw))}
    fields.update(headers or {})
    wire=(f'{method} {path} HTTP/1.1\r\n'+''.join(f'{k}: {v}\r\n' for k,v in [*fields.items(),*extra])+'\r\n').encode()+raw
    class Connection:
        def __init__(self):self.output=io.BytesIO()
        def makefile(self,*a,**k):return io.BytesIO(wire)
        def sendall(self,v):self.output.write(v)
    connection=Connection()
    server.Handler(connection,('127.0.0.1',50001),SimpleNamespace(server_address=('127.0.0.1',8765)))
    head,response=connection.output.getvalue().split(b'\r\n\r\n',1)
    return int(head.split()[1]), response


@pytest.fixture
def runtime(registered,monkeypatch):
    monkeypatch.setattr(server,'ROOT',registered[0])
    return registered[0]


def test_real_handler_read_image_save_and_idempotent_readback(runtime):
    before={p:p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
    status,raw=request('/api/product-workspace/r2-candidate/review?offer_id=123')
    assert status==200
    value=json.loads(raw)['review']
    status,image=request(value['images'][0]['local_url'])
    assert status==200 and image.startswith(b'\x89PNG')
    assert before=={p:p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
    body=decision_request(value)
    status,saved=request('/api/product-workspace/r2-candidate/decision',body)
    assert status==200 and json.loads(saved)['review']['keep_count']==1
    assert request('/api/product-workspace/r2-candidate/decision',body)==(status,saved)
    assert request('/api/product-workspace/r2-candidate/review?offer_id=123')==(status,saved)


@pytest.mark.parametrize('damage',['origin','host','type','duplicate','transfer','length','query','foreign'])
def test_bad_http_never_writes(runtime,damage):
    before={p:p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
    body=decision_request(view(runtime));headers={};extra=();raw=None
    path='/api/product-workspace/r2-candidate/decision'
    if damage=='origin':headers['Origin']='https://foreign.example'
    if damage=='host':headers['Host']='foreign.example:8765'
    if damage=='type':headers['Content-Type']='text/plain'
    if damage=='duplicate':raw=json.dumps(body).encode().replace(b'{',b'{"offer_id":"123",',1)
    if damage=='transfer':headers['Transfer-Encoding']='chunked'
    if damage=='length':extra=(('Content-Length','1'),)
    if damage=='query':path+='?offer_id=456'
    if damage=='foreign':body['offer_id']='456'
    assert request(path,body,headers,raw,extra)[0] in {400,403,409}
    assert before=={p:p.read_bytes() for p in runtime.rglob('*') if p.is_file()}


def test_legacy_write_cannot_bypass_registered_review(runtime):
    before={p:p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
    for path in ['/api/product-flow/content-package/review','/api/product-workspace/facts','/api/product-workspace/publish']:
        assert request(path,{'offer_id':'123'})[0]==409
    assert before=={p:p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
