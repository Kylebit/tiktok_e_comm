"""Inspect a pinned Orbit worker candidate without starting it or writing its ledger.

The deployment JSON is the existing sealed deployment identity. This command
does not convert a web-only deployment into a worker deployment.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared_platform.operations_launch import preflight_deployment
from shared_platform.operations_runtime import runtime_manifest


REQUIRED_TABLES = frozenset({
    'workbench_tasks', 'workbench_execution', 'workbench_external_tasks',
    'workbench_domain_operations',
})
OPEN_STATES = frozenset({'queued', 'running', 'waiting_user', 'waiting_domain',
                         'failed', 'reconciliation_required'})


def _release(config):
    return {'code_version': config['code_version'], 'environment': 'stable',
            'manifest_digest': config['manifest_digest']}


def _instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('task/admission timestamp lacks timezone')
    return parsed


def _ledger_snapshot(database: Path, release: dict):
    """Use SQLite's read-only URI; never instantiate WorkbenchEngine."""
    uri = database.resolve(strict=True).as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        tables = {row['name'] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise ValueError('required operations ledger tables missing: ' + ','.join(missing))
        admission = {'record_exists': False, 'cutoff_at': None}
        if 'workbench_worker_admissions' in tables:
            for row in conn.execute('SELECT version_json, cutoff_at FROM workbench_worker_admissions'):
                if json.loads(row['version_json']) == release:
                    admission = {'record_exists': True, 'cutoff_at': row['cutoff_at']}
                    _instant(row['cutoff_at'])
                    break
        rows = conn.execute('''
            SELECT e.task_id, e.state, e.version_json, t.created_at,
                   x.task_id IS NOT NULL AS is_external
            FROM workbench_execution AS e
            JOIN workbench_tasks AS t ON t.task_id=e.task_id
            LEFT JOIN workbench_external_tasks AS x ON x.task_id=e.task_id
            ORDER BY e.task_id
        ''').fetchall()
        same = [row for row in rows if json.loads(row['version_json']) == release]
        open_rows = [row for row in same if row['state'] in OPEN_STATES]
        admitted = ([row for row in open_rows
                     if _instant(row['created_at']) >= _instant(admission['cutoff_at'])]
                    if admission['record_exists'] else [])
        unresolved = conn.execute(
            "SELECT COUNT(*) FROM workbench_domain_operations WHERE state<>'completed'"
        ).fetchone()[0]
        external = conn.execute('SELECT COUNT(*) FROM workbench_external_tasks').fetchone()[0]
    return {
        'admission': admission,
        'ledger': {
            'same_release_task_count': len(same),
            'same_release_open_task_ids': [row['task_id'] for row in open_rows],
            'same_release_admitted_open_task_ids': [row['task_id'] for row in admitted],
            'same_release_external_task_count': sum(bool(row['is_external']) for row in same),
            'external_task_count': external,
            'unresolved_domain_operation_count': unresolved,
        },
    }


def assess(deployment_path, agent_executable):
    """Return machine-readable checks and blockers; make no task or provider calls."""
    result = {'ok': False, 'worker_started': False, 'blockers': [],
              'deployment': str(deployment_path), 'agent_executable': str(agent_executable),
              'admission': {'record_exists': None, 'cutoff_at': None}, 'ledger': None}
    deployment = Path(deployment_path)
    try:
        if not deployment.is_absolute() or not deployment.is_file():
            raise ValueError('deployment must be an existing absolute JSON file')
        config = json.loads(deployment.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('deployment must be a JSON object')
        release = _release(config)
        root = Path(config['code_root'])
        result['execution_mode'] = config.get('execution_mode')
        result['release'] = release
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['blockers'].append('DEPLOYMENT_INVALID')
        result['error'] = str(error)
        return result

    if result['execution_mode'] != 'worker-only':
        result['blockers'].append('DEPLOYMENT_NOT_WORKER_ONLY')
    agent = Path(agent_executable)
    if not agent.is_absolute() or not agent.is_file():
        result['blockers'].append('AGENT_EXECUTABLE_INVALID')
    try:
        paths = preflight_deployment(config, root)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        result['blockers'].append('DEPLOYMENT_PREFLIGHT_FAILED')
        result['error'] = str(error)
        return result
    try:
        if runtime_manifest(root) != config['manifest_digest']:
            result['blockers'].append('MANIFEST_MISMATCH')
    except (OSError, ValueError, KeyError) as error:
        result['blockers'].append('MANIFEST_CHECK_FAILED')
        result['error'] = str(error)
    try:
        result.update(_ledger_snapshot(paths['operations_data_root'] / 'tasks.db', release))
        if result['ledger']['unresolved_domain_operation_count']:
            result['blockers'].append('UNRESOLVED_DOMAIN_OPERATIONS')
    except (OSError, ValueError, KeyError, sqlite3.DatabaseError) as error:
        result['blockers'].append('LEDGER_READ_FAILED')
        result['ledger_error'] = str(error)
    result['ok'] = not result['blockers']
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', required=True, type=Path)
    parser.add_argument('--agent-executable', required=True, type=Path)
    args = parser.parse_args(argv)
    result = assess(args.deployment, args.agent_executable)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
