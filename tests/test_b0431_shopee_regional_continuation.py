"""Actual retained SH11 and whole DB clone; no API or credential access."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform import native_shopee_regional_continuation as recovery
from test_b0431_tiktok_locale_draft_correction import captured, clone, _whole_original_database


@pytest.fixture
def completion(tmp_path,monkeypatch):
    """Actual post-R74 whole clone and official rows; source/actor are boundaries."""
    supplied=os.environ.get('ORBIT_B0431_SH_COMPLETION_INPUTS')
    if not supplied:pytest.skip('explicit private captured completion inputs not supplied')
    folder=Path(supplied)
    source=folder/'ORIGINAL_RELEASE_STORE_POST_R74_FULL.private.db'
    assert hashlib.sha256(source.read_bytes()).hexdigest()=='494befe1ce4083693d4b552a231ff1d03c393a16c2181123c6f12553f612439a'
    shutil.copy2(source,tmp_path/'actual-whole-clone.db')
    from shared_platform.release_store import ReleaseStore
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService
    from shared_platform.native_sole_final_execution import _NativeExecution,_ACTIVE
    store=ReleaseStore(tmp_path/'actual-whole-clone.db')
    template=Path(os.environ['ORBIT_B0431_SH_COMPLETION_TEMPLATE'])
    doc=json.loads(template.read_bytes());assert doc['status']=='NOT_AUTHORIZED'
    with pytest.raises(RuntimeError):recovery.read_existing_item_completion_proof(dict(path=str(template),sha256=hashlib.sha256(template.read_bytes()).hexdigest()))
    doc['status']='ROOT_TECHNICAL_EXECUTION_APPROVED' # Test-only file; no signed source authority is installed.
    path=tmp_path/'fixture-completion-proof.json';path.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    ref=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert recovery.read_existing_item_completion_proof(ref)==doc
    old_ref=Path(os.environ['ORBIT_B0431_SH_ORIGINAL_REGIONAL_PROOF'])
    old=json.loads(old_ref.read_bytes());ctx=recovery._material(dict(path=str(old_ref),sha256=hashlib.sha256(old_ref.read_bytes()).hexdigest()),old)
    with store._connect_readonly() as db:
        receipt=db.execute('SELECT owner_sid FROM native_sole_final_decisions WHERE decision_id=?',(recovery.DECISION,)).fetchone()
    active=object.__new__(_NativeExecution);active.store=store
    active.service=NativeSoleFinalDecisionService(store,lambda:{})
    active.plan_id,active.decision_id,active.owner_sid=recovery.PLAN,recovery.DECISION,receipt[0]
    runtime=tmp_path/'runtime'
    while len(str(runtime))<155:runtime=runtime/'original-native-checkpoint'
    active.runtime=SimpleNamespace(supervisor=SimpleNamespace(root=runtime))
    ctx.update(active=active,list_only=recovery._list_material(ref,doc))
    monkeypatch.setattr(recovery,'_context',lambda item:ctx if item is active else pytest.fail('Wrong native actor scope'))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**kw:pytest.fail('Network forbidden'))
    token=_ACTIVE.set(active)
    try:yield ctx,store,doc,ref
    finally:_ACTIVE.reset(token)
    assert hashlib.sha256(source.read_bytes()).hexdigest()=='494befe1ce4083693d4b552a231ff1d03c393a16c2181123c6f12553f612439a'


def _completion_original_loop(completion,monkeypatch,tmp_path,*,deny_first_wire=False):
    from modules.products import server,release_adapters
    from modules.shopee import auth,client,global_sku_map,publish
    from domains.channel_operations.release_executor import AdapterRegistration
    from shared_platform import native_canonical_provider_edges as edges,product_publication_executors as executors
    from shared_platform import native_tiktok_locale_draft_correction as tk,native_ozon_three_card_media_recovery as oz
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    from shared_platform.native_prepared_v4_targets import _result
    from shared_platform.native_final_mutation_budget import _DurableLedger
    from shared_platform.product_description_media import shopee_extended_description
    ctx,store,doc,ref=completion;active=ctx['active'];before=deepcopy(store.get_run(recovery.RUN));plan=store.get_plan(recovery.PLAN)
    observed={};model_replies={}
    for label,refs in doc['official_item_refs'].items():
        observed[recovery.LIST_SHOPS[label]]=json.loads(recovery._ref(refs['item_ref']))['response']['item_list'][0]
        model_replies[recovery.LIST_SHOPS[label]]=json.loads(recovery._ref(refs['models_ref']))
    root=Path(os.environ['ORBIT_B0431_SH_CAPTURED_GLOBAL_MODELS'])
    global_reply=json.loads(root.read_bytes())
    tokenfile=tmp_path/'OFFLINE_ONLY_AUTH_FIXTURE.json'
    tokenfile.write_text(json.dumps(dict(sync_shop_ids={k.split(':')[1]:v for k,v in recovery.LIST_SHOPS.items()},
        shops={str(v):dict(region=k.split(':')[1],merchant_id=4970102,access_token='OFFLINE_TEST_TOKEN',expire_at=9999999999) for k,v in recovery.LIST_SHOPS.items()},
        merchants={'4970102':dict(access_token='OFFLINE_TEST_TOKEN',expire_at=9999999999)})),encoding='utf-8')
    monkeypatch.setattr(auth,'token_path',lambda:tokenfile)
    monkeypatch.setattr(global_sku_map,'load_map',lambda:{})
    mapping=[];monkeypatch.setattr(global_sku_map,'record_shop_item',lambda *a,**kw:mapping.append((a,kw)))
    wire=[];claims=[]
    class WireDenied(BaseException):pass
    def merchant_get(path,merchant,token,params):
        assert merchant==4970102
        if path.endswith('/get_global_model_list'):return deepcopy(global_reply)
        if path.endswith('/get_global_item_id'):
            shop=params['shop_id'];item=str(params['item_id_list'])
            assert item==str(observed[shop]['item_id'])
            # Envelope fixture derived from the pinned actual get_published_list relation.
            return dict(error='',response=dict(item_id_map=[dict(item_id=int(item),global_item_id=int(recovery.GLOBAL))]))
        pytest.fail('Uncaptured merchant read '+path)
    def get(path,shop,token,params):
        if path.endswith('/get_item_base_info'):return dict(error='',response=dict(item_list=[deepcopy(observed[shop])]))
        if path.endswith('/get_model_list'):return deepcopy(model_replies[shop])
        pytest.fail('Uncaptured shop read '+path)
    def post(path,shop,token,body):
        label=next(k for k,v in recovery.LIST_SHOPS.items() if v==shop);item=observed[shop]
        ids=doc['approved_image_ids'][label]
        if path.endswith('/update_item'):
            copy='item_name' in body;operation='update_regional_copy' if copy else 'update_description_media'
            request=doc['expected_request_payloads'][label][operation]
            expected=dict(item_id=int(recovery.LIST_ITEMS[label]),description_type='extended',
                description_info=shopee_extended_description(request['description'],ids))
            if copy:expected['item_name']=request['title']
            assert body==expected
        elif path.endswith('/unlist_item'):
            operation='list_regional_item';assert body==dict(item_list=[dict(item_id=int(recovery.LIST_ITEMS[label]),unlist=False)])
        else:pytest.fail('Create/global/upload/stock/price is forbidden')
        wire.append((label,operation,deepcopy(body)))
        with store._connect_readonly() as db:
            rows=recovery._list_facts(db,ctx);slot=next(r for r in reversed(rows) if r['target_label']==label and r['operation']==operation)
            assert slot['request_digest'] and slot['outcome'] is None
        if deny_first_wire:raise WireDenied('Original durable operation reached, no network')
        # Typed mutation-response fixture applies exactly the observed SDK body
        # to this local item. No matcher result or other commercial fact changes.
        if operation=='list_regional_item':
            item['item_status']='NORMAL'
            return dict(error='',response=dict(success_list=[dict(item_id=item['item_id'],unlist=False)]))
        item['description']='';item['description_type']='extended';item['description_info']=deepcopy(body['description_info'])
        if 'item_name' in body:item['item_name']=body['item_name']
        return dict(error='',response={})
    monkeypatch.setattr(client,'merchant_get',merchant_get)
    monkeypatch.setattr(client,'merchant_post',lambda *a,**kw:pytest.fail('No merchant POST'))
    monkeypatch.setattr(client,'shop_get',get);monkeypatch.setattr(client,'shop_post',post)
    monkeypatch.setattr(publish,'_logistic_info',lambda shop,*a,**kw:deepcopy(observed[shop]['logistic_info']))
    class Resolver:
        def __call__(self,request):return recovery.GLOBAL
        def write_count(self,request):return 0
    def adapter(request):
        label=request.target_label
        with store._connect_readonly() as db:
            binding=json.loads(db.execute("SELECT binding_json FROM native_final_budget_scopes WHERE plan_id=? AND platform='SHOPEE'",(recovery.PLAN,)).fetchone()[0])
        def recheck(db=None):
            if db is not None:
                active.service._decision(db,recovery.DECISION,owner_sid=active.owner_sid)
                assert tuple(db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?',(recovery.RUN,label)).fetchone())==('RUNNING',recovery.LIST_BEFORE[label]+1)
        ledger=recovery.for_active(active,_DurableLedger(store=store,binding=binding,target_label=label,recheck=recheck))
        refs=[];deps=edges.shopee_dependencies(ledger,active.runtime.supervisor.root,refs)
        deps=recovery.bind_dependencies(replace(deps,global_item_id_resolver=Resolver()),ledger)
        assert deps.runtime.existing_regional_item(deps.runtime.context(label.split(':')[1]),recovery.GLOBAL)==recovery.LIST_ITEMS[label]
        mapped=dict(file_run_id='native-files-24c3e512eb40b27ee4b58b72a18171caf87359e8c2099676920970900eaab939',
            file_report_id='native-report-24c3e512eb40b27ee4b58b72a18171caf87359e8c2099676920970900eaab939')
        native=PublicationPlatformRequest(run_id=mapped['file_run_id'],report_id=mapped['file_report_id'],platform='SHOPEE',target_labels=(label,),
            snapshot=ctx['snapshot'],write_budget_ledger=ledger,checkpoint_root=active.runtime.supervisor.root)
        canonical=executors.build_shopee_region_executor(global_item_id_resolver=deps.global_item_id_resolver,runtime=deps.runtime)(native)
        return _result(canonical,request,ledger,provider_reference=refs[-1] if refs else None,durable_seams=True)
    registry={name:AdapterRegistration(name,adapter if channel=='shopee' else None,True,True,True,True) for channel,name in release_adapters.ADAPTER_NAMES.items()}
    def gate(data,*,store):
        with store._connect_readonly() as db:active.service._decision(db,recovery.DECISION,owner_sid=active.owner_sid)
        return dict(dashboard={'frozen_review_projection':True},payload=plan['payload'],plan=plan,run=store.get_run(recovery.RUN),predecessor_run=None,
            registry=registry,target_rows=[dict(channel=x.split(':')[0],site=x.split(':')[1],adapter=release_adapters.ADAPTER_NAMES[x.split(':')[0]]) for x in plan['targets']]),None
    active.gate=gate;active.common_dependency_verified=lambda _:True
    monkeypatch.setattr(tk,'activate_failed_targets',lambda _:())
    monkeypatch.setattr(oz,'activate_failed_targets',lambda _:())
    monkeypatch.setattr(oz,'recovery_actions',lambda active,run,actions:actions)
    monkeypatch.setattr(server,'_activate_native_shopee_global_zero',lambda _:())
    monkeypatch.setattr(server,'_activate_native_tiktok_claimed_save',lambda _:())
    begin=store.begin_target
    def traced(run,label):
        value=begin(run,label);claims.append((label,value['attempts']));return value
    monkeypatch.setattr(store,'begin_target',traced)
    data=dict(offer_id=plan['product_id'],plan_id=recovery.PLAN,confirmation_token=plan['confirmation_token'],confirm_publish=True)
    if deny_first_wire:
        with pytest.raises(WireDenied):server._publish_selected_release(data,native_gate=active)
    else:server._publish_selected_release(data,native_gate=active)
    return before,store.get_run(recovery.RUN),wire,claims


def test_actual_four_existing_items_original_loop_frozen_completion_and_list(completion,monkeypatch,tmp_path):
    before,after,wire,claims=_completion_original_loop(completion,monkeypatch,tmp_path)
    assert claims==[(label,recovery.LIST_BEFORE[label]+1) for label in recovery.LIST_BEFORE]
    for old,new in zip(before['targets'],after['targets']):
        if new['target_label'] in recovery.LIST_BEFORE:
            assert new['status']=='SUCCEEDED' and new['failure_events']==old['failure_events']
            ops=[op for label,op,body in wire if label==new['target_label']]
            assert ops==(['update_description_media','list_regional_item'] if new['target_label']=='shopee:PH' else ['update_regional_copy','list_regional_item'])
        else:assert new==old


def test_actual_completion_unknown_wire_keeps_original_prefix_no_retry(completion,monkeypatch,tmp_path):
    before,after,wire,claims=_completion_original_loop(completion,monkeypatch,tmp_path,deny_first_wire=True)
    assert claims==[('shopee:MY',5)] and len(wire)==1 and wire[0][:2]==('shopee:MY','update_regional_copy')
    ctx,store,doc,ref=completion
    with store._connect_readonly() as db:
        rows=recovery._list_facts(db,ctx)
        assert len(rows)==15 and rows[-1]['outcome'] is None and rows[-1]['request_digest']
    assert [r['failure_events'] for r in after['targets']]==[r['failure_events'] for r in before['targets']]
    assert 'shopee:MY' not in recovery.activate_failed_targets(ctx['active'])


def test_completion_wrong_payload_or_new_scope_refused(completion,tmp_path):
    ctx,store,doc,ref=completion
    altered=deepcopy(doc);altered['operation_maxima']['shopee:PH']['update_regional_copy']=1
    p=tmp_path/'altered.json';p.write_text(json.dumps(altered),encoding='utf-8')
    with pytest.raises(RuntimeError):recovery.read_existing_item_completion_proof(dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    recovery.activate_failed_targets(ctx['active']);store.begin_target(recovery.RUN,'shopee:MY')
    from shared_platform.native_final_mutation_budget import _DurableLedger
    with store._connect_readonly() as db:binding=json.loads(db.execute("SELECT binding_json FROM native_final_budget_scopes WHERE plan_id=? AND platform='SHOPEE'",(recovery.PLAN,)).fetchone()[0])
    base=_DurableLedger(store=store,binding=binding,target_label='shopee:MY',recheck=lambda db=None:None)
    ledger=recovery.for_active(ctx['active'],base)
    for operation in ('create_publish_task','upload_regional_image','update_price'):
        with pytest.raises(RuntimeError):ledger.reserve_target('shopee:MY',operation)
    ledger.reserve_target('shopee:MY','update_regional_copy')
    wrong=dict(item_id=recovery.LIST_ITEMS['shopee:MY'],title='unapproved',description='unapproved')
    with pytest.raises(RuntimeError):ledger.dispatch_reserved('update_regional_copy',lambda _:pytest.fail('No wire'),wrong)
    item=json.loads(recovery._ref(doc['official_item_refs']['shopee:MY']['item_ref']))['response']['item_list'][0]
    item['weight']='0.3'
    with pytest.raises(RuntimeError):recovery._completion_parcel(ctx['snapshot'],item)
    models=json.loads(recovery._ref(doc['official_item_refs']['shopee:MY']['models_ref']))['response']['model']
    changed=deepcopy(models);changed[0]['stock_info_v2']['seller_stock'][0]['stock']=201
    with pytest.raises(RuntimeError):recovery._completion_models(ctx['snapshot'],'shopee:MY',changed,models)


@pytest.fixture
def regional(clone, tmp_path, monkeypatch):
    _whole_original_database(clone)
    path = Path(os.environ['ORBIT_B0431_SH_REGIONAL_TEMPLATE'])
    doc = json.loads(path.read_bytes());assert doc['status'] == 'NOT_AUTHORIZED'
    doc['status'] = 'ROOT_TECHNICAL_EXECUTION_APPROVED'  # Local fixture only; no source event/actual authority is created.
    authorized = tmp_path / 'fixture-technical-proof.json'
    authorized.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    ref = dict(path=str(authorized), sha256=hashlib.sha256(authorized.read_bytes()).hexdigest())
    assert recovery.read_regional_proof(ref) == doc
    ctx = recovery._material(ref, doc);ctx['active'] = clone['active']
    monkeypatch.setattr(recovery, '_context', lambda active: ctx if active is clone['active'] else pytest.fail('wrong active'))
    return dict(clone=clone, ctx=ctx, template=path)


def _base(regional, label):
    from shared_platform.native_final_mutation_budget import _DurableLedger
    ctx = regional['ctx'];active = ctx['active'];store = active.store
    with store._connect_readonly() as db:
        scope = db.execute("SELECT binding_json FROM native_final_budget_scopes WHERE platform='SHOPEE' AND plan_id=?", (recovery.PLAN,)).fetchone()
    def recheck(db=None):
        if db is not None:
            active.service._decision(db, recovery.DECISION, owner_sid=active.owner_sid)
            row = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (recovery.RUN, label)).fetchone()
            assert tuple(row) == ('RUNNING', recovery.BEFORE[label] + 1)
    return _DurableLedger(store=store, binding=json.loads(scope[0]), target_label=label, recheck=recheck)


def test_actual_shared10_is_not_regional_dispatch_and_default_authority_refused(regional):
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    ctx = regional['ctx'];store = ctx['active'].store;rows = recovery._joined(ctx['image'])
    assert len(rows) == 11 and len([r for r in rows if r['budget_scope'] == 'shared']) == 10
    assert [r['target_label'] for r in rows if r['budget_scope'] == 'target'] == ['shopee:PH']
    th = next(r for r in ctx['image']['release_target_failure_events'] if r['target_label'] == 'shopee:TH' and r['attempt'] == 3)
    evidence = json.loads(th['evidence_json'])
    assert evidence['canonical_platform_result']['targets'][0]['status'] == 'PROCESSING'
    assert not any(r['budget_scope'] == 'target' and r['target_label'] == 'shopee:TH' for r in rows)
    template = regional['template'];ref = dict(path=str(template), sha256=hashlib.sha256(template.read_bytes()).hexdigest())
    with pytest.raises(NativeMutationBudgetHold, match='EXACT_TECHNICAL_AUTHORITY_REQUIRED'):recovery.read_regional_proof(ref)
    before = deepcopy(store.get_run(recovery.RUN))
    assert recovery.activate_failed_targets(ctx['active']) == tuple(recovery.BEFORE)
    after = store.get_run(recovery.RUN)
    for original, current in zip(before['targets'], after['targets']):
        assert original['failure_events'] == current['failure_events']
        if current['target_label'] in recovery.BEFORE:
            assert current['status'] == 'PENDING' and current['attempts'] == original['attempts']
        else:assert current == original
    with store._connect_readonly() as db:assert recovery._facts(db, ctx) == rows


@pytest.mark.parametrize('label', recovery.NOT_SENT)
def test_regional_NOT_SENT_original_begin_and_budget_no_shared_recreate(regional, label):
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    ctx = regional['ctx'];active = ctx['active'];store = active.store
    recovery.activate_failed_targets(active)
    target = store.begin_target(recovery.RUN, label)
    assert target['status'] == 'RUNNING' and target['attempts'] == recovery.BEFORE[label] + 1
    ledger = recovery.for_active(active, _base(regional, label))
    assert ledger.shared_attempt_count == 10 and ledger.target_attempt_counts[label] == 0
    with pytest.raises(NativeMutationBudgetHold, match='SHARED_WRITE_FORBIDDEN'):ledger.reserve_shared('create_global_item')
    ledger.reserve_target(label, 'create_publish_task')
    with pytest.raises(NativeMutationBudgetHold):ledger.reserve_target(label, 'create_publish_task')
    assert ledger.target_attempt_counts[label] == 1 and ledger.shared_attempt_count == 10
    # Even an undispatched UNKNOWN is not an implicit regional retry.
    with pytest.raises(NativeMutationBudgetHold, match='ALREADY_ATTEMPTED_OR_UNKNOWN'):
        _failed_preimage(regional, label)


def _failed_preimage(regional, label):
    # A separate local clone scenario: zero-claim preimage with a new UNKNOWN
    # reservation may not be activated. No original row in evidence is changed.
    active = regional['ctx']['active']
    with active.store._transaction() as db:
        db.execute("UPDATE release_target_runs SET status='FAILED',attempts=? WHERE run_id=? AND target_label=?",
            (recovery.BEFORE[label], recovery.RUN, label))
    return recovery.activate_failed_targets(active)


def test_original_loop_PH_existing_item_list_outlet_no_new_task(regional, monkeypatch, tmp_path):
    from modules.products import server, release_adapters
    from domains.channel_operations.release_executor import AdapterRegistration
    from shared_platform import native_tiktok_locale_draft_correction as tiktok
    from shared_platform import native_ozon_three_card_media_recovery as media
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    from shared_platform.product_publication_executors import ShopeeRegionExecutorDependencies
    from modules.shopee.skill_regions import OfficialShopeeRegionRuntime
    from modules.shopee import client
    active = regional['ctx']['active'];store = active.store;plan = store.get_plan(recovery.PLAN);claims = [];calls = []
    image = regional['ctx']['image'];refs = image['official_SH_global_and_PH_observation_refs']
    def load(name):return json.loads(recovery._ref(refs[name]))
    item_reply = load('SHOPEE_PH_47970054181_PH_ITEM.response.private.json')
    model_reply = load('SHOPEE_PH_47970054181_PH_MODELS.response.private.json')
    context = SimpleNamespace(region='PH', shop_id=1527371343, merchant_id=4970102, shop_token='fixture', merchant_token='fixture')
    class WireOutlet(BaseException):pass
    def adapter(request):
        if request.target_label == 'shopee:MY':
            # The original frozen order begins MY. Its resolver is a named
            # local boundary in this PH outlet case, with no provider request.
            raise ValueError('Fixture MY resolver boundary, no provider request')
        assert request.target_label == 'shopee:PH'
        ledger = recovery.for_active(active, _base(regional, request.target_label))
        class Runtime(OfficialShopeeRegionRuntime):
            def existing_regional_item(self, ctx, gid):return None
            def resolved_global_item_id(self, ctx, item):return recovery.GLOBAL
            def list_item(self, ctx, item):
                return ledger.dispatch_reserved('list_regional_item',
                    lambda wire: super(Runtime, self).list_item(ctx, item), {'item_id': item})
        def get(path, shop, token, body):
            assert shop == context.shop_id
            if path.endswith('/get_item_base_info'):return deepcopy(item_reply)
            if path.endswith('/get_model_list'):return deepcopy(model_reply)
            raise AssertionError('Uncaptured read is not a passed provider fact')
        def post(path, shop, token, body):
            calls.append((path, shop, deepcopy(body)))
            assert path == '/api/v2/product/unlist_item' and shop == 1527371343
            assert body == {'item_list': [{'item_id': 47970054181, 'unlist': False}]}
            with store._connect_readonly() as db:
                rows = recovery._facts(db, regional['ctx'])
                assert len(rows) == 12 and rows[-1]['operation'] == 'list_regional_item' and rows[-1]['request_digest']
            raise WireOutlet('Original unlist=False SDK outlet, no provider request')
        monkeypatch.setattr(client, 'shop_get', get);monkeypatch.setattr(client, 'shop_post', post)
        class Resolver:
            def __call__(self, request):return recovery.GLOBAL
            def write_count(self, request):return 0
        deps = recovery.bind_dependencies(ShopeeRegionExecutorDependencies(Resolver(), Runtime()), ledger)
        assert deps.global_item_id_resolver(request) == recovery.GLOBAL
        assert deps.runtime.existing_regional_item(context, recovery.GLOBAL) == recovery.PH_ITEM
        class WrongModels(Runtime):
            def regional_models(self, ctx, item):return [{'model_sku': '2000431', 'model_id': 'wrong'}]
        wrong = recovery.bind_dependencies(ShopeeRegionExecutorDependencies(Resolver(), WrongModels()), ledger)
        with pytest.raises(NativeMutationBudgetHold, match='PH_OFFICIAL_MODELS_REQUIRED'):
            wrong.runtime.existing_regional_item(context, recovery.GLOBAL)
        with pytest.raises(NativeMutationBudgetHold, match='PH_RECREATE'):
            deps.runtime.create_publish_task(context, {'global_item_id': int(recovery.GLOBAL)})
        with pytest.raises(NativeMutationBudgetHold, match='NO_RECREATE'):
            ledger.reserve_target('shopee:PH', 'create_publish_task')
        ledger.reserve_target('shopee:PH', 'list_regional_item')
        deps.runtime.list_item(context, recovery.PH_ITEM)
    registry = {name: AdapterRegistration(name, adapter if channel == 'shopee' else None, True, True, True, True)
        for channel, name in release_adapters.ADAPTER_NAMES.items()}
    def gate(data, *, store):
        with store._connect_readonly() as db:active.service._decision(db, recovery.DECISION, owner_sid=active.owner_sid)
        return dict(dashboard={'frozen_review_projection': True}, payload=plan['payload'], plan=plan, run=store.get_run(recovery.RUN),
            predecessor_run=None, registry=registry, target_rows=[dict(channel=label.split(':')[0], site=label.split(':')[1],
                adapter=release_adapters.ADAPTER_NAMES[label.split(':')[0]]) for label in plan['targets']]), None
    active.gate = gate;active.common_dependency_verified = lambda gate: True
    monkeypatch.setattr(server, '_activate_native_shopee_global_zero', lambda active: ())
    monkeypatch.setattr(server, '_activate_native_tiktok_claimed_save', lambda active: ())
    monkeypatch.setattr(tiktok, 'activate_failed_targets', lambda active: ())
    monkeypatch.setattr(media, 'activate_failed_targets', lambda active: (_ for _ in ()).throw(NativeMutationBudgetHold('OZON_THREE_MEDIA_TEST_BOUNDARY')))
    monkeypatch.setattr(media, 'recovery_actions', lambda active, run, actions: actions)
    original = store.begin_target
    def begin(run, label):
        row = original(run, label);claims.append((label, row['status'], row['attempts']));return row
    monkeypatch.setattr(store, 'begin_target', begin)
    try:server._publish_selected_release(dict(offer_id=plan['product_id'], plan_id=recovery.PLAN,
        confirmation_token=plan['confirmation_token'], confirm_publish=True), native_gate=active)
    except WireOutlet:pass
    else:pytest.fail(str(dict(claims=claims, calls=calls)))
    assert claims == [('shopee:MY', 'RUNNING', 4), ('shopee:PH', 'RUNNING', 4)] and len(calls) == 1
