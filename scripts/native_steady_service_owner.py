import ctypes, hashlib, http.client, json, os, socket, stat, subprocess, sys, time, uuid
from ctypes import wintypes
from pathlib import Path
BASE=Path(__file__).resolve().parent
def pin(p,value):
    p=Path(p); regular(p)
    if digest(p)!=value:raise RuntimeError('FORMAL_FIXED_INPUT_DRIFT')
def save_new(p,v):
    with Path(p).open('x',encoding='utf-8') as f:f.write(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sealed(path,value):
    pin(path,value);return json.loads(Path(path).read_bytes())
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def regular(p, directory=False):
    for q in (p, *p.parents):
        if q.exists():
            s = q.lstat()
            if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
                raise ValueError('OWNED_PREVIEW_REPARSE')
    s = p.lstat()
    if directory:
        if not stat.S_ISDIR(s.st_mode): raise ValueError('OWNED_PREVIEW_DIRECTORY_REQUIRED')
    elif not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
        raise ValueError('OWNED_PREVIEW_REGULAR_REQUIRED')

def save(path, value):
    # Only this fresh owned preview run. No formal/profile/source writes.
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def get_one(port, path, marker):
    started = time.monotonic()
    row = {'method': 'GET', 'path': path, 'redirect_followed': False}
    stage = 'request'
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', path, headers={'Host': '127.0.0.1:' + str(port), 'Connection': 'close'})
        stage = 'headers'
        left = 5 - (time.monotonic() - started)
        if left <= 0: raise TimeoutError()
        if connection.sock is not None: connection.sock.settimeout(left)
        response = connection.getresponse(); row['http_status'] = response.status
        if time.monotonic() - started > 5: raise TimeoutError()
        response_socket = connection.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
        if response_socket is None: raise RuntimeError('PREVIEW_RESPONSE_SOCKET_UNAVAILABLE')
        parts = []; size = 0; stage = 'body'
        # read1 closes fp at Content-Length exhaustion; never reset a retired socket.
        while not response.isclosed():
            left = 5 - (time.monotonic() - started)
            if left <= 0: raise TimeoutError()
            response_socket.settimeout(min(1, left))
            chunk = response.read1(65536)
            if time.monotonic() - started > 5: raise TimeoutError()
            if not chunk: break
            size += len(chunk)
            if size > 2 * 1024 * 1024: raise ValueError('PREVIEW_GET_BODY_BOUND')
            parts.append(chunk)
        if response.length not in (None, 0):
            raise ValueError('PREVIEW_GET_INCOMPLETE_BODY')
        stage = 'decode'
        body = b''.join(parts)
        row.update(body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest(),
                   content_type=response.getheader('Content-Type'))
        row['marker_present'] = marker in body.decode('utf-8', errors='replace') if marker is not None else None
        row['passed'] = response.status == 200 and (marker is None or row['marker_present'])
        value = json.loads(body) if marker is None and response.status == 200 else None
        # Fixed safe public metadata only; never save raw task/health body.
        if isinstance(value, dict):
            row['public_fields'] = {k: value[k] for k in
                ('ok', 'environment', 'version', 'manifest_digest', 'worker_enabled',
                 'business_execution_enabled', 'execution_mode', 'identity_only') if k in value}
            if path == '/api/health': row['source_commit'] = value.get('commit')
            if path == '/api/orbit/operations-runtime':
                row['worker_running'] = (value.get('dispatcher') or {}).get('running')
                row['preparation_running'] = (value.get('new_task_preparation') or {}).get('running')
                row['native_decision_installed'] = (value.get('native_decision_execution') or {}).get('installed')
            if path.startswith('/api/catalog/skus'):
                items=value.get('items');row['catalog_total']=value.get('total');row['catalog_limit']=value.get('limit')
                if not isinstance(items,list) or len(items)>100:raise RuntimeError('CATALOG_ITEMS_BOUND')
                row['catalog_items']=[{'entity_key':v.get('entity_key'),'sku':v.get('sku'),'image_key':v.get('image_key'),'cost_layers':len(v.get('cost',{}).get('choices',[])) if isinstance(v.get('cost'),dict) else None,'cost_object_shape':isinstance(v.get('cost'),dict) and 'amount' in v['cost'],'cost_status':v.get('cost',{}).get('status') if isinstance(v.get('cost'),dict) else None} for v in items]
            if path == '/api/orbit/tasks': row['task_count'] = len(value.get('tasks', [])) if isinstance(value.get('tasks'), list) else None
    except Exception as error:
        row.update(passed=False, error_type=type(error).__name__, error_code='PREVIEW_GET_FAILED', error_stage=stage)
        number = getattr(error, 'errno', None)
        if isinstance(number, int) and not isinstance(number, bool) and -65536 <= number <= 65536:
            row['error_errno'] = number
    finally:
        connection.close()
    row['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return row

def job_api():
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong), ('flags', wintypes.DWORD),
            ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
            ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', BasicLimits), ('io', IO), ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
            ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in ('user', 'kernel', 'period_user', 'period_kernel')] + [(name, wintypes.DWORD) for name in ('faults', 'total', 'active', 'terminated')]
    for name, args, result in (
        ('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ('SetInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        ('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ('QueryInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        ('TerminateJobObject', [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL)):
        f = getattr(k, name); f.argtypes = args; f.restype = result
    def ok(value):
        if not value: raise ctypes.WinError(ctypes.get_last_error())
    job = k.CreateJobObjectW(None, None); ok(job)
    limits = ExtendedLimits(); limits.basic.flags = 0x2000
    try: ok(k.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
    except BaseException: k.CloseHandle(job); raise
    def active():
        v = Accounting(); ok(k.QueryInformationJobObject(job, 1, ctypes.byref(v), ctypes.sizeof(v), None)); return int(v.active)
    def pids():
        cap = 128
        while cap <= 8192:
            class IDs(ctypes.Structure):
                _fields_ = [('assigned', wintypes.DWORD), ('count', wintypes.DWORD), ('ids', ctypes.c_size_t * cap)]
            value = IDs()
            if k.QueryInformationJobObject(job, 3, ctypes.byref(value), ctypes.sizeof(value), None):
                return [int(x) for x in value.ids[:value.count]]
            if ctypes.get_last_error() != 234: raise ctypes.WinError(ctypes.get_last_error())
            cap *= 2
        raise RuntimeError('OWNED_PREVIEW_PID_BOUND')
    return k, job, ok, active, pids


OPTIONAL_GETS=[('/', '任务工作台'),('/catalog','商品目录'),('/product-workspace','publicationFlowReview'),
('/profit','利润中心'),('/profit-original/artifacts/profit_reports_monthly/2026-07/index.html','2026-07'),
('/knowledge','知识工具'),('/supply-chain/','逐 SKU'),('/api/orbit/tasks',None)]
REQUIRED_GETS=[('/api/health',None),('/api/orbit/operations-runtime',None)]

FIXED_START_CODES=frozenset(('STEADY_READY_MISSING_OR_STALE','STEADY_READY_IDENTITY','CATALOG_ITEMS_BOUND','CATALOG_DEFAULT_50_OR_COUNT_DRIFT','CATALOG_0001_UNIQUE_COST_LAYER_MISSING','RETURNED_ORIGINAL_CACHED_IMAGE_REQUIRED','ORIGINAL_CACHED_IMAGE_NOT_SERVED_EXACT','STEADY_REQUIRED_GET_FAILED','STEADY_RUNTIME_IDENTITY','STEADY_NATIVE_SCOPE_NOT_INSTALLED','FALLBACK_CAPABILITY_DRIFT','STEADY_STOP_IDENTITY'))


def read_optional_components(cache, get=None):
    """Observe business surfaces without granting authority or retiring a healthy owner."""
    get = get_one if get is None else get
    checks=[];readiness={};snapshot={}
    def request(path,marker):
        try:row=get(49289,path,marker)
        except Exception as error:
            row={'method':'GET','path':path,'passed':False,'error_type':type(error).__name__,'error_code':'COMPONENT_OBSERVATION_FAILED'}
        # Persist only bounded public observations, never the response or per-product rows.
        checks.append({k:row[k] for k in ('method','path','redirect_followed','http_status','passed',
                      'error_type','error_code','error_stage','error_errno','elapsed_seconds',
                      'body_bytes','body_sha256','content_type','marker_present') if k in row})
        readiness[path]={'status':'AVAILABLE' if row.get('passed') is True else 'DEGRADED',
                         'error_code':None if row.get('passed') is True else row.get('error_code','COMPONENT_READ_FAILED')}
        return row
    def failed(component,code,error=None):
        readiness[component]={'status':'DEGRADED','error_code':code}
        if error is not None:readiness[component]['error_type']=type(error).__name__
    for path,marker in OPTIONAL_GETS:request(path,marker)
    directory=request('/api/catalog/skus',None);items=directory.get('catalog_items',[])
    total=directory.get('catalog_total')
    if (directory.get('passed') is not True or not isinstance(items,list) or type(total) is not int
        or total<len(items) or directory.get('catalog_limit')!=50 or len(items)!=min(total,50)
        or any(not isinstance(v.get('entity_key'),str) or not v['entity_key'] for v in items)
        or len({v['entity_key'] for v in items})!=len(items)):
        failed('catalog','CATALOG_STRUCTURE_UNAVAILABLE');items=[]
    else:
        readiness['catalog']={'status':'AVAILABLE','error_code':None}
        snapshot.update(count=total,default_page_items=len(items))
    single=request('/api/catalog/skus?q=0001',None)
    merged=[v for v in single.get('catalog_items',[]) if v.get('sku')=='0001']
    snapshot['exact_0001_entity_count']=len(merged)
    if single.get('passed') is not True or len(merged)!=1 or merged[0].get('cost_object_shape') is not True:
        failed('historical_sku_0001','HISTORICAL_SKU_OR_COST_UNAVAILABLE')
    else:
        readiness['historical_sku_0001']={'status':'AVAILABLE','error_code':None}
        snapshot.update(exact_0001_cost_choice_count=merged[0].get('cost_layers'),exact_0001_cost_status=merged[0].get('cost_status'))
    chosen=None
    try:
        # Only existing registered local bytes; never manufacture a key or fetch a CDN image.
        for item in items:
            key=item.get('image_key')
            if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):continue
            binary=cache/(key+'.bin');meta=cache/(key+'.json')
            if not binary.exists() or not meta.exists():continue
            regular(binary);regular(meta)
            if not 0<binary.stat().st_size<=2*1024*1024:continue
            mime=json.loads(meta.read_bytes()).get('mime')
            if mime not in ('image/png','image/jpeg','image/webp','image/avif'):continue
            chosen=(key,digest(binary),mime);break
        if chosen is None:failed('cached_image','LOCAL_REGISTERED_IMAGE_UNAVAILABLE')
        else:
            image=request('/api/catalog/image?key='+chosen[0],'')
            if image.get('passed') is not True or image.get('body_sha256')!=chosen[1] or image.get('content_type','').split(';')[0]!=chosen[2]:
                failed('cached_image','ORIGINAL_CACHED_IMAGE_NOT_SERVED_EXACT')
            else:
                readiness['cached_image']={'status':'AVAILABLE','error_code':None}
                snapshot.update(cached_image_key=chosen[0],cached_sha256=chosen[1])
    except Exception as error:failed('cached_image','LOCAL_IMAGE_OBSERVATION_FAILED',error)
    snapshot['cdn_probe']=False
    return {'checks':checks,'readiness':readiness,'catalog_readback':snapshot}

def steady_owner(seal_path, seal_sha, fallback=False):
    s=sealed(Path(seal_path),seal_sha)
    expected_head=s['source_head'];expected_root=Path(s['source_root'])
    python=Path(s['supervisor_python_path'])
    config=Path(s['config']['path']);pin(config,s['config']['sha256'])
    if not sys.flags.isolated or not sys.dont_write_bytecode:raise RuntimeError('ISOLATION_BYTECODE_REQUIRED')
    if not isinstance(expected_head,str) or len(expected_head)!=40 or any(c not in '0123456789abcdef' for c in expected_head) or s['port']!=49289:raise RuntimeError('STEADY_IDENTITY')
    for p,v in s['pins'].items():pin(p,v)
    d=json.loads(config.read_bytes())
    if d['code_version']!=expected_head or d['port']!=49289:raise RuntimeError('SCOPED_CONFIG')
    if not fallback and d['native_service_scope']!='explicit-new-task-and-decision/v1':raise RuntimeError('SCOPED_CONFIG')
    if fallback and d['execution_mode']!='web-only':raise RuntimeError('FALLBACK_WEB_ONLY_CONFIG')
    if Path(sys.executable).resolve()!=Path(s['supervisor_python_path']).resolve():raise RuntimeError('FIXED_PYTHON')
    # Deliberately no preview audit hook. Original explicit-task/lease/provider contracts govern business actions.
    root=Path(s['owner_output_root']); regular(root,directory=True)
    run=root/('steady-owner-'+uuid.uuid4().hex);run.mkdir()
    r={'status':'STARTING','run':str(run),'port':49289,'source_head':expected_head,'supervisor_pid':os.getpid(),'installation_token':s['installation_token'],'started_utc':time.time(),'mode':'FALLBACK_READONLY_NO_BACKUP_NO_DDL_NO_QUIET' if fallback else 'STEADY_ONLY_NO_BACKUP_NO_DDL_NO_QUIET'}
    save(run/'owner.json',r)
    # Publish the unique STARTING owner before any child START or GET. Failure retirement sees this installation.
    locator=root/'current-owner.json'
    if locator.exists():regular(locator)
    temporary=root/('current-owner-'+uuid.uuid4().hex+'.tmp')
    save_new(temporary,{'run':str(run),'owner_path':str(run/'owner.json'),'supervisor_pid':os.getpid(),'source_head':expected_head,'port':49289,'installation_token':s['installation_token']})
    os.replace(temporary,locator)
    try:k,job,ok,active,pids=job_api()
    except BaseException as e:
        r.update(status='STEADY_PRE_JOB_FAILED',job_created=False,child_started=False,error_type=type(e).__name__)
        save(run/'owner.json',r);raise
    child=None;started=False
    r.update(job_created=True,child_started=False)
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1);probe.bind(('127.0.0.1',49289))
        bootstrap="import sys,subprocess;token=sys.stdin.buffer.readline();sys.exit(subprocess.call(sys.argv[1:]) if token==b'START\\n' else 125)"
        argv=([str(python),'-I','-B','-X','utf8',s['fallback_wrapper']['path']] if fallback else [str(python),'-I','-B','-X','utf8',str(expected_root/'scripts/start_operations_web.py'),'--deployment',str(config)])
        with (run/'service.log').open('x',encoding='utf-8') as log:
            child=subprocess.Popen([str(python),'-I','-B','-S','-c',bootstrap,*argv],cwd=expected_root,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            r['bootstrap_pid']=child.pid;r['child_started']=True
            try:ok(k.AssignProcessToJobObject(job,wintypes.HANDLE(int(child._handle))))
            except BaseException:
                child.terminate();child.wait(timeout=5);r['assignment_failed_before_START']=True;raise
            r['job_enrolled_before_START']=True;r['active_before_START']=active()
            child.stdin.write(b'START\n');child.stdin.flush();child.stdin.close();started=True
            # Readiness is bounded, lifetime is persistent and owned by the ScheduledTask instance.
            deadline=time.monotonic()+90
            ready=Path(d['ready_path']);declared=None
            while child.poll() is None and time.monotonic()<deadline:
                if ready.exists():
                    regular(ready);candidate=json.loads(ready.read_bytes())
                    if candidate.get('pid') in pids():declared=candidate;break
                time.sleep(.25)
            if not declared:raise RuntimeError('STEADY_READY_MISSING_OR_STALE')
            if declared.get('version')!=expected_head or declared.get('port')!=49289 or Path(declared.get('code_root','')).resolve()!=expected_root.resolve():raise RuntimeError('STEADY_READY_IDENTITY')
            r['ready_pid']=declared['pid'];r['enrolled_pids_at_startup']=pids()
            r['effective_allowed_posts']=[] if fallback else None
            if fallback:r['route_wrapper_sha256']=s['fallback_wrapper']['sha256']
            rows=[get_one(49289,p,m) for p,m in REQUIRED_GETS]
            health=next(x for x in rows if x['path']=='/api/health')
            operations=next(x for x in rows if x['path']=='/api/orbit/operations-runtime')
            if not all(x['passed'] for x in rows) or health.get('source_commit')!=expected_head:raise RuntimeError('STEADY_REQUIRED_GET_FAILED')
            fields=operations.get('public_fields',{})
            if fields.get('version')!=expected_head or fields.get('manifest_digest')!=d['manifest_digest']:raise RuntimeError('STEADY_RUNTIME_IDENTITY')
            if not fallback and operations.get('native_decision_installed') is not True:raise RuntimeError('STEADY_NATIVE_SCOPE_NOT_INSTALLED')
            if fallback and (fields.get('worker_enabled') is not False or fields.get('business_execution_enabled') is not False):raise RuntimeError('FALLBACK_CAPABILITY_DRIFT')
            components=read_optional_components(Path(d['operations_data_root'])/'image-cache')
            r.update(status='RUNNING_FORMAL_READONLY_FALLBACK_OWNER' if fallback else 'RUNNING_FORMAL_SCOPED_OWNER',get_checks=rows,active_at_review=active(),allowed_business_scope='Original explicit-new-task-and-decision/v1 only; no history scan or probe POST')
            r.update(component_checks=components['checks'],component_readiness=components['readiness'],
                     catalog_readback=components['catalog_readback'],component_observed_utc=time.time(),
                     service_health='HEALTHY',business_components_degraded=any(v['status']!='AVAILABLE' for v in components['readiness'].values()))
            save(run/'owner.json',r)
            # Exact current locator; each actual owner remains uniquely named, historical directories preserved.
            while child.poll() is None:
                stop=run/'STOP.json'
                if stop.exists():
                    regular(stop)
                    if json.loads(stop.read_bytes())!={'run':str(run),'supervisor_pid':os.getpid(),'port':49289}:raise RuntimeError('STEADY_STOP_IDENTITY')
                    r['stop_requested']=True;break
                time.sleep(.25)
    except BaseException as e:
        r.update(status='STEADY_START_FAILED',error_type=type(e).__name__,error_code=str(e) if isinstance(e,RuntimeError) and str(e) in FIXED_START_CODES else 'STEADY_UNCLASSIFIED_FAILURE',get_checks=rows if 'rows' in locals() else []);raise
    finally:
        errors=[]
        def attempt(label,fn):
            try:return fn()
            except BaseException as e:errors.append({'stage':label,'error_type':type(e).__name__});return None
        r['active_before_cleanup']=attempt('active_before',active)
        if r['active_before_cleanup']!=0:attempt('terminate_owned_job',lambda:ok(k.TerminateJobObject(job,124)))
        if child is not None and child.poll() is None:
            def await_child():
                if not started:child.terminate()
                child.wait(timeout=5)
            attempt('await_child',await_child)
        deadline=time.monotonic()+5;remaining=attempt('active_after',active)
        while remaining and time.monotonic()<deadline:
            time.sleep(.05);remaining=attempt('active_after',active)
        exited=child is None or child.poll() is not None
        r.update(active_at_release=remaining,child_exit_confirmed=exited,child_exit_code=child.poll() if child is not None else None,cleanup_errors=errors)
        r['status']='STEADY_RETIRED' if remaining==0 and exited else 'RETIREMENT_UNKNOWN'
        persisted=attempt('persist_retirement',lambda:save(run/'owner.json',r))
        if remaining!=0 or not exited or any(x['stage']=='persist_retirement' for x in errors):
            r.update(status='RETIREMENT_UNKNOWN',strong_refs_retained=True);attempt('persist_unknown',lambda:save(run/'owner.json',r))
            while True:
                try:
                    if active()==0 and (child is None or child.poll() is not None):
                        r.update(status='STEADY_RETIRED_AFTER_UNKNOWN',active_at_release=0,child_exit_confirmed=True);save(run/'owner.json',r);break
                    time.sleep(1)
                except BaseException:time.sleep(1)
        ok(k.CloseHandle(job))

if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1] not in ('--steady-scoped-service-only','--steady-readonly-fallback-only'):raise SystemExit('FIXED_STEADY_SEAL_ARGV_REQUIRED')
    steady_owner(sys.argv[2],sys.argv[3],fallback=sys.argv[1]=='--steady-readonly-fallback-only')
