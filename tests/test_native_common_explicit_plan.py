"""Real current task/frozen source guards before inert COMMON persistence."""
from copy import deepcopy
import pytest
from modules.products import server
from shared_platform import operations_publication_common as common
from shared_platform.final_review_server_admission import ApprovalBlocked
from test_native_r2_service_consumers import live, images_task, _complete_images, _runtime
from test_round1_workspace_freeze import live as original_live
from test_round1_auto_freeze import public_settings


@pytest.mark.parametrize('drift',['lease','scope','payload'])
def test_explicit_common_rejects_changed_lease_scope_or_candidate_before_persist(images_task, monkeypatch, tmp_path, drift):
    v=images_task
    task=_complete_images(v,monkeypatch)
    runtime=_runtime(v,monkeypatch,tmp_path,enabled=True)
    code,view=server._preview_r3_common_stage({'offer_id':task['scope']['offer_id'],
        'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']})
    assert code==200,view
    plan=view['common']['plan']
    binding=common._verified_binding(task,view['common'],v['profile'])
    assert binding['preparation_source']['status']=='VERIFIED_NATIVE_R1'
    assert runtime.store.get_plan(plan['plan_id']) is None
    before=runtime.store.path.read_bytes()
    bad_task=deepcopy(task);bad_plan=deepcopy(plan);token=v['token']
    if drift=='lease':token='not-the-original-lease'
    elif drift=='scope':bad_task['scope']['shops']=bad_task['scope']['shops'][:-1]
    else:bad_plan['payload']['product_facts']['title']='caller replacement title'
    try:
        expected={'lease':'^stale or invalid lease$',
            'scope':'^NATIVE_COMMON_TASK_CURRENT_IDENTITY_CHANGED$',
            'payload':'^NATIVE_COMMON_TASK_CURRENT_CANDIDATE_CHANGED$'}[drift]
        with pytest.raises((ValueError,ApprovalBlocked),match=expected):
            runtime.prepare_common_for_task(v['engine'],bad_task,token,v['profile'],binding,bad_plan)
        assert runtime.store.get_plan(plan['plan_id']) is None
        assert runtime.store.path.read_bytes()==before
    finally:runtime.close()


@pytest.mark.parametrize('drift',['missing-ref','source-ref','different-common'])
def test_full_scope_common_membership_requires_exact_original_native_source(images_task, monkeypatch, tmp_path, drift):
    v=images_task;task=_complete_images(v,monkeypatch)
    _runtime(v,monkeypatch,tmp_path,enabled=True)
    code,view=server._preview_r3_common_stage({'offer_id':task['scope']['offer_id'],
        'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']})
    assert code==200,view
    assert common._verified_binding(task,view['common'],v['profile'])['preparation_source']['targets']==task['scope']['shops']
    changed=deepcopy(view['common']);stage=changed['plan']['payload']['r3_stage_binding']
    if drift=='missing-ref':del stage['native_preparation_source']['prepared_reference']
    elif drift=='source-ref':stage['native_preparation_source']['prepared_reference']='foreign-prepared'
    else:stage['marketplace_targets']=[label.replace('miaoshou:COMMON','miaoshou:OTHER') for label in stage['marketplace_targets']]
    before=v['live']['store'].path.read_bytes()
    expected='COMMON_PREPARATION_SOURCE_CONFLICT' if drift=='source-ref' else 'COMMON 计划未绑定'
    with pytest.raises(ValueError,match=expected):common._verified_binding(task,changed,v['profile'])
    assert v['live']['store'].path.read_bytes()==before
    assert v['live']['store'].get_plan(view['common']['plan']['plan_id']) is None
