#!/usr/bin/env python3
"""Read Product Center's current stage contract without creating local state."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from shared_platform.publication_r3_image_bridge import DEFAULT_STAGE_BASE_URL, request_publication_stage

def classify_workflow(*, offer_id, first_review=None, localized=None, handoff=None):
    """Legacy sidecars cannot establish current publication authority."""
    return {'schema_version': 'product-publication-workflow-status/v2', 'offer_id': str(offer_id),
        'stage': 'RECONCILIATION_REQUIRED', 'requires_reconciliation': True,
        'reason': 'CURRENT_STAGE_CONTRACT_REQUIRED', 'next_command': None,
        'approval_recorded': False, 'miaoshou_verified': False}

def status_offer(offer_id: str, *, base_url=DEFAULT_STAGE_BASE_URL) -> dict:
    if type(offer_id) is not str or not re.fullmatch(r'[0-9]{1,32}', offer_id):
        raise ValueError('offer_id must contain 1 to 32 ASCII digits')
    status, view = request_publication_stage('/api/product-workspace/publication-stages?' + urlencode({'offer_id': offer_id}), base_url=base_url)
    if status != 200 or view.get('ok') is not True or view.get('schema_version') != 'publication-stages/v1' or view.get('offer_id') != offer_id:
        stage = view.get('stage') or 'RECONCILIATION_REQUIRED'
        preparation = None
        if stage == 'SECOND_ROUND_REQUIRED':
            preparation = f'.venv\\Scripts\\python.exe skills\\prepare-product-images\\scripts\\prepare_product_images.py --offer-id {offer_id}'
        return {'schema_version': 'product-publication-workflow-status/v2', 'offer_id': offer_id,
            'stage': stage, 'reason': view.get('error') or 'INVALID_STAGE_RESPONSE', 'next_action': view.get('next_action'),
            'requires_reconciliation': stage not in {'FIRST_ROUND_REQUIRED', 'SECOND_ROUND_REQUIRED'},
            'next_command': preparation, 'http_status': status}
    common, market = view['common'], view.get('marketplace') or {}
    preparation = f'.venv\\Scripts\\python.exe skills\\publish-approved-product\\scripts\\prepare_publication_execution.py --offer-id {offer_id} --base-url {base_url}'
    stage = 'COMMON_' + common['status']
    command = preparation
    if common['status'] == 'APPROVAL_REQUIRED':
        stage = 'COMMON_TECHNICAL_ADMISSION_BLOCKED'
        command = None
    elif common['status'] == 'READY_TO_SYNC':
        from shared_platform.publication_common_write_admission import inspect_common_write_admission
        admission = inspect_common_write_admission(common.get('plan'))
        if admission['status'] != 'READY':
            stage = 'COMMON_TECHNICAL_ADMISSION_BLOCKED'
            command = None
        else:
            plan = common['plan']
            command += f" --execute-miaoshou --confirm-miaoshou-write --plan-id {plan['plan_id']} --confirmation-token {plan['confirmation_token']}"
    elif common['status'] == 'VERIFIED':
        stage = 'MARKETPLACE_' + market.get('status', 'NOT_FROZEN')
        command += ' --finalize-release-handoff'
        if market.get('status') == 'APPROVAL_BINDING_REQUIRED':
            command = preparation + ' --resume-approval-binding --plan-id ' + market['plan']['plan_id']
        elif market.get('status') in {'READY_TO_PUBLISH', 'PARTIAL'}:
            ready = [row for row in market.get('platforms', []) if row['next_action'] == 'EXECUTE_APPROVED_PLATFORM']
            command = None
            if ready:
                command = (f'.venv\\Scripts\\python.exe skills\\publish-approved-product\\scripts\\product_center_publication.py'
                    f" --offer-id {offer_id} --plan-id {market['plan']['plan_id']} --platform {ready[0]['platform'].lower()} --base-url {base_url} --execute")
        elif market.get('status') in {'BLOCKED', 'RECONCILIATION_REQUIRED', 'PROCESSING', 'READBACK_ONLY',
                                    'PUBLISHED', 'FAILED', 'SUPERSEDED'}:
            command = None
    if common['status'] in {'RECONCILIATION_REQUIRED', 'RUNNING', 'BLOCKED'}:
        command = None
    return {'schema_version': 'product-publication-workflow-status/v2', 'offer_id': offer_id, 'stage': stage,
        'miaoshou_verified': common['status'] == 'VERIFIED',
        'approval_recorded': bool(market.get('approval') or (market.get('plan') or {}).get('approval')),
        'requires_reconciliation': 'RECONCILIATION_REQUIRED' in stage,
        'common': common, 'marketplace': market, 'next_command': command}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offer-id', required=True)
    parser.add_argument('--base-url', default=DEFAULT_STAGE_BASE_URL)
    args = parser.parse_args(argv)
    try:
        result = status_offer(args.offer_id, base_url=args.base_url)
    except (ValueError, OSError) as error:
        result = {'schema_version': 'product-publication-workflow-status/v2', 'offer_id': args.offer_id,
                  'stage': 'RECONCILIATION_REQUIRED', 'reason': str(error), 'requires_reconciliation': True, 'next_command': None}
    # stdout may be a Windows legacy code page even when the HTTP payload is UTF-8.
    # JSON escapes keep the CLI machine-readable without changing parsed values.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 2 if result.get('requires_reconciliation') else 0

if __name__ == '__main__':
    raise SystemExit(main())
