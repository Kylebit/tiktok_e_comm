"""Serve an explicitly registered local review copy; no original/provider writes."""
from pathlib import Path
import argparse,json,os,sys,importlib.machinery
parser=argparse.ArgumentParser()
parser.add_argument('--metadata',required=True);parser.add_argument('--state-dir',required=True)
parser.add_argument('--port',type=int,default=0)
parser.add_argument('--profile',type=Path,help='explicit non-secret profile located in this review state directory')
parser.add_argument('--weight-overrides',type=Path,help='optional override copy in the review database private directory')
parser.add_argument('--publication-review-offer',help='enable local image decisions for one already registered offer')
parser.add_argument('--original-profit-root',type=Path,help='explicit read-only directory of allowlisted July report originals')
parser.add_argument('--operations-agent-executable',type=Path,help='optional explicitly selected controlled agent CLI')
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1];OUT=Path(args.state_dir).resolve();OUT.mkdir(parents=True,exist_ok=True)
metadata=json.loads(Path(args.metadata).read_text(encoding='utf-8'))
DATABASE=Path(metadata['snapshot_database']).resolve()
assert DATABASE.is_file() and DATABASE!=Path(metadata['source_database']).resolve()
assert DATABASE.parent.name=='private' and DATABASE.parent.parent==Path(args.metadata).resolve().parent
override_path=args.weight_overrides.resolve() if args.weight_overrides else None
if override_path is not None:
    assert override_path.is_file() and override_path.parent==DATABASE.parent
settings=OUT/'settings.json'
profile_path=args.profile.resolve() if args.profile else None
if profile_path is not None:
    assert profile_path.parent==OUT and profile_path.is_file()
sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT))
runtime=Path(sys.executable).resolve().parents[1];sys.path.append(str(runtime/'Lib/site-packages'))
review_directory=None
review_reports=None
if args.publication_review_offer:
    import re
    assert re.fullmatch(r'[0-9]{1,32}',args.publication_review_offer)
    from shared_platform.publication_r2_review import review_view
    review_view(args.publication_review_offer,runtime_root=ROOT)
    review_directory=(ROOT/'data/r2_candidate_reviews'/args.publication_review_offer).resolve()
    assert review_directory.is_relative_to(ROOT/'data/r2_candidate_reviews')
    review_reports=review_directory/'project/reports/product-preparation'/args.publication_review_offer
for key in list(os.environ):
    if any(word in key.upper() for word in ('TOKEN','SECRET','PASSWORD','API_KEY','PROXY','ORBIT','SHOPEE','TIKTOK','OZON','LINGSHI')):os.environ.pop(key,None)
if profile_path is not None:
    from shared_platform.runtime_identity import read_profile
    profile=read_profile(ROOT,profile_path)
    expected_stores={'catalog':str(DATABASE),'reports_release':str(OUT/'unconnected-platform.db'),
        'workbench':str(OUT/'unconnected-workbench.db'),'ozon':str(ROOT/'modules/ozon/legacy_webapp/data')}
    assert profile is not None and profile['settings_path']==str(settings.resolve()) and profile['stores']==expected_stores
    os.environ['ORBIT_RUNTIME_PROFILE']=str(profile_path)
os.environ['ORBIT_R3_CONFIG_ROOT']=str(OUT/'unconnected-r3')
os.environ['ORBIT_OPERATIONS_ENV']='preview'
os.environ['ORBIT_OPERATIONS_DATA_ROOT']=str(OUT/'operations')
if args.operations_agent_executable:
    os.environ['ORBIT_OPERATIONS_AGENT_EXECUTABLE']=str(args.operations_agent_executable.resolve(strict=True))
def audit(event,values):
    if event in {'socket.connect','socket.getaddrinfo'}:
        from shared_platform.catalog_images import permits_network
        if not permits_network(event,values):raise PermissionError('review copy: only registered image reads allowed')
    if event=='os.system':raise PermissionError('review copy: external actions disabled')
    if event=='sqlite3.connect':
        value=str(values[0])
        if value not in {str(DATABASE),DATABASE.as_uri()+'?mode=ro'} and not value.startswith(str(OUT)):raise PermissionError('review copy: database outside scope')
    if event=='open' and isinstance(values[0],(str,bytes,os.PathLike)):
        path=Path(os.fsdecode(values[0])).resolve();mode,flags=values[1:3]
        writing=isinstance(mode,str) and any(c in mode for c in 'wax+') or isinstance(flags,int) and bool(flags&(os.O_WRONLY|os.O_RDWR))
        review_write=review_directory is not None and (path==review_directory/'decision.lock' or
            path.parent==review_reports and (path.name=='r2-candidate-adoption.json' or path.name.startswith('r2-candidate-adoption.tmp-')))
        if writing and not path.is_relative_to(OUT) and not path.is_relative_to(DATABASE.parent) and not review_write:raise PermissionError('review copy: write outside copy')
        if path.name.lower() in {'settings.json','.env','tiktok_tokens.json','lingshi.local.json'} and not path.is_relative_to(OUT):raise PermissionError('review copy: personal configuration disabled')
sys.addaudithook(audit)
original=importlib.machinery.SourceFileLoader.get_code
imports={}
def compiled(loader,name):
    path=Path(loader.path).resolve()
    if path.is_relative_to(ROOT):
        import hashlib
        raw=path.read_bytes();imports[str(path)]=hashlib.sha256(raw).hexdigest()
        return compile(raw,str(path),'exec',dont_inherit=True)
    return original(loader,name)
importlib.machinery.SourceFileLoader.get_code=compiled
from core import config
settings.write_text(json.dumps({'database':str(DATABASE),'shopee':{'global_sku_map':str(OUT/'absent-map.json')}}))
config.CONFIG_PATH=settings;config.FALLBACK_CONFIG_PATHS=[];config.load_settings()
from modules.catalog import ozon_data
if override_path is not None:
    from modules.catalog import weight_overrides
    weight_overrides.PATH=override_path
ozon_data._ozon_dir=lambda:OUT/'absent-legacy-ozon'
from shared_platform import report_store,release_store,workbench_store
report_store.DEFAULT_REPORT_STORE_PATH=OUT/'unconnected-platform.db'
release_store.DEFAULT_RELEASE_STORE_PATH=OUT/'unconnected-platform.db'
workbench_store.WorkbenchStore.__init__.__defaults__=(OUT/'unconnected-workbench.db',)
from modules.products import server
if args.original_profit_root:
    from shared_platform import original_profit_reports
    original_profit_reports.ASSET_ROOT=args.original_profit_root.resolve(strict=True)
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
class Review(server.Handler):
    def do_POST(self):
        if urlparse(self.path).path.startswith('/api/orbit/tasks'):return super().do_POST()
        if urlparse(self.path).path=='/api/catalog/cost':return super().do_POST()
        if review_directory is not None and urlparse(self.path).path=='/api/product-workspace/r2-candidate/decision':return super().do_POST()
        self._json(403,{'ok':False,'error':'本地审核副本：仅允许保存副本成本，平台操作未启用。'})
    def do_PUT(self):self._json(403,{'ok':False,'error':'审核副本不支持此操作。'})
    do_DELETE=do_PUT;do_PATCH=do_PUT
    def do_GET(self):
        try:super().do_GET()
        except Exception as error:self._json(503,{'ok':False,'error':'此审核副本未连接该业务数据。','reason':type(error).__name__})
http=ThreadingHTTPServer(('127.0.0.1',args.port),Review);http.daemon_threads=True;http.supply_chain_capture=None
from shared_platform.catalog_images import ImageCache
from core.db import connect_readonly
image_conn=connect_readonly()
try:http.catalog_image_cache=ImageCache(image_conn,OUT/'image-cache')
finally:image_conn.close()
http.catalog_review_copy={'mode':'LOCAL_REVIEW_COPY','captured_at':metadata['captured_at'],
    'source_database':metadata['source_database'],'source_record_updated_at':metadata['source_updated_at'],
    'cost_changes':'COPY_ONLY','images':'REGISTERED_CACHE'}
# Health remains the actual Handler's runtime identity; ready does not assert a
# commit independently from that endpoint.
(OUT/'ready.json').write_text(json.dumps({'pid':os.getpid(),'port':http.server_port,'root':str(ROOT),'database':str(DATABASE),'mode':'LOCAL_REVIEW_COPY','profile_path':str(profile_path) if profile_path else None,'imports':imports},indent=2))
from shared_platform.operations_service import get_runtime
operations = get_runtime(http, ROOT)
try:
    http.serve_forever()
finally:
    operations.worker.close()
