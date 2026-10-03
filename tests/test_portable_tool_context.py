from __future__ import annotations
import json
from pathlib import Path
import shutil

import pytest

from shared_platform import capability_runtime as rt
from scripts.package_agent_tools import build
from scripts.sync_product_publication_skills import sync_registered, SkillSetInstallError

ROOT = Path(__file__).resolve().parents[1]


def profile(tmp_path, tenant='alpha'):
    path=tmp_path/'profile.json'
    path.write_text(json.dumps({'schema':'orbit-tool-profile/v1','tenant_id':tenant,'artifact_root':'artifacts',
        'providers':{'lingshi':{'origin':'https://api.lk888.ai','credential_env':tenant.upper()+'_KEY'},
                     'duoplus':{'origin':'https://openapi.duoplus.cn','credential_env':tenant.upper()+'_DUO'},
                     'tikhub':{'origin':'https://api.tikhub.io','credential_env':'TikHub'}}}),encoding='utf-8')
    return rt.load_profile(tmp_path,path)


def test_profiles_are_explicit_and_preview_does_not_read_environment(monkeypatch,tmp_path):
    context=profile(tmp_path)
    class NoValues(dict):
        def get(self,*args):raise AssertionError('environment value read')
    monkeypatch.setattr(rt.os,'environ',NoValues({'ALPHA_KEY':'fixture-only'}))
    doctor=rt.doctor(ROOT,context,'lingshi')
    assert doctor['credentials']['lingshi']['key_present'] is True
    result=rt.preview(ROOT,context,'lingshi','media',{'model':'example','prompt':'fixture','params':{}})
    assert result['request_attempted'] is False and result['credential_value_read'] is False
    assert result['request']['payload']['prompt']=='fixture'


def test_profile_rejects_secret_fields_and_outside_artifact_root(tmp_path):
    context=profile(tmp_path);path=context['profile_path'];data=context['profile']
    data['api_key']='synthetic';path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(rt.ToolContextError):rt.load_profile(tmp_path,path)
    data.pop('api_key');data['artifact_root']='../outside';path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(rt.ToolContextError):rt.load_profile(tmp_path,path)


def test_profile_identity_changes_request_digest_without_importing_business_defaults(tmp_path):
    first=profile(tmp_path,'alpha');second=profile(tmp_path,'beta')
    payload={'keyword':'fixture','region':'TH','video_count':20}
    a=rt.preview(ROOT,first,'tikhub','query',payload);b=rt.preview(ROOT,second,'tikhub','query',payload)
    assert a['request_digest']!=b['request_digest'] and a['profile_digest']!=b['profile_digest']
    assert a['request']['authorization_scope']['plan_digest']==rt.digest({k:v for k,v in a['request'].items() if k!='authorization_scope'})


def test_package_is_new_manifest_bound_and_missing_workflow_is_explicit(tmp_path):
    package=tmp_path/'package';build(ROOT,package,write=True)
    project=tmp_path/'project';project.mkdir();context=profile(project)
    assert rt.doctor(package,context,'lingshi')['ok'] is True
    missing=rt.doctor(package,context,'prepare-product-images')
    assert missing['ok'] is False and missing['capabilities'][0]['missing_files']
    with pytest.raises(ValueError,match='new'):build(ROOT,package,write=True)
    (package/'modules/tools/tikhub.py').write_text('# tampered',encoding='utf-8')
    with pytest.raises(rt.ToolContextError,match='changed_runtime_files'):rt.validate_runtime(package)


def test_missing_python_dependency_is_not_ready(monkeypatch,tmp_path):
    context=profile(tmp_path)
    monkeypatch.setattr(rt.importlib.util,'find_spec',lambda name:None)
    result=rt.doctor(ROOT,context,'lingshi')
    assert not result['ok'] and result['capabilities'][0]['missing_python_modules']==['requests']


def test_portable_closure_requires_unbundled_full_runtime(tmp_path):
    package=tmp_path/'closure-package';build(ROOT,package,write=True)
    project=tmp_path/'closure-project';project.mkdir()
    result=rt.doctor(package,profile(project),'publication-closure')
    row=result['capabilities'][0]
    assert result['ok'] is False
    assert 'shared_platform/product_publication_closure.py' in row['missing_files']
    assert not (package/'shared_platform/product_publication_closure.py').exists()


def test_registered_install_uses_full_sources_and_preserves_extras_and_previous_version(tmp_path):
    destination=tmp_path/'installed';names=['delist-products-by-sku','use-lingshi-ai']
    before=sync_registered(runtime_root=ROOT,destination_root=destination,names=names)
    assert not before['ok'] and not destination.exists()
    installed=sync_registered(runtime_root=ROOT,destination_root=destination,names=names,install=True)
    assert installed['ok']
    source=destination/'delist-products-by-sku/scripts/delist_products_by_sku.py'
    source.write_text(source.read_text(encoding='utf-8')+'\n# prior local version\n',encoding='utf-8')
    prior=source.read_bytes()
    restored=sync_registered(runtime_root=ROOT,destination_root=destination,names=names,install=True)
    backup=destination/restored['backups']['delist-products-by-sku']/'scripts/delist_products_by_sku.py'
    assert backup.read_bytes()==prior and restored['ok']
    extra=destination/'use-lingshi-ai/user-notes.md';extra.write_text('retain',encoding='utf-8')
    report=sync_registered(runtime_root=ROOT,destination_root=destination,names=names)
    assert report['skills']['use-lingshi-ai']['extra_files']==['user-notes.md']
    with pytest.raises(SkillSetInstallError,match='unmanaged'):
        sync_registered(runtime_root=ROOT,destination_root=destination,names=names,install=True)
    assert extra.read_text(encoding='utf-8')=='retain'
