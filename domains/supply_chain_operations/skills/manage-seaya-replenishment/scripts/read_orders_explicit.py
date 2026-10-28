"""Supply-only, explicit-source reads. Never refresh tokens or discover paths.

This entry does not import auth/shops, acquire a business lease, connect a DB,
start a server, or apply a dashboard. Profile/credential changes fail closed.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
ENTRY = Path(__file__).resolve()
ENTRY_SHA256 = hashlib.sha256(ENTRY.read_bytes()).hexdigest()
REGIONS = ('MY', 'TH', 'VN', 'PH')
WAREHOUSES = dict(zip(REGIONS, ('MY8803', 'TH8806', 'VN8805', 'PH8807')))
IDENTITY_PATH = '/authorization/202309/shops'


class NotReady(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise NotReady(message)


def absolute(value, label, *, file=False):
    require(type(value) is str and bool(value), f'{label}: explicit absolute path required')
    p = Path(value)
    require(p.is_absolute() and '..' not in p.parts, f'{label}: absolute path without traversal required')
    require(not p.is_symlink() and p.resolve() == p.absolute(), f'{label}: alias/junction path rejected')
    if file:
        require(p.is_file(), f'{label}: file unavailable')
    return p


def load_profile(path):
    p = absolute(str(path), 'profile', file=True)
    value = json.loads(p.read_text(encoding='utf-8'))
    require(type(value) is dict and set(value) == {'schema', 'source_root', 'settings_path', 'token_paths', 'output_root', 'targets'}, 'profile fields ambiguous')
    require(value['schema'] == 'supply-read-profile/v1', 'profile schema unsupported')
    require(absolute(value['source_root'], 'source_root') == ROOT, 'profile source differs from executing source; no C/D/E fallback')
    settings = absolute(value['settings_path'], 'settings_path', file=True)
    require(type(value['token_paths']) is dict and set(value['token_paths']) == {'tiktok', 'shopee'}, 'two exact token paths required')
    tokens = {name: absolute(raw, name + '_token', file=True) for name, raw in value['token_paths'].items()}
    output = absolute(value['output_root'], 'output_root')
    require(output != ROOT and not output.is_relative_to(ROOT), 'runtime output must be outside code checkout')
    require(all(output != item and not item.is_relative_to(output) for item in (settings, *tokens.values())), 'output overlaps credential/config directory')
    targets = value['targets']
    require(type(targets) is dict and set(targets) == set(REGIONS), 'four exact country targets required')
    for region, target in targets.items():
        require(type(target) is dict and set(target) == {'warehouse', 'shopee_shop_id', 'tiktok_shop_id', 'tiktok_cipher'}, 'target fields ambiguous')
        require(target['warehouse'] == WAREHOUSES[region] and type(target['shopee_shop_id']) is int and target['shopee_shop_id'] > 0, 'warehouse/shop scope invalid')
        require(all(v is None or (type(v) is str and v.strip() and not any(x in v for x in ('...', '…', '*'))) for v in (target['tiktok_shop_id'], target['tiktok_cipher'])), 'TikTok identity invalid')
    require(len({t['shopee_shop_id'] for t in targets.values()}) == 4, 'Shopee target reused across countries')
    for field in ('tiktok_shop_id', 'tiktok_cipher'):
        values = [t[field] for t in targets.values() if t[field] is not None]
        require(len(set(values)) == len(values), 'TikTok target reused across countries')
    inherited = os.environ.get('ORBIT_HIVE_SETTINGS')
    require(not inherited or Path(inherited).resolve() == settings, 'inherited settings conflict; start a fresh process with the exact settings')
    return value


def bind_runtime(profile):
    os.environ['ORBIT_HIVE_SETTINGS'] = profile['settings_path']
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # Config provenance is frozen before any API module import.
    from core.config import load_settings
    settings = load_settings()
    require(bool(settings.get('app_key') and settings.get('app_secret')), 'TikTok app config unavailable')
    return settings


def inspect_sessions(profile, *, platform='all', now=None):
    """Read only existing files; return secrets separately from safe diagnostics."""
    now = now or datetime.now(timezone.utc)
    states, selected, fingerprints = {}, {}, {}
    for name in ('tiktok', 'shopee'):
        if platform not in ('all', name):
            continue
        p = Path(profile['token_paths'][name])
        try:
            raw = p.read_bytes()
            store = json.loads(raw)
        except (OSError, ValueError):
            states[name] = {'state': 'BLOCKED_CREDENTIAL_READ', 'path': str(p)}
            continue
        fingerprints[p] = hashlib.sha256(raw).hexdigest()
        if name == 'tiktok':
            entries = {'selected': (store.get('access_token'), store.get('access_token_expire_in'))}
        else:
            entries = {r: ((store.get('shops', {}).get(str(t['shopee_shop_id'])) or {}).get('access_token'),
                           (store.get('shops', {}).get(str(t['shopee_shop_id'])) or {}).get('expire_at')) for r, t in profile['targets'].items()}
        states[name] = {}
        for region, (token, expiry) in entries.items():
            valid = type(token) is str and bool(token.strip()) and type(expiry) is int and expiry > now.timestamp() + 300
            state = 'READY_EXISTING_SESSION' if valid else 'PENDING_AUTH_RECOVERY'
            states[name][region] = {'state': state, 'expiresAt': datetime.fromtimestamp(expiry, timezone.utc).isoformat() if type(expiry) is int and 0 < expiry < 253402300800 else None}
            if valid:
                selected[(name, region)] = token
        # A recovery journal can describe an uncertain rotated token. Never ignore it.
        journal = p.with_name(p.name + '.refresh-state.json')
        if journal.exists():
            try:
                journal_raw = journal.read_bytes()
                recovery = json.loads(journal_raw)
                fingerprints[journal] = hashlib.sha256(journal_raw).hexdigest()
                if name == 'shopee':
                    blocked = shopee_recovery_blocks(recovery, store, profile['targets'])
                    for region in blocked:
                        states[name][region]['state'] = 'BLOCKED_RECONCILIATION'
                        selected.pop((name, region), None)
                    if blocked:
                        states[name]['recovery'] = {'state': 'BLOCKED_RECONCILIATION', 'path': str(journal), 'regions':blocked}
                elif recovery.get('status') not in ('COMPLETED', 'READY'):
                    states[name]['recovery'] = {'state': 'BLOCKED_RECONCILIATION', 'path': str(journal)}
                    selected = {k: v for k, v in selected.items() if k[0] != name}
            except (OSError, ValueError):
                states[name]['recovery'] = {'state': 'BLOCKED_RECONCILIATION_READ', 'path': str(journal)}
                selected = {k: v for k, v in selected.items() if k[0] != name}
    return states, selected, fingerprints


def shopee_recovery_blocks(recovery, store, targets):
    """Original auth.py journal, read-only. Never reconcile or replay a rotation."""
    require(type(recovery) is dict and recovery.get('schema_version') == 'shopee-refresh-state/v1', 'Shopee recovery schema unsupported')
    require(type(recovery.get('shops')) is dict and type(recovery.get('merchants',{})) is dict, 'Shopee recovery entities invalid')
    entries = [entry for collection in ('shops','merchants') for entry in recovery.get(collection,{}).values()]
    require(all(type(entry) is dict and entry.get('status') in {'IN_FLIGHT','UNKNOWN','COMMITTED','COMMITTED_RECOVERED','FAILED'} for entry in entries), 'Shopee recovery status unsupported')
    uncertain = {entry.get('old_refresh_digest') for entry in entries if entry['status'] in {'IN_FLIGHT','UNKNOWN'}}
    require(all(type(value) is str and value.startswith('sha256:') for value in uncertain), 'Shopee uncertain alias identity missing')
    blocked = []
    for region,target in targets.items():
        sid=str(target['shopee_shop_id'])
        prior=recovery['shops'].get(sid,{})
        refresh=(store.get('shops',{}).get(sid) or {}).get('refresh_token')
        digest='sha256:'+hashlib.sha256(refresh.encode()).hexdigest() if type(refresh) is str and refresh else None
        if prior.get('status') in {'IN_FLIGHT','UNKNOWN'} or (digest is not None and digest in uncertain):
            blocked.append(region)
    return blocked


def read_shopee_identities(profile, sessions, guard, *, reader=None):
    if reader is None:
        from modules.shopee.client import shop_get
        reader=shop_get
    for region,target in profile['targets'].items():
        result=reader('/api/v2/shop/get_shop_info',target['shopee_shop_id'],sessions['shopee',region],{},call_guard=guard)
        # The existing Shopee client returns the provider JSON unchanged;
        # get_shop_info also exposes identity fields at the top level.
        data=result.get('response') or result
        require(type(data) is dict, f'{region}: Shopee official identity shape invalid')
        # get_shop_info is scoped by the signed exact shop_id request. Some
        # official shapes omit its redundant ID; never invent a returned ID.
        require(not result.get('error') and ('shop_id' not in data or data['shop_id']==target['shopee_shop_id']) and str(data.get('region','')).upper()==region, f'{region}: Shopee official ID/region mismatch or incomplete')


def wire_guard(fingerprints, settings_path):
    p = Path(settings_path)
    expected = {**fingerprints, p: hashlib.sha256(p.read_bytes()).hexdigest()}
    def guard():
        for source, digest in expected.items():
            try:
                require(hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'config/session changed during read; abort without refresh')
            except OSError:
                raise NotReady('config/session became unreadable; abort without refresh') from None
    return guard


def read_identity(profile, token, guard, *, request=None):
    if request is None:
        from core.api_client import request
    result = request('GET', IDENTITY_PATH, token, _retry_on_401=False, retry_read=False, call_guard=guard)
    require(result.get('code') == 0, 'TikTok identity rejected; no credential recovery attempted')
    data = result.get('data') or {}
    rows = data.get('shops', data.get('list'))
    require(type(rows) is list, 'TikTok identity response incomplete')
    selected = {}
    for region in REGIONS:
        matches = [s for s in rows if str(s.get('region', '')).upper() == region]
        require(len(matches) == 1, f'{region}: shop scope missing or ambiguous; pin exact identity before orders')
        shop = matches[0]
        sid, cipher = str(shop.get('id') or ''), shop.get('cipher') or shop.get('shop_cipher')
        require(sid and type(cipher) is str and cipher and not any(x in sid or x in cipher for x in ('...', '…', '*')), f'{region}: complete TikTok identity missing')
        frozen = profile['targets'][region]
        require(frozen['tiktok_shop_id'] in (None, sid) and frozen['tiktok_cipher'] in (None, cipher), f'{region}: TikTok identity differs from profile')
        selected[region] = {'tiktok_shop_id': sid, 'tiktok_cipher': cipher}
    require(all(len({t[field] for t in selected.values()}) == 4 for field in ('tiktok_shop_id','tiktok_cipher')), 'TikTok provider identities reused across countries')
    return selected


def requesters(guard):
    from core.api_client import request
    from modules.shopee.client import shop_get
    allowed_tiktok = '/order/202309/orders/search'
    allowed_shopee = {'/api/v2/order/get_order_list', '/api/v2/order/get_order_detail'}
    def tiktok(path, token, query, body):
        require(path == allowed_tiktok, 'unreviewed TikTok endpoint')
        return request('POST', path, token, query=query, body=body, _retry_on_401=False, retry_read=False, call_guard=guard)
    def shopee(path, shop_id, token, params):
        require(path in allowed_shopee, 'unreviewed Shopee endpoint')
        return shop_get(path, shop_id, token, params, call_guard=guard)
    return {'tiktok': tiktok, 'shopee': shopee}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--preflight', action='store_true')
    action.add_argument('--inspect-sessions', action='store_true')
    action.add_argument('--read-identity', action='store_true')
    action.add_argument('--read-orders', action='store_true')
    action.add_argument('--read-tiktok-orders', action='store_true', help='Four TikTok stores only; explicitly incomplete, never admitted as an eight-store source')
    action.add_argument('--read-shopee-orders', action='store_true', help='Continue only missing Shopee from a retained TikTok capture; never reread TikTok')
    parser.add_argument('--continue-from-tiktok', type=Path)
    args = parser.parse_args(argv)
    phase = 'local-profile'
    try:
        profile_path = absolute(str(args.profile), 'profile', file=True)
        profile_digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        profile = load_profile(profile_path)
        continuation=None
        if args.read_shopee_orders:
            require(args.continue_from_tiktok is not None, 'retained TikTok source required; no repeat collection')
            continued_path=absolute(str(args.continue_from_tiktok),'retained TikTok source',file=True)
            continued_bytes=continued_path.read_bytes();continued_digest=hashlib.sha256(continued_bytes).hexdigest()
            continuation=json.loads(continued_bytes)
            expected_targets={r:{k:t[k] for k in ('tiktok_shop_id','tiktok_cipher','shopee_shop_id')} for r,t in profile['targets'].items()}
            require(continuation.get('schema')=='supply-partial-order-read/v1' and continuation.get('completeness')=='TIKTOK_ONLY_NOT_COMPLETE' and continuation.get('days')==31 and continuation.get('targets')==expected_targets, 'retained TikTok source scope/window differs')
            require(set(continuation.get('regions',{}))==set(REGIONS), 'retained TikTok source countries missing')
        else:
            require(args.continue_from_tiktok is None, 'continuation requires Shopee-only action')
        metadata = {k: profile[k] for k in ('source_root', 'settings_path', 'token_paths', 'output_root', 'targets')}
        if args.preflight:
            print(json.dumps({'state':'PREFLIGHT_EXPLICIT_ONLY', **metadata, 'networkReads':0, 'authWrites':0, 'businessWrites':0}, ensure_ascii=False)); return 0
        phase = 'existing-session-read'
        states, sessions, fingerprints = inspect_sessions(profile, platform='shopee' if args.read_shopee_orders else 'tiktok' if args.read_identity or args.read_tiktok_orders else 'all')
        if args.inspect_sessions:
            print(json.dumps({'state':'SESSION_INSPECTION_ONLY', 'sessions':states, 'networkReads':0, 'authWrites':0, 'businessWrites':0}, ensure_ascii=False)); return 0
        if args.read_shopee_orders:
            require(len(sessions)==4, 'Shopee selected sessions not ready; owner reconciliation required; no network or refresh attempted')
        elif args.read_identity or args.read_tiktok_orders:
            require(('tiktok', 'selected') in sessions, 'TikTok existing session unavailable; inspect exact recovery blocker')
        else:
            require(len(sessions) == 5, 'selected sessions not ready; inspect-sessions; no network or refresh attempted')
            require(all(t['tiktok_shop_id'] and t['tiktok_cipher'] for t in profile['targets'].values()), 'TikTok shop identities not pinned; read-identity first')
        if args.read_tiktok_orders:
            require(all(t['tiktok_shop_id'] and t['tiktok_cipher'] for t in profile['targets'].values()), 'TikTok shop identities not pinned; read-identity first')
        bind_runtime(profile)
        fingerprints[profile_path] = profile_digest
        fingerprints[ENTRY] = ENTRY_SHA256
        if continuation is not None:fingerprints[continued_path]=continued_digest
        guard = wire_guard(fingerprints, profile['settings_path'])
        phase = 'shopee-identity-read' if args.read_shopee_orders else 'tiktok-identity-read'
        if not args.read_shopee_orders:identity = read_identity(profile, sessions['tiktok','selected'], guard)
        if args.read_identity:
            print(json.dumps({'state':'IDENTITY_READ_ONLY', 'targets':identity, 'observedAt':datetime.now(timezone.utc).isoformat(), 'networkReads':1, 'authWrites':0, 'businessWrites':0}, ensure_ascii=False)); return 0
        spec = importlib.util.spec_from_file_location('supply_order_capture', Path(__file__).with_name('pull_order_demand.py'))
        capture = importlib.util.module_from_spec(spec); spec.loader.exec_module(capture)
        adapter_path = Path(__file__).with_name('pull_order_demand.py')
        adapter_digest = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
        # Extend the guard before the first order page; no source discovery.
        fingerprints[adapter_path] = adapter_digest
        guard = wire_guard(fingerprints, profile['settings_path'])
        started=datetime.now(timezone.utc).isoformat()
        cutoff = continuation['capturedAt'] if continuation is not None else started
        require(datetime.fromisoformat(cutoff).tzinfo is not None and datetime.fromisoformat(cutoff)<=datetime.fromisoformat(started), 'source cutoff invalid')
        targets = {r:{k:t[k] for k in ('tiktok_shop_id','tiktok_cipher','shopee_shop_id')} for r,t in profile['targets'].items()}
        from core.db import db_path
        from shared_platform.publication_canonical_aliases import load_alias_registry
        alias_registry=load_alias_registry(db_path())
        if continuation is not None:
            end=int(datetime.fromisoformat(cutoff).timestamp());start=end-31*86400
            phase='retained-tiktok-source-validation'
            for region,target in targets.items():capture.captured_order_rows(continuation['regions'][region]['tiktok'],'tiktok',target['tiktok_cipher'],start,end)
            phase='shopee-identity-read'
            read_shopee_identities(profile,sessions,guard)
        phase = 'order-pages-read'
        if args.read_tiktok_orders or args.read_shopee_orders:
            platform='shopee' if args.read_shopee_orders else 'tiktok'
            source = {'schema':'supply-partial-order-read/v1', 'completeness':'SHOPEE_ONLY_NOT_COMPLETE' if args.read_shopee_orders else 'TIKTOK_ONLY_NOT_COMPLETE',
                'missingPlatforms':['TikTok'] if args.read_shopee_orders else ['Shopee'], 'capturedAt':cutoff, 'collectionStartedAt':started, 'days':31, 'targets':targets, 'regions':{}}
            if continuation is not None:source['retainedTikTokSource']={'path':str(continued_path),'sha256':continued_digest,'capturedAt':cutoff}
            end = int(datetime.fromisoformat(cutoff).timestamp()); start = end - 31 * 86400
            requester = requesters(guard)[platform]
            from domains.supply_chain_operations.order_demand import aggregate_tiktok_orders, aggregate_shopee_orders, finalize_order_snapshot
            for region, target in targets.items():
                exact_target=str(target['shopee_shop_id']) if args.read_shopee_orders else target['tiktok_cipher']
                envelope = {'target':exact_target, 'blocks':[]}
                if args.read_shopee_orders:
                    envelope['details']=[]
                reader = capture._receipt_requester(platform, exact_target, requester, envelope, target['shopee_shop_id'] if args.read_shopee_orders else target['tiktok_shop_id'])
                if args.read_shopee_orders:capture.pull_shopee_region(target['shopee_shop_id'],sessions['shopee',region],start,end,requester=reader)
                else:capture.pull_tiktok_region(sessions['tiktok','selected'], target['tiktok_cipher'], start, end, requester=reader)
                rows = capture.captured_order_rows(envelope, platform, exact_target, start, end)
                facts, evidence = (aggregate_shopee_orders if args.read_shopee_orders else aggregate_tiktok_orders)(rows, region,alias_registry=alias_registry,shop_key=exact_target)
                unresolved = any(evidence.get(k,0) for k in ('orders_invalid','item_lines_unresolved','item_lines_invalid_quantity'))
                source['regions'][region] = {platform:envelope, 'mappingState':'BLOCKED_UNRESOLVED_ORDER_FACTS' if unresolved else 'MAPPED_SHOPEE_ONLY' if args.read_shopee_orders else 'MAPPED_TIKTOK_ONLY', 'snapshot':finalize_order_snapshot(facts, region=region,
                    platform='Shopee' if args.read_shopee_orders else 'TikTok', captured_at=datetime.fromisoformat(cutoff), days=31, evidence=evidence)}
        else:
            selected = {r:{'tiktok':sessions['tiktok','selected'], 'shopee':sessions['shopee',r]} for r in REGIONS}
            source = capture.capture_orders(targets=targets, cutoff=cutoff, days=31, source_id='explicit-orders-'+cutoff,
                started_at=cutoff, materialized_at=cutoff, sessions=selected, requesters=requesters(guard),alias_registry=alias_registry)
        source['canonicalAliasRegistryDigest']=alias_registry.digest
        source['materializedAt'] = datetime.now(timezone.utc).isoformat()
        source['collector'] = {'sourceRoot':str(ROOT), 'entrySha256':ENTRY_SHA256, 'captureAdapterSha256':adapter_digest,
            'profileSha256':profile_digest, 'automaticAuthRecovery':False}
        guard()
        root = Path(profile['output_root']); root.mkdir(parents=True, exist_ok=True)
        dest = root / (('shopee-orders-' if args.read_shopee_orders else 'orders-') + started.replace(':','').replace('+','_') + '.json')
        with dest.open('x', encoding='utf-8') as stream:
            json.dump(source, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'state':'PARTIAL_SHOPEE_ONLY_NOT_APPLIED' if args.read_shopee_orders else 'PARTIAL_TIKTOK_ONLY_NOT_APPLIED' if args.read_tiktok_orders else 'CAPTURED_NOT_APPLIED',
            'path':str(dest), 'capturedAt':cutoff, 'materializedAt':source['materializedAt'], 'authWrites':0, 'businessWrites':0}, ensure_ascii=False)); return 0
    except (NotReady, OSError, ValueError, RuntimeError) as error:
        # Do not print provider text, URLs with query credentials, or token values.
        print(json.dumps({'state':'NOT_READY', 'action':'read-shopee-orders' if args.read_shopee_orders else 'read-identity' if args.read_identity else 'read-tiktok-orders' if args.read_tiktok_orders else 'read-orders' if args.read_orders else 'local-preflight',
            'phase':phase, 'detail':str(error) if isinstance(error, NotReady) else type(error).__name__,
            'authWrites':0, 'businessWrites':0}, ensure_ascii=False)); return 2


if __name__ == '__main__':
    raise SystemExit(main())
