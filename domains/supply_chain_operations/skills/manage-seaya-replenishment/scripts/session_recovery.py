"""Explicit session recovery; CLI is inspection-only, never loads credentials.

execute requires a frozen plan and a separately supplied authorization receipt.
No default settings/auth imports, discovery, orders, or retry are permitted.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[5]
SOURCE_FILES=('core/http_retry.py','core/api_client.py','core/config.py','modules/shopee/sign.py','domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts/session_recovery.py')
SHOPS={'MY':1561117812,'TH':1561124013,'VN':1723948773,'PH':1527371343}

class RecoveryError(Exception):
    """Contains only a fixed stage label, never a provider exception."""

def require(ok, code):
    if not ok:raise RecoveryError(code)

def _load(name, relative):
    spec=importlib.util.spec_from_file_location(name,ROOT/relative)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

# Bind actual source locations: an earlier D import must not silently replace R.
for name in ('core','core.config','core.http_retry','core.api_client'):
    loaded=sys.modules.get(name)
    if loaded and getattr(loaded,'__file__',None):
        require(Path(loaded.__file__).resolve().is_relative_to(ROOT),'SOURCE_ROOT_MISMATCH')
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
http=_load('u04_verified_http','core/http_retry.py')
sp_sign=_load('u04_shopee_sign','modules/shopee/sign.py')
tk_sign=_load('u04_tiktok_sign','core/api_client.py').sign

ENDPOINTS={
 'tiktok_refresh':('GET','https://auth.tiktok-shops.com','/api/v2/token/refresh'),
 'tiktok_identity':('GET','https://open-api.tiktokglobalshop.com','/authorization/202309/shops'),
 'shopee_refresh':('POST','https://partner.shopeemobile.com','/api/v2/auth/access_token/get'),
 'shopee_identity':('GET','https://partner.shopeemobile.com','/api/v2/shop/get_shop_info'),
}

def source_binding():
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}

def version(path):
    s=Path(path).stat();return {'size':s.st_size,'mtime_ns':s.st_mtime_ns,'inode':s.st_ino}

def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def prepare(*,path,provider,shop_id=None,country=None,journal=None,run_id):
    """Metadata only: no credential read, write, ACL change, or network."""
    path=Path(path).absolute()
    require(provider in ('tiktok','shopee') and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',run_id or ''),'PLAN_INVALID')
    require(provider!='shopee' or (type(shop_id) is int and SHOPS.get(country)==shop_id),'TARGET_INVALID')
    expected_journal=path.with_name(path.name+f'.recovery-state-{shop_id if provider=="shopee" else "tiktok"}.json')
    require(journal is None or Path(journal).absolute()==expected_journal,'JOURNAL_PATH_INVALID')
    return {'schema':'u04-session-recovery/v1','source_root':str(ROOT),'sources':source_binding(),'path':str(path),'expected_version':version(path),'provider':provider,'shop_id':shop_id,'country':country,'journal':str(expected_journal),'run_id':run_id,'max_attempts':2,'mode':'prepare'}

def current_sid():
    """Windows token SID, no credentials. Used only during explicit execution."""
    from ctypes import wintypes as w
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=w.HANDLE
    adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
    handle=w.HANDLE();require(adv.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(handle)),'PROTECTION_UNVERIFIED')
    try:
        size=w.DWORD();adv.GetTokenInformation(handle,1,None,0,ctypes.byref(size));buf=ctypes.create_string_buffer(size.value)
        require(adv.GetTokenInformation(handle,1,buf,size,ctypes.byref(size)),'PROTECTION_UNVERIFIED')
        return _sid_string(ctypes.c_void_p.from_buffer(buf).value)
    finally:kernel.CloseHandle(handle)

def _sid_string(sid):
    from ctypes import wintypes as w
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    adv.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.LPWSTR)]
    text=w.LPWSTR();require(adv.ConvertSidToStringSidW(sid,ctypes.byref(text)),'PROTECTION_UNVERIFIED')
    try:return text.value
    finally:kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))

def protected(path):
    """Verify existing private ACL/mode; never creates or relaxes permissions."""
    path=Path(path)
    for part in (path,*path.parents):
        st=part.lstat();require(not stat.S_ISLNK(st.st_mode) and not (getattr(st,'st_file_attributes',0)&0x400),'REPARSE_FORBIDDEN')
    if os.name!='nt':
        st=path.stat();require(st.st_uid==os.getuid() and not st.st_mode&0o077,'PROTECTION_UNVERIFIED');return
    from ctypes import wintypes as w
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes=[w.LPWSTR,ctypes.c_int,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    adv.GetAce.argtypes=[ctypes.c_void_p,w.DWORD,ctypes.POINTER(ctypes.c_void_p)]
    owner=ctypes.c_void_p();dacl=ctypes.c_void_p();sd=ctypes.c_void_p()
    require(adv.GetNamedSecurityInfoW(str(path),1,5,ctypes.byref(owner),None,ctypes.byref(dacl),None,ctypes.byref(sd))==0,'PROTECTION_UNVERIFIED')
    try:
        user=current_sid();require(_sid_string(owner.value)==user and bool(dacl.value),'PROTECTION_UNVERIFIED')
        count=ctypes.c_ushort.from_address(dacl.value+4).value;require(count>0,'PROTECTION_UNVERIFIED')
        for index in range(count):
            ace=ctypes.c_void_p();require(adv.GetAce(dacl,index,ctypes.byref(ace)),'PROTECTION_UNVERIFIED')
            # Only simple allow ACEs to the user/system/admin are accepted.
            require(ctypes.c_ubyte.from_address(ace.value).value==0,'PROTECTION_UNVERIFIED')
            require(_sid_string(ace.value+8) in {user,'S-1-5-18','S-1-5-32-544'},'PROTECTION_UNVERIFIED')
    finally:kernel.LocalFree(sd)

def _write_new(path, raw):
    protected(path.parent)
    with open(path,'xb') as stream:
        if os.name!='nt':os.chmod(path,0o600)
        protected(path);stream.write(raw);stream.flush();os.fsync(stream.fileno())

@contextmanager
def _reserve(path):
    """Create once, verify protection while empty, retain the exact file handle."""
    protected(path.parent)
    if os.name=='nt':
        from ctypes import wintypes as w
        import msvcrt
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
        kernel.CreateFileW.restype=w.HANDLE
        # CREATE_NEW, read/write/DELETE; deny other writers and deleters.
        handle=kernel.CreateFileW(str(path),0xC0010000,1,None,1,0x80,None)
        require(handle!=ctypes.c_void_p(-1).value,'OUTPUT_RESERVATION_FAILED')
        try:fd=msvcrt.open_osfhandle(handle,os.O_RDWR|os.O_BINARY)
        except Exception:
            kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle(handle);raise
        stream=os.fdopen(fd,'w+b')
    else:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);stream=os.fdopen(fd,'w+b')
    try:
        protected(path)
        info=os.fstat(stream.fileno())
        reservation={'path':path,'stream':stream,'identity':(info.st_dev,info.st_ino)}
        _bound(reservation)
        # Probe flush/fsync before dispatch too; disk failures can still occur later.
        stream.flush();os.fsync(stream.fileno())
        yield reservation
    finally:stream.close()

def _bound(reservation, path=None):
    path=reservation['path'] if path is None else path
    info=path.stat();opened=os.fstat(reservation['stream'].fileno())
    require((info.st_dev,info.st_ino)==reservation['identity']==(opened.st_dev,opened.st_ino),'OUTPUT_BINDING_CHANGED')

def _write_reserved(reservation, raw):
    _bound(reservation);protected(reservation['path'])
    stream=reservation['stream'];require(os.fstat(stream.fileno()).st_size==0,'OUTPUT_NOT_EMPTY')
    stream.write(raw);stream.flush();os.fsync(stream.fileno())

def _rename_info(destination):
    from ctypes import wintypes as w
    encoded=str(destination.absolute()).encode('utf-16-le')
    units=len(encoded)//2
    require(ctypes.sizeof(w.WCHAR)==2 and units<32767,'DESTINATION_ENCODING_UNSUPPORTED')
    class RenameInfo(ctypes.Structure):
        _fields_=[('ReplaceIfExists',w.BYTE),('RootDirectory',w.HANDLE),('FileNameLength',w.DWORD),('FileName',w.WCHAR*(units+1))]
    info=RenameInfo();info.ReplaceIfExists=1;info.FileNameLength=len(encoded)
    ctypes.memmove(ctypes.addressof(info)+RenameInfo.FileName.offset,encoded,len(encoded))
    return info  # zero-initialized trailing UTF-16 NUL stays inside the allocation

def _replace_reserved(reservation, destination):
    _bound(reservation)
    if os.name=='nt':
        from ctypes import wintypes as w
        import msvcrt
        require(reservation.get('destination')==destination,'DESTINATION_CHANGED')
        info=reservation['rename_info']
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.SetFileInformationByHandle.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        # Rename the retained handle itself, not a reopened source pathname.
        result=kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(reservation['stream'].fileno()),3,ctypes.byref(info),ctypes.sizeof(info))
        require(result,'ATOMIC_REPLACE_FAILED_'+str(ctypes.get_last_error()))
    else:os.replace(reservation['path'],destination)
    _bound(reservation,destination)

def _atomic(path, value):
    temp=path.with_name(path.name+'.next')
    _write_new(temp,json.dumps(value,sort_keys=True).encode())
    os.replace(temp,path)

@contextmanager
def locked(path):
    """Durable exclusion: orphan lock after crash requires reconciliation."""
    lock=path.with_name(path.name+'.recovery-lock')
    try:_write_new(lock,b'locked')
    except Exception:raise RecoveryError('LOCKED_OR_UNPROTECTED') from None
    try:yield
    finally:lock.unlink()

def _token(value):
    return type(value) is str and 8<=len(value)<=16384 and value.strip()==value and not any(c.isspace() for c in value)

def refresh_expiry_status(value):
    now=int(time.time())
    if type(value) is not int:return 'UNKNOWN'
    if value<=now:return 'LOCAL_EXPIRED_NOT_PROVIDER_VERIFIED'
    if value>now+366*86400:return 'UNKNOWN_IMPLAUSIBLE_SAVED_EXPIRY'
    return 'LOCAL_UNEXPIRED_NOT_PROVIDER_VERIFIED'

def _read_json(raw):
    require(len(raw)<=1024*1024,'RESPONSE_TOO_LARGE')
    def pairs(items):
        out={}
        for k,v in items:
            require(k not in out,'JSON_INVALID');out[k]=v
        return out
    try:value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(RecoveryError('JSON_INVALID')))
    except Exception:raise RecoveryError('JSON_INVALID') from None
    require(type(value) is dict,'JSON_INVALID');return value

def _request(action, entry, credentials, shop_id):
    method,host,path=ENDPOINTS[action];now=int(time.time())
    if action.startswith('tiktok'):
        require(_token(credentials.get('app_key')) and _token(credentials.get('app_secret')),'APP_CREDENTIALS_INVALID')
        query={'app_key':credentials['app_key']}
        if action.endswith('refresh'):query.update(app_secret=credentials['app_secret'],refresh_token=entry['refresh_token'],grant_type='refresh_token')
        else:
            query['timestamp']=str(now);query['sign']=tk_sign(path,query,credentials['app_secret'])
        body=None
    else:
        pid=credentials.get('partner_id');key=credentials.get('partner_key')
        require(type(pid) is int and pid>0 and _token(key),'APP_CREDENTIALS_INVALID')
        if action.endswith('refresh'):
            ts,sig=sp_sign.sign_partner(path,pid,key,now);body=json.dumps({'shop_id':shop_id,'refresh_token':entry['refresh_token'],'partner_id':pid}).encode();query={'partner_id':pid,'timestamp':ts,'sign':sig}
        else:
            ts,sig=sp_sign.sign_shop(path,pid,key,entry['access_token'],shop_id,now);body=None;query={'partner_id':pid,'timestamp':ts,'sign':sig,'access_token':entry['access_token'],'shop_id':shop_id}
    req=urllib.request.Request(host+path+'?'+urllib.parse.urlencode(query),data=body,method=method,headers={'Content-Type':'application/json'})
    if action=='tiktok_identity':req.add_header('x-tts-access-token',entry['access_token'])
    return req

def _send(action, req, state, journal):
    require(action in ENDPOINTS and action not in state['attempts'] and len(state['attempts'])<2,'ATTEMPT_ALREADY_SPENT')
    method,host,path=ENDPOINTS[action];url=urllib.parse.urlsplit(req.full_url)
    require(req.get_method()==method and url.scheme+'://'+url.netloc==host and url.path==path and not url.fragment,'ENDPOINT_FORBIDDEN')
    state['attempts'].append(action);state['state']='ATTEMPTED';_atomic(journal,state)
    try:
        with http.urlopen(req,timeout=30,attempts=1,allow_curl_fallback=False) as resp:
            require(getattr(resp,'status',None)==200,'HTTP_REJECTED')
            raw=resp.read(1024*1024+1)
        return _read_json(raw)
    except Exception:raise RecoveryError('REQUEST_OUTCOME_UNVERIFIED') from None

def _candidate(provider, old, data, shop_id):
    now=int(time.time())
    if provider=='tiktok':
        require(type(data.get('code')) is int and data['code']==0 and type(data.get('data')) is dict,'BUSINESS_REJECTED')
        data=data['data'];expiry=data.get('access_token_expire_in')
        if old.get('open_id') and 'open_id' in data:require(data['open_id']==old['open_id'],'SELLER_MISMATCH')
        require(type(expiry) is int and now+300<expiry<=now+366*86400,'ACCESS_EXPIRY_INVALID')
    else:
        require(not data.get('error'),'BUSINESS_REJECTED')
        require('shop_id' not in data or (type(data['shop_id']) is int and data['shop_id']==shop_id),'SHOP_MISMATCH')
        duration=data.get('expire_in');require(type(duration) is int and 300<duration<=366*86400,'ACCESS_EXPIRY_INVALID');expiry=now+duration
    require(_token(data.get('access_token')) and _token(data.get('refresh_token',old.get('refresh_token'))),'TOKEN_INVALID')
    new=dict(old);new.update(access_token=data['access_token'],refresh_token=data.get('refresh_token',old['refresh_token']))
    if provider=='tiktok':
        new.update(access_token_expire_in=expiry,saved_at=now)
        # Retain the provider's exact refresh-expiry value, even if implausible.
        if 'refresh_token_expire_in' in data:new['refresh_token_expire_in']=data['refresh_token_expire_in']
    else:new.update(expire_at=expiry,updated_at=now)
    return new

def _identity(provider, data, shop_id, country):
    if provider=='shopee':
        require(not data.get('error') and type(data.get('response')) is dict,'IDENTITY_UNVERIFIED');row=data['response']
        require(type(row.get('shop_id')) is int and row['shop_id']==shop_id and row.get('region')==country,'IDENTITY_MISMATCH')
        return {'shop_id':shop_id,'country':country}
    require(type(data.get('code')) is int and data['code']==0 and type(data.get('data')) is dict,'IDENTITY_UNVERIFIED')
    rows=data['data'].get('shops');require(type(rows) is list,'IDENTITY_UNVERIFIED')
    selected={}
    for row in rows:
        require(type(row) is dict,'IDENTITY_UNVERIFIED')
        region=row.get('region')
        if region not in ('MY','TH','VN','PH'):continue
        require(region not in selected and type(row.get('id')) is str and row['id'] and type(row.get('cipher')) is str and row['cipher'],'IDENTITY_AMBIGUOUS')
        selected[region]={'shop_id':row['id'],'cipher':row['cipher']}
    require(set(selected)=={'MY','TH','VN','PH'},'IDENTITY_INCOMPLETE')
    require(len({r['shop_id'] for r in selected.values()})==4 and len({r['cipher'] for r in selected.values()})==4,'IDENTITY_AMBIGUOUS')
    # This verifies candidates only. Operator approval of daily targets is separate.
    return {'candidates':selected,'selection_required':True}

def execute(plan, authorization, credentials):
    """Explicit API; no live CLI. Caller must obtain actual scope authorization."""
    require(type(authorization) is dict and authorization.get('auth_write') is True and authorization.get('plan_digest')==plan_digest(plan) and type(authorization.get('authority')) is str and authorization['authority'],'AUTHORIZATION_REQUIRED')
    require(plan.get('schema')=='u04-session-recovery/v1' and plan.get('mode')=='prepare' and plan.get('source_root')==str(ROOT) and plan.get('sources')==source_binding() and plan.get('max_attempts')==2,'SOURCE_OR_PLAN_CHANGED')
    path=Path(plan['path']);journal=Path(plan['journal']);provider=plan['provider'];sid=plan['shop_id']
    require(provider in ('tiktok','shopee') and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',plan.get('run_id','')) and journal==path.with_name(path.name+f'.recovery-state-{sid if provider=="shopee" else "tiktok"}.json'),'PLAN_INVALID')
    require(provider!='shopee' or (type(sid) is int and SHOPS.get(plan['country'])==sid),'TARGET_INVALID')
    protected(path.parent);protected(path)
    with locked(path), ExitStack() as outputs:
        require(version(path)==plan['expected_version'],'VERSION_CHANGED')
        state={'schema':'u04-session-journal/v1','plan_digest':plan_digest(plan),'state':'PREPARED','attempts':[]}
        # A durable journal is never reset by a new run ID or authorization.
        require(not journal.exists(),'EXISTING_RUN_RECONCILE')
        raw=path.read_bytes();store=_read_json(raw)
        old=store if provider=='tiktok' else (store.get('shops') or {}).get(str(sid),{})
        require(_token(old.get('refresh_token')),'REFRESH_UNAVAILABLE')
        state['refresh_expiry_status']=refresh_expiry_status(old.get('refresh_token_expire_in'))
        if provider=='shopee':require(old.get('shop_id')==sid and (store.get('sync_shop_ids') or {}).get(plan['country'])==sid,'LOCAL_TARGET_MISMATCH')
        req=_request(provider+'_refresh',old,credentials,sid)
        prefix=path.name+'.'+str(sid if provider=='shopee' else 'tiktok')+'.'+plan['run_id']
        backup=path.with_name(prefix+'.backup')
        candidate=path.with_name(prefix+'.candidate')
        temp=path.with_name(prefix+'.replacement')
        try:
            _write_new(backup,raw);require(backup.read_bytes()==raw,'BACKUP_UNVERIFIED')
            candidate_output=outputs.enter_context(_reserve(candidate))
            replacement_output=outputs.enter_context(_reserve(temp))
            if os.name=='nt':
                replacement_output.update(destination=path,rename_info=_rename_info(path))
        except Exception:raise RecoveryError('OUTPUT_STORAGE_NOT_READY') from None
        try:
            data=_send(provider+'_refresh',req,state,journal)
            # Persist only allowlisted refresh fields, never the raw response.
            payload=data.get('data') if provider=='tiktok' else data
            if type(payload) is dict:
                retained={k:payload[k] for k in ('access_token','refresh_token','access_token_expire_in','refresh_token_expire_in','expire_in','shop_id') if k in payload}
                _write_reserved(candidate_output,json.dumps(retained).encode())
            new=_candidate(provider,old,data,sid)
            updated=new if provider=='tiktok' else {**store,'shops':{**store['shops'],str(sid):new}}
            protected(path);require(version(path)==plan['expected_version'] and path.read_bytes()==raw,'VERSION_CHANGED')
            _write_reserved(replacement_output,json.dumps(updated,ensure_ascii=False).encode());_replace_reserved(replacement_output,path)
            protected(path);replacement_output['stream'].seek(0)
            require(_read_json(replacement_output['stream'].read())==updated,'SAVE_UNVERIFIED')
            state['state']='SAVED_IDENTITY_PENDING';_atomic(journal,state)
        except Exception as failure:
            state['state']='RECONCILIATION_REQUIRED' if state['attempts'] else 'LOCAL_PRECHECK_FAILED'
            state['failure_code']=str(failure) if type(failure) is RecoveryError and re.fullmatch('[A-Z_0-9]+',str(failure)) else 'LOCAL_OR_TRANSPORT_FAILURE'
            try:_atomic(journal,state)
            except Exception:pass  # Earlier ATTEMPTED journal still prevents replay.
            raise RecoveryError(state['state']) from None
        try:
            identity=_send(provider+'_identity',_request(provider+'_identity',new,credentials,sid),state,journal)
            result=_identity(provider,identity,sid,plan['country'])
            state['state']='TARGET_SELECTION_REQUIRED' if provider=='tiktok' else 'IDENTITY_VERIFIED'
            state['identity']=result;_atomic(journal,state);return state
        except Exception:
            state['state']='IDENTITY_UNVERIFIED'
            try:_atomic(journal,state)
            except Exception:pass
            raise RecoveryError('IDENTITY_UNVERIFIED') from None

def main():
    parser=argparse.ArgumentParser(description='Inspect a PUBLIC frozen plan only. No execution CLI.')
    parser.add_argument('--dry-run',type=Path,required=True);args=parser.parse_args()
    plan=json.loads(args.dry_run.read_text())
    require(plan.get('schema')=='u04-session-recovery/v1','PLAN_INVALID')
    print(json.dumps({'state':'DRY_RUN_ONLY','plan_digest':plan_digest(plan),'source_match':plan.get('source_root')==str(ROOT) and plan.get('sources')==source_binding(),'network_attempts':0,'credential_reads':0,'writes':0,'authorization_required':True}))

if __name__=='__main__':
    try:main()
    except Exception:print('{"state":"DRY_RUN_REJECTED"}');raise SystemExit(1)
