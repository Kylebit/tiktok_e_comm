"""Original native R1/R2/COMMON plus owned, exact official metadata edges."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import r3_technical_category_evidence as supplement
from test_round1_workspace_freeze import live


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(supplement._bytes(value))
    return path


def _evidence(root, documents, *, signing_config=None):
    from modules.miaoshou.tiktok_publisher import EXPECTED_SHOP_ID_BY_TARGET
    ledger = []
    def receipt(provider, endpoint, body):
        key = supplement._digest(dict(provider=provider, endpoint=endpoint, body=body)).removeprefix('sha256:')
        intent = dict(event='intent', provider=provider, endpoint=endpoint, body=body,
            request_scope_digest=key, semantic_read_only=True, as_of_utc='2026-11-01T01:00:00+00:00')
        response = dict(event='response', provider=provider, endpoint=endpoint, request_scope_digest=key,
            response_sha256_canonical=hashlib.sha256(supplement._bytes({'closed_owned_provider_data':body})).hexdigest(),
            as_of_utc='2026-11-01T01:00:01+00:00', result='success', code='success')
        ledger.extend([intent, response]); return response
    labels = [r['target'] for r in documents['first_review']['targets'] if r['target'].startswith('tiktok:')]
    metadata, trees = [], []
    for label in labels:
        site = label.rsplit('_', 1)[-1].split(':')[-1]; shop = EXPECTED_SHOP_ID_BY_TARGET[label]
        metadata.append(dict(target_label=label, site=site, shop_id=shop, category_id='852872',
            receipt=receipt('miaoshou', supplement.META, dict(site=site, cid=852872, shopIds=[int(shop)])),
            category_metadata=dict(categoryProductAttrList=([
                dict(attrId='102255', name='Batch number', isMandatory=True, isCertificationAttr=False,
                    attributeType=3, values=[dict(id='1000256', name='1')])] if label == 'tiktok:GB' else []),
                categorySaleAttrList=[], categoryConfig={'packageDimensionIsRequired': '1'})))
        if site not in [r['site'] for r in trees]:
            trees.append(dict(site=site, receipt=receipt('miaoshou', supplement.TREE, dict(site=site)), nodes=[
                dict(node=dict(cid=852872, name='Storage Holders & Racks', disabled=False),
                     path=[dict(id='600001', name='Home Supplies'), dict(id='852872', name='Storage Holders & Racks')])]))
    oz_tree = dict(ozon=dict(receipt=receipt('ozon', '/v1/description-category/tree', dict(language='EN')),
        matched_nodes=[dict(node=dict(type_id=92404, type_name='Napkin Holder', disabled=False),
            path=[dict(id='17027907', name='Cooking Accessories'), dict(id='92404', name='Napkin Holder')])]))
    oz_meta = dict(description_category_id=17027907, type_id=92404,
        receipt=receipt('ozon', '/v1/description-category/attribute',
            dict(description_category_id=17027907, type_id=92404, language='EN')),
        attributes=[dict(id=a, is_required=True, type='String', dictionary_id=0 if a == 9048 else 1)
                    for a in (85, 8229, 9048)])
    dictionaries = []
    for aid, query, vid, text in [(85, 'No brand', 126745801, 'Нет бренда'), (8229, 'Napkin Holder', 92404, 'Napkin Holder')]:
        dictionaries.append(dict(attribute_id=aid, search=query, values=[dict(id=vid, value=text)],
            receipt=receipt('ozon', '/v1/description-category/attribute/values/search',
                dict(attribute_id=aid, description_category_id=17027907, type_id=92404,
                    language='EN', limit=20, value=query))))
    config = []
    for provider in ('miaoshou', 'ozon'):
        path = _write(root/(provider+'.local.json'),
            {'app_id':'owned-fixture','app_secret':'owned-secret'} if provider=='miaoshou' else
            {'client_id':'12345','api_key':'owned-secret'})
        if provider=='miaoshou' and signing_config is not None:
            path.write_bytes((signing_config.root/'config/miaoshou.local.json').read_bytes())
        config.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            **({'account_id':'12345'} if provider=='ozon' else {})))
    result = {k: _write(root/(k+'.json'), v) for k, v in dict(
        discovery=dict(offer_id=documents['first_review']['offer_id'], config_binding=config),
        tiktok_taxonomy=dict(sites=trees), tiktok_metadata=dict(rows=metadata), ozon_tree=oz_tree,
        ozon_metadata=oz_meta, ozon_dictionaries=dict(rows=dictionaries)).items()}
    path = root/'ledger.jsonl'; path.write_bytes(b''.join(supplement._bytes(r)+b'\n' for r in ledger))
    result['ledger'] = path
    return result


def _completed(live, monkeypatch, tmp_path, *, single_variant_gallery=False):
    import test_native_common_retained_review_graph as original
    import test_round1_sidecar_convergence as sidecar
    import test_common_native_preparation_source as preparation
    from owned_wall_sticker_gallery import wall_sticker_gallery, include_homebloom_target
    include_homebloom_target(live)
    old_images = preparation._image_proposal
    if single_variant_gallery:
        old_source = preparation.complete_source
        def source(value):
            old_source(value)
            path = value['root']/'data/new_product_workbench'/f"{value['offer']}.json"
            state = json.loads(path.read_bytes()); state['review']['category']={'name':'Desktop napkin holder'}
            state['review']['title']='Owned desktop napkin holder'
            path.write_bytes(supplement._bytes(state))
        monkeypatch.setattr(preparation, 'complete_source', source)
        def image_proposal():
            value = old_images()
            state = json.loads((live['root']/'data/new_product_workbench'/f"{live['offer']}.json").read_bytes())
            key = state['review']['selected_sku_keys'][0]
            for brand in value['brand_plans']:
                for index, asset in enumerate(brand['generated_assets']):
                    asset['role'] = ('cover','product_detail','scene_1','scene_2','scene_3','proportion_evidence','construction_evidence')[index]
                    asset['variant_keys']=[key]
                    if index in (5,6):
                        asset['role_substitution']={'replaces':'size' if index==5 else 'instructions',
                            'reason':'SOURCE_FACT_UNKNOWN','evidence_basis':'Owned source-visible construction only.',
                            'unknown_facts_not_asserted':True}
            return value
        monkeypatch.setattr(preparation, '_image_proposal', image_proposal)
    else:
        monkeypatch.setattr(preparation, '_image_proposal', lambda: wall_sticker_gallery(old_images()))
    old = original._propose_market_inputs
    def propose(*args):
        old(*args)
        source = sidecar.candidate
        def candidate(target):
            result = source(target)
            for row in result['target_candidates']:
                if row['target'].startswith('tiktok:'):
                    row['category']['candidate'] = '852872 | Storage Holders & Racks'
                elif row['target'] == 'ozon:RU':
                    row['category']['candidate'] = '17027907 / type 92404 | Napkin Holder'
            return result
        monkeypatch.setattr(sidecar, 'candidate', candidate)
    monkeypatch.setattr(original, '_propose_market_inputs', propose)
    original._propose_market_inputs(live, monkeypatch)
    from test_native_common_technical_execution import _setup
    from test_native_common_service_facts import _installed, _closed_signed_transport
    from modules.products import server
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    config, _ = _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload)
    code, result = server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':plan['plan_id'], 'confirm_miaoshou_write':True})
    assert code==200 and result['native_run']['state']=='CONFIRMED_WRITE', result
    return store, plan, payload, config, result['native_run']['run_id']


def _manifest(store, documents, plan, run_id, refs, root):
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        context = supplement._context(store, db, documents, plan['plan_id'], run_id)
    scripts = []
    for name in ('capture_tree.py','capture_metadata.py','capture_dictionary.py'):
        path = root/name; path.write_text('# Owned original closed read capture producer.\n')
        scripts.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'schema_version':'root-owned-original-category-safe-capture/v1', 'source_binding':context,
        'evidence_files':{k:{'path':str(v),'sha256':hashlib.sha256(v.read_bytes()).hexdigest()} for k,v in refs.items()},
        'capture_scripts':scripts}


def test_sku_category_reuses_snapshot_while_budget_writer_waits(live, monkeypatch, tmp_path):
    """Actual SKU/category consumers, real SQLite PENDING writer, no provider."""
    import socket,sqlite3,threading,time
    from shared_platform import sku_only_publication_correction as correction
    from shared_platform import publication_rounds
    network=[];connect_socket=socket.socket.connect
    def deny(sock,address):
        if address[0] in {'127.0.0.1','localhost','::1'}:return connect_socket(sock,address)
        network.append('connect');pytest.fail('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny)
    store,common,_,_,run_id=_completed(live,monkeypatch,tmp_path)
    documents=bridge.load_r2_documents(common['product_id']);offer=common['product_id']
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        baseline=correction._known_common(store,db,offer_id=offer,plan_id=common['plan_id'],run_id=run_id)
    tasks=tmp_path/'owned-tasks.db';release={'code_version':'owned','environment':'stable','manifest_digest':'owned'}
    scope={'offer_id':offer,'shops':['miaoshou:COMMON'],'skus':['7777']}
    original=tmp_path/'original.txt';original.write_bytes(b'owned immutable original')
    record={'schema_version':correction.SCHEMA,'offer_id':offer,'generation_id':'a'*32,
        'tasks_path':str(tasks),'release_store_path':str(store.path),'task_id':'owned-current',
        'predecessor_task_id':'owned-old','source_key':'owned-source','scope':scope,'release':release,
        'original_files':{str(original):hashlib.sha256(original.read_bytes()).hexdigest()},'common':baseline}
    record['correction_digest']=correction._digest(record)
    reports=publication_rounds.REPORTS_ROOT;base=reports/offer
    (base/'sku-corrections'/record['generation_id']).mkdir(parents=True)
    (base/correction.POINTER).write_bytes(correction._bytes(record))
    with sqlite3.connect(tasks) as db:
        db.executescript('CREATE TABLE workbench_events(task_id,event_type,detail_json);'
            'CREATE TABLE workbench_execution(task_id,scope_json,version_json,state,worker,lease_token,external_started);'
            'CREATE TABLE workbench_tasks(task_id,source_key);')
        db.execute('INSERT INTO workbench_events VALUES(?,?,?)',('owned-current',correction.EVENT,json.dumps(record)))
        db.executemany('INSERT INTO workbench_execution VALUES(?,?,?,?,?,?,?)',[
            ('owned-old','{}','{}','cancelled',None,None,0),
            ('owned-current',json.dumps(scope),json.dumps(release),'reconciliation_required',None,None,0)])
        db.execute('INSERT INTO workbench_tasks VALUES(?,?)',('owned-current','owned-source'))
    with sqlite3.connect(store.path) as db:db.execute('CREATE TABLE owned_writer_receipt(value INTEGER)');db.execute('INSERT INTO owned_writer_receipt VALUES(0)')
    outer=sqlite3.connect(store.path,timeout=2);outer.row_factory=sqlite3.Row
    outer.execute('BEGIN');outer.execute('SELECT * FROM owned_writer_receipt').fetchall()
    waiting=threading.Event();writer_errors=[]
    def write():
        try:
            with sqlite3.connect(store.path,timeout=5) as db:
                db.execute('BEGIN IMMEDIATE');db.execute('UPDATE owned_writer_receipt SET value=1')
                waiting.set();db.commit()
        except BaseException as error:writer_errors.append(error);waiting.set()
    writer=threading.Thread(target=write);writer.start()
    # Observe the real SQLite PENDING lock, rather than relying on a sleep.
    try:
        assert waiting.wait(2)
        deadline=time.monotonic()+2;pending=False
        while time.monotonic()<deadline:
            try:
                with sqlite3.connect(store.path,timeout=0) as probe:probe.execute('SELECT * FROM owned_writer_receipt').fetchall()
            except sqlite3.OperationalError as error:
                assert 'locked' in str(error);pending=True;break
        assert pending and not writer_errors
        # Bound the old nested reader's failure to 150 ms in this owned test.
        connect=store._connect_readonly
        def short_read():
            db=connect();db.execute('PRAGMA busy_timeout=150');return db
        monkeypatch.setattr(store,'_connect_readonly',short_read)
        context={'offer_id':offer}
        manifest={'schema_version':'sku-only-category-capture-derivation/v1',
            'correction_digest':record['correction_digest'],'source_binding':context}
        # After reading the valid retained proof, the independent missing QA
        # binding must still refuse. Before the fix this instead raises BUSY.
        with pytest.raises(ValueError,match='SKU_CORRECTION_BINDING_CHANGED'):
            supplement._checked_receipt(store,outer,context,documents,manifest)
        assert outer.in_transaction and writer.is_alive()
    finally:
        outer.rollback();outer.close();writer.join(6)
    assert not writer.is_alive() and not writer_errors
    with store._connect_readonly() as db:
        assert db.execute('SELECT value FROM owned_writer_receipt').fetchone()[0]==1
    assert correction.verify_generation(store,reports_root=reports,offer_id=offer)==record
    with sqlite3.connect(tmp_path/'foreign.db') as foreign:
        foreign.execute('BEGIN')
        with pytest.raises(ValueError,match='COMMON_SOURCE_DATABASE_MISMATCH'):
            correction.verify_generation(store,reports_root=reports,offer_id=offer,connection=foreign)
    original.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='ORIGINAL_SOURCE_CHANGED'):
        correction.verify_generation(store,reports_root=reports,offer_id=offer)
    assert network==[]


def test_actual_native_common_consumes_supplement_without_rewriting_or_republishing(live, monkeypatch, tmp_path):
    from test_b4b_release_compiler import policy, INCIDENTS
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    store, common, payload, signing_config, run_id = _completed(live, monkeypatch, tmp_path)
    documents = bridge.load_r2_documents(payload['product_id']); before = deepcopy(documents)
    warehouse = resolve_warehouse_decision(offer_id=payload['product_id'], round1=documents['round1_snapshot'],
        pinned=PinnedOzonCredentials('12345','owned-secret','a'*64),
        post_bound=lambda *a, **k: {'warehouses':[dict(warehouse_id=71,status='active',is_kgt=False)]})
    def build():
        return bridge.build_marketplace_stage_payload(documents, store.get_plan(common['plan_id']), store.get_run(run_id),
            policy=policy(40), incidents=INCIDENTS, ozon_stock_decision=warehouse['decision'], category_store=store)
    # The original failure is a display-guidance row with no official receipt.
    with pytest.raises(ValueError, match='MARKETPLACE_CATEGORY_EVIDENCE_REQUIRED'):
        build()
    refs = _evidence(tmp_path/'original-safe-metadata', documents, signing_config=signing_config)
    ozon = refs['discovery'].parent/'ozon.local.json'
    monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_PATH', str(ozon))
    monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_SHA256', hashlib.sha256(ozon.read_bytes()).hexdigest())
    monkeypatch.setenv('ORBIT_OZON_EXPECTED_ACCOUNT_ID', '12345')
    manifest = _manifest(store, documents, common, run_id, refs, tmp_path/'original-safe-metadata')
    db_before = store.path.read_bytes()
    result = supplement.prepare_technical_category_supplement(store, documents=documents,
        common_plan_id=common['plan_id'], common_run_id=run_id, capture_manifest=manifest)
    assert result['execution_authority'] is False
    market = build()  # GREEN requires the actual R3 consumer, not a validator-only success.
    assert store.path.read_bytes() == db_before and documents == before
    assert bridge.load_r2_documents(payload['product_id']) == before
    proofs = market['r3_marketplace_binding']['technical_category_metadata']
    assert set(proofs) == {r['target'] for r in documents['first_review']['targets']
                          if r['target'].startswith('tiktok:') or r['target'] == 'ozon:RU'}
    assert market['product_facts']['categories_by_target']['ozon:RU']['category']['type_id'] == '92404'
    assert [r['attribute_id'] for r in proofs['ozon:RU']['required_attributes']] == [85, 8229, 9048]
    assert proofs['ozon:RU']['required_attributes'][-1]['manufacturer_model_claim'] is False
    snapshot = build_publication_preview(market)
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        evidence = bridge.marketplace_quality_evidence(snapshot, market, category_store=store, category_connection=db)
    assert evidence['publication_bridge']['target_facts']['tiktok:LH_PH']['category']['evidence_digest'] == result['receipt_digest']
    category = evidence['publication_bridge']['target_facts']['tiktok:LH_PH']['category']
    assert category['status'] == 'TECHNICAL_EVIDENCE_BOUND' and 'not human approval' in category['note']
    from shared_platform import publication_autopilot
    shown = publication_autopilot.compile_release_preview(snapshot, policy=policy(40),
        incident_registry=INCIDENTS, durable_evidence=evidence)['review_manifest']
    ozon_shown = next(r for r in shown['targets'] if r['target_label']=='ozon:RU')['category']
    assert [r['attribute_id'] for r in ozon_shown['technical_attribute_candidates']] == [85,8229,9048]
    assert ozon_shown['final_marketplace_approval'] is False and '不是厂家型号' in ozon_shown['note_zh']
    # Re-reading is idempotent and the original COMMON run/attempt and R1 remain unchanged.
    assert supplement.prepare_technical_category_supplement(store, documents=documents,
        common_plan_id=common['plan_id'], common_run_id=run_id, capture_manifest=manifest) == result
    changed = deepcopy(market); changed['r3_marketplace_binding']['technical_category_metadata']['ozon:RU']['category']['type_id']='92011'
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(ValueError, match='MARKETPLACE_TECHNICAL_CATEGORY_PROJECTION_CHANGED'):
            bridge.marketplace_quality_evidence(snapshot, changed, category_store=store, category_connection=db)
    changed = deepcopy(market); changed['r3_marketplace_binding'].pop('technical_category_metadata')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(ValueError, match='MARKETPLACE_TECHNICAL_CATEGORY_SCOPE_CHANGED'):
            bridge.marketplace_quality_evidence(snapshot, changed, category_store=store, category_connection=db)
    changed_documents = deepcopy(documents)
    next(r for r in changed_documents['first_review']['targets'] if r['target']=='ozon:RU')['category']['candidate']='17027905 / type92011 | Toilet Paper Holder'
    with pytest.raises(ValueError, match='R2_ROUND1_IDENTITY_CONFLICT'):
        supplement.prepare_technical_category_supplement(store, documents=changed_documents,
            common_plan_id=common['plan_id'], common_run_id=run_id, capture_manifest=manifest)
    bad = deepcopy(manifest); bad['source_binding']['account_identity_digest']='sha256:'+'f'*64
    with pytest.raises(ValueError, match='ROOT_ADMITTED_CAPTURE_MANIFEST_REQUIRED'):
        supplement.prepare_technical_category_supplement(store, documents=documents,
            common_plan_id=common['plan_id'], common_run_id=run_id, capture_manifest=bad)
    raw = refs['tiktok_metadata'].read_bytes(); refs['tiktok_metadata'].write_bytes(raw+b' ')
    with pytest.raises(ValueError, match='ORIGINAL_FILE_CHANGED'):
        build()
    assert store.path.read_bytes() == db_before


@pytest.mark.parametrize('damage', ['shop', 'required', 'tree', 'request'])
def test_original_metadata_cannot_authorize_a_different_category_or_skip_required_fields(tmp_path, damage):
    docs = {'first_review': {'offer_id':'987654321', 'product_facts':{'seller_sku':'1234',
        'skus':[dict(seller_sku='1234',package_cm=['15','13','10'])]}, 'targets':[
            dict(target='tiktok:GB', category=dict(status='DECISION_REQUIRED', candidate='852872 | Storage Holders & Racks', evidence_digest='sha256:'+'a'*64)),
            dict(target='ozon:RU', category=dict(status='DECISION_REQUIRED', candidate='17027907 / type92404 | Napkin Holder', evidence_digest='sha256:'+'b'*64))]}}
    paths = _evidence(tmp_path, docs)
    evidence = {k: ([json.loads(r) for r in v.read_bytes().splitlines()] if k == 'ledger'
                    else json.loads(v.read_bytes())) for k,v in paths.items()}
    rows = supplement._targets(docs, evidence)
    assert rows['tiktok:GB']['required_attributes'][0]['value_id']=='1000256'
    assert rows['tiktok:GB']['frozen_package_dimensions'][0]['package_cm']==['15','13','10']
    if damage=='shop': evidence['tiktok_metadata']['rows'][0]['shop_id']='9999'
    elif damage=='required': evidence['tiktok_metadata']['rows'][0]['category_metadata']['categoryProductAttrList'][0]['values'].append(dict(id='2',name='2'))
    elif damage=='tree': evidence['tiktok_taxonomy']['sites'][0]['nodes'][0]['node']['disabled']=True
    else: evidence['ledger'][0]['body']['cid']=853256
    with pytest.raises(ValueError, match='R3_TECHNICAL_CATEGORY_'):
        supplement._targets(docs, evidence)
