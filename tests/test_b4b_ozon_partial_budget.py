"""Ozon local refusal preserves earlier provider facts through the real runner."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from modules.ozon.approved_publication_v4 import (
    OzonDispatchFact, OzonStockDispatchFact, build_ozon_v4_executor,
)
from shared_platform.publication_write_budget import PublicationWriteBudgetLedger
from shared_platform.product_publication_runner import ProductPublicationRunner
from test_product_publication_runner import _SnapshotStore, _report_store
from test_b4b_release_compiler import snapshot
from test_ozon_approved_publication_v4 import _published_item


def ozon_payload():
    from test_approved_publication_snapshot import _approved_plan, _rebind, _category_decision
    from domains.product_operations import build_approved_publication_snapshot
    plan=_approved_plan()
    # Retain a real wall-sticker control, now complete under its family rules.
    facts=plan['payload']['product_facts']
    facts['title']='Bear wall sticker'
    facts['description']='Blue and pink bear decoration.'
    facts['category']={'id':'wall-sticker','name':'Wall Stickers'}
    facts['image_urls']=[f'https://img.example/main-{i}.jpg' for i in range(1,8)]
    for label,category_id in (('tiktok:LH_PH','600338'),('tiktok:LH_MY','600338'),('shopee:PH','101157'),('ozon:RU','17027906')):
        facts['categories_by_target'][label]=_category_decision(label,category_id,'Wall Stickers','14500','Home')
    plan['payload']['product_facts']['stock_policy']={
        'schema_version':'publication-default-stock/v1','quantity_per_sku':200,
        'scope':'EACH_SELECTED_SKU','source':'SYSTEM_GOVERNED_DEFAULT','review_round':'ROUND1'}
    for row in plan['payload']['pricing']['selected_targets']['ozon:RU']['sku_prices']:
        row.update(list_price='40',currency='CNY',old_price_cny='52')
    return plan['payload']


def ozon_snapshot():
    from test_approved_publication_snapshot import _approved_plan,_rebind
    from domains.product_operations import build_approved_publication_snapshot
    plan=_approved_plan();plan['payload']=ozon_payload()
    return build_approved_publication_snapshot(_rebind(plan)).payload()


def real_ozon_store(tmp_path, payload=None):
    from domains.product_operations import (ModelSkuAssignment,SkuAssignment,
        resolve_source_product_identity,resolve_sku_lineage_reservation,finalize_new_source_sku_reservation)
    from shared_platform.release_store import ReleaseStore
    payload=deepcopy(payload) if payload is not None else ozon_payload()
    source=resolve_source_product_identity(collect_box={'source_item_id':'986159122616',
        'itemNum':'JD5047（38*45cm）'},precollect={'source_id':'986159122616'},source_authority='1688').identity
    assignment=SkuAssignment(seller_sku=payload['seller_sku'],model_skus=tuple(
        ModelSkuAssignment(**row) for row in payload['sku_lineage']['assignment']['model_skus']))
    lineage=resolve_sku_lineage_reservation(source_identity=source,predecessor_records=[])
    reservation=finalize_new_source_sku_reservation(source_identity=source,assignment=assignment).reservation.payload()
    payload['source_product_identity']=source.payload()
    payload['sku_lineage']={**lineage.payload(),'assignment':assignment.payload(),
        'reservation':reservation,'reservation_digest':reservation['reservation_digest']}
    payload['digests']['sku_lineage']=reservation['reservation_digest']
    payload['approved_publication_snapshot_schema_version']='approved-publication-snapshot/v4'
    store=ReleaseStore(tmp_path/'release.db')
    preview=store.preview_plan(payload)
    plan=store.create_plan(payload)
    assert preview['confirmation_token']==plan['confirmation_token']
    store.approve_plan(plan['plan_id'],approved_by='Kyle',user_approved=True,confirmation_token=plan['confirmation_token'])
    snapshot=store.approved_publication_snapshot(offer_id=payload['product_id'],plan_id=plan['plan_id'])
    assert snapshot is not None
    return store,store.get_plan(plan['plan_id']),snapshot


def _run(tmp_path, *, cap, first_outcome='ACCEPTED', stock=False):
    value=ozon_snapshot()
    imports=[];stock_writes=[]
    ledger=PublicationWriteBudgetLedger(platform='OZON',target_labels=('ozon:RU',),
        budget={'shared_maximum':cap,'per_target_maximum':0})
    def dispatch(payload):
        imports.append(deepcopy(payload))
        return OzonDispatchFact(outcome=first_outcome,
            task_id='offline-task' if first_outcome=='ACCEPTED' else None)
    def readback(ids):
        return [_published_item(row,item_id=100+index) for index,row in enumerate(imports)
            if first_outcome=='ACCEPTED' and row['offer_id'] in ids]
    executor=build_ozon_v4_executor(dispatch_variant=dispatch,readback_variants=readback,
        update_stocks=lambda rows: stock_writes.append(rows) or OzonStockDispatchFact(outcome='ACCEPTED'),
        readback_stocks=lambda ids:[{'offer_id':identity,'stock':200 if stock_writes else 0} for identity in ids])
    # Request enrichment is the existing documented executor seam; the runner
    # and durable report store below are real, with no provider-level result fake.
    def enriched(request):
        return executor(SimpleNamespace(**{**vars(request),'write_budget_ledger':ledger}))
    runner=ProductPublicationRunner(release_store=_SnapshotStore(value),report_store=_report_store(tmp_path))
    receipt=runner.run(run_id='ozon-partial',offer_id=value['offer_id'],plan_id=value['plan_id'],
        platform_scope=('OZON',),platform_executors={'OZON':enriched})
    return receipt,imports,stock_writes,ledger,runner,value,enriched


@pytest.mark.parametrize('cap',[0,1,2])
def test_local_budget_refusal_retains_known_prefix_and_never_becomes_unknown(tmp_path,cap):
    receipt,imports,stocks,ledger,runner,value,executor=_run(tmp_path,cap=cap)
    assert len(imports)==cap
    assert stocks==[]
    assert ledger.total_attempt_count==cap
    evidence=receipt.report['summary']['evidence']
    assert evidence['external_write_count']==cap
    assert evidence['dispatch_attempted']==bool(cap)
    target=receipt.report['targets'][0]
    assert target['status']=='FAILED'
    assert target['evidence']['outcome_unknown'] is False
    detail=json.loads(target['evidence']['provider_reason'])
    if cap<2:
        assert detail=={'unsent_from':cap,'variant_count':2,'verified_prefix':cap}
    else:
        assert detail=={'imports_verified':True,'stock_attempted':False}
    replay=runner.run(run_id='ozon-partial',offer_id=value['offer_id'],plan_id=value['plan_id'],
        platform_scope=('OZON',),platform_executors={'OZON':executor})
    assert replay.replayed is True
    assert len(imports)==cap


def test_prior_unknown_remains_unknown_when_next_variant_is_locally_refused(tmp_path):
    receipt,imports,stocks,ledger,*_=_run(tmp_path,cap=1,first_outcome='UNKNOWN')
    assert len(imports)==1 and stocks==[] and ledger.total_attempt_count==1
    assert receipt.report['summary']['evidence']['external_write_count'] is None
    assert receipt.report['targets'][0]['status']=='PROCESSING'
    assert receipt.report['targets'][0]['evidence']['outcome_unknown'] is True
