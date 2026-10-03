"""Explicit missing-cost assumptions for official same-order TikTok identities."""
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

from .cost_policy import resolve_temporary_cost_policy
from shared_platform.internal_catalog_sku import internal_sku

_MISSING_ONLY_ISSUES = {'ambiguous_seller_sku_scope', 'cost_missing'}

def _canonical(value):
    return internal_sku(value)


def monthly_cost_period(site, start, end, timezone_name=None):
    zones = {'TH': ('Asia/Bangkok', 7), 'VN': ('Asia/Ho_Chi_Minh', 7),
             'MY': ('Asia/Kuala_Lumpur', 8), 'PH': ('Asia/Manila', 8)}
    if site not in zones or (timezone_name is not None and timezone_name != zones[site][0]):
        raise ValueError('monthly cost timezone must match the exact supported site')
    zone = timezone(timedelta(hours=zones[site][1]))
    if end < start:
        raise ValueError('ordered monthly cost period required')
    return datetime.combine(start, time.min, zone), datetime.combine(end + timedelta(days=1), time.min, zone)

def _positive(value):
    try:
        number = Decimal(str(value))
        return number.is_finite() and number > 0
    except (InvalidOperation, ValueError, TypeError):
        return False

def resolve_monthly_missing_cost_policy(catalog, evidence, required_skus, *, site, start, end, allow_missing_default, timezone_name=None):
    """Allow CNY5 only for evidenced missing costs; never authorize conflicts.

    A cost-only copy removes the legacy cross-site internal-SKU block. The
    original catalog, order adapter and full provider identities stay intact.
    """
    if not allow_missing_default:
        raise ValueError('shared-SKU missing cost requires explicit per-run missing-cost authority')
    if evidence.get('platform') != 'tiktok' or str(evidence.get('site', '')).upper() != site or not evidence.get('shop_id'):
        raise ValueError('exact TikTok evidence platform/site/shop required')
    required = {str(s) for s in required_skus if str(s)}
    proven, unsafe, observations = set(), set(), []
    # Finance can have multiple positive/negative statements for the same order.
    # Repetition is valid; divergent raw Seller SKUs for the same exact provider
    # variant are not, even when their normalized internal suffix agrees.
    statement_mappings = {}
    for order in evidence.get('orders') or []:
        for item in order.get('items') or []:
            if not isinstance(item, dict):
                continue
            identity = (str(order.get('shop_id') or ''), str(order.get('order_id') or ''), str(item.get('platform_sku') or ''))
            statement_mappings.setdefault(identity, set()).add(str(item.get('seller_sku') or '').strip())
    for order in evidence.get('orders') or []:
        if not start.isoformat() <= str(order.get('order_created_at') or '')[:10] <= end.isoformat():
            continue
        items = [item for item in order.get('items') or [] if isinstance(item, dict)]
        mappings = {}
        for item in items:
            sku = _canonical(item.get('seller_sku'))
            variant = str(item.get('platform_sku') or '')
            mappings.setdefault(variant, set()).add(sku)
        scope_ok = bool(order.get('order_id')) and order.get('shop_id') == evidence['shop_id'] and str(order.get('region', '')).upper() == site and order.get('settlement_status') == 'settled'
        for item in items:
            sku = _canonical(item.get('seller_sku'))
            if sku not in required:
                continue
            variant = str(item.get('platform_sku') or '')
            statement_identity = (str(order.get('shop_id') or ''), str(order.get('order_id') or ''), variant)
            if not scope_ok or not variant or not sku or len(mappings[variant]) != 1 or len(statement_mappings[statement_identity]) != 1:
                unsafe.add(sku)
                continue
            proven.add(sku)
            observations.append({'order_id':str(order['order_id']), 'platform_sku':variant, 'canonical_sku':sku})
    eligible, blocked_reasons = set(), {}
    for sku in sorted(required):
        reasons = []
        candidates = tuple(catalog.cost_candidates_by_sku.get(sku, ()))
        records = tuple(catalog.cost_records_by_sku.get(sku, ()))
        if sku not in proven or sku in unsafe:
            reasons.append('official_same_order_identity_unverified')
        if candidates or records or _positive(catalog.costs_by_sku.get(sku)):
            reasons.append('existing_cost_evidence_not_missing')
        for issue in catalog.issues:
            if issue.record_id == sku or issue.identity.get('seller_sku') == sku:
                if issue.code not in _MISSING_ONLY_ISSUES:
                    reasons.append(issue.code)
        if reasons:
            blocked_reasons[sku] = sorted(set(reasons))
        else:
            eligible.add(sku)
    blocked = set(catalog.blocked_skus) - eligible
    # Even a formerly unblocked SKU must not silently pick a conflicting high
    # cost under a missing-cost authorization.
    for sku in set(catalog.costs_by_sku) | required:
        # Dated candidates are filtered by the exact requested period below;
        # conflicting applicable values are rejected by the policy itself.
        if (sku in required and sku not in proven) or sku in unsafe:
            blocked.add(sku)
        if sku in required and sku not in eligible and not catalog.cost_candidates_by_sku.get(sku) and not _positive(catalog.costs_by_sku.get(sku)):
            blocked.add(sku)
    view = replace(catalog, blocked_skus=tuple(sorted(blocked)))
    period_start, period_end = monthly_cost_period(site, start, end, timezone_name)
    policy = resolve_temporary_cost_policy(view, required, allow_missing_default=True, allow_conflict_high=False, period_start=period_start, period_end=period_end)
    receipt = {'policy':'official-same-order-missing-cost-cny5/v1', 'site':site, 'period':[start.isoformat(),end.isoformat()], 'eligible_skus':sorted(eligible), 'blocked_reasons':blocked_reasons, 'official_binding_sha256':sha256(json.dumps(observations,sort_keys=True,separators=(',',':')).encode()).hexdigest(), 'historical_cost_confirmed':False, 'conflict_cost_authorized':False}
    return policy, receipt
