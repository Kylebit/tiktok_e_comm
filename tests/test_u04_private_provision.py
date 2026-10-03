from pathlib import Path
import importlib.util,json,os,subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts/provision_private_directory.py'
def module():
    spec=importlib.util.spec_from_file_location('provision',SCRIPT);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_native_private_directory_created_and_verified(tmp_path):
    m=module();target=tmp_path/'synthetic-private-😀';plan=m.prepare(target)
    result=m.execute(plan,{'plan_digest':m.g.plan_digest(plan),'directory_create':True,'authority':'synthetic-only'})
    assert result['state']=='PROTECTED_EMPTY_DIRECTORY';m.g.protected(target);assert not list(target.iterdir())
    with pytest.raises(m.g.RecoveryError,match='TARGET_EXISTS'):m.execute(plan,{'plan_digest':m.g.plan_digest(plan),'directory_create':True,'authority':'synthetic-only'})

@pytest.mark.parametrize('bad',['occupied','nonempty','missing_parent','authority','source','parent_identity','acl_failure'])
def test_provision_refuses_unsafe_or_changed_target(tmp_path,monkeypatch,bad):
    m=module();target=tmp_path/'synthetic-private';plan=m.prepare(target);auth={'plan_digest':m.g.plan_digest(plan),'directory_create':True,'authority':'synthetic-only'}
    if bad in ('occupied','nonempty'):
        target.mkdir()
        if bad=='nonempty':(target/'preserve.txt').write_text('synthetic existing data')
    elif bad=='missing_parent':plan['target']=str(tmp_path/'missing/child');auth['plan_digest']=m.g.plan_digest(plan)
    elif bad=='authority':auth['directory_create']=False
    elif bad=='source':plan['sources']={};auth['plan_digest']=m.g.plan_digest(plan)
    elif bad=='parent_identity':plan['parent_identity']['inode']+=1;auth['plan_digest']=m.g.plan_digest(plan)
    else:
        def fail(*args):raise m.g.RecoveryError('ACL_DESCRIPTOR_FAILED')
        monkeypatch.setattr(m,'_create',fail)
    with pytest.raises(m.g.RecoveryError):m.execute(plan,auth)
    if bad=='nonempty':assert (target/'preserve.txt').read_text()=='synthetic existing data'
    elif bad!='occupied':assert not target.exists()

def test_post_create_verification_failure_preserves_unknown_directory(tmp_path,monkeypatch):
    m=module();target=tmp_path/'synthetic';plan=m.prepare(target);auth={'plan_digest':m.g.plan_digest(plan),'directory_create':True,'authority':'synthetic-only'}
    original=m.verify
    def fail(path):raise OSError('synthetic verification interruption')
    monkeypatch.setattr(m,'verify',fail)
    with pytest.raises(m.g.RecoveryError,match='PROVISION_UNVERIFIED_RECONCILE'):m.execute(plan,auth)
    assert target.is_dir()
    with pytest.raises(m.g.RecoveryError,match='TARGET_EXISTS'):m.execute(plan,auth)
    monkeypatch.setattr(m,'verify',original);assert m.verify(target)['state']=='PROTECTED_EMPTY_DIRECTORY'

def test_real_child_interrupt_after_protected_creation(tmp_path):
    m=module();target=tmp_path/'synthetic-crash-😀';plan=m.prepare(target);public=tmp_path/'plan.json';public.write_text(json.dumps(plan),encoding='utf-8')
    script=tmp_path/'child.py';script.write_text("import importlib.util,sys,json,os\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location('p',sys.argv[1]);p=importlib.util.module_from_spec(s);s.loader.exec_module(p)\nplan=json.loads(Path(sys.argv[2]).read_text());p.verify=lambda path:os._exit(73)\np.execute(plan,{'plan_digest':p.g.plan_digest(plan),'directory_create':True,'authority':'synthetic-only'})\n",encoding='utf-8')
    result=subprocess.run([sys.executable,'-B','-X','utf8',str(script),str(SCRIPT),str(public)],capture_output=True,timeout=20);assert result.returncode==73
    m.g.protected(target)
    with pytest.raises(m.g.RecoveryError,match='TARGET_EXISTS'):m.execute(plan,{'plan_digest':m.g.plan_digest(plan),'directory_create':True,'authority':'synthetic-only'})
    assert m.verify(target)['state']=='PROTECTED_EMPTY_DIRECTORY'

def test_reparse_parent_is_never_provisioned(tmp_path):
    m=module();real=tmp_path/'real';real.mkdir();link=tmp_path/'link'
    if os.name=='nt':
        result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(real)],capture_output=True);assert result.returncode==0
    else:link.symlink_to(real,target_is_directory=True)
    with pytest.raises(m.g.RecoveryError,match='REPARSE_FORBIDDEN'):m.prepare(link/'private')
    assert not (real/'private').exists()
