"""Task-bound, read-only projection of saved delisting evidence.

No provider/Skill imports, credentials, directory discovery or business mutations.
"""
from __future__ import annotations

import copy
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re

from shared_platform.immutable_approval_files import require_local_path

MAX_BYTES = 2 * 1024 * 1024
MAX_TARGETS = 500
HASH = re.compile(r'[a-f0-9]{64}\Z')
TASK_ID = re.compile(r'TASK-\d{8}-\d{3,}\Z')
SHOP = re.compile(r'(tiktok|shopee|ozon):[A-Za-z0-9_:.-]{1,80}\Z')
DATE = re.compile(r'\d{4}-\d\d-\d\d[T ][0-9:.+-]+Z?\Z')
DOWN = {'tiktok': {'DEACTIVATE', 'DEACTIVATED', 'SELLER_DEACTIVATED', 'SUSPENDED', 'FROZEN', 'DRAFT'},
        'shopee': {'UNLIST'}, 'ozon': {'ARCHIVED'}}
ACTIVE = {'tiktok': {'ACTIVE', 'ACTIVATE'}, 'shopee': {'NORMAL'}, 'ozon': {'ACTIVE'}}


class ReceiptError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def receipt_url(task_id):
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise ReceiptError('TASK_ID_INVALID')
    return '/api/orbit/tasks/' + task_id + '/delisting-receipt'


def _canonical(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _date(value):
    return value if isinstance(value, str) and len(value) <= 40 and DATE.fullmatch(value) else None


def _skus(value):
    if not isinstance(value, list) or not value or len(value) > MAX_TARGETS or any(
            not isinstance(x, str) or not re.fullmatch(r'\d{4}', x) for x in value) or len(set(value)) != len(value):
        raise ReceiptError('IDENTITY_MISMATCH')
    return set(value)


def _key(row):
    if not isinstance(row, dict) or not isinstance(row.get('target_label'), str) or not SHOP.fullmatch(row['target_label']):
        raise ReceiptError('IDENTITY_MISMATCH')
    product = row.get('product_id')
    if isinstance(product, bool) or not isinstance(product, (str, int)) or not re.fullmatch(r'\d{1,40}', str(product)):
        raise ReceiptError('IDENTITY_MISMATCH')
    return row['target_label'], str(product)


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ReceiptError('MALFORMED_DOCUMENT')
        obj[key] = value
    return obj


def _reference(task, kind):
    current = task.get('checkpoint') or {}
    step_key = {'plan': 'identify', 'execution': 'delist', 'readback': 'readback'}[kind]
    steps = [step for step in task.get('steps', []) if step.get('key') == step_key]
    if len(steps) > 1:
        raise ReceiptError('REFERENCE_CONFLICT')
    if steps and steps[0].get('checkpoint') is not None and not isinstance(steps[0]['checkpoint'], dict):
        raise ReceiptError('MALFORMED_DOCUMENT')
    saved = (steps[0].get('checkpoint') or {}).get(kind) if steps else None
    if kind in current and saved is not None and current[kind] != saved:
        raise ReceiptError('REFERENCE_CONFLICT')
    return current[kind] if kind in current else saved


def _read(ref, private):
    if ref is None:
        raise ReceiptError('REFERENCE_MISSING')
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str) or not HASH.fullmatch(str(ref.get('sha256', ''))):
        raise ReceiptError('REFERENCE_INVALID')
    path = Path(ref['path'])
    if (not path.is_absolute() or str(path).startswith(('\\\\', '//')) or path.suffix != '.json'
            or any(':' in part for part in path.parts[1:]) or path.is_reserved()):
        raise ReceiptError('PATH_REJECTED')
    try:
        before = require_local_path(path, root=private)
        if before is None:
            raise ReceiptError('FILE_MISSING')
        if before.st_size > MAX_BYTES:
            raise ReceiptError('DOCUMENT_TOO_LARGE')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ReceiptError('FILE_CHANGED')
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        final = require_local_path(path, root=private)
        if final is None or any((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) !=
                                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) for s in (after, final)):
            raise ReceiptError('FILE_CHANGED')
    except ReceiptError:
        raise
    except (ValueError, OSError, RuntimeError):
        raise ReceiptError('PATH_REJECTED') from None
    if len(raw) > MAX_BYTES:
        raise ReceiptError('DOCUMENT_TOO_LARGE')
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ReceiptError('HASH_MISMATCH')
    try:
        doc = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ReceiptError('MALFORMED_DOCUMENT')))
    except (UnicodeError, ValueError, RecursionError):
        raise ReceiptError('MALFORMED_DOCUMENT') from None
    if not isinstance(doc, dict):
        raise ReceiptError('MALFORMED_DOCUMENT')
    return doc


def _plan(doc, scope):
    if doc.get('schema_version') != 'product-delist-plan/v1':
        raise ReceiptError('SCHEMA_MISMATCH')
    supplied = doc.get('plan_digest')
    if supplied != _canonical({k: v for k, v in doc.items() if k != 'plan_digest'}):
        raise ReceiptError('PLAN_DIGEST_MISMATCH')
    skus = _skus(doc.get('requested_skus'))
    shops = doc.get('expected_targets')
    if (skus != _skus(scope.get('skus')) or not isinstance(shops, list) or not shops
            or any(not isinstance(x, str) or not SHOP.fullmatch(x) for x in shops)
            or len(set(shops)) != len(shops) or set(shops) != set(scope.get('shops') or [])):
        raise ReceiptError('IDENTITY_MISMATCH')
    targets = doc.get('targets')
    if not isinstance(targets, list) or not 0 < len(targets) <= MAX_TARGETS:
        raise ReceiptError('IDENTITY_MISMATCH')
    indexed = {}
    for row in targets:
        key = _key(row)
        if (key in indexed or key[0] not in shops or row.get('platform') != key[0].split(':')[0]
                or _skus(row.get('requested_skus')) != _skus(row.get('all_product_skus'))
                or not _skus(row['requested_skus']).issubset(skus)):
            raise ReceiptError('IDENTITY_MISMATCH')
        indexed[key] = row
    if any(set().union(*(set(row['requested_skus']) for key, row in indexed.items() if key[0] == shop)) != skus for shop in shops):
        raise ReceiptError('IDENTITY_MISMATCH')
    return indexed


def _rows(doc, kind, plan, expected):
    if doc.get('schema_version') != 'product-delist-' + kind + '/v1':
        raise ReceiptError('SCHEMA_MISMATCH')
    if doc.get('plan_digest') != plan['plan_digest'] or _skus(doc.get('requested_skus')) != set(plan['requested_skus']):
        raise ReceiptError('IDENTITY_MISMATCH')
    rows = doc.get('targets')
    if not isinstance(rows, list) or len(rows) > MAX_TARGETS:
        raise ReceiptError('IDENTITY_MISMATCH')
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ReceiptError('IDENTITY_MISMATCH')
        legacy = kind == 'execution' and 'product_id' not in row
        if legacy:
            matches = [key for key in expected if key[0] == row.get('target_label')]
            if len(matches) != 1:
                raise ReceiptError('IDENTITY_GAP')
            key = matches[0]
        else:
            key = _key(row)
        if key not in expected or key in indexed:
            raise ReceiptError('IDENTITY_MISMATCH')
        source = expected[key]
        if (not legacy or 'requested_skus' in row) and _skus(row.get('requested_skus')) != set(source['requested_skus']):
            raise ReceiptError('IDENTITY_MISMATCH')
        if (kind == 'readback' or 'all_product_skus' in row) and _skus(row.get('all_product_skus')) != set(source['all_product_skus']):
            raise ReceiptError('IDENTITY_MISMATCH')
        indexed[key] = row
    return indexed


def build_receipt(inputs, profile):
    task = inputs['task']; task_id = task['task_id']
    receipt_url(task_id)
    private = Path(profile.data_root).absolute() / 'private' / task_id
    result = {'schema': 'orbit-delisting-receipt/v1', 'task_id': task_id,
              'task_state': task['state'], 'scope': {'skus': [], 'shops': []},
              'evidence': {}, 'targets': [], 'domain': {'state': 'UNBOUND'},
              'summary': {'confirmed': 0, 'total': 0, 'execution_confirmed': 0, 'status': 'EVIDENCE_GAP'},
              'network_performed': False, 'business_writes_performed': False}
    docs = {}; expected = {}
    for kind in ('plan', 'execution', 'readback'):
        evidence = {'status': 'REFERENCE_MISSING'}
        try:
            ref = _reference(task, kind)
            doc = _read(ref, private)
            if kind == 'plan':
                expected = _plan(doc, task['scope'])
                result['scope'] = {'skus': sorted(doc['requested_skus']), 'shops': sorted(doc['expected_targets'])}
                result['plan_digest'] = doc['plan_digest']
            elif not expected:
                raise ReceiptError('PLAN_NOT_VERIFIED')
            else:
                doc = {'document': doc, 'rows': _rows(doc, kind, docs['plan'], expected)}
            docs[kind] = doc
            evidence = {'status': 'VERIFIED_BYTES_AND_IDENTITY', 'sha256': ref['sha256'],
                        'observed_at': _date(doc.get('created_at') if kind == 'plan' else doc['document'].get('created_at'))}
            if kind != 'plan' and set(doc['rows']) != set(expected):
                evidence['status'] = 'TARGETS_MISSING'
        except (ReceiptError, TypeError, KeyError, ValueError, RecursionError) as error:
            evidence['status'] = error.code if isinstance(error, ReceiptError) else 'MALFORMED_DOCUMENT'
        result['evidence'][kind] = evidence
    if not expected:
        return result
    plan = docs['plan']
    resources = sorted('product:' + json.dumps([shop, sku], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                       for shop in plan['expected_targets'] for sku in plan['requested_skus'])
    operations = [x for x in inputs['operations'] if x['operation_id'] == 'delisting:' + plan['plan_digest']]
    if len(operations) == 1:
        operation = operations[0]
        try:
            bound = operation['owner_task_id'] == task_id and json.loads(operation['resources_json']) == resources
        except (ValueError, TypeError):
            bound = False
        if bound:
            result['domain'] = {'state': operation['state'] if operation['state'] in {'inflight', 'completed'} else 'UNKNOWN',
                                'locked_resource_count': int(operation['locked_resource_count']), 'completion_bound': False}
            if operation['state'] == 'completed' and 'execution' in docs:
                digest = hashlib.sha256(json.dumps(docs['execution']['document'], sort_keys=True).encode()).hexdigest()
                result['domain']['completion_bound'] = (operation['readback_ref'] == 'delisting-result:' + digest
                                                        and operation['locked_resource_count'] == 0
                                                        and all(x['state'] == 'completed' for x in inputs['operations']))
    execution = docs.get('execution', {}).get('rows', {})
    readback = docs.get('readback', {}).get('rows', {})
    for key, frozen in expected.items():
        sent = execution.get(key); observed = readback.get(key); platform = key[0].split(':')[0]
        execution_status = 'NOT_RECORDED'
        if sent:
            if sent.get('verified') is True and sent.get('outcome') in {'VERIFIED_DELISTED', 'VERIFIED_ALREADY_DELISTED'}:
                execution_status = sent['outcome']
            elif sent.get('attempted') is True or sent.get('outcome') == 'UNKNOWN':
                execution_status = 'UNKNOWN'
            elif sent.get('attempted') is False and sent.get('outcome') not in {None, ''}:
                execution_status = 'NOT_ATTEMPTED_BLOCKED'
        readback_status = 'READBACK_MISSING'; official_status = None
        if observed:
            status = str(observed.get('current_status') or '').upper()
            if not observed.get('read_error') and status in DOWN[platform] and observed.get('verified') is True:
                readback_status = 'VERIFIED_DELISTED'; official_status = status
            elif not observed.get('read_error') and status in ACTIVE[platform] and observed.get('verified') is False:
                readback_status = 'OBSERVED_ACTIVE'; official_status = status
            else:
                readback_status = 'READBACK_UNKNOWN'
        result['targets'].append({'target_label': key[0], 'product_id': key[1], 'requested_skus': sorted(frozen['requested_skus']),
                                  'execution_status': execution_status, 'readback_status': readback_status, 'official_status': official_status})
    confirmed = sum(x['readback_status'] == 'VERIFIED_DELISTED' for x in result['targets'])
    total = len(expected)
    valid_evidence = all(x['status'] == 'VERIFIED_BYTES_AND_IDENTITY' for x in result['evidence'].values())
    if confirmed == total and valid_evidence and task['state'] == 'completed' and result['domain'].get('completion_bound'):
        status = 'COMPLETE_VERIFIED_RECEIPT'
    elif not valid_evidence:
        status = 'EVIDENCE_GAP'
    elif confirmed:
        status = 'PARTIAL_RECONCILIATION_REQUIRED' if confirmed < total else 'ALL_OBSERVED_RECONCILIATION_REQUIRED'
    elif result['evidence']['readback']['status'] != 'VERIFIED_BYTES_AND_IDENTITY':
        status = 'EVIDENCE_GAP'
    else:
        status = 'RECONCILIATION_REQUIRED'
    result['summary'] = {'confirmed': confirmed, 'total': total,
                         'execution_confirmed': sum(x['execution_status'] in {'VERIFIED_DELISTED', 'VERIFIED_ALREADY_DELISTED'} for x in result['targets']), 'status': status}
    return result


def public_projection(value):
    """Keep private refs in the engine; never expose them through adjacent task JSON."""
    value = copy.deepcopy(value)
    tasks = value.get('tasks', []) + ([value['task']] if 'task' in value else [])
    allowed = {'task_id', 'id', 'title', 'template', 'execution_state', 'status', 'scope', 'version',
                'created_at', 'updated_at', 'completed_at', 'priority', 'current_step', 'executor_connected'}
    def display_identity(raw, fallback='已分派（名称不可展示）'):
        if raw is None or raw == '':
            return raw
        return raw if isinstance(raw, str) and re.fullmatch(r'[\w .:@-]{1,128}', raw) else fallback
    for task in tasks:
        if task.get('template') != 'delisting':
            continue
        identity = task.get('task_id') or task.get('id')
        external = task.get('external_task')
        url = task.get('result_url', '') if external else receipt_url(identity)
        steps = [{'key': s.get('key'), 'label': s.get('label'), 'state': s.get('state')}
                 for s in task.get('steps', [])]
        safe = {k: task[k] for k in allowed if k in task}
        for key in ('worker', 'last_executor', 'owner'):
            if key in task:
                safe[key] = display_identity(task[key])
        if 'last_claimed_at' in task:
            safe['last_claimed_at'] = _date(task['last_claimed_at'])
        action = task.get('required_action')
        safe_action = None
        if action and action.get('kind') == 'input':
            safe_action = {'kind': 'input', 'action_id': action.get('action_id'),
                           'label': '补充下架任务资料', 'reason': '请按当前任务要求提供资料；不要填写凭据。'}
        safe.update(steps=steps, checkpoint={}, required_action=safe_action, result_url=url,
                    blocked_reason='已有下架记录尚待核对，请查看任务回执。' if task.get('execution_state') in {'failed', 'reconciliation_required'} else '')
        if task.get('pending_observation'):
            safe['pending_observation'] = {'kind': 'observe', 'label': '等待官方回读',
                'reason': '原下架操作正在等待平台结果；此处保留待核对状态，不会重复提交。'}
        if external:
            safe['external_task'] = {'owner': display_identity(external.get('owner')),
                'observed_status': display_identity(external.get('observed_status'), '历史状态记录不可展示'),
                'observed_at': _date(external.get('observed_at')), 'live_status': 'unknown'}
            safe['blocked_reason'] = '由已关联任务执行；所示观测为历史记录，当前状态尚未核实。'
            if isinstance(task.get('checkpoint'), dict) and task['checkpoint'].get('business_status') == 'needs_review':
                safe['checkpoint'] = {'business_status': 'needs_review'}
        task.clear(); task.update(safe)
        if value.get('task') is task:
            value['events'] = [{k: e[k] for k in ('event_type', 'created_at') if k in e} for e in value.get('events', [])]
    return value


LABELS = {'VERIFIED_DELISTED': '已核实下架', 'VERIFIED_ALREADY_DELISTED': '执行前已下架',
          'UNKNOWN': '提交结果待核对', 'NOT_RECORDED': '无执行记录', 'NOT_ATTEMPTED_BLOCKED': '记录为未尝试 / 被阻断',
          'READBACK_MISSING': '缺少回读', 'READBACK_UNKNOWN': '回读待核对', 'OBSERVED_ACTIVE': '回读仍上架',
          'COMPLETE_VERIFIED_RECEIPT': '回执完整，任务已完成', 'PARTIAL_RECONCILIATION_REQUIRED': '部分确认，其余待核对',
          'ALL_OBSERVED_RECONCILIATION_REQUIRED': '目标均已读回；任务或域记录待核对',
          'EVIDENCE_GAP': '回执证据缺口', 'RECONCILIATION_REQUIRED': '回执待核对'}


def render_receipt(receipt):
    def esc(value): return escape(str(value if value is not None else '尚无记录'))
    summary = receipt['summary']
    rows = ''.join('<tr><td>'+esc(row['target_label'])+'</td><td>'+esc(row['product_id'])+'</td><td>'+esc('、'.join(row['requested_skus']))+
                   '</td><td>'+esc(LABELS[row['execution_status']])+'</td><td>'+esc(LABELS[row['readback_status']])+
                   '</td><td>'+esc(row['official_status'])+'</td></tr>' for row in receipt['targets'])
    evidence = ''.join('<li><strong>'+esc(kind)+'</strong>：'+esc(item['status'])+'<br>观察时间：'+esc(item.get('observed_at'))+
                       '<br>SHA256：<code>'+esc(item.get('sha256'))+'</code></li>' for kind, item in receipt['evidence'].items())
    domain_label = {'inflight': '尚有未决操作，保留占用', 'completed': '已记录完成',
                    'UNBOUND': '缺少精确归属记录', 'UNKNOWN': '操作状态待核对'}[receipt['domain']['state']]
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>下架任务回执</title><style>body{font:16px system-ui;margin:24px auto;padding:0 16px;max-width:1050px;color:#182434}'
            'nav{display:flex;gap:24px}h1{font-size:28px}.notice{padding:16px;background:#f4f5f7;border-radius:8px}'
            '.table{overflow:auto}table{width:100%;min-width:760px;border-collapse:collapse}th,td{padding:12px;text-align:left;border-bottom:1px solid #ddd;white-space:nowrap}'
            '.table-hint{color:#566171;font-size:14px}@media(min-width:800px){.table-hint{display:none}}'
            'code,li,p{overflow-wrap:anywhere}li{margin-bottom:16px}</style><nav><a href="/">返回任务首页</a><a href="/catalog">商品目录</a></nav>'
            '<main><h1>下架任务回执</h1><p>任务 '+esc(receipt['task_id'])+' · 历史任务状态：'+esc(receipt['task_state'])+'</p>'
            '<section class="notice" role="status"><h2>'+esc(LABELS[summary['status']])+'</h2><p>最终回读已确认 '+str(summary['confirmed'])+' / '+str(summary['total'])+
            ' 个商品目标；执行即时确认 '+str(summary['execution_confirmed'])+' 项。</p><p>仅展示已保存证据，未查询平台。缺失或未知不表示未发送，不会自动重试。</p></section>'
            '<p>冻结 SKU：'+esc('、'.join(receipt['scope']['skus']))+'；目标：'+esc('、'.join(receipt['scope']['shops']))+'</p>'
            '<p>操作记录：'+esc(domain_label)+'</p><p class="table-hint">可横向滚动表格查看全部状态。</p>'
            '<div class="table" role="region" aria-label="逐目标下架回执，可横向滚动"><table><thead><tr><th>店铺</th><th>平台商品</th><th>SKU</th>'
            '<th>执行证据</th><th>最终回读</th><th>官方状态</th></tr></thead><tbody>'+rows+'</tbody></table></div><h2>证据完整性</h2><ul>'+evidence+'</ul>'
            '<p>观察时间仅代表该次证据，不代表实时平台状态。此页不批准、执行或恢复下架。</p></main></html>')
