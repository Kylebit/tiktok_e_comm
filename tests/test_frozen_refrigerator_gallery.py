"""Exact frozen product policy plus real completed-v6 source/finalize seam.

The six immutable business documents are explicit file inputs, never a live DB.
The existing closed SDK/OS fixture supplies the independently signed v6 chain.
"""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import json,os
import pytest
from shared_platform import publication_gallery_contract as gallery
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import agent_prepared_source_successor as source
from shared_platform.operations_runtime import runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from modules.products import server
from test_round1_workspace_freeze import live
from test_agent_prepared_source_successor_v4 import real_chain
from test_native_common_colormap_correction import test_real_rejection_v6_public_commit_full_readback_and_stages as _completed_v6
from test_agent_prepared_source_successor import git


def _documents():
    root=Path(os.environ['ORBIT_GALLERY_FROZEN_DOCUMENTS'])
    return {key:json.loads((root/name).read_bytes()) for key,name in bridge.R2_DOCUMENTS.items()}


def test_actual_frozen_refrigerator_gallery_and_fail_closed_negatives():
    docs=_documents();r1=docs['round1_snapshot'];before=deepcopy(docs)
    bridge.validate_r2_identity(docs)
    route=bridge.build_dual_brand_publication_bridge(offer_id=r1['offer_id'],first_review=docs['first_review'],
        generation=docs['generation_result'],translation=docs['translation_result'],approved_by=r1['approved_by'])
    result=gallery.validate_gallery_documents(docs,route)
    assert result['policy']['source']==gallery.REFRIGERATOR_POLICY
    assert result['policy']['minimum_per_brand']==8
    assert result['distinct_qualified_per_brand']=={'livelyhive-sea':8,'homebloom-sea':8}
    assert result['total_distinct_qualified']==16 and docs==before
    for field in ('identity','category','role','brief'):
        wrong=deepcopy(r1);effective=None
        if field=='identity':wrong['snapshot_digest']='sha256:'+'0'*64
        elif field=='category':wrong['fact_snapshot']['product_facts']['category_semantic']='wallpaper'
        else:
            effective=deepcopy(r1['image_plan'])
            effective['brand_plans'][0]['generated_assets'][0][field]='not frozen'
        with pytest.raises(ValueError):gallery.validate_gallery_plan(wrong,effective_plan=effective)
    # A missing generated artifact still fails even though the frozen plan is valid.
    wrong=deepcopy(docs);wrong['generation_result']['assets'].pop()
    with pytest.raises(ValueError):gallery.validate_gallery_documents(wrong,route)
    wall=dict(fact_snapshot=dict(product_facts=dict(category_semantic='wallpaper',title='Wallpaper')))
    policy=gallery.gallery_policy(wall)
    assert policy['source']!=gallery.REFRIGERATOR_POLICY and policy['minimum_per_brand']>=7
    with pytest.raises(ValueError):gallery.validate_gallery_plan(wall,effective_plan=r1['image_plan'])
    assert gallery.gallery_policy({})['source'].startswith('generic-seven/')


def test_completed_v6_true_new_head_and_original_finalize_consumer(real_chain,monkeypatch,tmp_path):
    v=real_chain
    _completed_v6(v,monkeypatch,tmp_path)  # Real source/HMAC/native readers and SDK, not a source/current stub.
    from test_b4b_release_compiler import INCIDENTS
    incident_path=server.R3_STARTUP_CONFIG.root/server.R3_STARTUP_CONFIG.incidents
    incident_path.parent.mkdir(parents=True,exist_ok=True);incident_path.write_text(json.dumps(INCIDENTS))
    service=server._AGENT_PREPARED_COMMON_SERVICE
    before=v.engine.get(v.task_id)
    with v.engine.transaction() as db:
        old={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in (
            'agent_prepared_publication_adoptions','agent_prepared_source_successors','agent_prepared_source_successors_v2',
            'agent_prepared_source_successors_v3','agent_prepared_source_successors_v4','agent_prepared_source_successors_v5','agent_prepared_source_successors_v6')}
    code_root=Path(__file__).resolve().parents[1]
    for name in (source.GALLERY_FILE,'shared_platform/agent_prepared_source_successor.py','scripts/continue_agent_prepared_source.py'):
        path=v.profile.root/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes((code_root/name).read_bytes())
        git(v.profile.root,'add','-f',name)
    git(v.profile.root,'commit','-m','frozen gallery compatibility source')
    v.profile=replace(v.profile,version=git(v.profile.root,'rev-parse','HEAD'),manifest_digest=runtime_manifest(v.profile.root))
    v.engine=WorkbenchEngine(v.profile.data_root/'tasks.db',source._version(v.profile))
    record=source.continue_gallery_source(v.engine,v.profile,v.store,task_id=v.task_id,expected_adoption_digest=v.adoption_digest,actor_reader=object())
    assert record['new_edit_attempts']==0 and record['external_write_count']==0
    assert set(record['source']['changed_files'])<=source.GALLERY_FILES and source.GALLERY_FILE in record['source']['changed_files']
    assert source.continue_gallery_source(v.engine,v.profile,v.store,task_id=v.task_id,expected_adoption_digest=v.adoption_digest,actor_reader=object())==record
    v.operations.engine=v.engine;v.operations.profile=v.profile;service.engine=v.engine;service.profile=v.profile
    current=service._current()[0]
    assert current['checkpoint']==before['checkpoint'] and current['pending_observation']==before['pending_observation']
    with v.engine.transaction() as db:
        assert old=={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in old}
        assert db.execute('SELECT count(*) FROM workbench_events WHERE task_id=? AND event_type=?',(v.task_id,source.GALLERY_EVENT)).fetchone()[0]==1
    # No provider call is permitted by compatibility or the resumed compiler.
    from modules.miaoshou import client
    monkeypatch.setattr(client,'post_open',lambda *a,**k:pytest.fail('Completed COMMON must not dispatch'))
    # Supply the existing real warehouse-receipt producer with a closed official response.
    from shared_platform.ozon_runtime_credentials import load_pinned_ozon_credentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    credentials=tmp_path/'owned-ozon.json';credentials.write_text(json.dumps(dict(client_id='12345',api_key='owned-closed-key')))
    digest=sha256(credentials.read_bytes()).hexdigest()
    monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_PATH',str(credentials));monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_SHA256',digest)
    monkeypatch.setenv('ORBIT_OZON_EXPECTED_ACCOUNT_ID','12345')
    docs=bridge.load_r2_documents(v.payload['product_id'])
    receipt=resolve_warehouse_decision(offer_id=v.payload['product_id'],round1=docs['round1_snapshot'],
        pinned=load_pinned_ozon_credentials(credentials,expected_sha256=digest,expected_account_id='12345'),
        post_bound=lambda *a,**k:dict(warehouses=[dict(warehouse_id=71,status='active',is_kgt=False)]))
    (v.profile.root/'reports/product-preparation'/v.payload['product_id']/'ozon-warehouse-readback.json').write_text(json.dumps(receipt))
    code,result=server._preview_r3_marketplace_stage(dict(offer_id=v.payload['product_id']))
    # This old synthetic wallpaper R1 still lacks its own size/installation proof.
    # Its unchanged gate proves the source-compatible original finalize path is live.
    assert code==409 and result['error']=='GALLERY_UNVERIFIED_SIZE_OR_INSTALLATION',result
    assert service._current()[0]['version']==source._version(v.profile)
