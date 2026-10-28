import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts/read_orders_explicit.py'


def module():
    spec = importlib.util.spec_from_file_location('explicit_supply_read_test', SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def profile(tmp_path, m):
    settings = tmp_path/'settings.json'; settings.write_text('{}')
    tk = tmp_path/'tiktok.json'; tk.write_text(json.dumps({'access_token':'SYNTHETIC_TOKEN','access_token_expire_in':2000000000}))
    sp = tmp_path/'shopee.json'; sp.write_text(json.dumps({'shops':{str(i+1):{'access_token':'SYNTHETIC_TOKEN','expire_at':2000000000} for i in range(4)}}))
    value = {'schema':'supply-read-profile/v1','source_root':str(ROOT),'settings_path':str(settings),
        'token_paths':{'tiktok':str(tk),'shopee':str(sp)},'output_root':str(tmp_path/'output'),
        'targets':{r:{'warehouse':m.WAREHOUSES[r],'shopee_shop_id':i+1,'tiktok_shop_id':str(i+1),'tiktok_cipher':f'CIPHER_{r}'} for i,r in enumerate(m.REGIONS)}}
    p=tmp_path/'profile.json'; p.write_text(json.dumps(value)); return value,p


def test_explicit_profile_has_no_cwd_or_legacy_fallback(tmp_path, monkeypatch):
    m=module(); value,p=profile(tmp_path,m); monkeypatch.delenv('ORBIT_HIVE_SETTINGS',raising=False)
    assert m.load_profile(p)==value
    value['source_root']=str(tmp_path);p.write_text(json.dumps(value))
    with pytest.raises(m.NotReady,match='source differs'):m.load_profile(p)
    value['source_root']=str(ROOT);p.write_text(json.dumps(value))
    monkeypatch.setenv('ORBIT_HIVE_SETTINGS',str(tmp_path/'old-c-config.json'))
    with pytest.raises(m.NotReady,match='settings conflict'):m.load_profile(p)


def test_expired_sessions_never_refresh_or_create_output(tmp_path):
    m=module(); value,p=profile(tmp_path,m)
    sp=Path(value['token_paths']['shopee']); old=sp.read_bytes()
    states,sessions,files=m.inspect_sessions(value,now=datetime(2040,1,1,tzinfo=timezone.utc))
    assert not sessions
    assert all(states['shopee'][r]['state']=='PENDING_AUTH_RECOVERY' for r in m.REGIONS)
    assert sp.read_bytes()==old
    assert not Path(value['output_root']).exists()


def test_uncertain_recovery_and_rotated_credentials_abort(tmp_path):
    m=module(); value,p=profile(tmp_path,m)
    journal=Path(value['token_paths']['shopee']+'.refresh-state.json'); journal.write_text('{"schema_version":"shopee-refresh-state/v1","shops":{"1":{"status":"UNKNOWN","old_refresh_digest":"sha256:synthetic"}}}')
    states,sessions,files=m.inspect_sessions(value,now=datetime(2026,1,1,tzinfo=timezone.utc))
    assert states['shopee']['recovery']['state']=='BLOCKED_RECONCILIATION'
    assert ('shopee','MY') not in sessions
    assert ('shopee','TH') in sessions
    guard=m.wire_guard(files,value['settings_path']);guard()
    Path(value['token_paths']['tiktok']).write_text('{}')
    with pytest.raises(m.NotReady,match='changed during read'):guard()


def test_unreadable_recovery_pauses_platform_without_using_backup(tmp_path,monkeypatch):
    m=module();value,p=profile(tmp_path,m)
    journal=Path(value['token_paths']['shopee']+'.refresh-state.json');journal.write_text('{"schema_version":"shopee-refresh-state/v1","shops":{}}')
    original=Path.read_bytes
    def denied(path):
        if path==journal:raise PermissionError('synthetic Windows ACL denial')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',denied)
    states,sessions,_=m.inspect_sessions(value,now=datetime(2026,1,1,tzinfo=timezone.utc))
    assert states['shopee']['recovery']['state']=='BLOCKED_RECONCILIATION_READ'
    assert all(key[0]!='shopee' for key in sessions)
    assert ('tiktok','selected') in sessions
    assert not Path(value['output_root']).exists()


def test_source_guard_and_country_identity_cannot_silently_change(tmp_path,monkeypatch):
    import hashlib
    m=module();value,p=profile(tmp_path,m);monkeypatch.delenv('ORBIT_HIVE_SETTINGS',raising=False)
    guard=m.wire_guard({p:hashlib.sha256(p.read_bytes()).hexdigest()},value['settings_path']);guard()
    value['targets']['TH']['tiktok_cipher']=value['targets']['MY']['tiktok_cipher'];p.write_text(json.dumps(value))
    with pytest.raises(m.NotReady,match='changed during read'):guard()
    with pytest.raises(m.NotReady,match='reused across countries'):m.load_profile(p)


def test_identity_is_single_attempt_and_exact_scope(tmp_path):
    m=module(); value,p=profile(tmp_path,m); calls=[]
    def fake(*args,**kwargs):
        calls.append((args,kwargs));kwargs['call_guard']()
        return {'code':0,'data':{'shops':[{'id':t['tiktok_shop_id'],'cipher':t['tiktok_cipher'],'region':r} for r,t in value['targets'].items()]}}
    assert set(m.read_identity(value,'SYNTHETIC',lambda:None,request=fake))==set(m.REGIONS)
    assert len(calls)==1
    assert calls[0][1]['_retry_on_401'] is False and calls[0][1]['retry_read'] is False
    value['targets']['TH']['tiktok_shop_id']='OTHER'
    def ambiguous(*args,**kwargs):return {'code':0,'data':{'shops':[{'id':'OTHER','cipher':'CIPHER_TH','region':'TH'},{'id':'2','cipher':'CIPHER_TH','region':'TH'}]}}
    with pytest.raises(m.NotReady,match='missing or ambiguous'):m.read_identity(value,'SYNTHETIC',lambda:None,request=ambiguous)


def test_read_order_cli_stops_before_bind_or_http(tmp_path,monkeypatch,capsys):
    m=module();value,p=profile(tmp_path,m);monkeypatch.delenv('ORBIT_HIVE_SETTINGS',raising=False)
    store=json.loads(Path(value['token_paths']['shopee']).read_text())
    for row in store['shops'].values():row['expire_at']=1
    Path(value['token_paths']['shopee']).write_text(json.dumps(store))
    def forbidden(*a,**kw):raise AssertionError('HTTP/config binding must not occur')
    monkeypatch.setattr(m,'bind_runtime',forbidden)
    assert m.main(['--profile',str(p),'--read-orders'])==2
    report=json.loads(capsys.readouterr().out)
    assert report['state']=='NOT_READY' and report['authWrites']==report['businessWrites']==0
    assert not Path(value['output_root']).exists()


def test_partial_tiktok_capture_never_invents_shopee(tmp_path,monkeypatch,capsys):
    import socket
    m=module();value,p=profile(tmp_path,m);monkeypatch.delenv('ORBIT_HIVE_SETTINGS',raising=False)
    monkeypatch.setattr(m,'bind_runtime',lambda p:{})
    monkeypatch.setattr(m,'read_identity',lambda *args:{})
    calls=[]
    def reader(*args):
        calls.append(args[0]);return {'code':0,'data':{'orders':[],'next_page_token':'','total_count':0}}
    monkeypatch.setattr(m,'requesters',lambda guard:{'tiktok':reader})
    def forbidden(*a,**kw):raise AssertionError('real network is forbidden in fixture')
    monkeypatch.setattr(socket,'socket',forbidden)
    assert m.main(['--profile',str(p),'--read-tiktok-orders'])==0
    report=json.loads(capsys.readouterr().out);source=json.loads(Path(report['path']).read_text())
    assert report['state']=='PARTIAL_TIKTOK_ONLY_NOT_APPLIED'
    assert len(calls)==20
    assert source['completeness']=='TIKTOK_ONLY_NOT_COMPLETE' and source['missingPlatforms']==['Shopee']
    assert all('shopee' not in row and row['mappingState']=='MAPPED_TIKTOK_ONLY' for row in source['regions'].values())
    assert 'SYNTHETIC_TOKEN' not in Path(report['path']).read_text()


def test_original_shopee_journal_committed_entries_and_uncertain_shared_alias(tmp_path):
    import hashlib
    m=module();value,p=profile(tmp_path,m)
    store=json.loads(Path(value['token_paths']['shopee']).read_text())
    for entry in store['shops'].values():entry['refresh_token']='SYNTHETIC_SHARED_REFRESH'
    Path(value['token_paths']['shopee']).write_text(json.dumps(store))
    recovery={'schema_version':'shopee-refresh-state/v1','shops':{str(i+1):{'status':'COMMITTED_RECOVERED' if i==0 else 'COMMITTED'} for i in range(4)},'merchants':{}}
    journal=Path(value['token_paths']['shopee']+'.refresh-state.json');journal.write_text(json.dumps(recovery))
    _,sessions,_=m.inspect_sessions(value,platform='shopee',now=datetime(2026,1,1,tzinfo=timezone.utc))
    assert len(sessions)==4
    recovery['merchants']['OTHER']={'status':'UNKNOWN','old_refresh_digest':'sha256:'+hashlib.sha256(b'SYNTHETIC_SHARED_REFRESH').hexdigest()}
    journal.write_text(json.dumps(recovery))
    states,sessions,_=m.inspect_sessions(value,platform='shopee',now=datetime(2026,1,1,tzinfo=timezone.utc))
    assert not sessions and set(states['shopee']['recovery']['regions'])==set(m.REGIONS)


def test_shopee_continuation_reuses_exact_tiktok_receipts_without_tiktok_http(tmp_path,monkeypatch,capsys):
    import socket
    m=module();value,p=profile(tmp_path,m);monkeypatch.delenv('ORBIT_HIVE_SETTINGS',raising=False)
    monkeypatch.setattr(m,'bind_runtime',lambda p:{})
    monkeypatch.setattr(m,'read_identity',lambda *args:{})
    monkeypatch.setattr(m,'requesters',lambda guard:{'tiktok':lambda *args:{'code':0,'data':{'orders':[],'next_page_token':''}}})
    monkeypatch.setattr(socket,'socket',lambda *args,**kwargs:pytest.fail('real network forbidden'))
    assert m.main(['--profile',str(p),'--read-tiktok-orders'])==0
    initial=json.loads(capsys.readouterr().out);retained=Path(initial['path']);original=retained.read_bytes()
    monkeypatch.setattr(m,'read_identity',lambda *args:pytest.fail('TikTok identity must not repeat'))
    def identities(profile,sessions,guard):assert len(sessions)==4;guard()
    monkeypatch.setattr(m,'read_shopee_identities',identities)
    calls=[]
    def shopee_reader(*args):
        calls.append(args[0]);return {'response':{'order_list':[],'more':False,'next_cursor':''}}
    monkeypatch.setattr(m,'requesters',lambda guard:{'shopee':shopee_reader})
    assert m.main(['--profile',str(p),'--read-shopee-orders','--continue-from-tiktok',str(retained)])==0
    report=json.loads(capsys.readouterr().out);source=json.loads(Path(report['path']).read_text())
    assert len(calls)==12 and retained.read_bytes()==original
    assert report['state']=='PARTIAL_SHOPEE_ONLY_NOT_APPLIED' and source['capturedAt']==initial['capturedAt']
    assert source['retainedTikTokSource']['path']==str(retained)
    assert all('tiktok' not in row and row['mappingState']=='MAPPED_SHOPEE_ONLY' for row in source['regions'].values())


def test_shopee_official_country_identity_is_exact(tmp_path):
    m=module();value,p=profile(tmp_path,m);sessions={('shopee',r):'SYNTHETIC' for r in m.REGIONS};calls=[]
    def reader(endpoint,sid,token,params,*,call_guard):
        call_guard();calls.append(sid);return {'response':{'shop_id':sid,'region':next(r for r,t in value['targets'].items() if t['shopee_shop_id']==sid)}}
    m.read_shopee_identities(value,sessions,lambda:None,reader=reader)
    assert calls==[1,2,3,4]
    def signed_scope_without_redundant_id(endpoint,sid,token,params,*,call_guard):
        call_guard();return {'response':{'region':next(r for r,t in value['targets'].items() if t['shopee_shop_id']==sid)}}
    m.read_shopee_identities(value,sessions,lambda:None,reader=signed_scope_without_redundant_id)
    def top_level_identity(endpoint,sid,token,params,*,call_guard):
        call_guard();return {'error':'','region':next(r for r,t in value['targets'].items() if t['shopee_shop_id']==sid)}
    m.read_shopee_identities(value,sessions,lambda:None,reader=top_level_identity)
    def wrong(*args,**kwargs):return {'response':{'shop_id':1,'region':'PH'}}
    with pytest.raises(m.NotReady,match='mismatch'):m.read_shopee_identities(value,sessions,lambda:None,reader=wrong)


@pytest.mark.parametrize('blocker',['expired','invalid_retained_receipt'])
def test_shopee_continuation_dependencies_fail_before_any_provider_call(tmp_path,monkeypatch,capsys,blocker):
    m=module();value,p=profile(tmp_path,m);monkeypatch.delenv('ORBIT_HIVE_SETTINGS',raising=False)
    targets={r:{k:t[k] for k in ('tiktok_shop_id','tiktok_cipher','shopee_shop_id')} for r,t in value['targets'].items()}
    source={'schema':'supply-partial-order-read/v1','completeness':'TIKTOK_ONLY_NOT_COMPLETE','days':31,
        'capturedAt':'2026-09-30T00:00:00+00:00','targets':targets,'regions':{r:{'tiktok':{'target':t['tiktok_cipher'],'blocks':[]}} for r,t in targets.items()}}
    retained=tmp_path/'retained.json';retained.write_text(json.dumps(source))
    if blocker=='expired':
        store=json.loads(Path(value['token_paths']['shopee']).read_text())
        for entry in store['shops'].values():entry['expire_at']=1
        Path(value['token_paths']['shopee']).write_text(json.dumps(store))
    monkeypatch.setattr(m,'bind_runtime',lambda p:{})
    def forbidden(*a,**kw):pytest.fail('no provider request allowed for blocked dependencies')
    monkeypatch.setattr(m,'read_shopee_identities',forbidden);monkeypatch.setattr(m,'read_identity',forbidden)
    assert m.main(['--profile',str(p),'--read-shopee-orders','--continue-from-tiktok',str(retained)])==2
    report=json.loads(capsys.readouterr().out)
    assert report['state']=='NOT_READY' and report['authWrites']==report['businessWrites']==0
    assert not Path(value['output_root']).exists()
