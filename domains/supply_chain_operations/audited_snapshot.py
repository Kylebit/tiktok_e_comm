"""Historical manual snapshot admission. No provider authority or capture pages.

The native importer accepts an explicitly pinned source checkout and normalized
inputs. It never reruns an aggregator, downloads media, or edits that checkout.
An audited marker is deliberately different from a captured COMPLETE marker.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path,_ignore_error
from stat import S_ISREG
import re
from datetime import datetime
import base64
import html
import mimetypes
import subprocess
from functools import lru_cache
from contextlib import contextmanager
import os
import time

REGIONS={'MY':'MY8803','TH':'TH8806','VN':'VN8805','PH':'PH8807'}
PREFIXES={'MY':'660','TH':'990','VN':'880','PH':'770'}
FIELDS=('stock','available','allocated','frozen','inbound')
FILES=('data.js','inbound-plan.js')
GRADE='AUDITED_MANUAL_SNAPSHOT'
SCHEMA='supply-chain-audited-snapshot/v1'
HEX=re.compile(r'[0-9a-f]{64}')
SOURCE_PATHS={
 'data':'domains/supply_chain_operations/dashboard/data.js',
 'inbound':'domains/supply_chain_operations/dashboard/inbound-plan.js',
 'receipt':'domains/supply_chain_operations/dashboard/refresh-20260930-manual.md',
 'inventory':'.tmp/seaya-inventory-20260930-manual.json',
 'orders':'.tmp/order-demand-20260930-manual.json',
}
CONSUMERS=('index.html','inbound-batches.html','app.js','inbound-timeline.js',
           'inbound-batches.js','transport-history.js','styles.css','audited-bootstrap.js','load-errors.js')
GAPS=('provider_page_receipts_not_retained','shop_account_identity_not_proven',
      'inbound_raw_list_not_retained','economics_not_refreshed')
ECONOMICS={'settlementOrders','settlementUnits','customerPayment','actualShippingFee',
           'economicsBasis','settlementSource','settlementEvidence'}

def require(value,message='audited manual snapshot unavailable'):
    if not value:raise ValueError(message)

def sha(raw):return hashlib.sha256(raw).hexdigest()

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()

def bounded_bytes(path,limit=4*1024*1024):
    from shared_platform.capability_runtime import checked_path
    path=checked_path(Path(path).parent,Path(path).name)
    return _bounded_file_bytes(path,limit)

def _bounded_file_bytes(path,limit):
    try:observed=path.stat()
    except OSError as error:
        # Match this runtime's Path.is_file missing/invalid-path contract only.
        if not _ignore_error(error):raise
        require(False)
    except ValueError:require(False)
    require(S_ISREG(observed.st_mode) and 0<observed.st_size<=limit)
    with path.open('rb') as stream:raw=stream.read(limit+1)
    require(0<len(raw)<=limit);return raw

def _bounded_root_bytes(root,relative,limit=4*1024*1024):
    # One complete original root-relative path check for this read. Never cache
    # paths or filesystem facts; each caller still reads and hashes all bytes.
    from shared_platform.capability_runtime import checked_path
    return _bounded_file_bytes(checked_path(root,relative),limit)

@contextmanager
def _shared_business_read_lock(root,business_digest,*,timeout=2):
    """Independent shared byte-zero handle, incompatible with the existing writer.

    This does not take the writer's process-local exclusive mutex. Every reader
    still validates all artifact bytes under a kernel lock on the identical file.
    """
    from modules.sourcing.image_generation_checkpoint import CheckpointRecoveryRequired
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    path=root/f'.lingshi-{business_digest[:24]}.lock'
    with path.open('a+b') as stream:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            import msvcrt
            class Overlapped(ctypes.Structure):
                _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),
                          ('Offset',wintypes.DWORD),('OffsetHigh',wintypes.DWORD),
                          ('hEvent',wintypes.HANDLE)]
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            lock=kernel.LockFileEx;unlock=kernel.UnlockFileEx
            lock.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,
                           wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
            unlock.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,
                             wintypes.DWORD,ctypes.POINTER(Overlapped)]
            lock.restype=unlock.restype=wintypes.BOOL
            handle=msvcrt.get_osfhandle(stream.fileno());position=Overlapped()
            def acquire():
                # FAIL_IMMEDIATELY, without EXCLUSIVE: shared offset 0, length 1.
                if not lock(handle,1,0,1,0,ctypes.byref(position)):
                    raise ctypes.WinError(ctypes.get_last_error())
            def release():
                if not unlock(handle,0,1,0,ctypes.byref(position)):
                    raise ctypes.WinError(ctypes.get_last_error())
            def contention(error):return error.winerror==33
        else:
            import errno,fcntl
            def acquire():fcntl.flock(stream.fileno(),fcntl.LOCK_SH|fcntl.LOCK_NB)
            def release():fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            def contention(error):return error.errno in (errno.EACCES,errno.EAGAIN)
        acquired=False;failure=None;deadline=time.monotonic()+timeout
        try:
            while True:
                try:acquire();acquired=True;break
                except OSError as error:
                    if not contention(error):raise
                    if time.monotonic()>=deadline:
                        raise CheckpointRecoveryRequired(path,'another writer holds this business identity') from error
                    time.sleep(min(.05,max(0,deadline-time.monotonic())))
            yield
        except BaseException as error:failure=error;raise
        finally:
            if acquired:
                try:release()
                except BaseException as error:
                    if failure is None:raise
                    failure.add_note(f'Read lock cleanup also failed: {type(error).__name__}: {error}')

def pairs(rows):
    result={}
    for key,value in rows:require(key not in result);result[key]=value
    return result

def loads(raw):
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:require(False))

def clock(value):
    require(type(value) is str)
    time=datetime.fromisoformat(value.replace('Z','+00:00'));require(time.tzinfo is not None)
    return time

def js_object(raw,variable):
    """Accept only an assignment of JSON with optional bare object keys.

    A lexical pass quotes keys outside strings; no JS, comments, functions,
    expressions, or unquoted values are executed or accepted.
    """
    text=raw.decode('utf-8').strip();prefix='window.'+variable+' = '
    require(text.startswith(prefix) and text.endswith(';'))
    text=text[len(prefix):-1];out=[];i=0
    while i<len(text):
        char=text[i]
        if char=='"':
            start=i;i+=1
            while i<len(text):
                if text[i]=='\\':i+=2;continue
                if text[i]=='"':i+=1;break
                i+=1
            else:require(False)
            out.append(text[start:i]);continue
        if char.isalpha() or char=='_':
            token=re.match(r'[A-Za-z_][A-Za-z_0-9]*',text[i:]);require(token is not None)
            word=token.group();end=i+len(word);cursor=end
            while cursor<len(text) and text[cursor].isspace():cursor+=1
            if cursor<len(text) and text[cursor]==':':out.append(json.dumps(word))
            else:require(word in {'true','false','null'});out.append(word)
            i=end;continue
        out.append(char);i+=1
    value=loads(''.join(out));require(type(value) is dict);return value

def _validate_pair_uncached(raws):
    require(set(raws)==set(SOURCE_PATHS))
    data=js_object(raws['data'],'SUPPLY_CHAIN_DATA')
    inbound=js_object(raws['inbound'],'SUPPLY_CHAIN_INBOUND_PLAN')
    inventory=loads(raws['inventory']);orders=loads(raws['orders'])
    require(type(inventory) is dict and set(inventory)=={'capturedAt','source','records'})
    require(type(inventory['records']) is list and inventory['records'])
    require(type(orders) is dict and orders.get('schemaVersion')=='order_demand_snapshot_v1')
    require(set(orders['countries'])==set(REGIONS) and type(orders['days']) is int and 30<=orders['days']<=366)
    require(set(data['countries'])==set(REGIONS) and data['quantityBasis']=='valid_order' and data['economicsBasis']=='settlement')
    clock(inventory['capturedAt']);clock(orders['capturedAt']);clock(inbound['capturedAt'])
    require(data['orderDemandCapturedAt']==orders['capturedAt'] and data['snapshotDate']==inventory['capturedAt'][:10])
    require(inbound['capturedAt']==inventory['capturedAt'] and set(inbound['regions'])==set(REGIONS))
    grouped={region:{} for region in REGIONS};counts={region:0 for region in REGIONS};seen=set()
    for record in inventory['records']:
        require(type(record) is dict and record['captured_at']==inventory['capturedAt'])
        region=next((r for r,w in REGIONS.items() if w==record['warehouse']),None);require(region is not None)
        seller=record['seller_sku'];require(type(seller) is str and seller.isdigit())
        require(len(seller)==4 or len(seller)==6 and seller.startswith(PREFIXES[region]))
        identity=(record['warehouse'],seller);require(identity not in seen);seen.add(identity);counts[region]+=1
        sku=seller[-4:];entry=grouped[region].setdefault(sku,{**{f:0 for f in FIELDS},'warehouse':record['warehouse'],'sourceAliases':[]})
        entry['sourceAliases'].append(seller)
        for field in FIELDS:
            value=record[field];require(type(value) is int and value>=0);entry[field]+=value
    coverage={}
    for region,warehouse in REGIONS.items():
        config=data['config'][region];require(config['warehouse']==warehouse)
        rows=grouped[region]
        for row in rows.values():row['sourceAliases']=sorted(row['sourceAliases'])
        evidence=config['inventoryEvidence']
        require(evidence['digest']==sha(encoded(rows)) and evidence['source']==inventory['source'])
        require(evidence['capturedAt']==inventory['capturedAt'] and type(evidence['rawRows']) is int
                and evidence['rawRows']==counts[region] and type(evidence['canonicalSkuCount']) is int
                and evidence['canonicalSkuCount']==len(rows))
        dashboard={row['sku']:row for row in data['countries'][region]}
        require(len(dashboard)==len(data['countries'][region]) and set(rows)<=set(dashboard))
        for sku,row in dashboard.items():
            require(type(sku) is str and re.fullmatch(r'[0-9]{4}',sku) and row['inventory']['warehouse']==warehouse)
            require(set(row['channels'])=={'tiktok','shopee'})
            expected=rows.get(sku,{f:0 for f in FIELDS})
            require(all(type(row['inventory'][f]) is int and row['inventory'][f]==expected[f] for f in FIELDS))
        require(set(orders['countries'][region])=={'tiktok','shopee'})
        order_coverage={}
        for platform in ('tiktok','shopee'):
            source=orders['countries'][region][platform];require(source['region']==region)
            require(source['platform']=={'tiktok':'TikTok','shopee':'Shopee'}[platform])
            require(source['captured_at']==orders['capturedAt'] and source['days']==orders['days'])
            require(source['digest']==sha(encoded({k:v for k,v in source.items() if k!='digest'})))
            actual=config['orderDemandEvidence'][platform];require(actual['digest']==source['digest'])
            counts_map={'orders_seen':'ordersSeen','orders_included':'ordersIncluded','orders_excluded':'ordersExcluded','item_lines_unresolved':'itemLinesUnresolved'}
            for key,display in counts_map.items():
                value=source['evidence'].get(key,0)
                require(type(value) is int and value>=0 and type(actual[display]) is int and actual[display]==value)
            require(actual['ordersIncluded']+actual['ordersExcluded']<=actual['ordersSeen'])
            require(set(source['facts'])<=set(dashboard))
            for sku,fact in source['facts'].items():
                require(fact['quantityBasis']=='valid_order' and fact['state']=='READY')
                require(fact['days']==orders['days'] and all(type(fact[k]) is int and fact[k]>=0 for k in ('units','orders','recent30Units','cancelledUnits','returnedUnits')))
                require(fact['recent30Units']<=fact['units'])
            for sku,row in dashboard.items():
                fact=source['facts'].get(sku)
                if fact is None:
                    # Exact existing zero-SKU projection, not arbitrary extra demand.
                    display={'tiktok':'TikTok','shopee':'Shopee'}[platform]
                    fact={'days':orders['days'],'orders':0,'units':0,'recent30Units':0,
                        'quantityBasis':'valid_order','eventTimeBasis':
                        'paid_time_preferred_confirmed_create_fallback' if platform=='tiktok' else 'create_time_confirmed_order',
                        'state':'READY','source':display+' '+region+' 有效订单','evidence':'complete_order_window_no_sku',
                        'sourceAliases':[],'cancelledUnits':0,'returnedUnits':0}
                expected={k:v for k,v in fact.items() if k not in {'name','imageUrl'}}
                channel=row['channels'][platform]
                require({k:v for k,v in channel.items() if k not in ECONOMICS}==expected)
            order_coverage[platform]=actual
        plan=inbound['regions'][region]
        # This first bounded display admission supports the audited no-active-batch case.
        require(plan=={'totalUnits':0,'allocationPolicy':'NO_ACTIVE_BATCH','batches':[]})
        require(all(row['inbound']==0 for row in rows.values()))
        coverage[region]={'warehouse':warehouse,'inventory_rows':counts[region],'canonical_skus':len(rows),
            'inventory_digest':evidence['digest'],'orders':order_coverage,'active_inbound_units':0}
    reconciliation=inbound['reconciliation'];require(reconciliation['activeBatchCount']==0 and reconciliation['activeUnits']==0)
    status=reconciliation['sourceStatusCounts'];require(all(type(v) is int and v>=0 for v in status.values()))
    require(status['all']==status['completed']+status['voided'] and all(status[k]==0 for k in ('pendingReview','inTransit','waitingForInbound','inboundProcessing')))
    for receipt in reconciliation.get('latestCompletedReceipts',[]):
        require(receipt['region'] in REGIONS and type(receipt['batchId']) is str and receipt['batchId'])
        require(clock(receipt['signedAt'])<=clock(receipt['shelvedAt'])<=clock(inventory['capturedAt']))
    require(raws['receipt'].decode('utf-8').strip())
    return data,{'evidence_grade':GRADE,'quantity_basis':'valid_order','economics_basis':'settlement',
       'inventory_captured_at':inventory['capturedAt'],'orders_captured_at':orders['capturedAt'],
       'inbound_captured_at':inbound['capturedAt'],'economics_status':'NOT_REFRESHED_CAPTURE_DATE_UNKNOWN',
       'coverage':coverage,'gaps':list(GAPS),'execution_authority':False}

@lru_cache(maxsize=2)
def _validated_pair_content(raw_items):
    # Only the pure transformation is shared. Every filesystem read/hash and
    # writer exclusion stays at its original call site; no stat/TTL cache exists.
    return encoded(_validate_pair_uncached(dict(raw_items)))

def validate_pair(raws):
    require(set(raws)==set(SOURCE_PATHS))
    if not all(type(raws[key]) is bytes for key in SOURCE_PATHS):
        return _validate_pair_uncached(raws)
    data,evidence=loads(_validated_pair_content(tuple((key,raws[key]) for key in SOURCE_PATHS)))
    return data,evidence

@lru_cache(maxsize=2)
def _validated_read_projection(raw_items):
    # Store reads need only these immutable derived facts. Full source bytes,
    # file hashes and paths are still reread by every caller of read_version.
    data,evidence=validate_pair(dict(raw_items))
    references=frozenset(row['image'] for rows in data['countries'].values() for row in rows)
    return references,encoded(evidence)

def git(root,*args):
    # Local object inspection only. Never fetch, execute source tools, or mutate Git.
    return subprocess.check_output(['git','-c','gc.auto=0','-C',str(root),*args])

def read_source(source_root,source_commit,expected):
    from shared_platform.capability_runtime import checked_path
    root=checked_path(Path(source_root),'.')
    require(type(source_commit) is str and re.fullmatch(r'[0-9a-f]{40}',source_commit))
    require(Path(git(root,'rev-parse','--show-toplevel').decode().strip()).resolve()==root)
    require(git(root,'rev-parse','HEAD').decode().strip()==source_commit)
    require(type(expected) is dict and set(expected)==set(SOURCE_PATHS)
            and all(type(v) is str and HEX.fullmatch(v) for v in expected.values()))
    raws={}
    for key,relative in SOURCE_PATHS.items():
        raw=bounded_bytes(checked_path(root,relative));require(sha(raw)==expected[key]);raws[key]=raw
        if key in {'data','inbound','receipt'}:
            require(git(root,'cat-file','blob',source_commit+':'+relative)==raw.replace(b'\r\n',b'\n'))
    data,evidence=validate_pair(raws)
    dashboard=checked_path(root,'domains/supply_chain_operations/dashboard')
    assets={}
    for rows in data['countries'].values():
        for row in rows:
            path=row['image'];require(type(path) is str and re.fullmatch(r'assets/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|gif)',path))
            if path in assets:continue
            require(len(assets)<512)
            raw=bounded_bytes(checked_path(dashboard,path));
            require(git(root,'cat-file','blob',source_commit+':domains/supply_chain_operations/dashboard/'+path)==raw)
            assets[path]=raw
    require(sum(len(raw) for raw in (*raws.values(),*assets.values()))<=64*1024*1024)
    require(git(root,'rev-parse','HEAD').decode().strip()==source_commit)
    return raws,assets,evidence

def import_snapshot(code_root,source_root,source_commit,expected,config):
    """Explicit native-only historical display import; no HTTP producer exists.

    expected binds actual input bytes selected by this work package. The manual
    receipt and Git author are provenance, never a user approval or provider sig.
    Consumer compatibility still needs its own browser acceptance.
    """
    from .captured_serving import capture_artifact_root
    from shared_platform.capability_runtime import checked_path,digest
    from modules.sourcing.image_generation_checkpoint import business_lock,atomic_json
    require(config.get('mode')==GRADE)
    selected={k:v for k,v in config.items() if k!='mode'}
    data_root,artifact=capture_artifact_root(code_root,selected)
    require('runtime_root' in selected)
    output=checked_path(artifact,selected['output_root'])
    require(output!=artifact and output.is_relative_to(artifact))
    supply=checked_path(Path(code_root),'domains/supply_chain_operations/dashboard')
    consumers={name:sha(bounded_bytes(checked_path(supply,name))) for name in CONSUMERS}
    raws,assets,evidence=read_source(source_root,source_commit,expected)
    payload={'schema':SCHEMA,'evidence_grade':GRADE,'source_commit':source_commit,
      'source_files':{key:{'relative_path':path,'sha256':sha(raws[key])} for key,path in SOURCE_PATHS.items()},
      'provenance':{'kind':'ENGINEERING_SOURCE_BINDING','receipt_source':'source_files.receipt',
                    'user_approval':False,'provider_authenticity':'NOT_ESTABLISHED'},
      'pair_sha256':{'data.js':sha(raws['data']),'inbound-plan.js':sha(raws['inbound'])},
      'asset_sha256':{name:sha(raw) for name,raw in assets.items()},'consumer_sha256':consumers,
      'asset_reference_policy':'ROW_IMAGE_ONLY_PINNED_CONSUMERS_NO_REMOTE_MEDIA',
      'evidence':evidence}
    version=sha(encoded(payload));manifest={**payload,'version':version}
    scope=digest({'root':str(data_root).casefold(),'artifact':str(artifact).casefold()})
    with business_lock(artifact,scope,timeout=0):
        directory=checked_path(output,version);directory.mkdir(parents=True,exist_ok=True)
        bodies={**{FILES[i]:raws[k] for i,k in enumerate(('data','inbound'))},
                **assets,**{'inputs/'+key+'.source':raw for key,raw in raws.items()}}
        for relative,raw in bodies.items():
            path=checked_path(directory,relative);path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():require(bounded_bytes(path)==raw)
            else:
                with path.open('xb') as stream:stream.write(raw)
        marker=checked_path(directory,'audited.json');raw=encoded(manifest)
        if marker.exists():require(bounded_bytes(marker)==raw)
        else:
            with marker.open('xb') as stream:stream.write(raw)
        # Recheck exact artifacts before publishing the manual-only index.
        store=AuditedSnapshotStore(code_root,config);store.read_version(version,sha(raw))
        index_path=checked_path(artifact,'audited-serving.json')
        previous=store.index() if index_path.exists() else None
        entry={'version':version,'sha256':sha(raw)}
        old=[row for row in previous['versions'] if row['version']!=version] if previous else []
        if previous and previous['current']==version:require(next(row for row in previous['versions'] if row['version']==version)==entry)
        else:atomic_json(index_path,{'schema':'supply-chain-audited-index/v1','current':version,'versions':(old+[entry])[-32:]})
    return {'version':version,'evidence_grade':GRADE,'execution_authority':False,'external_writes_performed':[]}

class AuditedSnapshotStore:
    def __init__(self,root,config):
        from .captured_serving import capture_artifact_root
        from shared_platform.capability_runtime import checked_path
        require(type(config) is dict and set(config)=={'runtime_root','artifact_root','output_root','mode'} and config['mode']==GRADE)
        self.root,self.artifact=capture_artifact_root(root,{k:v for k,v in config.items() if k!='mode'})
        self.output=checked_path(self.artifact,config['output_root']);require(self.output!=self.artifact and self.output.is_relative_to(self.artifact))
        self.supply=checked_path(Path(root),'domains/supply_chain_operations/dashboard')

    def index(self):
        from shared_platform.capability_runtime import checked_path
        value=loads(_bounded_root_bytes(self.artifact,'audited-serving.json',128*1024))
        require(set(value)=={'schema','current','versions'} and value['schema']=='supply-chain-audited-index/v1')
        rows=value['versions'];require(type(rows) is list and 0<len(rows)<=32)
        require(all(type(row) is dict and set(row)=={'version','sha256'} and all(type(v) is str and HEX.fullmatch(v) for v in row.values()) for row in rows))
        require(len({row['version'] for row in rows})==len(rows) and value['current'] in {row['version'] for row in rows})
        return value

    def _read_bound_pair(self,version,expected_manifest_sha):
        from shared_platform.capability_runtime import checked_path
        require(type(version) is str and HEX.fullmatch(version))
        directory=checked_path(self.output,version)
        raw=_bounded_root_bytes(directory,'audited.json',128*1024);require(sha(raw)==expected_manifest_sha)
        manifest=loads(raw)
        require(set(manifest)=={'schema','evidence_grade','source_commit','source_files','provenance','pair_sha256','asset_sha256','consumer_sha256','asset_reference_policy','evidence','version'})
        require(manifest['schema']==SCHEMA and manifest['evidence_grade']==GRADE and manifest['version']==version)
        require(type(manifest['source_commit']) is str and re.fullmatch(r'[0-9a-f]{40}',manifest['source_commit']))
        require(manifest['asset_reference_policy']=='ROW_IMAGE_ONLY_PINNED_CONSUMERS_NO_REMOTE_MEDIA')
        require(sha(encoded({k:v for k,v in manifest.items() if k!='version'}))==version)
        require(encoded(manifest['provenance'])==encoded({'kind':'ENGINEERING_SOURCE_BINDING','receipt_source':'source_files.receipt','user_approval':False,'provider_authenticity':'NOT_ESTABLISHED'}))
        require(set(manifest['source_files'])==set(SOURCE_PATHS) and set(manifest['pair_sha256'])==set(FILES))
        require(set(manifest['consumer_sha256'])==set(CONSUMERS))
        for name,expected in manifest['consumer_sha256'].items():
            require(sha(_bounded_root_bytes(self.supply,name))==expected,
                    'AUDITED_CONSUMER_CHANGED: selected display code differs; reimport and verify browser compatibility')
        inputs={}
        for key,relative in SOURCE_PATHS.items():
            row=manifest['source_files'][key];require(set(row)=={'relative_path','sha256'} and row['relative_path']==relative)
            raw=_bounded_root_bytes(directory,'inputs/'+key+'.source');require(sha(raw)==row['sha256']);inputs[key]=raw
        references,evidence_raw=_validated_read_projection(tuple((key,inputs[key]) for key in SOURCE_PATHS))
        require(evidence_raw==encoded(manifest['evidence']))
        bodies={name:_bounded_root_bytes(directory,name) for name in FILES}
        require(bodies=={'data.js':inputs['data'],'inbound-plan.js':inputs['inbound']})
        require(all(sha(bodies[name])==manifest['pair_sha256'][name] for name in FILES))
        require(set(manifest['asset_sha256'])==references)
        require(all(type(expected) is str and HEX.fullmatch(expected) for expected in manifest['asset_sha256'].values()))
        return bodies,manifest,directory

    def read_version(self,version,expected_manifest_sha):
        # Full admission remains fresh, including every referenced image.
        bodies,manifest,directory=self._read_bound_pair(version,expected_manifest_sha)
        for name,expected in manifest['asset_sha256'].items():require(sha(_bounded_root_bytes(directory,name))==expected)
        return bodies,manifest,directory

    def current(self,version=None):
        index=self.index();version=version or index['current']
        entry=next((row for row in index['versions'] if row['version']==version),None);require(entry is not None)
        return self.read_version(version,entry['sha256'])

    def _versioned_resource(self,version,name):
        # A resource request revalidates the complete source/consumer binding,
        # then its own bytes. Unrelated image damage is caught by full admission.
        index=self.index()
        entry=next((row for row in index['versions'] if row['version']==version),None);require(entry is not None)
        bodies,manifest,directory=self._read_bound_pair(version,entry['sha256'])
        if name in FILES:return 200,bodies[name],'application/javascript; charset=utf-8'
        if name not in manifest['asset_sha256']:return 404,b'Unknown audited image.','text/plain; charset=utf-8'
        raw=_bounded_root_bytes(directory,name);require(sha(raw)==manifest['asset_sha256'][name])
        return 200,raw,mimetypes.guess_type(name)[0] or 'application/octet-stream'

    def response(self,suffix,supply):
        from .captured_serving import SCRIPT
        from shared_platform.capability_runtime import checked_path,digest
        scope=digest({'root':str(self.root).casefold(),'artifact':str(self.artifact).casefold()})
        # Read handles share the same byte range; existing writers remain excluded.
        # Full pages check all images; versioned resources check their own bytes
        # after fresh complete source/consumer binding. The read wait is unchanged.
        with _shared_business_read_lock(self.artifact,scope,timeout=2):
            if suffix in ('index.html','inbound-batches.html'):
                bodies,manifest,directory=self.current();version=manifest['version']
                text=_bounded_root_bytes(supply,suffix).decode();scripts=[]
                for match in SCRIPT.finditer(text):
                    src=match.group(1);filename=src.removeprefix('./').split('?')[0]
                    require(filename in {*FILES,'transport-history.js','inbound-timeline.js','app.js','inbound-batches.js'})
                    integrity=''
                    if filename in FILES:
                        src='/supply-chain/audited/'+version+'/'+filename
                        integrity='sha256-'+base64.b64encode(bytes.fromhex(manifest['pair_sha256'][filename])).decode()
                    else:
                        integrity='sha256-'+base64.b64encode(bytes.fromhex(manifest['consumer_sha256'][filename])).decode()
                    scripts.append({'src':src,'integrity':integrity})
                require(sum('/'+f in item['src'] for item in scripts for f in FILES)==2)
                payload=html.escape(json.dumps(scripts,separators=(',',':')),quote=True)
                bootstrap_integrity='sha256-'+base64.b64encode(bytes.fromhex(manifest['consumer_sha256']['audited-bootstrap.js'])).decode()
                bootstrap=f'<script src="./audited-bootstrap.js" integrity="{bootstrap_integrity}" data-audited-version="{version}" data-audited-scripts="{payload}"></script>'
                text,count=SCRIPT.subn('',text);require(count==len(scripts) and count>=4)
                e=manifest['evidence']
                unresolved='；'.join(region+' '+platform+' '+str(row['orders'][platform]['itemLinesUnresolved'])
                    for region,row in e['coverage'].items() for platform in ('tiktok','shopee') if row['orders'][platform]['itemLinesUnresolved'])
                banner='<section role="note" class="section" id="manualSnapshotEvidence"><strong>规范化历史快照 · 来源已核对</strong><p>'+html.escape(
                    f"库存 {e['inventory_captured_at']}；有效订单 {e['orders_captured_at']}；入库 {e['inbound_captured_at']}。结算沿用旧事实，捕获时点未知。未解析订单行已排除：{unresolved or '0'}。本页仅历史条件测算；原始分页和店铺身份未证明，不授予执行权限。")+'</p></section>'
                # Keep the original DOM, math and source data bytes. Only display URLs are projected.
                mains=list(re.finditer(r'<main(?:\s[^<>]*)?>',text))
                require(len(mains)==1 and text.count('</body>')==1,
                        'AUDITED_DISPLAY_TEMPLATE_CHANGED: source banner cannot be attached')
                at=mains[0].end();text=text[:at]+banner+text[at:]
                return 200,text.replace('</body>',bootstrap+'\n</body>',1).encode(),'text/html; charset=utf-8'
            if suffix in CONSUMERS:
                # Native fallback must not serve changed scripts/styles after page admission.
                _,manifest,_=self.current()
                raw=_bounded_root_bytes(supply,suffix)
                require(sha(raw)==manifest['consumer_sha256'][suffix],
                        'AUDITED_CONSUMER_CHANGED: selected display code differs; reimport and verify browser compatibility')
                ctype='text/css; charset=utf-8' if suffix.endswith('.css') else 'application/javascript; charset=utf-8'
                return 200,raw,ctype
            if suffix in FILES:
                self.current();return 409,b'A page must select one audited manual snapshot.','text/plain; charset=utf-8'
            if suffix.startswith('audited/'):
                match=re.fullmatch(r'audited/([0-9a-f]{64})/(data\.js|inbound-plan\.js|assets/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|gif))',suffix)
                if not match:return 404,b'Unknown audited resource.','text/plain; charset=utf-8'
                version,name=match.groups();return self._versioned_resource(version,name)
            if suffix.startswith('assets/'):
                # Images must be bound to the same immutable page generation.
                self.current();return 409,b'Use the audited image version selected by the page.','text/plain; charset=utf-8'
            if suffix.startswith('captured/'):
                self.current();return 404,b'Manual snapshots are not captured generations.','text/plain; charset=utf-8'
            return None
