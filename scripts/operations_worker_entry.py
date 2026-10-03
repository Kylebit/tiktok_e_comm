"""Validate a sealed worker-only Orbit process without activating adapters.

This is a separate entry from operations_web_entry.py. It deliberately opens
no HTTP listener and has no enabled execution path until the worker-to-review
bridge and each business adapter pass isolated end-to-end acceptance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import worker_admission_preflight as admission_preflight


def plan(deployment_path, agent_executable):
    """Construct a read-only, fail-closed worker launch plan."""
    deployment = Path(deployment_path)
    agent = Path(agent_executable)
    result = {
        'ok': False, 'blockers': [], 'worker_started': False,
        'http_listener_started': False, 'activation_available': False,
        'resume_task_ids': [], 'existing_tasks_require_explicit_resume': [],
        'deployment': str(deployment), 'agent_executable': str(agent),
    }
    try:
        if not deployment.is_absolute() or not deployment.is_file():
            raise ValueError('deployment must be an existing absolute JSON file')
        config = json.loads(deployment.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('deployment must be a JSON object')
        code_root = Path(config['code_root'])
        if not code_root.is_absolute() or code_root.resolve(strict=True) != ROOT.resolve():
            result['blockers'].append('ENTRY_CODE_ROOT_MISMATCH')
    except (OSError, ValueError, KeyError, TypeError) as error:
        if not result['blockers']:
            result['blockers'].append('DEPLOYMENT_INVALID')
        result['error'] = str(error)
        return result
    if config.get('execution_mode') != 'worker-only':
        result['blockers'].append('DEPLOYMENT_NOT_WORKER_ONLY')
    if config.get('resume_task_ids') or config.get('auto_resume_old_tasks') or config.get('resume_all'):
        result['blockers'].append('IMPLICIT_RESUME_NOT_ALLOWED')
    if result['blockers']:
        return result

    assessment = admission_preflight.assess(deployment, agent)
    result['assessment'] = assessment
    result['blockers'].extend(assessment['blockers'])
    ledger = assessment.get('ledger') or {}
    admitted = set(ledger.get('same_release_admitted_open_task_ids') or [])
    result['existing_tasks_require_explicit_resume'] = [
        task_id for task_id in ledger.get('same_release_open_task_ids') or []
        if task_id not in admitted
    ]
    result['release'] = assessment.get('release')
    result['admission'] = assessment.get('admission')
    result['ok'] = not result['blockers']
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', required=True, type=Path)
    parser.add_argument('--agent-executable', required=True, type=Path)
    parser.add_argument('--run', action='store_true',
                        help='Reserved; currently fails closed without starting a worker')
    args = parser.parse_args(argv)
    result = plan(args.deployment, args.agent_executable)
    if args.run:
        result['ok'] = False
        result['blockers'].append('WORKER_ACTIVATION_NOT_AVAILABLE')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
