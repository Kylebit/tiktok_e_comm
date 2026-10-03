from __future__ import annotations
import json
from pathlib import Path

import pytest

from modules.product_agent import knowledge as k
from scripts.sync_product_publication_knowledge import main
from tests.test_product_knowledge_contracts import review,args
from tests.test_product_knowledge_regressions import vault


def test_sync_failure_before_publish_preserves_primary_when_cleanup_also_fails(tmp_path,monkeypatch):
    root,_=vault(tmp_path);manifest=review(root);output=tmp_path/'snapshot.json'
    def fail_fsync(fd):raise OSError(28,'synthetic primary fsync failure')
    def fail_cleanup(path):raise PermissionError('synthetic cleanup failure')
    monkeypatch.setattr(k.os,'fsync',fail_fsync);monkeypatch.setattr(k.os,'unlink',fail_cleanup)
    with pytest.raises(OSError) as result:k.sync_snapshot(output,**args(root,manifest),output_root=tmp_path,write=True)
    assert result.value.errno==28 and result.value.output_commit_state=='NOT_PUBLISHED'
    assert any('cleanup' in note for note in result.value.__notes__)
    assert not output.exists() and list(tmp_path.glob('.snapshot.json.*.tmp'))


def test_competing_output_is_never_overwritten_at_atomic_publish(tmp_path,monkeypatch):
    root,_=vault(tmp_path);manifest=review(root);output=tmp_path/'snapshot.json';real=k.os.link
    def race(source,target):
        Path(target).write_bytes(b'other writer unique bytes')
        return real(source,target)
    monkeypatch.setattr(k.os,'link',race)
    with pytest.raises(FileExistsError):k.sync_snapshot(output,**args(root,manifest),output_root=tmp_path,write=True)
    assert output.read_bytes()==b'other writer unique bytes'


def test_cli_reports_published_output_when_only_temporary_cleanup_fails(tmp_path,monkeypatch,capsys):
    root,_=vault(tmp_path);manifest=review(root);(tmp_path/'review.json').write_text(json.dumps(manifest),encoding='utf-8')
    output=tmp_path/'snapshot.json'
    original=k.os.unlink
    def fail_cleanup(path):raise PermissionError('synthetic cleanup failure')
    monkeypatch.setattr(k.os,'unlink',fail_cleanup)
    argv=['--project-root',str(tmp_path),'--vault',str(root),'--source-id','source-a','--review-manifest','review.json',
          '--expected-review-digest',manifest['manifest_digest'],'--output','snapshot.json','--write']
    assert main(argv)==2
    result=json.loads(capsys.readouterr().out)
    assert result['output_commit_state']=='PUBLISHED' and result['local_write_count']==1
    old=output.read_bytes();base=k.KnowledgeBase.from_path(output,expected_review_digest=manifest['manifest_digest'])
    assert base.search('Approved')
    monkeypatch.setattr(k.os,'unlink',original)
    assert main(argv)==0
    assert json.loads(capsys.readouterr().out)['local_write_count']==0 and output.read_bytes()==old
