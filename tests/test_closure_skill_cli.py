import importlib.util
import http.client
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import urllib.parse
import urllib.request

import pytest

from modules.products import server
from test_r3_closure_service import synthetic_closure_context

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'skills/publish-approved-product/scripts/close_product_publication.py'


def test_portable_cli_help_without_project_imports(tmp_path):
    result=subprocess.run([sys.executable,'-I',str(SCRIPT),'--help'],cwd=tmp_path,capture_output=True)
    assert result.returncode==0,result.stderr.decode()
    assert b'prepare' in result.stdout and b'record' in result.stdout


def test_detached_default_doctor_is_readonly_configuration_required(tmp_path):
    copied=tmp_path/'close.py';copied.write_bytes(SCRIPT.read_bytes())
    before=set(tmp_path.iterdir())
    result=subprocess.run([sys.executable,'-I',str(copied)],cwd=tmp_path,capture_output=True)
    assert result.returncode==2
    assert json.loads(result.stdout)['status']=='CONFIG_REQUIRED'
    assert set(tmp_path.iterdir())==before


@pytest.fixture
def cli(monkeypatch):
    spec=importlib.util.spec_from_file_location('canonical_closure_cli',SCRIPT)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    calls=[]
    def transport(_opener,req,timeout=None):
        parsed=urllib.parse.urlsplit(req.full_url);data=req.data or b''
        calls.append((req.get_method(),parsed.path))
        target=parsed.path+('?' + parsed.query if parsed.query else '')
        raw=(f'{req.get_method()} {target} HTTP/1.1\r\nHost: {parsed.netloc}\r\n'
             f'Content-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n').encode()+data
        class Connection:
            def __init__(self):self.output=io.BytesIO()
            def makefile(self,*_args,**_kwargs):return io.BytesIO(raw)
            def sendall(self,value):self.output.write(value)
        connection=Connection()
        server.Handler(connection,('127.0.0.1',50001),SimpleNamespace(server_address=('127.0.0.1',parsed.port)))
        head,body=connection.output.getvalue().split(b'\r\n\r\n',1)
        response=io.BytesIO(body);response.code=int(head.split(b' ')[1])
        return response
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',transport)
    return module,calls


def invoke(cli,capsys,*args,expected_root=None,base='http://127.0.0.1:8765'):
    module,_=cli
    code=module.main(['--base-url',base,'--expected-root',str(expected_root or ROOT),*args])
    return code,json.loads(capsys.readouterr().out)


def prepare_file(cli,capsys,tmp_path,inputs):
    path=tmp_path/'inputs.json';path.write_text(json.dumps(inputs),encoding='utf-8')
    code,result=invoke(cli,capsys,'prepare','--input',str(path))
    assert code==0,result
    prepared=tmp_path/'prepared.json';prepared.write_text(json.dumps(result),encoding='utf-8')
    return prepared,result


def test_actual_cli_handler_roundtrip_and_doctor(tmp_path,monkeypatch,cli,capsys):
    _,data,io_calls,market,_,reports,inputs=synthetic_closure_context(tmp_path,monkeypatch,'PROCESSING')
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    code,doctor=invoke(cli,capsys,'doctor')
    assert code==0 and doctor['status']=='RUNTIME_IDENTIFIED'
    assert doctor['runtime']['business_authority']=='NOT_CHECKED'
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    prepared,preview=prepare_file(cli,capsys,tmp_path,inputs)
    assert [r['target_label'] for r in preview['result']['target_results']]==market['targets']
    code,recorded=invoke(cli,capsys,'record','--prepared',str(prepared))
    assert code==0 and recorded['result']['closure']==preview['result']['closure']
    assert 'writes_performed' not in recorded
    assert recorded['result']['closure']['targets'][1]['source_status']=='PROCESSING'
    code,latest=invoke(cli,capsys,'latest','--offer-id',data['offer_id'],'--plan-id',data['plan_id'])
    assert code==0 and latest['result']['closure']==recorded['result']['closure']
    assert invoke(cli,capsys,'record','--prepared',str(prepared))[0]==0
    assert len(list(reports.report_store.reports_root.rglob('closure-report.json')))==1
    assert io_calls.mutations==1
    assert all(path=='/api/health' or path.startswith('/api/product-workspace/publication-closure/') for _,path in cli[1])


@pytest.mark.parametrize('case',['stale','missing_authority','unknown_scope'])
def test_cli_preserves_blockers_and_refuses_changed_evidence(tmp_path,monkeypatch,cli,capsys,case):
    from test_r3_status_projection import seed_historical_report
    from shared_platform import publication_autopilot as authority
    store,data,_,market,snapshot,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    prepared,_=prepare_file(cli,capsys,tmp_path,inputs)
    if case=='stale':seed_historical_report(store,data,market,snapshot,run_id='later',state='FAILED')
    elif case=='missing_authority':authority.final_approval_receipt_path(market['candidate'],reports_root=service.authority_root).unlink()
    else:
        edited=json.loads(prepared.read_text());edited['request']['manual_handoffs']={'tiktok:foreign':{'accepted_by':'Owner','accepted_at':inputs['recorded_at'],'note':'Foreign'}}
        prepared.write_text(json.dumps(edited))
    code,result=invoke(cli,capsys,'record','--prepared',str(prepared))
    assert code==2 and result['http_status']==409
    assert result['result'].get('target_results')
    assert not list(service.report_store.reports_root.rglob('closure-report.json'))


@pytest.mark.parametrize('base',['https://127.0.0.1:8765','http://user@localhost:8765','http://localhost.evil:8765',
    'http://127.0.0.1:8765/path','http://127.0.0.1:8765?root=x','http://127.0.0.1:8765#x',
    'http://2130706433:8765','http://127.0.0.1','http://localhost:8765,evil','http://[::1]:8765/'])
def test_cli_rejects_ambiguous_or_remote_service_before_transport(cli,capsys,base):
    code,result=invoke(cli,capsys,'doctor',base=base)
    assert code==2 and result['code']=='EXPLICIT_LOOPBACK_BASE_URL_REQUIRED'
    assert cli[1]==[]


def test_wrong_runtime_root_stops_before_prepare(tmp_path,monkeypatch,cli,capsys):
    synthetic_closure_context(tmp_path,monkeypatch)
    code,result=invoke(cli,capsys,'doctor',expected_root=tmp_path)
    assert code==2 and result['code']=='RUNTIME_ROOT_MISMATCH'
    assert cli[1]==[('GET','/api/health')]


def test_missing_runtime_dependency_is_doctor_state(tmp_path,monkeypatch,cli,capsys):
    synthetic_closure_context(tmp_path,monkeypatch)
    original=importlib.util.find_spec
    monkeypatch.setattr(importlib.util,'find_spec',lambda name,*a,**k:None if name=='PIL' else original(name,*a,**k))
    code,result=invoke(cli,capsys,'doctor')
    assert code==2 and result['status']=='DEPENDENCY_UNAVAILABLE'
    assert result['runtime']['missing_dependencies']==['PIL']


@pytest.mark.parametrize('failure',[TimeoutError,http.client.IncompleteRead])
def test_record_disconnect_never_reposts_and_keeps_expected_identity(tmp_path,monkeypatch,cli,capsys,failure):
    _,_,_,_,_,_,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    prepared,preview=prepare_file(cli,capsys,tmp_path,inputs)
    original=urllib.request.OpenerDirector.open;attempts=[]
    def disconnect(opener,req,timeout=None):
        if req.full_url.endswith('/record'):
            attempts.append(req.full_url)
            original(opener,req,timeout=timeout)
            raise failure(b'Synthetic response lost after local record')
        return original(opener,req,timeout=timeout)
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',disconnect)
    code,result=invoke(cli,capsys,'record','--prepared',str(prepared))
    assert code==2 and result['status']=='RECORD_OUTCOME_UNKNOWN' and len(attempts)==1
    assert result['expected_closure_id']==preview['result']['closure']['closure_id']
    assert result['expected_closure_digest']==preview['result']['closure']['closure_digest']
    assert result['automatic_retry'] is False


def test_redirect_and_response_size_limits(cli):
    module,_=cli
    with pytest.raises(ValueError,match='REDIRECT_REJECTED'):
        module.NoRedirect().redirect_request(None,None,302,'',{},'http://evil.example')


def test_response_limit_is_enforced(cli,monkeypatch):
    module,_=cli
    def oversized(*args,**kwargs):
        response=io.BytesIO(b'x'*(module.LIMIT+1));response.code=200;return response
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',oversized)
    with pytest.raises(ValueError,match='RESPONSE_TOO_LARGE'):
        module.request('http://127.0.0.1:8765','/api/health')


@pytest.mark.parametrize('damage',['request_list','result_list'])
def test_malformed_preparation_returns_json_error(tmp_path,monkeypatch,cli,capsys,damage):
    _,_,_,_,_,_,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    prepared,result=prepare_file(cli,capsys,tmp_path,inputs)
    result['request' if damage=='request_list' else 'result']=[]
    prepared.write_text(json.dumps(result))
    code,error=invoke(cli,capsys,'record','--prepared',str(prepared))
    assert code==2 and error['status']=='BLOCKED'


def test_malformed_record_response_is_unknown_after_single_post(tmp_path,monkeypatch,cli,capsys):
    _,_,_,_,_,_,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    prepared,_=prepare_file(cli,capsys,tmp_path,inputs)
    original=urllib.request.OpenerDirector.open
    def malformed(opener,req,timeout=None):
        response=original(opener,req,timeout=timeout)
        if req.full_url.endswith('/record'):
            body=json.loads(response.read());body['status']=[]
            response=io.BytesIO(json.dumps(body).encode());response.code=200
        return response
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',malformed)
    code,result=invoke(cli,capsys,'record','--prepared',str(prepared))
    assert code==2 and result['status']=='RECORD_OUTCOME_UNKNOWN'
    assert 'writes_performed' not in result


def test_prepared_identity_tampering_stops_before_record_post(tmp_path,monkeypatch,cli,capsys):
    _,_,_,_,_,_,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    prepared,_=prepare_file(cli,capsys,tmp_path,inputs)
    document=json.loads(prepared.read_text(encoding='utf-8'))
    document['request']['offer_id']='9000053'
    prepared.write_text(json.dumps(document),encoding='utf-8')
    before=list(cli[1])
    code,result=invoke(cli,capsys,'record','--prepared',str(prepared))
    assert code==2 and result['code']=='PREPARED_IDENTITY_MISMATCH'
    assert cli[1]==before+[('GET','/api/health')]
