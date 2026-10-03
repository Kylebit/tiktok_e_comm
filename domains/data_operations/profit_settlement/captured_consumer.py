"""Read a deliberately selected captured profile; never pull or approve data."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .cost_policy import resolve_temporary_cost_policy
from .local_catalog import load_local_catalog
from .settlement_evidence_adapter import adapt_settlement_evidence
from .shared_inputs import CostSnapshot, FxSnapshot
from .weekly_evidence_bundle import build_weekly_evidence_bundle
from .captured_coverage import captured_coverage
from .captured_history import captured_history
from .knowledge_base import _checksum


def build_captured_weekly(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Return review JSON from explicit paths without defaults or output writes.

    A profile selects platform/site/shop/period/UTC offset, catalog_path,
    evidence_path and fx_path. Temporary costs require an explicit boolean.
    The profile is input selection, never publication or knowledge approval.
    """
    try:
        if profile.get('schema_version') != 'profit-captured-profile/v1':
            raise ValueError('invalid_profile')
        platform, site, shop = (str(profile.get(key) or '') for key in ('platform', 'site', 'shop_id'))
        if platform not in {'tiktok', 'shopee', 'ozon'} or not site or not shop:
            raise ValueError('invalid_scope')
        start, end = date.fromisoformat(profile['start']), date.fromisoformat(profile['end'])
        offset = str(profile['timezone'])
        if end < start or not re.fullmatch(r'[+-]\d{2}:\d{2}', offset):
            raise ValueError('invalid_period')
        zone = datetime.fromisoformat('2000-01-01T00:00:00' + offset).tzinfo
        paths = {key: Path(profile[key]) for key in ('catalog_path', 'evidence_path', 'fx_path')}
        if any(not path.is_absolute() for path in paths.values()):
            raise ValueError('absolute_input_paths_required')
        evidence_raw, fx_raw = paths['evidence_path'].read_bytes(), paths['fx_path'].read_bytes()
        evidence, fx_input = json.loads(evidence_raw), json.loads(fx_raw)
        if not isinstance(evidence, dict) or not isinstance(fx_input, dict):
            raise ValueError('invalid_captured_object')
        if (evidence.get('platform'), evidence.get('site'), evidence.get('shop_id')) != (platform, site, shop):
            raise ValueError('captured_scope_mismatch')
        if any(isinstance(row, dict) and row.get('shop_id') not in (None, '', shop) for row in evidence.get('orders', [])):
            raise ValueError('captured_scope_mismatch')
        catalog = load_local_catalog(paths['catalog_path'])
        adapted = adapt_settlement_evidence(evidence, catalog, period_kind='weekly',
                                           seller_sku_by_platform_sku=profile.get('seller_sku_mapping'))
        required = {str(row.get('canonical_sku') or '') for row in adapted.rows}
        policy = resolve_temporary_cost_policy(catalog, required,
            period_start=datetime.combine(start, time.min, zone),
            period_end=datetime.combine(end + timedelta(days=1), time.min, zone))
        values, policy_issues = dict(policy.values), list(policy.issues)
        allow_assumptions = profile.get('allow_temporary_cost_policy') is True
        if not allow_assumptions:
            for warning in policy.warnings:
                values.pop(warning.canonical_sku, None)
                policy_issues.append({'code': 'cost_assumption_not_selected', 'canonical_sku': warning.canonical_sku,
                                      'message': 'Temporary cost selection is not enabled in this captured profile'})
        costs = CostSnapshot.from_mapping(values)
        base_fx = FxSnapshot.from_mapping(fx_input.get('rates_cny') or {}, source=fx_input.get('source') or '',
                                         as_of=fx_input.get('as_of') or '', snapshot_id=fx_input.get('snapshot_id'))
        overrides = profile.get('fx_overrides') or {}
        if not isinstance(overrides, dict):
            raise ValueError('invalid_fx_override')
        for currency, value in overrides.items():
            amount = Decimal(str(value))
            if not amount.is_finite() or amount <= 0 or not re.fullmatch(r'[A-Z]{3}', currency):
                raise ValueError('invalid_fx_override')
        fx = FxSnapshot.from_mapping({**base_fx.rates_cny, **overrides},
              source='operator_override:' + base_fx.checksum, as_of=base_fx.as_of) if overrides else base_fx
        result = build_weekly_evidence_bundle({platform: evidence}, replace(catalog, costs_by_sku={sku: Decimal(value['unit_cost_cny']) for sku, value in values.items()}),
            period_start=start, period_end=end, costs=costs, fx=fx,
            ad_rate=profile.get('ad_rate', '0.22'),
            ad_rate_source='operator_global_override' if 'ad_rate' in profile else 'default_22',
            seller_sku_by_ozon_sku=profile.get('seller_sku_mapping'),
            cost_assumption_warnings=policy.warnings if allow_assumptions else (),
            cost_policy_issues=tuple(policy_issues), calculation_timezone=offset,
            platforms=(platform,), code_version='profit-captured-consumer/v1')
        result['scope'] = {'platform': platform, 'site': site, 'shop_id': shop, 'start': str(start), 'end': str(end), 'timezone': offset}
        result['input_mode'] = 'captured'
        result['network_reads_performed'] = []
        result['local_writes_performed'] = []
        result['captured_inputs'] = {'evidence_sha256': sha256(evidence_raw).hexdigest(), 'fx_sha256': sha256(fx_raw).hexdigest()}
        result['fx_provenance'] = {'base_snapshot': base_fx.payload(), 'overrides': {key: str(value) for key, value in overrides.items()},
                                   'input_source': 'operator_override' if overrides else 'captured_snapshot'}
        result['cost_policy'] = {'snapshot_id': policy.snapshot_id, 'temporary_assumptions_selected': allow_assumptions, 'issues': policy_issues}
        result['coverage'] = captured_coverage(profile,evidence,result['captured_inputs']['evidence_sha256'])
        result['approval_status'] = 'REVIEW_ONLY'
        result['history'] = captured_history(profile)
        result['review_id'] = 'captured-review:' + _checksum({'scope':result['scope'],'inputs':result['captured_inputs'],
            'coverage':result['coverage'],'cost_policy':result['cost_policy'],'fx':result['fx_provenance'],
            'reports':{key:value['report']['idempotency_key'] for key,value in result['reports'].items()}})
        return result
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        # No raw exception text: provider data and private paths do not belong here.
        return {'schema_version': 'profit-weekly-evidence-bundle/v1', 'status': 'check_failed',
                'result_scope': 'no_calculated_facts', 'reports': {},
                'error': {'code': 'captured_input_check_failed', 'type': type(exc).__name__},
                'input_mode': 'captured', 'network_reads_performed': [], 'local_writes_performed': [],
                'external_writes_performed': []}
