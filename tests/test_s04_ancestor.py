"""Real Windows ancestor redirects and audited synthetic consumer access."""
from contextlib import contextmanager
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from shared_platform import capability_runtime as rt
from scripts import package_agent_tools as package
from scripts import sync_product_publication_skills as suite
from scripts import sync_publish_approved_product_skill as single

ROOT=Path(__file__).resolve().parents[1]
GUARD_RECORDS=[]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='real Windows ancestor redirects')


@pytest.fixture(params=['junction','directory_link'])
def redirected(tmp_path,request):
    outside=tmp_path/'target';outside.mkdir()
    nested=outside/'nested';nested.mkdir()
    alias=tmp_path/'alias'
    if request.param=='junction':
        p=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(alias),str(outside)],capture_output=True)
        assert p.returncode==0,p.stderr
    else:os.symlink(outside,alias,target_is_directory=True)
    return alias/'nested',nested,outside


@contextmanager
def no_target_access(target,deny_process=False):
    target=target.resolve();events=[];active=True;busy=False
    def guard(event,args):
        nonlocal busy
        if not active or busy:return
        if deny_process and event=='subprocess.Popen':
            events.append({'event':event})
            raise AssertionError('process attempted before raw-root validation')
        if event not in {'open','os.mkdir','os.remove','os.rmdir','os.rename','os.listdir','os.scandir'}:return
        paths=args[:2] if event=='os.rename' else args[:1]
        for value in paths:
            if not isinstance(value,(str,bytes,os.PathLike)):continue
            busy=True
            try:path=Path(os.fsdecode(value)).absolute().resolve()
            finally:busy=False
            if path==target or path.is_relative_to(target):
                events.append({'event':event,'path':str(value),'resolved':str(path)})
                raise AssertionError('synthetic target access before ancestor rejection: '+str(events[-1]))
    sys.addaudithook(guard)
    try:yield events
    finally:
        active=False
        GUARD_RECORDS.append({'target':str(target),'events':list(events),'deny_process':deny_process})


def rejected_without_access(target,operation):
    with no_target_access(target) as events:
        with pytest.raises(rt.ToolContextError,match='symlink|reparse'):
            operation()
    assert events==[]


def test_profile_ancestor_is_rejected_before_canary_read(redirected):
    raw,target,outside=redirected
    (target/'profile.json').write_text(json.dumps({'schema':'orbit-tool-profile/v1','tenant_id':'synthetic','artifact_root':'artifacts','providers':{}}))
    rejected_without_access(outside,lambda:rt.load_profile(raw,'profile.json'))


@pytest.mark.parametrize('provider',['duoplus','tikhub'])
def test_provider_artifact_ancestor_rejected_before_local_or_provider_write(redirected,provider):
    from test_portable_provider_recovery import FakeDuo,run,collect
    raw,target,outside=redirected;client=FakeDuo();calls=[]
    operation=(lambda:run(client,raw)) if provider=='duoplus' else (lambda:collect(raw,lambda *a:calls.append(a)))
    rejected_without_access(outside,operation)
    assert not client.installs and not calls and not list(target.iterdir())


def test_registry_skill_install_ancestor_rejected_before_copy(redirected):
    raw,target,outside=redirected
    rejected_without_access(outside,lambda:suite.sync_registered(runtime_root=ROOT,destination_root=raw,names=['use-lingshi-ai'],install=True))
    assert not list(target.iterdir())


def test_package_destination_ancestor_rejected_before_creation(redirected):
    raw,target,outside=redirected
    rejected_without_access(outside,lambda:package.build(ROOT,raw/'package',write=True))
    assert not list(target.iterdir())


def test_navigation_preserves_raw_repository_before_read(redirected):
    from shared_platform.orbit_registry import navigation_payload
    raw,target,outside=redirected
    (target/'shared_platform').mkdir()
    (target/'shared_platform/entry_catalog.json').write_bytes((ROOT/'shared_platform/entry_catalog.json').read_bytes())
    rejected_without_access(outside,lambda:navigation_payload(root=raw))


@pytest.mark.parametrize('mode',['check','install'])
def test_legacy_suite_destination_raw_ancestor_is_validated(redirected,mode):
    raw,target,outside=redirected
    fn=suite.check_all if mode=='check' else suite.install_all
    rejected_without_access(outside,lambda:fn(source_root=ROOT/'skills',destination_root=raw))


@pytest.mark.parametrize('mode',['manifest','install'])
def test_single_skill_raw_ancestor_is_validated(redirected,tmp_path,mode):
    raw,target,outside=redirected
    (target/'SKILL.md').write_text('synthetic canary')
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('synthetic source')
    fn=(lambda:single.build_manifest(raw)) if mode=='manifest' else (lambda:single.sync_install(source,raw))
    rejected_without_access(outside,fn)


def test_knowledge_anchor_protection_is_already_present(redirected):
    from modules.product_agent.knowledge import _root
    raw,target,outside=redirected
    rejected_without_access(outside,lambda:_root(raw))


def test_explicit_already_resolved_path_has_no_recoverable_alias_provenance(redirected):
    raw,target,outside=redirected
    resolved=raw.resolve()
    assert rt.checked_path(resolved,'new.json')==target/'new.json'
    assert not (target/'new.json').exists()


def test_normal_ancestors_and_missing_directories_remain_lexical(tmp_path):
    root=tmp_path/'new'/'nested'
    assert rt.checked_path(root,'file')==root/'file'
    assert not root.exists()


def test_single_cli_missing_bundled_checker_fails_explicitly(tmp_path):
    script=tmp_path/'scripts/sync_publish_approved_product_skill.py'
    script.parent.mkdir()
    script.write_bytes((ROOT/'scripts/sync_publish_approved_product_skill.py').read_bytes())
    result=subprocess.run([sys.executable,'-I',str(script),'--help'],cwd=tmp_path,capture_output=True)
    assert result.returncode!=0
    assert b'requires the bundled shared_platform/capability_runtime.py' in result.stderr


def test_temporary_history_can_be_restored_through_validated_source(tmp_path):
    destination=tmp_path/'installed';name='use-lingshi-ai'
    suite.sync_registered(runtime_root=ROOT,destination_root=destination,names=[name],install=True)
    target=destination/name
    path=target/'SKILL.md';path.write_text(path.read_text(encoding='utf-8')+'\nSynthetic prior revision\n',encoding='utf-8')
    prior=single.build_manifest(target)
    result=suite.sync_registered(runtime_root=ROOT,destination_root=destination,names=[name],install=True)
    backup=destination/result['backups'][name]
    assert single.build_manifest(backup).digest==prior.digest
    assert single.sync_install(backup,target).digest==prior.digest
    assert single.check_parity(backup,target)['ok']


@pytest.mark.parametrize('entry',['orbit_tools','package_agent_tools','refresh_tool_manifests'])
def test_runtime_launcher_validates_raw_root_before_resolving(redirected,tmp_path,capsys,entry):
    raw,target,outside=redirected
    runtime=target/'runtime';package.build(ROOT,runtime,write=True)
    if entry=='refresh_tool_manifests':
        shutil.copyfile(ROOT/'scripts/refresh_tool_manifests.py',runtime/'scripts/refresh_tool_manifests.py')
    script=runtime/'scripts'/(entry+'.py')
    spec=importlib.util.spec_from_file_location('ancestor_'+entry,script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args=['--runtime-root',str(raw/'runtime')]
    if entry=='orbit_tools':args+=['catalog']
    elif entry=='package_agent_tools':args+=['--destination',str(tmp_path/'new-package'),'--build']
    else:args+=['--source-basis-commit','0'*40]
    with no_target_access(outside,deny_process=True) as events:
        assert module.main(args)==2
    output=json.loads(capsys.readouterr().out)
    assert 'symlink' in output['error'] or 'reparse' in output['error']
    assert events==[]
