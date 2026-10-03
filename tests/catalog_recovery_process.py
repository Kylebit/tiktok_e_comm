"""Fresh-process, guarded real HTTP recovery with fake GET transport only."""
from pathlib import Path
import sys,os,json,hashlib,sqlite3,threading,http.client,ast,subprocess
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];TEMP=Path(sys.argv[1]).resolve()
RUNTIME=Path(sys.executable).resolve().parents[1]
assert TEMP.is_relative_to(Path(os.environ['B4B_BOUND_TEMP']).resolve())
sys.dont_write_bytecode=True;sys.path[:0]=[str(ROOT),str(RUNTIME/'Lib/site-packages')]
os.environ['ORBIT_RUNTIME_PROFILE']=str(TEMP/'absent-profile.json')
os.environ['ORBIT_R3_CONFIG_ROOT']=str(TEMP/'absent-startup-config')
allowed_reads=set()
metadata=ROOT/'.git'
gitdir=(ROOT/metadata.read_text().strip().removeprefix('gitdir: ')).resolve() if metadata.is_file() else metadata
common=(gitdir/(gitdir/'commondir').read_text().strip()).resolve() if (gitdir/'commondir').is_file() else gitdir
ref=(gitdir/'HEAD').read_text().strip().removeprefix('ref: ')
allowed_reads.update({metadata,gitdir/'commondir',gitdir/'HEAD',common/ref,common/'packed-refs'})
allowed_reads.update((ROOT/name).resolve() for name in json.loads((ROOT/'shared_platform/entry_catalog.json').read_text(encoding='utf-8'))['supply_images'])
for statement in ast.parse((ROOT/'shared_platform/runtime_identity.py').read_text()).body:
    if isinstance(statement,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'ENTRY_FILES','ASSET_FILES'} for t in statement.targets):
        allowed_reads.update((ROOT/name).resolve() for name in ast.literal_eval(statement.value))
git_command=['git','-c','safe.directory='+ROOT.as_posix(),'-C',str(ROOT),'rev-parse','HEAD']
counts={};denied=[];ports=set();sources={}
def reject(event):
    denied.append(event);raise RuntimeError('recovery process guard: '+event)
def guard(event,args):
    if event=='sqlite3.connect':
        p=str(args[0]);p=p.removeprefix('file:').split('?',1)[0]
        from urllib.parse import unquote
        if not Path(unquote(p).lstrip('/') if p.startswith('/') else unquote(p)).resolve().is_relative_to(TEMP):reject(event)
    elif event=='subprocess.Popen':
        if args[1] not in (git_command,subprocess.list2cmdline(git_command)):reject(event)
    elif event in {'os.system','socket.sendto'}:reject(event)
    elif event in {'socket.bind','socket.connect'}:
        if args[-1][0]!='127.0.0.1' or event=='socket.connect' and args[-1][1] not in ports:reject(event)
    elif event in {'socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'}:
        if args[0] not in {'127.0.0.1','localhost'}:reject(event)
    elif event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        p=Path(os.fsdecode(args[0])).resolve();mode,flags=args[1:3]
        writing=isinstance(mode,str) and any(c in mode for c in 'wax+') or isinstance(flags,int) and bool(flags&(os.O_WRONLY|os.O_RDWR))
        if p.is_relative_to(TEMP):return
        if writing:reject('outside_temp_write')
        if p in allowed_reads:return
        if p.is_relative_to(ROOT):
            if p in allowed_reads or p.is_relative_to(ROOT/'.git'):return
            if p.suffix!='.py':reject('non_source_workspace_read:'+str(p))
            sources[str(p)]=True
        elif not any(p.is_relative_to(base) for base in [RUNTIME,Path(sys.base_prefix),Path(os.environ['SYSTEMROOT'])]):reject('outside_runtime_read')
    elif event in {'os.mkdir','os.rename','os.remove','os.rmdir'}:
        for p in args[:2] if event=='os.rename' else args[:1]:
            if not Path(p).resolve().is_relative_to(TEMP):reject('outside_temp_mutation')
sys.addaudithook(guard)
import importlib.machinery
original_get_code=importlib.machinery.SourceFileLoader.get_code
def source_code(loader,name):
    p=Path(loader.path).resolve()
    if p.is_relative_to(ROOT):sources[str(p)]=True
    return compile(p.read_bytes(),str(p),'exec',dont_inherit=True) if p.is_relative_to(ROOT) else original_get_code(loader,name)
importlib.machinery.SourceFileLoader.get_code=source_code
scenario=json.loads((TEMP/'scenario.json').read_text())
mapping_before=(TEMP/'map.json').read_bytes() if (TEMP/'map.json').exists() else None
from core import config
config._cache={'database':str(TEMP/'shop.db'),'shopee':{'global_sku_map':str(TEMP/'map.json')}}
if scenario.get('platform')=='OZON':
    sys.path.insert(0,str(ROOT/'tests'))
    from ozon_recovery_process import run
    result,calls=run(scenario,TEMP,ports)
    assert not denied,denied
    (TEMP/'RECOVERY.json').write_text(json.dumps({'pid':os.getpid(),'result':result,'calls':calls,'denied':denied,'source_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}},indent=2))
    raise SystemExit(0)
from modules.shopee import client,auth
from modules.products import server
from shared_platform.catalog_publication_sync import CatalogPublicationSync
from shared_platform.catalog_cost_projection import save_manual,read_cost
auth.load_tokens=lambda:scenario['tokens']
def forbidden(*args,**kwargs):raise AssertionError('repair or credential refresh forbidden')
auth.ensure_shop_token=forbidden;auth.refresh_merchant_token=forbidden
client.shop_post=client.merchant_post=client.upload_image=forbidden
calls=[]
def fake_get(path,owner,token,params=None):
    calls.append({'method':'GET','path':path,'owner':owner,'params':params})
    if scenario['mode']=='get_error':raise TimeoutError('synthetic transport timeout')
    expected=scenario['gets'][path]
    assert params==expected['params'] and owner==expected['owner']
    return expected['response']
client.shop_get=client.merchant_get=fake_get
server._catalog_publication_sync=lambda:CatalogPublicationSync(TEMP/'shop.db',TEMP/'outbox')
if scenario['mode']=='manual_conflict':
    i=scenario['identity']
    with sqlite3.connect(TEMP/'shop.db') as conn:conn.execute('INSERT INTO shopee_products(model_id,shop_id,item_id,seller_sku) VALUES(?,?,?,?)',(i['variant_id'],i['shop_key'],i['product_id'],i['seller_sku']))
    save_manual(TEMP/'shop.db',i,'43',0)
httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);ports.add(httpd.server_port)
thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
try:
    def request():
        connection=http.client.HTTPConnection('127.0.0.1',httpd.server_port)
        connection.request('POST','/api/catalog/publication-sync/official-readback',json.dumps({'receipt_id':scenario['receipt_id']}),{'Content-Type':'application/json'})
        response=connection.getresponse();body=json.loads(response.read());connection.close();assert response.status==200,body;return body
    result=request();before=len(calls)
    if scenario['mode']=='normal':
        assert result['state']=='COMPLETE',result
        assert request()['state']=='COMPLETE' and len(calls)==before
    elif scenario['mode']=='manual_conflict':
        assert result['state']=='NEEDS_REVIEW',result
        with sqlite3.connect(TEMP/'shop.db') as conn:
            conn.row_factory=sqlite3.Row;assert read_cost(conn,scenario['identity'])['amount']=='43'
    else:
        assert result['state']=='PENDING_OFFICIAL_EVIDENCE',result
        with sqlite3.connect(TEMP/'shop.db') as conn:assert conn.execute('SELECT count(*) FROM shopee_products').fetchone()[0]==0
        if scenario['mode']=='old_intent':assert not calls
        if scenario['mode'].startswith('localized_'):assert result['code'] in {'localized_gallery_identity_conflict','localized_gallery_binding_missing'},result
    from shared_platform.catalog_shopee_readback import CatalogShopeeQueries
    try:CatalogShopeeQueries().get(None,'/api/v2/product/update_item',{})
    except ValueError:pass
    else:raise AssertionError('GET allowlist missing')
    assert not denied,sorted(set(denied))[:12]
    assert ((TEMP/'map.json').read_bytes() if (TEMP/'map.json').exists() else None)==mapping_before
    (TEMP/'RECOVERY.json').write_text(json.dumps({'pid':os.getpid(),'result':result,'calls':calls,'denied':denied,'source_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}},indent=2))
finally:
    httpd.shutdown();httpd.server_close();thread.join(3)
