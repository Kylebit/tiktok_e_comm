"""Real new-task R1/R2/getter -> whitelist summary; closed local HTTP edge."""
from argparse import Namespace
import json

from scripts import orbit_publish as entry
from modules.products import server
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from owned_new_publication_task import complete_new_task


def test_real_new_stage_default_result_and_review_are_compact_readonly(live,monkeypatch,tmp_path):
    runtime,market,task,_=complete_new_task(live,monkeypatch,tmp_path)
    try:
        code,stage=server._publication_stages_for_request({'offer_id':market['product_id']})
        assert code==200 and stage['marketplace']['final_review_available']
        full_bytes=len(json.dumps(stage,ensure_ascii=False,separators=(',',':')).encode())
        assert full_bytes>100_000
        bound={'runtime':{'base_url':'http://127.0.0.1:49999','workflow_mode':'native-sole-final'}}
        monkeypatch.setattr(entry,'check_execution_profile',lambda _:bound)
        monkeypatch.setattr(entry,'verify_runtime',lambda *_:None)
        before=runtime._operations.engine.read_task_snapshot(task['task_id']);calls=[]
        def request(base,path,body=None):
            calls.append((path,body));assert body is None
            if '/home/tasks/' in path:return 200,{'ok':True,'task':runtime._operations.engine.get(task['task_id'])}
            assert path.startswith('/api/product-workspace/publication-stages?')
            return server._publication_stages_for_request({'offer_id':market['product_id']})
        sizes={}
        for command in ('status','result','review'):
            value=entry.run(Namespace(profile='closed',command=command,task_id=task['task_id'],offer_id=market['product_id']),request=request)
            raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode();sizes[command]=len(raw)
            assert len(raw)<20_000 and value['execution_authority'] is False
            summary=value['publication'];candidate=summary['marketplace']['candidate']
            assert candidate['variants_count']==3 and candidate['images_count']==14
            assert summary['source_stage']['read_digest']==entry.digest(stage)
            assert summary['source_stage']['full_bytes']==full_bytes
            assert summary['source_stage']['url'].endswith('offer_id='+market['product_id'])
            assert 'review_manifest' not in raw.decode() and 'native_technical_details' not in raw.decode()
            if command!='review':assert value['task']['task']['task_id']==task['task_id']
        assert runtime._operations.engine.read_task_snapshot(task['task_id'])==before
        assert len(calls)==5 and all(body is None for _,body in calls)
        (tmp_path/'compact-summary-measurement.json').write_text(json.dumps({'source_bytes':full_bytes,
            'summary_bytes':sizes,'image_count':14,'variant_count':3,'post_count':0,'task_unchanged':True}),encoding='utf-8')
    finally:runtime.close()


def test_summary_drops_raw_evidence_but_keeps_original_target_blockers():
    stage={'marketplace':{'status':'RECONCILIATION_REQUIRED','target_results':[{'target_label':'shopee:VN',
        'status':'UNKNOWN','next_action':'RECONCILE_ORIGINAL','blocked_by':['ORIGINAL_TASK_UNKNOWN'],
        'evidence':{'raw_checkpoint':'x'*100_000}}],'preview':{'review_manifest':{'variants':[],
        'image_sets':[]}},'native_final_review':{'review':{'critical_content_digest':'original-digest'}}}}
    value=entry.publication_summary(stage,{'runtime':{'base_url':'http://127.0.0.1:12345'}},'123')
    row=value['marketplace']['target_results'][0]
    assert row=={'target_label':'shopee:VN','status':'UNKNOWN','next_action':'RECONCILE_ORIGINAL',
        'blocked_by':['ORIGINAL_TASK_UNKNOWN'],'blocker_count':0,'omitted_blocker_count':0}
    assert value['marketplace']['review_binding']['critical_content_digest']=='original-digest'
    assert 'raw_checkpoint' not in json.dumps(value)


def test_stage_failure_keeps_original_error_and_http_code(monkeypatch):
    bound={'runtime':{'base_url':'http://127.0.0.1:49999','workflow_mode':'native-sole-final'}}
    monkeypatch.setattr(entry,'check_execution_profile',lambda _:bound)
    monkeypatch.setattr(entry,'verify_runtime',lambda *_:None)
    def request(base,path,body=None):
        assert body is None
        if '/home/tasks/' in path:return 200,{'task':{'task_id':'original','template':'publication','scope':{'offer_id':'123'}}}
        return 500,{'ok':False,'error':'ORIGINAL_STAGE_ERROR','message':'Original stage unavailable','raw_checkpoint':'omitted'}
    result=entry.run(Namespace(profile='closed',command='status',task_id='original'),request=request)['publication']
    assert result['stage_http_status']==500 and result['ok'] is False
    assert result['error']=='ORIGINAL_STAGE_ERROR' and result['message']=='Original stage unavailable'
    assert 'raw_checkpoint' not in result


def test_task_summary_preserves_scope_and_original_action_objects():
    action={'kind':'FINAL_REVIEW','label':'Original review','url':'http://127.0.0.1:12345/review',
        'reason':'ORIGINAL_PENDING','action_id':'original-action','evidence':'not returned'}
    result=entry.task_summary({'task':{'task_id':'original','source_key':'original-source',
        'scope':{'offer_id':'123','skus':['0431','0432'],'source_key':'scope-source'},
        'required_action':action,'pending_observation':action}})['task']
    assert result['source_key']=='original-source'
    assert result['scope']['skus']==['0431','0432'] and result['scope']['source_key']=='scope-source'
    assert result['required_action']==result['pending_observation']=={k:v for k,v in action.items() if k!='evidence'}


def test_common_dictionary_user_status_and_blocker_truncation_are_explicit():
    common={'user_status':{'summary':'Waiting for original READ','next_action':{'kind':'READBACK','label':'Read original'},
        'raw_checkpoint':'omitted'},'blockers':['BLOCK_'+str(i) for i in range(67)]}
    result=entry.publication_summary({'common':common},{'runtime':{'base_url':'http://127.0.0.1:12345'}},'123')['common']
    assert result['user_status']=={'summary':'Waiting for original READ','next_action':{'kind':'READBACK','label':'Read original'}}
    assert len(result['blockers'])==64 and result['blocker_count']==67 and result['omitted_blocker_count']==3
