import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import ssl
import urllib.error
import urllib.request
import pytest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts/session_recovery.py'

def module():
    spec=importlib.util.spec_from_file_location('u04_session',SCRIPT)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj

@pytest.fixture
def case(tmp_path):
    m=module();private=tmp_path/'private';private.mkdir()
    if os.name=='nt':
        sid=m.current_sid()
        subprocess.run(['icacls',str(private),'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F'],check=True,capture_output=True)
    else:private.chmod(0o700)
    old={'shops':{'1561117812':{'shop_id':1561117812,'access_token':'OLD-SYNTHETIC-SENTINEL','refresh_token':'REFRESH-SYNTHETIC-SENTINEL','expire_at':1},'OTHER':{'keep':['all','bytes-in-entry']}},'sync_shop_ids':{'MY':1561117812}}
    path=private/'fixture.json';path.write_text(json.dumps(old));path.chmod(0o600)
    journal=path.with_name(path.name+'.recovery-state-1561117812.json')
    plan=m.prepare(path=path,provider='shopee',shop_id=1561117812,country='MY',journal=journal,run_id='synthetic-run')
    approved={'plan_digest':m.plan_digest(plan),'authority':'synthetic-test-only','auth_write':True}
    return m,path,journal,plan,approved,old

def response(value):
    class Reply(io.BytesIO):
        status=200
    return Reply(json.dumps(value).encode())

def test_recovers_only_selected_entry(case,monkeypatch):
    m,path,journal,plan,approved,old=case;calls=[]
    def once(req,**kw):
        calls.append(req.get_method())
        if req.get_method()=='POST':return response({'access_token':'NEW-SYNTHETIC-SENTINEL','refresh_token':'ROTATED-SYNTHETIC-SENTINEL','expire_in':14400,'shop_id':1561117812})
        return response({'response':{'shop_id':1561117812,'region':'MY','shop_name':'Synthetic'}})
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    result=m.execute(plan,approved,{'partner_id':123,'partner_key':'KEY-SYNTHETIC-SENTINEL'})
    new=json.loads(path.read_text());assert new['shops']['OTHER']==old['shops']['OTHER'] and new['sync_shop_ids']==old['sync_shop_ids']
    assert new['shops']['1561117812']['access_token']=='NEW-SYNTHETIC-SENTINEL'
    assert calls==['POST','GET'] and result['state']=='IDENTITY_VERIFIED'
    assert 'SENTINEL' not in journal.read_text()

CREDS={'partner_id':123,'partner_key':'KEY-SYNTHETIC-SENTINEL'}
GOOD={'access_token':'NEW-SYNTHETIC-SENTINEL','refresh_token':'ROTATED-SYNTHETIC-SENTINEL','expire_in':14400,'shop_id':1561117812}

@pytest.mark.parametrize('failure',['timeout','certificate','redirect','business','json','length','fake_token','wrong_shop','expiry','duplicate_json'])
def test_failure_is_one_attempt_no_replay_no_secret_log(case,monkeypatch,failure):
    m,path,journal,plan,approved,old=case;raw=path.read_bytes();calls=[]
    def once(req,**kw):
        calls.append(1)
        assert kw['context'].verify_mode==ssl.CERT_REQUIRED and kw['context'].check_hostname
        if failure=='timeout':raise TimeoutError('PRIVATE-SENTINEL')
        if failure=='certificate':raise ssl.SSLCertVerificationError('PRIVATE-SENTINEL')
        if failure=='redirect':raise urllib.error.HTTPError(req.full_url,302,'PRIVATE-SENTINEL',None,None)
        if failure=='json':return io_reply(b'PRIVATE-SENTINEL')
        if failure=='length':return io_reply(b'x'*(1024*1024+1))
        if failure=='duplicate_json':return io_reply(b'{"access_token":"PRIVATE-SENTINEL","access_token":"other"}')
        data=dict(GOOD)
        if failure=='business':data['error']='PRIVATE-SENTINEL'
        if failure=='fake_token':data['access_token']=' '
        if failure=='wrong_shop':data['shop_id']=1
        if failure=='expiry':data['expire_in']=True
        return response(data)
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    monkeypatch.setattr(m.http,'_curl_urlopen',lambda *a,**k:pytest.fail('curl must not run'))
    with pytest.raises(m.RecoveryError) as error:m.execute(plan,approved,CREDS)
    assert str(error.value)=='RECONCILIATION_REQUIRED' and path.read_bytes()==raw and calls==[1]
    assert 'SENTINEL' not in journal.read_text() and 'SENTINEL' not in str(error.value)
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,CREDS)
    assert calls==[1]

def io_reply(raw):
    class Reply(io.BytesIO):status=200
    return Reply(raw)

@pytest.mark.parametrize('bad',['authorization','version','source','unprotected','lock','wrong_target'])
def test_precheck_has_zero_transport(case,monkeypatch,bad):
    m,path,journal,plan,approved,old=case;calls=[]
    monkeypatch.setattr(m.http,'_urlopen_once',lambda *a,**k:calls.append(1))
    if bad=='authorization':approved['auth_write']=False
    if bad=='version':path.write_text(path.read_text()+' ')
    if bad=='source':plan['sources']['core/http_retry.py']='0'*64;approved['plan_digest']=m.plan_digest(plan)
    if bad=='unprotected':
        if os.name=='nt':subprocess.run(['icacls',str(path.parent),'/grant','*S-1-1-0:(OI)(CI)R'],check=True,capture_output=True)
        else:path.chmod(0o644)
    if bad=='lock':path.with_name(path.name+'.recovery-lock').write_text('occupied')
    if bad=='wrong_target':plan['shop_id']=999;approved['plan_digest']=m.plan_digest(plan)
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,CREDS)
    assert calls==[] and not journal.exists()

@pytest.mark.parametrize('failure',['version_during_request','replace','candidate_protection','identity_mismatch','identity_timeout'])
def test_rotation_failure_preserves_candidate_and_no_rollback(case,monkeypatch,failure):
    m,path,journal,plan,approved,old=case;raw=path.read_bytes();calls=[]
    def once(req,**kw):
        calls.append(req.get_method())
        if req.get_method()=='POST':
            if failure=='version_during_request':path.write_text('concurrent writer')
            return response(GOOD)
        if failure=='identity_timeout':raise TimeoutError('PRIVATE-SENTINEL')
        return response({'response':{'shop_id':1561117812,'region':'TH'}})
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    original_replace=m._replace_reserved
    def replace(reservation,dst):
        if failure=='replace':raise OSError('PRIVATE-SENTINEL')
        return original_replace(reservation,dst)
    monkeypatch.setattr(m,'_replace_reserved',replace)
    original_protected=m.protected
    def protect(p):
        if str(p).endswith('.candidate') and failure=='candidate_protection':raise OSError('PRIVATE-SENTINEL')
        return original_protected(p)
    monkeypatch.setattr(m,'protected',protect)
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,CREDS)
    if failure=='version_during_request':assert path.read_text()=='concurrent writer'
    elif failure.startswith('identity'):assert json.loads(path.read_text())['shops']['1561117812']['access_token']==GOOD['access_token']
    else:assert path.read_bytes()==raw
    candidates=list(path.parent.glob('*.candidate'));assert len(candidates)==1
    if failure!='candidate_protection':
        original_protected(candidates[0]);assert json.loads(candidates[0].read_text())['refresh_token']==GOOD['refresh_token']
    else:assert candidates[0].read_bytes()==b'' and calls==[] and not journal.exists()
    if journal.exists():assert 'SENTINEL' not in journal.read_text()
    previous=list(calls)
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,CREDS)
    assert calls==previous

def test_actual_r_opener_disables_redirect_and_never_curl(case,monkeypatch):
    m,path,journal,plan,approved,old=case;seen=[]
    def opener(*handlers):
        redirect=next(h for h in handlers if isinstance(h,urllib.request.HTTPRedirectHandler))
        https=next(h for h in handlers if isinstance(h,urllib.request.HTTPSHandler))
        assert https._context.verify_mode==ssl.CERT_REQUIRED and https._context.check_hostname
        class Opener:
            def open(self,req,timeout):
                seen.append(req.get_method())
                assert redirect.redirect_request(req,None,302,'redirect',{},'https://example.org') is None
                raise urllib.error.HTTPError(req.full_url,302,'PRIVATE-SENTINEL',None,None)
        return Opener()
    monkeypatch.setattr(m.http.urllib.request,'build_opener',opener)
    monkeypatch.setattr(m.http,'_curl_urlopen',lambda *a,**k:pytest.fail('curl'))
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,CREDS)
    assert seen==['POST']

@pytest.mark.parametrize('url,method',[('http://partner.shopeemobile.com/api/v2/auth/access_token/get','POST'),('https://evil.invalid/api/v2/auth/access_token/get','POST'),('https://partner.shopeemobile.com/api/v2/product/update_item','POST'),('https://partner.shopeemobile.com/api/v2/auth/access_token/get','GET')])
def test_exact_endpoint_allowlist_blocks_before_attempt(case,url,method):
    m,path,journal,plan,approved,old=case;state={'attempts':[]}
    with pytest.raises(m.RecoveryError):m._send('shopee_refresh',urllib.request.Request(url,method=method),state,journal)
    assert state['attempts']==[] and not journal.exists()

def test_tiktok_get_refresh_timeout_is_one_physical_attempt(case,monkeypatch):
    m,path,journal,plan,approved,old=case;path.write_text(json.dumps({'access_token':'OLD-SYNTHETIC-SENTINEL','refresh_token':'REFRESH-SYNTHETIC-SENTINEL','refresh_token_expire_in':4902400000}))
    plan=m.prepare(path=path,provider='tiktok',run_id='synthetic-tiktok');approved['plan_digest']=m.plan_digest(plan);calls=[]
    def once(req,**kw):calls.append(req.get_method());raise TimeoutError('PRIVATE-SENTINEL')
    monkeypatch.setattr(m.http,'_urlopen_once',once);monkeypatch.setattr(m.http,'_curl_urlopen',lambda *a,**k:pytest.fail('curl'))
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,{'app_key':'APP-SYNTHETIC','app_secret':'SECRET-SYNTHETIC'})
    assert calls==['GET']

@pytest.mark.parametrize('ambiguous',[False,True])
def test_tiktok_candidates_never_approve_targets_and_keep_expiry(case,monkeypatch,ambiguous):
    m,path,journal,plan,approved,old=case;path.write_text(json.dumps({'access_token':'OLD-SYNTHETIC-SENTINEL','refresh_token':'REFRESH-SYNTHETIC-SENTINEL','refresh_token_expire_in':4902400000}))
    plan=m.prepare(path=path,provider='tiktok',run_id='synthetic-tiktok');approved['plan_digest']=m.plan_digest(plan);calls=[]
    def once(req,**kw):
        calls.append(1)
        if len(calls)==1:return response({'code':0,'data':{'access_token':'NEW-SYNTHETIC-SENTINEL','refresh_token':'ROTATED-SYNTHETIC-SENTINEL','access_token_expire_in':int(time.time())+3600}})
        rows=[{'id':r+'-ID','cipher':r+'-CIPHER','region':r} for r in m.SHOPS]
        if ambiguous:rows.append(dict(rows[0],id='SECOND-MY'))
        return response({'code':0,'data':{'shops':rows}})
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    if ambiguous:
        with pytest.raises(m.RecoveryError,match='IDENTITY_UNVERIFIED'):m.execute(plan,approved,{'app_key':'APP-SYNTHETIC','app_secret':'SECRET-SYNTHETIC'})
    else:assert m.execute(plan,approved,{'app_key':'APP-SYNTHETIC','app_secret':'SECRET-SYNTHETIC'})['state']=='TARGET_SELECTION_REQUIRED'
    assert len(calls)==2 and json.loads(path.read_text())['refresh_token_expire_in']==4902400000

def test_four_shopee_targets_serial_without_changing_other_entries(case,monkeypatch):
    m,path,journal,plan,approved,old=case
    old['sync_shop_ids']=dict(m.SHOPS)
    for sid in m.SHOPS.values():old['shops'][str(sid)]={'shop_id':sid,'refresh_token':'REFRESH-SYNTHETIC-SENTINEL','access_token':'OLD-SYNTHETIC-SENTINEL','expire_at':1}
    path.write_text(json.dumps(old));calls=[]
    def once(req,**kw):
        calls.append(req.get_method())
        if req.data:
            sid=json.loads(req.data)['shop_id'];return response(dict(GOOD,shop_id=sid))
        import urllib.parse
        sid=int(urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)['shop_id'][0])
        return response({'response':{'shop_id':sid,'region':next(r for r,s in m.SHOPS.items() if s==sid)}})
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    for country,sid in m.SHOPS.items():
        plan=m.prepare(path=path,provider='shopee',shop_id=sid,country=country,run_id='serial-fixture')
        approved['plan_digest']=m.plan_digest(plan);assert m.execute(plan,approved,CREDS)['state']=='IDENTITY_VERIFIED'
    assert calls==['POST','GET']*4 and json.loads(path.read_text())['shops']['OTHER']==old['shops']['OTHER']

def test_dry_run_cli_never_reads_fixture_credentials(case):
    m,path,journal,plan,approved,old=case;public=path.parent/'public-plan.json';public.write_text(json.dumps(plan));path.unlink()
    result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--dry-run',str(public)],capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)['credential_reads']==0 and not journal.exists()

def test_concurrent_executor_cannot_spend_second_attempt(case,monkeypatch):
    import threading
    m,path,journal,plan,approved,old=case;entered=threading.Event();release=threading.Event();calls=[];errors=[]
    def once(req,**kw):
        calls.append(1);entered.set();assert release.wait(5);raise TimeoutError('PRIVATE-SENTINEL')
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    def first():
        try:m.execute(plan,approved,CREDS)
        except m.RecoveryError as exc:errors.append(str(exc))
    thread=threading.Thread(target=first);thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(m.RecoveryError,match='LOCKED_OR_UNPROTECTED'):m.execute(plan,approved,CREDS)
    finally:release.set();thread.join(5)
    assert not thread.is_alive() and calls==[1] and errors==['RECONCILIATION_REQUIRED']

def test_implausible_refresh_expiry_remains_unknown(case):
    m,*_=case
    assert m.refresh_expiry_status(4902400000)=='UNKNOWN_IMPLAUSIBLE_SAVED_EXPIRY'
    assert m.refresh_expiry_status(None)==m.refresh_expiry_status('4902400000')=='UNKNOWN'

def test_attempt_journal_failure_prevents_dispatch(case,monkeypatch):
    m,path,journal,plan,approved,old=case;raw=path.read_bytes();calls=[]
    monkeypatch.setattr(m.http,'_urlopen_once',lambda *a,**k:calls.append(1))
    original=m.os.replace
    def fail(src,dst):
        if Path(dst)==journal:raise OSError('PRIVATE-SENTINEL')
        return original(src,dst)
    monkeypatch.setattr(m.os,'replace',fail)
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,CREDS)
    assert calls==[] and path.read_bytes()==raw
    assert journal.with_name(journal.name+'.next').exists()

@pytest.mark.parametrize('kind',['candidate_file','replacement_file','candidate_directory','replacement_directory','candidate_protection','replacement_protection'])
def test_output_readiness_precedes_refresh(case,monkeypatch,kind):
    m,path,journal,plan,approved,old=case;raw=path.read_bytes();calls=[]
    suffix=kind.split('_')[0];output=path.with_name(path.name+'.1561117812.synthetic-run.'+suffix)
    if kind.endswith('_file'):output.write_bytes(b'PREEXISTING-SYNTHETIC')
    elif kind.endswith('_directory'):output.mkdir()
    else:
        original=m.protected
        def deny(p):
            if Path(p)==output:raise OSError('PRIVATE-SENTINEL')
            return original(p)
        monkeypatch.setattr(m,'protected',deny)
    def once(req,**kw):calls.append(req.get_method());return response(GOOD)
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,CREDS)
    assert calls==[], 'refresh must wait for all required output reservations'
    assert path.read_bytes()==raw
    if kind.endswith('_file'):assert output.read_bytes()==b'PREEXISTING-SYNTHETIC'
    elif kind.endswith('_directory'):assert output.is_dir() and list(output.iterdir())==[]
    else:assert output.read_bytes()==b''

@pytest.mark.parametrize('target',['candidate','replacement'])
def test_post_response_disk_failure_remains_unreplayed(case,monkeypatch,target):
    m,path,journal,plan,approved,old=case;raw=path.read_bytes();calls=[]
    def once(req,**kw):calls.append(req.get_method());return response(GOOD)
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    original=m._write_reserved
    def failed_write(reservation,data):
        if reservation['path'].suffix=='.'+target:raise OSError('PRIVATE-SENTINEL')
        return original(reservation,data)
    monkeypatch.setattr(m,'_write_reserved',failed_write)
    with pytest.raises(m.RecoveryError,match='RECONCILIATION_REQUIRED'):m.execute(plan,approved,CREDS)
    assert calls==['POST'] and path.read_bytes()==raw
    candidate=next(path.parent.glob('*.candidate'));m.protected(candidate)
    if target=='replacement':assert json.loads(candidate.read_text())['refresh_token']==GOOD['refresh_token']
    else:assert candidate.read_bytes()==b''  # Later media failure can still prevent recovery.
    with pytest.raises(m.RecoveryError):m.execute(plan,approved,CREDS)
    assert calls==['POST'] and 'SENTINEL' not in journal.read_text()

def test_reserved_handles_survive_through_successful_rotation(case,monkeypatch):
    m,path,journal,plan,approved,old=case;reserved={};calls=[]
    def once(req,**kw):
        calls.append(req.get_method())
        if req.get_method()=='POST':
            for suffix in ('candidate','replacement'):
                output=path.with_name(path.name+'.1561117812.synthetic-run.'+suffix)
                assert output.exists() and output.stat().st_size==0
                reserved[suffix]=output.stat().st_ino
                if os.name=='nt':
                    with pytest.raises(OSError):
                        with output.open('wb') as other:other.write(b'not allowed')
                    with pytest.raises(OSError):os.replace(output,output.with_suffix('.stolen'))
            return response(GOOD)
        return response({'response':{'shop_id':1561117812,'region':'MY'}})
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    assert m.execute(plan,approved,CREDS)['state']=='IDENTITY_VERIFIED'
    candidate=next(path.parent.glob('*.candidate'))
    assert candidate.stat().st_ino==reserved['candidate'] and path.stat().st_ino==reserved['replacement']
    assert json.loads(candidate.read_text())['refresh_token']==GOOD['refresh_token'] and calls==['POST','GET']

@pytest.mark.parametrize('padding',['','a','ab','abc'])
def test_non_bmp_private_directory_recovers_with_reserved_handle(case,monkeypatch,padding):
    m,path,journal,plan,approved,old=case
    private=path.parent/('synthetic_\U0001f600'+padding);private.mkdir()
    renamed=private/'fixture.json';renamed.write_bytes(path.read_bytes());renamed.chmod(0o600)
    plan=m.prepare(path=renamed,provider='shopee',shop_id=1561117812,country='MY',run_id='unicode-fixture');approved['plan_digest']=m.plan_digest(plan);calls=[]
    def once(req,**kw):
        calls.append(req.get_method())
        if req.get_method()=='POST':return response(GOOD)
        return response({'response':{'shop_id':1561117812,'region':'MY'}})
    monkeypatch.setattr(m.http,'_urlopen_once',once)
    assert m.execute(plan,approved,CREDS)['state']=='IDENTITY_VERIFIED'
    assert calls==['POST','GET'] and json.loads(renamed.read_text())['shops']['1561117812']['access_token']==GOOD['access_token']
    assert json.loads(next(private.glob('*.candidate')).read_text())['refresh_token']==GOOD['refresh_token']

def test_destination_encoding_failure_is_pre_dispatch(case,monkeypatch):
    m,path,journal,plan,approved,old=case;calls=[];raw=path.read_bytes()
    def unsupported(destination):raise m.RecoveryError('DESTINATION_ENCODING_UNSUPPORTED')
    monkeypatch.setattr(m,'_rename_info',unsupported)
    monkeypatch.setattr(m.http,'_urlopen_once',lambda *a,**k:calls.append(1))
    if os.name=='nt':
        with pytest.raises(m.RecoveryError,match='OUTPUT_STORAGE_NOT_READY'):m.execute(plan,approved,CREDS)
        assert calls==[] and path.read_bytes()==raw and not journal.exists()
