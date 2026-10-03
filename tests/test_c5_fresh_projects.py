from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from modules.product_agent import knowledge as k
from tests.test_c5_portable_composition import ROOT,cli

@pytest.fixture
def tmp_path(tmp_path_factory):return tmp_path_factory.mktemp('n')

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def ok(result):
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(result.stdout)

@pytest.mark.parametrize('tenant',['fresh-alpha','fresh-beta'])
def test_new_project_actual_package_cli_install_and_knowledge_version_journey(tmp_path,tenant):
    runtime=tmp_path/'r';project=tmp_path/'p';project.mkdir()
    preview=ok(cli(ROOT,project,'package_agent_tools.py','--runtime-root',ROOT,'--destination',runtime))
    assert not runtime.exists()
    package=ok(cli(ROOT,project,'package_agent_tools.py','--runtime-root',ROOT,'--destination',runtime,'--build'))
    assert package['manifest']==preview['manifest']
    assert package['manifest']['runtime_requirements']['minimum_python']=='3.11'
    assert set(p.relative_to(runtime).as_posix() for p in runtime.rglob('*') if p.is_file())==set(package['manifest']['files'])|{'config/tool_runtime_manifest.json'}
    write(project/'profile.json',{'schema':'orbit-tool-profile/v1','tenant_id':tenant,'artifact_root':'a','providers':{
        'duoplus':{'origin':'https://openapi.duoplus.cn','credential_env':'C5_MISSING_DUO'},
        'tikhub':{'origin':'https://api.tikhub.io','credential_env':'TikHub'},
        'lingshi':{'origin':'https://api.lk888.ai','credential_env':'C5_MISSING_LINGSHI'}}})
    prefix=['--runtime-root',runtime,'--project-root',project,'--profile','profile.json']
    for capability in ['duoplus','tikhub','lingshi','publication-knowledge']:
        result=ok(cli(runtime,project,'orbit_tools.py',*prefix,'doctor','--capability',capability))
        assert result['network_call_performed'] is False and result['live_health_verified'] is False
        assert not any(row['key_present'] for row in result['credentials'].values())
    unavailable=cli(runtime,project,'orbit_tools.py',*prefix,'doctor','--capability','prepare-product-images')
    assert unavailable.returncode==1 and json.loads(unavailable.stdout)['capabilities'][0]['missing_files']
    for provider,operation,payload in [('duoplus','devices',{}),('tikhub','query',{'keyword':'synthetic','region':'TH','video_count':1}),
        ('lingshi','media',{'model':'example','prompt':'synthetic offline','params':{}})]:
        write(project/'request.json',payload)
        result=ok(cli(runtime,project,'orbit_tools.py',*prefix,'preview',provider,operation,'--payload','request.json'))
        assert result['request_attempted'] is False and result['credential_value_read'] is False
    install=project/'installed'
    install_args=['--registry','--runtime-root',runtime,'--destination-root',install]
    for name in ['use-lingshi-ai','control-duoplus-cloud-phone','research-tikhub-reference']:install_args+=['--skill',name]
    check=cli(runtime,project,'sync_product_publication_skills.py',*install_args,'--check')
    assert check.returncode!=0 and not install.exists()
    ok(cli(runtime,project,'sync_product_publication_skills.py',*install_args,'--install'))
    parity=ok(cli(runtime,project,'sync_product_publication_skills.py',*install_args,'--check'))
    assert parity['ok']
    vault=project/'vault';section=vault/'rules';section.mkdir(parents=True)
    reviewed=section/'rule.md';reviewed.write_text('Approved synthetic '+tenant+' rule.\n',encoding='utf-8')
    (section/'reference.md').write_text('Unreviewed synthetic reference.\n',encoding='utf-8')
    kp=['--runtime-root',runtime,'--project-root',project]
    unknown=cli(runtime,project,'sync_product_publication_knowledge.py','export-preview',*kp,'--vault','vault','--section','rules','--source-id',tenant)
    assert unknown.returncode!=0
    review={'schema_version':k.REVIEW_SCHEMA,'source':k.source_identity(vault,Path('rules'),tenant),
        'review_id':'fixture-c5-v1','review_scope':'one synthetic rule','reviewed_at':'2026-09-05T00:00:00+00:00',
        'instruction_ref':'fixture://c5-bounded-review','documents':[{'document_id':'rule-a','path':'rules/rule.md',
        'source_digest':'sha256:'+hashlib.sha256(reviewed.read_bytes()).hexdigest(),'usage':'runtime_rule','source_date':'2026-09-05','supersedes':[],'report_refs':['fixture://c5-evidence']}]}
    review['manifest_digest']=k.version_digest(review);write(project/'review.json',review)
    profile={'schema_version':'product-publication-knowledge-profile/v1','project_id':tenant,'vault_root':'vault','source_id':tenant,
        'section':'rules','review_manifest':'review.json','expected_review_digest':review['manifest_digest']}
    write(project/'knowledge.json',profile)
    export=['export-preview',*kp,'--profile','knowledge.json','--output','v1.json']
    old=ok(cli(runtime,project,'sync_product_publication_knowledge.py',*export))
    assert old['document_count']==1 and not (project/'v1.json').exists()
    ok(cli(runtime,project,'sync_product_publication_knowledge.py',*export,'--write'))
    original=(project/'v1.json').read_bytes()
    ok(cli(runtime,project,'sync_product_publication_knowledge.py','parity',*kp,'--profile','knowledge.json','--snapshot','v1.json'))
    loaded=ok(cli(runtime,project,'sync_product_publication_knowledge.py','inspect',*kp,'--snapshot','v1.json','--expected-version',old['knowledge_version'],'--query',tenant))
    assert len(loaded['matches'])==1
    reviewed.write_text('New separately reviewed synthetic rule.\n',encoding='utf-8')
    changed=cli(runtime,project,'sync_product_publication_knowledge.py',*export)
    assert changed.returncode!=0 and 'SOURCE_DIGEST_CHANGED' in changed.stdout
    review['review_id']='fixture-c5-v2';review['documents'][0]['source_digest']='sha256:'+hashlib.sha256(reviewed.read_bytes()).hexdigest()
    review['documents'][0]['supersedes']=['rule-a@'+old['knowledge_version']]
    review['manifest_digest']=k.version_digest({key:value for key,value in review.items() if key!='manifest_digest'});write(project/'review.json',review)
    profile['expected_review_digest']=review['manifest_digest'];write(project/'knowledge.json',profile)
    fresh=ok(cli(runtime,project,'sync_product_publication_knowledge.py','export-preview',*kp,'--profile','knowledge.json','--output','v2.json','--write'))
    assert fresh['knowledge_version']!=old['knowledge_version']
    wrong=cli(runtime,project,'sync_product_publication_knowledge.py','inspect',*kp,'--snapshot','v2.json','--expected-version',old['knowledge_version'],'--query','rule')
    assert wrong.returncode!=0 and 'expected version' in wrong.stdout
    assert (project/'v1.json').read_bytes()==original
    code="import json,sys;sys.path.insert(0,sys.argv[1]);import modules.product_agent as p;import modules.product_agent.knowledge as k;import shared_platform.capability_runtime as c;print(json.dumps({'paths':[p.__file__,k.__file__,c.__file__],'shadow':[m for m in sys.modules if m.startswith('modules.product_agent.') and m!='modules.product_agent.knowledge']}))"
    imports=subprocess.run([sys.executable,'-I','-B','-c',code,str(runtime)],cwd=project,capture_output=True,text=True,encoding='utf-8',timeout=20)
    imported=ok(imports);assert not imported['shadow'] and all(Path(p).is_relative_to(runtime) for p in imported['paths'])
    write(project/'actual-import-paths.json',imported)
