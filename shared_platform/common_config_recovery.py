"""Local-only recovery of a proven initial COMMON config-load failure.

No provider-success receipt is produced. Historical recovery is deliberately
limited to the independently audited d6877dd7 call chain. No HTTP endpoint.
"""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

ERROR = 'Miaoshou local config was not found in the workspace or main project.'
VERSION = 'd6877dd7508406453677fd9112284d38edf430b5'
SOURCES = {
    'modules/miaoshou/client.py': '90c834dbca0eb0d44e675f894bb0b66ad9ef7328a07b29d551421ee6bbcf9cb6',
    'modules/products/release_adapters.py': '2db99036df7352e48e23f4b3445841ebb35267e14479fed4cf13c2380adfc4d5',
    'modules/products/server.py': 'ccc4d3f9d0cc49c920fdf4374b5185a6e8e967abba2ee272ac32e0d5c96da8e3',
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def reconcile_historical_config_failure(*, store, engine, task_id, run_id, source_root, verified_by):
    """Append exact attempt evidence, close the local claim, retain failed run.

    Invoke while the old executor is stopped. Instantiate engine with the old
    task's pinned release identity; this method never changes that identity.
    A subsequent migration may cancel the queued task and reuse the SAME release
    database, plan and run; attempt 2 is a new operation, not replay of attempt 1.
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
            operation = 'publication-common:' + plan_row['plan_id']
            domain = connection.execute('SELECT * FROM workbench_domain_operations WHERE operation_id=?', (operation,)).fetchone()
            from shared_platform.internal_catalog_sku import internal_sku
            exact_shops = {s for s in stage.get('marketplace_targets', []) if s!='miaoshou:COMMON'}
            if (task['template'] != 'publication' or scope.get('offer_id') != plan['product_id']
                or plan.get('targets') != ['miaoshou:COMMON'] or not stage
                or scope.get('skus') != [internal_sku(plan.get('seller_sku'))]
                or {s for s in scope.get('shops',[]) if s!='miaoshou:COMMON'} != exact_shops
                or not domain or domain['owner_task_id'] != task_id
                or row['status'] != 'FAILED' or row['attempts'] != 1
                or row['error'] != ERROR or row['external_id']):
                raise ValueError('exact initial COMMON config failure required')
            for table in ('release_target_submissions', 'release_target_readbacks', 'release_target_repairs', 'release_target_retry_operations'):
                if db.execute(f'SELECT 1 FROM {table} WHERE run_id=? AND target_label=?', (run_id, 'miaoshou:COMMON')).fetchone():
                    raise ValueError('COMMON external or repair evidence prevents local recovery')
            proof = {'schema_version':'common-local-config-not-dispatched/v1',
                'source':'independently_verified_local_call_chain', 'verified_by':verified_by,
                'code_version':VERSION, 'source_sha256_lf':SOURCES,
                'task_id':task_id, 'run_id':run_id, 'plan_id':plan_row['plan_id'],
                'payload_digest':plan_row['payload_digest'], 'attempt':1,
                'original_error':ERROR, 'request_attempted':False,
                'external_write_count':0, 'external_writes_performed':[],
                'write_outcome':'not_dispatched', 'call_boundary':'post_open._load_config before initial COMMON detail request'}
            encoded = _json(proof)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            receipt_ref = 'local-not-dispatched:' + digest
            previous = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=1', (run_id, 'miaoshou:COMMON')).fetchone()
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
                    (run_id, 'miaoshou:COMMON', 1, encoded, digest, datetime.now(timezone.utc).isoformat()))
        # ReleaseStore committed first: interrupted recovery can resume idempotently.
        connection.execute("UPDATE workbench_domain_operations SET state='completed',readback_ref=? WHERE operation_id=?", (receipt_ref, operation))
        connection.execute('DELETE FROM workbench_domain_locks WHERE operation_id=?', (operation,))
        connection.execute('UPDATE workbench_execution SET external_started=0 WHERE task_id=?', (task_id,))
        engine._state(connection, task_id, 'queued')
        checkpoint = json.loads(task['checkpoint_json'])
        checkpoint['common_config_recovery'] = {'local_evidence_ref':receipt_ref,
            'plan_id':plan_row['plan_id'],'run_id':run_id,'failure_attempt':1}
        connection.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?', (_json(checkpoint),task_id))
        engine._event(connection, task_id, 'domain_operation_not_dispatched', {'operation_id':operation,'local_evidence_ref':receipt_ref,'proof':proof})
        return proof
