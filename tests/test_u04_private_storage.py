import importlib.util,json,os,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts/private_storage.py'

def load():
    spec=importlib.util.spec_from_file_location('private_storage',SCRIPT);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.fixture
def case(tmp_path):
    m=load();legacy=tmp_path/'legacy';legacy.mkdir();private=tmp_path/'private-😀';private.mkdir()
    if os.name=='nt':subprocess.run(['icacls',str(private),'/inheritance:r','/grant:r',f'*{m.g.current_sid()}:(OI)(CI)F'],check=True,capture_output=True)
    else:private.chmod(0o700)
    paths={k:legacy/(k+'.json') for k in ('settings','tiktok','shopee')}
    values={'settings':{'token_file':'tiktok.json','shopee':{'token_file':'shopee.json','partner_key':'SYNTHETIC-APP-SECRET'},'unrelated':{'retain':[1,2]}},'tiktok':{'access_token':'SYNTHETIC-TK','other':[1]},'shopee':{'shops':{'1':{'access_token':'SYNTHETIC-SP'},'OTHER':{'keep':True}},'sync_shop_ids':{'MY':1},'merchants':{'keep':[1,2]}}}
    for k,p in paths.items():p.write_text(json.dumps(values[k]),encoding='utf-8')
    plan=m.prepare(settings=paths['settings'],tiktok=paths['tiktok'],shopee=paths['shopee'],private=private,run_id='synthetic',writers=['synthetic-writer'],database_target=legacy/'data/shop.db')
    auth={'plan_digest':m.g.plan_digest(plan),'authority':'synthetic-only','storage_write':True}
    def stopped(plan):return {'plan_digest':m.g.plan_digest(plan),'writers':plan['writers'],'all_stopped':True,'evidence':'synthetic no writer process'}
    return m,paths,private,values,plan,auth,stopped

def test_atomic_migration_preserves_tokens_and_other_config(case):
    m,paths,private,values,plan,auth,stopped=case
    originals={k:p.read_bytes() for k,p in paths.items()}
    result=m.execute(plan,auth,stopped);assert result['state']=='COMPLETE'
    for k in ('tiktok','shopee'):assert paths[k].read_bytes()==originals[k] and (private/(k+'.json')).read_bytes()==originals[k]
    assert paths['settings'].read_bytes()==originals['settings']
    config=json.loads((private/'settings.json').read_text(encoding='utf-8'));expected=values['settings'];expected['token_file']=str(private/'tiktok.json');expected['shopee']['token_file']=str(private/'shopee.json');expected['database']=str(paths['settings'].parent/'data/shop.db');assert config==expected
    assert m.execute(plan,auth,stopped)['state']=='REUSED'

@pytest.mark.parametrize('bad',['authorization','writers','source','settings','source_binding','private_acl','destination'])
def test_preconditions_no_source_writes(case,monkeypatch,bad):
    m,paths,private,values,plan,auth,stopped=case
    if bad=='authorization':auth['storage_write']=False
    elif bad=='writers':stopped=lambda _: {'all_stopped':False}
    elif bad in ('source','settings'):paths['tiktok' if bad=='source' else 'settings'].write_text('{}')
    elif bad=='source_binding':plan['sources']={};auth['plan_digest']=m.g.plan_digest(plan)
    elif bad=='private_acl':
        if os.name=='nt':subprocess.run(['icacls',str(private),'/grant','*S-1-1-0:(OI)(CI)R'],check=True,capture_output=True)
        else:private.chmod(0o755)
    else:(private/'tiktok.json').write_text('{}')
    before={k:p.read_bytes() for k,p in paths.items()}
    with pytest.raises(m.g.RecoveryError):m.execute(plan,auth,stopped)
    assert {k:p.read_bytes() for k,p in paths.items()}==before and not (private/'settings.json').exists()

@pytest.mark.parametrize('target',['tiktok.json','shopee.json','settings.json'])
def test_interrupted_publication_resumes_same_plan(case,monkeypatch,target):
    m,paths,private,values,plan,auth,stopped=case;original=m._atomic_private
    def interrupted(destination,raw,directory):
        original(destination,raw,directory)
        if destination.name==target:raise OSError('SYNTHETIC interruption after atomic publication')
    monkeypatch.setattr(m,'_atomic_private',interrupted)
    with pytest.raises(OSError):m.execute(plan,auth,stopped)
    monkeypatch.setattr(m,'_atomic_private',original)
    assert m.execute(plan,auth,stopped)['state']=='COMPLETE'
    assert (private/'tiktok.json').read_bytes()==paths['tiktok'].read_bytes()
    assert (private/'shopee.json').read_bytes()==paths['shopee'].read_bytes()

def test_metadata_prepare_never_reads_payload(case,monkeypatch):
    m,paths,private,values,plan,auth,stopped=case;read=Path.read_bytes
    def forbid(p):
        assert p not in paths.values(),'credential payload read in prepare'
        return read(p)
    monkeypatch.setattr(Path,'read_bytes',forbid)
    new=m.prepare(**paths,private=private,run_id='other',writers=['synthetic-writer'])
    text=json.dumps(new);assert 'SYNTHETIC-APP-SECRET' not in text and 'SYNTHETIC-TK' not in text and 'sha256' not in new['versions']

def test_partial_tampering_requires_reconciliation(case,monkeypatch):
    m,paths,private,values,plan,auth,stopped=case;original=m._atomic_private
    def stop(destination,raw,directory):
        original(destination,raw,directory)
        if destination.name=='tiktok.json':raise OSError('synthetic')
    monkeypatch.setattr(m,'_atomic_private',stop)
    with pytest.raises(OSError):m.execute(plan,auth,stopped)
    (private/'tiktok.json').write_text('{}');monkeypatch.setattr(m,'_atomic_private',original)
    with pytest.raises(m.g.RecoveryError,match='DESTINATION_CHANGED'):m.execute(plan,auth,stopped)

def test_source_writer_between_publications_stops_config(case,monkeypatch):
    m,paths,private,values,plan,auth,stopped=case;original=m._atomic_private
    def race(destination,raw,directory):
        original(destination,raw,directory)
        if destination.name=='tiktok.json':paths['shopee'].write_text('{}')
    monkeypatch.setattr(m,'_atomic_private',race)
    with pytest.raises(m.g.RecoveryError,match='SOURCE_CHANGED'):m.execute(plan,auth,stopped)
    assert not (private/'settings.json').exists()

def test_backup_interruption_requires_reconciliation(case,monkeypatch):
    m,paths,private,values,plan,auth,stopped=case;original=m.g._write_new
    def stop(path,raw):
        if path.suffix=='.backup':raise OSError('synthetic backup disk failure')
        return original(path,raw)
    monkeypatch.setattr(m.g,'_write_new',stop)
    with pytest.raises(OSError):m.execute(plan,auth,stopped)
    monkeypatch.setattr(m.g,'_write_new',original)
    with pytest.raises(m.g.RecoveryError,match='RECONCILIATION_REQUIRED'):m.execute(plan,auth,stopped)
    assert not (private/'settings.json').exists()

def test_missing_private_directory_never_creates_it(case):
    m,paths,private,values,plan,auth,stopped=case;plan['private']=str(private/'missing');auth['plan_digest']=m.g.plan_digest(plan)
    with pytest.raises((OSError,m.g.RecoveryError)):m.execute(plan,auth,stopped)
    assert not Path(plan['private']).exists()

def test_private_git_directory_is_rejected(case):
    m,paths,private,values,plan,auth,stopped=case;(private/'.git').write_text('synthetic git marker')
    with pytest.raises(m.g.RecoveryError,match='PRIVATE_INSIDE_GIT'):m.execute(plan,auth,stopped)

def test_unverified_database_target_blocks_before_payload_reads(case,monkeypatch):
    m,paths,private,values,plan,auth,stopped=case;plan['database_target']=None;auth['plan_digest']=m.g.plan_digest(plan);original=Path.read_bytes
    def safe(p):
        assert p not in paths.values(),'payload read before database target approval'
        return original(p)
    monkeypatch.setattr(Path,'read_bytes',safe)
    with pytest.raises(m.g.RecoveryError,match='DATABASE_TARGET_UNVERIFIED'):m.execute(plan,auth,stopped)

def test_absolute_database_preserved_without_open(case):
    m,paths,private,values,plan,auth,stopped=case;target=private.parent/'never-open-this.db'
    values['settings']['database']=str(target);paths['settings'].write_text(json.dumps(values['settings']),encoding='utf-8')
    plan=m.prepare(**paths,private=private,run_id='synthetic',writers=['synthetic-writer'],database_target=target);auth['plan_digest']=m.g.plan_digest(plan)
    m.execute(plan,auth,stopped);assert json.loads((private/'settings.json').read_text(encoding='utf-8'))['database']==str(target) and not target.exists()

def test_real_process_crash_leaves_lock_then_explicit_synthetic_reconcile(case):
    import sys
    m,paths,private,values,plan,auth,stopped=case
    public=private.parent/'public-plan.json';public.write_text(json.dumps({'plan':plan,'auth':auth}),encoding='utf-8')
    script=private.parent/'crash.py';script.write_text("import importlib.util,json,os,sys\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location('m',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nx=json.loads(Path(sys.argv[2]).read_text());original=m._atomic_private\ndef crash(d,r,p):\n original(d,r,p)\n if d.name=='tiktok.json':os._exit(73)\nm._atomic_private=crash\nm.execute(x['plan'],x['auth'],lambda p:{'plan_digest':m.g.plan_digest(p),'writers':p['writers'],'all_stopped':True,'evidence':'synthetic child only'})\n",encoding='utf-8')
    result=subprocess.run([sys.executable,'-B','-X','utf8',str(script),str(SCRIPT),str(public)],capture_output=True,timeout=20);assert result.returncode==73
    with pytest.raises(m.g.RecoveryError,match='LOCKED_OR_UNPROTECTED'):m.execute(plan,auth,stopped)
    # Only the known synthetic child exited; explicit test reconciliation removes
    # its orphan lock. Production needs separately audited owner/liveness evidence.
    (private/'migration-destination.recovery-lock').unlink()
    assert m.execute(plan,auth,stopped)['state']=='COMPLETE'

@pytest.mark.parametrize('root',['canonical','relocated'])
def test_actual_consumers_only_synthetic_config_no_db(case,root):
    import sys
    m,paths,private,values,plan,auth,stopped=case;m.execute(plan,auth,stopped)
    if root=='canonical':root=str(ROOT)
    else:
        import shutil
        relocated=private.parent/'relocated-source'
        for folder in ('core','modules/shopee'):
            for source in (ROOT/folder).glob('*.py'):
                target=relocated/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        root=str(relocated)
    command=[sys.executable,'-B','-X','utf8',str(ROOT/'tests/u04_private_consumers.py'),root,str(paths['settings']),str(private/'settings.json')]
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
    (private/'consumer-proof.json').write_text(json.dumps({'command':command,'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr}),encoding='utf-8')
    assert result.returncode==0,result.stderr
