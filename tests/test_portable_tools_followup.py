from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from modules.tools import duoplus
from scripts.orbit_tools import main
from scripts.sync_product_publication_skills import sync_registered, build_manifest
from shared_platform.capability_runtime import ToolContextError
from tests.test_portable_tool_context import profile, ROOT


@pytest.mark.parametrize('operation,payload,endpoint', [
    ('devices', {'page':1,'pagesize':20}, '/api/v1/cloudPhone/list'),
    ('info', {'image_id':'device-a'}, '/api/v1/cloudPhone/info'),
    ('status', {'image_ids':['device-a']}, '/api/v1/cloudPhone/status'),
    ('apps', {'page':1,'pagesize':100}, '/api/v1/app/list'),
    ('installed-apps', {'image_id':'device-a'}, '/api/v1/app/installedList'),
])
def test_cli_read_uses_exact_profile_and_real_sdk_without_install_authority(tmp_path,monkeypatch,capsys,operation,payload,endpoint):
    context=profile(tmp_path);path=tmp_path/'request.json';path.write_text(json.dumps(payload),encoding='utf-8')
    monkeypatch.setenv('ALPHA_DUO','fixture-key-only');calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'code':200,'data':{'id':'device-a','adb_password':'fixture-password'}}).encode()
    def http(req,timeout):
        calls.append((req.full_url,req.method,json.loads(req.data),req.get_header('Duoplus-api-key')))
        return Response()
    monkeypatch.setattr(duoplus,'open_request',http)
    assert main(['--runtime-root',str(ROOT),'--project-root',str(tmp_path),'--profile','profile.json',
                 'read','duoplus',operation,'--payload','request.json'])==0
    result=json.loads(capsys.readouterr().out)
    assert calls==[('https://openapi.duoplus.cn'+endpoint,'POST',payload,'fixture-key-only')]
    assert result['response']['data']['adb_password']=='***REDACTED***'
    assert result['profile_digest']==context['profile_digest'] and result['cloud_phone_mutation_performed'] is False
    assert not (tmp_path/'artifacts').exists()


@pytest.mark.parametrize('directory_redirect', [False,True])
def test_existing_nested_backup_redirect_is_refused_before_external_read(tmp_path,directory_redirect):
    dest=tmp_path/'installed';name='use-lingshi-ai'
    sync_registered(runtime_root=ROOT,destination_root=dest,names=[name],install=True)
    current=dest/name;prior=build_manifest(current)
    backup=dest/'.history'/name/prior.digest;shutil.copytree(current,backup)
    external=tmp_path/'external';external.mkdir();canary=external/'canary.md';canary.write_text('outside original',encoding='utf-8')
    alias=backup/'references'/('redirect-dir' if directory_redirect else 'redirect.md')
    os.symlink(external if directory_redirect else canary,alias,target_is_directory=directory_redirect)
    active=[True];outside=[]
    def audit(event,args):
        if active[0] and event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve().is_relative_to(external.resolve()):
                outside.append(str(args[0]));raise AssertionError('EXTERNAL_BACKUP_READ')
    sys.addaudithook(audit)
    try:
        with pytest.raises(ToolContextError,match='symlink|reparse'):
            sync_registered(runtime_root=ROOT,destination_root=dest,names=[name],install=True)
    finally:active[0]=False
    assert not outside and canary.read_text(encoding='utf-8')=='outside original'
    assert build_manifest(current).digest==prior.digest and alias.is_symlink()
