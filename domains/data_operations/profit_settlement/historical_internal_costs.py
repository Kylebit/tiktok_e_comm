"""Traceable original cost observations under the user's internal-SKU rule."""
from copy import deepcopy

from shared_platform.internal_catalog_sku import internal_sku


def attach_internal_history(review):
    """Share original exact-variant observations, never a current projection.

    Source identities, dates, currency and every competing observation survive.
    This is a cost grouping only: product/variant metadata is never merged.
    """
    groups = {}
    for row in review['records']:
        if not row['identity_valid'] or row['identity']['platform'] not in {'tiktok', 'shopee'}:
            continue
        if any(i['code'] in {'missing_shop_mapping', 'invalid_variant_identity', 'ambiguous_full_identity_or_alias'} and i.get('identity') == row['identity'] for i in review['issues']):
            continue
        code = internal_sku(row['identity']['seller_sku'])
        if not code:
            continue
        for original in row['cost']['candidates']:
            if original['matching_basis'] != 'exact_variant_identity':
                continue
            candidate = deepcopy(original)
            candidate['original_matching_basis'] = candidate['matching_basis']
            candidate['matching_basis'] = 'user_internal_sku_history'
            candidate['historical_internal_sku'] = code
            candidate['historical_cost_basis'] = 'undated_legacy_observation' if not candidate.get('valid_from') and not candidate.get('valid_to') else 'declared_interval_observation'
            # candidate_id identifies the original source row, not a target.
            groups.setdefault(code, {})[candidate['candidate_id']] = candidate
    for row in review['records']:
        code = internal_sku(row['identity']['seller_sku'])
        if not row['identity_valid'] or row['identity']['platform'] not in {'tiktok', 'shopee'} or code not in groups:
            continue
        if any(i['code'] in {'missing_shop_mapping', 'invalid_variant_identity', 'ambiguous_full_identity_or_alias'} and i.get('identity') == row['identity'] for i in review['issues']):
            continue
        originals = {c['candidate_id']: c for c in row['cost']['candidates']}
        originals.update(deepcopy(groups[code]))
        row['cost']['candidates'] = [originals[k] for k in sorted(originals)]
    return review
