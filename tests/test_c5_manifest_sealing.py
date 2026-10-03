"""Source CLI must survive a real generated commit without hiding content drift."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

from tests.test_c5_portable_composition import ROOT, cli


@pytest.fixture
def source(tmp_path_factory):
    folder=tmp_path_factory.mktemp('g');root=folder/'r';project=folder/'p';project.mkdir()
    result=cli(ROOT,project,'package_agent_tools.py','--runtime-root',ROOT,'--destination',root,'--build')
    assert result.returncode==0,result.stdout+result.stderr
    shutil.copyfile(ROOT/'scripts/refresh_tool_manifests.py',root/'scripts/refresh_tool_manifests.py')
    path=root/'config/capability_catalog.json';catalog=json.loads(path.read_text(encoding='utf-8'))
    for kind in ('skills','tools'):
        catalog[kind]=[r for r in catalog[kind] if r['stage'].startswith('PORTABLE_')]
    catalog['tools'].append({'id':'pending-fixture','stage':'PENDING_B4B_COMPOSITION',
        'required_files':['absent-business-contract.py'],'file_digests':{},'source':{}})
    path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    git(root,'init');git(root,'add','.');git(root,'-c','user.name=C5 fixture','-c','user.email=c5@example.invalid','commit','-m','Synthetic source checkpoint')
    return root,project


def git(root,*args):
    result=subprocess.run(['git','-c','safe.directory='+str(root),'-C',str(root),*args],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr
    return result.stdout.strip()


def refresh(root,project,*args):
    return cli(root,project,'refresh_tool_manifests.py','--runtime-root',root,
               '--source-basis-commit',git(root,'rev-parse','HEAD'),*args)


def test_current_checkout_catalog_and_runtime_manifest_are_sealed(tmp_path):
    """File hashes alone cannot validate stale per-capability composed digests."""
    checked = refresh(ROOT, tmp_path)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    result = json.loads(checked.stdout)
    assert result['ok'] is True
    assert result['catalog_changed'] is False
    assert result['runtime_changed'] is False
    assert result['checked_head'] == git(ROOT, 'rev-parse', 'HEAD')


def test_real_generate_commit_check_and_content_drift(source):
    root,project=source
    generated=refresh(root,project,'--write');assert generated.returncode==0,generated.stdout+generated.stderr
    git(root,'add','.');git(root,'-c','user.name=C5 fixture','-c','user.email=c5@example.invalid','commit','-m','Seal generated content')
    before={p:p.read_bytes() for p in (root/'config').glob('*.json')}
    checked=refresh(root,project);assert checked.returncode==0,checked.stdout+checked.stderr
    result=json.loads(checked.stdout)
    assert result['missing_declared_files']['pending-fixture']==['absent-business-contract.py']
    assert result['unavailable_capabilities']['pending-fixture']['stage']=='PENDING_B4B_COMPOSITION'
    assert not result['blocking_missing_files']
    assert result['workflow_runtime_ready'] is True
    assert not result['workflow_runtime_blockers']
    assert git(root,'status','--porcelain=v1','-uall')==''
    profile=project/'profile.json';profile.write_text(json.dumps({'schema':'orbit-tool-profile/v1','tenant_id':'sealed',
        'artifact_root':'a','providers':{}}),encoding='utf-8')
    doctor_args=['--runtime-root',root,'--project-root',project,'--profile','profile.json','doctor','--capability','duoplus']
    assert cli(root,project,'orbit_tools.py',*doctor_args).returncode==0
    target=root/'modules/tools/duoplus.py';target.write_bytes(target.read_bytes()+b'\n# unsealed source change\n')
    drift=refresh(root,project);assert drift.returncode!=0 and json.loads(drift.stdout)['catalog_changed']
    assert cli(root,project,'orbit_tools.py',*doctor_args).returncode!=0
    assert all(p.read_bytes()==raw for p,raw in before.items())
    wrong=cli(root,project,'refresh_tool_manifests.py','--runtime-root',root,'--source-basis-commit','0'*40)
    assert wrong.returncode!=0


@pytest.mark.parametrize('stage',['PORTABLE_OFFLINE_ENTRY','UNRECOGNIZED_EXECUTABLE_STAGE'])
def test_missing_executable_or_unknown_requirement_blocks_generation(source,stage):
    root,project=source
    path=root/'config/capability_catalog.json';catalog=json.loads(path.read_text(encoding='utf-8'))
    row=next(r for r in catalog['tools'] if r['id']=='pending-fixture');row['stage']=stage
    path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    before={p:p.read_bytes() for p in (root/'config').glob('*.json')}
    result=refresh(root,project,'--write')
    assert result.returncode!=0,result.stdout+result.stderr
    assert json.loads(result.stdout)['blocking_missing_files']['pending-fixture']==['absent-business-contract.py']
    assert all(p.read_bytes()==raw for p,raw in before.items())
