"""Agent entry drives original task/decision routes; only HTTP edge is closed."""
from argparse import Namespace
from pathlib import Path
import pytest
from scripts import orbit_publish as entry

@pytest.fixture
def bound(tmp_path,monkeypatch):
    value={'source_root':str(tmp_path),'expected_source_head':'1'*40,'settings_path':str(tmp_path/'settings.json'),
        'data_root':str(tmp_path),'checkpoint_root':str(tmp_path),'source_dirty':'',
        'source_identity':{'root':str(tmp_path),'head':'1'*40,'manifest_digest':'2'*64},
        'profile_ref':{'path':str(tmp_path/'profile.json'),'sha256':'3'*64},
        'runtime':{'base_url':'http://127.0.0.1:49999','workflow_mode':'native-sole-final'}}
    monkeypatch.setattr(entry,'check_execution_profile',lambda _:value)
    return value

def closed(bound,extra):
    calls=[]
    def request(base,path,body=None):
        calls.append((path,body))
        if path=='/api/health':return 200,{'root':bound['source_root'],'commit':'1'*40,'profile':{'settings_path':bound['settings_path']}}
        if path=='/api/orbit/home/runtime':return 200,{'version':'1'*40,'manifest_digest':'2'*64,'data_root':bound['data_root']}
        return extra(path,body)
    return request,calls

def test_prepare_uses_original_explicit_worker_task_and_same_intent(bound):
    request,calls=closed(bound,lambda p,b:(201,{'task':{'task_id':'new-three-model'}}))
    args=Namespace(profile='explicit',command='prepare',offer_id='9012345678',targets='miaoshou:COMMON,tiktok:LH_PH,shopee:PH,ozon:RU',source_key='exact-new')
    assert entry.run(args,request=request)['task']['task_id']=='new-three-model'
    entry.run(args,request=request)
    posts=[(p,b) for p,b in calls if b is not None]
    assert len(posts)==1 and posts[0][0]=='/api/orbit/tasks'
    assert posts[0][1]=={'template':'publication','source_key':'exact-new','scope':{'offer_id':'9012345678','shops':args.targets.split(',')}}

def test_unknown_local_request_does_not_post_again(bound):
    def lost(path,body):raise TimeoutError('closed lost local response')
    request,calls=closed(bound,lost)
    args=Namespace(profile='explicit',command='prepare',offer_id='9012345678',targets='miaoshou:COMMON,tiktok:LH_PH',source_key='exact-new')
    with pytest.raises(TimeoutError):entry.run(args,request=request)
    with pytest.raises(entry.ExecutionPackageError,match='ORIGINAL_ENTRY_REQUEST_UNKNOWN'):
        entry.run(args,request=request)
    assert sum(b is not None for _,b in calls)==1

def test_execute_consumes_original_saved_decision_without_second_approval(bound):
    def edge(path,body):
        if 'publication-stages?' in path:return 200,{'marketplace':{'native_final_review':{'offer_id':'9012345678','approval_saved':True,'decision_id':'stored-decision'}}}
        assert path=='/api/product-workspace/native-final/resume' and body=={'decision_id':'stored-decision'}
        return 202,{'queued':True,'approval_saved':True,'new_human_review_required':False}
    request,calls=closed(bound,edge)
    args=Namespace(profile='explicit',command='execute',offer_id='9012345678',decision_id='stored-decision')
    assert entry.run(args,request=request)['new_human_review_required'] is False
    assert [p for p,b in calls if b is not None]==['/api/product-workspace/native-final/resume']

def test_changed_decision_or_runtime_blocks_before_post(bound):
    def edge(path,body):return 200,{'marketplace':{'native_final_review':{'offer_id':'9012345678','approval_saved':True,'decision_id':'different'}}}
    request,calls=closed(bound,edge)
    with pytest.raises(entry.ExecutionPackageError,match='ORIGINAL_STORED_SOLE_DECISION'):
        entry.run(Namespace(profile='explicit',command='execute',offer_id='9012345678',decision_id='old'),request=request)
    assert all(body is None for _,body in calls)

def test_result_is_readonly_and_includes_original_task_and_target_truth(bound):
    def edge(path,body):
        assert body is None
        if '/home/tasks/' in path:return 200,{'task':{'task_id':'task-original','template':'publication','scope':{'offer_id':'9012345678'}}}
        return 200,{'marketplace':{'status':'RECONCILIATION_REQUIRED','result_rows':[{'target_label':'shopee:VN','status':'UNKNOWN'}]}}
    request,calls=closed(bound,edge)
    before=list(Path(bound['data_root']).iterdir())
    result=entry.run(Namespace(profile='explicit',command='result',task_id='task-original'),request=request)
    assert result['publication']['marketplace']['status']=='RECONCILIATION_REQUIRED'
    assert all(body is None for _,body in calls) and list(Path(bound['data_root']).iterdir())==before
