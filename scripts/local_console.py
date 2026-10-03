"""Operate an explicit local review profile; dry-run unless --execute is supplied."""
import argparse,datetime,hashlib,http.client,json,pathlib,sqlite3,subprocess,time,socket

ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE=ROOT.parent

def read(path):return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
def digest(path):return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
def health(port):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    try:
        c.request('GET','/api/health');r=c.getresponse();assert r.status==200;return json.loads(r.read())
    finally:c.close()
def listener(port):
    rows=subprocess.check_output(['netstat','-ano','-p','tcp'],text=True).splitlines()
    ids={int(r.split()[-1]) for r in rows if len(r.split())>=5 and r.split()[1]==f'127.0.0.1:{port}' and 'LISTENING' in r}
    assert len(ids)==1,'Expected exactly one loopback listener'
    return ids.pop()
def table_counts(c):
    return {n:c.execute('SELECT count(*) FROM "'+n.replace('"','""')+'"').fetchone()[0]
            for n, in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
def validate_rollback(cfg,db,port):
    """Validate frozen evidence before any service-control operation."""
    frozen=cfg['rollback_binding'];receipt=pathlib.Path(cfg['rollback_receipt'])
    assert digest(receipt)==frozen['receipt_sha256'],'Rollback receipt drift'
    command=read(receipt)['rollback_args']
    assert hashlib.sha256(json.dumps(command,separators=(',',':')).encode()).hexdigest()==frozen['command_sha256'],'Rollback command drift'
    assert pathlib.Path(command[0]).name.lower()=='python.exe' and pathlib.Path(command[0]).is_file()
    assert '--port' in command and int(command[command.index('--port')+1])==port
    script=pathlib.Path(command[5]).resolve();root=script.parents[1]
    assert script.name=='catalog_review_preview.py' and root==pathlib.Path(frozen['root']).resolve()
    expected=frozen['head'];assert len(expected)==40 and all(c in '0123456789abcdef' for c in expected)
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    assert actual==expected,'Rollback HEAD drift'
    assert not subprocess.check_output(['git','-C',str(root),'status','--porcelain=v1','-uall'],text=True).strip(),'Rollback tree dirty'
    metadata=pathlib.Path(command[command.index('--metadata')+1]).resolve()
    profile=pathlib.Path(command[command.index('--profile')+1]).resolve()
    override=(pathlib.Path(command[command.index('--weight-overrides')+1]) if '--weight-overrides' in command else root/'data/weight_overrides.json').resolve()
    required={str(script),str(metadata),str(profile),str(override)}
    assert required<=set(frozen['file_hashes']),'Incomplete rollback fingerprints'
    for name,value in frozen['file_hashes'].items():assert digest(name)==value,'Rollback file drift'
    assert pathlib.Path(read(metadata)['snapshot_database']).resolve()==db
    return command,str(root),expected
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['status','start','restart','backup','rollback'])
    p.add_argument('--config',type=pathlib.Path,required=True)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--backup-dir',type=pathlib.Path)
    a=p.parse_args();cfg=read(a.config)
    assert pathlib.Path(cfg['code_root']).resolve()==ROOT
    state=pathlib.Path(cfg['state_dir']).resolve();assert state.is_relative_to(BASE)
    meta=pathlib.Path(cfg['metadata']).resolve();db=pathlib.Path(read(meta)['snapshot_database']).resolve()
    override=pathlib.Path(cfg['override']).resolve();assert override.parent==db.parent and override.is_file()
    port=int(cfg['port']);assert 1024<=port<=65535
    py=BASE/'environment/Scripts/python.exe';assert py.is_file()
    head=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    assert head==cfg['expected_head'],'Update reviewed config to exact candidate HEAD'
    profile={'profile_id':cfg['profile_id'],'settings_path':str(state/'settings.json'),'stores':{
        'catalog':str(db),'reports_release':str(state/'unconnected-platform.db'),
        'workbench':str(state/'unconnected-workbench.db'),'ozon':str(ROOT/'modules/ozon/legacy_webapp/data')}}
    command=[str(py),'-I','-B','-X','utf8',str(ROOT/'scripts/catalog_review_preview.py'),
        '--metadata',str(meta),'--state-dir',str(state),'--profile',str(state/'runtime-profile.json'),
        '--weight-overrides',str(override),'--port',str(port)]
    if cfg.get('review_offer'):command+=['--publication-review-offer',cfg['review_offer']]
    plan={'action':a.action,'dry_run':not a.execute,'head':head,'root':str(ROOT),'catalog':str(db),'override':str(override),'command':command}
    if a.action=='status':plan['health']=health(port);print(json.dumps(plan));return
    if a.action=='rollback':
        command,rollback_root,rollback_head=validate_rollback(cfg,db,port)
        plan.update(command=command,rollback_root=rollback_root,rollback_head=rollback_head)
    if a.action=='backup':
        assert a.backup_dir is not None
        target=a.backup_dir.resolve();assert target.is_relative_to(BASE/'backups') and not target.exists()
        plan['backup_dir']=str(target)
    if not a.execute:print(json.dumps(plan));return
    if a.action=='backup':
        target.mkdir(parents=True,exist_ok=False)
        user=subprocess.check_output(['whoami'],text=True).strip()
        subprocess.run(['icacls',str(target),'/inheritance:r','/grant:r',user+':(OI)(CI)F'],check=True,capture_output=True)
        before=digest(override);c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        counts=table_counts(c);d=sqlite3.connect(target/'catalog.db');c.backup(d)
        assert d.execute('PRAGMA integrity_check').fetchall()==[('ok',)] and table_counts(d)==counts;d.close();c.close()
        (target/'weight_overrides.json').write_bytes(override.read_bytes());assert digest(override)==before
        plan.update(table_counts=counts,backup_sha256=digest(target/'catalog.db'),override_sha256=before)
        (target/'RECEIPT.json').write_text(json.dumps(plan,indent=2));print(json.dumps(plan));return
    old=None
    if a.action!='rollback' and (state/'runtime-profile.json').exists():assert read(state/'runtime-profile.json')==profile
    if a.action in ['restart','rollback']:
        try:old=health(port)
        except ConnectionRefusedError:
            if a.action!='rollback':raise
        if old is not None:
            assert old['code_root']==str(ROOT) and old['commit']==cfg['expected_running_head'] and old['stores']['catalog']==str(db)
            actual=listener(port)
            cmd=subprocess.check_output(['wmic','process','where',f'ProcessId={actual}','get','CommandLine'],text=True)
            assert str(ROOT/'scripts/catalog_review_preview.py') in cmd and str(state) in cmd
            subprocess.run(['taskkill','/PID',str(actual),'/F'],check=True,capture_output=True)
            for _ in range(50):
                try:connection=socket.create_connection(('127.0.0.1',port),timeout=.2)
                except OSError:break
                else:connection.close();time.sleep(.1)
            else:raise RuntimeError('Old listener has not stopped')
    else:
        try:health(port)
        except OSError:pass
        else:raise RuntimeError('Port already serves a process')
    state.mkdir(parents=True,exist_ok=True)
    if a.action!='rollback':
        profile_path=state/'runtime-profile.json'
        if profile_path.exists():assert read(profile_path)==profile
        else:profile_path.write_text(json.dumps(profile))
    with (state/'operator.stdout.log').open('a') as out,(state/'operator.stderr.log').open('a') as err:
        process=subprocess.Popen(command,cwd=plan.get('rollback_root',str(ROOT)),stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(100):
        assert process.poll() is None,'Startup failed; use preserved rollback configuration'
        try:
            current=health(port)
            expected=plan.get('rollback_root',str(ROOT))
            if current['code_root']==expected and current['stores']['catalog']==str(db) and current['commit']==plan.get('rollback_head',head):break
        except OSError:pass
        time.sleep(.2)
    else:raise RuntimeError('Startup timeout; inspect operator log and rollback configuration')
    plan.update(health=current,actual_pid=listener(port),at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    (state/'OPERATOR_RECEIPT.json').write_text(json.dumps(plan,indent=2));print(json.dumps(plan))
if __name__=='__main__':main()
