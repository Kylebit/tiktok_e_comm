"""Explicit local evidence contract, NOT a Seaya transport or provider schema.

The caller supplies a witnessed full-query transcript. Hashes bind that claim;
they cannot establish its truth. No saved aggregate is upgraded into a receipt.
"""
from __future__ import annotations
import copy
import csv
import hashlib
import io
import importlib.util
from datetime import datetime
from pathlib import Path
from shared_platform.capability_runtime import digest

WAREHOUSES = {'MY': 'MY8803', 'TH': 'TH8806', 'VN': 'VN8805', 'PH': 'PH8807'}


def require(ok, message):
    if not ok:
        raise ValueError('NOT_READY: ' + message)


def clock(value):
    require(type(value) is str, 'independent clock missing')
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None, 'clock requires timezone')
    return stamp


def identity(value):
    return type(value) is str and bool(value.strip()) and not any(x in value for x in ('...', '…', '*'))


def consumer(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def packet(value, scope, started, materialized):
    require(type(value) is dict and value.get('scope') == scope, 'exact warehouse/query scope required')
    coverage = value.get('coverage', {})
    require(type(coverage) is dict, 'coverage object required')
    require(coverage.get('kind') == 'observed_full_query' and identity(coverage.get('reference')), 'full-query evidence required; a total alone is insufficient')
    pages = value.get('pages')
    require(type(pages) is list and bool(pages) and all(type(p) is dict for p in pages), 'complete pages required even for empty scope')
    ids = [p.get('id') for p in pages]
    require(all(identity(i) for i in ids) and len(set(ids)) == len(ids), 'missing or duplicate page identity')
    require(coverage.get('pageIds') == ids and coverage.get('terminalPage') == ids[-1], 'missing page or terminal receipt')
    rows = []
    observations = []
    for index, page in enumerate(pages):
        require(page.get('sha256') == digest({k: v for k, v in page.items() if k != 'sha256'}), 'page digest mismatch')
        require('next' in page and page['next'] == (ids[index+1] if index+1 < len(ids) else None), 'incomplete page chain')
        observed = clock(page.get('observedAt'))
        require(started <= observed <= materialized, 'old detail/page or invalid observation clock')
        require(not observations or observed >= observations[-1], 'page observation order invalid')
        observations.append(observed)
        require(type(page.get('rows')) is list and all(type(r) is dict for r in page['rows']), 'raw rows required')
        rows.extend(page['rows'])
    require(type(coverage.get('totalRows')) is int and coverage['totalRows'] == len(rows), 'row count differs from full-query evidence')
    return rows, observations


def normalized(warehouse, rows):
    # A consumer envelope, not a reconstruction of provider pagination.
    return {'target': warehouse, 'totalRows': len(rows), 'pages': [{'cursor': '', 'nextCursor': '', 'rows': rows}]}


def capture_global_browser(evidence, inventory_source=None):
    """Normalize retained all-warehouse browser rows, never provider cursors.

    Pagination counts and observation clocks are witnessed caller evidence.
    The retained CSV does not bind individual rows to provider pages; partition
    pages below are expressly local consumer envelopes. No atomic claim.
    """
    require(type(evidence) is dict and evidence.get('schema') == 'seaya-global-browser-evidence/v1', 'global browser evidence required')
    kind = evidence.get('kind')
    require(kind in ('inventory', 'inbound') and identity(evidence.get('tenantId')) and identity(evidence.get('sourceId')), 'global evidence identity invalid')
    require(evidence.get('atomicSnapshot') is False and evidence.get('rowToPageBinding') == 'NOT_RETAINED', 'do not invent atomic or per-page row binding')
    require(evidence.get('scope') == {'warehouses': list(WAREHOUSES.values()), 'selection': 'all_inventory' if kind == 'inventory' else 'all_inbound_statuses'}, 'unfiltered global scope required')
    pages = evidence.get('pagination')
    require(type(pages) is list and pages and all(type(p) is dict for p in pages), 'global pagination witness required')
    require([p.get('page') for p in pages] == list(range(1, len(pages)+1)), 'missing global page')
    size, total = evidence.get('pageSize'), evidence.get('totalRows')
    require(type(size) is int and size > 0 and type(total) is int and total >= 0, 'global page sizes invalid')
    require(len(pages) == max(1, (total+size-1)//size) and evidence.get('terminalPageObserved') is True, 'terminal global page not witnessed')
    observations = [clock(p.get('observedAt')) for p in pages]
    require(observations == sorted(observations), 'global observation order invalid')
    require(all(type(p.get('rowCount')) is int and p['rowCount'] == min(size, max(0, total-size*i)) for i,p in enumerate(pages)), 'global count mismatch')
    raw_csv = evidence.get('rawCsv')
    require(type(raw_csv) is str and hashlib.sha256(raw_csv.encode('utf-8')).hexdigest() == evidence.get('rawCsvSha256'), 'raw CSV binding invalid')
    rows = list(csv.DictReader(io.StringIO(raw_csv)))
    require(len(rows) == total, 'retained rows differ from global query total')
    require(clock(evidence.get('collectionStartedAt')) == observations[0] and clock(evidence.get('capturedAt')) == observations[-1], 'observer source clock relabeled')
    require(clock(evidence.get('materializedAt')) >= observations[-1], 'materialization precedes observation')
    witness = evidence.get('tenantWitness')
    require(type(witness) is dict and witness.get('visibleUsername') == evidence['tenantId'] and witness.get('headerMatchesUsernameField') is True, 'tenant witness missing')
    clock(witness.get('observedAt'))
    require(identity(evidence.get('reference')) and identity(witness.get('page')), 'witness reference missing')
    regions = {}
    if kind == 'inventory':
        records = []
        for row in rows:
            require(row.get('warehouse') in WAREHOUSES.values(), 'unexpected warehouse in global scope')
            record = {k: row.get(k) for k in ('warehouse', 'seller_sku')}
            for key in ('stock', 'available', 'allocated', 'frozen', 'inbound'):
                value = row.get(key)
                require(type(value) is str and value.isdigit(), 'invalid retained inventory quantity')
                record[key] = int(value)
            # A local projection clock; its exact meaning is bound below.
            record['captured_at'] = evidence['capturedAt']
            records.append(record)
        require(not consumer('validate_inventory_snapshot').validate_payload({'records': records}), 'invalid global inventory')
        consumer('apply_inventory_snapshot').aggregate_snapshot({'records': records})
        for region, warehouse in WAREHOUSES.items():
            regions[region] = normalized(warehouse, [r for r in records if r['warehouse'] == warehouse])
    else:
        require(type(inventory_source) is dict and inventory_source.get('schema') == 'supply-chain-captured-source/v3' and inventory_source.get('kind') == 'inventory', 'bound global inventory required')
        require(inventory_source == capture_global_browser(inventory_source.get('evidence')), 'global inventory normalization differs')
        require(inventory_source['tenantId'] == evidence['tenantId'] and clock(inventory_source['capturedAt']) <= observations[0], 'global inventory tenant/observation mismatch')
        ids = [r.get('batch_id') for r in rows]
        require(all(identity(i) for i in ids) and len(ids) == len(set(ids)), 'global batch identity invalid')
        require(all(r.get('warehouse') in WAREHOUSES.values() and r.get('status') in {'COMPLETED', 'VOIDED'} for r in rows), 'active/unknown inbound needs full detail evidence; never treat as zero')
        for row in rows:
            require(str(row.get('total_units', '')).isdigit(), 'invalid terminal batch total')
        records = [r for v in inventory_source['regions'].values() for p in v['pages'] for r in p['rows']]
        require(all(r['inbound'] == 0 for r in records), 'terminal list disagrees with stock inbound')
        regions = {r: normalized(w, []) for r,w in WAREHOUSES.items()}
    result = {k: evidence[k] for k in ('kind', 'sourceId', 'tenantId', 'capturedAt', 'collectionStartedAt', 'materializedAt')}
    result.update(schema='supply-chain-captured-source/v3', regions=regions, evidence=copy.deepcopy(evidence), evidenceDigest=digest(evidence),
                  atomicSnapshot=False, clockBasis='observer_last_page_not_provider_cutoff', normalizationBasis='local_warehouse_partition_of_retained_global_rows')
    if kind == 'inbound':
        result['inventoryEvidenceDigest'] = inventory_source['evidenceDigest']
    return result


def capture(evidence, inventory_source=None):
    require(type(evidence) is dict and evidence.get('schema') == 'seaya-capture-evidence/v1', 'explicit evidence contract required')
    kind = evidence.get('kind')
    require(kind in ('inventory', 'inbound'), 'unsupported evidence kind')
    require(identity(evidence.get('tenantId')) and identity(evidence.get('sourceId')), 'explicit tenant and source identity required')
    captured = clock(evidence.get('capturedAt'))
    started = clock(evidence.get('collectionStartedAt'))
    materialized = clock(evidence.get('materializedAt'))
    require(captured <= started <= materialized, 'source/collection/materialization clocks invalid')
    require(set(evidence.get('regions', {})) == set(WAREHOUSES), 'four exact country scopes required')
    ai = consumer('apply_inventory_snapshot')
    validator = consumer('validate_inventory_snapshot')
    regions = {}
    if kind == 'inbound':
        require(type(inventory_source) is dict and inventory_source.get('kind') == 'inventory' and inventory_source.get('schema') == 'supply-chain-captured-source/v2', 'bound complete inventory evidence required')
        require(inventory_source == capture(inventory_source.get('evidence')), 'inventory normalization differs from evidence')
        require(inventory_source['tenantId'] == evidence['tenantId'] and clock(inventory_source['capturedAt']) == captured, 'inventory tenant or source cutoff differs')
        records = [row for p in inventory_source['regions'].values() for page in p['pages'] for row in page['rows']]
        grouped = ai.aggregate_snapshot({'records': records})
    for region, warehouse in WAREHOUSES.items():
        value = evidence['regions'][region]
        selection = 'all_inventory' if kind == 'inventory' else 'all_in_transit_batches'
        rows, observations = packet(value, {'warehouse': warehouse, 'selection': selection}, started, materialized)
        if kind == 'inventory':
            validation_errors = validator.validate_payload({'records': rows})
            require(not validation_errors, '; '.join(validation_errors))
            require(all(r['warehouse'] == warehouse and clock(r['captured_at']) == captured for r in rows), 'wrong warehouse or relabeled inventory source clock')
            ai.aggregate_snapshot({'records': rows})  # existing alias and duplicate blocking semantics
            regions[region] = normalized(warehouse, copy.deepcopy(rows))
            continue
        ids = [r.get('batch_id') for r in rows]
        require(all(identity(i) for i in ids) and len(set(ids)) == len(ids), 'invalid or duplicate batch identity')
        details = value.get('details')
        require(type(details) is dict and set(details) == set(ids), 'batch details missing or obsolete batch supplied')
        batches = []
        totals = {}
        for row in rows:
            batch_id = row['batch_id']
            require(row.get('country') == region and row.get('warehouse') == warehouse and row.get('status') == 'IN_TRANSIT', 'batch warehouse or lifecycle unsupported')
            detail = details[batch_id]
            require(type(detail) is dict, 'batch detail object required')
            require(detail.get('status') == 'DETAIL_COMPLETE', 'batch detail incomplete')
            raw, times = packet(detail, {'warehouse': warehouse, 'batch_id': batch_id, 'selection': 'all_batch_details'}, observations[-1], materialized)
            amounts = {}
            seen = set()
            for line in raw:
                sku = ai.canonical_inventory_sku(line.get('seller_sku'), region)
                key = (line.get('box_no'), sku)
                require(identity(key[0]) and key not in seen, 'missing or duplicate box/SKU detail')
                seen.add(key)
                quantity = line.get('quantity')
                require(type(quantity) is int and quantity >= 0, 'invalid detail quantity')
                amounts[sku] = amounts.get(sku, 0) + quantity
            require(bool(amounts) and type(row.get('total_units')) is int and row['total_units'] == sum(amounts.values()), 'batch total differs from complete details')
            created = clock(row.get('created_at'))
            anchor = row.get('domestic_inbound_at') or row.get('estimated_anchor_at')
            require(clock(anchor) >= created and created <= captured, 'invalid batch creation/anchor clock')
            require(type(row.get('transport_days')) is int and row['transport_days'] >= 0, 'transport days missing')
            date = datetime.strptime(row.get('expected_sellable_date'), '%Y-%m-%d').date()
            require(date >= clock(anchor).date(), 'sellable date precedes anchor')
            batches.append({'batchId': batch_id, 'createdAt': row['created_at'], 'anchorAt': row.get('domestic_inbound_at'), 'estimatedAnchorAt': row.get('estimated_anchor_at'), 'transportDays': row['transport_days'], 'estimatedSellableDate': row['expected_sellable_date'], 'totalUnits': row['total_units'], 'skuQuantities': amounts, 'detailObservedAt': min(times).isoformat(), 'detailLastObservedAt': max(times).isoformat(), 'detailStatus': 'DETAIL_COMPLETE', 'sourceStatus': row['status']})
            for sku, quantity in amounts.items():
                totals[sku] = totals.get(sku, 0) + quantity
        require({k: v for k, v in totals.items() if v} == {k: v['inbound'] for k, v in grouped.get(region, {}).items() if v['inbound']}, 'batch details and inventory inbound differ')
        regions[region] = normalized(warehouse, batches)
    result = {k: evidence[k] for k in ('kind', 'sourceId', 'tenantId', 'capturedAt', 'collectionStartedAt', 'materializedAt')}
    result.update(schema='supply-chain-captured-source/v2', regions=regions, evidence=copy.deepcopy(evidence), evidenceDigest=digest(evidence))
    if kind == 'inbound':
        result['inventoryEvidenceDigest'] = inventory_source['evidenceDigest']
    return result
