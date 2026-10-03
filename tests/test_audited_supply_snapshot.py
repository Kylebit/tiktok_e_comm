"""Entirely synthetic sources; no D04, installed Skill, providers or business DB."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import pytest
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
if __name__ == '__main__' and sys.argv[1:2] == ['--audited-lock-peer']:
    sys.path.insert(0,str(ROOT))

def module():
    from domains.supply_chain_operations import audited_snapshot
    return audited_snapshot

def git(root,*args):
    run=subprocess.run(['git','-c','gc.auto=0','-C',str(root),*args],capture_output=True,text=True,encoding='utf-8')
    assert run.returncode==0,run.stderr
    return run.stdout.strip()

def write(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)

def normalized_fixture():
    m=module();clock='2026-09-30T02:18:17.989Z';order_clock='2026-09-30T02:02:34+00:00'
    inventory={'capturedAt':clock,'source':'fixture://manual-inventory','records':[]}
    orders={'schemaVersion':'order_demand_snapshot_v1','capturedAt':order_clock,'days':31,'countries':{},'networkReads':8,'businessWrites':0,'authWrites':None}
    data={'snapshotDate':'2026-09-30','orderDemandCapturedAt':order_clock,'quantityBasis':'valid_order','economicsBasis':'settlement','config':{},'countries':{}}
    inbound={'capturedAt':clock,'source':'fixture://manual-inbound','regions':{},'reconciliation':{
        'activeBatchCount':0,'activeUnits':0,'sourceStatusCounts':{'all':1,'completed':1,'voided':0,'pendingReview':0,'inTransit':0,'waitingForInbound':0,'inboundProcessing':0},
        'latestCompletedReceipts':[{'batchId':'SYNTHETIC-COMPLETED-TH','region':'TH','totalUnits':5,'skuCount':1,'signedAt':'2026-09-23T00:00:00+00:00','shelvedAt':'2026-09-24T00:00:00+00:00'}]}}
    for region,warehouse in m.REGIONS.items():
        quantities={'stock':6,'available':5,'allocated':1,'frozen':0,'inbound':0}
        inventory['records'].append({'warehouse':warehouse,'seller_sku':'0001','captured_at':clock,**quantities})
        canonical={'0001':{**quantities,'warehouse':warehouse,'sourceAliases':['0001']}}
        config={'name':region+' synthetic','warehouse':warehouse,'leadDays':15,'transportDays':15,
            'targetDays':30,'safetyDays':3,'weightRatioKgM3':167,'fixedHeadFreightUnitCny':1,'taxSavingRate':0,
            'shippingSavingRate':.2,'fxToCny':1,'currencySymbol':'S$','freightMode':'synthetic','demandCoverage':'synthetic normalized','shippingCoverage':'older synthetic economics',
            'inventoryEvidence':{'capturedAt':clock,'source':inventory['source'],'rawRows':1,'canonicalSkuCount':1,'digest':m.sha(m.encoded(canonical))},'orderDemandEvidence':{}}
        row={'sku':'0001','name':'Synthetic manual item','image':'assets/sku-0001.png','kind':'existing',
             'dimensionsCm':[10,10,10],'weightG':100,'costCny':5,'sourceAliases':['0001'],
             'inventory':{**quantities,'warehouse':warehouse},'channels':{}}
        orders['countries'][region]={}
        for platform,display in [('tiktok','TikTok'),('shopee','Shopee')]:
            fact={'days':31,'orders':2,'units':5,'recent30Units':5,'quantityBasis':'valid_order',
                'eventTimeBasis':'create_time_confirmed_order','state':'READY','source':display+' '+region+' 有效订单',
                'evidence':'complete_order_window','sourceAliases':['0001'],'cancelledUnits':0,'returnedUnits':0,
                'name':row['name'],'imageUrl':'https://example.invalid/never-request.png'}
            evidence={'orders_seen':3,'orders_included':2,'orders_excluded':1,'item_lines_unresolved':0}
            source={'region':region,'platform':display,'captured_at':order_clock,'days':31,'facts':{'0001':fact},'evidence':evidence}
            source['digest']=m.sha(m.encoded(source));orders['countries'][region][platform]=source
            config['orderDemandEvidence'][platform]={'ordersSeen':3,'ordersIncluded':2,'ordersExcluded':1,'itemLinesUnresolved':0,'digest':source['digest']}
            row['channels'][platform]={**{k:v for k,v in fact.items() if k not in {'name','imageUrl'}},
                'settlementOrders':1,'settlementUnits':1,'customerPayment':30,'actualShippingFee':2,
                'economicsBasis':'settlement','settlementSource':'older synthetic settlement','settlementEvidence':'older audited fixture'}
        data['config'][region]=config;data['countries'][region]=[row]
        inbound['regions'][region]={'totalUnits':0,'allocationPolicy':'NO_ACTIVE_BATCH','batches':[]}
    return data,inventory,orders,inbound

@pytest.fixture
def snapshot(tmp_path,monkeypatch):
    import sqlite3,urllib.request
    def forbidden(*a,**k):raise AssertionError('business/provider I/O forbidden')
    monkeypatch.setattr(sqlite3,'connect',forbidden);monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    m=module();code=tmp_path/'code';source=tmp_path/'source';runtime=tmp_path/'runtime'
    for root in (code,source,runtime):root.mkdir()
    dashboard='domains/supply_chain_operations/dashboard'
    for name in m.CONSUMERS:write(code/dashboard/name,(ROOT/dashboard/name).read_bytes())
    # The original shell asks the real navigation Handler for this catalog.
    write(code/'shared_platform/entry_catalog.json',(ROOT/'shared_platform/entry_catalog.json').read_bytes())
    for name in ('operations_shell.js','operations_shell.css'):
        write(code/'web/static'/name,(ROOT/'web/static'/name).read_bytes())
    git(code,'init');git(code,'add','.')
    git(code,'-c','user.name=Synthetic test','-c','user.email=fixture@example.invalid','commit','-m','Synthetic browser code fixture')
    data,inventory,orders,inbound=normalized_fixture()
    raws={'data':b'window.SUPPLY_CHAIN_DATA = '+m.encoded(data)+b';\n',
          'inbound':b'window.SUPPLY_CHAIN_INBOUND_PLAN = '+m.encoded(inbound)+b';\n',
          'receipt':b'# Synthetic engineering provenance, no user approval\n',
          'inventory':m.encoded(inventory),'orders':m.encoded(orders)}
    for key,path in m.SOURCE_PATHS.items():write(source/path,raws[key])
    # A real renderable PNG, not a placeholder tag or external URL.
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9WQAAAAASUVORK5CYII=')
    write(source/dashboard/'assets/sku-0001.png',png)
    (source/'.gitignore').write_text('.tmp/\n',encoding='utf-8')
    git(source,'init');git(source,'add','.')
    git(source,'-c','user.name=Synthetic test','-c','user.email=fixture@example.invalid','commit','-m','Synthetic manual source')
    head=git(source,'rev-parse','HEAD')
    config={'runtime_root':str(runtime),'artifact_root':'outputs/manual','output_root':'snapshots','mode':m.GRADE}
    expected={key:m.sha(raw) for key,raw in raws.items()}
    return code,source,head,expected,config,raws,png

def admit(snapshot):
    m=module();code,source,head,expected,config,*_=snapshot
    result=m.import_snapshot(code,source,head,expected,config)
    return result,m.AuditedSnapshotStore(code,config)

def test_manual_mode_is_explicit_and_original_complete_config_remains_strict():
    from domains.supply_chain_operations.captured_serving import deployment_capture,capture_artifact_root
    config={'runtime_root':str(ROOT.parent/'not-created'),'artifact_root':'outputs/manual','output_root':'snapshots','mode':'AUDITED_MANUAL_SNAPSHOT'}
    result=deployment_capture({'supply_chain_capture':config})
    assert result==config and result is not config
    assert deployment_capture({}) is None
    with pytest.raises(ValueError):deployment_capture({'supply_chain_capture':{**config,'mode':'COMPLETE'}})
    with pytest.raises(ValueError):capture_artifact_root(ROOT,config)

def test_native_import_immutable_pair_and_engineering_grade_without_captured_marker(snapshot):
    m=module();result,store=admit(snapshot);code,source,head,expected,config,raws,png=snapshot
    bodies,manifest,directory=store.current()
    assert bodies=={'data.js':raws['data'],'inbound-plan.js':raws['inbound']}
    assert manifest['source_commit']==head and manifest['evidence']['execution_authority'] is False
    assert manifest['provenance']['user_approval'] is False and manifest['provenance']['provider_authenticity']=='NOT_ESTABLISHED'
    assert manifest['evidence']['economics_status']=='NOT_REFRESHED_CAPTURE_DATE_UNKNOWN'
    assert not list(store.artifact.rglob('complete.json')) and not (store.artifact/'captured-serving.json').exists()
    before={p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    repeated=m.import_snapshot(code,source,head,expected,config)
    assert repeated==result and {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}==before
    assert all((source/m.SOURCE_PATHS[k]).read_bytes()==raw for k,raw in raws.items())

@pytest.mark.parametrize('bad',['input_bytes','pair','asset','consumer','marker','missing_index'])
def test_corruption_is_unavailable_and_does_not_return_old_or_partial_pair(snapshot,bad):
    m=module();result,store=admit(snapshot);_,manifest,directory=store.current()
    target={'input_bytes':directory/'inputs/orders.source','pair':directory/'data.js',
            'asset':directory/'assets/sku-0001.png','consumer':store.supply/'app.js',
            'marker':directory/'audited.json','missing_index':store.artifact/'audited-serving.json'}[bad]
    if bad=='missing_index':target.unlink()
    else:target.write_bytes(b'changed')
    with pytest.raises((ValueError,KeyError,OSError)):store.response('index.html',store.supply)

@pytest.mark.parametrize('bad',['wrong_warehouse','changed_order_fact','changed_inventory','active_inbound','order_clock','boolean_inventory_count','invented_user_approval'])
def test_semantic_binding_rejects_hash_consistent_wrong_source(snapshot,bad):
    m=module();raws=dict(snapshot[-2]);data,inventory,orders,inbound=normalized_fixture()
    if bad=='wrong_warehouse':data['config']['MY']['warehouse']='TH8806'
    elif bad=='changed_order_fact':data['countries']['MY'][0]['channels']['tiktok']['units']=999
    elif bad=='changed_inventory':data['countries']['MY'][0]['inventory']['available']=999
    elif bad=='active_inbound':inbound['regions']['MY']['totalUnits']=1
    elif bad=='order_clock':data['orderDemandCapturedAt']='2026-09-29T00:00:00+00:00'
    elif bad=='boolean_inventory_count':data['config']['MY']['inventoryEvidence']['rawRows']=True
    else:
        _,store=admit(snapshot);_,manifest,directory=store.current()
        manifest['provenance']['user_approval']=True
        version=m.sha(m.encoded({k:v for k,v in manifest.items() if k!='version'}));manifest['version']=version
        copied=store.output/version;shutil.copytree(directory,copied)
        changed=m.encoded(manifest);(copied/'audited.json').write_bytes(changed)
        with pytest.raises(ValueError):store.read_version(version,m.sha(changed))
        return
    raws['data']=b'window.SUPPLY_CHAIN_DATA = '+m.encoded(data)+b';'
    raws['inbound']=b'window.SUPPLY_CHAIN_INBOUND_PLAN = '+m.encoded(inbound)+b';'
    with pytest.raises(ValueError):m.validate_pair(raws)

def test_page_projects_original_full_dom_and_versioned_images_without_complete_state(snapshot):
    m=module();result,store=admit(snapshot);code,source,head,expected,config,raws,png=snapshot
    status,html,ctype=store.response('index.html',store.supply)
    assert status==200 and b'id="skuRows"' in html and b'id="manualSnapshotEvidence"' in html
    assert '规范化历史快照'.encode() in html and '结算沿用旧事实'.encode() in html
    assert b'data-audited-version="'+result['version'].encode()+b'"' in html and b'data-captured-version' not in html
    assert b'integrity="sha256-' in html
    for name,raw in [('data.js',raws['data']),('inbound-plan.js',raws['inbound']),('assets/sku-0001.png',png)]:
        response=store.response('audited/'+result['version']+'/'+name,store.supply)
        assert response[0]==200 and response[1]==raw
    assert store.response('data.js',store.supply)[0]==409
    assert store.response('captured/'+result['version']+'/data.js',store.supply)[0]==404
    assert b"READY_MANUAL_DISPLAY" in (store.supply/'audited-bootstrap.js').read_bytes()
    assert b"status = 'COMPLETE'" not in (store.supply/'audited-bootstrap.js').read_bytes()

def test_unknown_mode_does_not_become_an_implicit_manual_bypass(snapshot):
    from domains.supply_chain_operations.captured_serving import CompleteStore,deployment_capture
    _,_,_,_,config,*_=snapshot
    with pytest.raises(ValueError):deployment_capture({'supply_chain_capture':{**config,'mode':'UNKNOWN'}})
    with pytest.raises(ValueError):CompleteStore(snapshot[0],config)

@pytest.mark.parametrize('bad',['extra_quantity_field','unbacked_sku_demand'])
def test_all_displayed_demand_must_be_backed_by_exact_source(snapshot,bad):
    m=module();data,inventory,orders,inbound=normalized_fixture();raws=dict(snapshot[-2])
    if bad=='extra_quantity_field':data['countries']['MY'][0]['channels']['tiktok']['extraDemand']=777
    else:
        row=copy.deepcopy(data['countries']['MY'][0]);row['sku']='0002'
        for field in m.FIELDS:row['inventory'][field]=0
        # The same count is not the same SKU fact. It cannot become a zero projection.
        data['countries']['MY'].append(row)
    raws['data']=b'window.SUPPLY_CHAIN_DATA = '+m.encoded(data)+b';'
    with pytest.raises(ValueError):m.validate_pair(raws)

def test_missing_source_banner_insertion_point_fails_closed(snapshot):
    code=snapshot[0];page=code/'domains/supply_chain_operations/dashboard/index.html'
    page.write_bytes(page.read_bytes().replace(b'<main>',b'<div id="no-main">').replace(b'</main>',b'</div>'))
    _,store=admit(snapshot)
    with pytest.raises(ValueError,match='AUDITED_DISPLAY_TEMPLATE_CHANGED'):store.response('index.html',store.supply)

@pytest.fixture
def snapshot_http(snapshot,monkeypatch):
    import http.client,importlib,socket,threading
    from types import SimpleNamespace
    from http.server import ThreadingHTTPServer
    code=snapshot[0];config=snapshot[4]
    import core.config as settings
    monkeypatch.setattr(settings,'load_settings',lambda *a,**k: (_ for _ in ()).throw(AssertionError('real settings forbidden')))
    server_module=importlib.import_module('modules.products.server');monkeypatch.setattr(server_module,'ROOT',code)
    server=ThreadingHTTPServer(('127.0.0.1',0),server_module.Handler);server.daemon_threads=True
    server.supply_chain_capture=config
    # Supply browser acceptance does not exercise task persistence. Give the
    # original operations-status Handler an explicit inert in-memory runtime,
    # rather than allowing lazy startup to create a task SQLite database.
    server.operations_runtime=SimpleNamespace(
        profile=SimpleNamespace(environment='preview',public=lambda:{'environment':'preview','synthetic_fixture':True}),
        engine=SimpleNamespace(dashboard=lambda:{'executor':{'connected':False,'templates':[]},'domain_guard':{}}),
        worker=SimpleNamespace(status=lambda:{'running':False,'state':'stopped'}),worker_enabled=False)
    connect=socket.socket.connect
    def only_local(sock,address):
        assert address==('127.0.0.1',server.server_port),'provider socket forbidden'
        return connect(sock,address)
    monkeypatch.setattr(socket.socket,'connect',only_local)
    worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});worker.start()
    def get(path):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        try:
            connection.request('GET',path);response=connection.getresponse()
            return response.status,dict(response.headers),response.read()
        finally:connection.close()
    try:yield server,get
    finally:server.shutdown();server.server_close();worker.join(5);assert not worker.is_alive()

def test_actual_original_handler_manual_pair_and_fail_closed_consumer_change(snapshot,snapshot_http):
    result,store=admit(snapshot);server,get=snapshot_http
    for page in ('','inbound-batches.html'):
        status,headers,body=get('/supply-chain/'+page)
        assert status==200 and headers['Cache-Control']=='no-store'
        assert b'id="manualSnapshotEvidence"' in body and b'data-captured-version' not in body
    assert get('/supply-chain/audited/'+result['version']+'/data.js')[2]==snapshot[-2]['data']
    (store.supply/'app.js').write_bytes(b'changed')
    status,headers,body=get('/supply-chain/')
    assert status==503 and '重新导入并核验页面兼容'.encode() in body
    assert get('/supply-chain/app.js')[0]==503
    assert snapshot[-2]['data'] not in body

def test_actual_browser_full_manual_snapshot_images_four_countries_and_refresh(snapshot,snapshot_http,tmp_path):
    result,store=admit(snapshot);server,get=snapshot_http
    node='C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    args=[node,str(ROOT/'tests/audited_snapshot_browser.cjs'),f'http://127.0.0.1:{server.server_port}',str(tmp_path),result['version']]
    run=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=45)
    (tmp_path/'browser-command.json').write_text(json.dumps({'command':args,'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr}),encoding='utf-8')
    assert run.returncode==0,run.stderr
    proof=json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))
    assert proof['version']==result['version'] and proof['fourCountries']==['MY','TH','VN','PH']
    assert proof['allImagesRendered'] and proof['allImagesVersionBound'] and proof['refreshSameVersion']
    assert proof['executionAuthority'] is False and not proof['captureStatePresent'] and proof['pageErrors']==[]
    expected=store.current()[1]
    for response in proof['responses']:
        name=response['name'];digest=expected['pair_sha256'].get(name) or expected['asset_sha256'].get(name)
        assert response['status']==200 and response['sha256']==digest

def test_actual_handler_parallel_readers_keep_both_verified_responses(snapshot,snapshot_http,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    import threading
    result,store=admit(snapshot);server,get=snapshot_http
    first_entered=threading.Event();release_first=threading.Event();guard=threading.Lock();calls=[]
    original=module().AuditedSnapshotStore._read_bound_pair
    def held_first(self,*args,**kwargs):
        with guard:
            first=not calls;calls.append(threading.get_ident())
        if first:
            first_entered.set();assert release_first.wait(3),'first verified reader not released'
        return original(self,*args,**kwargs)
    monkeypatch.setattr(module().AuditedSnapshotStore,'_read_bound_pair',held_first)
    # Actual original Handler and distinct HTTP threads, not mocked responses.
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(get,'/supply-chain/audited/'+result['version']+'/data.js')
        try:
            assert first_entered.wait(3)
            second=pool.submit(get,'/supply-chain/audited-bootstrap.js')
            try:second_response=second.result(timeout=.2)
            except TimeoutError:second_response=None
        finally:release_first.set()
        first_response=first.result(timeout=10)
        if second_response is None:second_response=second.result(timeout=10)
    assert first_response[0]==200 and first_response[2]==snapshot[-2]['data']
    assert second_response[0]==200,(second_response[0],second_response[1],second_response[2].decode())
    assert second_response[2]==(store.supply/'audited-bootstrap.js').read_bytes()
    assert len(set(calls))==2

def test_actual_handler_verified_script_style_mime_ignores_host_registry(snapshot,snapshot_http,monkeypatch):
    result,store=admit(snapshot);server,get=snapshot_http
    monkeypatch.setattr(module().mimetypes,'guess_type',lambda *a,**k:('text/html',None))
    for name,ctype in [('styles.css','text/css'),('audited-bootstrap.js','application/javascript')]:
        status,headers,body=get('/supply-chain/'+name)
        assert status==200 and body==(store.supply/name).read_bytes()
        assert headers['Content-Type'].split(';')[0]==ctype,(name,headers['Content-Type'])

def test_repeated_pair_avoids_reparsing_and_returns_detached_values(snapshot,monkeypatch):
    m=module();raws=snapshot[5];calls=[];original=m.js_object
    cache=getattr(m,'_validated_pair_content',None)
    if cache is not None:cache.cache_clear()
    def tracked(*args):calls.append(args[1]);return original(*args)
    monkeypatch.setattr(m,'js_object',tracked)
    data,evidence=m.validate_pair(raws);expected=copy.deepcopy((data,evidence))
    data['countries']['MY'][0]['inventory']['stock']=99999
    evidence['coverage']['MY']['canonical_skus']=99999
    assert m.validate_pair(raws)==expected
    assert calls==['SUPPLY_CHAIN_DATA','SUPPLY_CHAIN_INBOUND_PLAN']

@pytest.mark.parametrize('changed',('data','inbound','receipt','inventory','orders'))
def test_every_complete_input_byte_participates_in_pair_revalidation(snapshot,monkeypatch,changed):
    m=module();raws=snapshot[5];calls=[];original=m.js_object
    cache=getattr(m,'_validated_pair_content',None)
    if cache is not None:cache.cache_clear()
    def tracked(*args):calls.append(args[1]);return original(*args)
    monkeypatch.setattr(m,'js_object',tracked)
    expected=m.validate_pair(raws)
    assert m.validate_pair({**raws,changed:raws[changed]+b'\n'})==expected
    assert len(calls)==4

@pytest.mark.parametrize('changed',('inventory','asset','consumer'))
def test_warm_pair_never_hides_same_size_same_mtime_artifact_damage(snapshot,changed):
    import os
    m=module();result,store=admit(snapshot);_,manifest,directory=store.current()
    paths={'inventory':directory/'inputs/inventory.source','asset':directory/'assets/sku-0001.png','consumer':store.supply/'app.js'}
    path=paths[changed];before=path.stat();raw=path.read_bytes()
    path.write_bytes(bytes((raw[0]^1,))+raw[1:]);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert path.stat().st_size==before.st_size and path.stat().st_mtime_ns==before.st_mtime_ns
    with pytest.raises(ValueError):store.current()

def _scope(store):
    from shared_platform.capability_runtime import digest
    return digest({'root':str(store.root).casefold(),'artifact':str(store.artifact).casefold()})

def test_concurrent_responses_overlap_and_each_revalidate_full_snapshot(snapshot,monkeypatch):
    m=module();result,store=admit(snapshot);code,_,_,_,_,raws,png=snapshot
    barrier=threading.Barrier(2);current=store._read_bound_pair;read=m._bounded_file_bytes;seen={};guard=threading.Lock()
    def tracked(path,*a,**k):
        with guard:seen.setdefault(threading.get_ident(),set()).add(str(Path(path).resolve()))
        return read(path,*a,**k)
    def simultaneous(*a,**k):barrier.wait(1);return current(*a,**k)
    monkeypatch.setattr(m,'_bounded_file_bytes',tracked);monkeypatch.setattr(store,'_read_bound_pair',simultaneous)
    supply=code/'domains/supply_chain_operations/dashboard'
    suffix='audited/'+result['version']+'/assets/sku-0001.png'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(store.response,suffix,supply) for _ in range(2)]
        assert [f.result(timeout=4)[1] for f in futures]==[png,png]
    assert len(seen)==2
    directory=store.output/result['version']
    mandatory={str((supply/name).resolve()) for name in m.CONSUMERS}
    mandatory|={str((directory/'inputs'/(name+'.source')).resolve()) for name in m.SOURCE_PATHS}
    mandatory|={str((directory/name).resolve()) for name in (*m.FILES,'assets/sku-0001.png','audited.json')}
    assert all(mandatory<=paths for paths in seen.values())

@pytest.mark.parametrize('first',['writer','reader'])
def test_shared_reader_and_original_writer_are_mutually_exclusive_same_process(tmp_path,first):
    from modules.sourcing.image_generation_checkpoint import business_lock,CheckpointRecoveryRequired
    m=module();root=tmp_path/'same-process';scope=m.sha(b'private shared lock fixture')
    if first=='writer':
        with business_lock(root,scope,timeout=0):
            with pytest.raises(CheckpointRecoveryRequired):
                with m._shared_business_read_lock(root,scope,timeout=.1):pytest.fail('reader entered writer range')
    else:
        with m._shared_business_read_lock(root,scope):
            with pytest.raises(CheckpointRecoveryRequired):
                with business_lock(root,scope,timeout=0):pytest.fail('writer entered reader range')
    with business_lock(root,scope,timeout=0):pass

def _peer(root,mode,label):
    return subprocess.Popen([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),
        '--audited-lock-peer',mode,str(root),label],stdout=subprocess.PIPE,stderr=subprocess.PIPE)

def _ready(root,label,peer):
    deadline=time.monotonic()+4
    while not (root/('ready-'+label+'.json')).exists():
        if peer.poll() is not None:
            stdout,stderr=peer.communicate();pytest.fail(f'lock peer exited {peer.returncode}: {stdout!r} {stderr!r}')
        if time.monotonic()>=deadline:pytest.fail('owned lock peer did not acquire')
        time.sleep(.01)
    assert json.loads((root/('ready-'+label+'.json')).read_text())['acquired'] is True

def _release(root,label,peer):
    (root/('release-'+label+'.marker')).write_text('release',encoding='utf-8')
    try:
        stdout,stderr=peer.communicate(timeout=4)
        assert peer.returncode==0,(stdout,stderr)
    finally:
        if peer.poll() is None:peer.kill();peer.wait(timeout=3)

def test_two_owned_process_readers_overlap_and_writer_waits_for_last_release(tmp_path):
    from modules.sourcing.image_generation_checkpoint import business_lock,CheckpointRecoveryRequired
    m=module();root=tmp_path/'peers';root.mkdir();scope=m.sha(b'private shared lock fixture')
    first=_peer(root,'hold_reader','first');second=None
    try:
        _ready(root,'first',first);second=_peer(root,'hold_reader','second');_ready(root,'second',second)
        with pytest.raises(CheckpointRecoveryRequired):
            with business_lock(root,scope,timeout=0):pytest.fail('writer entered two shared readers')
        _release(root,'first',first)
        with pytest.raises(CheckpointRecoveryRequired):
            with business_lock(root,scope,timeout=0):pytest.fail('writer entered remaining shared reader')
        _release(root,'second',second)
        with business_lock(root,scope,timeout=0):pass
        assert (root/('.lingshi-'+scope[:24]+'.lock')).exists()
    finally:
        for label,peer in [('first',first),('second',second)]:
            if peer is not None and peer.poll() is None:_release(root,label,peer)

def test_owned_process_writer_blocks_shared_reader_for_original_two_second_budget(tmp_path):
    from modules.sourcing.image_generation_checkpoint import CheckpointRecoveryRequired
    m=module();root=tmp_path/'writer-peer';root.mkdir();scope=m.sha(b'private shared lock fixture')
    peer=_peer(root,'hold_writer','writer')
    try:
        _ready(root,'writer',peer);started=time.monotonic()
        with pytest.raises(CheckpointRecoveryRequired):
            with m._shared_business_read_lock(root,scope):pytest.fail('reader entered external writer')
        assert 1.9<=time.monotonic()-started<3
    finally:_release(root,'writer',peer)
    with m._shared_business_read_lock(root,scope):pass

def test_owned_reader_process_exit_releases_kernel_lock(tmp_path):
    from modules.sourcing.image_generation_checkpoint import business_lock
    m=module();root=tmp_path/'exit-peer';root.mkdir();scope=m.sha(b'private shared lock fixture')
    peer=_peer(root,'exit_reader','exit')
    try:
        stdout,stderr=peer.communicate(timeout=4);assert peer.returncode==0,(stdout,stderr)
        assert json.loads((root/'ready-exit.json').read_text())['acquired'] is True
        with business_lock(root,scope,timeout=0):pass
    finally:
        if peer.poll() is None:peer.kill();peer.wait(timeout=3)

def test_shared_reader_exception_releases_original_writer_range(tmp_path):
    from modules.sourcing.image_generation_checkpoint import business_lock
    m=module();root=tmp_path/'exception';scope=m.sha(b'private shared lock fixture')
    with pytest.raises(ValueError,match='private validation failure'):
        with m._shared_business_read_lock(root,scope):raise ValueError('private validation failure')
    with business_lock(root,scope,timeout=0):pass

@pytest.mark.parametrize('changed',['inventory','asset','consumer'])
def test_shared_response_warm_read_rejects_same_size_same_time_damage(snapshot,changed):
    from modules.sourcing.image_generation_checkpoint import business_lock
    m=module();result,store=admit(snapshot);code=snapshot[0];supply=code/'domains/supply_chain_operations/dashboard'
    suffix='audited/'+result['version']+'/assets/sku-0001.png'
    assert store.response(suffix,supply)[1]==snapshot[-1]
    directory=store.output/result['version']
    path={'inventory':directory/'inputs/inventory.source','asset':directory/'assets/sku-0001.png',
          'consumer':supply/'app.js'}[changed]
    stat=path.stat();raw=path.read_bytes();modified=bytes([raw[0]^1])+raw[1:]
    with business_lock(store.artifact,_scope(store),timeout=0):
        path.write_bytes(modified);__import__('os').utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    assert path.stat().st_size==stat.st_size and path.stat().st_mtime_ns==stat.st_mtime_ns
    with pytest.raises(ValueError):store.response(suffix,supply)

def test_root_relative_read_uses_one_complete_original_path_check(tmp_path,monkeypatch):
    from shared_platform import capability_runtime
    m=module();root=tmp_path/'explicit';path=root/'nested'/'file.bin';write(path,b'abcd')
    original=capability_runtime.checked_path;calls=[]
    def observed(actual_root,relative):
        calls.append((Path(actual_root),Path(relative)))
        return original(actual_root,relative)
    monkeypatch.setattr(capability_runtime,'checked_path',observed)
    assert m._bounded_root_bytes(root,'nested/file.bin',4)==b'abcd'
    assert calls==[(root,Path('nested/file.bin'))]
    # Separate requests must perform their own full path check and actual read.
    path.write_bytes(b'wxyz')
    assert m._bounded_root_bytes(root,'nested/file.bin',4)==b'wxyz'
    assert calls==[(root,Path('nested/file.bin'))]*2

@pytest.mark.parametrize('kind',['file_symlink','directory_symlink','directory_junction'])
def test_root_relative_read_rejects_actual_owned_redirects(tmp_path,kind):
    from shared_platform.capability_runtime import ToolContextError
    m=module();root=tmp_path/'explicit';other=tmp_path/'other';root.mkdir();other.mkdir()
    target=other/'file.bin';target.write_bytes(b'abcd');link=root/'redirect'
    if kind=='file_symlink':link.symlink_to(target);relative='redirect'
    elif kind=='directory_symlink':link.symlink_to(other,target_is_directory=True);relative='redirect/file.bin'
    else:
        import _winapi
        _winapi.CreateJunction(str(other),str(link));relative='redirect/file.bin'
        assert link.lstat().st_file_attributes & 0x400
    assert link.exists() and (link if kind=='file_symlink' else link/'file.bin').read_bytes()==b'abcd'
    with pytest.raises(ToolContextError,match='symlink or reparse point'):
        m._bounded_root_bytes(root,relative,4)

@pytest.mark.parametrize('kind',['parent_escape','absolute_outside'])
def test_root_relative_read_retains_original_explicit_root_containment(tmp_path,kind):
    from shared_platform.capability_runtime import ToolContextError
    m=module();root=tmp_path/'explicit';root.mkdir();outside=tmp_path/'outside.bin';outside.write_bytes(b'abcd')
    relative='../outside.bin' if kind=='parent_escape' else outside
    with pytest.raises(ToolContextError,match='explicit root'):
        m._bounded_root_bytes(root,relative,4)

@pytest.mark.parametrize('kind',['empty','oversized','missing','directory','grew_during_open'])
def test_root_relative_read_preserves_bounded_read_rejections(tmp_path,monkeypatch,kind):
    m=module();root=tmp_path/'explicit';root.mkdir();path=root/'file.bin'
    if kind=='directory':path.mkdir()
    elif kind!='missing':path.write_bytes(b'' if kind=='empty' else b'abcde' if kind=='oversized' else b'abcd')
    if kind=='grew_during_open':
        original=Path.open
        def changed_at_open(actual,*a,**k):
            if actual==path and a==('rb',):
                with original(actual,'wb') as stream:stream.write(b'abcde')
            return original(actual,*a,**k)
        monkeypatch.setattr(Path,'open',changed_at_open)
    with pytest.raises(ValueError):m._bounded_root_bytes(root,'file.bin',4)

def test_root_relative_read_accepts_exact_size_limit(tmp_path):
    m=module();write(tmp_path/'file.bin',b'abcd')
    assert m._bounded_root_bytes(tmp_path,'file.bin',4)==b'abcd'

@pytest.mark.parametrize('changed',list(module().SOURCE_PATHS))
def test_immutable_read_projection_keys_every_complete_input_byte(snapshot,monkeypatch,changed):
    m=module();raws=snapshot[-2];m._validated_read_projection.cache_clear();calls=[];original=m.validate_pair
    def observed(values):calls.append(dict(values));return original(values)
    monkeypatch.setattr(m,'validate_pair',observed)
    key=lambda values:tuple((k,values[k]) for k in m.SOURCE_PATHS)
    first=m._validated_read_projection(key(raws))
    assert m._validated_read_projection(key(dict(raws))) is first
    updated={**raws,changed:raws[changed]+b' '}
    second=m._validated_read_projection(key(updated))
    assert first==second
    assert calls==[raws,updated]


def test_immutable_read_projection_cannot_alias_mutable_public_pair(snapshot):
    m=module();raws=snapshot[-2];m._validated_read_projection.cache_clear()
    key=tuple((k,raws[k]) for k in m.SOURCE_PATHS)
    references,evidence_raw=m._validated_read_projection(key)
    assert type(references) is frozenset and type(evidence_raw) is bytes
    with pytest.raises(AttributeError):references.add('assets/forged.png')
    with pytest.raises(TypeError):evidence_raw[0]=0
    data,evidence=m.validate_pair(raws)
    data['countries']['MY'][0]['image']='assets/forged.png';evidence['execution_authority']=True
    assert m._validated_read_projection(key)==(references,evidence_raw)
    assert 'assets/forged.png' not in references and m.loads(evidence_raw)['execution_authority'] is False
    fresh_data,fresh_evidence=m.validate_pair(raws)
    assert fresh_data['countries']['MY'][0]['image']=='assets/sku-0001.png'
    assert fresh_evidence['execution_authority'] is False


def test_repeated_verified_store_read_avoids_full_tree_decode(snapshot,monkeypatch):
    m=module();result,store=admit(snapshot);raws=snapshot[-2]
    m._validated_read_projection.cache_clear()
    serialized=m._validated_pair_content(tuple((k,raws[k]) for k in m.SOURCE_PATHS));original=m.loads;decodes=[]
    def observed(raw):
        if raw==serialized:decodes.append(True)
        return original(raw)
    monkeypatch.setattr(m,'loads',observed)
    first=store.current();second=store.current()
    assert first==second and first[1]['version']==result['version']
    assert len(decodes)==1


@pytest.mark.parametrize('kind',['regular','empty','oversize','directory','missing'])
def test_single_file_stat_retains_real_type_size_and_read_boundary(tmp_path,monkeypatch,kind):
    m=module();target=tmp_path/'item';original=Path.stat;calls=[]
    if kind=='directory':target.mkdir()
    elif kind!='missing':target.write_bytes({'regular':b'abcd','empty':b'','oversize':b'abcde'}[kind])
    def observed(path,*args,**kwargs):
        if path==target:calls.append(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',observed)
    if kind=='regular':assert m._bounded_file_bytes(target,4)==b'abcd'
    else:
        with pytest.raises(ValueError,match='audited manual snapshot unavailable'):m._bounded_file_bytes(target,4)
    assert calls==[target]


@pytest.mark.parametrize('kind,number',[
    ('errno',2),('errno',20),('errno',9),('errno',10062),('errno',13),
    ('winerror',21),('winerror',123),('winerror',1921),('winerror',5),('value',None),
])
def test_single_file_stat_matches_actual_path_is_file_exception_contract(tmp_path,monkeypatch,kind,number):
    m=module();target=tmp_path/'item'
    if kind=='value':error=ValueError('embedded null or invalid path')
    else:
        error=OSError(number if kind=='errno' else 0,'single-stat contract')
        if kind=='winerror':error.winerror=number
    calls=[]
    def rejected(path,*args,**kwargs):
        assert path==target;calls.append(path);raise error
    monkeypatch.setattr(Path,'stat',rejected)
    def original_condition():m.require(target.is_file() and 0<target.stat().st_size<=4)
    with pytest.raises((OSError,ValueError)) as old:original_condition()
    old_calls=list(calls);calls.clear()
    with pytest.raises((OSError,ValueError)) as new:m._bounded_file_bytes(target,4)
    assert type(new.value) is type(old.value) and str(new.value)==str(old.value)
    assert calls==old_calls==[target]
    if old.value is error:assert new.value is error


def _lock_peer_main():
    import os
    from modules.sourcing.image_generation_checkpoint import business_lock
    # Script argv omit Python's flags: fixed mode, private root and signal label.
    assert len(sys.argv)==5 and sys.argv[1]=='--audited-lock-peer'
    mode,root,label=sys.argv[2],Path(sys.argv[3]).resolve(),sys.argv[4]
    assert (mode,label) in {('hold_reader','first'),('hold_reader','second'),('hold_writer','writer'),('exit_reader','exit')}
    assert root.is_dir()
    m=module();scope=m.sha(b'private shared lock fixture')
    lock=business_lock(root,scope,timeout=0) if mode=='hold_writer' else m._shared_business_read_lock(root,scope)
    with lock:
        (root/('ready-'+label+'.json')).write_text(json.dumps({'acquired':True,'pid':os.getpid(),'mode':mode}),encoding='utf-8')
        if mode=='exit_reader':os._exit(0)
        deadline=time.monotonic()+6
        while not (root/('release-'+label+'.marker')).exists():
            if time.monotonic()>=deadline:raise RuntimeError('owned lock peer release missing')
            time.sleep(.01)

if __name__=='__main__':_lock_peer_main()
