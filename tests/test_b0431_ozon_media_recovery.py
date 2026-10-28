"""Finite media/stock recovery in temporary DBs; no live or provider writes.

The original B rows and approved snapshot are read query-only. Source/actor
installation is an explicit fixture boundary. Visual proofs here are synthetic
test data, never technical verification of the real Ozon images.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sqlite3
import shutil
import hashlib
from types import SimpleNamespace

import pytest

from shared_platform import native_ozon_three_card_media_recovery as media
from shared_platform.native_final_mutation_budget import SCHEMA_SQL, _DurableLedger, _json, _sha


@pytest.fixture
def two_cards(tmp_path, monkeypatch):
    """Whole captured DB; only installation/actor/current-source are fixtures."""
    root = Path(os.environ.get('ORBIT_B0431_TWO_CARD_INPUTS', ''))
    if not (root/'ORIGINAL_RELEASE_STORE_FULL.private.db').is_file():pytest.skip('explicit private captured clone not supplied')
    source = root/'ORIGINAL_RELEASE_STORE_FULL.private.db'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == 'c551c511e5e2afc72046bab72bae73360b85bcb38814e5a620c05181d9493ea9'
    shutil.copy2(source,tmp_path/'whole-private-clone.db')
    from shared_platform.release_store import ReleaseStore
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService
    from shared_platform.native_sole_final_execution import _NativeExecution,_ACTIVE
    store = ReleaseStore(tmp_path/'whole-private-clone.db')
    image = json.loads((root/'CURRENT_IDLE_ORIGINAL_RUN_SHOPEE_REGIONAL_PREIMAGE.private.json').read_text(encoding='utf-8'))
    # Source 8734 does not exist in this earlier retained DB capture. This one
    # fixture field stages the new pure proof; genuine source/HMAC is NOT tested.
    image['source_head'] = '8734a3a957811d4352f081cb3d82b741a6e67c45'
    with store._connect_readonly() as db:
        grant = dict(db.execute('SELECT * FROM native_ozon_media_grants WHERE run_id=?',(media.RUN,)).fetchone())
        decision = dict(db.execute('SELECT * FROM native_sole_final_decisions WHERE decision_id=?',(media.DECISION,)).fetchone())
        raw = db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?',(media.PLAN,)).fetchone()[0]
    assert _sha(raw) == media.SNAPSHOT_RAW_SHA
    active = object.__new__(_NativeExecution)
    active.store,active.service = store,NativeSoleFinalDecisionService(store,lambda:{})
    active.plan_id,active.decision_id,active.owner_sid = media.PLAN,media.DECISION,decision['owner_sid']
    active.runtime = SimpleNamespace(supervisor=SimpleNamespace(root=tmp_path/'runtime'))
    terminal = Path(os.environ['ORBIT_B0431_TWO_CARD_TERMINAL'])
    doc = dict(schema='native-ozon-two-eligible-card-stock-technical-authority/v1',status='ROOT_TECHNICAL_EXECUTION_APPROVED',
        task_id='TASK-20261023-001',offer_id='8412287877065586543',plan_id=media.PLAN,run_id=media.RUN,decision_id=media.DECISION,
        candidate_digest='c5eacc9725ecefa012af02c6d4f5130c6098c9bedab65e2159357a708c73a5cc',snapshot_digest=media.SNAPSHOT,
        account_id=media.ACCOUNT,products=media.PRODUCTS,selected_offers=list(media.PARTIAL_OFFERS),blocked_offer='2000431',
        stock_per_selected=200,warehouse_id=media.PARTIAL_WAREHOUSE,original_shared_maximum=4,original_accepted_imports=3,
        remaining_stock_batches=1,new_import=False,new_media=False,new_budget=False,new_human_review=False,full_three_card_success=False,
        stock_body=media._two_stock_body(),base_source_head=image['source_head'],retained_ref=_pin(tmp_path/'retained.json',image),
        official_terminal_ref=dict(path=str(terminal.resolve()),sha256=media.TERMINAL_SHA))
    ref=_pin(tmp_path/'proof.json',doc);assert media.read_two_card_stock_proof(ref)==doc
    ctx=dict(active=active,body=json.loads(grant['body_json']),grant_id=grant['grant_id'],snapshot=json.loads(raw),
        root=tmp_path/'responses',partial=media._partial_material(ref,doc))
    from shared_platform import publication_ozon_card_structure as structure
    # Existing trusted startup structure/API reader is an explicit installation
    # boundary. Its original frozen value is used; no source gate is claimed.
    monkeypatch.setattr(structure,'require_verified_snapshot_structure',lambda snapshot:deepcopy(ctx['snapshot']['product']['ozon_card_structure']))
    monkeypatch.setattr(media,'_context',lambda value:ctx if value is active else pytest.fail('wrong native context'))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**kw:pytest.fail('Network forbidden'))
    token=_ACTIVE.set(active)
    try:yield ctx,store,image,doc,ref
    finally:_ACTIVE.reset(token)
    assert hashlib.sha256(source.read_bytes()).hexdigest()=='c551c511e5e2afc72046bab72bae73360b85bcb38814e5a620c05181d9493ea9'


def _two_card_original_loop(fixture,monkeypatch,*,deny_wire=False):
    from modules.products import server,release_adapters
    from domains.channel_operations.release_executor import AdapterRegistration
    from modules.ozon.approved_publication_v4 import execute_ozon_v4_publication,project_ozon_v4_variants,OzonStockDispatchFact
    from shared_platform.native_prepared_v4_targets import _result
    from shared_platform import native_tiktok_locale_draft_correction as tk,native_shopee_regional_continuation as sh
    ctx,store,image,doc,ref=fixture;active=ctx['active'];plan=store.get_plan(media.PLAN);before=deepcopy(store.get_run(media.RUN))
    # Real official identity/status/duplicate response. Frozen matching fields
    # absent from that capture are exact fixtures, NOT a current official read.
    official=json.loads(media._read_reference(doc['official_terminal_ref']))['items']
    profile=dict(schema_version='ozon-official-profile-resolution/v1',resolution='EXACT',description_category_id=53968796,
        category_name='Accessories and Spare Parts for Refrigerators · Чехол',category_path=deepcopy(ctx['snapshot']['categories_by_target'][media.TARGET]['category']['path']),
        type_id=970663046,type_name='Captured category protocol fixture',required_attributes=dict(brand=dict(attribute_id=85,dictionary_value_id=1,value='Fixture brand'),
            model_name=dict(attribute_id=9048),product_type=dict(attribute_id=8229,dictionary_value_id=970663046,value='Fixture product type')),
        independent_card_attributes=dict(model_name=9048,quantity_per_pack=8513))
    variants=project_ozon_v4_variants(ctx['snapshot'],target_labels=(media.TARGET,),official_profile_resolver=lambda _:profile)
    observed=[]
    for variant,item in zip(variants,official):
        card=variant['independent_card']
        observed.append(dict(offer_id=variant['offer_id'],id=item['id'],statuses=item['statuses'],
            name=variant['title'].replace(' | ',' ',1),description=variant['description'],price=variant['price'],old_price=variant['old_price'],
            images=variant['images'],category_id=variant['category']['id'],type_id='970663046',weight_kg=variant['parcel']['weight_kg'],
            package_cm=variant['parcel']['package_cm'],independent_card=card))
    # Use the existing card matcher input shape, without inventing provider reads.
    from shared_platform.publication_ozon_card_structure import card_readback_matches
    for item,variant in zip(observed,variants):
        item['attributes']={'9048':[dict(value=variant['independent_card']['model_name'])],'8513':[dict(value='1')]}
        assert card_readback_matches(variant['independent_card'],item)
    claims=[];wire=[];reads=[]
    class WireDenied(BaseException):pass
    def adapter(request):
        assert request.target_label==media.TARGET
        with store._connect_readonly() as db:
            binding=json.loads(db.execute("SELECT binding_json FROM native_final_budget_scopes WHERE plan_id=? AND platform='OZON'",(media.PLAN,)).fetchone()[0])
        base=_DurableLedger(store=store,binding=binding,target_label=media.TARGET)
        ledger=media.for_active(active,base)
        with store._connect_readonly() as db:pictures=media._verified(db,ctx)
        def post(path,body):
            if path=='/v3/product/info/list':
                assert body==dict(offer_id=list(media.PRODUCTS),limit=1000,visibility='ALL')
                return dict(items=deepcopy(official))
            if path==media.INFO:
                return dict(items=[dict(product_id=k,errors=[],color_photo=[],photo_360=[],primary_photo=[v[0]],photo=v[1:]) for k,v in pictures.items()])
            assert path=='/v2/products/stocks' and body==media._two_stock_body()
            assert all(r['offer_id']!='2000431' for r in body['stocks'])
            wire.append(deepcopy(body))
            with store._connect_readonly() as db:
                rows=media._stock_rows(image);current=base._rows(db)
                assert len(current)==4 and current[-1]['request_digest'] and current[-1]['outcome']=='UNKNOWN'
                assert len(rows)==3
            if deny_wire:raise WireDenied('Durable stock slot4 reached; actual wire forbidden')
            return dict(result=[dict(offer_id=k,updated=True,errors=[]) for k in media.PARTIAL_OFFERS])
        wrapped=ledger.wrap_post(post,provider='ozon')
        def current_items(ids):
            wrapped('/v3/product/info/list',dict(offer_id=list(ids),limit=1000,visibility='ALL'))
            # Current formal normalizer omits errors; the frozen-content fields
            # here remain the explicit fixture described above.
            return deepcopy(observed)
        def stock_read(ids):
            assert tuple(ids)==media.PARTIAL_OFFERS
            reads.append(tuple(ids))
            return [dict(offer_id=k,stock=0 if len(reads)==1 else 200,warehouse_id=media.PARTIAL_WAREHOUSE) for k in ids]
        def update(updates):
            wrapped('/v2/products/stocks',dict(stocks=list(updates)))
            ledger.complete_reserved('update_stock','ACCEPTED')
            return OzonStockDispatchFact(outcome='ACCEPTED')
        execute=media.bind_executor(active,ledger,lambda request:execute_ozon_v4_publication(ctx['snapshot'],target_labels=(media.TARGET,),
            official_profile_resolver=lambda _:profile,dispatch_variant=lambda _:pytest.fail('Reimport forbidden'),
            readback_variants=current_items,update_stocks=update,readback_stocks=stock_read,
            before_stock_update=lambda _:ledger.reserve_shared('update_stock'),read_before_dispatch=True,partial_stock_selection=ledger.partial_stock_selection))
        canonical=execute(request)
        value=media.retain_original_reference(active,_result(canonical,request,ledger,durable_seams=True),ledger)
        assert not value.succeeded and value.readback_evidence['outcome_unknown'] is False
        return value
    registry={name:AdapterRegistration(name,adapter,True,True,True,True) for name in release_adapters.ADAPTER_NAMES.values()}
    def gate(data,*,store):
        with store._connect_readonly() as db:active.service._decision(db,media.DECISION,owner_sid=active.owner_sid)
        return dict(dashboard={'frozen_review_projection':True},payload=plan['payload'],plan=plan,run=store.get_run(media.RUN),predecessor_run=None,
            registry=registry,target_rows=[dict(channel=x.split(':')[0],site=x.split(':')[1],adapter=release_adapters.ADAPTER_NAMES[x.split(':')[0]]) for x in plan['targets']]),None
    active.gate=gate;active.common_dependency_verified=lambda _:True
    monkeypatch.setattr(tk,'activate_failed_targets',lambda _:())
    monkeypatch.setattr(sh,'activate_failed_targets',lambda _:())
    monkeypatch.setattr(server,'_activate_native_shopee_global_zero',lambda _:())
    monkeypatch.setattr(server,'_activate_native_tiktok_claimed_save',lambda _:())
    # Other frozen recovery families are outside this exact Ozon proof.
    from shared_platform import native_accepted_save_core_repair as core,native_shopee_known_zero_recovery as zero,native_ozon_existing_media_recovery as oldmedia
    for module in (core,zero,oldmedia):monkeypatch.setattr(module,'activate_failed_targets',lambda _:())
    begin=store.begin_target
    def traced(run,label):
        assert label==media.TARGET
        row=begin(run,label);claims.append((label,row['attempts']));return row
    monkeypatch.setattr(store,'begin_target',traced)
    data=dict(offer_id=plan['product_id'],plan_id=media.PLAN,confirmation_token=plan['confirmation_token'],confirm_publish=True)
    if deny_wire:
        with pytest.raises(WireDenied):server._publish_selected_release(data,native_gate=active)
    else:server._publish_selected_release(data,native_gate=active)
    assert claims==[(media.TARGET,3)] and wire==[media._two_stock_body()]
    after=store.get_run(media.RUN)
    old=next(r for r in before['targets'] if r['target_label']==media.TARGET)
    new=next(r for r in after['targets'] if r['target_label']==media.TARGET)
    assert new['submission']==old['submission'] and new['failure_events'][:1]==old['failure_events']
    assert [r for r in before['targets'] if r['target_label']!=media.TARGET]==[r for r in after['targets'] if r['target_label']!=media.TARGET]
    return new,ctx


def test_two_eligible_original_loop_begin3_one_stock_batch_partial(two_cards,monkeypatch):
    target,ctx=_two_card_original_loop(two_cards,monkeypatch)
    assert target['storage_status']==target['status']=='FAILED' and target['attempts']==3
    partial=target['partial_stock_result']
    assert partial['stock_verified'] is True and partial['all_three_success'] is False and partial['outcome_unknown'] is False
    assert [r['status'] for r in partial['cards']]==['BLOCKED_DUPLICATE','STOCK_VERIFIED','STOCK_VERIFIED']
    assert media.activate_failed_targets(ctx['active'])==()


def test_two_eligible_wire_denied_UNKNOWN_no_repeat(two_cards,monkeypatch):
    target,ctx=_two_card_original_loop(two_cards,monkeypatch,deny_wire=True)
    assert target['storage_status']=='RUNNING' and target['attempts']==3
    assert media.activate_failed_targets(ctx['active'])==()


def test_two_card_typed_proof_and_forbidden_body(two_cards,tmp_path):
    ctx,store,image,doc,ref=two_cards
    for patch in (dict(status='NOT_AUTHORIZED'),dict(selected_offers=['2000431','2000433']),dict(remaining_stock_batches=2)):
        with pytest.raises(RuntimeError):media.read_two_card_stock_proof(_pin(tmp_path/('bad-'+str(len(patch))+str(list(patch))+'.json'),{**doc,**patch}))
    ledger=media._StockOnlyLedger(ctx,None)
    for body in (dict(stocks=[dict(offer_id=k,stock=200,warehouse_id=media.PARTIAL_WAREHOUSE) for k in media.PRODUCTS]),
                 dict(stocks=[dict(offer_id=k,stock=201,warehouse_id=media.PARTIAL_WAREHOUSE) for k in media.PARTIAL_OFFERS])):
        with pytest.raises(RuntimeError):ledger._stock_body(body)


def _pin(path, value):
    path.write_text(_json(value), encoding='utf-8')
    return {'path': str(path.resolve()), 'sha256': _sha(_json(value))}


def test_real_r53_typed_reader_and_default_denial(tmp_path):
    source = Path(os.environ.get('ORBIT_B0431_R53', ''))
    if not source.is_file():pytest.skip('explicit R53 file not requested')
    ref = {'path': str(source.resolve()), 'sha256': _sha(source.read_text(encoding='utf-8'))}
    assert media.read_media3_decision(ref)['extra_media_maximum'] == 3
    for value in (None, {}, {**ref, 'sha256': '0' * 64}):
        with pytest.raises((RuntimeError, ValueError, TypeError, KeyError)):media.read_media3_decision(value)
    document = json.loads(source.read_text(encoding='utf-8'))
    for key,value in (('extra_media_maximum', 4), ('products', {'2000431': 999}), ('budget_reset_or_borrow', True)):
        altered = {**document, key: value}
        with pytest.raises(RuntimeError):media.read_media3_decision(_pin(tmp_path/(key+'.json'), altered))
    with pytest.raises(RuntimeError):media._context(SimpleNamespace())


def test_original_official_read_preflight_replay_has_no_reimport():
    path = os.environ.get('ORBIT_B0431_R53')
    if not path:pytest.skip('explicit actual R53 evidence not requested')
    source = Path(path)
    authority = media.read_media3_decision({'path': str(source.resolve()), 'sha256': _sha(source.read_text(encoding='utf-8'))})
    proposal = json.loads(media._read_reference(authority['proposal_ref']))
    report = json.loads(media._read_reference(proposal['basis']['original_import_readback']))
    replies = json.loads(media._read_reference(report['private_responses']))
    observed = []
    def post(endpoint, body):
        reply = replies[len(observed)]
        assert (endpoint, body) == (reply['path'], reply['body'])
        observed.append(endpoint)
        return deepcopy(reply['response'])
    media._preflight(post)
    assert observed == ['/v1/product/import/info'] * 3 + ['/v3/product/info/list']


@pytest.fixture
def original(tmp_path, monkeypatch):
    database = os.environ.get('ORBIT_B0431_ACTUAL_DB');r53 = os.environ.get('ORBIT_B0431_R53')
    if not database or not r53:pytest.skip('explicit actual query-only B fixture not requested')
    from shared_platform.release_store import ReleaseStore
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    def deny(*args, **kwargs):raise AssertionError('No real provider connection is allowed')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN');db.row_factory = sqlite3.Row
        tables = ['release_plans', 'release_approvals', 'approved_publication_snapshots', 'publication_business_snapshots',
            'native_sole_final_decisions', 'release_runs', 'release_target_runs', 'release_target_failure_events',
            'release_target_submissions', 'release_target_readbacks', 'release_target_repairs', 'native_final_budget_scopes',
            'native_final_budget_reservations', 'native_final_budget_dispatches', 'native_final_budget_outcomes']
        rows = {};schema = {}
        for table in tables:
            schema[table] = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            if table.startswith('native_final_budget_') and table != 'native_final_budget_scopes':
                selected = db.execute('SELECT * FROM '+table+' WHERE scope_key IN (SELECT scope_key FROM native_final_budget_scopes WHERE plan_id=?)', (media.PLAN,))
            else:
                column = 'run_id' if table.startswith('release_target_') else 'plan_id'
                selected = db.execute('SELECT * FROM '+table+' WHERE '+column+'=?', (media.RUN if column == 'run_id' else media.PLAN,))
            rows[table] = [dict(row) for row in selected]
        assert db.execute('PRAGMA query_only').fetchone()[0] == 1
    store = ReleaseStore(tmp_path/'isolated.db')
    with sqlite3.connect(store.path) as db:
        db.executescript(SCHEMA_SQL)
        for table in tables:
            if not table.startswith('native_final_budget_'):db.execute(schema[table])
            for row in rows[table]:
                db.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', tuple(row.values()))
    with store._transaction():pass
    decision = rows['native_sole_final_decisions'][0]
    service = NativeSoleFinalDecisionService(store, lambda: {})
    active = object.__new__(_NativeExecution)
    active.service, active.store, active.plan_id = service, store, media.PLAN
    active.decision_id, active.owner_sid = media.DECISION, decision['owner_sid']
    active.runtime = SimpleNamespace(supervisor=SimpleNamespace(root=tmp_path/'runtime'))
    path = Path(r53);ref = {'path': str(path.resolve()), 'sha256': _sha(path.read_text(encoding='utf-8'))}
    authority = media.read_media3_decision(ref)
    proposal = json.loads(Path(authority['proposal_ref']['path']).read_text(encoding='utf-8'))
    body = dict(schema='native-three-card-existing-media-grant/v1', plan_id=media.PLAN, run_id=media.RUN,
        decision_id=media.DECISION, snapshot_digest=media.SNAPSHOT, account_id=media.ACCOUNT, products=media.PRODUCTS,
        user_ref=ref, proposal_ref=authority['proposal_ref'], requests=proposal['requests_not_executed'],
        original_target_preimage=next(row for row in rows['release_target_runs'] if row['target_label'] == media.TARGET),
        original_failure_rows=[row for row in rows['release_target_failure_events'] if row['target_label'] == media.TARGET],
        original_submission_rows=[row for row in rows['release_target_submissions'] if row['target_label'] == media.TARGET])
    snapshot = json.loads(rows['approved_publication_snapshots'][0]['snapshot_json'])
    assert _sha(rows['approved_publication_snapshots'][0]['snapshot_json']) == media.SNAPSHOT_RAW_SHA
    ctx = dict(active=active, body=body, grant_id=_sha(_json(body)), snapshot=snapshot, root=tmp_path/'responses')
    monkeypatch.setattr(media, '_context', lambda value: ctx if value is active else (_ for _ in ()).throw(ValueError('not protected')))
    token = _ACTIVE.set(active)
    try:yield ctx, rows
    finally:_ACTIVE.reset(token)


class ProviderFixture:
    """Synthetic protocol responses only, with an explicit mutation journal."""
    def __init__(self, fail=None):self.calls=[];self.fail=fail
    def __call__(self, endpoint, body):
        self.calls.append((endpoint, deepcopy(body)))
        if endpoint == '/v1/product/import/info':
            index = media.TASKS.index(body['task_id']);offer = list(media.PRODUCTS)[index]
            return {'result': {'total': 1, 'items': [{'offer_id': offer, 'product_id': media.PRODUCTS[offer], 'status': 'imported'}]}}
        if endpoint == '/v3/product/info/list':
            return {'items': [dict(id=product, offer_id=offer, is_archived=False, statuses={'is_created': True}) for offer,product in media.PRODUCTS.items()]}
        if endpoint == media.IMPORT:
            if body['product_id'] == self.fail:raise TimeoutError('synthetic unknown after durable dispatch')
            return {'result': {'pictures': [dict(product_id=body['product_id'], url=url, state='imported',
                is_primary=index == 0, is_360=False, is_color=False) for index,url in enumerate(body['images'])]}}
        if endpoint == media.INFO:return _pictures()
        if endpoint == '/v2/products/stocks':return {'result': [{'updated': True, 'errors': []}]}
        raise AssertionError('No other endpoint, especially product/import, is allowed')


def _pictures():
    return {'items': [dict(product_id=product, errors=[], color_photo=[], photo_360=[],
        primary_photo=[f'https://synthetic.test/{product}/0.jpg'],
        photo=[f'https://synthetic.test/{product}/{n}.jpg' for n in range(1,8)]) for product in media.PRODUCTS.values()]}


def _proof(ctx, tmp_path):
    observed = media.picture_rows(_pictures())
    picture_ref = _pin(tmp_path/'pictures.json', _pictures())
    visual_ref = _pin(tmp_path/'synthetic-visual.json', {'synthetic_test_fixture': True})
    cards = []
    for request in ctx['body']['requests']:
        offer = request['offer_id'];product = media.PRODUCTS[offer]
        positions = [dict(position=n+1, role=request['ordered_roles'][n], source_url_sha256=_sha(url),
            ozon_image_url=observed[product][n], same_approved_image=True, order_matches=True,
            is_primary=n == 0, actual_visual_evidence_ref=visual_ref) for n,url in enumerate(request['body']['images'])]
        cards.append(dict(offer_id=offer, product_id=product, positions=positions))
    return _pin(tmp_path/'synthetic-proof.json', dict(schema_version='ozon-three-card-media-content-assessment/v1',
        status='PASS', method='ACTUAL_TECHNICAL_VISUAL_REVIEW', grant_id=ctx['grant_id'],
        user_decision_ref=ctx['body']['user_ref'], pictures_info_ref=picture_ref, cards=cards))


def test_three_once_then_verified_original_stock_only(original, tmp_path):
    ctx, rows = original;active = ctx['active'];store = active.store;provider = ProviderFixture()
    before = next(row for row in store.get_run(media.RUN)['targets'] if row['target_label'] == media.TARGET)
    others = [row for row in store.get_run(media.RUN)['targets'] if row['target_label'] != media.TARGET]
    media._intake(ctx)
    assert media._prepare_media(ctx, provider)
    assert media._prepare_media(ctx, provider)  # no repeated picture request
    assert len([row for row in provider.calls if row[0] == media.IMPORT]) == 3
    assert not [row for row in provider.calls if row[0] == '/v3/product/import']
    with store._connect_readonly() as db:
        assert media._verified(db, ctx) is None  # acceptance/count is not content verification
        assert db.execute("SELECT count(*) FROM native_final_budget_reservations r JOIN native_final_budget_scopes s USING(scope_key) WHERE s.platform='OZON'").fetchone()[0] == 3
    media.intake_verification(active, _proof(ctx, tmp_path))
    assert media.activate_failed_targets(active) == (media.TARGET,)
    target = next(row for row in store.get_run(media.RUN)['targets'] if row['target_label'] == media.TARGET)
    assert target['storage_status'] == 'PENDING' and target['attempts'] == 1 and target['failure_events'] == before['failure_events']
    assert target['submission'] == before['submission']
    store.begin_target(media.RUN, media.TARGET)
    with store._connect_readonly() as db:
        row = db.execute("SELECT binding_json FROM native_final_budget_scopes WHERE plan_id=? AND platform='OZON'", (media.PLAN,)).fetchone()
    base = _DurableLedger(store=store, binding=json.loads(row[0]), target_label=media.TARGET)
    ledger = media.for_active(active, base);post = ledger.wrap_post(provider, provider='ozon')
    with pytest.raises(RuntimeError):ledger.reserve_shared('import_variant')
    warehouse = ctx['snapshot']['product']['ozon_stock_decision']['warehouse_id']
    from modules.ozon.approved_publication_v4 import _complete_governed_stock, OzonStockDispatchFact
    count = []
    def stock_read(offer_ids):
        count.append(True)
        return [dict(offer_id=offer, stock=0 if len(count) == 1 else 200, warehouse_id=warehouse) for offer in offer_ids]
    def stock_update(updates):
        post('/v2/products/stocks', {'stocks': [{**row, 'warehouse_id': warehouse} for row in updates]})
        ledger.complete_reserved('update_stock', 'ACCEPTED')
        return OzonStockDispatchFact(outcome='ACCEPTED')
    canonical = _complete_governed_stock(
        variants=tuple(dict(offer_id=offer, stock_quantity=200, stock_warehouse_id=warehouse) for offer in media.PRODUCTS),
        update_stocks=stock_update, readback_stocks=stock_read,
        before_stock_update=lambda _: ledger.reserve_shared('update_stock'),
        prior_external_write_count=0, prior_dispatch_attempted=False)
    assert canonical['targets'][0]['status'] == 'PUBLISHED'
    assert base.snapshot()['attempts']['shared'] == 4
    assert ledger.snapshot()['technical_media_budget']['attempts']['shared'] == 3
    with pytest.raises(RuntimeError):ledger.reserve_shared('update_stock')
    assert len([row for row in provider.calls if row[0] == '/v2/products/stocks']) == 1
    assert len([row for row in provider.calls if row[0] == media.IMPORT]) == 3
    from shared_platform.native_prepared_v4_targets import _result
    value = _result(canonical, SimpleNamespace(target_label=media.TARGET), ledger,
        provider_reference='temporary read reference', durable_seams=True)
    value = media.retain_original_reference(active, value, ledger)
    store.record_target_success(media.RUN, media.TARGET, external_id=value.external_reference, readback_evidence=value.readback_evidence)
    final = next(row for row in store.get_run(media.RUN)['targets'] if row['target_label'] == media.TARGET)
    assert final['status'] == 'SUCCEEDED' and final['attempts'] == 2
    assert final['submission'] == before['submission'] and final['failure_events'] == before['failure_events']
    assert final['external_id'] == before['external_id']
    assert [row for row in store.get_run(media.RUN)['targets'] if row['target_label'] != media.TARGET] == others


def test_unknown_card_is_spent_and_no_remaining_card_or_stock_is_dispatched(original):
    ctx, _ = original;provider = ProviderFixture(fail=media.PRODUCTS['2000432'])
    media._intake(ctx)
    assert not media._prepare_media(ctx, provider)
    assert not media._prepare_media(ctx, provider)
    ledger = media._media_ledger(ctx)
    assert ledger.snapshot()['attempts']['shared'] == 2
    assert [row['outcome'] for row in ledger.snapshot()['reservations']] == ['ACCEPTED', 'UNKNOWN']
    assert len([row for row in provider.calls if row[0] == media.IMPORT]) == 2
    assert not [row for row in provider.calls if row[0] in {'/v3/product/import', '/v2/products/stocks'}]


def test_full_gallery_and_private_title_equivalence(original, tmp_path):
    ctx, _ = original
    bad = _pictures();bad['items'][1]['photo'].pop()
    with pytest.raises(RuntimeError):media.picture_rows(bad)
    bad = _pictures();bad['items'][1]['product_id'] = 999
    with pytest.raises(RuntimeError):media.picture_rows(bad)
    proof = _proof(ctx, tmp_path)
    doc = json.loads(Path(proof['path']).read_text());doc['cards'][1]['positions'][0]['role'] = 'refrigerator_cover_daisy'
    with pytest.raises(RuntimeError):media._validate_verification(ctx, _pin(tmp_path/'wrong-hero.json', doc))
    title = 'Frozen Russian title | Three Patterns'
    expected = {'offer_id': '2000432', 'title': title}
    observed = {'offer_id': '2000432', 'id': media.PRODUCTS['2000432'], 'name': title.replace(' | ', ' ', 1)}
    assert not media.title_matches(expected, observed)
    token = media._TITLE_SCOPE.set(dict(active=ctx['active'], snapshot_digest=media.SNAPSHOT, products=media.PRODUCTS))
    try:
        assert media.title_matches(expected, observed)
        for change in ({'id': 999}, {'name': title.lower()}, {'name': title.replace(' | ', '  ')}, {'name': 'different'}):
            assert not media.title_matches(expected, {**observed, **change})
        assert not media.title_matches({**expected, 'title': title+' | extra'}, observed)
    finally:media._TITLE_SCOPE.reset(token)
    from modules.ozon.approved_publication_v4 import _classify_variant, OzonDispatchFact
    expected.update(description='Frozen description', price='10', old_price='20', image_count=8,
        category={'id': '53968796'}, parcel={'weight_kg': '0.2', 'package_cm': [30,30,5]})
    observed.update(description='Frozen description', price='10', old_price='20', images=list(range(8)),
        category_id='53968796', weight_kg='0.2', package_cm=[30,30,5], statuses={'is_created': True, 'status': 'price_sent'})
    dispatch = OzonDispatchFact(outcome='ACCEPTED', task_id=str(media.TASKS[1]))
    assert _classify_variant(expected, observed, dispatch) == 'PROCESSING'
    token = media._TITLE_SCOPE.set(dict(active=ctx['active'], snapshot_digest=media.SNAPSHOT, products=media.PRODUCTS))
    try:assert _classify_variant(expected, observed, dispatch) == 'PUBLISHED'
    finally:media._TITLE_SCOPE.reset(token)
