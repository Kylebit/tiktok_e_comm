"""Captured rejection -> true v6 -> original commit/readback/stages, offline."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from modules.products import server,release_adapters as adapters
from modules.miaoshou import client
from shared_platform import native_common_baseline_successor as baseline
from shared_platform import native_common_colormap_correction as color
from shared_platform import native_common_technical_execution as technical
from shared_platform import agent_prepared_source_successor as source
from shared_platform.operations_runtime import runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.release_store import ReleaseAuthorizationError
from test_round1_workspace_freeze import live
from test_agent_prepared_source_successor_v4 import real_chain
from test_native_common_baseline_lock_fix import _bound
from test_agent_prepared_source_successor import git
from test_native_common_service_facts import _REAL_POST_OPEN
from test_service_common_observation import ClosedCommonTransport
from test_common_detail_observation import LocalResponse,CONFIG


def _pin(path,value,kind):
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    return kind(path,sha256(path.read_bytes()).hexdigest())


def test_template_is_inert_and_three_frozen_dto_rows_align(tmp_path):
    template=json.loads((Path(__file__).resolve().parents[1]/'docs/contracts/common-colormap-correction.NOT_AUTHORIZED.json').read_bytes())
    assert color.installed_decision({}) is None
    with pytest.raises(ReleaseAuthorizationError,match='FINITE_TECHNICAL_DECISION_REQUIRED'):
        _pin(tmp_path/'none.json',template,color.PinnedColorMapDecision).read()
    with pytest.raises(ReleaseAuthorizationError,match='TECHNICAL_PIN_REQUIRED'):
        color.installed_decision({'agent_prepared_common_colormap_decision':{'approved':True}})
    rows=template['variant_bindings']
    first=dict(targets=[dict(target='tiktok:LH_PH',copy=dict(specification_name='Pattern',variants=[
        dict(seller_sku=r['model_sku'],display_name=r['english_display_name']) for r in rows]))],
        product_facts=dict(skus=[dict(source_key=r['variant_key'],seller_sku=r['model_sku'],approved_display_name=r['name']) for r in rows]))
    payload=dict(product_id='8412287877065586543',seller_sku='2000431',images=[dict(image_url=r['image_url'],role=r['role'],artifact_digest=r['artifact_digest']) for r in rows],
        product_facts=dict(title='Frozen title',package_cm=[30,30,5],weight_kg=.2,
            selected_sku_keys=[r['variant_key'] for r in rows],selected_skus=[dict(key=r['variant_key'],model_sku=r['model_sku'],label=r['name']) for r in rows],
            sku_label_overrides={r['variant_key']:r['name'] for r in rows}),listing_copy=dict(shopee_description_en='Frozen description'))
    old=adapters._immutable_miaoshou_common_draft(payload)
    projection=color.frozen_projection(payload,first,template,payload['images'])
    payload[color.KEY]=dict(dto=projection)
    current=dict(colorMap=[],colorPropName='',sizeMap=[],skuMap={r['variant_key']:dict(itemNum=r['model_sku'],specLabel=r['name'],price='4') for r in rows})
    updated=adapters._build_immutable_common_edit(payload,current)
    assert set(updated['skuMap'])==set(updated['colorMap'])=={'manual-01','manual-02','manual-03'}
    assert updated['colorPropName']=='Pattern' and updated['sizeMap']==[]
    for r in rows:
        key=r['variant_key'].strip(';').split(';')[0]
        assert updated['colorMap'][key]==dict(name=r['name'],imgUrls=[r['image_url']],imgUrl=r['image_url'])
        assert updated['skuMap'][r['variant_key']]['price']=='4' and 'stock' not in updated['skuMap'][r['variant_key']]
    old_again=deepcopy(payload);old_again.pop(color.KEY)
    assert old==adapters._immutable_miaoshou_common_draft(old_again) and 'colorMap' not in old
    bad=deepcopy(template);bad['variant_bindings'][2]['role']='refrigerator_cover_starry'
    with pytest.raises(ReleaseAuthorizationError,match='VARIANT_ALIGNMENT_CHANGED'):color.frozen_projection(payload,first,bad,payload['images'])


def _transport(monkeypatch,store,detail,*,reject=False):
    """Only the official signed SDK boundary is closed; mutable detail is real-shaped."""
    monkeypatch.setattr(client,'post_open',_REAL_POST_OPEN)
    state=dict(detail=deepcopy(detail),calls=[],bodies=[])
    def closed(request,timeout):
        assert request.full_url.startswith(client.OPEN_BASE_URL) and timeout==30
        path=request.full_url[len(client.OPEN_BASE_URL):]
        assert path in {client.COMMON_DETAIL_OBSERVATION_PATH,adapters.MIAOSHOU_COMMON_EDIT_PATH}
        assert request.get_header('X-app-key')==CONFIG['app_id']
        raw=request.data.decode();stamp=int(request.get_header('X-timestamp'))
        assert request.get_header('X-sign')==client._open_sign(CONFIG['app_secret'],path,stamp,CONFIG['app_id'],raw)
        state['calls'].append(path)
        if path==adapters.MIAOSHOU_COMMON_EDIT_PATH:
            with store._connect_readonly() as db:
                assert db.execute('SELECT count(*) FROM release_runs WHERE technical_execution_state=?',('UNKNOWN',)).fetchone()[0]>=2
                assert db.execute('SELECT count(*) FROM release_approvals').fetchone()[0]==0
            state['bodies'].append(json.loads(raw))
            if reject:value=dict(result='fail',message=color.REJECTION)
            else:
                state['detail']=deepcopy(json.loads(raw)['editCommonCollectBoxDetail'])
                value=dict(result='success',data={})
        else:value=dict(result='success',data=dict(editCommonCollectBoxDetail=deepcopy(state['detail']),ossMd5='closed-md5'))
        return LocalResponse(json.dumps(value,ensure_ascii=False).encode(),url=request.full_url)
    monkeypatch.setattr(client.urllib.request,'urlopen',closed)
    monkeypatch.setattr(client.urllib.request,'build_opener',lambda *a:SimpleNamespace(open=closed))
    monkeypatch.setattr(client,'_wait_for_open_slot',lambda:None)
    monkeypatch.setattr(client,'_load_config',lambda:pytest.fail('No ambient credentials'))
    return state


def _snapshot(db,runid):
    result=dict(run=dict(db.execute('SELECT * FROM release_runs WHERE run_id=?',(runid,)).fetchone()),
        target=dict(db.execute('SELECT * FROM release_target_runs WHERE run_id=?',(runid,)).fetchone()))
    for table in ('release_target_submissions','release_target_failure_events','release_target_readbacks'):
        result[table]=[dict(row) for row in db.execute('SELECT * FROM '+table+' WHERE run_id=? ORDER BY target_label',(runid,))]
    return result


def test_real_rejection_v6_public_commit_full_readback_and_stages(real_chain,monkeypatch,tmp_path):
    v=real_chain;s=_bound(v,monkeypatch)
    parent_payload=baseline.select_payload(v.store,v.payload);parent=v.store.create_plan(parent_payload)
    old_detail=ClosedCommonTransport.detail_for(v.payload)
    rejected=_transport(monkeypatch,v.store,old_detail,reject=True)
    code,result=server._prepare_native_common_ledger(dict(offer_id=v.payload['product_id'],plan_id=parent['plan_id'],confirm_miaoshou_write=True),v.store)
    assert code==502 and len(rejected['bodies'])==1,result
    with v.store._connect_readonly() as db:
        creates={table:[dict(r) for r in db.execute('SELECT * FROM '+table)] for table in (
            'manual_common_creations','manual_common_create_successors','manual_common_create_corrections')}
        runid=db.execute('SELECT run_id FROM release_runs WHERE plan_id=?',(parent['plan_id'],)).fetchone()[0]
        snapshot=_snapshot(db,runid)
        assert _snapshot(db,v.reader.read()['original_run_id'])==v.snapshot
    with v.engine.transaction() as db:
        row=v.engine._row(db,v.task_id);action=json.loads(row['action_json'])
        action['receipt_binding'].update(plan_id=parent['plan_id'],payload_digest=parent['payload_digest'])
        db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(json.dumps(action),v.task_id))
        v.engine._event(db,v.task_id,'domain_observation_pending',action)
        oldrows={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in (
            'agent_prepared_publication_adoptions','agent_prepared_source_successors','agent_prepared_source_successors_v2',
            'agent_prepared_source_successors_v3','agent_prepared_source_successors_v4','agent_prepared_source_successors_v5')}
    before=v.engine.get(v.task_id)
    standing=_pin(tmp_path/'standing.json',dict(schema_version='miaoshou-unpublished-draft-edit-standing-authority/v1',actor='Kyle',
        standing_authority=True,action_class='STANDING_PERMISSION_TO_EDIT_UNPUBLISHED_PREPARATION_DRAFTS',
        immediate_scope={k:v.reader.read()[k] for k in ('task_id','offer_id','common_detail_id')}),color.PinnedColorMapDecision)
    from shared_platform.native_common_budget_facts import _stored_preparation
    with v.store._connect_readonly() as db:
        assert creates=={table:[dict(r) for r in db.execute('SELECT * FROM '+table)] for table in creates}
        first=_stored_preparation(db,v.payload['product_id'],v.original['facts']['prepared_reference'])['packet']
    english=next(r['copy'] for r in first['targets'] if r['target'] in {'tiktok:LH_PH','shopee:PH'})
    english_by_model={r['seller_sku']:r['display_name'] for r in english['variants']}
    bindings=[];assets=color.generation_assets(v.profile.root,parent_payload)
    for offset,sku in enumerate(parent_payload['product_facts']['selected_skus']):
        image=assets[offset]
        canonical=next(k for k,val in v.original['facts']['aliases'].items() if val==sku['model_sku'])
        bindings.append(dict(variant_key=sku['key'],canonical_sku=canonical,model_sku=sku['model_sku'],name=sku['label'],
            english_display_name=english_by_model[sku['model_sku']],role=image['role'],image_url=image['image_url'],artifact_digest=image['artifact_digest']))
    decision=deepcopy(json.loads((Path(__file__).resolve().parents[1]/'docs/contracts/common-colormap-correction.NOT_AUTHORIZED.json').read_bytes()))
    decision.update({k:v.reader.read()[k] for k in ('task_id','offer_id','original_adoption_digest','original_plan_id','original_run_id','original_payload_digest','original_ledger_sha256','common_detail_id','current_detail_sha256')})
    failure=snapshot['release_target_failure_events'][0];summary=json.loads(failure['evidence_json'])
    decision.update(decision_status='STANDING_AUTHORITY_DERIVED',baseline_decision=dict(path=str(v.reader.path),sha256=v.reader.sha256),
        standing_authority_pin=dict(path=str(standing.path),sha256=standing.sha256),variant_bindings=bindings,
        rejected_plan_id=parent['plan_id'],rejected_run_id=runid,rejected_payload_digest=parent['payload_digest'],
        rejected_ledger_sha256=baseline._digest(snapshot),failure_evidence_digest=failure['evidence_digest'],response_canonical_sha256=summary['response_canonical_sha256'])
    reader=_pin(tmp_path/'color.json',decision,color.PinnedColorMapDecision)
    root=v.profile.root;(root/'shared_platform/agent_prepared_common_service.py').write_text('# colorMap source 6\n')
    git(root,'add','-f','shared_platform/agent_prepared_common_service.py');git(root,'commit','-m','colorMap source 6')
    v.profile=replace(v.profile,version=git(root,'rev-parse','HEAD'),manifest_digest=runtime_manifest(root))
    v.engine=WorkbenchEngine(v.profile.data_root/'tasks.db',source._version(v.profile))
    with pytest.raises(ValueError,match='FINITE_TECHNICAL_DECISION_REQUIRED'):
        source.continue_sixth_source(v.engine,v.profile,v.store,task_id=v.task_id,expected_adoption_digest=v.adoption_digest,actor_reader=object(),decision_reader=None)
    record=source.continue_sixth_source(v.engine,v.profile,v.store,task_id=v.task_id,expected_adoption_digest=v.adoption_digest,actor_reader=object(),decision_reader=reader)
    assert record['maximum_new_edit_attempts']==1 and record['combined_edit_attempt_cap']==3
    v.operations.engine=v.engine;v.operations.profile=v.profile;s.engine=v.engine;s.profile=v.profile;s.colormap_decision=reader
    after=s._current()[0]
    assert after['checkpoint']==before['checkpoint'] and after['pending_observation']==before['pending_observation']
    with v.engine.transaction() as db:
        assert oldrows=={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in oldrows}
    payload=baseline.select_payload(v.store,v.payload);preview=v.store.preview_plan(payload)
    accepted=_transport(monkeypatch,v.store,old_detail)
    request=dict(offer_id=v.payload['product_id'],release_stage='R3_COMMON',publication_targets=['miaoshou:COMMON'],
        plan_id=preview['plan_id'],confirmation_token=preview['confirmation_token'],confirm_miaoshou_write=True)
    code,result=s.commit(request)
    assert code==200 and len(accepted['bodies'])==1,result
    with v.store._connect_readonly() as db:
        assert creates=={table:[dict(r) for r in db.execute('SELECT * FROM '+table)] for table in creates}
        assert _snapshot(db,runid)==snapshot and _snapshot(db,v.reader.read()['original_run_id'])==v.snapshot
        assert db.execute('SELECT sum(attempts) FROM release_target_runs').fetchone()[0]==3
        current=db.execute('SELECT * FROM release_runs WHERE plan_id=?',(payload['plan_id'],)).fetchone()
        assert current['technical_execution_state']=='CONFIRMED_WRITE'
        evidence=json.loads(db.execute('SELECT evidence_json FROM release_target_readbacks WHERE run_id=?',(current['run_id'],)).fetchone()[0])
        assert evidence['checks']['color_map'] is True and evidence['checks']['color_prop_name'] is True
        for oldplan,oldrun in ((v.plan['plan_id'],v.reader.read()['original_run_id']),(parent['plan_id'],runid)):
            with pytest.raises(ReleaseAuthorizationError,match='ACCEPTED_EDIT_RECEIPT_MISSING'):
                technical.accepted_edit_for_recovery(v.store,db,oldplan,oldrun)
    calls=list(accepted['calls']);code,again=server._prepare_native_common_ledger(dict(offer_id=v.payload['product_id'],plan_id=payload['plan_id'],confirm_miaoshou_write=True),v.store)
    assert code==200 and accepted['calls']==calls,again
    code,stages=server._publication_stages_for_request(dict(offer_id=v.payload['product_id']))
    assert code==200 and stages['common']['status']=='RETAINED_TECHNICAL_BASELINE',stages
    assert stages['common']['plan']['plan_id']==payload['plan_id'] and stages['common']['run']['run_id']==current['run_id']
    assert baseline.retained_run_ids(decision)=={runid,v.reader.read()['original_run_id']}
