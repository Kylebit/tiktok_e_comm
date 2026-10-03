"""Explicit supply refresh: preflight, synthetic staging, or captured local apply.

No live account access, implicit Home, scheduler update, or production write.
Captured sources are validated locally and applied only to the profile output root.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from shared_platform.capability_runtime import checked_path, file_content, digest
from modules.sourcing.image_generation_checkpoint import business_lock, atomic_json, CheckpointRecoveryRequired

SCRIPT_DIR = 'domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts/'
SOURCE_FILES = tuple(SCRIPT_DIR + name + '.py' for name in (
    'refresh_supply_chain', 'captured_refresh', 'seaya_capture', 'pull_order_demand', 'apply_inventory_snapshot', 'apply_order_demand', 'validate_inventory_snapshot')) + (
    'domains/supply_chain_operations/order_demand.py', 'domains/supply_chain_operations/demand_trend.py',
    'shared_platform/capability_runtime.py', 'modules/sourcing/image_generation_checkpoint.py',
    'domains/supply_chain_operations/captured_serving.py','core/static_files.py',
)
REGIONS = ('MY', 'TH', 'VN', 'PH')
WAREHOUSES = {'MY': 'MY8803', 'TH': 'TH8806', 'VN': 'VN8805', 'PH': 'PH8807'}


class RefreshBlocked(ValueError):
    def __init__(self, state, detail): self.state, self.detail = state, detail; super().__init__(detail)


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'safe.directory='+str(root), '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()


def source_hashes(root):
    return {name: hashlib.sha256(file_content(checked_path(root, name))).hexdigest() for name in SOURCE_FILES}


def preflight(root, profile_path):
    if not root.is_dir(): raise RefreshBlocked('ROOT_MISSING', 'explicit project root does not exist')
    if root.resolve() != ROOT: raise RefreshBlocked('ROOT_MISMATCH', 'project root must match this source launcher')
    profile = json.loads(checked_path(profile_path.parent, profile_path.name).read_text(encoding='utf-8'))
    fields = {'schema', 'project_root', 'branch', 'source_commit', 'source_sha256', 'artifact_root', 'stable_release'}
    if profile.get('schema') == 'supply-chain-refresh-profile/v2': fields.add('captured')
    if 'runtime_root' in profile and profile.get('schema') == 'supply-chain-refresh-profile/v2': fields.add('runtime_root')
    if set(profile) != fields or profile['schema'] not in {'supply-chain-refresh-profile/v1', 'supply-chain-refresh-profile/v2'} or type(profile['stable_release']) is not bool:
        raise RefreshBlocked('PROFILE_INVALID', 'explicit non-secret refresh profile fields required')
    if profile['schema']=='supply-chain-refresh-profile/v2' and (
        type(profile['captured']) is not dict or set(profile['captured'])!={'sources','targets','output_root'}):
        raise RefreshBlocked('PROFILE_INVALID','captured sources, targets and output_root fields required')
    if Path(profile['project_root']).resolve() != root.resolve(): raise RefreshBlocked('ROOT_MISMATCH', 'profile root differs')
    if git(root, 'branch', '--show-current') != profile['branch']: raise RefreshBlocked('BRANCH_MISMATCH', 'profile branch differs')
    if git(root, 'rev-parse', 'HEAD') != profile['source_commit']: raise RefreshBlocked('SOURCE_MISMATCH', 'profile must pin exact current HEAD')
    missing = [p for p in SOURCE_FILES if not checked_path(root, p).is_file()]
    if missing: raise RefreshBlocked('DEPENDENCIES_MISSING', ', '.join(missing))
    if source_hashes(root) != profile['source_sha256']: raise RefreshBlocked('SOURCE_MISMATCH', 'explicit source file hashes differ')
    if git(root, 'status', '--porcelain=v1', '-uall'): raise RefreshBlocked('SOURCE_DIRTY', 'freeze candidate source before refresh preflight')
    relative = Path(profile['artifact_root'])
    if relative.is_absolute() or len(relative.parts) < 2 or relative.parts[0] != 'outputs':
        raise RefreshBlocked('ARTIFACT_SCOPE_INVALID', 'outputs must be under an explicit project outputs subdirectory')
    from domains.supply_chain_operations.captured_serving import capture_artifact_root
    capture_config={'artifact_root':profile['artifact_root'],'output_root':profile['captured']['output_root']} if profile['schema']=='supply-chain-refresh-profile/v2' else None
    if capture_config is None:
        artifact=checked_path(root,relative)
    else:
        if 'runtime_root' in profile:capture_config['runtime_root']=profile['runtime_root']
        try:_,artifact=capture_artifact_root(root,capture_config)
        except (ValueError,OSError,TypeError):raise RefreshBlocked('ARTIFACT_SCOPE_INVALID','explicit capture runtime root must be an existing local directory outside the source checkout')
    return profile, artifact


def module(name):
    path = checked_path(ROOT, SCRIPT_DIR+name+'.py')
    spec = importlib.util.spec_from_file_location('u04_'+name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


class FixtureRequests:
    """Synthetic responses below the unchanged paginated collector loops."""
    def __init__(self, payload): self.payload, self.calls = payload, []
    def tiktok(self, endpoint, token, query, body):
        region = query['shop_cipher']; rows = self.payload['countries'][region]['tiktok']
        selected = [r for r in rows if body['create_time_ge'] <= r['create_time'] < body['create_time_lt']]
        offset = int(query.get('page_token', '0')); page = selected[offset:offset+1]
        self.calls.append({'region': region, 'platform': 'TikTok', 'endpoint': endpoint, 'offset': offset, 'rows': len(page)})
        return {'code': 0, 'data': {'orders': page, 'next_page_token': str(offset+1) if offset+1 < len(selected) else ''}}
    def shopee(self, endpoint, shop, token, params):
        region = REGIONS[shop]; rows = self.payload['countries'][region]['shopee']
        if endpoint.endswith('get_order_list'):
            selected = [r for r in rows if params['time_from'] <= r['create_time'] < params['time_to']]
            offset = int(params.get('cursor', '0')); page = selected[offset:offset+1]
            result = {'order_list': [{'order_sn': r['order_sn']} for r in page], 'more': offset+1 < len(selected),
                      'next_cursor': str(offset+1) if offset+1 < len(selected) else ''}
        else:
            selected = set(params['order_sn_list'].split(','))
            result = {'order_list': [r for r in rows if r['order_sn'] in selected]}
            if self.payload.get('omit_detail') == region: result['order_list'] = result['order_list'][:-1]
        self.calls.append({'region': region, 'platform': 'Shopee', 'endpoint': endpoint, 'rows': len(result['order_list'])})
        return {'response': result}


def stage_fixture(payload):
    if payload.get('schema') != 'supply-chain-refresh-fixture/v1' or payload.get('synthetic') is not True:
        raise RefreshBlocked('FAKE_SOURCE_REQUIRED', 'only explicitly synthetic fixture input is accepted')
    inventory, orders, inbound = payload['inventory'], payload['orders'], payload['inbound']
    validator = module('validate_inventory_snapshot'); errors = validator.validate_payload(inventory)
    if errors: raise RefreshBlocked('BLOCKED_IDENTITY', '; '.join(errors))
    for source in (inventory, orders, inbound):
        coverage = source.get('coverage', {})
        if set(coverage) != set(REGIONS) or any(c.get('complete') is not True or type(c.get('pagesRead')) is not int
                or c['pagesRead'] < 1 or c['pagesRead'] != c.get('pagesExpected') for c in coverage.values()):
            raise RefreshBlocked('BLOCKED_COVERAGE', 'four countries and complete source pages required')
        if datetime.fromisoformat(source['capturedAt']).tzinfo is None: raise RefreshBlocked('CLOCK_INVALID', 'source clock must include timezone')
    apply_inventory, apply_orders = module('apply_inventory_snapshot'), module('apply_order_demand')
    grouped = apply_inventory.aggregate_snapshot(inventory)
    for region in REGIONS:
        if payload['seed']['config'][region]['warehouse'] != WAREHOUSES[region]: raise RefreshBlocked('BLOCKED_IDENTITY', 'country warehouse differs')
        expected = {sku: fact['inbound'] for sku, fact in grouped.get(region, {}).items() if fact['inbound']}
        actual = {}; seen = set()
        for batch in inbound['regions'][region]['batches']:
            if not batch['batchId'] or batch['batchId'] in seen: raise RefreshBlocked('BLOCKED_IDENTITY', 'batch identity missing or duplicated')
            seen.add(batch['batchId'])
            if not batch['skuQuantities'] or any(type(n) is not int or n < 0 for n in batch['skuQuantities'].values()):
                raise RefreshBlocked('BLOCKED_INBOUND', 'exact integer batch SKU allocation required')
            if sum(batch['skuQuantities'].values()) != batch['totalUnits']: raise RefreshBlocked('BLOCKED_INBOUND', 'batch total differs')
            for sku, amount in batch['skuQuantities'].items(): actual[sku] = actual.get(sku, 0)+amount
        if {k: v for k, v in actual.items() if v} != expected: raise RefreshBlocked('BLOCKED_INBOUND', 'batch and inventory inbound do not reconcile')
    pull = module('pull_order_demand'); transport = FixtureRequests(orders)
    captured = datetime.fromisoformat(orders['capturedAt']); end = int(captured.timestamp()); days = orders['days']; start = end-days*86400
    snapshot = {'schemaVersion': 'order_demand_snapshot_v1', 'capturedAt': orders['capturedAt'], 'days': days, 'countries': {}}
    for index, region in enumerate(REGIONS):
        tk, _ = pull.pull_tiktok_region('synthetic', region, start, end, requester=transport.tiktok)
        sp, _ = pull.pull_shopee_region(index, 'synthetic', start, end, requester=transport.shopee)
        snapshot['countries'][region] = {}
        for platform, raw, aggregate in [('tiktok', tk, pull.aggregate_tiktok_orders), ('shopee', sp, pull.aggregate_shopee_orders)]:
            rows, evidence = aggregate(raw, region)
            if any(evidence.get(name, 0) for name in ['orders_invalid', 'item_lines_unresolved', 'item_lines_invalid_quantity']):
                raise RefreshBlocked('BLOCKED_ORDER_DATA', 'unresolved or invalid order identity/quantity')
            snapshot['countries'][region][platform] = pull.finalize_order_snapshot(rows, region=region,
                platform='TikTok' if platform=='tiktok' else 'Shopee', captured_at=captured, days=days, evidence=evidence)
            seed_skus = {r['sku'] for r in payload['seed']['countries'][region]}
            if set(rows)-seed_skus: raise RefreshBlocked('FIXTURE_PRESENTATION_MISSING', 'seed every fixture SKU; image downloads are forbidden')
    seed = json.loads(json.dumps(payload['seed']))
    applied = apply_inventory.apply_inventory(seed, inventory)
    applied = apply_orders.apply_snapshot(applied, snapshot)
    return {'data': applied, 'inbound': inbound, 'orders': snapshot, 'fake_requests': transport.calls,
        'clocks': {'inventory': applied['snapshotDate'], 'orders': applied['orderDemandCapturedAt'], 'inbound': inbound['capturedAt']},
        'imports': {name: str(value.__file__) for name, value in [('validator', validator), ('inventory', apply_inventory), ('orders', apply_orders), ('collector', pull)]},
        'auth_modules_loaded': [name for name in sys.modules if name.startswith(('core.auth', 'core.config', 'modules.shopee.auth'))]}


def execute(root, profile_path, fixture_path=None):
    profile, artifact = preflight(root, profile_path)
    # Scope is the actual output root, never the profile's name or run UUID.
    lease_root=Path(profile.get('runtime_root',root)).resolve()
    scope = digest({'root': str(lease_root).casefold(), 'artifact': str(artifact).casefold()})
    try:
        with business_lock(artifact, scope, timeout=0):
            preflight(root, profile_path)
            if fixture_path is None:
                return {'ok': True, 'state': 'DRY_RUN', 'source_commit': profile['source_commit'], 'branch': profile['branch'],
                    'artifact_root': str(artifact), 'steps': ['validate complete inventory and inbound pages', 'pull_order_demand.py --days 31',
                        'apply_inventory_snapshot.py', 'apply_order_demand.py', 'verify independent clocks and stage for review'],
                    'scheduler_ready': False, 'stable_root_gate': 'coordinator must accept stable root, profile and target task before changing existing automation',
                    'network_reads': 0, 'auth_writes': 0, 'business_writes': 0, 'checkpoint_writes': 0, 'lease_file_only': True}
            source = checked_path(artifact, fixture_path)
            payload = json.loads(source.read_text(encoding='utf-8'))
            if payload.get('synthetic') is not True: raise RefreshBlocked('FAKE_SOURCE_REQUIRED', 'no live source execution')
            request_digest = digest({'profile': profile, 'fixture': payload})
            checkpoint = artifact/'checkpoint.json'
            previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else None
            if previous and previous['state'] == 'COMPLETE' and previous['request_digest'] == request_digest:
                output = checked_path(artifact, previous['output'])
                if hashlib.sha256(output.read_bytes()).hexdigest() != previous['output_sha256']:
                    raise RefreshBlocked('CHECKPOINT_OUTPUT_CHANGED', 'preserve existing checkpoint and inspect changed output')
                return {**previous, 'ok': True, 'state': 'REUSED', 'network_reads': 0, 'auth_writes': 0, 'business_writes': 0}
            if previous and previous['state'] == 'RUNNING' and previous['request_digest'] != request_digest:
                raise RefreshBlocked('RECOVERY_SCOPE_MISMATCH', 'incomplete checkpoint belongs to another frozen fixture/profile')
            run_id = uuid.uuid4().hex; run_dir = checked_path(artifact, run_id); run_dir.mkdir()
            record = {'schema': 'supply-chain-refresh-run/v1', 'run_id': run_id, 'pid': os.getpid(), 'state': 'RUNNING',
                'request_digest': request_digest, 'source_commit': profile['source_commit'], 'synthetic': True,
                'recovered_from': previous['run_id'] if previous and previous['state']=='RUNNING' else None}
            atomic_json(checkpoint, record); atomic_json(run_dir/'started.json', record)
            try:
                if payload.get('wait_for'):
                    gate = checked_path(artifact, payload['wait_for']); deadline = time.monotonic()+30
                    while not gate.is_file():
                        if time.monotonic() >= deadline: raise RefreshBlocked('FIXTURE_GATE_TIMEOUT', 'synthetic source gate not released')
                        time.sleep(.05)
                result = stage_fixture(payload)
                output = run_dir/'staged.json'; atomic_json(output, result)
                record.update(state='COMPLETE', output=output.relative_to(artifact).as_posix(), output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                    fake_request_count=len(result['fake_requests']), clocks=result['clocks'])
                atomic_json(run_dir/'finished.json', record); atomic_json(checkpoint, record)
            except Exception as error:
                record.update(state='FAILED', error=str(error)); atomic_json(run_dir/'finished.json', record); atomic_json(checkpoint, record)
                raise
            return {**record, 'ok': True, 'network_reads': 0, 'auth_writes': 0, 'business_writes': 0, 'dashboard_source_writes': 0}
    except CheckpointRecoveryRequired as error:
        raise RefreshBlocked('ALREADY_RUNNING', 'another writer holds this exact artifact root; checkpoint retained') from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True); parser.add_argument('--profile', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True); modes.add_argument('--dry-run', action='store_true'); modes.add_argument('--fake-source', type=Path)
    modes.add_argument('--captured-stage', action='store_true'); modes.add_argument('--captured-apply', metavar='STAGE_DIGEST')
    args = parser.parse_args()
    try:
        if args.captured_stage or args.captured_apply:
            profile, artifact = preflight(args.project_root, args.profile)
            result = module('captured_refresh').execute(profile, artifact, module, args.captured_apply)
        else: result = execute(args.project_root, args.profile, args.fake_source)
    except RefreshBlocked as error: result = {'ok': False, 'state': error.state, 'detail': error.detail}
    except CheckpointRecoveryRequired as error: result = {'ok': False, 'state': 'ALREADY_RUNNING', 'detail': 'another writer holds this artifact root; preserve the current apply journal'}
    except RuntimeError as error: result = {'ok': False, 'state': 'BLOCKED_COVERAGE', 'detail': str(error)}
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error: result = {'ok': False, 'state': 'INPUT_OR_DEPENDENCY_ERROR', 'detail': str(error)}
    print(json.dumps(result, ensure_ascii=False)); return 0 if result['ok'] else 1


if __name__ == '__main__': raise SystemExit(main())
