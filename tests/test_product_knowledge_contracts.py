from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from modules.product_agent import knowledge as k
from scripts.sync_product_publication_knowledge import main as knowledge_cli
from tests.test_product_knowledge_regressions import vault


def review(root, *, source_id='source-a', name='reviewed.md'):
    rel=(k.DEFAULT_SECTION/name).as_posix()
    value={'schema_version':k.REVIEW_SCHEMA,'source':k.source_identity(root,k.DEFAULT_SECTION,source_id),
        'review_id':'review-a','review_scope':'fixture-existing-publication-rules','reviewed_at':'2026-09-05T00:00:00+00:00',
        'instruction_ref':'fixture://existing-review','documents':[{'document_id':'rule-a','path':rel,
            'source_digest':'sha256:'+hashlib.sha256((root/rel).read_bytes()).hexdigest(),'usage':'runtime_rule',
            'source_date':'2026-09-01','supersedes':[],'report_refs':['fixture://execution-report']} ]}
    return seal(value)


def seal(value):
    value['manifest_digest']=k.version_digest({key:row for key,row in value.items() if key!='manifest_digest'})
    return value


def args(root,manifest):
    return dict(vault_root=root,source_id=manifest['source']['source_id'],review_manifest=manifest,
                expected_review_digest=manifest['manifest_digest'])


def test_real_export_load_search_and_unreviewed_reference_separation(tmp_path):
    root,section=vault(tmp_path);manifest=review(root);kw=args(root,manifest)
    inspection=k.inspect_sources(**kw)
    assert inspection['ready'] and inspection['current_execution_authority'] is False
    decisions={Path(r['path']).name:r['decision'] for r in inspection['documents']}
    assert decisions=={'draft.md':'NOT_IN_REVIEWED_MANIFEST','reviewed.md':'REVIEWED_RUNTIME_RULE'}
    output=tmp_path/'snapshots'/'first.json'
    snapshot=k.sync_snapshot(output,**kw,output_root=tmp_path)
    assert not output.exists() and len(snapshot['documents'])==1
    k.sync_snapshot(output,**kw,output_root=tmp_path,write=True)
    original=output.read_bytes()
    base=k.KnowledgeBase.from_path(output,expected_version=snapshot['knowledge_version'])
    assert base.search('Approved')[0].content=='Approved synthetic rule.\n'
    assert base.search('Unreviewed')==[] and base.search('Approved')[0].as_dict()['knowledge_version']==base.version
    k.sync_snapshot(output,**kw,output_root=tmp_path,write=True)
    assert output.read_bytes()==original
    assert base.provenance()['source_text_preserved'] and not base.provenance()['fresh_business_facts_verified']
    assert 'modules.product_agent.runtime' not in sys.modules and 'modules.product_agent.store' not in sys.modules


def test_reviewed_reference_can_be_exported_and_searched_only_with_pinned_reference_mode(tmp_path):
    root, section = vault(tmp_path)
    original = (section/'draft.md').read_bytes()
    manifest = review(root, name='draft.md')
    manifest['documents'][0]['usage'] = 'reference'
    seal(manifest)
    kw = args(root, manifest)

    inspection = k.inspect_sources(**kw)
    assert inspection['ready']
    assert {row['path']: row['decision'] for row in inspection['documents']}[
        (k.DEFAULT_SECTION/'draft.md').as_posix()] == 'REFERENCE_ONLY'
    with pytest.raises(ValueError, match='no approved runtime documents'):
        k.build_snapshot(**kw)

    snapshot = k.build_snapshot(**kw, mode='reference')
    assert snapshot['knowledge_mode'] == 'reference'
    output = tmp_path/'snapshots'/'reference.json'
    k.sync_snapshot(output, **kw, mode='reference', output_root=tmp_path, write=True)
    assert (section/'draft.md').read_bytes() == original
    with pytest.raises(ValueError, match='reference'):
        k.KnowledgeBase.from_path(output, expected_version=snapshot['knowledge_version'])
    with pytest.raises(ValueError, match='expected version'):
        k.KnowledgeBase.from_path(output, mode='reference')
    with pytest.raises(ValueError, match='expected version'):
        k.KnowledgeBase.from_path(output, mode='reference', expected_version='sha256:'+'f'*64)
    base = k.KnowledgeBase.from_path(output, mode='reference', expected_version=snapshot['knowledge_version'])
    matches = base.search('Unreviewed')
    assert len(matches) == 1 and matches[0].usage == 'reference'
    assert matches[0].knowledge_version == snapshot['knowledge_version']
    assert base.provenance()['current_execution_authority'] is False
    assert base.provenance()['fresh_business_facts_verified'] is False

    (section/'draft.md').write_bytes(original+b'\nchanged')
    with pytest.raises(ValueError, match='SOURCE_DIGEST_CHANGED'):
        k.build_snapshot(**kw, mode='reference')
    assert len(base.search('Unreviewed')) == 1  # pinned prior bytes remain readable


def test_reference_cli_requires_explicit_mode_and_task_version(tmp_path, capsys):
    root, _ = vault(tmp_path)
    manifest = review(root, name='draft.md')
    manifest['documents'][0]['usage'] = 'reference'
    seal(manifest)
    (tmp_path/'review.json').write_text(json.dumps(manifest), encoding='utf-8')
    common = ['--runtime-root', str(Path(__file__).resolve().parents[1]),
              '--project-root', str(tmp_path), '--vault', str(root),
              '--section', str(k.DEFAULT_SECTION), '--source-id', manifest['source']['source_id'],
              '--review-manifest', 'review.json', '--expected-review-digest', manifest['manifest_digest']]
    assert knowledge_cli(['export-preview', *common, '--output', 'reference.json']) == 2
    assert 'no approved runtime documents' in json.loads(capsys.readouterr().out)['error']
    assert knowledge_cli(['export-preview', *common, '--reference', '--output', 'reference.json', '--write']) == 0
    version = json.loads(capsys.readouterr().out)['knowledge_version']
    assert knowledge_cli(['inspect', *common, '--reference', '--snapshot', 'reference.json']) == 2
    assert 'expected version' in json.loads(capsys.readouterr().out)['error']
    assert knowledge_cli(['inspect', *common, '--snapshot', 'reference.json', '--expected-version', version]) == 2
    assert 'reference snapshot' in json.loads(capsys.readouterr().out)['error']
    assert knowledge_cli(['inspect', *common, '--reference', '--snapshot', 'reference.json',
                          '--expected-version', version, '--query', 'Unreviewed']) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result['matches']) == 1 and result['matches'][0]['usage'] == 'reference'


def test_valid_legacy_is_pinned_read_only_and_missing_raw_bytes_are_explicit():
    path=Path(__file__).parent/'fixtures/knowledge_legacy_v1.json';raw=path.read_bytes();snapshot=json.loads(raw)
    with pytest.raises(ValueError,match='read-only replay'):k.KnowledgeBase(snapshot,expected_version=snapshot['knowledge_version'])
    base=k.KnowledgeBase.from_path(path,mode='replay',expected_version=snapshot['knowledge_version'])
    assert base.search('Approved')[0].usage=='historical_reference'
    assert not base.provenance()['source_text_preserved'] and base.provenance()['limitations']
    assert path.read_bytes()==raw


@pytest.mark.parametrize('mutation',['body','source-text','reapproved-other-content','wrong-task-version','missing-pin'])
def test_rehashed_snapshot_is_not_automatically_an_approved_version(tmp_path,mutation):
    root,_=vault(tmp_path);manifest=review(root);snapshot=k.build_snapshot(**args(root,manifest));changed=copy.deepcopy(snapshot)
    kwargs={'expected_review_digest':manifest['manifest_digest']}
    if mutation=='body':changed['documents'][0]['content']='altered'
    elif mutation in {'source-text','reapproved-other-content'}:
        row=changed['documents'][0];row['source_text']=row['source_text'].replace('Approved','Altered');row['content']=row['content'].replace('Approved','Altered')
        row['content_digest']='sha256:'+hashlib.sha256(row['source_text'].encode()).hexdigest()
        if mutation=='reapproved-other-content':
            changed['review_manifest']['documents'][0]['source_digest']=row['content_digest'];seal(changed['review_manifest'])
    elif mutation=='wrong-task-version':kwargs['expected_version']='sha256:'+'f'*64
    else:kwargs={}
    changed['knowledge_version']=k.version_digest({key:value for key,value in changed.items() if key!='knowledge_version'})
    with pytest.raises(ValueError):k.KnowledgeBase(changed,**kwargs)


def test_new_review_cannot_replace_old_task_or_overwrite_its_snapshot(tmp_path):
    root,section=vault(tmp_path);old_review=review(root);kw=args(root,old_review);old=k.build_snapshot(**kw)
    output=tmp_path/'first.json';k.sync_snapshot(output,**kw,output_root=tmp_path,write=True);oldbytes=output.read_bytes()
    loaded=k.KnowledgeBase(old,expected_version=old['knowledge_version'])
    (section/'reviewed.md').write_text('New synthetic rule after a separate review.\n',encoding='utf-8')
    with pytest.raises(ValueError,match='SOURCE_DIGEST_CHANGED'):k.build_snapshot(**kw)
    fresh=review(root);fresh['review_id']='review-b';fresh['documents'][0]['supersedes']=['rule-a@'+old['knowledge_version']];seal(fresh)
    new=k.build_snapshot(**args(root,fresh))
    assert new['knowledge_version']!=old['knowledge_version']
    with pytest.raises(ValueError,match='expected version'):k.KnowledgeBase(new,expected_version=old['knowledge_version'])
    with pytest.raises(ValueError,match='preserve old bytes'):k.sync_snapshot(output,**args(root,fresh),output_root=tmp_path,write=True)
    assert output.read_bytes()==oldbytes and loaded.search('Approved')
    original_content=loaded.search('Approved')[0].content
    old['documents'][0]['content']='caller mutation';copy_out=loaded.snapshot;copy_out['documents'][0]['content']='public snapshot mutation'
    assert loaded.search('Approved')[0].content==original_content


@pytest.mark.parametrize('change',['source-id','vault','missing-file','case-path','duplicate-id','duplicate-path','unbound-review'])
def test_exact_review_scope_and_source_are_required(tmp_path,change):
    root,section=vault(tmp_path);manifest=review(root);kw=args(root,manifest)
    if change=='source-id':kw['source_id']='other-source'
    elif change=='vault':
        other=tmp_path/'other';other.mkdir();other_root,_=vault(other);kw['vault_root']=other_root
    elif change=='missing-file':(section/'reviewed.md').unlink()
    elif change=='case-path':manifest['documents'][0]['path']=manifest['documents'][0]['path'].replace('reviewed.md','REVIEWED.md');seal(manifest);kw=args(root,manifest)
    elif change.startswith('duplicate'):
        row=copy.deepcopy(manifest['documents'][0]);row['document_id']='other-id' if change=='duplicate-path' else 'rule-a'
        if change=='duplicate-id':row['path']=(k.DEFAULT_SECTION/'draft.md').as_posix()
        manifest['documents'].append(row);seal(manifest);kw=args(root,manifest)
    else:kw['expected_review_digest']=None
    with pytest.raises(ValueError):k.build_snapshot(**kw)


def test_frontmatter_does_not_require_mutating_original_vault_to_apply_explicit_review(tmp_path):
    root,section=vault(tmp_path);before=(section/'draft.md').read_bytes()
    manifest=review(root,name='draft.md');snapshot=k.build_snapshot(**args(root,manifest))
    assert snapshot['documents'][0]['metadata']['status']=='draft'
    assert k.KnowledgeBase(snapshot,expected_review_digest=manifest['manifest_digest']).search('Unreviewed')
    assert (section/'draft.md').read_bytes()==before


def test_multiple_vaults_never_choose_recent_open_and_explicit_environment_wins(tmp_path,monkeypatch):
    paths=[]
    for name in ['a','b']:
        p=tmp_path/name;(p/'.obsidian').mkdir(parents=True);paths.append(p)
    registry=tmp_path/'obsidian.json';registry.write_text(json.dumps({'vaults':{'a':{'path':str(paths[0]),'open':False,'ts':1},'b':{'path':str(paths[1]),'open':True,'ts':999}}}),encoding='utf-8')
    monkeypatch.delenv(k.OBSIDIAN_ENV,raising=False)
    with pytest.raises(ValueError,match='multiple vaults'):k.discover_obsidian_vault(obsidian_config=registry)
    with pytest.raises(ValueError,match='explicit'):k.discover_obsidian_vault()
    monkeypatch.setenv(k.OBSIDIAN_ENV,str(paths[0]))
    assert k.discover_obsidian_vault(obsidian_config=registry)==paths[0]


def test_raw_utf8_bom_and_crlf_identity_is_preserved(tmp_path):
    root,section=vault(tmp_path);path=section/'reviewed.md'
    raw=b'\xef\xbb\xbf---\r\ntitle: Example\r\nstatus: reviewed\r\n---\r\nApproved body.\r\n';path.write_bytes(raw)
    manifest=review(root);snapshot=k.build_snapshot(**args(root,manifest));row=snapshot['documents'][0]
    assert row['source_text'].encode('utf-8')==raw and row['content']=='Approved body.\n'
    assert k.KnowledgeBase(snapshot,expected_review_digest=manifest['manifest_digest']).search('Approved')


def test_export_cannot_write_back_into_vault_or_follow_output_redirect(tmp_path):
    root,_=vault(tmp_path);manifest=review(root);kw=args(root,manifest)
    with pytest.raises(ValueError,match='source vault'):k.sync_snapshot(root/'snapshot.json',**kw,output_root=tmp_path,write=True)
    canary=tmp_path/'outside.json';canary.write_text('preserve',encoding='utf-8');alias=tmp_path/'output.json';os.symlink(canary,alias)
    with pytest.raises(ValueError,match='symlink|reparse'):k.sync_snapshot(alias,**kw,output_root=tmp_path,write=True)
    assert canary.read_text(encoding='utf-8')=='preserve'
