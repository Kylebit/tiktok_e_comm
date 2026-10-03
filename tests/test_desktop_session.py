"""Desktop boundaries: real loopback HTTP and generated non-secret profiles."""
from copy import deepcopy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from threading import Thread

import pytest

from desktop.session import DesktopSelection, DesktopSession, external_destination
from desktop.startup import create_profile, parser
from scripts import product_publication_runtime as runtime
from shared_platform.runtime_identity import expected_identity

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def selection(tmp_path):
    profile = tmp_path / 'runtime-profile.json'
    profile.write_text(json.dumps({'profile_id':'D00-synthetic', 'settings_path':str(tmp_path/'settings.json'),
        'stores':{name:str(tmp_path/name) for name in ('catalog','reports_release','workbench','ozon')}}))
    return DesktopSelection(ROOT, profile, None)


@pytest.fixture
def service(selection):
    state = {'payload': {'ok':False,'service':'unrelated'}, 'requests':[],'status':200}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state['requests'].append((self.command,self.path))
            data=json.dumps(state['payload']).encode()
            self.send_response(state['status']); self.send_header('Content-Length',str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def log_message(self,*args): pass
    class Server(ThreadingHTTPServer):
        def server_bind(self):
            TCPServer.server_bind(self); self.server_name='fixture'; self.server_port=self.server_address[1]
    server=Server(('127.0.0.1',0),Handler)
    Thread(target=server.serve_forever,daemon=True).start()
    session=DesktopSession(DesktopSelection(ROOT,selection.profile_path,None,server.server_port), selection.profile_path.parent/'client')
    yield state, session
    server.shutdown(); server.server_close()


def identity(session):
    expected=expected_identity(ROOT,'orbit-hive-local-console',profile_path=session.selection.profile_path)
    return {**deepcopy(expected),'ok':True,'configuration_initialized':True,'configuration_source_known':True,
        'configuration_source_matches':True,'stores':expected['profile']['stores'],'missing_dependencies':[]}


def test_foreign_json_is_not_online(service):
    state,session=service
    assert session.check()['state']=='WRONG_SERVICE'
    assert session.start()['state']=='WRONG_SERVICE'
    assert all(method=='GET' and path=='/api/health' for method,path in state['requests'])


def test_bound_port_without_health_is_not_startable(service):
    state,session=service; state['status']=503
    assert session.start()['state']=='PORT_IN_USE'


@pytest.mark.parametrize('change,expected',[
    ({},'READY'),({'commit':'other-commit'},'SOURCE_MISMATCH'),
    ({'code_root':'other-tree'},'SOURCE_MISMATCH'),
    ({'configuration_source_known':False},'UNKNOWN'),
    ({'configuration_source_matches':False},'CONFIG_MISMATCH'),
    ({'stores':{}},'DATA_PROFILE_MISMATCH'),
    ({'missing_dependencies':['requests']},'DEPENDENCY_UNAVAILABLE'),
])
def test_actual_identity_controls_connection(service,change,expected):
    state,session=service; state['payload']={**identity(session),**change}
    assert session.check()['state']==expected
    assert session.allows_document(session.selection.origin+'/product-workspace') is (expected=='READY')


def test_ready_service_does_not_need_python_or_launch(service,monkeypatch):
    state,session=service; state['payload']=identity(session)
    monkeypatch.setattr(runtime,'start_runtime',lambda **kw:pytest.fail('READY service was relaunched'))
    assert session.selection.python is None
    assert session.start()['state']=='READY'
    state['payload']['commit']='changed'
    assert not session.verify_document(session.selection.origin+'/product-workspace')
    assert session.last_result['state']=='SOURCE_MISMATCH'
    assert not session.allows_document(session.selection.url)
    assert session.close()=={'background_services_stopped':False,'business_requests_sent':0}


def test_unknown_selection_never_probes(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'probe_service',lambda *a:pytest.fail('unknown project contacted a port'))
    session=DesktopSession(DesktopSelection(None,None,None),tmp_path)
    assert session.check()['state']=='PROJECT_REQUIRED'
    assert not list(tmp_path.iterdir())


def test_stopped_requires_explicit_python(selection,tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'probe_service',lambda spec:{'state':'STOPPED'})
    session=DesktopSession(selection,tmp_path)
    assert session.start()['state']=='PYTHON_REQUIRED'
    assert not (tmp_path/'service-logs').exists()


@pytest.mark.parametrize('page',['https://example.com','//example.com','/\\other','file:///x'])
def test_external_page_argument_rejected(page):
    with pytest.raises(ValueError): DesktopSelection(None,None,None,page=page)


@pytest.mark.parametrize('url',['http://example.com','https://127.0.0.1/x','https://localhost','file:///x','javascript:alert(1)','https://u:p@example.com'])
def test_unsafe_external_destination_rejected(url):
    assert not external_destination(url)


def test_public_https_link_has_no_local_service_authority():
    assert external_destination('https://example.com/help')


def test_profile_generation_is_exclusive_and_never_opens_stores(tmp_path):
    path=tmp_path/'runtime-profile.json'
    args=parser().parse_args(['--create-profile','--profile',str(path),'--profile-id','generated',
        '--settings-path',str(tmp_path/'settings.json'),'--catalog-store',str(tmp_path/'catalog.db'),
        '--report-store',str(tmp_path/'report.db'),'--workbench-store',str(tmp_path/'workbench.db'),
        '--ozon-dir',str(tmp_path/'ozon')])
    assert create_profile(args)['configuration_read'] is False
    assert list(tmp_path.iterdir())==[path]
    with pytest.raises(FileExistsError): create_profile(args)


def test_sensitive_filename_not_read_as_profile(selection,tmp_path,monkeypatch):
    sensitive=tmp_path/'settings.json'; sensitive.write_text('synthetic canary only')
    original=Path.read_text
    def read(path,*a,**kw):
        if path==sensitive: pytest.fail('selected settings file was read')
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'read_text',read)
    session=DesktopSession(DesktopSelection(ROOT,sensitive,None),tmp_path)
    assert session.check()['state']=='PROFILE_INVALID'


def test_port_override_keeps_other_services_unchanged():
    specs=runtime.service_specs(root=ROOT,product_port=49199,include_rus=True)
    assert [s.port for s in specs]==[49199,8766,8767]
    assert specs[0].command[specs[0].command.index('--port')+1]=='49199'
