"""Parse retained Ozon JSON at an explicit base, without config/auth setup."""
from __future__ import annotations

import json
from pathlib import Path

from modules.catalog.sku_key import tk_match_key

OZON_OFFLINE_READER_CONTRACT = 'orbit-ozon-captured-data/v1'


def _attrs_items(base: Path) -> list[dict]:
    attrs_path = base / 'all_products_attrs.json'
    if not attrs_path.is_file():
        return []
    try:
        data = json.loads(attrs_path.read_text(encoding='utf-8'))
        items = data.get('result') if isinstance(data, dict) else data
        return items if isinstance(items, list) else []
    except json.JSONDecodeError:
        return []


def load_ozon_from_directory(base: Path, *, path_guard=None, attrs_reader=None, key_resolver=tk_match_key):
    """Default callers retain their resolver/setup; scoped callers supply base."""
    base = Path(base)
    for name in ('all_products_attrs.json', 'migrated_offers.json', 'tk_sku_map.json'):
        if path_guard:
            path_guard(base/name)
    migrated: set[str] = set()
    mig_path = base / 'migrated_offers.json'
    if mig_path.is_file():
        if path_guard:
            path_guard(mig_path)
        try:
            raw = json.loads(mig_path.read_text(encoding='utf-8'))
            migrated = set(str(x) for x in raw) if isinstance(raw, list) else set()
        except json.JSONDecodeError:
            migrated = set()
    tk_map: dict = {}
    tk_path = base / 'tk_sku_map.json'
    if tk_path.is_file():
        if path_guard:
            path_guard(tk_path)
        try:
            tk_map = json.loads(tk_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            tk_map = {}
    out: dict[str, dict] = {}
    if path_guard:
        path_guard(base/'all_products_attrs.json')
    for it in (attrs_reader or _attrs_items)(base):
        oid = str(it.get('offer_id') or '').strip()
        mk = key_resolver(oid)
        if not mk:
            continue
        imgs = it.get('images') or []
        image_url = it.get('primary_image') or (imgs[0] if imgs else '')
        out[mk] = {
            'platform': 'ozon', 'match_key': mk, 'seller_sku': oid, 'offer_id': oid,
            'product_id': str(it.get('sku') or it.get('id') or ''),
            'product_name': (it.get('name') or '').strip(), 'image_url': image_url,
            'migrated': True, 'status': 'live', 'tk_id': '',
        }
        migrated.add(oid)
    for key, row in tk_map.items():
        if not isinstance(row, dict):
            continue
        seller_sku = str(row.get('seller_sku') or '').strip()
        match_key = key_resolver(seller_sku) or str(key).zfill(4)
        if match_key in out:
            if not out[match_key].get('tk_id') and row.get('tk_id'):
                out[match_key]['tk_id'] = str(row.get('tk_id') or '')
            continue
        imgs = row.get('image_urls') or []
        offer_id = seller_sku or match_key
        migrated_flag = offer_id in migrated
        out[match_key] = {
            'platform': 'ozon', 'match_key': match_key, 'seller_sku': seller_sku,
            'offer_id': offer_id, 'product_id': str(row.get('tk_id') or ''),
            'product_name': (row.get('title') or '').strip(),
            'image_url': imgs[0] if imgs else '', 'migrated': migrated_flag,
            'status': 'live' if migrated_flag else 'pending', 'tk_id': str(row.get('tk_id') or ''),
        }
    return out
