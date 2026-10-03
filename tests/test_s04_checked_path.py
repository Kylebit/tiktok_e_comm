"""Resolved representation controls preserve lexical and redirect boundaries."""
import os
from pathlib import Path
import subprocess

import pytest

from shared_platform import capability_runtime as rt

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows resolved-prefix regression')


def representation(monkeypatch,root,candidate,root_extended,candidate_extended):
    original=Path.resolve
    def resolve(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if (path==root and root_extended) or (path==candidate and candidate_extended):
            return Path('\\\\?\\'+str(result))
        return result
    monkeypatch.setattr(Path,'resolve',resolve)


@pytest.mark.parametrize('root_extended,candidate_extended',[(True,False),(False,True),(True,True),(False,False)])
@pytest.mark.parametrize('existing',[False,True])
def test_equivalent_local_resolve_forms_accept_without_changing_return_path(tmp_path,monkeypatch,root_extended,candidate_extended,existing):
    root=tmp_path/'root';candidate=root/'new'/'receipt.json'
    if existing:
        candidate.parent.mkdir(parents=True);candidate.write_text('synthetic')
    representation(monkeypatch,root,candidate,root_extended,candidate_extended)
    assert rt.checked_path(root,'new/receipt.json')==candidate
    if not existing:assert not root.exists()


@pytest.mark.parametrize("escape", ["../outside", "../root-other/file"])
def test_lexical_escape_rejected_before_resolve(tmp_path,monkeypatch,escape):
    monkeypatch.setattr(Path,'resolve',lambda *a,**k:pytest.fail('lexical rejection must precede resolve'))
    with pytest.raises(rt.ToolContextError):rt.checked_path(tmp_path/'root',escape)


@pytest.mark.parametrize('candidate',['\\\\server\\share\\x','\\\\?\\C:\\x','\\\\.\\C:\\x','\\\\?\\UNC\\server\\share\\x','\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\x'])
def test_caller_namespace_rejected_before_resolve(tmp_path,monkeypatch,candidate):
    monkeypatch.setattr(Path,'resolve',lambda *a,**k:pytest.fail('caller namespace must be rejected'))
    with pytest.raises(rt.ToolContextError):rt.checked_path(tmp_path,candidate)


@pytest.mark.parametrize('root_extended',[False,True])
def test_resolved_sibling_is_not_equivalent(tmp_path,monkeypatch,root_extended):
    root=tmp_path/'root';candidate=root/'file'
    original=Path.resolve
    def resolve(path,*args,**kwargs):
        if path==candidate:return Path('\\\\?\\'+str(tmp_path/'root-other'/'file'))
        value=original(path,*args,**kwargs)
        return Path('\\\\?\\'+str(value)) if path==root and root_extended else value
    monkeypatch.setattr(Path,'resolve',resolve)
    with pytest.raises(rt.ToolContextError):rt.checked_path(root,'file')


@pytest.mark.parametrize('target',['\\\\server\\share\\x','\\\\?\\UNC\\server\\share\\x','\\\\.\\C:\\x','\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\x','Z:\\outside'])
def test_nonlocal_or_different_drive_resolve_rejected(tmp_path,monkeypatch,target):
    root=tmp_path/'root';candidate=root/'file';original=Path.resolve
    monkeypatch.setattr(Path,'resolve',lambda p,*a,**k:Path(target) if p==candidate else original(p,*a,**k))
    with pytest.raises(rt.ToolContextError):rt.checked_path(root,'file')


@pytest.mark.parametrize('kind',['file','directory','junction'])
def test_real_redirect_is_rejected_before_resolving_target(tmp_path,monkeypatch,kind):
    root=tmp_path/'root';root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    canary=outside/'canary';canary.write_text('synthetic unchanged')
    link=root/'link'
    if kind=='junction':
        result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(outside)],capture_output=True)
        assert result.returncode==0,result.stderr
    else:os.symlink(canary if kind=='file' else outside,link,target_is_directory=kind=='directory')
    monkeypatch.setattr(Path,'resolve',lambda *a,**k:pytest.fail('reparse rejection must precede resolve'))
    with pytest.raises(rt.ToolContextError,match='symlink|reparse'):rt.checked_path(root,'link' if kind=='file' else 'link/canary')
    assert canary.read_text()=='synthetic unchanged'


def test_registry_consumer_retains_current_catalog_stages():
    from shared_platform.orbit_registry import navigation_payload
    root=Path(__file__).resolve().parents[1]
    catalog=rt.catalog(root)
    entries={row['id']:row for row in catalog['skills']+catalog['tools']}
    cards={row['id']:row for row in navigation_payload(root=root)['tools']}
    for name in ['tikhub','duoplus','publication-knowledge','publish-approved-product','apply-product-discounts']:
        assert cards[name]['stage']==entries[name]['stage']
        assert cards[name]['status']!='工具包未核验'


def test_checked_path_checks_every_component_with_one_fresh_lstat_each_call(tmp_path,monkeypatch):
    root=tmp_path/'root';candidate=root/'nested'/'file';candidate.parent.mkdir(parents=True);candidate.write_bytes(b'original')
    expected=[*reversed(root.parents),root,root/'nested',candidate]
    original=Path.lstat;calls=[]
    def observed(path):calls.append(path);return original(path)
    monkeypatch.setattr(Path,'lstat',observed)
    monkeypatch.setattr(Path,'is_symlink',lambda *a:pytest.fail('redundant lstat through is_symlink'))
    assert rt.checked_path(root,'nested/file')==candidate
    assert calls==expected
    calls.clear();candidate.write_bytes(b'changed!')
    assert rt.checked_path(root,'nested/file')==candidate
    assert calls==expected


@pytest.mark.parametrize('kind',['directory_link','junction'])
def test_single_stat_keeps_actual_ancestor_redirect_rejection_before_resolve(tmp_path,monkeypatch,kind):
    target=tmp_path/'target';(target/'nested').mkdir(parents=True);canary=target/'nested'/'canary';canary.write_bytes(b'unchanged')
    alias=tmp_path/'alias'
    if kind=='junction':
        import _winapi
        _winapi.CreateJunction(str(target),str(alias))
        assert alias.lstat().st_file_attributes & 0x400
    else:alias.symlink_to(target,target_is_directory=True)
    monkeypatch.setattr(Path,'resolve',lambda *a,**k:pytest.fail('ancestor rejection must precede resolving target'))
    with pytest.raises(rt.ToolContextError,match='symlink|reparse'):
        rt.checked_path(alias/'nested','canary')
    assert canary.read_bytes()==b'unchanged'


def test_single_stat_rejects_actual_dangling_symlink_before_resolve(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();link=root/'link';link.symlink_to(tmp_path/'missing')
    assert link.is_symlink() and not link.exists()
    monkeypatch.setattr(Path,'resolve',lambda *a,**k:pytest.fail('dangling link rejection must precede resolve'))
    with pytest.raises(rt.ToolContextError,match='symlink|reparse'):rt.checked_path(root,'link')


@pytest.mark.parametrize('kind',['symlink_mode_only','reparse_attribute_only'])
def test_single_stat_rejects_each_fresh_redirect_indicator_before_resolve(tmp_path,monkeypatch,kind):
    from stat import S_IFLNK,S_IFREG
    from types import SimpleNamespace
    root=tmp_path/'root';root.mkdir();candidate=root/'file';candidate.write_bytes(b'canary');original=Path.lstat
    def observed(path):
        if path==candidate:return SimpleNamespace(st_mode=S_IFLNK if kind=='symlink_mode_only' else S_IFREG,st_file_attributes=0 if kind=='symlink_mode_only' else 0x400)
        return original(path)
    monkeypatch.setattr(Path,'lstat',observed)
    monkeypatch.setattr(Path,'resolve',lambda *a,**k:pytest.fail('fresh stat rejection must precede resolve'))
    with pytest.raises(rt.ToolContextError,match='symlink|reparse'):rt.checked_path(root,'file')


def test_single_stat_never_suppresses_unexpected_lstat_error(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();original=Path.lstat
    def observed(path):
        if path==root:raise PermissionError('actual component unavailable')
        return original(path)
    monkeypatch.setattr(Path,'lstat',observed)
    monkeypatch.setattr(Path,'resolve',lambda *a,**k:pytest.fail('lstat error must stop before resolve'))
    with pytest.raises(PermissionError,match='component unavailable'):rt.checked_path(root,'file')
