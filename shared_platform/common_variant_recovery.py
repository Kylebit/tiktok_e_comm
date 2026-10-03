"""Local-only recovery of a proven COMMON variant-key failure before edit.

No provider-success receipt is produced. Historical recovery is deliberately
limited to the independently audited 8443a9df call chain. No HTTP endpoint.
"""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

ERROR = "'th3655-58*22cm*4pcs'"
VERSION = '8443a9df38c149f15f4fe27e282bc4c6e2449b80'
SOURCES = {
    'modules/miaoshou/client.py': '1f8861af631074a79e16fe254b710a02b5a59a38193e92eab952c8346d1bd07d',
    'modules/products/release_adapters.py': 'fa094df8a911878dbb3f984c7b57922aab16274546c2f8f72be5a88346ce99ba',
    'modules/products/server.py': '6e9345fc2fc8bb46dde59eb078eeaceb30cbbc33e2a57fce22534526aed5b0bc',
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def reconcile_historical_variant_failure(*, store, engine, task_id, run_id, source_root, verified_by, detail_path, detail_sha256):
    """Append exact attempt evidence, close the local claim, retain failed run.

    Invoke while the old executor is stopped. Instantiate engine with the old
    task's pinned release identity; this method never changes that identity.
    A subsequent migration may cancel the queued task and reuse the SAME release
    database, plan and run; attempt 3 is a new operation, not replay of attempt 2.
    """
    if not isinstance(verified_by, str) or not verified_by.strip():
        raise ValueError('independent verifier identity required')
    root = Path(source_root)
    for name, expected in SOURCES.items():
        if hashlib.sha256((root/name).read_text(encoding='utf-8').encode()).hexdigest() != expected:
            raise ValueError('historical config failure source contract changed')
    with engine.transaction() as connection:
        task = engine._row(connection, task_id)
        engine._require_version(task)
        if json.loads(task['version_json']).get('code_version') != VERSION:
            raise ValueError('historical recovery requires exact audited runtime version')
        with store._transaction() as db:
            store._require_active_run(db, run_id)
            row = store._target_for_update(db, run_id, 'miaoshou:COMMON')
            plan_row = db.execute('SELECT p.* FROM release_plans p JOIN release_runs r ON r.plan_id=p.plan_id WHERE r.run_id=?', (run_id,)).fetchone()
            plan = json.loads(plan_row['payload_json'])
            scope = json.loads(task['scope_json'])
            stage = plan.get('r3_stage_binding') or {}
            operation = 'publication-common:' + plan_row['plan_id'] + ':attempt:2'
            domain = connection.execute('SELECT * FROM workbench_domain_operations WHERE operation_id=?', (operation,)).fetchone()
            from shared_platform.internal_catalog_sku import internal_sku
            exact_shops = {s for s in stage.get('marketplace_targets', []) if s!='miaoshou:COMMON'}
            if (task['template'] != 'publication' or scope.get('offer_id') != plan['product_id']
                or plan.get('targets') != ['miaoshou:COMMON'] or not stage
                or scope.get('skus') != [internal_sku(plan.get('seller_sku'))]
                or {s for s in scope.get('shops',[]) if s!='miaoshou:COMMON'} != exact_shops
                or not domain or domain['owner_task_id'] != task_id
                or row['status'] != 'FAILED' or row['attempts'] != 2
                or row['error'] != ERROR or row['external_id']):
                raise ValueError('exact second COMMON variant failure required')
            for table in ('release_target_submissions', 'release_target_readbacks', 'release_target_repairs', 'release_target_retry_operations'):
                if db.execute(f'SELECT 1 FROM {table} WHERE run_id=? AND target_label=?', (run_id, 'miaoshou:COMMON')).fetchone():
                    raise ValueError('COMMON external or repair evidence prevents local recovery')
            replay = _verify_local_replay(root, plan, detail_path, detail_sha256)
            proof = {'schema_version':'common-local-variant-key-not-dispatched/v1',
                'source':'independently_verified_local_call_chain', 'local_replay':replay, 'verified_by':verified_by,
                'code_version':VERSION, 'source_sha256_lf':SOURCES,
                'task_id':task_id, 'run_id':run_id, 'plan_id':plan_row['plan_id'],
                'payload_digest':plan_row['payload_digest'], 'attempt':2,
                # Legacy domain field means the commerce/edit request, not the
                # successful preceding read-only detail request.
                'original_error':ERROR, 'request_attempted':False, 'edit_request_attempted':False, 'readonly_request_count':1,
                'external_write_count':0, 'external_writes_performed':[],
                'write_outcome':'not_dispatched', 'call_boundary':'_r3_common_sku_logistics before COMMON edit request'}
            encoded = _json(proof)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            receipt_ref = 'local-not-dispatched:' + digest
            previous = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=2', (run_id, 'miaoshou:COMMON')).fetchone()
            if previous and (previous['evidence_json'] != encoded or previous['evidence_digest'] != digest):
                raise ValueError('existing failed attempt evidence is immutable')
            if domain['state'] == 'completed':
                if domain['readback_ref'] != receipt_ref:
                    raise ValueError('domain already closed with different outcome')
                return proof
            if domain['state'] != 'inflight' or task['state'] != 'reconciliation_required':
                raise ValueError('original unresolved task and domain claim required')
            if not previous:
                db.execute('INSERT INTO release_target_failure_events VALUES(?,?,?,?,?,?)',
                    (run_id, 'miaoshou:COMMON', 2, encoded, digest, datetime.now(timezone.utc).isoformat()))
        # ReleaseStore committed first: interrupted recovery can resume idempotently.
        connection.execute("UPDATE workbench_domain_operations SET state='completed',readback_ref=? WHERE operation_id=?", (receipt_ref, operation))
        connection.execute('DELETE FROM workbench_domain_locks WHERE operation_id=?', (operation,))
        connection.execute('UPDATE workbench_execution SET external_started=0 WHERE task_id=?', (task_id,))
        engine._state(connection, task_id, 'queued')
        checkpoint = json.loads(task['checkpoint_json'])
        checkpoint['common_config_recovery'] = {'local_evidence_ref':receipt_ref,
            'plan_id':plan_row['plan_id'],'run_id':run_id,'failure_attempt':2}
        connection.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?', (_json(checkpoint),task_id))
        engine._event(connection, task_id, 'domain_operation_not_dispatched', {'operation_id':operation,'local_evidence_ref':receipt_ref,'proof':proof})
        return proof


def _verify_local_replay(root, payload, detail_path, detail_sha256):
    import importlib.util
    raw = Path(detail_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != detail_sha256:
        raise ValueError('official detail evidence hash differs')
    response = json.loads(raw)
    spec = importlib.util.spec_from_file_location('historical_variant_adapter', root/'modules/products/release_adapters.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    calls = []
    def only_detail(path, body):
        calls.append(path)
        if path != module.MIAOSHOU_COMMON_DETAIL_PATH or body != {'commonCollectBoxDetailId':int(payload['product_id'])}:
            raise ValueError('historical replay reached a mutation or changed identity')
        return response
    try:
        module.write_miaoshou_common_from_plan(payload, post=only_detail)
    except KeyError as error:
        import traceback
        frames=traceback.extract_tb(error.__traceback__)
        if str(error) != ERROR or len(calls)!=1 or frames[-1].name != '_r3_common_sku_logistics':
            raise ValueError('historical local failure did not reproduce exactly') from error
        return {'detail_sha256':detail_sha256,'detail_calls':1,'edit_calls':0,'failed_function':frames[-1].name}
    raise ValueError('historical local failure did not reproduce')
