"""Own synthetic inputs + original complete Handler AST; no app startup."""
import argparse, ast, hashlib, json, mimetypes, os
from pathlib import Path
import socketserver, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path);args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    os.environ['ORBIT_R3_CONFIG_ROOT']=str(out/'synthetic-r3-config')
    os.environ['ORBIT_R3_POLICY_PATH']='missing-policy.json'
    os.environ['ORBIT_R3_INCIDENT_REGISTRY_PATH']='missing-incidents.json'
    sys.pycache_prefix=str(out/'unused-pycache')
    from u05_finance_guard import install
    install(ROOT,out)  # before business imports; usable without an external harness
    import sqlite3
    from test_i05_profit_facts import catalog_db,captured_evidence
    from core.static_files import resolve_static_path
    fixture=out/'inputs';fixture.mkdir()
    catalog=catalog_db(fixture)
    with sqlite3.connect(catalog) as db:
        db.execute("UPDATE sku_costs SET cost_cny='8'")
    fx=fixture/'fx.json';fx.write_text(json.dumps({'rates_cny':{'THB':'0.2'},'source':'SYNTHETIC FX','as_of':'2026-08-06T00:00:00Z'}))
    profiles={}
    for name in ('ready','partial','empty','slow','missing','conflict','formula','cash','coverage','coverage-gap','zero'):
        evidence=captured_evidence();evidence['shop_id']='fixture';evidence['orders']=[]
        for i in range(24 if name in {'ready','slow'} else 2):
            order=captured_evidence()['orders'][0]
            order['order_id']=f'TEST-{i+1:03d}';order['items'][0]['platform_sku']='variant'
            order['settled_at']=f'2026-08-05T{(i%12)+1:02d}:00:00+07:00'
            order['financial_components']=[{'code':'import_vat','amount':'1'},{'code':'customs_duty','amount':'1'}]
            if name=='cash':order['financial_components'] += [{'code':'customer_payment','amount':'190'},{'code':'customer_shipping_fee','amount':'10'}]
            if name=='partial' and i==1:order['items'][0]['product_id']='OTHER-PRODUCT'
            if name=='formula':
                if i==0:order['order_id']='=SUM(1,1)'
                else:order['net_settlement_amount']='-100'
            evidence['orders'].append(order)
        if name in {'empty','zero'}:evidence['orders']=[]
        evidence['net_settlement_total_local']=str(sum(int(row['net_settlement_amount']) for row in evidence['orders']))
        path=fixture/(name+'.json');path.write_text(json.dumps(evidence))
        profile={'schema_version':'profit-captured-profile/v1','platform':'tiktok','site':'TH','shop_id':'fixture','start':'2026-08-03','end':'2026-08-09','timezone':'+07:00','catalog_path':str(catalog),'evidence_path':str(path),'fx_path':str(fx),'allow_temporary_cost_policy':False}
        if name=='missing':profile['evidence_path']=str(fixture/'not-present.json')
        if name=='conflict':
            folder=fixture/'conflict';folder.mkdir();profile['catalog_path']=str(catalog_db(folder))
        if name in {'coverage','coverage-gap','zero'}:
            from test_u05_coverage import coverage_fixture,history_fixture
            coverage=coverage_fixture(profile)
            if name=='coverage-gap':coverage['streams']['settlements']['days'].pop()
            coverage_path=fixture/(name+'-coverage.json');coverage_path.write_text(json.dumps(coverage));profile['coverage_path']=str(coverage_path)
            if name=='coverage':
                history_fixture(fixture/'knowledge');profile['knowledge_root']=str(fixture/'knowledge')
        profile_path=fixture/(name+'-profile.json');profile_path.write_text(json.dumps(profile,indent=2));profiles[name]=str(profile_path)
    from test_u05_sku_evidence import scenario,save as save_fixture
    for name in ('ready','conflict','missing','no-cost'):
        profile,_,_=scenario(fixture/('sku-'+name),name)
        profile_path=fixture/('sku-'+name+'-profile.json');save_fixture(profile_path,profile);profiles['sku-'+name]=str(profile_path)
    from test_u05_waterfall import waterfall_fixture
    from test_u05_sku_evidence import resave
    for name in ('full','partial','unknown','invalid'):
        folder=fixture/('waterfall-'+name);folder.mkdir();profile,source,_=waterfall_fixture(folder)
        water=source['entries'][0]['waterfall']
        if name=='partial':
            water['prior']['with_affiliate']['components'].pop('logistics_local');water['sample_fees'][0]['components'].pop('logistics_local')
        if name=='unknown':
            water['prior'].pop('no_affiliate')
            for row in water['sample_fees']:row['affiliate_state']='unknown'
        if name=='invalid':water['valid_to']='2026-08-09T00:00:00+07:00'
        resave(profile,source);profile_path=fixture/('waterfall-'+name+'-profile.json');save_fixture(profile_path,profile);profiles['waterfall-'+name]=str(profile_path)
    from test_u05_sample_audit import sample_fixture
    for name in ('large','equal','empty','unknown','missing-fees'):
        folder=fixture/('sample-'+name);folder.mkdir();profile,_,_=sample_fixture(folder,name)
        profile_path=fixture/('sample-'+name+'-profile.json');save_fixture(profile_path,profile);profiles['sample-'+name]=str(profile_path)
    raw=(ROOT/'modules/products/server.py').read_bytes();tree=ast.parse(raw)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
    required_names={'PRODUCT_APPROVAL_BODY_LIMIT','_ROUND1_CATEGORY_PREFIX','_PUBLICATION_CLOSURE_PREFIX'}
    handler_constants=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in required_names for t in n.targets)]
    assert {t.id for n in handler_constants for t in n.targets if isinstance(t,ast.Name)}==required_names
    selected=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*handler_constants,cls],type_ignores=[])
    # Standard MIME defaults without registry/config discovery.
    mimetypes.inited=True;mimetypes._db=mimetypes.MimeTypes()
    namespace={'BaseHTTPRequestHandler':BaseHTTPRequestHandler,'Path':Path,'json':json,'urlparse':urlparse,'mimetypes':mimetypes,'ROOT':ROOT,'WEB_DIR':ROOT/'web','resolve_static_path':resolve_static_path}
    exec(compile(ast.fix_missing_locations(selected),str(ROOT/'modules/products/server.py'),'exec'),namespace)
    full_handler=os.environ.get('ORBIT_FULL_HANDLER')=='1'
    if full_handler:
        os.environ['no_proxy']='*'
        from modules.products import server as actual_server
        actual_server.ROOT=fixture
        (fixture/'shared_platform').mkdir()
        (fixture/'shared_platform/entry_catalog.json').write_bytes((ROOT/'shared_platform/entry_catalog.json').read_bytes())
        supply=fixture/'domains/supply_chain_operations/dashboard';supply.mkdir(parents=True)
        for name in ('index.html','inbound-batches.html','app.js','styles.css','inbound-batches.js','inbound-timeline.js','transport-history.js','data.js','inbound-plan.js'):
            origin=ROOT/('tests/fixtures/u01_combination' if name in {'data.js','inbound-plan.js'} else 'domains/supply_chain_operations/dashboard')/name
            (supply/name).write_bytes(origin.read_bytes())
        namespace['Handler']=actual_server.Handler
    from fixture_record_log import FixtureRecordLog
    record_log=FixtureRecordLog(out)
    def save():
        modules={n:{'path':str(Path(m.__file__).resolve()),'sha256':hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()} for n,m in list(sys.modules.items()) if getattr(m,'__file__',None) and Path(m.__file__).resolve().is_relative_to(ROOT)}
        with record_log.lock, (out/'runtime-imports.json').open('w',encoding='utf-8') as stream:
            json.dump(modules,stream,indent=2)
    class Guarded(namespace['Handler']):
        def _file(self,path,*,cache_seconds=None):
            if full_handler and Path(path) in {ROOT/'domains/supply_chain_operations/dashboard/data.js',ROOT/'domains/supply_chain_operations/dashboard/inbound-plan.js'}:
                path=ROOT/'tests/fixtures/u01_combination'/Path(path).name
            return super()._file(path,cache_seconds=cache_seconds)
        def do_GET(self):
            path=urlparse(self.path).path
            with record_log.lock:
                self.fixture_request_id=record_log.request('GET',self.path);save()
            if full_handler and path=='/api/orbit/operations-runtime':
                # This synthetic finance fixture has no Git checkout or worker.
                return self._json(200,{'execution_mode':'web-only','worker_enabled':False,
                                       'dispatcher':{'state':'stopped'},'release':{'environment':'preview'}})
            if full_handler and (path in {'/','/knowledge','/product-workspace'} or path.startswith('/static/') or path.startswith('/supply-chain/') or path.startswith('/api/orbit/')):
                return super().do_GET()
            if path not in {'/profit','/profit.html','/profit-review','/profit-review.html','/api/orbit/navigation','/api/profit-center/captured-review','/static/profit_center.css','/static/profit_center.js','/static/profit_sku.js','/static/profit_samples.js','/static/profit_review_model.js','/static/operations_shell.js'}:return self.send_error(404,'Fixture exposes finance only')
            return super().do_GET()
        def do_POST(self):
            with record_log.lock:
                self.fixture_request_id=record_log.request('POST',self.path);save()
            if self.path not in {'/api/profit-center/captured-review','/api/profit-center/report-view'}:return self.send_error(403)
            return super().do_POST()
        def _read_json(self):
            value=super()._read_json()
            # Transport delay only; unmodified JSON goes into the original consumer.
            if str(value.get('profile',{}).get('evidence_path','')).endswith('slow.json'):Event().wait(.8)
            return value
        def _json(self,status,payload):
            with record_log.lock:
                record_log.response(status,payload,self.path,self.fixture_request_id);save()
            try:return super()._json(status,payload)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def log_message(self,*args):pass
    class Server(ThreadingHTTPServer):
        def server_bind(self):socketserver.TCPServer.server_bind(self);self.server_name='localhost';self.server_port=self.server_address[1]
    server=Server(('127.0.0.1',0),Guarded)
    # /profit is the user-approved July report. The synthetic calculator
    # acceptance target is the explicit review route in every fixture mode.
    review_route='/profit-review'
    record={'pid':os.getpid(),'root':str(ROOT),'url':f'http://127.0.0.1:{server.server_port}{review_route}','profiles':profiles,'handler_sha256':hashlib.sha256(raw).hexdigest(),'full_handler':full_handler,'contract':'actual imported Handler across product finance and synthetic supply' if full_handler else 'complete original Handler AST, guarded routes, original catalog/evidence/engines; no business mocks'}
    (out/'preview.json').write_text(json.dumps(record,indent=2));print(json.dumps(record),flush=True)
    try:server.serve_forever(poll_interval=.05)
    finally:server.server_close();save()

if __name__=='__main__':main()
