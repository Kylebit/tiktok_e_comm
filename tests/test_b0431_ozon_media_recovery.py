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
from types import SimpleNamespace

import pytest

from shared_platform import native_ozon_three_card_media_recovery as media
from shared_platform.native_final_mutation_budget import SCHEMA_SQL, _DurableLedger, _json, _sha


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
