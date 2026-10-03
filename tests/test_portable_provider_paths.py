from __future__ import annotations
import os
from pathlib import Path
import sys

import pytest

from modules.sourcing.image_generation_checkpoint import digest
from modules.tools import duoplus, tikhub
from shared_platform.capability_runtime import ToolContextError


@pytest.mark.parametrize('provider,leaf', [
    ('duoplus','installs.json'), ('duoplus','installs.json.tmp'), ('duoplus','lock'),
    ('tikhub','manifest.json'), ('tikhub','manifest.json.tmp'), ('tikhub','lock'),
    ('tikhub','products.raw.json'), ('tikhub','products.raw.json.tmp'),
    ('tikhub','videos.raw.json'), ('tikhub','videos.raw.json.tmp')])
@pytest.mark.parametrize('dangling', [False, True])
def test_provider_rejects_preplaced_file_redirect_before_canary_access(tmp_path, provider, leaf, dangling):
    project = tmp_path/'project'; project.mkdir()
    canary = tmp_path/'external-canary.json'
    if not dangling: canary.write_text('outside fixture must remain untouched', encoding='utf-8')
    plan = tikhub.preview(keyword='fixture', region='TH')
    scope = {'tenant_id':'tenant-a','profile_digest':'a'*64,'plan_digest':digest(plan),'maximum_paid_requests':2}
    if provider == 'duoplus':
        directory = project/'duoplus'/('a'*64)
        lock_digest = digest({'tenant':'tenant-a','provider':duoplus.DEFAULT_BASE_URL})
    else:
        directory = project/'tikhub'/('a'*64)/digest(plan)[:24]
        lock_digest = digest(scope)
    directory.mkdir(parents=True)
    alias = directory/(f'.lingshi-{lock_digest[:24]}.lock' if leaf=='lock' else leaf)
    os.symlink(canary, alias)
    active = [True]; outside = []
    def audit(event,args):
        if active[0] and event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve() == canary.resolve():
                outside.append(str(args[0])); raise AssertionError('CANARY_ACCESS_ATTEMPT')
    sys.addaudithook(audit)
    try:
        with pytest.raises(ToolContextError, match='symlink|reparse'):
            if provider == 'duoplus':
                class Client:
                    base_url=duoplus.DEFAULT_BASE_URL
                    def apps(self,**kw):return {'data':{'list':[{'id':'app-a','pkg':'com.example.app','version_list':[{'id':'v-a'}]}]}}
                    def post(self,*args):return {'code':200}
                    def installed_apps(self,*args):return {'data':{'list':['com.example.app']}}
                bound=duoplus.install_scope(tenant_id='tenant-a',profile_digest='a'*64,origin=duoplus.DEFAULT_BASE_URL,
                    image_ids=['device-a'],app_id='app-a',app_version_id='v-a',package='com.example.app')
                duoplus.execute_install(Client(),artifact_root=project,scope=bound,
                    authorization={'authorization_id':'existing','instruction_ref':'fixture://existing','scope':bound})
            else:
                tikhub.collect(plan=plan,tenant_id='tenant-a',profile_digest='a'*64,artifact_root=project,
                    authorization={'authorization_id':'existing','instruction_ref':'fixture://existing','scope':scope},
                    api_key='fixture',request_fn=lambda *args:({'data':[]},{'endpoint':args[0]}))
    finally: active[0]=False
    assert outside == []
    assert (not canary.exists()) if dangling else canary.read_text(encoding='utf-8')=='outside fixture must remain untouched'
