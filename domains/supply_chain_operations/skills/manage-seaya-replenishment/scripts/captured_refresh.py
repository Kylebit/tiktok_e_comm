"""Local captured-source adapter; no transport or credential discovery.

Reuses inventory/order consumers. A durable journal recovers paired output writes;
the pair is not a filesystem transaction and must not be served while applying.
"""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from shared_platform.capability_runtime import checked_path, digest
from modules.sourcing.image_generation_checkpoint import business_lock, atomic_json, atomic_bytes
from domains.supply_chain_operations.captured_serving import publish_complete

REGIONS = ('MY', 'TH', 'VN', 'PH')
WAREHOUSES = dict(zip(REGIONS, ('MY8803', 'TH8806', 'VN8805', 'PH8807')))
FILES = ('data.js', 'inbound-plan.js')


def require(condition, message):
    if not condition: raise ValueError(message)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def pairs(rows):
    result = {}
    for key, value in rows:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def read(path):
    raw = path.read_bytes()
    require(len(raw) <= 32*1024*1024, 'captured input exceeds 32 MiB')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def clock(value):
    require(type(value) is str, 'source clock missing')
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None, 'source clock requires timezone')
    return stamp


def full_pages(value, target):
    require(value.get('target') == target, 'captured target mismatch')
    pages = value.get('pages'); total = value.get('totalRows')
    require(type(total) is int and total >= 0 and type(pages) is list and pages, 'complete page evidence required')
    rows = []; cursor = ''; seen = set()
    for index, page in enumerate(pages):
        require(type(page) is dict and page.get('cursor') == cursor and cursor not in seen, 'missing or duplicate page cursor')
        seen.add(cursor)
        require(type(page.get('rows')) is list, 'page rows required')
        rows.extend(page['rows']); cursor = page.get('nextCursor')
        require(type(cursor) is str, 'next cursor missing')
        require(bool(cursor) == (index < len(pages)-1), 'page chain incomplete')
    require(len(rows) == total, 'page count does not reconcile')
    return rows


def validate_order_records(rows, platform):
    # Reuse existing exclusions; this boundary supplies missing evidence checks,
    # without inventing a new lifecycle mapping or changing demand arithmetic.
    from domains.supply_chain_operations import order_demand
    status_key, detail_key = ('status', 'line_items') if platform == 'tiktok' else ('order_status', 'item_list')
    excluded = order_demand._TIKTOK_EXCLUDED if platform == 'tiktok' else order_demand._SHOPEE_EXCLUDED
    for row in rows:
        require(type(row) is dict, 'captured order record must be an object')
        status = row.get(status_key)
        require(type(status) is str and bool(status.strip()), 'captured order lifecycle missing')
        if status.upper() in excluded or (platform == 'tiktok' and any(row.get(k) is True for k in ('is_on_hold_order', 'is_sample_order', 'is_replacement_order'))):
            continue
        items = row.get(detail_key)
        require(type(items) is list and bool(items) and all(type(item) is dict for item in items), 'captured order detail missing or malformed')


def load_inputs(profile, artifact):
    config = profile.get('captured')
    require(profile['schema'] == 'supply-chain-refresh-profile/v2' and type(config) is dict, 'captured v2 profile required')
    require(set(config)-{'canonical_aliases'} == {'sources', 'targets', 'output_root'}, 'captured profile fields differ')
    require(set(config['sources']) == {'inventory', 'orders', 'inbound', 'seed'}, 'four explicit source files required')
    require(set(config['targets']) == set(REGIONS), 'exact four-country target set required')
    output = checked_path(artifact, config['output_root'])
    require(output != artifact and output.is_relative_to(artifact), 'output must be a child of artifact root')
    require(output.relative_to(artifact).parts[0] not in {'captured-stages', 'captured-apply.json'}, 'reserved refresh output path')
    sources = {}
    for kind, ref in config['sources'].items():
        require(set(ref) == {'path', 'sha256'}, 'source path and SHA required')
        path = checked_path(artifact, ref['path'])
        require(path.is_relative_to(artifact) and not path.is_relative_to(output), 'source must be separate from apply target')
        require(path.relative_to(artifact).parts[0] not in {'captured-stages', 'captured-apply.json'}, 'source overlaps refresh state')
        require(sha(path.read_bytes()) == ref['sha256'], 'captured source SHA mismatch')
        sources[kind] = read(path)
    for kind in ('inventory', 'orders', 'inbound'):
        source = sources[kind]
        schemas={'supply-chain-captured-source/v1','supply-chain-captured-source/v2','supply-chain-captured-source/v3'}
        require(source.get('schema') in schemas and source.get('kind') == kind, 'captured source kind mismatch')
        require(type(source.get('sourceId')) is str and bool(source['sourceId'].strip()), 'source identity required')
        clock(source.get('capturedAt'))
        require(set(source.get('regions', {})) == set(REGIONS), 'four source countries required')
    for region, targets in config['targets'].items():
        require(set(targets) == {'warehouse', 'tiktok', 'shopee'} and targets['warehouse'] == WAREHOUSES[region], 'warehouse target mismatch')
        require(all(type(v) is str and v.strip() and not any(m in v for m in ('...', '…', '*')) for v in targets.values()), 'complete target identity required')
    for platform in ('tiktok', 'shopee'):
        require(len({v[platform] for v in config['targets'].values()}) == len(REGIONS), 'channel target reused across countries')
    orders=sources['orders']
    if orders['schema'] in {'supply-chain-captured-source/v2','supply-chain-captured-source/v3'}:
        require(clock(orders['capturedAt'])<=clock(orders.get('collectionStartedAt'))<=clock(orders.get('materializedAt')), 'capture materialization clock invalid')
        targets=orders.get('targets');require(type(targets) is dict and set(targets)==set(REGIONS),'order target bindings missing')
        for r,t in targets.items():
            require(type(t) is dict and set(t)=={'tiktok_shop_id','tiktok_cipher','shopee_shop_id'},'order target binding invalid')
            require(type(t['tiktok_shop_id']) is str and t['tiktok_shop_id'].strip() and not any(x in t['tiktok_shop_id'] for x in ('...','…','*')),'TikTok shop identity missing')
            require(type(t['shopee_shop_id']) is int and t['shopee_shop_id']>0 and str(t['shopee_shop_id'])==config['targets'][r]['shopee'] and t['tiktok_cipher']==config['targets'][r]['tiktok'],'order target differs from profile')
        require(len({t['tiktok_shop_id'] for t in targets.values()})==4,'TikTok shop reused across countries')
    if orders['schema']=='supply-chain-captured-source/v3':
        require(orders.get('demandWindowPolicy')=='completed_local_calendar_30_v1' and type(orders.get('mappingExceptions')) is list, 'explicit complete-calendar policy and exact exceptions required')
    if 'platformCollectionClocks' in orders:
        clocks = orders['platformCollectionClocks']
        require(type(clocks) is dict and set(clocks)=={'tiktok','shopee'}, 'both independent platform clocks required')
        for value in clocks.values():
            require(type(value) is dict and set(value)=={'capturedAt','collectionStartedAt','materializedAt'}, 'independent platform clock fields differ')
            require(clock(value['capturedAt'])==clock(orders['capturedAt']) and
                    clock(orders['collectionStartedAt'])<=clock(value['collectionStartedAt'])<=clock(value['materializedAt'])<=clock(orders['materializedAt']),
                    'independent platform collection clock invalid')
        require(min(clock(v['collectionStartedAt']) for v in clocks.values())==clock(orders['collectionStartedAt']) and
                max(clock(v['materializedAt']) for v in clocks.values())==clock(orders['materializedAt']), 'composite clocks differ from platform observations')
    return config, sources, output


def unresolved_lines(raw, region, platform, aggregate, *, alias_registry=None, shop_key=None):
    """Reuse the existing lifecycle/SKU aggregator to identify each exception."""
    detail_key = 'line_items' if platform == 'tiktok' else 'item_list'
    id_key = 'id' if platform == 'tiktok' else 'order_sn'
    result = []
    for row in raw:
        for index, item in enumerate(row.get(detail_key) or []):
            _, evidence = aggregate([{**row, detail_key: [item]}], region,alias_registry=alias_registry,shop_key=shop_key)
            if evidence.get('item_lines_unresolved'):
                result.append({'country':region, 'platform':platform, 'orderId':row[id_key], 'lineIndex':index,
                               'itemSha256':digest(item), 'reason':'UNRESOLVED_SELLER_SKU'})
    return result


def prepare(profile, artifact, load_module):
    config, sources, output = load_inputs(profile, artifact)
    alias_registry=None
    if 'canonical_aliases' in config:
        ref=config['canonical_aliases']
        require(type(ref) is dict and set(ref)=={'database_path','registry_digest'},'explicit canonical registry source required')
        path=Path(ref['database_path'])
        require(path.is_absolute(),'canonical registry database path must be absolute')
        from shared_platform.publication_canonical_aliases import load_alias_registry
        alias_registry=load_alias_registry(path)
        require(alias_registry.digest==ref['registry_digest'],'canonical registry changed')
        source_pin=sources['orders'].get('canonicalAliasRegistryDigest')
        require(source_pin is None or source_pin==alias_registry.digest,'order canonical registry binding differs')
    require(sources['inventory']['schema'] == sources['inbound']['schema'], 'inventory and inbound evidence versions must match')
    seaya = None
    for kind in ('inventory', 'inbound'):
        source = sources[kind]
        if source['schema'] in {'supply-chain-captured-source/v2','supply-chain-captured-source/v3'}:
            seaya = load_module('seaya_capture')
            normalizer = seaya.capture_global_browser if source['schema']=='supply-chain-captured-source/v3' else seaya.capture
            expected = normalizer(source.get('evidence'), sources['inventory'] if kind == 'inbound' else None)
            require(source == expected, 'Seaya normalized source differs from bound complete evidence')
    seed = sources['seed']; require(set(seed['countries']) == set(REGIONS), 'four seed countries required')
    inventory = {'capturedAt': sources['inventory']['capturedAt'], 'source': sources['inventory']['sourceId'], 'records': []}
    inbound = {'capturedAt': sources['inbound']['capturedAt'], 'source': sources['inbound']['sourceId'], 'regions': {}}
    orders = sources['orders']; captured = clock(orders['capturedAt']); days = orders.get('days')
    require(type(days) is int and 30 <= days <= 366, 'order window must contain at least 30 days')
    if orders['schema']=='supply-chain-captured-source/v3': require(days >= 31, 'complete local dates require a buffered capture window')
    snapshot = {'schemaVersion': 'order_demand_snapshot_v1', 'capturedAt': orders['capturedAt'], 'days': days, 'countries': {}}
    validator = load_module('validate_inventory_snapshot'); ai = load_module('apply_inventory_snapshot'); ao = load_module('apply_order_demand'); pull = load_module('pull_order_demand')
    verified_exceptions = []
    demand_windows = {}
    for region in REGIONS:
        targets = config['targets'][region]
        require(seed['config'][region]['warehouse'] == targets['warehouse'], 'seed country warehouse mismatch')
        rows = seed['countries'][region]; known = {r['sku'] for r in rows}
        require(len(known) == len(rows) and all(re.fullmatch(r'[0-9]{4}', s) for s in known), 'seed SKU identity invalid')
        for row in rows:
            image = row.get('image')
            require(type(image) is str and image.startswith('assets/'), 'seed requires existing local image')
            require(checked_path(output, image).is_file(), 'local seed image missing; preserve existing assets before stage')
        records = full_pages(sources['inventory']['regions'][region], targets['warehouse'])
        require(not validator.validate_payload({'records': records}), 'invalid inventory identity or quantity')
        require(all(r['warehouse'] == targets['warehouse'] for r in records), 'inventory row country mismatch')
        for record in records: clock(record['captured_at'])
        grouped = ai.aggregate_snapshot({'records': records}).get(region, {})
        require(not (set(grouped)-known), 'unknown inventory SKU requires presentation intake')
        inventory['records'].extend(records)
        batches = full_pages(sources['inbound']['regions'][region], targets['warehouse']); seen = set(); totals = {}
        for batch in batches:
            identity = batch.get('batchId')
            require(type(identity) is str and identity.strip() and identity not in seen and not any(x in identity for x in ('...', '…', '*')), 'invalid or duplicate batch identity')
            seen.add(identity); amounts = batch.get('skuQuantities')
            created = clock(batch.get('createdAt'))
            anchor = clock(batch.get('anchorAt') or batch.get('estimatedAnchorAt'))
            require(anchor >= created, 'inbound anchor precedes creation')
            if batch.get('estimatedSellableConfirmedAt'): clock(batch['estimatedSellableConfirmedAt'])
            if batch.get('estimatedSellableDate'):
                date = datetime.strptime(batch['estimatedSellableDate'], '%Y-%m-%d').date()
                require(date >= anchor.date(), 'sellable date precedes anchor')
            require(type(amounts) is dict and bool(amounts) and not (set(amounts)-known), 'unknown inbound SKU')
            require(all(type(n) is int and n >= 0 for n in amounts.values()), 'invalid inbound quantity')
            require(type(batch.get('totalUnits')) is int and sum(amounts.values()) == batch['totalUnits'], 'inbound batch total mismatch')
            for sku, n in amounts.items(): totals[sku] = totals.get(sku, 0)+n
        require({k:v for k,v in totals.items() if v} == {k:v['inbound'] for k,v in grouped.items() if v['inbound']}, 'inbound and inventory quantities differ')
        inbound['regions'][region] = {'batches': batches, 'totalUnits': sum(batch['totalUnits'] for batch in batches)}
        require(set(orders['regions'][region]) == {'tiktok', 'shopee'}, 'both order channels required')
        snapshot['countries'][region] = {}
        for platform, aggregate in [('tiktok', pull.aggregate_tiktok_orders), ('shopee', pull.aggregate_shopee_orders)]:
            raw = pull.captured_order_rows(orders['regions'][region][platform],platform,targets[platform],int(captured.timestamp())-days*86400,int(captured.timestamp())) if orders['schema'] in {'supply-chain-captured-source/v2','supply-chain-captured-source/v3'} else full_pages(orders['regions'][region][platform], targets[platform])
            validate_order_records(raw, platform)
            identities = [r.get('id' if platform == 'tiktok' else 'order_sn') for r in raw]
            require(all(type(i) is str and i for i in identities) and len(set(identities)) == len(identities), 'duplicate or missing order identity')
            require(all(type(r.get('create_time')) is int and int(captured.timestamp())-days*86400 <= r['create_time'] < int(captured.timestamp()) for r in raw), 'order outside captured window')
            if platform == 'tiktok':
                require(all(not r.get('paid_time') or (type(r['paid_time']) is int and int(captured.timestamp())-days*86400 <= r['paid_time'] < int(captured.timestamp())) for r in raw), 'order payment event outside captured window')
            facts, evidence = aggregate(raw, region,alias_registry=alias_registry,shop_key=targets[platform])
            require(not any(evidence.get(k, 0) for k in ('orders_invalid', 'item_lines_invalid_quantity')), 'invalid order facts')
            if orders['schema']=='supply-chain-captured-source/v3':
                exact = unresolved_lines(raw, region, platform, aggregate,alias_registry=alias_registry,shop_key=targets[platform])
                require(len(exact)==evidence.get('item_lines_unresolved',0), 'unresolved line count differs')
                verified_exceptions.extend(exact)
            else:
                require(not evidence.get('item_lines_unresolved',0), 'unresolved order facts')
            require(not (set(facts)-known), 'unknown order SKU requires presentation intake')
            policy = orders['demandWindowPolicy'] if orders['schema']=='supply-chain-captured-source/v3' else 'capture_calendar_days_v1'
            finalized = pull.finalize_order_snapshot(facts, region=region, platform='TikTok' if platform=='tiktok' else 'Shopee', captured_at=captured, days=days, evidence=evidence, window_policy=policy)
            snapshot['countries'][region][platform] = finalized
            if 'demandWindow' in finalized: demand_windows[region] = finalized['demandWindow']
    if orders['schema']=='supply-chain-captured-source/v3':
        key = lambda r: (r['country'], r['platform'], r['orderId'], r['lineIndex'], r['itemSha256'])
        require(all(type(r) is dict and set(r)=={'country','platform','orderId','lineIndex','itemSha256','reason'} for r in orders['mappingExceptions']), 'exception fields differ')
        require(sorted(verified_exceptions,key=key)==sorted(orders['mappingExceptions'],key=key), 'exact unresolved exceptions differ; raw facts must remain intact')
    before = json.loads(json.dumps(seed))
    data = ao.apply_snapshot(ai.apply_inventory(seed, inventory), snapshot)
    conditions = []
    if orders['schema']=='supply-chain-captured-source/v3':
        data['demandWindowPolicy'] = orders['demandWindowPolicy']
        data['demandWindows'] = demand_windows
        data['orderMappingExceptions'] = [{**{k:v for k,v in e.items() if k!='orderId'}, 'orderRefSha256':digest(e['orderId'])} for e in verified_exceptions]
        for region in REGIONS:
            count = sum(r['country']==region for r in verified_exceptions)
            data['config'][region]['orderMappingComplete'] = count == 0
            data['config'][region]['demandCoverage'] = '30 completed local dates from retained 31-day transport window' + ('; mapped facts conditional' if count else '')
            if count:
                conditions.append({'country':region, 'code':'UNRESOLVED_SELLER_SKU', 'message':f'{region}：{count}条空SKU异常单独保留，已映射数量继续作条件测算；未知行不计为零销量。'})
                for row in data['countries'][region]:
                    for platform in ('tiktok','shopee'):
                        if any(e['country']==region and e['platform']==platform for e in verified_exceptions):
                            channel = row['channels'][platform]
                            channel['mappingComplete'] = False
                            if row['sku'] not in snapshot['countries'][region][platform]['facts']:
                                channel['evidence'] = 'no_mapped_fact_unresolved_scope_not_proven_zero'
                                channel['zeroIsObserved'] = False
    if sources['inventory']['schema']=='supply-chain-captured-source/v3':
        conditions.append({'country':'ALL', 'code':'NON_ATOMIC_BROWSER_OBSERVATIONS', 'message':'雅仓为全仓分页的独立观察时间，不是原子实时快照；库存、订单、入库时钟分别保留。'})
    if orders['schema']=='supply-chain-captured-source/v3' or sources['inventory']['schema']=='supply-chain-captured-source/v3':
        data['sourceReadiness'] = {'executionAuthority':False, 'businessAcceptance':False, 'conditional':bool(conditions), 'conditions':conditions}
    data['capturedRefreshSources'] = {k: {'sourceId':sources[k]['sourceId'], 'capturedAt':sources[k]['capturedAt'], 'sha256':config['sources'][k]['sha256']} for k in ('inventory', 'orders', 'inbound')}
    if alias_registry is not None:
        data['canonicalAliasRegistryDigest']=alias_registry.digest
    for kind in ('inventory', 'orders', 'inbound'):
        if sources[kind]['schema'] in {'supply-chain-captured-source/v2','supply-chain-captured-source/v3'}:
            data['capturedRefreshSources'][kind].update({k:sources[kind][k] for k in ('collectionStartedAt','materializedAt')})
            if kind != 'orders':
                data['capturedRefreshSources'][kind].update({k:sources[kind][k] for k in ('tenantId','evidenceDigest')})
            if sources[kind]['schema']=='supply-chain-captured-source/v3' and kind != 'orders':
                data['capturedRefreshSources'][kind].update({k:sources[kind][k] for k in ('atomicSnapshot','clockBasis','normalizationBasis')})
                e = sources[kind]['evidence']
                data['capturedRefreshSources'][kind].update(globalTotalRows=e['totalRows'], globalPageRowCounts=[p['rowCount'] for p in e['pagination']], rowToPageBinding=e['rowToPageBinding'], tenantIdentityBasis='visible_username_not_numeric_tenant_id', tenantIdentityObservedAt=e['tenantWitness']['observedAt'])
        if kind=='orders' and 'platformCollectionClocks' in sources[kind]:
            data['capturedRefreshSources'][kind]['platformCollectionClocks'] = json.loads(json.dumps(sources[kind]['platformCollectionClocks']))
    if sources['inbound']['schema'] in {'supply-chain-captured-source/v2','supply-chain-captured-source/v3'}:
        inbound['captureEvidence'] = data['capturedRefreshSources']['inbound']
    diff = []
    for region in REGIONS:
        old_rows = {row['sku']:row for row in before['countries'][region]}
        for row in data['countries'][region]:
            old = old_rows[row['sku']]
            if old != row: diff.append({'country':region,'sku':row['sku'],'before_inventory':old.get('inventory'),'after_inventory':row['inventory'],'before_channels':old.get('channels'),'after_channels':row['channels']})
    import sys
    consumers = [validator, ai, ao, pull] + [sys.modules[name] for name in ('domains.supply_chain_operations.order_demand','domains.supply_chain_operations.demand_trend') if name in sys.modules]
    if seaya is not None: consumers.append(seaya)
    bindings = {str(Path(m.__file__)):sha(Path(m.__file__).read_bytes()) for m in consumers}
    return {'data': data, 'inbound': inbound, 'diff': diff, 'consumer_sources':bindings, 'clocks': {k:sources[k]['capturedAt'] for k in ('inventory','orders','inbound')}, 'mode':'CAPTURED_LOCAL', 'freshness':'needs_review' if len({clock(sources[k]['capturedAt']).date() for k in ('inventory','orders','inbound')})>1 else 'source_dates_match_not_live_verified'}, output


def encoded(value, prefix): return (prefix+json.dumps(value,ensure_ascii=False,separators=(',', ':'))+';\n').encode('utf-8')


def execute(profile, artifact, load_module, stage_digest=None):
    # Same lease as the legacy path prevents competing writers across modes.
    lease_root=Path(profile.get('runtime_root',profile['project_root'])).resolve()
    scope = digest({'root': str(lease_root).casefold(), 'artifact': str(artifact).casefold()})
    with business_lock(artifact, scope, timeout=0):
        result, output = prepare(profile, artifact, load_module)
        identity = digest({'profile':profile, 'result':result}); directory = checked_path(artifact, 'captured-stages/'+identity)
        bodies = {'data.js':encoded(result['data'], 'window.SUPPLY_CHAIN_DATA = '), 'inbound-plan.js':encoded(result['inbound'], 'window.SUPPLY_CHAIN_INBOUND_PLAN = ')}
        hashes = {name:sha(body) for name,body in bodies.items()}
        if stage_digest is None:
            # A new refresh must start from the last applied presentation snapshot;
            # never silently discard later local fields using an old seed export.
            current = checked_path(output, 'data.js')
            if current.exists() and not checked_path(directory,'stage.json').exists():
                text = current.read_text(encoding='utf-8')
                require(text.startswith('window.SUPPLY_CHAIN_DATA = '), 'current output is not an exact dashboard snapshot')
                seed_path = checked_path(artifact,profile['captured']['sources']['seed']['path'])
                require(json.loads(text.removeprefix('window.SUPPLY_CHAIN_DATA = ').strip().removesuffix(';')) == read(seed_path), 'seed is stale; export current output before staging a new refresh')
            directory.mkdir(parents=True, exist_ok=True)
            record = {'stage_digest':identity,'profile_digest':digest(profile),'output_root':str(output),'output_sha256':hashes,'base_sha256':{name:sha(checked_path(output,name).read_bytes()) if checked_path(output,name).exists() else None for name in FILES},'clocks':result['clocks'],'freshness':result['freshness']}
            stage = checked_path(directory,'stage.json')
            if stage.exists():
                prior = read(stage); require(prior['output_sha256']==hashes, 'stage output identity changed')
                record = prior
            else:
                for name,body in bodies.items(): atomic_bytes(checked_path(directory,name),body)
                atomic_json(checked_path(directory,'preview.json'),result)
                atomic_json(stage,record)
            return {**record,'ok':True,'state':'STAGED','network_reads':0,'dashboard_source_writes':0}
        require(stage_digest == identity, 'stage digest differs from current frozen inputs')
        record = read(checked_path(directory,'stage.json'))
        require(record['profile_digest']==digest(profile) and record['output_sha256']==hashes and record['output_root']==str(output), 'stage identity mismatch')
        for name in FILES: require(sha(checked_path(directory,name).read_bytes())==hashes[name], 'staged bytes changed')
        output.mkdir(parents=True, exist_ok=True); journal_path = checked_path(artifact,'captured-apply.json')
        previous = read(journal_path) if journal_path.exists() else None
        require(not previous or previous['state']=='COMPLETE' or previous['stage_digest']==identity, 'unfinished apply requires original stage recovery')
        actual = {name:sha(checked_path(output,name).read_bytes()) if checked_path(output,name).exists() else None for name in FILES}
        if previous and previous['stage_digest']==identity and previous['state']=='COMPLETE':
            require(actual==hashes, 'applied output changed; do not overwrite local changes')
            publish_complete(artifact,output,identity,hashes)
            return {'ok':True,'state':'REUSED','stage_digest':identity,'network_reads':0,'dashboard_source_writes':0}
        recovering = bool(previous and previous['stage_digest']==identity and previous['state']=='APPLYING')
        for name in FILES: require(actual[name] in ({record['base_sha256'][name],hashes[name]} if recovering else {record['base_sha256'][name]}), 'apply base changed; preserve local output')
        atomic_json(journal_path,{'state':'APPLYING','stage_digest':identity,'output_root':str(output),'output_sha256':hashes})
        writes = 0
        for name in FILES:
            if actual[name]!=hashes[name]: atomic_bytes(checked_path(output,name),bodies[name]); writes += 1
        atomic_json(journal_path,{'state':'COMPLETE','stage_digest':identity,'output_root':str(output),'output_sha256':hashes})
        publish_complete(artifact,output,identity,hashes)
        return {'ok':True,'state':'APPLIED','stage_digest':identity,'recovered':recovering,'network_reads':0,'dashboard_source_writes':writes,'clocks':result['clocks'],'freshness':result['freshness']}
