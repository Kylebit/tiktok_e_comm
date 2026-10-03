"""Normal sidebar/history navigation with real registration and HTTP readers."""
import json
import os
from pathlib import Path
import subprocess
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
from modules.products import server
from test_publication_r2_review import registered
from test_publication_r2_candidate import candidate
from test_publication_takeover import packet
from test_release_ux_contract import _browser_runtime
from dense_workspace_preview import dashboard as fixture_dashboard


def test_normal_entry_clean_and_retained_queue(registered,monkeypatch,tmp_path):
    from shared_platform.publication_r2_review import review_view
    runtime,_=registered
    monkeypatch.setattr(server,'ROOT',runtime)
    monkeypatch.setattr(server,'WEB_DIR',Path(__file__).resolve().parents[1]/'web')
    before={str(p):p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
    class Handler(server.Handler):
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/api/product-workspace/dashboard':
                offer=parse_qs(urlparse(self.path).query).get('offer_id',[''])[0]
                if offer!='123':return self._json(404,{'ok':False,'error':'product not found'})
                payload=fixture_dashboard('99001',publication_targets=['tiktok:LH_MY','shopee:MY'])
                payload['product'].update(offer_id='123',source_offer_id='123',seller_sku_candidate='0001')
                payload.update(frozen_review_projection=True,r2_candidate_review=review_view('123',runtime_root=runtime),
                    first_review_image_plan={'status':'FROZEN_ROUND1','source_actions':[],'generated_assets':[],'summary':{}},
                    frozen_first_review={'offer_id':'123','approved_revision':7,'copy_review_sets':[{'label':'Fixture frozen copy','title_en':'Verified tile title','description_en':'Verified tile description'}],
                        'platform_categories':[{'platform':'shopee','category_id':'42','category_en':'Wall decals','authority':'fixture'}],
                        'targets':[{'target':'tiktok:LH_MY','copy':{'title':'Verified tile title','description':'Verified tile description','language':'ms-MY'}}]},
                    frozen_master_images=[],content={'approved':False,'image_count':0,'images':[],'blockers':[]})
                return self._json(200,server._product_workspace_view(payload))
            if path=='/api/product-flow/preview':
                return self._json(200,{'ok':True,'offer_id':'123','revision':7,'source':{'images':[]},'review':{'image_actions':[]},'read_only':True})
            if path.startswith('/api/') and path not in {'/api/product-workspace/history','/api/product-workspace/evidence'} and not path.startswith('/api/product-workspace/r2-candidate/'):
                return self._json(503,{'ok':False,'error':'Synthetic preview has no business data'})
            return super().do_GET()
        def do_POST(self):raise AssertionError('Navigation must not perform a POST')
        def log_message(self,*a):pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=httpd.serve_forever,daemon=True);thread.start()
    try:
        available=_browser_runtime();assert available,'Playwright runtime required'
        node,modules=available
        result=subprocess.run([str(node),str(Path(__file__).parent/'browser/workspace_normal_navigation.cjs'),
            f'http://127.0.0.1:{httpd.server_port}',str(tmp_path)],env=dict(os.environ,NODE_PATH=str(modules)),
            capture_output=True,text=True,encoding='utf-8',timeout=150)
        assert result.returncode==0,result.stdout+result.stderr
        facts=json.loads(result.stdout)
        assert facts['posts']==[] and facts['errors']==[] and facts['scenarios']==['clean','retained']
        assert before=={str(p):p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
    finally:httpd.shutdown();httpd.server_close();thread.join(5)
