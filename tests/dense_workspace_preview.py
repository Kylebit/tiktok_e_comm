"""Generated-data HTTP preview. Real page/static/dashboard dispatch, no business I/O.

All provider/store boundaries are synthetic. Nothing here is a business fixture
to install or import from application code. Run only with an explicit audit out.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from threading import Event, Thread
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FIXTURES = {}


def dashboard(offer_id: str, publication_targets=None):
    if offer_id == '99005':
        Event().wait(.6)  # Delayed synthetic A, for stale-response browser checks.
    if offer_id not in {'99001', '99002', '99003', '99005'}:
        raise FileNotFoundError('隔离夹具中没有该商品；未读取真实档案')
    approved = offer_id in {'99002', '99003'}
    labels = list(publication_targets or ['tiktok:LH_MY', 'shopee:MY', 'ozon:RU'])
    return {
        'ok': True, 'fixture_only': True,
        'product': {
            'offer_id': offer_id, 'source_offer_id': offer_id,
            'seller_sku_candidate': offer_id if offer_id == '99003' else 'TEST-' + offer_id, 'revision': 7,
            'title': {'99001': '隔离夹具 · Botanical wall panel', '99002': '隔离夹具 · Soft linen set',
                      '99003': '隔离夹具 · Shelf organiser', '99005': '隔离夹具 · Delayed panel'}[offer_id],
            'category': {'name':'家居装饰'}, 'cost_cny': 0 if offer_id == '99002' else 8.5,
            'weight_kg': .2, 'package_cm': [30,20,2],
            'selected_sites': ['lh_my'], 'selected_sku_keys': ['small','large'],
            'sku_commercial_facts': {'small': {'cost_cny': 0 if offer_id == '99002' else 8.5, 'weight_kg':.2, 'package_cm':[30,20,2]},
                                     'large': {'cost_cny': None, 'weight_kg':.3, 'package_cm':[40,30,3]}},
            'source_skus': [{'key':'small','label':'30 × 20 cm','source_label':'30 × 20 cm','price_cny':8.5},
                            {'key':'large','label':'40 × 30 cm','source_label':'40 × 30 cm','price_cny':None}],
            'actual_product_approved': approved, 'fields_locked': approved,
            'fact_evidence': {'ready':True,'warnings':[],'blockers':[], 'selected_sku_prices':[], 'fields':{}},
            'seller_sku_governance': {'available':True,'suggested_sku_range':['TEST-small','TEST-large']},
        },
        'content': {'approved':approved, 'image_count':None if offer_id == '99002' else 2,
                    'images':[{'image_url':'https://fixture.invalid/good.png','asset_type':'source'},
                              {'image_url':'https://fixture.invalid/broken.png','asset_type':'source'}], 'blockers':[]},
        'approval_rehearsal': {'ready':False,'warnings':[],'blockers':['生成夹具不提供批准权限']},
        'publication_rehearsal': {'ready':False,'drafts':[]},
        'actual_release_gate': {'ready':False,'blockers':['目标结果待核对']},
        'publication_scope': {'available_targets':[
            {'label':'tiktok:LH_MY','channel':'tiktok','country':'MY','shop':'TEST MY'},
            {'label':'shopee:MY','channel':'shopee','country':'MY','shop':'TEST Shopee'},
            {'label':'ozon:RU','channel':'ozon','country':'RU','shop':'TEST Ozon'}],
            'selected_labels': labels, 'default_labels':labels},
        'pricing_review': {'store_prices':[], 'target_pricing':{
            'tiktok:LH_MY': {'status':'ready','store_prices':[{'shop':'TEST MY','region':'MY','target_key':'lh_my','list_price':25.9,'sale_after_discount':None,'currency':'MYR'}]},
            'shopee:MY': {'status':'awaiting_tiktok_readback','blocker':'等待官方回读'},
            'ozon:RU': {'status':'blocked','blocker':'尚无当前目标售价'}}, 'legacy_audit':{'sections':[]}},
        'release_v1_fixture': deepcopy(RELEASE_FIXTURES.get(offer_id)) or {'plan_approved':False,'eligible_for_plan_approval':False,
            'plan':{}, 'run': {'status':'PARTIAL','targets':[
                {'target_label':'tiktok:LH_MY','status':'SUCCEEDED','result':{'verification':'fixture_only'}},
                {'target_label':'shopee:MY','status':'RECONCILIATION_REQUIRED'},
                {'target_label':'ozon:RU','status':'PENDING'}]} if offer_id=='99003' else None},
    }


def stale_collectbox_projection():
    plan = RELEASE_FIXTURES['99003']['plan']
    def platform(name, target):
        row = {'platform':name,'targets':[{'target_label':target,'status':'PENDING'}],
               'target_outcomes':[],'status':'PENDING','outcome':None,'attempt_count':0,
               'retry_allowed':False,'receipt_digest':None,'platform_detail_id_digest':None,
               'external_writes':{'count':0,'classes':[]},'error':None,'publishable':False}
        if name == 'TIKTOK': row['publishable_targets'] = []
        return row
    return {'schema_version':'collectbox-action-status/v1','ok':True,'persisted':False,
        'approved_plan':{'plan_id':plan['plan_id'],'product_revision':7,
                         'payload_digest':plan['payload_digest'],'targets_digest':plan['targets_digest']},
        'action':{'action_id':None,'status':'READY','start_allowed':True,'retry_allowed':False,
                  'terminal':False,'error':None,'platforms':[platform('TIKTOK','tiktok:LH_MY'),
                                                           platform('SHOPEE','shopee:MY')]},
        'external_writes_performed':[],'external_write_count':0,
        'canonical_next_action':{'action':'start_collectbox_action','target_focus':None}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--stage-mode', choices=('unavailable', 'unavailable-identified', 'malformed-200'), default='unavailable')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT)); sys.dont_write_bytecode=True
    sys.stdout.reconfigure(encoding='utf-8')
    allowed_ports = set(); events=[]; blocked=[]
    def forbidden(*_args, **_kwargs):
        blocked.append('forbidden business/config/network I/O')
        (out / 'blocked-calls.json').write_text(json.dumps(blocked), encoding='utf-8')
        raise AssertionError(blocked[-1])
    original_connect = socket.socket.connect
    def only_fixture(sock, address):
        if address[0] != '127.0.0.1' or address[1] not in allowed_ports:
            return forbidden()
        return original_connect(sock, address)
    socket.socket.connect = only_fixture
    fixture_db = out / f'synthetic-release-{os.getpid()}.db'
    original_sqlite_connect = sqlite3.connect
    def fixture_sqlite(database, *a, **kw):
        raw = str(database)
        expected = {str(fixture_db), fixture_db.as_posix(), fixture_db.as_uri() + '?mode=ro', 'file:' + fixture_db.as_posix() + '?mode=ro'}
        if raw not in expected: return forbidden()
        return original_sqlite_connect(database, *a, **kw)
    sqlite3.connect = fixture_sqlite
    import requests
    requests.sessions.Session.request = forbidden
    import core.config as config
    fixture = out / 'preview-data'; fixture.mkdir(exist_ok=True)
    settings = fixture / 'config/settings.json'; settings.parent.mkdir(exist_ok=True)
    settings.write_text('{}', encoding='utf-8')
    ozon = fixture / 'ozon'; ozon.mkdir(exist_ok=True)
    stores = {'catalog':str(fixture/'data/shop.db'),'reports_release':str(fixture/'data/orbit_platform.db'),
              'workbench':str(fixture/'data/orbit_workbench.db'),'ozon':str(ozon)}
    profile = fixture / 'runtime-profile.json'
    profile.write_text(json.dumps({'profile_id':'u00-dense-synthetic','settings_path':str(settings),'stores':stores}), encoding='utf-8')
    os.environ['ORBIT_RUNTIME_PROFILE'] = str(profile)
    config.CONFIG_PATH=settings; config.FALLBACK_CONFIG_PATHS=[]
    config._cache={'ozon':{'data_dir':str(ozon)}}; config.load_settings=forbidden
    config._cache_source=settings; config._cache_source_stat=(settings.stat().st_size,settings.stat().st_mtime_ns)
    from shared_platform import report_store, release_store, workbench_store, release_control
    store = release_store.ReleaseStore(fixture_db)
    plan = store.create_plan({'plan_id':'TEST-plan-99003', 'product_id':'99003',
        'seller_sku':'99003', 'product_revision':7,
        'product_package_id':'TEST-product-99003', 'content_package_id':'TEST-content-99003-r7',
        'targets':['tiktok:LH_MY','shopee:MY','ozon:RU'],
        'commercial_scope':{'cost_snapshot_id':'TEST-cost-r7','fx_snapshot_id':'TEST-fx','pricing_rule_version':'TEST-only'}})
    # Synthetic approval record: required literal from the real store contract,
    # confined to this generated database; it grants no real product authority.
    store.approve_plan(plan['plan_id'],approved_by='Kyle',user_approved=True,
        confirmation_token=plan['confirmation_token'])
    run = store.start_run(plan['plan_id'])
    store.begin_target(run['run_id'],'tiktok:LH_MY')
    store.record_target_success(run['run_id'],'tiktok:LH_MY',external_id='TEST-tiktok-99003',
        readback_evidence={'fixture_only':True,'verified':True,'seller_sku':'99003','title':'TEST source','currency':'MYR','price':25.9})
    store.begin_target(run['run_id'],'shopee:MY')
    store.record_target_failure(run['run_id'],'shopee:MY',error='TEST submission response unknown; reconcile before retry',
        external_id='TEST-shopee-99003',failure_evidence={'fixture_only':True,'reconciliation_required':True,'submission_unknown':True})
    RELEASE_FIXTURES['99003'] = {'plan_approved':True,'plan_persisted':True,
        'eligible_for_plan_approval':False,'publish_ready':False,'miaoshou_prepared':False,
        'plan':store.get_plan(plan['plan_id']),'run':store.get_run(run['run_id']),
        'blockers':['TEST: 不提供业务写入能力']}
    # Browser-only stale projection: this digest is never submitted to a provider.
    RELEASE_FIXTURES['99003']['plan']['targets_digest'] = 'b' * 64
    (out/'release-store-fixture.json').write_text(json.dumps(RELEASE_FIXTURES,ensure_ascii=False,indent=2),encoding='utf-8')
    report_store.DEFAULT_REPORT_STORE_PATH=Path(stores['reports_release'])
    release_store.DEFAULT_RELEASE_STORE_PATH=Path(stores['reports_release'])
    workbench_store.WorkbenchStore.__init__.__defaults__=(Path(stores['workbench']),)
    release_control.build_release_dashboard=dashboard
    from modules.products import server as module
    assert Path(module.__file__).resolve().is_relative_to(ROOT)
    module._release_v1_view=lambda payload: deepcopy(payload['release_v1_fixture'])
    module._apply_oneclick_release_authority=lambda payload: payload
    module._first_review_image_plan_view=lambda payload: {}
    class Guarded(module.Handler):
        def do_GET(self):
            url=urlsplit(self.path); query=parse_qs(url.query)
            events.append({'method':'GET','path':self.path})
            (out/'server-requests.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
            if url.path=='/api/product-workspace/publication-stages':
                offer=(query.get('offer_id') or [''])[0]
                if args.stage_mode=='malformed-200':
                    return self._json(200, {'ok':True,'schema_version':'publication-stages/v1',
                        'offer_id':offer,'common':{'status':'VERIFIED'},'marketplace':{'status':'APPROVAL_REQUIRED',
                            'final_review_available':True,'preview':{'candidate_digest':'fixture-untrusted',
                                'status':'PREVIEW_READY','approval_status':'NOT_APPROVED'},
                            'plan':{'plan_id':'fixture-untrusted','confirmation_token':'fixture-untrusted',
                                'targets':['ozon:RU'],'payload':{}}},
                        'external_writes_performed':[]})
                if args.stage_mode=='unavailable-identified':
                    return self._json(503, {'ok':False,'schema_version':'publication-stages/v1',
                        'offer_id':offer,'error':'Synthetic R3 stage unavailable; no write authority',
                        'common':{'status':'VERIFIED'},'marketplace':{'status':'APPROVAL_REQUIRED',
                            'final_review_available':True,'preview':{'candidate_digest':'fixture-untrusted',
                                'status':'PREVIEW_READY','approval_status':'NOT_APPROVED'},
                            'plan':{'plan_id':'fixture-untrusted','confirmation_token':'fixture-untrusted',
                                'targets':['ozon:RU'],'payload':{}}}})
                return self._json(503, {'ok':False,'error':'Synthetic R3 stage unavailable; no write authority'})
            if url.path=='/api/product-workspace/collectbox-action/preview':
                return self._json(200, stale_collectbox_projection())
            if url.path=='/api/product-flow/preview':
                offer=(query.get('offer_id') or [''])[0]
                return self._json(200, {'ok':True,'offer_id':offer,'revision':7,'source':{'images':[]},
                    'review':{'image_actions':[{'url':'https://fixture.invalid/good.png','action':'keep'},
                                               {'url':'https://fixture.invalid/broken.png','action':'keep'}]}})
            if url.path=='/api/product-flow/content-package/localized-image-review':
                return self._json(200, {'ok':True,'items':[], 'groups':[],'languages':[]})
            if url.path=='/api/proxy-image':
                if 'broken' in (query.get('url') or [''])[0]: return self.send_error(404,'Synthetic image unavailable')
                data=b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#eef2e8"/><rect x="80" y="65" width="240" height="270" rx="10" fill="#d9bca0"/><path d="M140 270 Q240 200 175 110 M170 210 Q230 180 260 205" fill="none" stroke="#487f69" stroke-width="16"/><text x="125" y="365" font-size="20" fill="#40566c">TEST FIXTURE</text></svg>'
                self.send_response(200); self.send_header('Content-Type','image/svg+xml'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
            if url.path=='/api/product-workspace/dashboard' and (query.get('offer_id') or [''])[0]=='99004':
                return self._json(503,{'ok':False,'error':'隔离夹具：模拟服务暂不可用，请重试'})
            if url.path.startswith('/api/') and url.path not in {'/api/product-workspace/dashboard','/api/orbit/navigation','/api/health'}:
                return self._json(503,{'ok':False,'error':'隔离夹具未提供此业务结果'})
            return super().do_GET()
        def do_POST(self):
            events.append({'method':'POST','path':self.path})
            (out/'server-requests.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
            return self._json(403,{'ok':False,'error':'隔离夹具禁止业务写入'})
        def log_message(self,*_args): pass
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Guarded); server.daemon_threads=True
    allowed_ports.add(server.server_port)
    Thread(target=server.serve_forever,daemon=True).start()
    record={'pid':os.getpid(),'url':f'http://127.0.0.1:{server.server_port}/','root':str(ROOT),'profile':str(profile),
            'generated_offers':['99001','99002','99003','99004','99005'], 'business_database_connections':0,
            'synthetic_release_database':str(fixture_db),'release_fixture':'real ReleaseStore create/approve/start/readback/failure; fake identities only',
            'configuration_initialization':0,'external_calls':0,'runtime_cache':'synthetic manually attributed',
            'dashboard':'real handler route + real workspace projection, generated release projection at backend boundary',
            'other_apis':'synthetic image fixture or explicit 503, POST 403'}
    (out/'preview.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False),flush=True)
    try: Event().wait()
    finally: server.shutdown(); server.server_close()


if __name__=='__main__': main()
