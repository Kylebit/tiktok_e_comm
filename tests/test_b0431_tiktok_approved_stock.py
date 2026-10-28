"""Original accepted-draft captured reads; no provider or actual DB access."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from domains.product_operations.approved_publication_snapshot import validate_approved_publication_snapshot
from modules.miaoshou import tiktok_v4_drafts as drafts


@pytest.fixture
def captured(monkeypatch):
    directory=os.environ.get('ORBIT_B0431_SAVE_CAPTURED')
    if not directory:pytest.skip('explicit retained original read fixture not requested')
    def deny(*args,**kwargs):raise AssertionError('No wire permitted in captured SAVE preparation')
    monkeypatch.setattr(socket.socket,'connect',deny)
    folder=Path(directory)
    def load(name,digest):
        raw=(folder/name).read_bytes();assert sha256(raw).hexdigest()==digest
        return json.loads(raw)
    editable=load('ORIGINAL_MY_EDITABLE_READ.response.private.json','90903a6383c71e21bbdd857eee9b7365eb54a7baaf5e8b25868b0f77e45a462a')
    warehouses=load('ORIGINAL_MY_WAREHOUSES_READ.response.private.json','b17984d50217934346d3420a7444a6657031c553496a8d48a9a7be17153e3261')
    snapshot_path=Path(os.environ['ORBIT_B0431_APPROVED_SNAPSHOT'])
    raw=snapshot_path.read_bytes();assert sha256(raw).hexdigest()=='3e41486796ec38f8f59fa73655abf1bc3deb1df27d62c5222e38baa5d4d02fb1'
    frozen=validate_approved_publication_snapshot(json.loads(raw)).payload()
    target=next(row for row in frozen['publication_targets'] if row['target_label']=='tiktok:LH_MY')
    draft=drafts._draft_payload(frozen,target=target,category=drafts._target_category(frozen,target=target,resolver=None))
    current=deepcopy(editable['data']['siteCollectItemInfo'])
    calls=[]
    def post(path,body):
        calls.append((path,deepcopy(body)))
        if path==drafts.READ_SITE_DRAFT_PATH:
            assert body=={'detailId':3487094129,'site':'MY'}
            return deepcopy(editable)
        if path==drafts.WAREHOUSE_GET_PATH:
            assert body=={'shopIds':['13295169']}
            return deepcopy(warehouses)
        raise AssertionError('Any mutation or extra endpoint is forbidden')
    return dict(frozen=frozen,draft=draft,current=current,post=post,calls=calls,
        identity=dict(target_label='tiktok:LH_MY',detail_id='3487094129',shop_id='13295169'))


def _bind(ctx,draft=None,*,approved_total=None):
    value=deepcopy(ctx['draft'] if draft is None else draft)
    desired=drafts._miaoshou_draft_info(value)
    stock_map,warehouse,zero,total=drafts._tiktok_warehouse_allocation(ctx['post'],
        current=ctx['current'],shop_id='13295169',approved=value.get('warehouse_inventory'))
    return drafts._provider_bound_sku_map(current=ctx['current'],draft=value,desired=desired['skuMap'],
        shop_id='13295169',warehouse_id=warehouse,zero_stock_warehouse_ids=zero,
        approved_warehouse_map=stock_map,approved_total=total if approved_total is None else approved_total)


def test_captured_original_clone_uses_frozen200_and_complete_SAVE(captured):
    ctx=captured;before=json.dumps(ctx['current'],sort_keys=True)
    assert all(not (str(row.get('stock') or '').isdigit() and int(str(row.get('stock') or '0'))>0)
        for row in ctx['current']['skuMap'].values())
    bound=_bind(ctx)
    assert len(bound)==3 and {row['itemNum'] for row in bound.values()}=={'2000431','2000432','2000433'}
    expected={'7496682241901561618':'200','7611376715547969300':'0'}
    assert all(row['stock']==200 and row['shopIdToWarehouseIdAndStockMap']=={'13295169':expected} for row in bound.values())
    transport=drafts.MiaoshouOpenApiTikTokV4DraftTransport(common_detail_id='4088184298',
        post=ctx['post'],read_attempts=1,read_retry_seconds=0,sleep=lambda value:None)
    prepared=transport.prepare_save_draft(identity=ctx['identity'],draft=ctx['draft'])
    assert prepared['path']==drafts.SAVE_SITE_DRAFT_PATH
    body=prepared['body'];info=body['siteCollectItemInfo']
    assert body['detailId']==3487094129 and body['site']=='MY'
    assert len(info['imgUrls'])==8 and len(info['skuMap'])==3
    assert all(row['stock']==200 and row['shopIdToWarehouseIdAndStockMap']=={'13295169':expected} for row in info['skuMap'].values())
    assert json.dumps(ctx['current'],sort_keys=True)==before
    assert set(path for path,_ in ctx['calls'])=={drafts.READ_SITE_DRAFT_PATH,drafts.WAREHOUSE_GET_PATH}


def test_frozen_stock_contract_rejects_drift_and_preserves_legacy(captured):
    ctx=captured
    for change in ({'quantity_per_sku':201},{'quantity_per_sku':True},{'scope':'ONE_SKU'},{'source':'PROVIDER'}):
        invalid=deepcopy(ctx['draft']);invalid['stock_policy'].update(change)
        with pytest.raises(drafts.TikTokV4DraftPreparationError,match='stock policy is invalid'):_bind(ctx,invalid)
    with pytest.raises(drafts.TikTokV4DraftPreparationError,match='stock and warehouse allocation conflict'):
        _bind(ctx,approved_total=199)
    legacy=deepcopy(ctx['draft']);legacy.pop('stock_policy')
    with pytest.raises(drafts.TikTokV4DraftPreparationError,match='approved stock is unavailable'):_bind(ctx,legacy)
    for row in ctx['current']['skuMap'].values():row['stock']='321'
    assert all(row['stock']==321 for row in _bind(ctx,legacy).values())
    assert all(row['stock']==200 for row in _bind(ctx).values())


@pytest.fixture
def preserved(captured,tmp_path,monkeypatch):
    """Immutable owner export into temporary SQLite/CP; no actual DB access."""
    from modules.products import server
    from shared_platform.release_store import ReleaseStore
    from shared_platform.native_final_mutation_budget import SCHEMA_SQL
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService,TABLES,TRIGGERS
    from shared_platform.native_sole_final_execution import _NativeExecution,_ACTIVE
    from shared_platform.product_publication_live_dependencies import TikTokV4DraftCheckpointStore
    from shared_platform import agent_prepared_v4_targets
    folder=Path(os.environ['ORBIT_B0431_SAVE_CAPTURED'])
    raw=(folder/'ORIGINAL_RUN_WITH_SH4_ATTEMPT2_FOR_OFFLINE_REPAIR.private.json').read_bytes()
    assert sha256(raw).hexdigest()=='12aae78097d023001c08a07b1e583e6cb8f219c2e79369eb571b31a193abb06a'
    rows=json.loads(raw)['tables'];store=ReleaseStore(tmp_path/'clone.db')
    schema_raw=(folder/'ORIGINAL_RELEASE_SCHEMA_FOR_CAPTURED_FIXTURE.json').read_bytes()
    assert sha256(schema_raw).hexdigest()=='53c7e5ee397093863d2c8e4fc764704540c6141c25cb4b72b6decc7f774f8fda'
    schema=json.loads(schema_raw)
    with sqlite3.connect(store.path) as db:
        db.executescript(SCHEMA_SQL)
        for table,sql in schema.items():
            if not table.startswith('native_final_budget_'):db.execute(sql)
        db.execute(TABLES[0])
        for sql in TRIGGERS:db.execute(sql)
    with store._transaction():pass
    with sqlite3.connect(store.path) as db:
        for table,records in rows.items():
            for row in records:
                db.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    decision=rows['native_sole_final_decisions'][0]
    active=object.__new__(_NativeExecution)
    active.store=store;active.service=NativeSoleFinalDecisionService(store,lambda:{})
    active.plan_id=decision['plan_id'];active.decision_id=decision['decision_id'];active.owner_sid=decision['owner_sid']
    active.runtime=SimpleNamespace(_operations=SimpleNamespace(agent_prepared_common=object()),supervisor=SimpleNamespace(root=tmp_path/'runtime'))
    request=SimpleNamespace(platform='TIKTOK',snapshot=captured['frozen'],
        run_id='native-files-24c3e512eb40b27ee4b58b72a18171caf87359e8c2099676920970900eaab939')
    checkpoints=TikTokV4DraftCheckpointStore(active.runtime.supervisor.root);path=checkpoints._path(request)
    cp=(folder/'TIKTOK_CHECKPOINT_ALL_FOUR_CREATED_CLAIMED.private.json').read_bytes()
    assert sha256(cp).hexdigest()=='931c0be4dae4a1dd9f3c48b21b85412831a470bd3a8e8f445c86aa9dc37284d8'
    path.parent.mkdir(parents=True);path.write_bytes(cp)
    monkeypatch.setattr(agent_prepared_v4_targets,'_scope',lambda value,plan:None)  # named source/actor boundary
    token=_ACTIVE.set(active)
    try:yield dict(active=active,store=store,rows=rows,request=request,checkpoints=checkpoints,path=path,cp=cp,captured=captured)
    finally:_ACTIVE.reset(token)


@pytest.mark.parametrize('first_channel',['tiktok','shopee'])
def test_original_loop_accepted_claim_or_shopee_zero_begin3(preserved,monkeypatch,first_channel):
    from modules.products import server,release_adapters
    from domains.channel_operations.release_executor import AdapterRegistration
    from shared_platform import native_ozon_three_card_media_recovery as media
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold,_DurableLedger
    from shared_platform.product_publication_live_dependencies import _ResumableTikTokDraftTransport
    ctx=preserved;active=ctx['active'];store=ctx['store'];plan=store.get_plan(active.plan_id)
    run_id='release-run:'+plan['payload_digest'][:24]
    before={row['target_label']:deepcopy(row) for row in store.get_run(run_id)['targets']}
    cp=ctx['checkpoints'].load(ctx['request'])
    class NeverClone:
        def claim_or_create(self,**kwargs):raise AssertionError('Recreate/reclaim forbidden')
    replay=_ResumableTikTokDraftTransport(request=ctx['request'],store=ctx['checkpoints'],transport=NeverClone())
    for label in ('tiktok:LH_MY','tiktok:LH_PH','tiktok:LH_TH','tiktok:LH_VN'):
        fact=replay.claim_or_create(target={'target_label':label},ordinal=0)
        accepted=[event for event in cp['events'] if event['target_label']==label][-1]
        assert fact.operation=='CLAIM_OR_CREATE' and fact.outcome=='ACCEPTED'
        assert (fact.detail_id,fact.shop_id)==(accepted['detail_id'],accepted['shop_id'])
    with store._connect_readonly() as db:
        budget_before={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in
            ('native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes')}
    def hold(value):raise NativeMutationBudgetHold('OZON_THREE_MEDIA_TEST_BOUNDARY')
    monkeypatch.setattr(media,'activate_failed_targets',hold);monkeypatch.setattr(media,'recovery_actions',lambda active,run,actions:actions)
    calls=[];gates=[];claims=[]
    class ExitBeforeWire(BaseException):pass
    def adapter(request):
        calls.append(request.target_label)
        if first_channel=='tiktok':
            original=drafts.MiaoshouOpenApiTikTokV4DraftTransport(common_detail_id='4088184298',post=ctx['captured']['post'],read_attempts=1)
            resumable=_ResumableTikTokDraftTransport(request=ctx['request'],store=ctx['checkpoints'],transport=original)
            fact=resumable.claim_or_create(target={'target_label':request.target_label},ordinal=0)
            identity={'target_label':request.target_label,'detail_id':fact.detail_id,'shop_id':fact.shop_id}
            prepared=resumable.prepare_save_draft(identity=identity,draft=ctx['captured']['draft'])
            assert all(row['stock']==200 for row in prepared['body']['siteCollectItemInfo']['skuMap'].values())
            assert ctx['path'].read_bytes()==ctx['cp']  # at real SAVE outlet, no accepted prefix rewritten
        raise ExitBeforeWire('Original SAVE/PUBLISH transport outlet; no provider mutation allowed')
    registry={name:AdapterRegistration(name,adapter if first_channel=='tiktok' or channel!='tiktok' else None,True,True,True,True)
        for channel,name in release_adapters.ADAPTER_NAMES.items()}
    def gate(data,*,store):
        gates.append(True)
        with store._connect_readonly() as db:active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        return dict(dashboard={'frozen_review_projection':True},payload=plan['payload'],plan=plan,run=store.get_run(run_id),
            predecessor_run=None,registry=registry,target_rows=[dict(channel=label.split(':')[0],site=label.split(':')[1],
                adapter=release_adapters.ADAPTER_NAMES[label.split(':')[0]]) for label in plan['targets']]),None
    active.gate=gate;active.common_dependency_verified=lambda value:True
    original_begin=store.begin_target
    def begin(run,label):
        row=original_begin(run,label);claims.append((label,row['status'],row['attempts']));return row
    monkeypatch.setattr(store,'begin_target',begin)
    with pytest.raises(ExitBeforeWire):server._publish_selected_release(dict(offer_id=plan['product_id'],plan_id=active.plan_id,
        confirmation_token=plan['confirmation_token'],confirm_publish=True),native_gate=active)
    expected='tiktok:LH_MY' if first_channel=='tiktok' else 'shopee:MY'
    assert calls==[expected] and claims==[(expected,'RUNNING',3)]
    after={row['target_label']:row for row in store.get_run(run_id)['targets']}
    for label,row in after.items():
        assert row['failure_events']==before[label]['failure_events'] and row['submission']==before[label]['submission']
        if label!=expected:assert row['attempts']==before[label]['attempts'] and row['error']==before[label]['error']
    assert after['ozon:RU']==before['ozon:RU'] and ctx['path'].read_bytes()==ctx['cp']
    with store._connect_readonly() as db:
        assert {table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in budget_before}==budget_before
        binding=json.loads(db.execute("SELECT binding_json FROM native_final_budget_scopes WHERE platform='TIKTOK'").fetchone()[0])
    # Real durable ceiling proof in the temporary clone; the original eight rows remain prefix.
    ledger=_DurableLedger(store=store,binding=binding,target_label='tiktok:LH_MY')
    assert ledger.target_attempt_counts['tiktok:LH_MY']==1 and ledger.shared_attempt_count==4
    ledger.reserve_target('tiktok:LH_MY','save_draft');ledger.reserve_target('tiktok:LH_MY','publish_target')
    with pytest.raises(RuntimeError):ledger.reserve_target('tiktok:LH_MY','extra_save')


def test_claimed_continuation_rejects_changed_CP_or_extra_budget(preserved):
    from modules.products import server
    from shared_platform.native_final_mutation_budget import _DurableLedger
    ctx=preserved;active=ctx['active'];store=ctx['store']
    before=deepcopy(store.get_run('release-run:25e66f7250d31cbb39ff8d0e'))
    ctx['path'].write_bytes(ctx['cp']+b' ')
    assert server._activate_native_tiktok_claimed_save(active)==()
    ctx['path'].write_bytes(ctx['cp'])
    with store._connect_readonly() as db:binding=json.loads(db.execute("SELECT binding_json FROM native_final_budget_scopes WHERE platform='TIKTOK'").fetchone()[0])
    ledger=_DurableLedger(store=store,binding=binding,target_label='tiktok:LH_MY');ledger.reserve_target('tiktok:LH_MY','save_draft')
    assert server._activate_native_tiktok_claimed_save(active)==()
    assert store.get_run(before['run_id'])==before
