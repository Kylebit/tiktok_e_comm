"""Read-only cross-domain work observations, separate from execution authority.

The manifest is explicitly configured by the coordinator. It neither creates
Operations tasks nor dispatches, retries, approves, or refreshes business work.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'orbit-work-observations/v1'
MAXIMUM = 512 * 1024
KINDS = {'publication', 'delisting', 'profit', 'replenishment', 'engineering',
         'cleanup', 'creator', 'video', 'other'}


def _object(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('evidence must be a JSON object')
    return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError('observation time missing')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('observation time needs a timezone')
    return parsed.astimezone(timezone.utc)


def _text(value, maximum=500):
    return str(value or '')[:maximum]


def _identity(value):
    if (not isinstance(value, dict) or set(value) != {'kind', 'id'}
            or value['kind'] not in {'offer_id', 'common_collect_box_id', 'supplier_offer_id', 'internal_sku', 'platform_product_id'}
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(value['id']))):
        raise ValueError('exact product identity missing')
    return {'kind': value['kind'], 'id': str(value['id'])}


def _url(value):
    if not isinstance(value, str) or not value:
        return None
    if (not value.startswith('/') or value.startswith('//') or '\\' in value
            or re.search(r'%2f|%5c|[\x00-\x20]', value, re.I)):
        raise ValueError('result must be a local application URL')
    return value


def _bytes(reference, maximum=MAXIMUM):
    from shared_platform.immutable_approval_files import require_local_path
    if not isinstance(reference, dict):
        raise ValueError('source reference missing')
    path = Path(reference['path'])
    digest = reference['sha256']
    if not path.is_absolute() or not re.fullmatch(r'[0-9a-f]{64}', str(digest)):
        raise ValueError('source identity missing')
    before = require_local_path(path, root=path.parent)
    if before is None or not 0 < before.st_size <= maximum:
        raise ValueError('source unavailable or too large')
    raw = path.read_bytes()
    after = require_local_path(path, root=path.parent)
    signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if (after is None or signature(before) != signature(after)
            or hashlib.sha256(raw).hexdigest() != digest):
        raise ValueError('source changed; needs a new dated observation')
    return raw


def _lineage(value, image=None):
    """Validate a coordinator label against the original product source receipts.

    A thumbnail's self-declared product_identity alone is never lineage evidence.
    The normalized display label is pinned separately from the producer's title.
    """
    proof = _object(_bytes(value.get('lineage')))
    identity = _identity(value.get('identity'))
    if (proof.get('schema') != 'orbit-product-observation-lineage/v1'
            or _identity(proof.get('identity')) != identity):
        raise ValueError('product lineage identity missing')
    facts = _object(_bytes(proof.get('name_source')))
    downloaded = _object(_bytes(proof.get('image_source')))
    if downloaded.get('schema') == 'new-product-source-image-download/v1':
        supplier = _identity(proof.get('source_identity'))
        source = facts.get('source', {})
        if not isinstance(source, dict):
            raise ValueError('product facts source missing')
        if (identity['kind'] != 'common_collect_box_id'
                or str(downloaded.get('common_id')) != identity['id']
                or str(facts.get('offer_id')) != identity['id']
                or str(source.get('offer_id')) != identity['id']
                or supplier['kind'] != 'supplier_offer_id'
                or str(downloaded.get('source_id')) != supplier['id']
                or not re.fullmatch(r'https?://detail\.1688\.com/offer/' + re.escape(supplier['id']) + r'\.html', str(source.get('source_url')))
                or not proof.get('source_title')
                or source.get('title_source') != proof['source_title']):
            raise ValueError('original common/supplier/name lineage conflict')
    elif downloaded.get('schema') == 'orbit-product-source/v1':
        if (_identity(downloaded.get('identity')) != identity
                or facts.get('schema') != 'orbit-product-source/v1'
                or _identity(facts.get('identity')) != identity
                or facts.get('name') != proof.get('source_title') or not proof.get('source_title')):
            raise ValueError('typed source lineage conflict')
    else:
        raise ValueError('unsupported product source receipt')
    if image:
        path = Path(image['path'])
        source_root = Path(proof['image_source']['path']).parent
        matched = []
        rows = downloaded.get('images', [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid original image receipt rows')
        for row in rows:
            original = Path(row.get('file', ''))
            original = original if original.is_absolute() else source_root / original
            if (row.get('status') == 'ORIGINAL_BYTES_DOWNLOADED'
                    and original.resolve() == path.resolve()
                    and row.get('sha256') == image.get('sha256')):
                matched.append(row)
        if len(matched) != 1:
            raise ValueError('image absent from exact original download receipt')
    return proof


def _product(value, assets):
    identity = _identity(value.get('identity'))
    try:
        proof = _lineage(value)
        name_verified = (value.get('name_verified') is True and bool(value.get('name'))
                         and proof.get('display_name') == value.get('name'))
    except (KeyError, OSError, TypeError, ValueError):
        name_verified = False
    result = {'identity': identity,
              'name': _text(value.get('name'), 160) if name_verified else '商品名称待核实',
              'name_verified': name_verified, 'image_status': 'missing',
              'image_url': None, 'source_label': _text(value.get('source_label'), 160)}
    image = value.get('thumbnail')
    if image:
        try:
            if not isinstance(image, dict):
                raise ValueError('invalid thumbnail reference')
            if _identity(image.get('product_identity')) != identity:
                result['image_status'] = 'identity_conflict'
                return result
            _lineage(value, image)
            raw = _bytes(image, maximum=8 * 1024 * 1024)
            mime = ('image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else
                    'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else
                    'image/webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else None)
            if not mime:
                raise ValueError('unsupported image')
            asset_id = hashlib.sha256((identity['kind'] + ':' + identity['id'] + ':'
                                       + image['sha256']).encode()).hexdigest()
            assets[asset_id] = {'reference': image, 'identity': identity, 'mime': mime,
                                'product': value}
            result.update(image_status='verified', image_url='/api/orbit/home/images/' + asset_id)
        except (KeyError, OSError, TypeError, ValueError):
            result['image_status'] = 'unavailable'
    return result


def read_registry(manifest_path, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not manifest_path:
        return {'status': 'UNBOUND', 'complete': False, 'items': [], 'assets': {},
                'message': '交接记录源尚未绑定；由 Agent 补齐，不代表没有工作。'}
    from shared_platform.immutable_approval_files import require_local_path
    path = Path(manifest_path)
    if not path.is_absolute():
        raise ValueError('manifest path must be explicit and absolute')
    stat = require_local_path(path, root=path.parent)
    if stat is None or not 0 < stat.st_size <= MAXIMUM:
        raise ValueError('manifest unavailable')
    raw = path.read_bytes()
    after = require_local_path(path, root=path.parent)
    if (after is None or (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            != (after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError('manifest changed during read')
    data = _object(raw)
    if data.get('schema') != SCHEMA or not isinstance(data.get('items'), list) or len(data['items']) > 200:
        raise ValueError('invalid work observation manifest')
    items, assets, seen = [], {}, set()
    for value in data['items']:
        if (not isinstance(value, dict) or not isinstance(value.get('products', []), list)
                or any(not isinstance(p, dict) for p in value.get('products', []))
                or not isinstance(value.get('sources', []), list)):
            raise ValueError('invalid work observation row')
        work_id = value.get('work_id')
        if (not isinstance(work_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', work_id)
                or work_id in seen):
            raise ValueError('duplicate or invalid work identity')
        seen.add(work_id)
        if value.get('handover') not in {'accepted', 'pending', 'user_owned'}:
            raise ValueError('handover scope missing')
        observed = _time(value.get('observed_at'))
        if observed > now:
            raise ValueError('future observation is not evidence')
        source_status, sources = 'verified', []
        for ref in value.get('sources', []):
            try:
                _bytes(ref)
                sources.append({'label': _text(ref.get('label'), 160), 'sha256': ref['sha256']})
            except (OSError, KeyError, TypeError, ValueError):
                source_status = 'changed_or_unavailable'
        if not sources and source_status == 'verified':
            source_status = 'not_bound'
        products = [_product(p if source_status == 'verified' else {
            'identity': p.get('identity'), 'source_label': '来源变化，需重新核实商品资料'
        }, assets) for p in value.get('products', [])]
        stale_after = max(60, min(int(value.get('stale_after_seconds', 1800)), 86400))
        age = (now - observed).total_seconds()
        item = {'work_id': work_id, 'kind': _text(value.get('kind') or 'other', 80),
                'title': _text(value.get('title'), 200), 'goal': _text(value.get('goal')),
                'stage': _text(value.get('stage')), 'handover': value['handover'],
                'last_reported_state': _text(value.get('last_reported_state') or 'unknown', 80),
                'observed_at': observed.isoformat(), 'stale': age > stale_after,
                'live_status': 'unknown', 'source_status': source_status,
                'owner_label': _text(value.get('owner_label'), 100),
                'blocker': _text(value.get('blocker')), 'user_decision': None,
                'result_url': _url(value.get('result_url')) if source_status == 'verified' else None,
                'products': products, 'sources': sources,
                'evidence_notes': [_text(note, 500) for note in value.get('evidence_notes', [])[:20]],
                'operations_task_id': value.get('operations_task_id'),
                'source_session_id': _text(value.get('source_session_id'), 100),
                'closed': source_status == 'verified' and value.get('closed') is True}
        decision = value.get('user_decision')
        if (source_status == 'verified' and not item['stale'] and isinstance(decision, dict)
                and decision.get('kind') in {'business_decision', 'final_review', 'personal_login', 'new_cost', 'authority_change'}
                and decision.get('label') and decision.get('reason')):
            item['user_decision'] = {'kind': decision['kind'], 'label': _text(decision['label']),
                                     'reason': _text(decision['reason']), 'url': _url(decision.get('url'))}
        items.append(item)
    scope_status, expected = 'not_bound', []
    if data.get('scope_ref'):
        try:
            scope = _object(_bytes(data['scope_ref']))
            if scope.get('schema') == 'orbithive-readonly-takeover-baseline/v1':
                expected = [row['thread_id'] for row in scope['continue_existing_tasks']]
            elif scope.get('schema') == 'orbit-work-scope/v1':
                expected = [row['session_id'] for row in scope['sessions']]
            else:
                raise ValueError('unsupported accepted work scope')
            if (not expected or len(expected) != len(set(expected))
                    or any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',s) for s in expected)):
                raise ValueError('invalid accepted scope')
            scope_status = 'verified'
        except (KeyError, OSError, TypeError, ValueError):
            scope_status, expected = 'changed_or_unavailable', []
    covered = {item['source_session_id'] for item in items if item['source_status'] == 'verified'}
    missing = sorted(set(expected) - covered)
    complete = scope_status == 'verified' and not missing
    return {'status': 'AVAILABLE', 'complete': complete,
            'scope_status': scope_status, 'missing_session_ids': missing,
            'accepted_scope_count': len(expected),
            'items': items, 'assets': assets,
            'message': _text(data.get('coverage_note') or '仅显示已绑定来源的交接记录；无心跳不判断正在运行。')}


def public_registry(value):
    return {key: content for key, content in value.items() if key != 'assets'}


def image_bytes(registry, asset_id):
    if not re.fullmatch(r'[0-9a-f]{64}', asset_id or ''):
        raise KeyError(asset_id)
    asset = registry['assets'][asset_id]
    if _identity(asset['reference'].get('product_identity')) != asset['identity']:
        raise ValueError('image identity changed')
    _lineage(asset['product'], asset['reference'])
    return _bytes(asset['reference'], maximum=8 * 1024 * 1024), asset['mime']
