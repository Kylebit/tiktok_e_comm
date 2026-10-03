import importlib.util
import json
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('iteration_projection',ROOT/'tools/knowledge_iteration/build_projection.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def fixture_index(tmp_path):
    vault=tmp_path/'vault';vault.mkdir()
    note=vault/'note.md';note.write_text('synthetic note',encoding='utf-8')
    index=tmp_path/'index.json'
    data={'schema_version':module.INDEX_SCHEMA,'read_only':True,'vault_root':str(vault),'audit_date':'2026-09-08','generated_at_utc':'2026-09-08T00:00:00Z','documents':[{'path':'note.md','kind':'business_fact_or_analysis','freshness':'UNKNOWN_OBSERVED_AT','content_digest':module.digest(note)}]}
    index.write_text(json.dumps(data),encoding='utf-8')
    return index,note,data

def test_index_source_drift_is_not_current_knowledge(tmp_path):
    index,note,data=fixture_index(tmp_path)
    first=module.project(index,tmp_path/'absent.json')
    assert first['source_check']=={'unchanged':1}
    note.write_text('changed synthetic note')
    second=module.project(index,tmp_path/'absent.json')
    assert second['source_check']=={'changed':1}
    assert second['candidate'] is None and second['execution_authority'] is False
    note.unlink()
    assert module.project(index,tmp_path/'absent.json')['source_check']=={'missing':1}

def test_missing_and_empty_index_are_distinct(tmp_path):
    assert module.project(tmp_path/'missing',tmp_path/'missing')['available'] is False
    index,note,data=fixture_index(tmp_path)
    data['documents']=[];index.write_text(json.dumps(data))
    projection=module.project(index,tmp_path/'missing')
    assert projection['available'] is True and projection['notes']==0

def test_path_escape_is_rejected(tmp_path):
    index,note,data=fixture_index(tmp_path)
    data['documents'][0]['path']='../index.json';index.write_text(json.dumps(data))
    assert module.project(index,tmp_path/'missing')['available'] is False

def test_candidate_changed_source_is_not_promoted(tmp_path):
    index,note,data=fixture_index(tmp_path)
    manifest=tmp_path/'candidate.json'
    c={'schema_version':'orbit-knowledge-candidate-manifest/v1','status':'CANDIDATE_NOT_REVIEWED','current_execution_authority':False,'candidate_vault':str(note.parent),'candidate_path':note.name,'candidate_digest':module.digest(note),'source_observed_at':'2026-09-07','verified_head':'a'*40,'source_evidence':[{'path':str(note),'digest':'sha256:'+'0'*64}]}
    manifest.write_text(json.dumps(c))
    candidate=module.project(index,manifest)['candidate']
    assert candidate['matches'] and not candidate['sources'][0]['matches'] and candidate['reviewed'] is False
    c['status']='APPROVED';manifest.write_text(json.dumps(c))
    assert module.project(index,manifest)['candidate'] is None

def test_reused_v2_keeps_date_semantics():
    from datetime import date
    s=importlib.util.spec_from_file_location('accepted_v2',ROOT/'tools/knowledge_iteration/readonly_index_v2.py')
    v2=importlib.util.module_from_spec(s);s.loader.exec_module(v2)
    facts=v2.observation_date_facts({'observed_at':'2026-08-01','updated_at':'2026-09-08'},'',date(2026,9,8))
    assert v2.freshness('business_fact_or_analysis',facts,[],'',date(2026,9,8))[0]=='STALE_FOR_CURRENT_DECISION'

@pytest.mark.parametrize('case',['missing','invalid','deceptive','malformed','traversal'])
def test_cli_rejects_unsafe_output_before_projection(tmp_path,monkeypatch,case):
    project=tmp_path/'project';project.mkdir()
    monkeypatch.setattr(module,'PROJECT_ROOT',project)
    vault=tmp_path/'vault';vault.mkdir()
    guide=vault/'guides.json';guide.write_text('{"schema":"orbit-knowledge-guides/v1"}')
    index=tmp_path/'index.json'
    if case=='invalid':index.write_text(json.dumps({'schema_version':'invalid','vault_root':str(vault)}))
    if case=='deceptive':index.write_text(json.dumps({'schema_version':module.INDEX_SCHEMA,'vault_root':str(tmp_path/'different-vault')}))
    if case=='malformed':index.write_text('{bad')
    output=str(guide) if case!='traversal' else str(project/'..'/'vault'/'guides.json')
    before=guide.read_bytes()
    monkeypatch.setattr(sys,'argv',['projection','--index',str(index),'--candidate',str(tmp_path/'none'),'--guides',output])
    with pytest.raises(SystemExit):module.main()
    assert guide.read_bytes()==before
    assert not (project/'web').exists()

def test_invalid_schema_does_not_erase_declared_vault_boundary(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'PROJECT_ROOT',tmp_path)
    guide=tmp_path/'web/static/knowledge_guides.json';guide.parent.mkdir(parents=True);guide.write_text('{}')
    index=tmp_path/'index.json';index.write_text(json.dumps({'schema_version':'invalid','vault_root':str(tmp_path)}))
    with pytest.raises(ValueError,match='declared Vault'):module.validate_output(guide,index)
    assert guide.read_text(encoding='utf-8')=='{}'

@pytest.mark.parametrize('kind',['file','directory'])
def test_symlink_output_cannot_alias_vault(tmp_path,monkeypatch,kind):
    project=tmp_path/'project';project.mkdir();monkeypatch.setattr(module,'PROJECT_ROOT',project)
    vault=tmp_path/'vault';vault.mkdir();actual=vault/'knowledge_guides.json';actual.write_text('{}')
    (project/'web').mkdir()
    if kind=='directory':(project/'web/static').symlink_to(vault,target_is_directory=True)
    else:
        (project/'web/static').mkdir()
        (project/'web/static/knowledge_guides.json').symlink_to(actual)
    with pytest.raises(ValueError,match='symlinks'):module.validate_output(project/'web/static/knowledge_guides.json',tmp_path/'missing')
    assert actual.read_text(encoding='utf-8')=='{}'

def test_safe_project_output_can_render_missing_index_without_vault_write(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'PROJECT_ROOT',tmp_path)
    guide=tmp_path/'web/static/knowledge_guides.json';guide.parent.mkdir(parents=True);guide.write_text('{"schema":"orbit-knowledge-guides/v1","entries":{}}')
    monkeypatch.setattr(sys,'argv',['projection','--index',str(tmp_path/'missing'),'--candidate',str(tmp_path/'none'),'--guides',str(guide)])
    module.main()
    assert json.loads(guide.read_text(encoding='utf-8'))['iteration']['available'] is False

from test_unified_entry import entry_http

def test_iteration_api_is_read_only_static_metadata(entry_http):
    get,_,_=entry_http
    status,_,body=get('/static/knowledge_guides.json')
    payload=json.loads(body)
    assert status==200 and len(payload['entries'])==13
    assert payload['iteration']['execution_authority'] is False
    assert payload['iteration']['candidate']['reviewed'] is False
