"""Reuse reviewed variant identities; never allocate replacements for frozen R1."""
from collections.abc import Mapping

from shared_platform.publication_rounds import ROUND1_SCHEMA, canonical_digest


def frozen_model_skus(snapshot, *, offer_id, state, source, seller_sku):
    """Validate the explicit, caller-authenticated R1 against current local facts.

    Caller must load this snapshot through the R2 domain identity contract. This
    function neither discovers a different snapshot nor treats a digest as approval.
    """
    def reject():
        raise ValueError('FROZEN_R1_SKU_ASSIGNMENT_IDENTITY_CONFLICT')

    if not isinstance(snapshot, Mapping):
        reject()
    unsigned = dict(snapshot)
    digest = unsigned.pop('snapshot_digest', None)
    approval = state.get('product_approval') or {}
    review = state.get('review') or {}
    facts = (snapshot.get('fact_snapshot') or {}).get('product_facts') or {}
    if (snapshot.get('schema_version') != ROUND1_SCHEMA
        or snapshot.get('status') != 'APPROVED'
        or digest != canonical_digest(unsigned)
        or snapshot.get('offer_id') != offer_id
        or state.get('offer_id') != offer_id
        or approval.get('status') != 'approved'
        or not snapshot.get('product_approval_id')
        or not snapshot.get('product_approval_fingerprint')
        or approval.get('approval_id') != snapshot['product_approval_id']
        or approval.get('input_fingerprint') != snapshot['product_approval_fingerprint']
        or review.get('fields_locked') is not True
        or review.get('seller_sku') != seller_sku
        or facts.get('seller_sku') != seller_sku
        or sorted(review.get('selected_sites') or []) != sorted(snapshot.get('workbench_tiktok_sites') or [])):
        reject()
    selected = review.get('selected_sku_keys')
    rows = facts.get('skus')
    if (not isinstance(selected, list) or not selected
        or not all(isinstance(key, str) and key for key in selected)
        or len(set(selected)) != len(selected)
        or not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows)):
        reject()
    frozen = {row.get('source_key'): row.get('seller_sku') for row in rows}
    source_keys = [str(row.get('key') or row.get('name') or '').strip()
                   for row in source.get('skus') or [] if isinstance(row, Mapping)]
    if (len(frozen) != len(rows) or set(frozen) != set(selected)
        or any(source_keys.count(key) != 1 for key in selected)
        or not all(isinstance(sku, str) and sku.isascii() and sku.isdigit() for sku in frozen.values())
        or len(set(frozen.values())) != len(frozen)):
        reject()
    return [frozen[key] for key in selected]
