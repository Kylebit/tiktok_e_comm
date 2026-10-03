"""Synthetic UI fixture with the real candidate HTTP service and durable store."""
import json
import os
from pathlib import Path
import subprocess
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
from modules.products import server
from test_publication_r2_review import registered
from test_publication_r2_candidate import candidate
from test_publication_takeover import packet
from test_release_ux_contract import _browser_runtime
from dense_workspace_preview import dashboard as fixture_dashboard


def test_candidate_choices_in_real_browser(registered,monkeypatch,tmp_path):
    from shared_platform.publication_r2_review import review_view
    runtime,_=registered
    monkeypatch.setattr(server,'ROOT',runtime)
    monkeypatch.setattr(server,'WEB_DIR',Path(__file__).resolve().parents[1]/'web')
    class Handler(server.Handler):
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/api/product-workspace/dashboard':
                payload=fixture_dashboard('99001',publication_targets=['tiktok:LH_MY','shopee:MY'])
                payload['product'].update(offer_id='123',source_offer_id='123',seller_sku_candidate='0001')
                payload.update(frozen_review_projection=True,r2_candidate_review=review_view('123',runtime_root=runtime),
                    first_review_image_plan={'status':'FROZEN_ROUND1','source_actions':[],'generated_assets':[],'summary':{}},
                    content={'approved':False,'image_count':0,'images':[],'blockers':[]})
                return self._json(200,server._product_workspace_view(payload))
            if path=='/api/product-flow/preview':
                return self._json(200,{'ok':True,'offer_id':'123','revision':7,'source':{'images':[]},'review':{'image_actions':[]},'read_only':True})
            if path.startswith('/api/') and not path.startswith('/api/product-workspace/r2-candidate/'):
                return self._json(503,{'ok':False,'error':'Synthetic preview has no business data'})
            return super().do_GET()
        def do_POST(self):
            assert urlparse(self.path).path=='/api/product-workspace/r2-candidate/decision'
            return super().do_POST()
        def log_message(self,*a):pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=httpd.serve_forever,daemon=True);thread.start()
    try:
        available=_browser_runtime();assert available,'Playwright runtime required'
        node,modules=available
        env=dict(os.environ,NODE_PATH=str(modules))
        script=Path(__file__).parent/'browser/r2_candidate_review.cjs'
        result=subprocess.run([str(node),str(script),f'http://127.0.0.1:{httpd.server_port}',str(tmp_path)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
        assert result.returncode==0,result.stdout+result.stderr
        facts=json.loads(result.stdout)
        assert facts['errors']==[] and facts['posts']==2
        value=review_view('123',runtime_root=runtime)
        assert value['revision']==1 and value['keep_count']==1 and not value['publication_authorized']
    finally:
        httpd.shutdown();httpd.server_close();thread.join(5)
