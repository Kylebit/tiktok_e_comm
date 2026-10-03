"""Real draft factory/preparer/publisher with lowest Miaoshou HTTP replaced."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from domains.channel_operations.tiktok_publisher import TikTokPublisher
from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport
from shared_platform.product_publication_live_dependencies import (
    DurableTikTokV4DraftPreparer,MiaoshouTikTokV4DraftTransportFactory,TikTokV4DraftCheckpointStore,
    LivePublicationDependencyError,
)
from shared_platform.product_publication_executors import build_tiktok_v4_executor
from shared_platform.publication_rounds import canonical_digest
from shared_platform.publication_write_budget import PublicationWriteBudgetLedger
from test_b4b_release_compiler import neutral_product_plan,policy,INCIDENTS
from test_approved_publication_snapshot import _rebind
from domains.product_operations import build_approved_publication_snapshot
from shared_platform.publication_autopilot import compile_release_candidate
from test_miaoshou_tiktok_v4_drafts import CategoryResolver,_editable_site_payload,_warehouse_payload
from test_tiktok_v4_execution import Readback


class OfflineTikTokHTTP:
    def __init__(self):self.calls=[];self.saved={};self.timeout_operation=None;self.timeout_shop=None
    def __call__(self,path,body):
        self.calls.append((path,deepcopy(body)))
        if path.rsplit('/',1)[-1]==self.timeout_operation and (
            self.timeout_shop is None or body.get('shopIds')==[self.timeout_shop]
        ):
            raise TimeoutError('synthetic mutation response timeout')
        if path.endswith('/claimed'):
            serial=body['detailSerialNumberPlatformList'][0]['serialNumber']
            return {'result':'success','data':{'platformCollectBoxDetailIdMap':{'tiktok':{'5001':7100+serial}}}}
        if path.endswith('get_site_collect_item_info'):
            site=body['site']
            if site in self.saved:
                return {'result':'success','data':{'siteCollectItemInfo':deepcopy(self.saved[site]),'ossMd5':'saved-revision'}}
            response=_editable_site_payload(site=site,revision='original-revision')
            response['data']['siteCollectItemInfo']['skuPropertyList']=[{'attrName':'Specification',
                'attrValueList':[{'attrValueId':'blue-38x45','attrValue':'Blue'},
                                 {'attrValueId':'pink-38x45','attrValue':'Pink'}]}]
            return response
        if path.endswith('get_shop_warehouse_list'):
            return _warehouse_payload(str(body['shopIds'][0]))
        if path.endswith('save_site_collect_item_info'):
            self.saved[body['site']]=deepcopy(body['siteCollectItemInfo'])
        elif not path.endswith(('claim_to_shop','save_move_collect_task')):
            raise AssertionError(path)
        return {'result':'success','data':{}}
    def mutations(self):
        return [path.rsplit('/',1)[1] for path,_ in self.calls if path.endswith(('/claimed','claim_to_shop','save_site_collect_item_info','save_move_collect_task'))]


def live_fixture(tmp_path,cap,*,existing=False,labels=('tiktok:LH_PH',)):
    plan=neutral_product_plan()
    for key,color in [('blue-38x45','Blue'),('pink-38x45','Pink')]:
        plan['payload']['product_facts']['sku_commercial_facts'][key]['specification']={'option':color}
    value=build_approved_publication_snapshot(_rebind(plan)).payload()
    candidate=compile_release_candidate(value,policy=policy(),incident_registry=INCIDENTS,
        platform_scope=('TIKTOK',),target_scope=labels)
    assert candidate['status']=='READY_FOR_FINAL_REVIEW',candidate['blockers']
    ledger=PublicationWriteBudgetLedger(platform='TIKTOK',target_labels=labels,
        budget={'shared_maximum':len(labels),'per_target_maximum':cap})
    request=SimpleNamespace(run_id='offline-live-tiktok',report_id='publication-report:offline-live-tiktok',
        platform='TIKTOK',target_labels=labels,snapshot=value,
        release_candidate=candidate,write_budget_ledger=ledger)
    body={'schema_version':'miaoshou-tiktok-v4-seed-identity/v2','snapshot_digest':value['snapshot_digest'],
          'common_detail_id':'5001','initial_platform_detail_id':None,
          'platform_detail_ids_by_target':{'tiktok:LH_PH':'7101','tiktok:LH_MY':'7102'} if existing else {}}
    identity={**body,'identity_digest':canonical_digest(body)}
    http=OfflineTikTokHTTP()
    factory=MiaoshouTikTokV4DraftTransportFactory(seed_identity_resolver=lambda _:identity,post=http)
    store=TikTokV4DraftCheckpointStore(tmp_path)
    preparer=DurableTikTokV4DraftPreparer(checkpoint_store=store,category_resolver=CategoryResolver(),transport_factory=factory)
    executor=build_tiktok_v4_executor(collectbox_context_resolver=None,draft_preparer=preparer,
        category_resolver=CategoryResolver(),publisher=TikTokPublisher(MiaoshouTikTokTransport(post=http)),storefront_readback=Readback())
    return request,ledger,http,store,preparer,executor


@pytest.mark.parametrize('cap',[0,1,2,3])
def test_real_live_tiktok_budget_counts_each_http_and_preserves_prepared_save(tmp_path,cap):
    request,ledger,http,store,preparer,executor=live_fixture(tmp_path,cap)
    result=executor(request)
    expected=['claimed','claim_to_shop','save_site_collect_item_info','save_move_collect_task'][:cap+1]
    assert http.mutations()==expected, result
    assert ledger.total_attempt_count==len(expected)
    assert result['external_write_count']==len(expected),result
    assert all(body.get('site','PH')=='PH' for _,body in http.calls)
    assert sum(path.endswith('save_site_collect_item_info') for path,_ in http.calls)<=1


def test_existing_seed_map_is_projected_to_requested_target_without_other_shop_write(tmp_path):
    request,ledger,http,store,preparer,executor=live_fixture(tmp_path,3,existing=True)
    result=executor(request)
    assert http.mutations()==['save_site_collect_item_info','save_move_collect_task'],result
    assert ledger.total_attempt_count==2
    assert set(store.load(request)['receipt']['collectbox_contexts'])=={'tiktok:LH_PH'}


def test_approved_request_without_ledger_fails_before_mutation(tmp_path):
    request,ledger,http,store,preparer,executor=live_fixture(tmp_path,3)
    request.write_budget_ledger=None
    with pytest.raises(LivePublicationDependencyError,match='budget'):
        preparer(request)
    assert http.mutations()==[]


@pytest.mark.parametrize('operation,count',[('claimed',1),('claim_to_shop',2),('save_site_collect_item_info',3),('save_move_collect_task',4)])
def test_attempted_http_timeout_retains_budget_and_unknown_fact(tmp_path,operation,count):
    request,ledger,http,store,preparer,executor=live_fixture(tmp_path,3)
    http.timeout_operation=operation
    result=executor(request)
    assert len(http.mutations())==count
    assert ledger.total_attempt_count==count
    assert result['external_write_count'] is None
    assert result['targets'][0]['evidence']['outcome_unknown'] is True
    if operation!='save_move_collect_task':
        # Preparation has its own durable events; the runner owns publish retry.
        executor(request)
        assert len(http.mutations())==count


def test_no_shared_budget_refuses_create_without_any_http_mutation(tmp_path):
    request,ledger,http,store,preparer,executor=live_fixture(tmp_path,3)
    request.write_budget_ledger=PublicationWriteBudgetLedger(platform='TIKTOK',target_labels=request.target_labels,
        budget={'shared_maximum':0,'per_target_maximum':3})
    result=executor(request)
    assert http.mutations()==[]
    assert result['external_write_count']==0
    assert result['targets'][0]['evidence']['outcome_unknown'] is False


def test_unknown_first_shop_does_not_publish_it_or_block_other_prepared_shop(tmp_path):
    request,ledger,http,store,preparer,executor=live_fixture(tmp_path,3,labels=('tiktok:LH_PH','tiktok:LH_MY'))
    http.timeout_operation='claim_to_shop';http.timeout_shop=7676267
    result=executor(request)
    saves=[body for path,body in http.calls if path.endswith('save_site_collect_item_info')]
    publishes=[body for path,body in http.calls if path.endswith('save_move_collect_task')]
    assert [row['site'] for row in saves]==['MY']
    assert len(publishes)==1,json.dumps(result)
    assert len(http.mutations())==6 and ledger.total_attempt_count==6
    by_label={row['target_label']:row for row in result['targets']}
    assert by_label['tiktok:LH_PH']['status']=='PROCESSING'
    assert by_label['tiktok:LH_PH']['evidence']['outcome_unknown'] is True
    assert result['external_write_count'] is None


def test_real_runner_retains_final_authority_and_four_actual_mutations(tmp_path):
    from shared_platform.product_publication_runner import ProductPublicationRunner
    from shared_platform.publication_autopilot import (
        _business_snapshot_digest,
        build_final_approval_receipt,
    )
    from test_product_publication_runner import _SnapshotStore,_report_store
    request,_,http,_,_,executor=live_fixture(tmp_path/'draft-checkpoints',3)
    assert request.release_candidate['snapshot_digest'] == _business_snapshot_digest(request.snapshot)
    assert request.release_candidate['business_snapshot_digest'] == request.release_candidate['snapshot_digest']
    assert request.release_candidate['approved_execution_snapshot_digest'] == request.snapshot['snapshot_digest']
    approval=build_final_approval_receipt(request.release_candidate,approved_by='Kyle')
    runner=ProductPublicationRunner(release_store=_SnapshotStore(request.snapshot),report_store=_report_store(tmp_path))
    kwargs=dict(run_id='tiktok-real-authorized-budget',offer_id=request.snapshot['offer_id'],plan_id=request.snapshot['plan_id'],
        platform_scope=('TIKTOK',),target_scope=request.target_labels,platform_executors={'TIKTOK':executor},
        release_candidate=request.release_candidate,final_approval=approval)
    receipt=runner.run(**kwargs)
    assert len(http.mutations())==4
    assert receipt.report['summary']['evidence']['external_write_count']==4
    budget=receipt.report['mutation_budgets'][0]
    assert budget['attempts']=={'shared':1,'per_target':{'tiktok:LH_PH':3},'total':4}
    assert runner.run(**kwargs).replayed is True
    assert len(http.mutations())==4
    runner.release_store.snapshot['product']['title'] += ' drifted after report'
    with pytest.raises(ValueError):
        runner.run(**kwargs)
    assert len(http.mutations())==4
