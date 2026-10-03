import json
import os
from pathlib import Path

import pytest

from scripts import sync_product_publication_skills as suite
from scripts import sync_publish_approved_product_skill as single
from scripts import package_agent_tools as package

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows supported-path planning')


def snapshot(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def long_history_root(tmp_path):
    base=tmp_path/'x'/'installed'
    leaf=base/'.history/delist-products-by-sku'/('a'*64)/'scripts'
    padding=248-len(str(leaf))+1
    assert padding>0
    return tmp_path/('x'*padding)/'installed'


@pytest.mark.parametrize('install',[False,True])
def test_all_registry_history_paths_block_before_any_selected_write(tmp_path,install):
    root=long_history_root(tmp_path);names=['use-lingshi-ai','delist-products-by-sku']
    suite.sync_registered(runtime_root=ROOT,destination_root=root,names=names,install=True)
    for name in names:
        p=root/name/'SKILL.md';p.write_text(p.read_text(encoding='utf-8')+'\nSynthetic prior revision\n',encoding='utf-8')
    before=snapshot(root)
    with pytest.raises(ValueError,match='LONG_PATH_UNSUPPORTED'):
        suite.sync_registered(runtime_root=ROOT,destination_root=root,names=names,install=install)
    assert snapshot(root)==before and not (root/'.history').exists()


def test_single_atomic_temp_is_preflighted_before_target_creation(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('synthetic')
    filename='f'*100+'.md';(source/filename).write_text('deep')
    target=tmp_path/('d'*(250-len(str(tmp_path))-len(filename)-2))
    assert len(str(target/filename))==250
    with pytest.raises(ValueError,match='LONG_PATH_UNSUPPORTED') as caught:single.sync_install(source,target)
    assert any(row['kind']=='temporary_file_template' for row in caught.value.diagnostic['blocked_paths'])
    assert not target.exists()


def test_package_preview_and_build_share_fail_before_write(tmp_path):
    target=tmp_path/('p'*150)
    for write in [False,True]:
        with pytest.raises(ValueError,match='LONG_PATH_UNSUPPORTED'):package.build(ROOT,target,write=write)
        assert not target.exists()


def test_partial_history_is_reported_before_any_selected_install(tmp_path):
    root=tmp_path/'installed';names=['use-lingshi-ai','delist-products-by-sku']
    suite.sync_registered(runtime_root=ROOT,destination_root=root,names=names,install=True)
    for name in names:
        p=root/name/'SKILL.md';p.write_text(p.read_text(encoding='utf-8')+'\nSynthetic prior revision\n',encoding='utf-8')
    previous=single.build_manifest(root/names[1])
    backup=root/'.history'/names[1]/previous.digest
    backup.mkdir(parents=True);(backup/'SKILL.md').write_bytes((root/names[1]/'SKILL.md').read_bytes())
    before=snapshot(root)
    with pytest.raises(ValueError,match='PARTIAL_BACKUP_REQUIRES_REVIEW'):
        suite.sync_registered(runtime_root=ROOT,destination_root=root,names=names,install=True)
    assert snapshot(root)==before


def test_utf16_component_and_explicit_shallow_recovery_diagnostic(tmp_path):
    from shared_platform.capability_runtime import preflight_write_paths
    path=tmp_path/('😀'*128)
    with pytest.raises(ValueError,match='LONG_PATH_UNSUPPORTED') as caught:
        preflight_write_paths(tmp_path,[('file',path)],operation='synthetic')
    result=caught.value.diagnostic
    assert result['writes_performed']==[] and result['blocked_paths'][0]['oversize_components']
    assert result['blocked_paths'][0]['utf16_units']>len(str(path))
    assert 'explicit' in result['recovery']


def test_backup_io_failure_preserves_old_install_and_reports_partial(tmp_path,monkeypatch):
    import shutil
    root=tmp_path/'installed';name='use-lingshi-ai'
    suite.sync_registered(runtime_root=ROOT,destination_root=root,names=[name],install=True)
    p=root/name/'SKILL.md';p.write_text(p.read_text(encoding='utf-8')+'\nSynthetic prior revision\n',encoding='utf-8')
    before=snapshot(root/name)
    def partial(source,target):
        target.mkdir();(target/'SKILL.md').write_bytes((source/'SKILL.md').read_bytes())
        raise OSError(5,'synthetic copy failure')
    monkeypatch.setattr(shutil,'copytree',partial)
    with pytest.raises(ValueError,match='REGISTERED_SKILL_WRITE_INTERRUPTED') as caught:
        suite.sync_registered(runtime_root=ROOT,destination_root=root,names=[name],install=True)
    result=caught.value.diagnostic
    assert result['stage']=='creating-backup' and result['completed_skills']==[]
    assert snapshot(root/name)==before
    assert (Path(result['possible_backup_paths'][name])/'SKILL.md').exists()


def test_atomic_replace_failure_retains_temp_and_managed_old_file(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('new')
    target=tmp_path/'target';target.mkdir();(target/'SKILL.md').write_text('old')
    def fail(*args):raise OSError(5,'synthetic replace failure')
    monkeypatch.setattr(single.os,'replace',fail)
    with pytest.raises(ValueError,match='SKILL_INSTALL_IO_INTERRUPTED') as caught:single.sync_install(source,target)
    assert (target/'SKILL.md').read_text()=='old'
    assert Path(caught.value.diagnostic['possible_partial_temp']).read_text()=='new'
    assert caught.value.diagnostic['completed_files']==[]


def test_package_io_interruption_is_not_a_valid_runtime(tmp_path,monkeypatch):
    target=tmp_path/'new-package'
    original=Path.write_bytes
    def fail_after_first(path,data):
        if path.is_relative_to(target) and path.name=='tool_profile.example.json':
            raise OSError(5,'synthetic package interruption')
        return original(path,data)
    monkeypatch.setattr(Path,'write_bytes',fail_after_first)
    with pytest.raises(ValueError,match='PACKAGE_WRITE_INTERRUPTED') as caught:package.build(ROOT,target,write=True)
    assert (target/'config/capability_catalog.json').is_file()
    assert not (target/'config/tool_runtime_manifest.json').exists()
    assert 'config/capability_catalog.json' in caught.value.diagnostic['completed_files']
