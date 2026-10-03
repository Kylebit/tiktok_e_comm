"""Synthetic receipt consumers: no platform, credentials or production data."""
import copy
import hashlib
import io
import json
import sqlite3
import os
import subprocess
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform.operations_http import handle
from shared_platform.workbench_delisting_adapter import run
from shared_platform.workbench_engine import WorkbenchEngine


def digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def never(*args, **kwargs):
    raise AssertionError('receipt must not call worker, provider or mutating task getter')


@pytest.fixture
def receipt_case(tmp_path):
    clock = [1000.0]
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'synthetic-v1'}, clock=lambda: clock[0])
    task = engine.create({'template': 'delisting', 'source_key': 'receipt', 'title': '合成下架任务',
                          'scope': {'skus': ['0001'], 'shops': ['shopee:MY', 'shopee:TH']}})
    targets = [{'target_label': shop, 'platform': 'shopee', 'product_id': str(index + 101),
                'requested_skus': ['0001'], 'all_product_skus': ['0001'],
                'status': 'READY', 'executable': True, 'current_status': 'NORMAL',
                'shop_cipher': 'SECRET-CIPHER-CANARY', 'access_token': 'SECRET-TOKEN-CANARY'}
               for index, shop in enumerate(task['scope']['shops'])]
    plan = {'schema_version': 'product-delist-plan/v1', 'created_at': '2026-09-23T12:00:00Z',
            'scope': 'exact_selected_stores', 'requested_skus': ['0001'],
            'expected_targets': task['scope']['shops'], 'targets': targets}
    plan['plan_digest'] = digest(plan)
    execution = {'schema_version': 'product-delist-execution/v1', 'created_at': '2026-09-23T12:01:00Z',
                 'plan_digest': plan['plan_digest'], 'requested_skus': ['0001'], 'external_write_count': 1,
                 'targets': [{**{key: row[key] for key in ('target_label', 'product_id', 'requested_skus')},
                              'verified': index == 0, 'attempted': True, 'external_write_count': 1 if index == 0 else 0,
                              'outcome': 'VERIFIED_DELISTED' if index == 0 else 'UNKNOWN',
                              'error': 'SECRET-TOKEN-CANARY C:/private/credential.json'}
                             for index, row in enumerate(targets)]}
    readback = {'schema_version': 'product-delist-readback/v1', 'created_at': '2026-09-23T12:02:00Z',
                'plan_digest': plan['plan_digest'], 'requested_skus': ['0001'], 'external_write_count': 0,
                'targets': [{**{key: row[key] for key in ('target_label', 'product_id', 'requested_skus', 'all_product_skus')},
                             'verified': index == 0, 'current_status': 'UNLIST' if index == 0 else None,
                             'outcome': 'VERIFIED_DELISTED' if index == 0 else 'NOT_VERIFIED_DELISTED',
                             'read_error': None if index == 0 else 'SECRET-TOKEN-CANARY'}
                            for index, row in enumerate(targets)]}
    private = tmp_path / 'private' / task['task_id']
    refs = {kind: save(private / (kind + '-LEASE-CANARY.json'), value)
            for kind, value in [('plan', plan), ('execution', execution), ('readback', readback)]}
    engine.begin_domain_operation('delisting:' + plan['plan_digest'], skus=['0001'], shops=task['scope']['shops'], owner_task_id=task['task_id'])
    with engine.transaction() as conn:
        steps = copy.deepcopy(task['steps'])
        steps[0].update(state='completed', checkpoint={'plan': refs['plan']})
        steps[1].update(state='completed', checkpoint={'execution': refs['execution']})
        conn.execute("UPDATE workbench_execution SET state='reconciliation_required', step_index=2, steps_json=?, checkpoint_json=?,external_started=1 WHERE task_id=?",
                     (json.dumps(steps), json.dumps(refs), task['task_id']))
    profile = SimpleNamespace(data_root=tmp_path, root=Path(__file__).resolve().parents[1], environment='stable')
    runtime = SimpleNamespace(engine=engine, profile=profile, worker_enabled=False,
                              worker=SimpleNamespace(wake=never, status=lambda: {'running': False, 'state': 'stopped'}))
    return SimpleNamespace(engine=engine, runtime=runtime, profile=profile, task_id=task['task_id'],
                           plan=plan, execution=execution, readback=readback, refs=refs, private=private, clock=clock)


def http(case, *, path=None, method='GET', host='127.0.0.1:49321', origin=None):
    headers = Message(); headers['Host'] = host
    if origin: headers['Origin'] = origin
    responses = []; output = io.BytesIO(); sent_headers = {}
    handler = SimpleNamespace(path=path or '/api/orbit/tasks/' + case.task_id + '/delisting-receipt',
                              headers=headers, client_address=('127.0.0.1', 1), server=SimpleNamespace(server_port=49321),
                              rfile=io.BytesIO(), wfile=output,
                              _json=lambda status, body: (responses.append(status), output.write(json.dumps(body, ensure_ascii=False).encode())),
                              send_response=responses.append, send_header=lambda k,v: sent_headers.update({k:v}), end_headers=lambda: None)
    assert handle(handler, method=method, runtime=case.runtime)
    return responses[0], output.getvalue().decode(), sent_headers


def project(case):
    from shared_platform.workbench_delisting_receipt import build_receipt
    return build_receipt(case.engine.read_delisting_receipt_inputs(case.task_id), case.profile)


def test_real_http_receipt_partial_unknown_is_safe_and_does_not_expire_or_wake(receipt_case, monkeypatch):
    c = receipt_case
    with c.engine.transaction() as conn:
        conn.execute("UPDATE workbench_execution SET state='running',lease_until=1 WHERE task_id=?", (c.task_id,))
    with sqlite3.connect(c.engine.store.path) as conn:
        before = tuple(conn.iterdump())
    monkeypatch.setattr(c.engine, 'get', never)
    monkeypatch.setattr(c.engine, 'dashboard', never)
    c.runtime.worker.status = never
    status, body, headers = http(c)
    assert status == 200
    assert '1 / 2' in body and '待核对' in body and 'shopee:MY' in body and 'shopee:TH' in body
    assert 'SECRET-' not in body and 'LEASE-CANARY' not in body and str(c.private) not in body
    assert '<form' not in body and '<script' not in body
    assert headers['Cache-Control'] == 'no-store' and "default-src 'none'" in headers['Content-Security-Policy']
    with sqlite3.connect(c.engine.store.path) as conn:
        assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize('host,origin', [('evil.example:49321', None), ('127.0.0.1:49321', 'https://example.com')])
def test_receipt_host_origin_rejected_before_read(receipt_case, monkeypatch, host, origin):
    monkeypatch.setattr(receipt_case.engine, 'read_delisting_receipt_inputs', never)
    assert http(receipt_case, host=host, origin=origin)[0] == 403


def test_partial_receipt_does_not_claim_zero_sent_or_unlock(receipt_case):
    result = project(receipt_case)
    assert result['summary']['confirmed'] == 1 and result['summary']['total'] == 2
    assert result['summary']['status'] == 'PARTIAL_RECONCILIATION_REQUIRED'
    unknown = next(x for x in result['targets'] if x['target_label'] == 'shopee:TH')
    assert unknown['execution_status'] == 'UNKNOWN'
    assert unknown['readback_status'] == 'READBACK_UNKNOWN'
    assert result['domain']['state'] == 'inflight'


@pytest.mark.parametrize('kind', ['execution', 'readback'])
def test_hash_mismatch_never_counts_changed_document_as_verified(receipt_case, kind):
    c = receipt_case
    Path(c.refs[kind]['path']).write_text('{"verified":true}', encoding='utf-8')
    result = project(c)
    assert result['evidence'][kind]['status'] == 'HASH_MISMATCH'
    if kind == 'readback': assert result['summary']['confirmed'] == 0


@pytest.mark.parametrize('foreign', ['other-task', 'outside'])
def test_same_digest_foreign_artifact_does_not_cross_task_boundary(receipt_case, foreign):
    c=receipt_case
    destination=c.profile.data_root / ('private/other-task' if foreign=='other-task' else 'outside') / 'plan.json'
    ref=save(destination,c.plan)
    with c.engine.transaction() as conn:
        steps=json.loads(conn.execute('SELECT steps_json FROM workbench_execution WHERE task_id=?',(c.task_id,)).fetchone()[0])
        steps[0]['checkpoint']['plan']=ref
        conn.execute('UPDATE workbench_execution SET steps_json=?,checkpoint_json=? WHERE task_id=?',(json.dumps(steps),json.dumps({**c.refs,'plan':ref}),c.task_id))
    result=project(c)
    assert result['evidence']['plan']['status']=='PATH_REJECTED'
    assert result['summary']['confirmed']==0 and result['targets']==[]


@pytest.mark.parametrize('change', ['digest', 'product', 'sku', 'duplicate'])
def test_wrong_frozen_identity_cannot_count_as_verified(receipt_case,change):
    c=receipt_case; altered=copy.deepcopy(c.readback)
    if change=='digest': altered['plan_digest']='sha256:'+'f'*64
    if change=='product': altered['targets'][0]['product_id']='999999'
    if change=='sku': altered['targets'][0]['requested_skus']=['9999']
    if change=='duplicate': altered['targets'].append(copy.deepcopy(altered['targets'][0]))
    ref=save(Path(c.refs['readback']['path']),altered)
    with c.engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',(json.dumps({**c.refs,'readback':ref}),c.task_id))
    result=project(c)
    assert result['summary']['confirmed']==0
    assert result['evidence']['readback']['status']=='IDENTITY_MISMATCH'


def test_legacy_digest_only_is_explicit_gap_not_glob_search(receipt_case):
    c=receipt_case
    with c.engine.transaction() as conn:
        conn.execute("UPDATE workbench_execution SET state='completed',checkpoint_json=? WHERE task_id=?",(json.dumps({'provider_readback_ref':c.refs['readback']['sha256']}),c.task_id))
    result=project(c)
    assert result['evidence']['readback']['status']=='REFERENCE_MISSING'
    assert result['task_state']=='completed' and result['summary']['confirmed']==0


def test_real_adapter_persists_failed_readback_reference(receipt_case):
    c=receipt_case
    with c.engine.transaction() as conn:
        conn.execute('DELETE FROM workbench_domain_locks')
        conn.execute('DELETE FROM workbench_domain_operations')
        conn.execute("UPDATE workbench_execution SET state='queued',external_started=0,checkpoint_json=? WHERE task_id=?",
                     (json.dumps({'plan':c.refs['plan'],'execution':c.refs['execution']}),c.task_id))
    c.engine.register_executor('fixture',['delisting'],c.engine.release)
    claim=c.engine.claim(c.task_id,'fixture')
    skill=SimpleNamespace(ALL_TARGETS=c.plan['expected_targets'],_verify_plan=lambda plan: None,
                          readback=lambda plan: copy.deepcopy(c.readback))
    run(c.engine,c.engine.get(c.task_id),claim['lease_token'],c.profile,skill=skill)
    stored=c.engine.get(c.task_id)
    assert stored['checkpoint']['readback']['sha256']
    assert Path(stored['checkpoint']['readback']['path']).is_file()
    assert stored['checkpoint']['execution']==c.refs['execution']


def test_skill_blocked_result_keeps_frozen_product_identity():
    from shared_platform.workbench_delisting_adapter import _module
    skill=_module(Path(__file__).resolve().parents[1])
    row={'target_label':'shopee:MY','product_id':'101','requested_skus':['0001'],
         'all_product_skus':['0001'],'status':'BLOCKED','executable':False}
    result=skill._execute_one(row)
    assert result['product_id']=='101' and result['requested_skus']==['0001']
    assert result['attempted'] is False


@pytest.mark.parametrize('unknown', [False, True])
def test_real_adapter_skill_domain_chain_keeps_identity_and_get_never_replays(tmp_path, monkeypatch, unknown):
    from shared_platform.workbench_delisting_adapter import _module
    from shared_platform import operations_domain_guard
    from modules.shopee import auth, client

    root = Path(__file__).resolve().parents[1]
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'synthetic-chain'})
    profile = SimpleNamespace(root=root, data_root=tmp_path, environment='stable')
    task = engine.create({'template': 'delisting', 'source_key': 'actual-adapter-chain',
                          'scope': {'skus': ['0001'], 'shops': ['shopee:MY', 'shopee:TH']}})
    skill = _module(root)
    rows = [{'target_label': label, 'platform': 'shopee', 'shop_id': index + 1,
             'product_id': str(index + 101), 'requested_skus': ['0001'], 'all_product_skus': ['0001'],
             'current_status': 'NORMAL', 'status': 'READY', 'executable': True}
            for index, label in enumerate(task['scope']['shops'])]
    observed = {row['product_id']: 'NORMAL' for row in rows}
    calls = []
    monkeypatch.setattr(operations_domain_guard, 'engine_for', lambda root: engine)
    monkeypatch.setattr(skill, '_live_shopee_rows', lambda skus, targets: copy.deepcopy(rows))
    monkeypatch.setattr(skill, '_live_verify', lambda row: {**row, 'current_status': observed[row['product_id']]})
    monkeypatch.setattr(auth, 'ensure_shop_token', lambda shop_id: 'synthetic-token')
    def post(endpoint, shop_id, token, payload):
        assert endpoint == '/api/v2/product/unlist_item' and token == 'synthetic-token'
        item = payload['item_list'][0]
        assert item['unlist'] is True
        calls.append(('POST', shop_id, str(item['item_id'])))
        if unknown and shop_id == 2:
            raise TimeoutError('synthetic ambiguous response; no real network')
        observed[str(item['item_id'])] = 'UNLIST'
        return {'response': {}}
    def get(endpoint, shop_id, token, payload):
        assert endpoint == '/api/v2/product/get_item_base_info'
        calls.append(('GET', shop_id, payload['item_id_list']))
        return {'response': {'item_list': [{'item_status': observed[payload['item_id_list']]}]}}
    monkeypatch.setattr(client, 'shop_post', post)
    monkeypatch.setattr(client, 'shop_get', get)
    engine.register_executor('fixture', ['delisting'], engine.release)
    claim = engine.claim(task['task_id'], 'fixture')
    for step in ['identify', 'delist'] + ([] if unknown else ['readback']):
        current = engine.get(task['task_id'])
        assert current['current_step'] == step
        run(engine, current, claim['lease_token'], profile, skill=skill)
    stored = engine.get(task['task_id'])
    assert stored['execution_state'] == ('reconciliation_required' if unknown else 'completed')
    saved_execution = json.loads(Path(stored['steps'][1].get('checkpoint', stored['checkpoint'])['execution']['path']).read_text(encoding='utf-8'))
    assert [r['product_id'] for r in saved_execution['targets']] == ['101', '102']
    if not unknown:
        assert stored['result_url'] == '/api/orbit/tasks/' + task['task_id'] + '/delisting-receipt'
        assert stored['checkpoint']['readback'] == stored['steps'][2]['checkpoint']['readback']
    else:
        assert saved_execution['targets'][1]['attempted'] is True
        assert saved_execution['targets'][1]['external_write_count'] == 0
    case = SimpleNamespace(engine=engine, profile=profile, task_id=task['task_id'],
                           runtime=SimpleNamespace(engine=engine, profile=profile, worker=SimpleNamespace(wake=never, status=never)))
    frozen_calls = list(calls)
    with sqlite3.connect(engine.store.path) as conn:
        frozen_db = tuple(conn.iterdump())
    for _ in range(2):
        status, body, _ = http(case)
        assert status == 200
        assert ('回执证据缺口' if unknown else '回执完整，任务已完成') in body
    assert calls == frozen_calls
    with sqlite3.connect(engine.store.path) as conn:
        assert tuple(conn.iterdump()) == frozen_db
    receipt = project(case)
    assert receipt['domain']['state'] == ('inflight' if unknown else 'completed')
    assert receipt['domain']['locked_resource_count'] == (2 if unknown else 0)
    assert receipt['targets'][1]['execution_status'] == ('UNKNOWN' if unknown else 'VERIFIED_DELISTED')


@pytest.mark.parametrize('mode', ['file-link', 'directory-link', 'dangling-directory'])
def test_links_rejected_before_reading_their_targets(receipt_case, mode):
    c=receipt_case; linked=c.private/'linked.json'
    if mode=='file-link': linked.symlink_to(Path(c.refs['plan']['path']))
    else:
        directory=c.private/'redirect'
        target=c.profile.data_root/'other-directory'
        if mode!='dangling-directory':
            target.mkdir(); (target/'plan.json').write_text('SHOULD-NOT-BE-READ',encoding='utf-8')
        directory.symlink_to(target,target_is_directory=True)
        linked=directory/'plan.json'
    ref={'path':str(linked),'sha256':c.refs['plan']['sha256']}
    with c.engine.transaction() as conn:
        row=json.loads(conn.execute('SELECT steps_json FROM workbench_execution WHERE task_id=?',(c.task_id,)).fetchone()[0])
        row[0]['checkpoint']['plan']=ref
        conn.execute('UPDATE workbench_execution SET steps_json=?,checkpoint_json=? WHERE task_id=?',(json.dumps(row),json.dumps({**c.refs,'plan':ref}),c.task_id))
    assert project(c)['evidence']['plan']['status']=='PATH_REJECTED'


@pytest.mark.skipif(os.name!='nt',reason='Windows junction contract')
def test_junction_rejected_before_read(receipt_case):
    c=receipt_case; target=c.profile.data_root/'junction-target'; target.mkdir()
    save(target/'plan.json',c.plan)
    junction=c.private/'redirect'
    result=subprocess.run(['cmd','/d','/c','mklink','/J',str(junction),str(target)],capture_output=True)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    from shared_platform.workbench_delisting_receipt import _read,ReceiptError
    with pytest.raises(ReceiptError,match='PATH_REJECTED'):
        _read({'path':str(junction/'plan.json'),'sha256':c.refs['plan']['sha256']},c.private)


@pytest.mark.parametrize('mode',['missing','oversize','malformed','duplicate-json-keys'])
def test_bad_artifact_reports_named_gap_without_reading_directory(receipt_case,mode):
    c=receipt_case; ref=copy.deepcopy(c.refs['readback']); path=Path(ref['path'])
    expected={'missing':'FILE_MISSING','oversize':'DOCUMENT_TOO_LARGE','malformed':'MALFORMED_DOCUMENT','duplicate-json-keys':'MALFORMED_DOCUMENT'}[mode]
    if mode=='missing': path.unlink()
    else:
        raw={'oversize':b'x'*(2*1024*1024+1),'malformed':b'{','duplicate-json-keys':b'{"a":1,"a":2}'}[mode]
        path.write_bytes(raw);ref['sha256']=hashlib.sha256(raw).hexdigest()
        with c.engine.transaction() as conn:
            conn.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',(json.dumps({**c.refs,'readback':ref}),c.task_id))
    result=project(c)
    assert result['evidence']['readback']['status']==expected and result['summary']['confirmed']==0


def test_same_shop_multiple_products_do_not_join_legacy_results_by_order(receipt_case):
    c=receipt_case; plan=copy.deepcopy(c.plan)
    plan['targets'].append({**plan['targets'][0],'product_id':'999'})
    plan.pop('plan_digest');plan['plan_digest']=digest(plan)
    execution=copy.deepcopy(c.execution);execution['plan_digest']=plan['plan_digest']
    for row in execution['targets']:row.pop('product_id')
    refs={**c.refs,'plan':save(Path(c.refs['plan']['path']),plan),'execution':save(Path(c.refs['execution']['path']),execution)}
    with c.engine.transaction() as conn:
        steps=json.loads(conn.execute('SELECT steps_json FROM workbench_execution WHERE task_id=?',(c.task_id,)).fetchone()[0])
        steps[0]['checkpoint']['plan']=refs['plan'];steps[1]['checkpoint']['execution']=refs['execution']
        conn.execute('UPDATE workbench_execution SET steps_json=?,checkpoint_json=? WHERE task_id=?',(json.dumps(steps),json.dumps(refs),c.task_id))
    result=project(c)
    assert result['evidence']['execution']['status']=='IDENTITY_GAP'
    assert all(row['execution_status']=='NOT_RECORDED' for row in result['targets'])


def make_completed(c):
    readback=copy.deepcopy(c.readback)
    for row in readback['targets']:row.update(verified=True,current_status='UNLIST',read_error=None,outcome='VERIFIED_DELISTED')
    execution=copy.deepcopy(c.execution)
    for row in execution['targets']:row.update(verified=True,attempted=True,outcome='VERIFIED_DELISTED',external_write_count=1)
    refs={**c.refs,'readback':save(Path(c.refs['readback']['path']),readback),'execution':save(Path(c.refs['execution']['path']),execution)}
    with c.engine.transaction() as conn:
        steps=json.loads(conn.execute('SELECT steps_json FROM workbench_execution WHERE task_id=?',(c.task_id,)).fetchone()[0])
        steps[1]['checkpoint']['execution']=refs['execution']
        conn.execute("UPDATE workbench_execution SET state='completed',checkpoint_json=?,steps_json=? WHERE task_id=?",(json.dumps(refs),json.dumps(steps),c.task_id))
        conn.execute('DELETE FROM workbench_domain_locks')
        ref='delisting-result:'+hashlib.sha256(json.dumps(execution,sort_keys=True).encode()).hexdigest()
        conn.execute("UPDATE workbench_domain_operations SET state='completed',readback_ref=?",(ref,))
    return refs


def test_full_receipt_requires_exact_domain_completion_and_all_evidence(receipt_case):
    c=receipt_case;refs=make_completed(c)
    assert project(c)['summary']['status']=='COMPLETE_VERIFIED_RECEIPT'
    with c.engine.transaction() as conn:conn.execute("UPDATE workbench_domain_operations SET readback_ref='not-this-receipt'")
    assert project(c)['summary']['status']!='COMPLETE_VERIFIED_RECEIPT'
    Path(refs['execution']['path']).write_text('{}',encoding='utf-8')
    assert project(c)['summary']['status']=='EVIDENCE_GAP'


def test_other_task_domain_owner_cannot_supply_completion(receipt_case):
    c = receipt_case
    make_completed(c)
    with c.engine.transaction() as conn:
        conn.execute('UPDATE workbench_domain_operations SET owner_task_id=NULL')
    result = project(c)
    assert result['domain']['state'] == 'UNBOUND'
    assert result['summary']['status'] == 'ALL_OBSERVED_RECONCILIATION_REQUIRED'


@pytest.mark.parametrize('kind', ['execution', 'readback'])
def test_missing_target_in_valid_document_keeps_partial_rows_but_never_full_receipt(receipt_case, kind):
    c = receipt_case
    refs = make_completed(c)
    path = Path(refs[kind]['path'])
    document = json.loads(path.read_text(encoding='utf-8'))
    document['targets'].pop()
    refs[kind] = save(path, document)
    with c.engine.transaction() as conn:
        steps = json.loads(conn.execute('SELECT steps_json FROM workbench_execution WHERE task_id=?', (c.task_id,)).fetchone()[0])
        if kind == 'execution':
            steps[1]['checkpoint']['execution'] = refs[kind]
            digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
            conn.execute('UPDATE workbench_domain_operations SET readback_ref=?', ('delisting-result:' + digest,))
        conn.execute('UPDATE workbench_execution SET checkpoint_json=?,steps_json=? WHERE task_id=?',
                     (json.dumps(refs), json.dumps(steps), c.task_id))
    result = project(c)
    assert result['summary']['status'] == 'EVIDENCE_GAP'
    assert result['evidence'][kind]['status'] == 'TARGETS_MISSING'
    assert result['targets'][0]['readback_status'] == 'VERIFIED_DELISTED'


def test_non_delisting_task_is_not_a_receipt(receipt_case):
    c = receipt_case
    with c.engine.transaction() as conn:
        conn.execute("UPDATE workbench_execution SET template='profit' WHERE task_id=?", (c.task_id,))
    assert http(c)[0] == 404


@pytest.mark.parametrize('column', ['steps_json', 'scope_json', 'checkpoint_json'])
def test_malformed_saved_task_is_generic_404_without_path_disclosure(receipt_case, column):
    c = receipt_case
    with c.engine.transaction() as conn:
        # These are fixed test column names, never request input.
        conn.execute('UPDATE workbench_execution SET ' + column + '=? WHERE task_id=?',
                     (json.dumps('SECRET-PATH-CANARY'), c.task_id))
    status, body, _ = http(c)
    assert status == 404 and 'SECRET-' not in body


def test_file_replaced_between_guard_and_open_is_rejected(receipt_case, monkeypatch):
    from shared_platform import workbench_delisting_receipt as module
    c = receipt_case
    path = Path(c.refs['readback']['path'])
    replacement = c.private / 'replacement.json'
    replacement.write_bytes(path.read_bytes())
    real_guard = module.require_local_path
    replaced = []
    def guard(selected, **kwargs):
        result = real_guard(selected, **kwargs)
        if selected == path and not replaced:
            os.replace(replacement, path)
            replaced.append(True)
        return result
    monkeypatch.setattr(module, 'require_local_path', guard)
    assert project(c)['evidence']['readback']['status'] == 'FILE_CHANGED'


@pytest.mark.parametrize('suffix', ['../outside.json', '../TASK-99999999-999/plan.json', 'file.json:stream.json'])
def test_ambiguous_paths_are_rejected_before_open(receipt_case, suffix):
    from shared_platform.workbench_delisting_receipt import _read, ReceiptError
    c = receipt_case
    with pytest.raises(ReceiptError, match='PATH_REJECTED'):
        _read({'path': str(c.private / suffix), 'sha256': c.refs['plan']['sha256']}, c.private)


def test_reference_conflict_does_not_silently_replace_frozen_step(receipt_case):
    c=receipt_case
    with c.engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',(json.dumps({**c.refs,'plan':{**c.refs['plan'],'sha256':'f'*64}}),c.task_id))
    assert project(c)['evidence']['plan']['status']=='REFERENCE_CONFLICT'


@pytest.mark.parametrize('path_suffix,method,expected',[('', 'POST',405),('?path=C:/private/credential.json','GET',404),('/raw.json','GET',404)])
def test_receipt_does_not_offer_mutations_or_caller_selected_files(receipt_case,path_suffix,method,expected):
    c=receipt_case
    assert http(c,path='/api/orbit/tasks/'+c.task_id+'/delisting-receipt'+path_suffix,method=method)[0]==expected


def test_adjacent_task_api_hides_all_private_checkpoints_steps_and_events(receipt_case):
    c=receipt_case
    with c.engine.transaction() as conn:
        c.engine._event(conn,c.task_id,'checkpoint_saved',{'checkpoint':c.refs,'token':'SECRET-TOKEN-CANARY'})
    for path in ['/api/orbit/tasks','/api/orbit/tasks/'+c.task_id]:
        status,body,_=http(c,path=path)
        assert status==200
        for secret in ['LEASE-CANARY','SECRET-TOKEN-CANARY',str(c.private)]:assert secret not in body
        assert '/delisting-receipt' in body


def test_public_input_action_survives_without_private_binding(receipt_case):
    from shared_platform.workbench_delisting_receipt import public_projection
    payload={'task':{'task_id':receipt_case.task_id,'template':'delisting','execution_state':'waiting_user',
                     'required_action':{'kind':'input','action_id':'action-1','receipt_binding':{'path':'SECRET-PATH'}},
                     'steps':[]},'events':[]}
    result=public_projection(payload)
    assert result['task']['required_action']['action_id']=='action-1'
    assert 'SECRET-PATH' not in json.dumps(result)


def file_inventory(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file()}


@pytest.mark.parametrize('mode', ['rollback', 'wal-no-sidecars', 'wal-active'])
def test_receipt_get_preserves_exact_file_inventory_and_hashes(receipt_case, monkeypatch, mode):
    c = receipt_case
    writer = None
    if mode.startswith('wal'):
        writer = sqlite3.connect(c.engine.store.path)
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        if mode == 'wal-active':
            writer.execute("UPDATE workbench_execution SET state='failed' WHERE task_id=?", (c.task_id,))
            writer.commit()
            assert Path(str(c.engine.store.path) + '-wal').exists()
        else:
            writer.close(); writer = None
            assert not Path(str(c.engine.store.path) + '-wal').exists()
            assert not Path(str(c.engine.store.path) + '-shm').exists()
    try:
        before = file_inventory(c.profile.data_root)
        opened = []
        connect = sqlite3.connect
        def observe_connect(path, *args, **kwargs):
            opened.append(path)
            return connect(path, *args, **kwargs)
        monkeypatch.setattr(sqlite3, 'connect', observe_connect)
        status, body, _ = http(c)
        after = file_inventory(c.profile.data_root)
        assert after == before
        assert opened == ([':memory:'] if mode == 'rollback' else [])
        if mode == 'rollback':
            assert status == 200
        else:
            assert status == 503 and 'RECEIPT_SNAPSHOT_UNAVAILABLE' in body
            assert 'COMPLETE_VERIFIED_RECEIPT' not in body
        # Test-harness evidence is written only after the GET's inventory check.
        (c.profile.data_root / 'FILESYSTEM_READONLY_PROOF.json').write_text(json.dumps(
            {'mode': mode, 'http_status': status, 'before': before, 'after': after,
             'sqlite_connections': opened, 'equal': after == before}, indent=2), encoding='utf-8')
    finally:
        if writer is not None:
            writer.close()


@pytest.mark.parametrize('bad', ['SECRET-PATH-CANARY', ['not-an-object'], 123])
def test_nested_step_checkpoint_has_fixed_gap_instead_of_connection_failure(receipt_case, bad):
    c = receipt_case
    with c.engine.transaction() as conn:
        steps = json.loads(conn.execute('SELECT steps_json FROM workbench_execution WHERE task_id=?', (c.task_id,)).fetchone()[0])
        steps[0]['checkpoint'] = bad
        conn.execute('UPDATE workbench_execution SET steps_json=? WHERE task_id=?', (json.dumps(steps), c.task_id))
    status, body, _ = http(c)
    assert status == 404 and 'SECRET-' not in body


def test_public_delisting_task_keeps_safe_executor_and_waiting_context(tmp_path):
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'synthetic-ui'})
    task = engine.create({'template': 'delisting', 'source_key': 'ui-compat',
                          'scope': {'skus': ['0001'], 'shops': ['shopee:MY']}})
    engine.register_executor('worker-03', ['delisting'], engine.release)
    token = engine.claim(task['task_id'], 'worker-03')['lease_token']
    from shared_platform.workbench_delisting_receipt import public_projection
    running = public_projection({'task': engine.get(task['task_id']), 'events': engine.store.events(task['task_id'])})
    assert running['task']['worker'] == 'worker-03'
    assert running['task']['last_executor'] == 'worker-03'
    engine.await_domain(task['task_id'], token, label='等待官方回读', reason='PROCESSING C:/SECRET-PRIVATE/token.json',
                        receipt_binding={'path': 'C:/SECRET-PRIVATE/token.json', 'lease_token': token})
    waiting = public_projection({'task': engine.get(task['task_id']), 'events': engine.store.events(task['task_id'])})
    assert waiting['task']['worker'] is None and waiting['task']['last_executor'] == 'worker-03'
    assert waiting['task']['pending_observation']['kind'] == 'observe'
    assert '等待' in waiting['task']['pending_observation']['reason']
    assert waiting['task']['required_action'] is None
    assert 'SECRET-' not in json.dumps(waiting) and token not in json.dumps(waiting)


def test_external_delisting_json_keeps_owner_observation_but_not_private_ref(receipt_case):
    c = receipt_case
    task = c.engine.create({'template': 'delisting', 'source_key': 'external-safe',
                           'scope': {'skus': ['0002'], 'shops': ['shopee:MY']}})
    c.engine.attach_external(task['task_id'], external_id='existing-03', owner='03',
                             observed_at='2026-09-23T12:34:56Z', observed_status='needs_review',
                             evidence_ref='C:/SECRET-PRIVATE/external-result.json')
    with c.engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET result_url=? WHERE task_id=?', ('/catalog', task['task_id']))
    for path in ['/api/orbit/tasks', '/api/orbit/tasks/' + task['task_id']]:
        status, body, _ = http(c, path=path)
        assert status == 200 and 'SECRET-' not in body and 'evidence_ref' not in body
        data = json.loads(body)
        projected = data.get('task') or next(row for row in data['tasks'] if row['task_id'] == task['task_id'])
        assert projected['external_task']['owner'] == '03'
        assert projected['external_task']['observed_status'] == 'needs_review'
        assert projected['external_task']['observed_at'] == '2026-09-23T12:34:56Z'
        assert projected['result_url'] == '/catalog'
        assert projected['execution_state'] == 'external_task'
    assert http(c, path='/api/orbit/tasks/' + task['task_id'] + '/delisting-receipt')[0] == 404


@pytest.mark.parametrize('mode',['complete','partial','missing','unknown'])
def test_actual_receipt_http_chromium_desktop_mobile(receipt_case,mode):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    c=receipt_case
    if mode=='complete':make_completed(c)
    if mode=='missing':
        with c.engine.transaction() as conn:
            conn.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',(json.dumps({'plan':c.refs['plan'],'execution':c.refs['execution']}),c.task_id))
    if mode=='unknown':
        readback=copy.deepcopy(c.readback)
        for row in readback['targets']:row.update(verified=False,current_status=None,read_error='synthetic unknown')
        ref=save(Path(c.refs['readback']['path']),readback)
        with c.engine.transaction() as conn:
            conn.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',(json.dumps({**c.refs,'readback':ref}),c.task_id))
    class Handler(BaseHTTPRequestHandler):
        def _json(self,status,value):
            raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if not handle(self,method='GET',runtime=c.runtime):self._json(404,{'error':'fixture route only'})
        def do_POST(self):raise AssertionError('browser must never mutate')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    node=Path('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
    try:
        completed=subprocess.run([str(node),str(c.profile.root/'tests/browser/delisting_receipt.cjs'),
                                  'http://127.0.0.1:'+str(server.server_port),c.task_id,str(c.profile.data_root),mode],
                                 capture_output=True,text=True,encoding='utf-8',timeout=45)
        assert completed.returncode==0,completed.stdout+completed.stderr
        result=json.loads((c.profile.data_root/'BROWSER_RESULT.json').read_text(encoding='utf-8'))
        assert result['blocked']==[] and result['errors']==[]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
