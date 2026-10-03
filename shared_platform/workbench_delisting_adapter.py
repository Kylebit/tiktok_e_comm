"""Exact-scope bridge to the retained delisting Skill, with durable no-replay state."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
from pathlib import Path


def _module(root):
    path = root / 'skills/delist-products-by-sku/scripts/delist_products_by_sku.py'
    spec = importlib.util.spec_from_file_location('orbit_task_delisting_skill', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _save(path, value, *, root):
    from shared_platform.immutable_approval_files import require_local_path
    require_local_path(path, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    require_local_path(path, root=root)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    with path.open('xb') as stream:
        stream.write(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def _read(ref, *, root):
    from shared_platform.immutable_approval_files import require_local_path
    if require_local_path(Path(ref['path']), root=root) is None:
        raise ValueError('frozen delisting artifact missing')
    raw = Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('frozen delisting artifact changed')
    return json.loads(raw)


def scoped_plan(skill, skus, shops):
    rows = []
    if any(s.startswith('tiktok:') for s in shops):
        live = skill._live_tiktok_rows(tuple(sorted(skus)), targets=shops)
        keys = {(r['target_label'], r['product_id']) for r in live}
        rows += live
        rows += [r for r in skill._local_tiktok_rows(tuple(sorted(skus))) if r['target_label'] in shops and (r['target_label'], r['product_id']) not in keys]
    if any(s.startswith('shopee:') for s in shops):
        rows += skill._live_shopee_rows(tuple(sorted(skus)), targets=shops)
    if 'ozon:RU' in shops:
        rows += skill._ozon_rows(tuple(sorted(skus)))
    targets = []
    for row in rows:
        if row['target_label'] not in shops:
            continue
        fresh = skill._live_verify(copy.deepcopy(row))
        actual = set(fresh.get('all_product_skus') or [])
        requested = set(fresh.get('requested_skus') or [])
        if fresh.get('live_read_error') or not actual or not requested or not actual.issubset(skus) or actual != requested:
            raise ValueError('live identity incomplete or includes unrequested SKU')
        fresh.update(status='READY', executable=True)
        targets.append(fresh)
    for shop in shops:
        if set().union(*(set(r['requested_skus']) for r in targets if r['target_label'] == shop)) != skus:
            raise ValueError('not every requested SKU was verified in ' + shop)
    identities = [(r['target_label'], str(r['product_id'])) for r in targets]
    if len(set(identities)) != len(identities):
        raise ValueError('ambiguous duplicate product identity')
    plan = {'schema_version': 'product-delist-plan/v1', 'created_at': skill._now(), 'scope': 'exact_selected_stores',
            'requested_skus': sorted(skus), 'expected_targets': sorted(shops), 'targets': targets}
    plan['plan_digest'] = skill._digest(plan)
    return plan


def run(engine, task, token, profile, *, skill=None):
    if profile.environment != 'stable':
        engine.fail(task['task_id'], token, '预览环境不执行平台下架；请在稳定工作台发起真实任务')
        return
    skill = skill or _module(profile.root)
    scope, step = task['scope'], task['current_step']
    skus, shops = set(scope['skus']), set(scope['shops'])
    if not skus or not shops or not shops.issubset(set(skill.ALL_TARGETS)):
        raise ValueError('exact supported platform/store identities required')
    if any(not sku.isdigit() or len(sku) != 4 for sku in skus):
        raise ValueError('internal SKUs must be exact four digits')
    output = profile.data_root / 'private' / task['task_id']
    if step == 'identify':
        plan = scoped_plan(skill, skus, shops)
        if {r['target_label'] for r in plan['targets']} != shops:
            raise ValueError('store scope incomplete')
        blocked = [r for r in plan['targets'] if r.get('status') != 'READY' or r.get('executable') is not True]
        if blocked:
            engine.fail(task['task_id'], token, '商品身份未通过核验：' + '；'.join(r['target_label'] + ' ' + r.get('status', '') for r in blocked))
            return
        ref = _save(output / ('plan-' + token + '.json'), plan, root=output)
        engine.complete_step(task['task_id'], token, expected_step=step, checkpoint={'plan': ref, 'scope_verified': True})
        return
    identify = next(s['checkpoint'] for s in task['steps'] if s['key'] == 'identify')
    plan = _read(identify['plan'], root=output)
    skill._verify_plan(plan)
    if set(plan['requested_skus']) != skus or set(plan['expected_targets']) != shops:
        raise ValueError('plan no longer matches immutable task scope')
    if step == 'delist':
        for row in plan['targets']:
            fresh = skill._live_verify(copy.deepcopy(row))
            if fresh.get('live_read_error') or set(fresh.get('all_product_skus', [])) != set(row['all_product_skus']) or set(fresh.get('requested_skus', [])) != set(row['requested_skus']) or str(fresh.get('product_id')) != str(row.get('product_id')) or fresh.get('current_status') != row.get('current_status'):
                raise ValueError('provider identity changed before delisting')
        engine.mark_external_started(task['task_id'], token)
        result = skill.execute(plan, operation_owner=task['task_id'])
        ref = _save(output / ('execution-' + token + '.json'), result, root=output)
        engine.record_checkpoint(task['task_id'], token, {'execution': ref, 'plan': identify['plan']})
        if len(result.get('targets', [])) != len(plan['targets']) or not all(r.get('verified') is True for r in result['targets']):
            engine.fail(task['task_id'], token, '下架提交结果未全部确认，保留原请求并进行只读对账')
            return
        engine.complete_step(task['task_id'], token, expected_step=step, checkpoint={'execution': ref, 'provider_readback_ref': ref['sha256']})
        return
    if step == 'readback':
        result = skill.readback(plan)
        ref = _save(output / ('readback-' + token + '.json'), result, root=output)
        checkpoint = {**task.get('checkpoint', {}), 'plan': identify['plan'], 'readback': ref}
        engine.record_checkpoint(task['task_id'], token, checkpoint)
        expected = {(r['target_label'], str(r['product_id'])): r for r in plan['targets']}
        seen = set()
        valid = True
        for row in result.get('targets', []):
            key = (row.get('target_label'), str(row.get('product_id')))
            source = expected.get(key)
            if not source or key in seen or row.get('verified') is not True or row.get('read_error') or set(row.get('requested_skus', [])) != set(source['requested_skus']) or set(row.get('all_product_skus', [])) != set(source['all_product_skus']):
                valid = False
            seen.add(key)
        if not valid or seen != set(expected):
            engine.fail(task['task_id'], token, '平台回读尚未全部确认下架，请核对原执行结果')
            return
        engine.complete_step(task['task_id'], token, expected_step=step,
                             checkpoint={**checkpoint, 'provider_readback_ref': ref['sha256'], 'verified_target_count': len(plan['targets'])},
                             result_url='/api/orbit/tasks/' + task['task_id'] + '/delisting-receipt')
