"""Pure, unapproved preview of a possible single R2/COMMON/marketplace review.

This module neither records a decision nor supplies authority to existing writers.
Consumers must separately perform durable CAS, official readback and reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from shared_platform.publication_r3_image_bridge import (
    build_dual_brand_publication_bridge, validate_r2_identity,
)
from shared_platform.release_store import preview_release_plan


def _required(mapping: Mapping[str, Any], key: str) -> Any:
    if not isinstance(mapping, Mapping) or key not in mapping or mapping[key] is None:
        raise ValueError('FINAL_REVIEW_FIELD_REQUIRED: ' + key)
    return mapping[key]


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as error:
        raise ValueError('FINAL_REVIEW_NOT_CANONICAL_JSON') from error


def _digest(value: bytes) -> str:
    return 'sha256:' + sha256(value).hexdigest()


def _fields(source: Any, names: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    return {name: source[name] for name in names if name in source}


def _business_dashboard_facts(dashboard: Mapping[str, Any]) -> dict[str, Any]:
    """Only offer-specific inputs relevant to the review; omit display/rollup state."""
    from domains.content_operations import release_listing_copy_identity

    product = _required(dashboard, 'product')
    content = _required(dashboard, 'content')
    scope = _required(dashboard, 'publication_scope')
    pricing = _required(dashboard, 'pricing_review')
    source = _required(dashboard, '_source_identity_inputs')
    if any(not isinstance(value, Mapping) for value in (product, content, scope, pricing, source)):
        raise ValueError('FINAL_REVIEW_BUSINESS_FACTS_INVALID')
    product_facts = _fields(product, (
        'offer_id', 'seller_sku_candidate', 'revision', 'title', 'source_title_zh', 'category',
        'actual_product_approved', 'cost_cny', 'weight_kg', 'package_cm',
        'selected_sku_keys', 'sku_label_overrides',
        'stock_policy', 'ozon_stock_decision',
    ))
    product_facts['actual_approval'] = _fields(product.get('actual_approval'), (
        'approval_id', 'package_id', 'input_fingerprint'))
    commercial = product.get('sku_commercial_facts') or {}
    if not isinstance(commercial, Mapping):
        raise ValueError('FINAL_REVIEW_SKU_FACTS_INVALID')
    product_facts['sku_commercial_facts'] = {
        str(key): {
            'cost_cny': row.get('cost_cny'),
            'weight_kg': row.get('weight_kg'),
            'package_cm': list(row.get('package_cm') or ()),
        }
        for key, row in commercial.items() if isinstance(row, Mapping)
    }
    selected = product.get('selected_sku_keys')
    if (not isinstance(selected, list) or not selected
            or any(type(key) is not str or not key for key in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError('FINAL_REVIEW_SELECTED_SKUS_INVALID')
    selected_keys = set(selected)
    selected_rows = [
        _fields(row, ('key', 'label', 'model_sku', 'price_cny', 'commercial_facts'))
        for row in product.get('source_skus') or []
        if isinstance(row, Mapping) and str(row.get('key') or '') in selected_keys
    ]
    if (len(selected_rows) != len(selected)
            or {row.get('key') for row in selected_rows} != selected_keys):
        raise ValueError('FINAL_REVIEW_SELECTED_SKU_ROWS_CONFLICT')
    product_facts['selected_source_skus'] = selected_rows
    content_facts = _fields(content, (
        'approved', 'approval_status', 'package_id', 'strategy', 'video_urls'))
    content_facts['images'] = [
        _fields(row, ('position', 'image_url', 'artifact_id', 'audit_id',
                      'asset_type', 'decision_source'))
        for row in content.get('images') or []
    ]
    listing = dashboard.get('listing_copy') or {}
    listing_facts, _ = release_listing_copy_identity(
        listing, approved_product_title=product.get('title'),
        current_input_signature=listing.get('current_input_signature'),
        target_labels=scope.get('selected_labels') or [],
    )
    collect_box = source.get('collect_box') or {}
    precollect = source.get('precollect') or {}
    source_record = source.get('source_record') or {}
    source_facts = {
        'collect_box': _fields(collect_box, ('source_item_id', 'source_item_code')),
        'precollect_records': [_fields(row, ('source_id',))
                               for row in precollect.get('records') or []],
        'source_record': _fields(source_record, ('source_id',)),
        'source_authority': source.get('source_authority'),
    }
    resolution = dashboard.get('_source_product_identity') or {}
    lineage = dashboard.get('_sku_lineage') or {}
    target_pricing = pricing.get('target_pricing') or {}
    if not isinstance(target_pricing, Mapping):
        raise ValueError('FINAL_REVIEW_PRICING_INVALID')
    selected_target_pricing = {
        label: target_pricing.get(label) or {}
        for label in scope.get('selected_labels') or []
    }
    omnichannel = dashboard.get('omnichannel_preview') or {}
    return {
        'product': product_facts,
        'content': content_facts,
        'publication_scope': _fields(scope, ('selected_labels',)),
        'pricing': {
            **_fields(pricing, ('schema_version', 'sku_pricing', 'master_price_source',
                                'workbench_exchange_rates', 'shopee_exchange_rates',
                                'ozon_exchange_rates')),
            'selected_target_pricing': selected_target_pricing,
        },
        'listing_copy': listing_facts,
        'source_inputs': source_facts,
        'source_identity': _fields(resolution, ('status', 'ready', 'identity')),
        'sku_lineage': _fields(lineage, (
            'status', 'ready', 'source_identity_digest', 'lineage_mode', 'assignment',
            'predecessor_id', 'predecessor_revision', 'predecessor_digest')),
        'omnichannel_scope_digest': (omnichannel.get('approval_summary') or {}).get(
            'approval_scope_digest'),
    }


@dataclass(frozen=True, slots=True)
class FinalReviewPreview:
    """Owned canonical bytes; callers receive copies and cannot mutate the contract."""

    _canonical_bytes: bytes
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical_bytes)


def build_final_review_preview(*, documents: Mapping[str, Any], dashboard: Mapping[str, Any],
                               common_view: Mapping[str, Any]) -> FinalReviewPreview:
    """Reject missing or drifting evidence before constructing an inert candidate."""
    identity = validate_r2_identity(documents)
    r1 = _required(documents, 'round1_snapshot')
    product = _required(dashboard, 'product')
    common = _required(common_view, 'common')
    if (common_view.get('ok') is not True or common.get('status') != 'APPROVAL_REQUIRED'
            or common_view.get('external_writes_performed') != []):
        raise ValueError('FINAL_REVIEW_COMMON_UNAPPROVED_PREVIEW_REQUIRED')
    plan = _required(common, 'plan')
    payload = _required(plan, 'payload')
    binding = _required(payload, 'r3_stage_binding')
    offer = _required(r1, 'offer_id')
    revision = _required(product, 'revision')
    targets = _required(r1, 'canonical_targets')
    _required(payload, 'product_facts')
    seller_sku = _required(payload, 'seller_sku')
    if (type(revision) is not int or revision < _required(r1, 'approved_product_center_revision')
            or product.get('offer_id') != offer or payload.get('product_id') != offer
            or payload.get('product_revision') != revision
            or product.get('seller_sku_candidate') != seller_sku
            or plan.get('product_id') != offer or plan.get('seller_sku') != seller_sku):
        raise ValueError('FINAL_REVIEW_OFFER_REVISION_SKU_CONFLICT')
    if (not isinstance(targets, list) or not targets or len(set(targets)) != len(targets)
            or any(type(row) is not str or not re.fullmatch(r'[a-z0-9_-]+:[A-Za-z0-9_-]+', row)
                   for row in targets)
            or binding.get('schema_version') != 'r3-common-stage/v1'
            or binding.get('execution_scope') != ['miaoshou:COMMON']
            or binding.get('marketplace_targets') != targets
            or binding.get('round1_snapshot_digest') != r1['snapshot_digest']
            or binding.get('r2_identity') != identity
            or common_view.get('offer_id') != offer
            or common.get('execution_scope') != ['miaoshou:COMMON']
            or (common_view.get('marketplace') or {}).get('targets') != targets
            or payload.get('targets') != ['miaoshou:COMMON']
            or plan.get('targets') != ['miaoshou:COMMON']):
        raise ValueError('FINAL_REVIEW_SCOPE_OR_EVIDENCE_CONFLICT')
    # Recreate the ordinary Store preview without opening its SQLite database.
    expected_plan = preview_release_plan(payload)
    if (plan.get('approved') is not False
            or plan.get('persisted') is not False
            or plan != expected_plan
            or not plan['plan_id'] or not plan['confirmation_token'] or not plan['payload_digest']):
        raise ValueError('FINAL_REVIEW_COMMON_PLAN_CONFLICT')

    images = []
    for kind, key in (('master', 'generation_result'), ('localized', 'translation_result')):
        assets = _required(_required(documents, key), 'assets')
        if not isinstance(assets, list) or not assets:
            raise ValueError('FINAL_REVIEW_IMAGES_REQUIRED')
        for asset in assets:
            digest = _required(asset, 'artifact_digest')
            if type(digest) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise ValueError('FINAL_REVIEW_IMAGE_DIGEST_INVALID')
            images.append({
                'kind': kind,
                'review_number': _required(asset, 'review_number') if kind == 'master'
                                 else _required(asset, 'source_review_number'),
                'brand_id': _required(asset, 'brand_id'),
                'role': _required(asset, 'role'),
                'locale': asset.get('locale') if kind == 'localized' else None,
                'artifact_digest': digest,
            })
    route = build_dual_brand_publication_bridge(
        offer_id=offer, first_review=documents['first_review'],
        generation=documents['generation_result'], translation=documents['translation_result'],
        approved_by=r1['approved_by'],
    )
    if route['targets'] != targets or set(route['image_routes']) != set(targets):
        raise ValueError('FINAL_REVIEW_IMAGE_TARGETS_CONFLICT')
    image_routes = {}
    for target in targets:
        rows = route['image_routes'][target]
        if not rows:
            raise ValueError('FINAL_REVIEW_IMAGE_ROUTE_REQUIRED')
        image_routes[target] = [
            {'position': _required(row, 'position'), 'brand_id': _required(row, 'brand_id'),
             'role': _required(row, 'role'), 'artifact_digest': _required(row, 'artifact_digest'),
             'source_review_number': _required(row, 'source_review_number'),
             'locale': _required(route['route_locales'], target)}
            for row in rows
        ]
    body = {
        'schema_version': 'publication-final-review-preview/v1',
        'status': 'UNAPPROVED_PREVIEW', 'execution_authority': False,
        'external_writes_performed': [],
        'offer_id': offer, 'seller_sku': seller_sku, 'product_revision': revision,
        'current_business_facts_digest': _digest(_canonical(_business_dashboard_facts(dashboard))),
        'round1_snapshot_digest': r1['snapshot_digest'],
        'round1_source_approval': {
            'actor': _required(r1, 'approved_by'),
            'authority': r1.get('approval_authority'),
            'human_approval': r1.get('human_approval'),
        },
        'r2_identity': identity, 'r2_images': images, 'r2_target_image_routes': image_routes,
        'marketplace_targets': targets,
        'common': {
            'plan_id': plan['plan_id'], 'payload_digest': plan['payload_digest'],
            'confirmation_token_digest': _digest(plan['confirmation_token'].encode('utf-8')),
            'payload': payload,
        },
        'expected_write_scope': [
            {'stage': 'R3_COMMON', 'target': 'miaoshou:COMMON',
             'operation': 'common_draft_sync'},
            *[{'stage': 'R3_MARKETPLACE', 'target': target,
               'operation': 'target_publication'} for target in targets],
        ],
    }
    encoded = _canonical(body)
    return FinalReviewPreview(encoded, _digest(encoded))


def require_current_final_review_preview(preview: FinalReviewPreview, *, documents: Mapping[str, Any],
                                         dashboard: Mapping[str, Any],
                                         common_view: Mapping[str, Any]) -> bool:
    """Rebuild from current evidence; no stale preview can gain authority."""
    if (type(preview) is not FinalReviewPreview
            or type(preview._canonical_bytes) is not bytes
            or type(preview.digest) is not str
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', preview.digest)
            or _digest(preview._canonical_bytes) != preview.digest):
        raise ValueError('FINAL_REVIEW_PREVIEW_INVALID')
    current = build_final_review_preview(documents=documents, dashboard=dashboard,
                                         common_view=common_view)
    if (current.digest != preview.digest
            or current._canonical_bytes != preview._canonical_bytes):
        raise ValueError('FINAL_REVIEW_PREVIEW_STALE')
    return True
