"""Exact frozen internal-SKU/provider-alias binding; never suffix inference."""
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re


def resolve_explicit_variant_aliases(*, offer_id, round1_snapshot, identity_binding):
    from shared_platform.publication_rounds import canonical_digest
    snapshot = round1_snapshot
    binding = identity_binding
    if not isinstance(snapshot, Mapping) or not isinstance(binding, Mapping):
        raise ValueError('EXPLICIT_VARIANT_IDENTITY_REQUIRED')
    unsigned = {key: value for key, value in snapshot.items() if key != 'snapshot_digest'}
    if (binding.get('schema') != 'orbit-new-B-R1-source-and-identity-binding/v1'
            or snapshot.get('offer_id') != offer_id or binding.get('offer_id') != offer_id
            or snapshot.get('snapshot_digest') != canonical_digest(unsigned)
            or (binding.get('snapshot') or {}).get('snapshot_digest') != snapshot.get('snapshot_digest')):
        raise ValueError('EXPLICIT_VARIANT_SNAPSHOT_CHANGED')
    aliases = binding.get('canonical_to_B_provider_sku')
    if (not isinstance(aliases, dict) or not aliases
            or any(type(key) is not str or not re.fullmatch(r'[0-9]{4}', key)
                   or type(value) is not str or not re.fullmatch(r'[0-9]{1,32}', value)
                   or not value.endswith(key) for key, value in aliases.items())
            or len(set(aliases.values())) != len(aliases)):
        raise ValueError('EXPLICIT_VARIANT_ALIAS_MAP_INVALID')
    positions = binding.get('source_positions')
    if (not isinstance(positions, list) or len(positions) != len(aliases)
            or {row.get('internal_sku') for row in positions if isinstance(row, Mapping)} != set(aliases)
            or [row.get('position') for row in positions if isinstance(row, Mapping)] != list(range(1, len(aliases) + 1))):
        raise ValueError('EXPLICIT_VARIANT_SOURCE_MAP_CHANGED')
    facts = (snapshot.get('fact_snapshot') or {}).get('product_facts') or {}
    rows = facts.get('skus')
    seller = facts.get('seller_sku')
    reverse = {value: key for key, value in aliases.items()}
    if not isinstance(rows, list) or len(rows) != len(aliases) or seller not in reverse:
        raise ValueError('EXPLICIT_VARIANT_FROZEN_SKUS_CHANGED')
    models, internal = {}, {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError('EXPLICIT_VARIANT_FROZEN_SKUS_CHANGED')
        key, model = row.get('source_key'), row.get('seller_sku')
        if not isinstance(key, str) or not key or key in models or model not in reverse:
            raise ValueError('EXPLICIT_VARIANT_FROZEN_SKUS_CHANGED')
        models[key], internal[key] = model, reverse[model]
    if set(models.values()) != set(reverse):
        raise ValueError('EXPLICIT_VARIANT_FROZEN_SKUS_CHANGED')
    return {'seller_sku': seller, 'internal_sku': reverse[seller],
            'variant_key_to_internal_sku': internal, 'variant_key_to_model_sku': models,
            'canonical_to_provider_sku': dict(aliases)}


def read_explicit_variant_aliases(*, offer_id, round1_snapshot, state,
                                report_directory, expected_binding_sha256):
    """Consume a server-pinned immutable companion, not caller-authored JSON."""
    from shared_platform.immutable_approval_files import require_local_path
    expected = str(expected_binding_sha256 or '').removeprefix('sha256:')
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('EXPLICIT_VARIANT_BINDING_PIN_REQUIRED')
    directory = Path(report_directory)
    path = directory / 'SOURCE_AND_IDENTITY_BINDING.json'
    require_local_path(path, root=directory)
    raw = path.read_bytes()
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('EXPLICIT_VARIANT_BINDING_BYTES_CHANGED')
    binding = json.loads(raw.decode('utf-8'))
    source = directory / 'round1-approved-snapshot.json'
    require_local_path(source, root=directory)
    snapshot_raw = source.read_bytes()
    if (hashlib.sha256(snapshot_raw).hexdigest() != (binding.get('snapshot') or {}).get('sha256')
            or json.loads(snapshot_raw.decode('utf-8')) != round1_snapshot
            or (state.get('new_b_identity') or {}).get('canonical_to_B_provider_sku')
                != binding.get('canonical_to_B_provider_sku')):
        raise ValueError('EXPLICIT_VARIANT_SOURCE_BINDING_CHANGED')
    result = resolve_explicit_variant_aliases(offer_id=offer_id,
        round1_snapshot=round1_snapshot, identity_binding=binding)
    if (state.get('review') or {}).get('seller_sku') != result['seller_sku']:
        raise ValueError('EXPLICIT_VARIANT_SELLER_CHANGED')
    return result
