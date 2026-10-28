"""Captured B failure/CP/ledger; temporary clone, no provider or actual DB access."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from shared_platform import native_tiktok_locale_draft_correction as repair


@pytest.fixture
def captured(tmp_path, monkeypatch):
    if not os.environ.get('ORBIT_B0431_LOCALE_CAPTURED'):pytest.skip('explicit captured B inputs required')
    def deny(*args, **kwargs):raise AssertionError('No network permitted')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    folder = Path(os.environ['ORBIT_B0431_LOCALE_CAPTURED'])
    template = Path(os.environ['ORBIT_B0431_CORRECTION_TEMPLATE'])
    assert json.loads(template.read_bytes())['status'] == 'NOT_AUTHORIZED'
    path = Path(os.environ['ORBIT_B0431_CORRECTION_AUTHORITY'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == 'f46b1b49519e3d16094453f165ab2643111b74df28dbc0234273ebaba3eed10c'
    doc = json.loads(path.read_bytes())
    ref = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert repair.read_correction_proof(ref) == doc
    ctx = repair._material(ref, doc, tmp_path / 'runtime')
    def load(name, digest):
        raw = (folder / name).read_bytes();assert hashlib.sha256(raw).hexdigest() == digest
        return json.loads(raw)
    saved = load('MY_AFTER_ACCEPTED_SAVE_EDITABLE_READ.response.private.json', 'a50c2ac6ee7d28a198174bdee3b7d8e1f3bf4187f4f8e3b5f13cd4076e3c47e2')
    warehouses = load('MY_AFTER_ACCEPTED_SAVE_WAREHOUSES_READ.response.private.json', 'b17984d50217934346d3420a7444a6657031c553496a8d48a9a7be17153e3261')
    return dict(ctx=ctx, saved=saved, warehouses=warehouses, folder=folder, template=template)


def _target(ctx, label='tiktok:LH_MY'):
    from domains.channel_operations.tiktok_v4_execution import project_tiktok_v4_execution_plan
    from domains.channel_operations.tiktok_publisher import _validate_snapshot
    from modules.miaoshou.tiktok_v4_drafts import _collectbox_context
    detail, shop, _, _, _ = repair.EXACT[label]
    context = _collectbox_context(ctx['snapshot'], identity=dict(target_label=label, detail_id=detail, shop_id=shop))
    plan = project_tiktok_v4_execution_plan(ctx['snapshot'],
        collectbox_contexts={label: context}, category_resolver=None, target_scope=(label,))
    return plan, _validate_snapshot(plan['targets'][0]['publisher_snapshot'])['targets'][0]


def test_captured_bilingual_failure_and_exact_frozen_locale_projection(captured):
    from domains.product_operations.approved_publication_snapshot import publication_content_for_target
    from modules.miaoshou import tiktok_v4_drafts as drafts
    from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport, READ_SITE_DRAFT_PATH, WAREHOUSE_GET_PATH
    ctx = captured['ctx'];frozen = ctx['snapshot']
    for label in repair.EXACT:
        target = next(t for t in frozen['publication_targets'] if t['target_label'] == label)
        value = drafts._draft_payload(frozen, target=target, category=drafts._target_category(frozen, target=target, resolver=None))
        expected = {r['model_sku']: r['display_name'] for r in publication_content_for_target(frozen, label)['variants']}
        assert {r['model_sku']: r['specification']['option'] for r in value['skus']} == expected
        assert value['images'] == frozen['product']['images'] and len(value['images']) == 8
        assert value['stock_policy']['quantity_per_sku'] == 200
    _, target = _target(ctx)
    def post(path, body):
        if path == READ_SITE_DRAFT_PATH:return deepcopy(captured['saved'])
        if path == WAREHOUSE_GET_PATH:return deepcopy(captured['warehouses'])
        raise AssertionError('Mutation forbidden in captured field comparison')
    transport = MiaoshouTikTokTransport(post=post)
    observed = transport.read_draft(target)
    assert transport.post_save_draft_matches(target, observed) is False
    prepared = transport.prepare_approved_draft(target, observed)
    corrected = dict(info=prepared['body']['siteCollectItemInfo'], oss_md5='local-readback-test')
    assert transport.post_save_draft_matches(target, corrected) is True
    assert corrected['info']['imgUrls'] == observed['info']['imgUrls']
    assert len(corrected['info']['skuMap']) == 3
    corrected['info']['title'] += ' different content'
    assert transport.post_save_draft_matches(target, corrected) is False


@pytest.fixture
def clone(captured, tmp_path, monkeypatch):
    from modules.products import server
    from shared_platform.release_store import ReleaseStore
    from shared_platform.native_final_mutation_budget import SCHEMA_SQL
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService, TABLES, TRIGGERS
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    from shared_platform.product_publication_live_dependencies import TikTokV4DraftCheckpointStore
    folder = Path(os.environ['ORBIT_B0431_PRIOR_CAPTURED'])
    raw = (folder / 'ORIGINAL_RUN_WITH_SH4_ATTEMPT2_FOR_OFFLINE_REPAIR.private.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '12aae78097d023001c08a07b1e583e6cb8f219c2e79369eb571b31a193abb06a'
    tables = json.loads(raw)['tables']
    raw = (folder / 'ORIGINAL_RELEASE_SCHEMA_FOR_CAPTURED_FIXTURE.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '53c7e5ee397093863d2c8e4fc764704540c6141c25cb4b72b6decc7f774f8fda'
    store = ReleaseStore(tmp_path / 'clone.db')
    with sqlite3.connect(store.path) as db:
        db.executescript(SCHEMA_SQL)
        for name, sql in json.loads(raw).items():
            if not name.startswith('native_final_budget_'):db.execute(sql)
        db.execute(TABLES[0])
        for sql in TRIGGERS:db.execute(sql)
    with store._transaction():pass
    ctx = captured['ctx'];rows = ctx['image']['native_TikTok_budget_rows'];scope = rows[0]['scope_key']
    with sqlite3.connect(store.path) as db:
        for table, records in tables.items():
            for row in records:
                if table.startswith('native_final_budget_') and table != 'native_final_budget_scopes' and row['scope_key'] == scope:continue
                db.execute('INSERT INTO ' + table + '(' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        for row in rows:
            db.execute('INSERT INTO native_final_budget_reservations VALUES(?,?,?,?,?,?,?)', tuple(row[k]
                for k in ('scope_key', 'sequence', 'target_label', 'budget_scope', 'operation', 'attempt_count', 'initial_outcome')))
            if row['request_digest']:db.execute('INSERT INTO native_final_budget_dispatches VALUES(?,?,?)', (scope, row['sequence'], row['request_digest']))
            if row['outcome']:db.execute('INSERT INTO native_final_budget_outcomes VALUES(?,?,?)', (scope, row['sequence'], row['outcome']))
        for row in ctx['image']['original_failure3_rows']:
            db.execute('INSERT INTO release_target_failure_events VALUES(?,?,?,?,?,?)', tuple(row[k]
                for k in ('run_id', 'target_label', 'attempt', 'evidence_json', 'evidence_digest', 'created_at')))
            # Actual retained failure3 + original identity row; only local clone state projection.
            db.execute("UPDATE release_target_runs SET status='FAILED',attempts=3 WHERE run_id=? AND target_label=?", (repair.RUN, row['target_label']))
    active = object.__new__(_NativeExecution);active.store = store
    active.service = NativeSoleFinalDecisionService(store, lambda: {})
    active.plan_id = repair.PLAN;active.decision_id = repair.DECISION
    active.owner_sid = tables['native_sole_final_decisions'][0]['owner_sid']
    active.runtime = SimpleNamespace(_operations=SimpleNamespace(agent_prepared_common=object()), supervisor=SimpleNamespace(root=ctx['root']))
    ctx['active'] = active
    request = SimpleNamespace(platform='TIKTOK', snapshot=ctx['snapshot'], run_id=repair.FILE_RUN, target_labels=('tiktok:LH_MY',))
    path = TikTokV4DraftCheckpointStore(ctx['root'])._path(request)
    path.parent.mkdir(parents=True);path.write_bytes(ctx['checkpoint_raw'])
    # Explicit current source/actor/COMMON boundary: source A owns genuine HMAC/CAS verification.
    monkeypatch.setattr(repair, '_context', lambda value: ctx if value is active else pytest.fail('active changed'))
    token = _ACTIVE.set(active)
    try:yield dict(captured=captured, ctx=ctx, store=store, active=active, request=request, path=path)
    finally:_ACTIVE.reset(token)


def _base(clone, label):
    from shared_platform.native_final_mutation_budget import _DurableLedger
    ctx = clone['ctx'];active = clone['active']
    with active.store._connect_readonly() as db:binding, _ = repair._facts(db, ctx)
    def recheck(db=None):
        if db is not None:
            active.service._decision(db, repair.DECISION, owner_sid=active.owner_sid)
            target = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (repair.RUN, label)).fetchone()
            assert tuple(target) == ('RUNNING', 4)
    return _DurableLedger(store=active.store, binding=binding, target_label=label, recheck=recheck)


def test_original_publish_loop_begin4_correction_then_original_publish_outlet(clone, monkeypatch):
    from modules.products import server, release_adapters
    from domains.channel_operations.release_executor import AdapterRegistration
    from domains.channel_operations.tiktok_v4_execution import execute_tiktok_v4_plan
    from shared_platform import native_canonical_provider_edges as edges
    from shared_platform import native_ozon_three_card_media_recovery as media
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    from modules.miaoshou.tiktok_publisher import READ_SITE_DRAFT_PATH, WAREHOUSE_GET_PATH, PUBLISH_PATH
    from modules.miaoshou.tiktok_v4_drafts import SAVE_SITE_DRAFT_PATH
    store = clone['store'];ctx = clone['ctx'];active = clone['active'];plan = store.get_plan(repair.PLAN)
    before = deepcopy(store.get_run(repair.RUN));saved = deepcopy(clone['captured']['saved']);wire = [];claims = []
    class PublishOutlet(BaseException):pass
    def post(path, body):
        if path == READ_SITE_DRAFT_PATH:return deepcopy(saved)
        if path == WAREHOUSE_GET_PATH:return deepcopy(clone['captured']['warehouses'])
        with store._connect_readonly() as db:
            platform = repair.PLATFORM if path == SAVE_SITE_DRAFT_PATH else 'TIKTOK'
            scope = db.execute('SELECT scope_key FROM native_final_budget_scopes WHERE platform=?', (platform,)).fetchone()[0]
            assert db.execute('SELECT count(*) FROM native_final_budget_dispatches WHERE scope_key=?', (scope,)).fetchone()[0] == (1 if path == SAVE_SITE_DRAFT_PATH else 13)
        wire.append(path)
        if path == SAVE_SITE_DRAFT_PATH:
            saved['data']['siteCollectItemInfo'] = deepcopy(body['siteCollectItemInfo']);saved['data']['ossMd5'] = 'after-local-correction'
            return {'result': 'success', 'code': '0', 'data': {}}
        assert path == PUBLISH_PATH and body == {'detailIds': [3487094129], 'shopIds': ['13295169']}
        assert clone['path'].read_bytes() == ctx['checkpoint_raw']
        raise PublishOutlet('First PUBLISH outlet after original slot durable dispatch; no actual wire')
    def adapter(request):
        assert request.target_label == 'tiktok:LH_MY'
        ledger = repair.for_active(active, _base(clone, request.target_label))
        preparer = repair.bind_preparer(None, ledger, None)
        receipt = preparer(clone['request'])
        assert receipt['external_write_count'] == 12 and receipt['targets'][0]['status'] == 'PREPARED'
        publisher = repair.bind_publisher(edges.tiktok_publisher(ledger.wrap_post(post, provider='miaoshou'), ledger), ledger)
        execution, _ = _target(ctx)
        execute_tiktok_v4_plan(execution, publisher=publisher,
            storefront_readback=SimpleNamespace(readback=lambda **kwargs: pytest.fail('No storefront before publish outlet')),
            before_publish=lambda label: ledger.reserve_target(label, 'publish_target'), accepted_save_targets=('tiktok:LH_MY',))
        pytest.fail('PUBLISH outlet was not reached')
    registry = {name: AdapterRegistration(name, adapter, True, True, True, True) for name in release_adapters.ADAPTER_NAMES.values()}
    def gate(data, *, store):
        with store._connect_readonly() as db:active.service._decision(db, repair.DECISION, owner_sid=active.owner_sid)
        return dict(dashboard={'frozen_review_projection': True}, payload=plan['payload'], plan=plan,
            run=store.get_run(repair.RUN), predecessor_run=None, registry=registry,
            target_rows=[dict(channel=label.split(':')[0], site=label.split(':')[1], adapter=release_adapters.ADAPTER_NAMES[label.split(':')[0]]) for label in plan['targets']]), None
    active.gate = gate;active.common_dependency_verified = lambda value: True
    monkeypatch.setattr(server, '_activate_native_shopee_global_zero', lambda active: ())  # Existing independent SH work outside this regression.
    monkeypatch.setattr(server, '_activate_native_tiktok_claimed_save', lambda active: ())  # Older CP8 continuation already consumed.
    monkeypatch.setattr(media, 'activate_failed_targets', lambda active: (_ for _ in ()).throw(NativeMutationBudgetHold('OZON_THREE_MEDIA_TEST_BOUNDARY')))
    monkeypatch.setattr(media, 'recovery_actions', lambda active, run, actions: actions)
    original = store.begin_target
    def begin(run, label):
        result = original(run, label);claims.append((label, result['status'], result['attempts']));return result
    monkeypatch.setattr(store, 'begin_target', begin)
    try:
        returned = server._publish_selected_release(dict(offer_id=plan['product_id'], plan_id=repair.PLAN,
            confirmation_token=plan['confirmation_token'], confirm_publish=True), native_gate=active)
    except PublishOutlet:pass
    else:
        code, body = returned
        pytest.fail(str(dict(http=code, code=body.get('code'), error=body.get('error'), claims=claims, wire=wire,
            actions=[{k: row.get(k) for k in ('target_label', 'runnable', 'reason_code')} for row in body.get('target_recovery_actions', [])])))
    assert claims == [('tiktok:LH_MY', 'RUNNING', 4)] and wire == [SAVE_SITE_DRAFT_PATH, PUBLISH_PATH]
    assert clone['path'].read_bytes() == ctx['checkpoint_raw']
    after = store.get_run(repair.RUN)
    assert [r['failure_events'] for r in after['targets']] == [r['failure_events'] for r in before['targets']]
    with store._connect_readonly() as db:
        _, rows = repair._facts(db, ctx)
        assert len(rows) == 16 and rows[9]['sequence'] == 10 and rows[9]['request_digest'] and rows[9]['outcome'] is None
        scope = db.execute('SELECT scope_key FROM native_final_budget_scopes WHERE platform=?', (repair.PLATFORM,)).fetchone()[0]
        extra = db.execute('SELECT outcome FROM native_final_budget_outcomes WHERE scope_key=?', (scope,)).fetchall()
        assert [r[0] for r in extra] == ['ACCEPTED']


def test_missing_authority_changed_CP_once_cap_and_unknown_correction_stop(clone):
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    from modules.miaoshou.tiktok_v4_drafts import SAVE_SITE_DRAFT_PATH
    ctx = clone['ctx'];store = clone['store'];active = clone['active']
    template = clone['captured']['template'];ref = dict(path=str(template), sha256=hashlib.sha256(template.read_bytes()).hexdigest())
    with pytest.raises(NativeMutationBudgetHold, match='EXACT_TECHNICAL_AUTHORITY_REQUIRED'):repair.read_correction_proof(ref)
    clone['path'].write_bytes(ctx['checkpoint_raw'] + b' ')
    with pytest.raises(NativeMutationBudgetHold, match='ORIGINAL_CP12_CHANGED'):repair._checkpoint(ctx)
    clone['path'].write_bytes(ctx['checkpoint_raw'])
    assert repair.activate_failed_targets(active) == tuple(repair.EXACT)
    for label, (_, _, _, sequence, _) in repair.EXACT.items():
        store.begin_target(repair.RUN, label)
        ledger = repair.for_active(active, _base(clone, label))
        assert ledger.reserve_target(label, 'publish_target')['sequence'] == sequence
        assert ledger.reserve_target(label, 'publish_target')['sequence'] == sequence
        with pytest.raises(NativeMutationBudgetHold, match='FRESH_EXACT_MATCH'):
            ledger.dispatch_reserved('publish_target', lambda *args: pytest.fail('Unmatched publish forbidden'), '/wrong', {})
        ledger.prepared = {'path': SAVE_SITE_DRAFT_PATH, 'body': {'detailId': int(repair.EXACT[label][0])}}
        ledger.reserve_target(label, 'save_draft')
        with pytest.raises(TimeoutError):ledger.dispatch_reserved('save_draft', lambda *args: (_ for _ in ()).throw(TimeoutError()), ledger.prepared['path'], ledger.prepared['body'])
        with pytest.raises(NativeMutationBudgetHold):ledger.reserve_target(label, 'save_draft')
        ledger.fresh_match = True;ledger.target = {'test': 'still blocked by UNKNOWN corrective SAVE'}
        from modules.miaoshou.tiktok_publisher import PUBLISH_PATH
        detail, shop, _, _, _ = repair.EXACT[label]
        with pytest.raises(NativeMutationBudgetHold, match='CORRECTIVE_SAVE_UNKNOWN_OR_REJECTED'):
            ledger.dispatch_reserved('publish_target', lambda *args: pytest.fail('UNKNOWN correction forbids publish'),
                PUBLISH_PATH, {'detailIds': [int(detail)], 'shopIds': [shop]})
        with store._transaction() as db:db.execute("UPDATE release_target_runs SET status='FAILED' WHERE run_id=? AND target_label=?", (repair.RUN, label))
    with store._connect_readonly() as db:
        _, rows = repair._facts(db, ctx)
        assert rows == ctx['image']['native_TikTok_budget_rows']
        scope = db.execute('SELECT scope_key FROM native_final_budget_scopes WHERE platform=?', (repair.PLATFORM,)).fetchone()[0]
        assert db.execute('SELECT count(*) FROM native_final_budget_reservations WHERE scope_key=?', (scope,)).fetchone()[0] == 4
        assert db.execute('SELECT count(*) FROM native_final_budget_outcomes WHERE scope_key=?', (scope,)).fetchone()[0] == 0
    assert repair.activate_failed_targets(active) == ()


def test_activation_constructs_ledger_readers_before_writer_transaction(clone, monkeypatch):
    """Real SQLite connection ordering; this does not manufacture SQLITE_BUSY."""
    import inspect
    from modules.products import server
    observed = [];original = repair._technical
    def technical(ctx, label, recheck=None):
        frames = inspect.stack()
        writers = [frame.frame.f_locals.get('db') for frame in frames
            if frame.function == 'activate_failed_targets' and frame.filename == repair.__file__]
        observed.append((label, any(isinstance(db, sqlite3.Connection) and db.in_transaction for db in writers)))
        return original(ctx, label, recheck)
    monkeypatch.setattr(repair, '_technical', technical)
    with server._release_execution_lock:
        assert repair.activate_failed_targets(clone['active']) == tuple(repair.EXACT)
    assert observed == [(label, False) for label in repair.EXACT]


def _whole_original_database(clone):
    path = Path(os.environ['ORBIT_B0431_FULL_RELEASE_CLONE'])
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '2f6cd096b1d7211635ef25963686a784ffe9b0b851183055124a3bd7f8c484b3'
    destination = clone['store'].path.with_name('whole-original-private-test.db')
    assert not destination.exists();destination.write_bytes(raw)
    clone['store'].path = destination


@pytest.mark.parametrize('version', ['original297', 'fixed'])
def test_whole_actual_database_original_loop_once(clone, monkeypatch, version):
    """Exact49-table/170-row copy; no synthetic SQLITE_BUSY is raised."""
    _whole_original_database(clone)
    if version == 'original297':
        import ast
        import subprocess
        root = Path(__file__).resolve().parents[1]
        raw = subprocess.check_output(['git', '-C', str(root), 'show',
            '297203e7c6dd6bbce1d592c227aec51f1cfde475:shared_platform/native_tiktok_locale_draft_correction.py'])
        node = next(n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name == 'activate_failed_targets')
        namespace = dict(repair.__dict__)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'original297/activation.py', 'exec'), namespace)
        monkeypatch.setattr(repair, 'activate_failed_targets', namespace['activate_failed_targets'])
    test_original_publish_loop_begin4_correction_then_original_publish_outlet(clone, monkeypatch)


def test_whole_actual_database_strict_unknown_and_cap_guards(clone):
    _whole_original_database(clone)
    test_missing_authority_changed_CP_once_cap_and_unknown_correction_stop(clone)


@pytest.fixture
def saved_publish(clone, tmp_path):
    """Actual failed4 whole DB and rows, with source/actor boundary as above."""
    folder = Path(os.environ['ORBIT_B0431_SAVED_PUBLISH_CAPTURED'])
    image_path = Path(os.environ['ORBIT_B0431_SAVED_PUBLISH_IMAGE'])
    raw = image_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '2b51f985eb7c6608f48494d3da6574a0cab81d13d254357b80507f484a62a372'
    image = json.loads(raw)
    original_db = Path(os.environ['ORBIT_B0431_SAVED_PUBLISH_DB']).read_bytes()
    assert hashlib.sha256(original_db).hexdigest() == 'c551c511e5e2afc72046bab72bae73360b85bcb38814e5a620c05181d9493ea9'
    target = tmp_path / 'actual-failed4-private-clone.db';target.write_bytes(original_db);clone['store'].path = target
    labels = ['tiktok:LH_PH', 'tiktok:LH_TH', 'tiktok:LH_VN']
    refs = {};slots = [];original = clone['ctx'];rows = repair._image_budget(image, 'TIKTOK');correction = repair._image_budget(image, repair.PLATFORM)
    for label in labels:
        path = folder / ('TIKTOK_' + label.rsplit('_', 1)[1] + '_ATTEMPT4_ORIGINAL_FAILURE.private.json')
        refs[label] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        failure = json.loads(path.read_bytes())['failure_rows'][0]
        detail, shop, _, sequence, _ = repair.EXACT[label]
        slot = next(r for r in rows if r['sequence'] == sequence);saved = next(r for r in correction if r['target_label'] == label)
        slots.append(dict(target_label=label,scope_key=slot['scope_key'],sequence=sequence,detail_id=detail,shop_id=shop,
            failure4_evidence_digest=failure['evidence_digest'],correction_scope_key=saved['scope_key'],
            correction_sequence=saved['sequence'],correction_request_digest=saved['request_digest']))
    doc = dict(schema='native-tiktok-accepted-save-unsent-publish-continuation/v1',status='NOT_AUTHORIZED',
        task_id='TASK-20261023-001',offer_id='8412287877065586543',plan_id=repair.PLAN,run_id=repair.RUN,decision_id=repair.DECISION,
        candidate_digest=repair.CANDIDATE,snapshot_digest=repair.SNAPSHOT,base_source_head=image['source_head'],target_labels=labels,
        before_attempt=4,no_new_SAVE=True,no_new_CREATE_or_CLAIM=True,no_new_budget=True,
        retained_ref=dict(path=str(image_path),sha256=hashlib.sha256(raw).hexdigest()),checkpoint_ref=original['doc']['checkpoint_ref'],
        original_correction_authority_ref=original['ref'],failure_refs=refs,not_sent_slots=slots)
    path = tmp_path / 'saved-continuation-fixture.json'
    def freeze(value):
        path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    with pytest.raises(NativeMutationBudgetHold,match='EXACT_SAVED_CONTINUATION_PROOF_REQUIRED'):repair.read_saved_publish_continuation_proof(freeze(doc))
    doc['status']='ROOT_TECHNICAL_EXECUTION_APPROVED'  # Fixture only, never an actual technical/source authority.
    ref=freeze(doc);assert repair.read_saved_publish_continuation_proof(ref)==doc
    original['saved_continuation']=repair._saved_material(ref,doc)
    return dict(clone=clone,doc=doc,ref=ref,image=image,freeze=freeze)


@pytest.mark.parametrize('label', ['tiktok:LH_PH','tiktok:LH_TH','tiktok:LH_VN'])
def test_actual_failed4_original_loop_begin5_accepted_SAVE_no_repeat_publish_outlet(saved_publish,monkeypatch,label):
    from modules.products import server,release_adapters
    from domains.channel_operations.release_executor import AdapterRegistration
    from domains.channel_operations.tiktok_v4_execution import execute_tiktok_v4_plan
    from shared_platform import native_canonical_provider_edges as edges,native_ozon_three_card_media_recovery as media
    from shared_platform.native_final_mutation_budget import _DurableLedger,NativeMutationBudgetHold
    from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport,READ_SITE_DRAFT_PATH,WAREHOUSE_GET_PATH,PUBLISH_PATH
    clone=saved_publish['clone'];ctx=clone['ctx'];store=clone['store'];active=clone['active'];plan=store.get_plan(repair.PLAN)
    # Select one actual NOT_SENT target per loop outlet case; other real rows are retained.
    doc=deepcopy(saved_publish['doc']);doc['target_labels']=[label];doc['failure_refs']={label:doc['failure_refs'][label]}
    doc['not_sent_slots']=[r for r in doc['not_sent_slots'] if r['target_label']==label]
    ref=saved_publish['freeze'](doc);assert repair.read_saved_publish_continuation_proof(ref)==doc
    ctx['saved_continuation']=repair._saved_material(ref,doc)
    before=deepcopy(store.get_run(repair.RUN));claims=[];wire=[]
    execution,target=_target(ctx,label);detail,shop,_,sequence,_=repair.EXACT[label]
    failure=ctx['saved_continuation']['failures'][label];evidence=json.loads(failure['evidence_json'])
    inventory=evidence['provider_readonly_observations']['tiktok_prewrite_inventory'][label]
    seed=deepcopy(clone['captured']['saved']['data']['siteCollectItemInfo']);seed['detailId']=int(detail)
    for row in seed['skuMap'].values():
        model=str(row.get('itemNum') or row.get('sellerSku'))
        row['shopIdToWarehouseIdAndStockMap']={shop:{key:str(value) for key,value in inventory[model].items()}}
    warehouses=deepcopy(clone['captured']['warehouses'])
    ids=next(iter(inventory.values()));positive=next(k for k,v in ids.items() if v==200);zero=next(k for k,v in ids.items() if v==0)
    for group in warehouses['data']['shopWarehouseList']:
        group['shopId']=shop
        for row in group['warehouseList']:
            old=str(row['warehouseId']);row['warehouseId']=positive if old=='7496682241901561618' else zero
    def readonly(path,body):
        if path==WAREHOUSE_GET_PATH:return deepcopy(warehouses)
        raise AssertionError('Fixture preparation cannot call a provider or save')
    prepared=MiaoshouTikTokTransport(post=readonly).prepare_approved_draft(target,dict(info=seed,oss_md5='local-fixture'))
    saved=dict(result='success',data=dict(siteCollectItemInfo=prepared['body']['siteCollectItemInfo'],ossMd5='local-exact-fixture'))
    # The fresh editable response is a strict frozen-command fixture, not a claimed current provider GET.
    class PublishOutlet(BaseException):pass
    def adapter(request):
        assert request.target_label==label
        with store._connect_readonly() as db:binding,_=repair._facts(db,ctx)
        def recheck(db=None):
            if db is not None:
                active.service._decision(db,repair.DECISION,owner_sid=active.owner_sid)
                assert tuple(db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?',(repair.RUN,label)).fetchone())==('RUNNING',5)
        ledger=repair.for_active(active,_DurableLedger(store=store,binding=binding,target_label=label,recheck=recheck))
        assert ledger.saved_only
        with pytest.raises(NativeMutationBudgetHold,match='ACCEPTED_SAVE_MUST_NOT_REPEAT'):ledger.reserve_target(label,'save_draft')
        with pytest.raises(NativeMutationBudgetHold):ledger.reserve_shared('create_platform_draft')
        with pytest.raises(NativeMutationBudgetHold):ledger.reserve_target(label,'claim_draft')
        def post(path,body):
            if path==READ_SITE_DRAFT_PATH:return deepcopy(saved)
            if path==WAREHOUSE_GET_PATH:return deepcopy(warehouses)
            wire.append(path);assert path==PUBLISH_PATH and body=={'detailIds':[int(detail)],'shopIds':[shop]}
            with store._connect_readonly() as db:
                _,rows=repair._facts(db,ctx);slot=next(r for r in rows if r['sequence']==sequence)
                assert slot['request_digest'] and slot['outcome'] is None
                assert repair._image_budget(saved_publish['image'],repair.PLATFORM)==repair._rows(db,ledger.extra.scope_key)
            raise PublishOutlet('Original slot first dispatch, no network')
        publisher=repair.bind_publisher(edges.tiktok_publisher(ledger.wrap_post(post,provider='miaoshou'),ledger),ledger)
        assert publisher.transport.post_save_draft_matches(target,publisher.transport.read_draft(target)) is True
        submit=publisher.transport.submit
        class ExactPublishBlocked(BaseException):pass
        def traced_submit(value):
            try:return submit(value)
            except Exception as error:raise ExactPublishBlocked(type(error).__name__ + ':' + str(error)) from None
        publisher.transport.submit=traced_submit
        execute_tiktok_v4_plan(execution,publisher=publisher,
            storefront_readback=SimpleNamespace(readback=lambda **kw:pytest.fail('No official read before wire outlet')),
            before_publish=lambda value:ledger.reserve_target(value,'publish_target'),accepted_save_targets=(label,))
        pytest.fail('Original publish outlet not reached')
    registry={name:AdapterRegistration(name,adapter,True,True,True,True) for name in release_adapters.ADAPTER_NAMES.values()}
    def gate(data,*,store):
        with store._connect_readonly() as db:active.service._decision(db,repair.DECISION,owner_sid=active.owner_sid)
        return dict(dashboard={'frozen_review_projection':True},payload=plan['payload'],plan=plan,run=store.get_run(repair.RUN),
            predecessor_run=None,registry=registry,target_rows=[dict(channel=x.split(':')[0],site=x.split(':')[1],adapter=release_adapters.ADAPTER_NAMES[x.split(':')[0]]) for x in plan['targets']]),None
    active.gate=gate;active.common_dependency_verified=lambda gate:True
    monkeypatch.setattr(server,'_activate_native_shopee_global_zero',lambda active:())
    monkeypatch.setattr(server,'_activate_native_tiktok_claimed_save',lambda active:())
    from shared_platform import native_shopee_regional_continuation as shopee
    monkeypatch.setattr(shopee,'activate_failed_targets',lambda active:())  # Independent frozen SH path, not exercised by this TK outlet case.
    monkeypatch.setattr(media,'activate_failed_targets',lambda active:(_ for _ in ()).throw(NativeMutationBudgetHold('OZON_THREE_MEDIA_TEST_BOUNDARY')))
    monkeypatch.setattr(media,'recovery_actions',lambda active,run,actions:actions)
    original=store.begin_target
    def begin(run,value):
        row=original(run,value);claims.append((value,row['status'],row['attempts']));return row
    monkeypatch.setattr(store,'begin_target',begin)
    try:server._publish_selected_release(dict(offer_id=plan['product_id'],plan_id=repair.PLAN,confirmation_token=plan['confirmation_token'],confirm_publish=True),native_gate=active)
    except PublishOutlet:pass
    else:pytest.fail(str(dict(claims=claims,wire=wire)))
    assert claims==[(label,'RUNNING',5)] and wire==[PUBLISH_PATH]
    assert [r['failure_events'] for r in store.get_run(repair.RUN)['targets']]==[r['failure_events'] for r in before['targets']]
    assert clone['path'].read_bytes()==ctx['checkpoint_raw']


def test_saved_continuation_wrong_selection_or_dispatched_UNKNOWN_never_retries(saved_publish):
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold,_request_sha
    from modules.miaoshou.tiktok_publisher import PUBLISH_PATH
    clone=saved_publish['clone'];ctx=clone['ctx'];store=clone['store'];doc=deepcopy(saved_publish['doc'])
    doc['target_labels']=['tiktok:LH_MY']
    with pytest.raises(NativeMutationBudgetHold,match='EXACT_SAVED_CONTINUATION_PROOF_REQUIRED'):
        repair.read_saved_publish_continuation_proof(saved_publish['freeze'](doc))
    label='tiktok:LH_PH';detail,shop,_,sequence,_=repair.EXACT[label]
    slot=next(r for r in repair._image_budget(saved_publish['image'],'TIKTOK') if r['sequence']==sequence)
    with store._transaction() as db:
        db.execute('INSERT INTO native_final_budget_dispatches VALUES(?,?,?)',(slot['scope_key'],sequence,
            _request_sha('publish_target',(PUBLISH_PATH,{'detailIds':[int(detail)],'shopIds':[shop]}),{})))
    with pytest.raises(NativeMutationBudgetHold,match='SAVED_PUBLISH_ALREADY_DISPATCHED_OR_UNKNOWN'):repair.activate_failed_targets(clone['active'])
    assert all(r['status']=='FAILED' and r['attempts']==4 for r in store.get_run(repair.RUN)['targets'] if r['target_label'] in doc['failure_refs'])
