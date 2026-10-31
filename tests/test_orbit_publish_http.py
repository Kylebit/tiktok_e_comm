"""Agent CLI talks to the actual Handler, task engine and read-only health.

Owned loopback server only; the original new-task worker is registered but not
started, so the test cannot generate images, refresh credentials or call shops.
"""
from argparse import Namespace
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,subprocess,threading
import pytest
from scripts import orbit_publish as entry
from test_round1_auto_freeze import public_settings


def test_source_bound_cli_actual_handler_health_task_and_stage_contract(tmp_path,monkeypatch):
    from modules.products import server
    from shared_platform.operations_runtime import RuntimeProfile,OperationsWorker,runtime_manifest
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.native_task_preparation import ExplicitNewTaskWorker,NativeR1FilesystemBoundary
    from shared_platform.runtime_identity import capture_runtime_identity
    from shared_platform import release_store
    from core import config
    root=entry.ROOT
    head=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],text=True,capture_output=True,check=True).stdout.strip()
    data=tmp_path/'task-data';data.mkdir()
    for name in ('inputs','out','reports','checkpoints'):(data/name).mkdir()
    store=release_store.ReleaseStore(data/'release.db')
    with store._connect() as db:store._ensure_schema(db)
    monkeypatch.setattr(release_store,'DEFAULT_RELEASE_STORE_PATH',store.path)
    profile=RuntimeProfile(root,data,'stable',head,runtime_manifest(root))
    engine=WorkbenchEngine(data/'tasks.db',{'code_version':head,'environment':'stable','manifest_digest':profile.manifest_digest})
    legacy=OperationsWorker(engine,profile,{})
    worker=ExplicitNewTaskWorker(engine,profile,NativeR1FilesystemBoundary(profile))
    operations=SimpleNamespace(profile=profile,engine=engine,worker=legacy,worker_enabled=False,new_task_worker=worker)
    monkeypatch.setattr(server,'ROOT',root)
    monkeypatch.setattr(server,'_NATIVE_FINAL_SERVICE',None)
    config.load_settings()
    monkeypatch.setattr(server,'RUNTIME_IDENTITY',capture_runtime_identity('orbit-hive-local-console',root=root,web_root=root/'web'))
    http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);http.operations_runtime=operations
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(http.server_port)
    body={'schema_version':'publication-execution-profile/v1','source_root':root.as_posix(),'expected_source_head':head,
        'settings_path':config.settings_path().resolve().as_posix(),'data_root':str(data),'inputs_root':str(data/'inputs'),
        'output_root':str(data/'out'),'reports_root':str(data/'reports'),'checkpoint_root':str(data/'checkpoints'),
        'release_store_path':str(store.path),'runtime':{'base_url':base,'expected_source_root':str(root),
            'expected_source_head':head,'workflow_mode':'native-sole-final'}}
    path=tmp_path/'entry-profile.json';path.write_text(json.dumps(body),encoding='utf-8')
    try:
        bound=entry.check_execution_profile(path)
        assert entry.verify_runtime(bound)['data_root']==str(data)
        assert not bound['source_dirty'],'Run this identity-bound HTTP test after committing the exact candidate source.'
        args=Namespace(profile=str(path),command='prepare',offer_id='9012345678',
            targets='miaoshou:COMMON,tiktok:LH_PH,shopee:PH,ozon:RU',source_key='owned-real-cli-http')
        result=entry.run(args)
        task=result['task'];assert task['scope']['shops']==sorted(args.targets.split(','))
        assert worker.owns(task['task_id']) and engine.explicit_new_post_task_ids()==(task['task_id'],)
        assert not worker.thread.is_alive() and not worker.inflight
        before=engine.read_task_snapshot(task['task_id'])
        result=entry.run(Namespace(profile=str(path),command='result',task_id=task['task_id']))
        assert result['task']['task']['task_id']==task['task_id']
        assert 'publication' in result and result['execution_authority'] is False
        assert engine.read_task_snapshot(task['task_id'])==before
        # Source/setting/data drift is rejected before a second task POST.
        wrong={**body,'expected_source_head':'0'*40};path.write_text(json.dumps(wrong),encoding='utf-8')
        with pytest.raises(entry.ExecutionPackageError,match='HEAD_CHANGED'):entry.run(args)
        assert len(engine.explicit_new_post_task_ids())==1
    finally:
        http.shutdown();http.server_close();thread.join(5);worker.close();legacy.close()


def test_original_http_body_is_durable_before_control_response_decoder(tmp_path):
    raw=b'{"accepted": true, broken json'
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(202);self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    bound={'runtime':{'base_url':'http://127.0.0.1:'+str(http.server_port)},'checkpoint_root':str(tmp_path),
        'profile_ref':{'path':str(tmp_path/'profile.json'),'sha256':'1'*64},'source_identity':{'head':'2'*40},'data_root':str(tmp_path)}
    try:
        with pytest.raises(ValueError):entry._mutation(bound,'/api/orbit/tasks',{'source_key':'same'},entry.request_json)
        wires=list((tmp_path/'agent-entry').glob('*.http-body.private.bin'))
        assert len(wires)==1 and wires[0].read_bytes()==raw
        metadata=json.loads(next((tmp_path/'agent-entry').glob('*.http-response.json')).read_bytes())
        assert metadata['http_status']==202 and metadata['body_sha256']==hashlib.sha256(raw).hexdigest()
        with pytest.raises(entry.ExecutionPackageError,match='UNKNOWN_READBACK'):
            entry._mutation(bound,'/api/orbit/tasks',{'source_key':'same'},entry.request_json)
    finally:http.shutdown();http.server_close();thread.join(5)
