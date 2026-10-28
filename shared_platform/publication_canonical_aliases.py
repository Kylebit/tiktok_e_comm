"""Exact publication aliases registered only at the official catalog seam.

Provider seller SKUs remain provider facts. No suffix inference is added here.
The typed authority is derived by the server from pinned immutable R1 bytes;
caller-authored JSON is never an authority for a local alias registration.
"""
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re

SCHEMA = '''
CREATE TABLE IF NOT EXISTS catalog_canonical_bindings (
 identity_key TEXT PRIMARY KEY, canonical_sku TEXT NOT NULL,
 platform TEXT NOT NULL, shop_key TEXT NOT NULL, product_id TEXT NOT NULL,
 variant_id TEXT NOT NULL, offer_id TEXT NOT NULL, seller_sku TEXT NOT NULL,
 binding_json TEXT NOT NULL, binding_digest TEXT NOT NULL, receipt_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS catalog_canonical_bindings_sku
 ON catalog_canonical_bindings(canonical_sku);
'''
_SEAL = object()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _identity(value):
    if not isinstance(value, Mapping):
        raise ValueError('CANONICAL_FULL_OFFICIAL_IDENTITY_REQUIRED')
    keys = ('platform', 'shop_key', 'product_id', 'seller_sku')
    result = {key: value.get(key) for key in keys}
    platform = result['platform']
    if platform == 'ozon':
        if value.get('identity_kind') != 'product_offer' or value.get('variant_id') is not None:
            raise ValueError('CANONICAL_TYPED_OZON_IDENTITY_REQUIRED')
        result.update(identity_kind='product_offer', offer_id=value.get('offer_id'))
        if result['offer_id'] != result['seller_sku']:
            raise ValueError('CANONICAL_PROVIDER_SKU_CHANGED')
    elif platform in {'tiktok', 'shopee'}:
        result['variant_id'] = value.get('variant_id')
        if str(result['variant_id'] or '').startswith('item_'):
            raise ValueError('CANONICAL_FULL_OFFICIAL_IDENTITY_REQUIRED')
    else:
        raise ValueError('CANONICAL_PLATFORM_REQUIRED')
    if any(type(v) is not str or not v or v != v.strip()
           or any(ord(c) < 32 for c in v) for v in result.values()):
        raise ValueError('CANONICAL_FULL_OFFICIAL_IDENTITY_REQUIRED')
    return result


class _FrozenAliasAuthority:
    def __init__(self, seal, *, offer_id, r1_digest, pin, publication_snapshot, models):
        if seal is not _SEAL:
            raise ValueError('CANONICAL_SERVER_AUTHORITY_REQUIRED')
        self.offer_id, self.r1_digest, self.pin = offer_id, r1_digest, pin
        self.snapshot_digest = publication_snapshot['snapshot_digest']
        self.targets = frozenset(t['target_label'] for t in publication_snapshot['publication_targets'])
        self.models = dict(models)

    def _binding(self, row):
        i = _identity(row.get('identity'))
        model = row.get('model_sku')
        pair = self.models.get(model)
        if (row.get('authority') != 'OFFICIAL' or row.get('verified') is not True
                or row.get('target_label') not in self.targets
                or row['target_label'].split(':', 1)[0] != i['platform']
                or i['seller_sku'] != model or pair is None or row.get('variant_key') != pair[0]):
            raise ValueError('CANONICAL_OFFICIAL_FROZEN_VARIANT_CHANGED')
        unsigned = {k: v for k, v in row.items() if k != 'canonical_binding'}
        return {'schema_version': 'publication-canonical-binding/v1',
                'identity': i, 'canonical_sku': pair[1], 'provider_sku': model,
                'provider_shop_id': (row.get('shop_binding') or {}).get('official_shop_id',''),
                'region': (row.get('listing') or {}).get('region',''),
                'offer_id': self.offer_id, 'variant_key': pair[0],
                'round1_snapshot_digest': self.r1_digest,
                'source_identity_binding_sha256': self.pin,
                'publication_snapshot_digest': self.snapshot_digest,
                'official_readback_digest': _digest(unsigned)}

    def bind_rows(self, rows, *, snapshot_digest, target_labels):
        if snapshot_digest != self.snapshot_digest or not set(target_labels) <= self.targets:
            raise ValueError('CANONICAL_PUBLICATION_SCOPE_CHANGED')
        expected = {(target, model) for target in target_labels for model in self.models}
        seen = set()
        bound = []
        identities = set()
        products = {}
        for row in rows:
            key = (row.get('target_label'), row.get('model_sku'))
            identity=_identity(row.get('identity'))
            identity_key = _digest({k:v for k,v in identity.items() if k!='seller_sku'})
            if key not in expected or key in seen or identity_key in identities:
                raise ValueError('CANONICAL_COMPLETE_TARGET_READBACK_REQUIRED')
            seen.add(key);identities.add(identity_key)
            if identity['platform'] in {'tiktok','shopee'}:
                products.setdefault(key[0],set()).add((identity['shop_key'],identity['product_id']))
            value = deepcopy(row)
            value['canonical_binding'] = self._binding(value)
            bound.append(value)
        if seen != expected or not expected:
            raise ValueError('CANONICAL_COMPLETE_TARGET_READBACK_REQUIRED')
        if any(len(value)!=1 for value in products.values()):
            raise ValueError('CANONICAL_SINGLE_PRODUCT_READBACK_REQUIRED')
        return bound

    def validate_packet(self, packet):
        if packet.get('offer_id') != self.offer_id or packet.get('snapshot_digest') != self.snapshot_digest:
            raise ValueError('CANONICAL_PUBLICATION_SCOPE_CHANGED')
        rows = packet.get('rows') or []
        targets = tuple(sorted({r.get('target_label') for r in rows}))
        bound = self.bind_rows(rows, snapshot_digest=packet['snapshot_digest'], target_labels=targets)
        if any(r.get('canonical_binding') != v['canonical_binding'] for r,v in zip(rows,bound)):
            raise ValueError('CANONICAL_BINDING_CHANGED')


def create_frozen_alias_authority(*, offer_id, round1_snapshot, state, report_directory,
                                  expected_binding_sha256, publication_snapshot):
    from shared_platform.publication_variant_identity import read_explicit_variant_aliases
    from shared_platform.publication_rounds import canonical_digest
    resolved = read_explicit_variant_aliases(offer_id=offer_id, round1_snapshot=round1_snapshot,
        state=state, report_directory=report_directory,
        expected_binding_sha256=expected_binding_sha256)
    final = publication_snapshot
    if (not isinstance(final, Mapping) or final.get('schema_version') != 'approved-publication-snapshot/v4'
            or final.get('offer_id') != offer_id
            or final.get('snapshot_digest') != canonical_digest({k:v for k,v in final.items() if k!='snapshot_digest'})):
        raise ValueError('CANONICAL_FINAL_SNAPSHOT_CHANGED')
    reverse = {v:k for k,v in resolved['canonical_to_provider_sku'].items()}
    rows = final.get('skus') or []
    if len(rows) != len(reverse) or {r.get('model_sku') for r in rows} != set(reverse):
        raise ValueError('CANONICAL_FINAL_VARIANTS_CHANGED')
    models = {r['model_sku']: (r.get('variant_key'), reverse[r['model_sku']]) for r in rows}
    if any(type(v[0]) is not str or not v[0] for v in models.values()) or len({v[0] for v in models.values()}) != len(models):
        raise ValueError('CANONICAL_FINAL_VARIANTS_CHANGED')
    if {r['variant_key']:r['model_sku'] for r in rows} != resolved['variant_key_to_model_sku']:
        raise ValueError('CANONICAL_FINAL_VARIANT_LINEAGE_CHANGED')
    targets=final.get('publication_targets') or []
    frozen_targets=round1_snapshot.get('canonical_targets')
    from shared_platform.prepared_target_consumer import configured_target_consumer,effective_marketplace_targets
    if isinstance(frozen_targets,list) and configured_target_consumer(offer_id) is not None:
        frozen_targets=effective_marketplace_targets(offer_id,[label for label in frozen_targets if label!='miaoshou:COMMON'])
    if (not isinstance(frozen_targets,list) or not frozen_targets
            or len(targets)!=len(frozen_targets)
            or {r.get('target_label') for r in targets}!=set(frozen_targets)):
        raise ValueError('CANONICAL_FINAL_TARGETS_CHANGED')
    return _FrozenAliasAuthority(_SEAL, offer_id=offer_id, r1_digest=round1_snapshot['snapshot_digest'],
        pin=str(expected_binding_sha256).removeprefix('sha256:'), publication_snapshot=final, models=models)


def validate_projection_authority(packet, authority):
    if not isinstance(packet, Mapping):
        return False
    if any('canonical_binding' in row for row in packet.get('rows') or []):
        if type(authority) is not _FrozenAliasAuthority:
            raise ValueError('CANONICAL_SERVER_AUTHORITY_REQUIRED')
        authority.validate_packet(packet)
        return True
    return False


def register_binding(conn, row, receipt_id, *, packet, authority):
    if (not validate_projection_authority(packet,authority)
            or row not in packet['rows'] or receipt_id!=packet.get('receipt_id')):
        raise ValueError('CANONICAL_SERVER_AUTHORITY_REQUIRED')
    binding = row['canonical_binding']
    i = _identity(row['identity'])
    key, digest = _digest(i), _digest(binding)
    old = conn.execute('SELECT binding_json FROM catalog_canonical_bindings WHERE identity_key=?', (key,)).fetchone()
    if old:
        before = json.loads(old[0])
        if any(before.get(k) != binding.get(k) for k in ('identity','canonical_sku','provider_sku','offer_id','variant_key','round1_snapshot_digest','source_identity_binding_sha256')):
            raise ValueError('CANONICAL_REGISTERED_IDENTITY_CONFLICT')
        return
    # The same provider product/variant identity cannot acquire a changed raw
    # seller SKU through a second registration.
    drift = conn.execute('SELECT seller_sku FROM catalog_canonical_bindings WHERE platform=? AND shop_key=? AND product_id=? AND variant_id=? AND offer_id=?',
        (i['platform'],i['shop_key'],i['product_id'],i.get('variant_id',''),i.get('offer_id',''))).fetchall()
    if drift:
        raise ValueError('CANONICAL_REGISTERED_IDENTITY_CONFLICT')
    conn.execute('INSERT INTO catalog_canonical_bindings VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (key,binding['canonical_sku'],i['platform'],i['shop_key'],i['product_id'],i.get('variant_id',''),i.get('offer_id',''),
         i['seller_sku'],json.dumps(binding,ensure_ascii=False,sort_keys=True,separators=(',',':')),digest,receipt_id))


class CanonicalAliasRegistry:
    def __init__(self, seal, rows):
        if seal is not _SEAL:
            raise ValueError('CANONICAL_REGISTERED_SNAPSHOT_REQUIRED')
        self._rows = tuple(deepcopy(rows))
        self._by_key = {_digest(r['identity']):r for r in self._rows}

    def resolve(self, identity):
        found=self.binding_for(identity)
        return found['canonical_sku'] if found else None

    def binding_for(self, identity):
        try:
            i = _identity(identity)
        except ValueError:
            return None
        found = self._by_key.get(_digest(i))
        if not found and i['platform']=='tiktok':
            # Official shop ID and cipher are separate identifiers. The only
            # accepted alternate is the same captured, registered official join.
            matches=[r for r in self._rows if r.get('provider_shop_id')==i['shop_key']
                and all(r['identity'].get(k)==i.get(k) for k in ('platform','product_id','variant_id','seller_sku'))]
            found=matches[0] if len(matches)==1 else None
        return deepcopy(found) if found else None

    @property
    def digest(self):
        return _digest(sorted(self._rows,key=lambda r:_digest(r['identity'])))

    def rows(self):
        return deepcopy(self._rows)


def read_alias_registry(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_canonical_bindings'").fetchone()
    rows = []
    if exists:
        for record in conn.execute('SELECT * FROM catalog_canonical_bindings'):
            r = dict(record)
            binding = json.loads(r['binding_json'])
            if (_digest(binding) != r['binding_digest'] or _digest(_identity(binding['identity'])) != r['identity_key']
                    or binding.get('schema_version') != 'publication-canonical-binding/v1'
                    or not re.fullmatch('[0-9]{4}', str(binding.get('canonical_sku') or ''))):
                raise ValueError('CANONICAL_REGISTERED_BINDING_CORRUPT')
            rows.append(binding)
    return CanonicalAliasRegistry(_SEAL, rows)


def load_alias_registry(path):
    from core.db import connect_readonly
    conn = connect_readonly(path)
    try:
        conn.execute('BEGIN')
        return read_alias_registry(conn)
    finally:
        conn.close()


def canonical_for_identity(conn, identity, *, registry=None):
    registered = (registry if registry is not None else read_alias_registry(conn)).resolve(identity)
    if registered:
        return registered
    from shared_platform.internal_catalog_sku import internal_sku
    legacy = internal_sku(identity.get('seller_sku'))
    return '' if needs_registered_alias(identity.get('seller_sku')) else legacy


def resolve_order_alias(registry, *, platform, shop_key, product_id, variant_id, seller_sku):
    if type(registry) is not CanonicalAliasRegistry:
        return None
    return registry.resolve({'platform':platform,'shop_key':str(shop_key or ''),
        'product_id':str(product_id or ''),'variant_id':str(variant_id or ''),'seller_sku':str(seller_sku or '')})


def needs_registered_alias(raw):
    from shared_platform.internal_catalog_sku import internal_sku
    # This publication alias seam governs explicit longer numeric provider
    # codes. Keep unrelated legacy textual SKU diagnostics unchanged.
    value=str(raw or '').strip()
    return bool(re.fullmatch('[0-9]{5,32}',value)) and not re.fullmatch('[0-9]{4}', internal_sku(value))


def registered_catalog_line(catalog, *, platform, shop_key, product_id, variant_id, seller_sku):
    registry = getattr(catalog, 'canonical_alias_registry', None)
    if type(registry) is not CanonicalAliasRegistry:
        return None
    i = _identity({'platform':platform,'shop_key':str(shop_key),'product_id':str(product_id),
                   'variant_id':str(variant_id),'seller_sku':str(seller_sku)})
    binding=registry.binding_for(i)
    metadata = getattr(catalog, 'product_by_identity', {}).get(_digest(binding['identity'])) if binding else None
    return (binding['canonical_sku'], deepcopy(metadata)) if metadata else None
