"""Exact B regional continuation over the already accepted Global and PH item.

No new shared mutation or budget, no PH create, no generic UNKNOWN retry.
Only the original decision and a distinct signed source-purpose admit this path.
"""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import re
import sqlite3

from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold, _json, _sha
from shared_platform.native_known_rejected_save_repair import _read_reference

EVENT = 'source_code_approved_native_shopee_regional_and_tiktok_saved_publish_continuation'
PLAN = 'r3-marketplace:8412287877065586543:07707b4b7901ca9b97b08acd'
RUN = 'release-run:25e66f7250d31cbb39ff8d0e'
DECISION = 'native-final-decision:7a10d3ac157c55c6949b663c5b63194b2c43eab81b0fee88eda6aae412838fe2'
SNAPSHOT = 'sha256:8eecd1c0b7beadcaddc7a8655f25b5f9e981729c4cb44e33dbd5aac17eaaa3de'
CANDIDATE = 'c5eacc9725ecefa012af02c6d4f5130c6098c9bedab65e2159357a708c73a5cc'
GLOBAL = '43135542690'
MODELS = {'2000431': '331671941960', '2000432': '331671941961', '2000433': '331671941962'}
PH_ITEM = '47970054181'
PH_MODELS = {'2000431': '316671959761', '2000432': '316671959762', '2000433': '316671959763'}
BEFORE = {'shopee:PH': 3, 'shopee:MY': 3, 'shopee:TH': 3, 'shopee:VN': 2}
NOT_SENT = ('shopee:MY', 'shopee:TH', 'shopee:VN')
LIST_EVENT = 'source_code_approved_native_ozon_two_card_stock_and_shopee_frozen_completion_continuation'
LIST_BEFORE = {'shopee:MY':4,'shopee:PH':4,'shopee:TH':4,'shopee:VN':3}
LIST_ITEMS = {'shopee:MY':'44920064243','shopee:PH':PH_ITEM,'shopee:TH':'53420052020','shopee:VN':'52420043241'}
LIST_SHOPS = {'shopee:MY':1561117812,'shopee:PH':1527371343,'shopee:TH':1561124013,'shopee:VN':1723948773}
LIST_MODELS = {'shopee:PH':PH_MODELS,
    'shopee:MY':{'2000431':'416672688860','2000432':'416672688861','2000433':'416672688862'},
    'shopee:TH':{'2000431':'391672782771','2000432':'391672782772','2000433':'391672782773'},
    'shopee:VN':{'2000431':'298054224007','2000432':'298054224008','2000433':'298054224009'}}
COMPLETION_MAXIMA = {label:{**({'update_regional_copy':1} if label!='shopee:PH' else {}),
    'update_description_media':1,'list_regional_item':1} for label in LIST_BEFORE}


def _completion_wire(snapshot,label,operation,image_ids):
    from modules.shopee.skill_regions import _approved_copy
    title,description=_approved_copy(snapshot,label)
    need(operation in COMPLETION_MAXIMA[label],'EXACT_FROZEN_COMPLETION_OPERATION_REQUIRED')
    value=dict(item_id=LIST_ITEMS[label])
    if operation=='update_regional_copy':value.update(title=title,description=description)
    if operation=='update_description_media':value.update(description=description,image_ids=tuple(image_ids))
    return value


def _completion_parcel(snapshot,item):
    from modules.shopee.skill_regions import _parcel_envelope
    weight,dimensions=_parcel_envelope(snapshot);parcel=item.get('dimension') or {}
    need(Decimal(str(item.get('weight')))==Decimal(str(weight))
        and [Decimal(str(parcel.get(k))) for k in ('package_length','package_width','package_height')]==
            [Decimal(str(v)) for v in dimensions],'FROZEN_COMPLETION_PARCEL_CHANGED')


def _completion_models(snapshot,label,models,original):
    from modules.shopee.skill_regions import _approved_models,_regional_model_price
    approved={r['model_sku']:r for r in _approved_models(snapshot,label)}
    def stock(row):
        values=(row.get('stock_info_v2') or {}).get('seller_stock') or []
        mapping={r.get('location_id'):r.get('stock') for r in values}
        need(len(mapping)==len(values) and mapping.get('CNZ')==200 and type(mapping.get('CNZ')) is int
            and all(type(k) is str and k and type(v) is int and v==(200 if k=='CNZ' else 0) for k,v in mapping.items()),'FROZEN_COMPLETION_STOCK_UNVERIFIED')
        return mapping
    expected={r['model_sku']:stock(r) for r in original}
    need(len(models)==3 and {str(r.get('model_sku')):str(r.get('model_id')) for r in models}==LIST_MODELS[label], 'EXACT_COMPLETION_MODELS_CHANGED')
    for row in models:
        model=row['model_sku'];facts=approved[model]
        need(_regional_model_price(row,facts['currency'])==Decimal(facts['price']) and stock(row)==expected[model], 'FROZEN_COMPLETION_PRICE_OR_STOCK_CHANGED')
PINS = {
    'retained_ref': '1f2546a5a29637fb33f93452d18475f18987812ea4425aa87bcf3c1c113169e9',
    'snapshot_ref': '3e41486796ec38f8f59fa73655abf1bc3deb1df27d62c5222e38baa5d4d02fb1',
    'global_official_ref': 'dccc4f1abda344958e581f360fbf97b567c549c531e9a9f7928abbd08ceaaf12',
    'ph_official_ref': 'd3d63fce306e902d1684a35cf4116dc40f713aab4a1567dea5859dbb192edbfb',
    'checkpoint_ref': '4e1492c4039a069e252d345bc2a03659a63f616ffa6339511af1381e678b9ce0'}


def need(value, code):
    if not value:raise NativeMutationBudgetHold('SHOPEE_REGIONAL_CONTINUATION_' + code)


def _ref(ref):
    need(type(ref) is dict and set(ref) == {'path', 'sha256'}, 'EXACT_FILE_PIN_REQUIRED')
    return _read_reference(ref)


def _joined(image):
    scopes = {r['scope_key']: r for r in image['native_final_budget_scopes']}
    dispatched = {(r['scope_key'], r['sequence']): r for r in image['native_final_budget_dispatches']}
    outcomes = {(r['scope_key'], r['sequence']): r for r in image['native_final_budget_outcomes']}
    return [{**r, 'request_digest': dispatched.get((r['scope_key'], r['sequence']), {}).get('request_digest'),
        'outcome': outcomes.get((r['scope_key'], r['sequence']), {}).get('outcome')}
        for r in sorted(image['native_final_budget_reservations'], key=lambda row: row['sequence'])
        if scopes[r['scope_key']]['platform'] == 'SHOPEE']


def read_regional_proof(ref):
    """Pure typed authority/pin reader; no configuration, API, store or intake."""
    doc = json.loads(_ref(ref))
    expected = dict(schema='native-shopee-existing-global-regional-continuation-technical-authority/v1',
        status='ROOT_TECHNICAL_EXECUTION_APPROVED', task_id='TASK-20261023-001', offer_id='8412287877065586543',
        plan_id=PLAN, run_id=RUN, decision_id=DECISION, snapshot_digest=SNAPSHOT, candidate_digest=CANDIDATE,
        target_labels=list(BEFORE), regional_not_sent_labels=list(NOT_SENT), before_attempts=BEFORE,
        global_item_id=GLOBAL, global_model_ids=MODELS, ph_item_id=PH_ITEM, ph_shop_id=1527371343,
        ph_model_ids=PH_MODELS, merchant_id=4970102, shared_maximum=10, per_target_maximum=9,
        new_shared_writes=0, new_PH_create=False, new_budget=False, new_human_review=False)
    need(all(type(doc.get(k)) is type(v) and doc[k] == v for k, v in expected.items())
        and re.fullmatch('[0-9a-f]{40}', str(doc.get('base_source_head') or '')), 'EXACT_TECHNICAL_AUTHORITY_REQUIRED')
    values = {}
    for key, digest in PINS.items():
        need(type(doc.get(key)) is dict and doc[key].get('sha256') == digest, 'ORIGINAL_EVIDENCE_PIN_CHANGED')
        values[key] = json.loads(_ref(doc[key]))
    image = values['retained_ref'];rows = _joined(image)
    need(len(rows) == 11 and all(r['outcome'] == 'ACCEPTED' and r['request_digest'] and r['attempt_count'] == 1 for r in rows),
        'ORIGINAL_ELEVEN_ACCEPTED_MUTATIONS_REQUIRED')
    shared = [r for r in rows if r['budget_scope'] == 'shared'];regional = [r for r in rows if r['budget_scope'] == 'target']
    need(len(shared) == 10 and [r['operation'] for r in shared] == ['upload_image'] * 8 + ['create_global_item', 'initialize_models']
        and len(regional) == 1 and regional[0]['target_label'] == 'shopee:PH'
        and regional[0]['operation'] == 'create_publish_task', 'EXACT_SHARED10_PH1_REQUIRED')
    targets = {r['target_label']: r for r in image['release_target_runs'] if r['target_label'] in BEFORE}
    need(set(targets) == set(BEFORE) and all(targets[k]['attempts'] == n
        and targets[k]['status'] == ('PENDING' if k == 'shopee:VN' else 'FAILED') for k, n in BEFORE.items()),
        'ORIGINAL_REGIONAL_TARGETS_REQUIRED')
    observed = image['official_SH_PH_observation']
    need(observed['global_item_id'] == GLOBAL and observed['item_id'] == PH_ITEM and observed['shop_id'] == 1527371343
        and observed['item_status'] == 'UNLIST' and observed['model_ids'] == PH_MODELS
        and observed['observed_per_model_stock'] == 200, 'ORIGINAL_OFFICIAL_PH_IDENTITY_REQUIRED')
    global_facts = values['global_official_ref']
    need(global_facts.get('global_item_id') == GLOBAL and str(global_facts.get('merchant_id')) == '4970102'
        and {str(m['global_model_sku']): str(m['global_model_id']) for r in global_facts['responses']
            for m in r.get('models') or []} == MODELS, 'ORIGINAL_OFFICIAL_GLOBAL_MODELS_REQUIRED')
    need(values['snapshot_ref']['snapshot_digest'] == SNAPSHOT, 'ORIGINAL_SNAPSHOT_REQUIRED')
    for label in NOT_SENT:
        # All regional writes in this pinned path reserve for that exact target
        # and commit their dispatch row before the canonical transport is called.
        need(not any(r['target_label'] == label and r['budget_scope'] == 'target' for r in rows), 'REGIONAL_NOT_SENT_PROOF_REQUIRED')
    return doc


def _material(ref, doc):
    return dict(ref=ref, doc=doc, digest=_sha(_json(doc)), image=json.loads(_ref(doc['retained_ref'])),
        snapshot=json.loads(_ref(doc['snapshot_ref'])))


def read_existing_item_completion_proof(ref):
    """Pure exact four-item proof; does not install authority or change a row."""
    doc=json.loads(_ref(ref))
    expected=dict(schema='native-shopee-four-existing-item-frozen-completion-technical-authority/v1',status='ROOT_TECHNICAL_EXECUTION_APPROVED',
        task_id='TASK-20261023-001',offer_id='8412287877065586543',plan_id=PLAN,run_id=RUN,decision_id=DECISION,
        candidate_digest=CANDIDATE,snapshot_digest=SNAPSHOT,base_source_head='8734a3a957811d4352f081cb3d82b741a6e67c45',
        target_labels=list(LIST_BEFORE),before_attempts=LIST_BEFORE,global_item_id=GLOBAL,merchant_id=4970102,
        item_ids=LIST_ITEMS,shop_ids=LIST_SHOPS,model_ids=LIST_MODELS,shared_maximum=10,per_target_maximum=9,
        listing_maximum_per_existing_item=1,new_shared_writes=0,new_create=False,new_upload=False,new_copy=False,new_budget=False,new_human_review=False,
        operation_maxima=COMPLETION_MAXIMA,restore_original_frozen_copy=True,restore_original_eight_description_images=True)
    need(all(type(doc.get(k)) is type(v) and doc[k]==v for k,v in expected.items()),'EXACT_EXISTING_ITEM_LIST_PROOF_REQUIRED')
    image=json.loads(_ref(doc['retained_ref']));rows=_joined(image)
    need(image.get('source_head')==doc['base_source_head'] and image['run']['run_id']==RUN
        and image['run']['plan_id']==PLAN and len(rows)==14 and all(r['outcome']=='ACCEPTED' and r['request_digest'] for r in rows), 'EXACT_FOUR_ACCEPTED_CREATES_REQUIRED')
    need([r['operation'] for r in rows[:10]]==['upload_image']*8+['create_global_item','initialize_models']
        and all(r['budget_scope']=='shared' and r['attempt_count']==1 for r in rows[:10])
        and {r['target_label'] for r in rows[10:]}==set(LIST_BEFORE)
        and all(r['budget_scope']=='target' and r['operation']=='create_publish_task' and r['attempt_count']==1 for r in rows[10:]), 'EXACT_SHARED10_CREATE4_NO_LIST_REQUIRED')
    targets={r['target_label']:r for r in image['release_target_runs']}
    failures=image['release_target_failure_events']
    for label,attempt in LIST_BEFORE.items():
        need((targets[label]['run_id'],targets[label]['status'],targets[label]['attempts'])==(RUN,'FAILED',attempt), 'ORIGINAL_EXISTING_ITEM_FAILED_ATTEMPT_REQUIRED')
        own=[r for r in failures if r['target_label']==label]
        need(own and own[-1]['attempt']==attempt and all(_sha(r['evidence_json'])==r['evidence_digest'] for r in own), 'ORIGINAL_EXISTING_ITEM_FAILURE_REQUIRED')
    scope=next(r for r in image['native_final_budget_scopes'] if r['platform']=='SHOPEE');binding=json.loads(scope['binding_json'])
    need(_sha(scope['binding_json'])==scope['binding_sha256'] and binding['shared_maximum']==10 and binding['per_target_maximum']==9
        and binding['decision_id']==DECISION and binding['snapshot_digest']==SNAPSHOT, 'EXACT_ORIGINAL_LIST_BUDGET_REQUIRED')
    association=json.loads(_ref(doc['official_association_ref']))
    query=json.loads(_ref(doc['official_association_query_ref']));observations=query.get('responses') or []
    need(query.get('global_item_id')==GLOBAL and query.get('merchant_id')==4970102 and len(observations)==1
        and observations[0].get('endpoint')=='/api/v2/global_product/get_published_list'
        and observations[0].get('params')==dict(global_item_id=int(GLOBAL))
        and observations[0].get('sha256')==doc['official_association_ref']['sha256'], 'EXACT_GLOBAL_ASSOCIATION_QUERY_REQUIRED')
    published=(association.get('response') or {}).get('published_item') or []
    need(not association.get('error') and len(published)==4
        and {r['shop_region']:(str(r['item_id']),r['shop_id']) for r in published}==
            {k.split(':')[1]:(LIST_ITEMS[k],LIST_SHOPS[k]) for k in LIST_BEFORE}, 'EXACT_OFFICIAL_FOUR_GLOBAL_RELATIONS_REQUIRED')
    need(set(doc.get('official_item_refs') or {})==set(LIST_BEFORE),'EXACT_OFFICIAL_FOUR_ITEM_REFS_REQUIRED')
    for label,refs in doc['official_item_refs'].items():
        item=json.loads(_ref(refs['item_ref']));models=json.loads(_ref(refs['models_ref']))
        items=(item.get('response') or {}).get('item_list') or [];model_rows=(models.get('response') or {}).get('model') or []
        need(not item.get('error') and not models.get('error') and len(items)==1 and str(items[0].get('item_id'))==LIST_ITEMS[label]
            and items[0].get('item_status') in {'UNLIST','NORMAL'} and len(model_rows)==3
            and {str(r.get('model_sku')):str(r.get('model_id')) for r in model_rows}==LIST_MODELS[label], 'EXACT_OFFICIAL_EXISTING_ITEM_MODELS_REQUIRED')
    need(doc.get('snapshot_ref',{}).get('sha256')==PINS['snapshot_ref'],'ORIGINAL_COMPLETION_SNAPSHOT_REQUIRED')
    snapshot=json.loads(_ref(doc['snapshot_ref']))
    from domains.product_operations import publication_images_for_target
    bindings=image['shopee_checkpoint']['image_bindings']
    for label in LIST_BEFORE:
        ids=[bindings[url] for url in publication_images_for_target(snapshot,label)]
        item=json.loads(_ref(doc['official_item_refs'][label]['item_ref']))['response']['item_list'][0]
        models=json.loads(_ref(doc['official_item_refs'][label]['models_ref']))['response']['model']
        _completion_parcel(snapshot,item);_completion_models(snapshot,label,models,models)
        need(len(ids)==len(set(ids))==8 and doc.get('approved_image_ids',{}).get(label)==ids
            and item['image']['image_id_list']==ids,'ORIGINAL_UPLOADED_EIGHT_IMAGE_ORDER_REQUIRED')
    expected_payloads={label:{operation:json.loads(_json(_completion_wire(snapshot,label,operation,doc['approved_image_ids'][label])))
        for operation in COMPLETION_MAXIMA[label]} for label in LIST_BEFORE}
    need(doc.get('expected_request_payloads')==expected_payloads,'EXACT_FROZEN_COMPLETION_REQUESTS_REQUIRED')
    need(doc.get('projection_gaps_ref',{}).get('sha256')=='1520c80410f9f471c0d92793b1610f84000dcd83f8a302dcc7cbfceeb9e5b014', 'EXACT_CAPTURED_PROJECTION_GAPS_REQUIRED')
    gaps=json.loads(_ref(doc['projection_gaps_ref']))
    need({r['target_label']:r['item_id'] for r in gaps['rows']}==LIST_ITEMS,'EXACT_CAPTURED_COMPLETION_SCOPE_REQUIRED')
    return doc


def _list_material(ref,doc):
    return dict(ref=ref,doc=doc,digest=_sha(_json(doc)),image=json.loads(_ref(doc['retained_ref'])))


def _list_event(bound,task):
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',(task['task_id'],LIST_EVENT)).fetchall()
    if not rows:return None
    need(len(rows)==1,'ONE_EXISTING_ITEM_LIST_SOURCE_REQUIRED')
    envelope=json.loads(rows[0][0]);raw=envelope['record_json']
    need(_sha(raw)==envelope['record_digest'],'EXISTING_ITEM_LIST_ENVELOPE_CHANGED')
    record=json.loads(raw);ref=record['shopee_existing_item_completion_ref'];doc=read_existing_item_completion_proof(ref)
    need(record['shopee_existing_item_completion_digest']==_sha(_json(doc)),'EXISTING_ITEM_LIST_SIGNED_PROOF_CHANGED')
    return _list_material(ref,doc)


def _event(bound, task):
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?', (task['task_id'], EVENT)).fetchall()
    need(len(rows) == 1, 'NEW_SIGNED_SOURCE_PURPOSE_REQUIRED')
    envelope = json.loads(rows[0][0]);raw = envelope.get('record_json')
    need(type(raw) is str and _sha(raw) == envelope.get('record_digest'), 'SIGNED_ENVELOPE_REQUIRED')
    record = json.loads(raw);ref = record.get('shopee_regional_continuation_ref');doc = read_regional_proof(ref)
    need(record.get('shopee_regional_continuation_digest') == _sha(_json(doc)), 'SIGNED_PROOF_CHANGED')
    return _material(ref, doc)


def _context(active):
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    from shared_platform.agent_prepared_v4_targets import _scope
    need(type(active) is _NativeExecution and _ACTIVE.get() is active and active.plan_id == PLAN
        and active.decision_id == DECISION and active.service.store is active.store, 'PROTECTED_ORIGINAL_DECISION_REQUIRED')
    need(active.service._actor()['owner_sid'] == active.owner_sid, 'CURRENT_ORIGINAL_ACTOR_REQUIRED')
    bound, task, _ = _scope(active, active.store.get_plan(PLAN))  # genuine complete current HMAC/source first
    ctx = _event(bound, task);ctx['active'] = active;ctx['list_only']=_list_event(bound,task)
    return ctx


def _facts(db, ctx):
    row = db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?', (PLAN,)).fetchone()
    need(row and _sha(row[0]) == PINS['snapshot_ref'], 'ORIGINAL_APPROVED_SNAPSHOT_CHANGED')
    scope = db.execute("SELECT * FROM native_final_budget_scopes WHERE plan_id=? AND snapshot_digest=? AND platform='SHOPEE'", (PLAN, SNAPSHOT)).fetchone()
    old_scope = next(r for r in ctx['image']['native_final_budget_scopes'] if r['platform'] == 'SHOPEE')
    need(scope and dict(scope) == old_scope, 'ORIGINAL_BUDGET_BINDING_CHANGED')
    binding = json.loads(scope['binding_json'])
    need((binding['target_labels'], binding['shared_maximum'], binding['per_target_maximum'], binding['decision_id'])
        == (list(BEFORE), 10, 9, DECISION), 'ORIGINAL_CAP10_9_REQUIRED')
    rows = [dict(r) for r in db.execute('SELECT r.*,d.request_digest,o.outcome FROM native_final_budget_reservations r '
        'LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence) LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence) '
        'WHERE r.scope_key=? ORDER BY r.sequence', (scope['scope_key'],))]
    old = _joined(ctx['image'])
    need(rows[:len(old)] == old and all(r['budget_scope'] == 'target' and r['target_label'] in BEFORE for r in rows[len(old):]),
        'ORIGINAL_ACCEPTED_PREFIX_OR_SHARED10_CHANGED')
    for original in ctx['image']['release_target_failure_events']:
        if original['target_label'] not in BEFORE:continue
        row = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=?',
            (RUN, original['target_label'], original['attempt'])).fetchone()
        need(row and dict(row) == original, 'ORIGINAL_FAILURE_HISTORY_CHANGED')
    return rows


def _list_facts(db,ctx):
    rows=_facts(db,ctx);image=ctx['list_only']['image'];old=_joined(image)
    need(rows[:len(old)]==old and all(r['operation'] in COMPLETION_MAXIMA.get(r['target_label'],{}) and r['budget_scope']=='target'
        and r['target_label'] in LIST_BEFORE and r['attempt_count']==1 for r in rows[len(old):]), 'EXISTING_ITEM_LIST_ONLY_NO_RECREATE')
    need(all(sum(r['target_label']==label and r['operation']==operation for r in rows[len(old):])<=maximum
        for label,maxima in COMPLETION_MAXIMA.items() for operation,maximum in maxima.items()),'ONE_EXISTING_ITEM_OPERATION_MAXIMUM')
    for prior in image['release_target_failure_events']:
        if prior['target_label'] not in LIST_BEFORE:continue
        row=db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=?',
            (RUN,prior['target_label'],prior['attempt'])).fetchone()
        need(row and dict(row)==prior,'EXISTING_ITEM_ORIGINAL_FAILURE_CHANGED')
    return rows


def activate_failed_targets(active):
    if getattr(active, 'plan_id', None) != PLAN:return ()
    ctx = _context(active);labels = []
    before_map=LIST_BEFORE if ctx.get('list_only') else BEFORE
    with active.store._transaction() as db:
        active.service._decision(db, DECISION, owner_sid=active.owner_sid);active.store._require_active_run(db, RUN)
        rows = _list_facts(db,ctx) if ctx.get('list_only') else _facts(db, ctx)
        old = _joined(ctx['list_only']['image'] if ctx.get('list_only') else ctx['image'])
        for label, before in before_map.items():
            row = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, label)).fetchone()
            if row['status'] not in {'FAILED', 'PENDING'} or row['attempts'] != before:continue
            own = [r for r in rows if r['budget_scope'] == 'target' and r['target_label'] == label]
            need(own == [r for r in old if r['budget_scope'] == 'target' and r['target_label'] == label], 'REGIONAL_ALREADY_ATTEMPTED_OR_UNKNOWN')
            need(not db.execute('SELECT 1 FROM release_target_submissions WHERE run_id=? AND target_label=?', (RUN, label)).fetchone()
                and not db.execute('SELECT 1 FROM release_target_readbacks WHERE run_id=? AND target_label=?', (RUN, label)).fetchone(), 'ORIGINAL_FAILED_TARGET_LINEAGE_CHANGED')
            if row['status'] == 'FAILED':
                changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=?",
                    (RUN, label, before))
                need(changed.rowcount == 1, 'ORIGINAL_TARGET_CAS_CHANGED')
            labels.append(label)
    return tuple(labels)


class _RegionalLedger:
    def __init__(self, ctx, base):self.ctx, self.base = ctx, base
    def __getattr__(self, key):return getattr(self.base, key)
    def _check(self, db=None):
        self.base._recheck(db)
        if db is None:
            current_ctx=_context(self.ctx['active'])
            need(current_ctx['digest'] == self.ctx['digest'] and (not self.ctx.get('list_only') or
                (current_ctx.get('list_only') or {}).get('digest')==self.ctx['list_only']['digest']), 'CURRENT_SOURCE_OR_PROOF_CHANGED')
            with self.base._connect(readonly=True) as current:(_list_facts if self.ctx.get('list_only') else _facts)(current,self.ctx)
        else:(_list_facts if self.ctx.get('list_only') else _facts)(db,self.ctx)
    def reserve_shared(self, *args, **kwargs):need(False, 'EXISTING_GLOBAL_SHARED_WRITE_FORBIDDEN')
    def reserve_target(self, label, operation, *, count=1):
        self._check()
        if self.ctx.get('list_only'):
            need(label==self.base.target_label and operation in COMPLETION_MAXIMA.get(label,{}) and type(count) is int and count==1,'EXISTING_ITEM_ONLY_FROZEN_COMPLETION_ALLOWED')
            with self.base._connect(readonly=True) as db:
                rows=_list_facts(db,self.ctx);old=_joined(self.ctx['list_only']['image'])
                need(not any(r['target_label']==label and r['operation']==operation for r in rows[len(old):]),'EXISTING_ITEM_OPERATION_ALREADY_RESERVED_NO_RETRY')
            return self.base.reserve_target(label,operation,count=count)
        need(label == self.base.target_label and type(count) is int and count == 1
            and operation in {'create_publish_task', 'list_regional_item', 'update_regional_copy', 'update_description_media'}, 'EXACT_REGIONAL_OPERATION_REQUIRED')
        need(label != 'shopee:PH' or operation != 'create_publish_task', 'EXISTING_PH_TASK_NO_RECREATE')
        return self.base.reserve_target(label, operation, count=count)
    def dispatch_reserved(self, operation, call, *args, **kwargs):
        self._check()
        if self.ctx.get('list_only'):
            label=self.base.target_label
            need(operation in COMPLETION_MAXIMA[label] and len(args)==1 and type(args[0]) is dict and not kwargs,'EXACT_FROZEN_COMPLETION_PAYLOAD_REQUIRED')
            expected=_completion_wire(self.ctx['snapshot'],label,operation,self.ctx['list_only']['doc']['approved_image_ids'][label])
            need(args[0]==expected,'EXACT_FROZEN_COMPLETION_PAYLOAD_REQUIRED')
            return self.base.dispatch_reserved(operation,call,*args,**kwargs)
        need(operation in {'create_publish_task', 'list_regional_item', 'update_regional_copy', 'update_description_media'}
            and (self.base.target_label != 'shopee:PH' or operation != 'create_publish_task'), 'SHARED_OR_PH_RECREATE_FORBIDDEN')
        if self.base.target_label == 'shopee:PH':
            need(len(args) == 1 and type(args[0]) is dict and str(args[0].get('item_id')) == PH_ITEM, 'EXACT_EXISTING_PH_ITEM_REQUIRED')
        return self.base.dispatch_reserved(operation, call, *args, **kwargs)
    def complete_reserved(self, operation, outcome):self._check();return self.base.complete_reserved(operation, outcome)


def for_active(active, base):
    if getattr(active, 'plan_id', None) != PLAN or base.platform != 'SHOPEE' or base.target_label not in BEFORE:return base
    ctx = _context(active)
    with base._connect(readonly=True) as db:
        (_list_facts if ctx.get('list_only') else _facts)(db,ctx)
        row = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, base.target_label)).fetchone()
        before=LIST_BEFORE if ctx.get('list_only') else BEFORE
        need(row and tuple(row) == ('RUNNING', before[base.target_label] + 1), 'EXACT_REGIONAL_CONTINUATION_BEGIN_REQUIRED')
    return _RegionalLedger(ctx, base)


def bind_dependencies(deps, ledger):
    if type(ledger) is not _RegionalLedger:return deps
    resolver, runtime = deps.global_item_id_resolver, deps.runtime
    if ledger.ctx.get('list_only'):
        # The formal copy adapter rereads the item internally. Validate that
        # very READ too, so a changed gallery cannot enter its actual SDK body.
        original_item=runtime.regional_item;label=ledger.base.target_label
        expected_ids=ledger.ctx['list_only']['doc']['approved_image_ids'][label]
        original_models=runtime.regional_models
        model_ref=ledger.ctx['list_only']['doc']['official_item_refs'][label]['models_ref']
        frozen_models=json.loads(_ref(model_ref))['response']['model']
        def exact_item(context,item_id):
            need('shopee:'+context.region==label and context.shop_id==LIST_SHOPS[label] and context.merchant_id==4970102
                and item_id==LIST_ITEMS[label],'EXACT_COMPLETION_READ_IDENTITY_REQUIRED')
            row=original_item(context,item_id)
            need(row and str(row.get('item_id'))==item_id and row.get('image',{}).get('image_id_list')==expected_ids,'FROZEN_COMPLETION_CURRENT_GALLERY_CHANGED')
            _completion_parcel(ledger.ctx['snapshot'],row)
            return row
        runtime.regional_item=exact_item
        def exact_models(context,item_id):
            need('shopee:'+context.region==label and context.shop_id==LIST_SHOPS[label] and context.merchant_id==4970102
                and item_id==LIST_ITEMS[label],'EXACT_COMPLETION_READ_IDENTITY_REQUIRED')
            rows=original_models(context,item_id)
            _completion_models(ledger.ctx['snapshot'],label,rows,frozen_models)
            return rows
        runtime.regional_models=exact_models
    class ExistingGlobal:
        def __call__(self, request):
            ledger._check();identity = resolver(request)
            need(str(identity) == GLOBAL and resolver.write_count(request) == 0, 'EXACT_EXISTING_VERIFIED_GLOBAL_REQUIRED')
            return GLOBAL
        def write_count(self, request):return resolver.write_count(request)
        def created_in_run(self, *args):return False
    class Regional:
        def __getattr__(self, key):return getattr(runtime, key)
        def _context(self, context):
            need('shopee:' + context.region == ledger.base.target_label and context.merchant_id == 4970102, 'EXACT_REGIONAL_ACCOUNT_REQUIRED')
            if context.region == 'PH':need(context.shop_id == 1527371343, 'EXACT_PH_SHOP_REQUIRED')
            if ledger.ctx.get('list_only'):need(context.shop_id==LIST_SHOPS[ledger.base.target_label],'EXACT_EXISTING_ITEM_SHOP_REQUIRED')
        def existing_regional_item(self, context, global_id):
            self._context(context);need(str(global_id) == GLOBAL, 'EXACT_EXISTING_GLOBAL_REQUIRED')
            known = runtime.existing_regional_item(context, global_id)
            if ledger.ctx.get('list_only'):
                label=ledger.base.target_label;item_id=LIST_ITEMS[label]
                need(known in (None,item_id) and str(runtime.resolved_global_item_id(context,item_id))==GLOBAL,'EXACT_EXISTING_ITEM_GLOBAL_LINK_REQUIRED')
                models=runtime.regional_models(context,item_id)
                need(len(models)==3 and {str(r.get('model_sku')):str(r.get('model_id')) for r in models}==LIST_MODELS[label],'EXACT_EXISTING_ITEM_OFFICIAL_MODELS_REQUIRED')
                item=runtime.regional_item(context,item_id)
                need(item and str(item.get('item_id'))==item_id and item.get('item_status') in {'UNLIST','NORMAL'},'EXACT_EXISTING_ITEM_STATUS_REQUIRED')
                return item_id
            if context.region != 'PH':return known
            need(known in (None, PH_ITEM) and str(runtime.resolved_global_item_id(context, PH_ITEM)) == GLOBAL, 'EXACT_PH_GLOBAL_LINK_REQUIRED')
            models = runtime.regional_models(context, PH_ITEM)
            need({str(r.get('model_sku')): str(r.get('model_id')) for r in models} == PH_MODELS, 'EXACT_PH_OFFICIAL_MODELS_REQUIRED')
            item = runtime.regional_item(context, PH_ITEM)
            need(item and str(item.get('item_id')) == PH_ITEM and item.get('item_status') in {'UNLIST', 'NORMAL'}, 'EXACT_EXISTING_PH_STATUS_REQUIRED')
            return PH_ITEM
        def create_publish_task(self, context, body):
            need(not ledger.ctx.get('list_only'),'EXISTING_ITEMS_NO_CREATE')
            self._context(context);need(context.region != 'PH' and str(body.get('global_item_id')) == GLOBAL, 'PH_RECREATE_OR_WRONG_GLOBAL_FORBIDDEN')
            return runtime.create_publish_task(context, body)
        def list_item(self,context,item_id):
            if ledger.ctx.get('list_only'):
                self._context(context);need(item_id==LIST_ITEMS[ledger.base.target_label],'EXACT_EXISTING_LIST_ITEM_ONLY')
            return runtime.list_item(context,item_id)
    return replace(deps, global_item_id_resolver=ExistingGlobal(), runtime=Regional())


def has_pending_work(store, decision_id, operations):
    """Availability only; source-protected activation owns every actual write."""
    if decision_id != DECISION:return False
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    bound = getattr(operations, 'agent_prepared_common', None)
    if type(bound) is not AgentPreparedCommonService or bound.store is not store:return False
    try:
        ctx = _event(bound, {'task_id': 'TASK-20261023-001'})
        ctx['list_only']=_list_event(bound,{'task_id':'TASK-20261023-001'})
        with store._connect_readonly() as db:
            rows=(_list_facts if ctx.get('list_only') else _facts)(db,ctx)
            old=_joined(ctx['list_only']['image'] if ctx.get('list_only') else ctx['image'])
            before=LIST_BEFORE if ctx.get('list_only') else BEFORE
            return any(r['target_label'] in before and r['attempts'] == before[r['target_label']]
                and r['status'] in {'FAILED', 'PENDING'} and [v for v in rows if v['budget_scope'] == 'target' and v['target_label'] == r['target_label']]
                == [v for v in old if v['budget_scope'] == 'target' and v['target_label'] == r['target_label']]
                for r in db.execute('SELECT * FROM release_target_runs WHERE run_id=?', (RUN,)))
    except (NativeMutationBudgetHold, ValueError, OSError, KeyError, TypeError):return False
