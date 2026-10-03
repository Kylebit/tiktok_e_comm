from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys

import pytest

from modules.product_agent import knowledge


def vault(tmp_path):
    root=tmp_path/'synthetic-vault';section=root/knowledge.DEFAULT_SECTION;section.mkdir(parents=True)
    (section/'reviewed.md').write_text('---\ntitle: Reviewed example\nstatus: reviewed\n---\nApproved synthetic rule.\n',encoding='utf-8')
    (section/'draft.md').write_text('---\ntitle: Draft example\nstatus: draft\n---\nUnreviewed synthetic proposal.\n',encoding='utf-8')
    return root,section


def test_K01_directory_and_draft_status_do_not_supply_runtime_approval(tmp_path):
    root,_=vault(tmp_path)
    with pytest.raises(ValueError,match='review|manifest'):
        knowledge.build_snapshot(root)


def test_K02_original_claimed_version_cannot_authorize_altered_content():
    snapshot=json.loads((Path(__file__).parent/'fixtures/knowledge_legacy_v1.json').read_text(encoding='utf-8'))
    tampered=copy.deepcopy(snapshot)
    tampered['documents'][0]['content']='ALTERED synthetic content with original claimed version'
    with pytest.raises(ValueError,match='version|digest'):
        knowledge.KnowledgeBase(tampered)


def test_K03_real_link_to_external_markdown_is_rejected_before_open(tmp_path):
    root,section=vault(tmp_path);outside=tmp_path/'outside-section-sentinel.md'
    outside.write_text('Synthetic outside section sentinel. No real user data.\n',encoding='utf-8')
    os.symlink(outside,section/'linked.md')
    active=[True];reads=[]
    def audit(event,args):
        if active[0] and event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve()==outside.resolve():
                reads.append(str(args[0]));raise AssertionError('EXTERNAL_SOURCE_READ')
    sys.addaudithook(audit)
    try:
        with pytest.raises(ValueError,match='symlink|reparse'):
            knowledge.build_snapshot(root)
    finally:active[0]=False
    assert reads==[]
    assert outside.read_text(encoding='utf-8')=='Synthetic outside section sentinel. No real user data.\n'
