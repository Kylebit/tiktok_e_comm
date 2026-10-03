"""Synthetic UI fixture with the real candidate HTTP service and durable store."""
import json
import os
from copy import deepcopy
from pathlib import Path
import subprocess
import pytest
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
from modules.products import server
from test_publication_r2_review import registered
from test_publication_r2_candidate import candidate
from test_publication_takeover import packet
from test_release_ux_contract import _browser_runtime
from dense_workspace_preview import dashboard as fixture_dashboard


@pytest.mark.parametrize('final_ready',[False,True,'successor','blocked-final',
                                          'blocked-common','blocked-common-approved'])
def test_original_review_layout_and_current_save_contract(registered,monkeypatch,tmp_path,final_ready):
    from shared_platform.publication_r2_review import review_view
    runtime,_=registered
    monkeypatch.setattr(server,'ROOT',runtime)
    monkeypatch.setattr(server,'WEB_DIR',Path(__file__).resolve().parents[1]/'web')
    class Handler(server.Handler):
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/api/product-workspace/publication-stages':
                manifest={'copy_sets':[{'language':'en','target_labels':['shopee:MY'],'title':'FINAL FROZEN TITLE','description':'Final frozen description'}],
                    'variants':[{'model_sku':'0001','specification':{'Style':'Tile 1pcs'},'parcel':{'weight_kg':.2,'package_cm':[10,20,3]}}],
                    'targets':[{'target_label':'shopee:MY','locale':'ms-MY','category':{'id':'42','name':'Wall decals','path':'Home > Wall decals'},'prices':[{'model_sku':'0001','amount':20,'currency':'MYR'}],'inventory':{'quantity':10,'note':'fixture'}}],
                    'image_sets':[{'image_set_id':'fixture-images','target_labels':['shopee:MY'],'images':[{'position':1,'role':'cover_scene','url':review_view('123',runtime_root=runtime)['images'][0]['local_url']}]}]}
                candidate={'candidate_digest':'fixture-final','target_labels':['shopee:MY'],'review_manifest':manifest,'write_budget':{'SHOPEE':{'target_labels':['shopee:MY'],'maximum_confirmed_writes':1}},'incident_safeguards':[{'incident_id':'fixture_guard','invariant':'fixture_only'}]}
                market={'status':'PREVIEW_READY' if final_ready else 'NOT_READY',**({'preview':candidate} if final_ready else {})}
                common={'status':'VERIFIED' if final_ready=='successor' else 'NOT_READY'}
                if final_ready=='blocked-final':
                    candidate.update(status='PREVIEW_READY',approval_status='NOT_APPROVED')
                    market.update(status='APPROVAL_REQUIRED',plan={'plan_id':'fixture-final','status':'PENDING_APPROVAL',
                        'confirmation_token':'fixture-token','targets':['shopee:MY'],'payload':{}},
                        final_review_available=False,
                        final_review_admission={'status':'BLOCKED','final_review_available':False,
                            'blockers':['COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN']})
                    common['status']='VERIFIED'
                if final_ready in {'blocked-common','blocked-common-approved'}:
                    market={'status':'NOT_FROZEN'}
                    approved=final_ready=='blocked-common-approved'
                    common={'status':'READY_TO_SYNC' if approved else 'APPROVAL_REQUIRED',
                        'plan':{'plan_id':'fixture-common',
                        'status':'APPROVED' if approved else 'PENDING_APPROVAL',
                        'confirmation_token':'fixture-token',
                        'targets':['miaoshou:COMMON'],'payload':{}}}
                if final_ready=='successor':
                    labels=[f'tiktok:T{i}' for i in range(10)]+['shopee:PH','shopee:MY','shopee:TH','shopee:VN','ozon:RU']
                    successor=deepcopy(candidate);successor.update(status='NOT_APPROVED',candidate_digest='fixture-successor',predecessor_candidate_digest='fixture-final',target_labels=labels,changed_target_labels=['shopee:MY','shopee:TH','shopee:VN'],durable_quality_evidence={'status':'PASSED'},review_blocked=False)
                    successor['review_manifest']=deepcopy(manifest)
                    successor['review_manifest']['copy_sets']=[{'language':'ms-MY','target_labels':['shopee:MY'],'title':'FINAL FROZEN TITLE','description':'Huraian tempatan baharu'}]
                    successor['review_manifest']['targets']=[{'target_label':label,'locale':'ms-MY' if label=='shopee:MY' else 'frozen','category':{'id':'42'},'prices':[{'model_sku':'0001','amount':20,'currency':'MYR'}]} for label in labels]
                    market.update(status='READY_TO_PUBLISH',candidate=candidate,successor_preview=successor)
                return self._json(200,{'ok':True,'schema_version':'publication-stages/v1','offer_id':'123','common':common,'marketplace':market})
            if path=='/api/product-workspace/dashboard':
                if parse_qs(urlparse(self.path).query).get('offer_id',[''])[0]!='123':return self._json(404,{'ok':False,'error':'该商品资料未连接'})
                payload=fixture_dashboard('99001',publication_targets=['tiktok:LH_MY','shopee:MY'])
                payload['product'].update(offer_id='123',source_offer_id='123',seller_sku_candidate='0001')
                payload['product']['actual_product_approved']=True
                frozen={'offer_id':'123','approved_revision':7,
                    'shared_review_facts':{'variants':[{'sku_id':'0001','specification_value':'Tile 1pcs','weight_kg':.2,'package_cm':[10,20,3]}]},
                    'platform_categories':[{'platform':'shopee','category_id':'42','category_zh':'家居 > 墙贴','category_en':'Home > Decals'}],
                    'copy_review_sets':[{'id':'lh','label':'LivelyHive','title_en':'Frozen English','title_zh':'冻结中文','description_en':'Original copy','description_zh':'原描述','specification_values':['Tile 1pcs']}],
                    'targets':[{'target':'shopee:MY','price':{'amount':20,'currency':'MYR','calculation':{'formula_zh':'成本除以利润系数','inputs':{'cost':5},'result':{'price':20}},'sku_prices':[{'display_name':'Tile 1pcs','amount':20,'currency':'MYR'}]}}],
                    'audit_log':{'revision':7,'evidence_sources':[{'source':'Synthetic original source','summary':'Fixture evidence'}],'decisions':[{'topic':'images','summary':'Exact image plan','reason':'Frozen source'}],'operations':[{'sequence':1,'event':'PREPARED','status':'COMPLETED','summary':'Synthetic operation','external_write_count':0}]}}
                payload.update(frozen_first_review=frozen,frozen_master_images=[],frozen_review_projection=True,r2_candidate_review=review_view('123',runtime_root=runtime),
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
        script=Path(__file__).parent/'browser/publication_original_review.cjs'
        result=subprocess.run([str(node),str(script),f'http://127.0.0.1:{httpd.server_port}',str(tmp_path),str(final_ready)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
        assert result.returncode==0,result.stdout+result.stderr
        facts=json.loads(result.stdout)
        assert facts['errors']==[] and facts['posts']==2
        value=review_view('123',runtime_root=runtime)
        assert value['revision']==1 and value['keep_count']==1 and not value['publication_authorized']
    finally:
        httpd.shutdown();httpd.server_close();thread.join(5)
