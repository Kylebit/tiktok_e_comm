"""Pull redacted TikTok and Shopee order-demand snapshots without business writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domains.supply_chain_operations.order_demand import (
    aggregate_shopee_orders,
    aggregate_tiktok_orders,
    finalize_order_snapshot,
)


REGIONS = ("MY", "TH", "VN", "PH")
TIKTOK_ORDER_SEARCH = "/order/202309/orders/search"
SHOPEE_ORDER_LIST = "/api/v2/order/get_order_list"
SHOPEE_ORDER_DETAIL = "/api/v2/order/get_order_detail"


def _chunks(start: int, end: int, days: int):
    step = days * 86400
    cursor = start
    while cursor < end:
        chunk_end = min(end, cursor + step)
        yield cursor, chunk_end
        cursor = chunk_end


def _tiktok_shop_ciphers(token: str) -> tuple[dict[str, str], int]:
    from core import shops as tiktok_shops
    shops = tiktok_shops.list_shops(token)
    selected: dict[str, str] = {}
    for shop in shops:
        region = str(shop.get("region") or "").upper()
        cipher = shop.get("cipher") or shop.get("shop_cipher")
        if region in REGIONS and region not in selected and type(cipher) is str and cipher:
            selected[region] = cipher
    return selected, 1


def pull_tiktok_region(
    token: str, cipher: str, start: int, end: int, *, requester=None
) -> tuple[list[dict[str, Any]], int]:
    if requester is None:
        from core.api_client import post as requester
    by_id: dict[str, dict[str, Any]] = {}
    reads = 0
    for chunk_start, chunk_end in _chunks(start, end, 7):
        page_token = ""
        while True:
            query = {"shop_cipher": cipher, "page_size": "100"}
            if page_token:
                query["page_token"] = page_token
            result = requester(
                TIKTOK_ORDER_SEARCH,
                token,
                query,
                {"create_time_ge": chunk_start, "create_time_lt": chunk_end},
            )
            reads += 1
            if result.get("code") != 0:
                raise RuntimeError("TikTok order search returned a business error")
            data = result.get("data") or {}
            for order in data.get("orders") or []:
                order_id = order.get("id")
                if type(order_id) is str and order_id:
                    by_id[order_id] = order
            page_token = data.get("next_page_token") or ""
            if not page_token:
                break
    return list(by_id.values()), reads


def _shopee_order_numbers(
    shop_id: int, token: str, start: int, end: int, *, requester=None
) -> tuple[list[str], int]:
    if requester is None:
        from modules.shopee.client import shop_get as requester
    numbers: set[str] = set()
    reads = 0
    for chunk_start, chunk_end in _chunks(start, end, 14):
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "time_range_field": "create_time",
                "time_from": chunk_start,
                "time_to": chunk_end,
                "page_size": 100,
            }
            if cursor:
                params["cursor"] = cursor
            response = requester(SHOPEE_ORDER_LIST, shop_id, token, params)
            reads += 1
            if response.get("error"):
                raise RuntimeError("Shopee order list returned a business error")
            body = response.get("response") or {}
            for order in body.get("order_list") or []:
                order_sn = order.get("order_sn")
                if type(order_sn) is str and order_sn:
                    numbers.add(order_sn)
            if not body.get("more"):
                break
            cursor = body.get("next_cursor") or ""
            if not cursor:
                raise RuntimeError("Shopee order list pagination cursor unavailable")
    return sorted(numbers), reads


def pull_shopee_region(
    shop_id: int, token: str, start: int, end: int, *, requester=None
) -> tuple[list[dict[str, Any]], int]:
    if requester is None:
        from modules.shopee.client import shop_get as requester
    order_numbers, reads = _shopee_order_numbers(shop_id, token, start, end, requester=requester)
    details: list[dict[str, Any]] = []
    for offset in range(0, len(order_numbers), 50):
        batch = order_numbers[offset : offset + 50]
        response = requester(
            SHOPEE_ORDER_DETAIL,
            shop_id,
            token,
            {
                "order_sn_list": ",".join(batch),
                "response_optional_fields": "item_list",
            },
        )
        reads += 1
        if response.get("error"):
            raise RuntimeError("Shopee order detail returned a business error")
        details.extend((response.get("response") or {}).get("order_list") or [])
    if len(details) != len(order_numbers):
        raise RuntimeError("Shopee order detail coverage is incomplete")
    return details, reads


def _require(value, reason):
    if not value: raise ValueError(reason)


def _identity(value):
    return type(value) is str and bool(value.strip()) and not any(x in value for x in ('...', '…', '*'))


def _receipt_hash(value):
    return hashlib.sha256(json.dumps({k:v for k,v in value.items() if k!='receiptSha256'},sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _seal_receipt(value):
    value['rowCount']=len(value['rows']);value['receiptSha256']=_receipt_hash(value);return value


def _check_receipt(value):
    _require(type(value) is dict and type(value.get('rows')) is list and type(value.get('rowCount')) is int and value['rowCount']==len(value['rows']) and value.get('receiptSha256')==_receipt_hash(value),'receipt hash/count mismatch')


def _redact_order(row, platform):
    from domains.supply_chain_operations import order_demand
    _require(type(row) is dict, 'order object missing')
    key, status, items = ('id','status','line_items') if platform=='tiktok' else ('order_sn','order_status','item_list')
    _require(_identity(row.get(key)) and type(row.get(status)) is str and row[status].strip(), 'order identity or lifecycle missing')
    fields = (key,status,'create_time','paid_time','is_on_hold_order','is_sample_order','is_replacement_order') if platform=='tiktok' else (key,status,'create_time')
    out={k:row[k] for k in fields if k in row}
    _require(all(type(v) in (str,int,bool) or (k=='paid_time' and v is None) for k,v in out.items()),'retained order field type invalid')
    for k in ('is_on_hold_order','is_sample_order','is_replacement_order'):
        if k in out:_require(type(out[k]) is bool,'order flag type invalid')
    excluded=order_demand._TIKTOK_EXCLUDED if platform=='tiktok' else order_demand._SHOPEE_EXCLUDED
    eligible=row[status].upper() not in excluded and not (platform=='tiktok' and any(row.get(k) is True for k in fields[4:]))
    detail=row.get(items)
    if eligible:_require(type(detail) is list and detail and all(type(v) is dict for v in detail),'order detail missing')
    if detail is not None:
        _require(type(detail) is list and all(type(v) is dict for v in detail),'order detail malformed')
        allowed=('id','sku_id','product_id','seller_sku') if platform=='tiktok' else ('item_id','model_id','model_sku','item_sku','model_quantity_purchased','cancelled_qty','returned_qty')
        out[items]=[{k:v[k] for k in allowed if k in v} for v in detail]
        _require(all(type(v) in (str,int) for item in out[items] for v in item.values()),'retained detail field type invalid')
    return out


def _unique(rows, key):
    found={}
    for row in rows:
        identity=row.get(key);_require(_identity(identity),'record identity missing')
        _require(identity not in found or found[identity]==row,'same order identity has conflicting content')
        found[identity]=row
    return list(found.values())


def _window_row(row, start, end):
    _require(type(row.get('create_time')) is int and start<=row['create_time']<end,'order outside requested block')


def captured_order_rows(envelope, platform, target, start, end):
    """Validate observed independent block chains and detail receipts, then dedupe."""
    _require(type(envelope) is dict and envelope.get('target')==target,'order capture target mismatch')
    blocks=envelope.get('blocks');_require(type(blocks) is list and blocks,'order blocks missing')
    expected=list(_chunks(start,end,7 if platform=='tiktok' else 14))
    _require([(b.get('start'),b.get('end')) for b in blocks]==expected,'order block window gap or overlap')
    all_rows=[];listed={}
    for block in blocks:
        pages=block.get('pages');_require(type(pages) is list and pages,'block pages missing');cursor='';seen=set()
        for index,page in enumerate(pages):
            _check_receipt(page)
            _require(page.get('windowStart')==block['start'] and page.get('windowEnd')==block['end'],'page window mismatch')
            _require(type(page) is dict and page.get('target')==target and page.get('cursor')==cursor and cursor not in seen,'page target or cursor invalid')
            seen.add(cursor);cursor=page.get('nextCursor')
            _require(type(cursor) is str and bool(cursor)==(index<len(pages)-1),'page terminal evidence missing')
            _require(page.get('endpoint')==(TIKTOK_ORDER_SEARCH if platform=='tiktok' else SHOPEE_ORDER_LIST),'page endpoint mismatch')
            rows=page.get('rows');_require(type(rows) is list,'page rows missing')
            if platform=='tiktok':
                for row in rows:
                    _require(_redact_order(row,platform)==row,'capture contains non-whitelisted order fields');_window_row(row,block['start'],block['end'])
                all_rows.extend(rows)
            else:
                _require(type(page.get('more')) is bool and page['more']==bool(cursor),'Shopee terminal flag missing')
                for row in rows:
                    _require(type(row) is dict and set(row)=={'order_sn'} and _identity(row['order_sn']),'list identity invalid')
                    order=row['order_sn'];bounds=(block['start'],block['end'])
                    _require(order not in listed or listed[order]==bounds,'order repeated across time blocks')
                    listed[order]=bounds
    if platform=='shopee':
        receipts=envelope.get('details');_require(type(receipts) is list,'detail receipts missing');requested=[]
        for receipt in receipts:
            _check_receipt(receipt)
            ids=receipt.get('requestedIds');rows=receipt.get('rows')
            _require(receipt.get('target')==target and receipt.get('endpoint')==SHOPEE_ORDER_DETAIL,'detail target mismatch')
            _require(type(ids) is list and 0<len(ids)<=50 and len(set(ids))==len(ids) and all(_identity(i) for i in ids),'detail request identities invalid')
            _require(type(rows) is list and len(rows)==len(ids),'detail coverage incomplete')
            _require(all(type(r) is dict and _identity(r.get('order_sn')) for r in rows),'detail identity missing')
            _require({r['order_sn'] for r in rows}==set(ids),'detail exact identity set differs')
            requested.extend(ids)
            for row in rows:
                _require(row['order_sn'] in listed,'unlisted detail')
                _require(_redact_order(row,platform)==row,'capture contains non-whitelisted detail fields');_window_row(row,*listed[row['order_sn']])
            all_rows.extend(rows)
        _require(len(requested)==len(set(requested)) and set(requested)==set(listed),'list/detail global coverage differs')
    for row in all_rows:
        # Missing/null/zero payment uses the original create_time fallback.
        paid_time=row.get('paid_time')
        if paid_time is not None:
            _require(type(paid_time) is int and (paid_time==0 or start<=paid_time<end),'payment outside capture window')
    return _unique(all_rows,'id' if platform=='tiktok' else 'order_sn')


def _receipt_requester(platform, target, requester, envelope, shop_id):
    seen=set()
    def call(endpoint, session, query, body=None):
        if platform=='tiktok':
            _require(query.get('shop_cipher')==target,'request target mismatch')
            bounds=(body['create_time_ge'],body['create_time_lt']);cursor=query.get('page_token','')
            response=requester(endpoint,session,query,body)
            _require(response.get('code')==0 and type(response.get('data')) is dict,'TikTok business response invalid')
            data=response['data'];_require(type(data.get('next_page_token')) is str and type(data.get('orders')) is list,'TikTok terminal or rows missing')
            for key in ('shop_cipher','cipher'):
                if key in data:_require(data[key]==target,'response target mismatch')
            if 'shop_id' in data:_require(str(data['shop_id'])==str(shop_id),'response shop ID mismatch')
            rows=[_redact_order(r,platform) for r in data['orders']]
            next_cursor=data['next_page_token'];page={'target':target,'endpoint':endpoint,'cursor':cursor,'nextCursor':next_cursor,'rows':rows}
            returned={'code':0,'data':{'orders':rows,'next_page_token':next_cursor}}
        else:
            shop=session;session=query;params=body
            _require(str(shop)==target,'request target mismatch')
            response=requester(endpoint,shop,session,params)
            _require(not response.get('error') and type(response.get('response')) is dict,'Shopee business response invalid')
            data=response['response']
            if 'shop_id' in data:_require(str(data['shop_id'])==target,'response target mismatch')
            _require(type(data.get('order_list')) is list,'Shopee rows missing')
            if endpoint==SHOPEE_ORDER_DETAIL:
                rows=[_redact_order(r,platform) for r in data['order_list']];ids=params['order_sn_list'].split(',')
                _require(len(rows)==len(ids) and {r['order_sn'] for r in rows}==set(ids),'detail exact identity set differs')
                envelope['details'].append(_seal_receipt({'target':target,'endpoint':endpoint,'requestedIds':ids,'rows':rows}))
                return {'response':{'order_list':rows}}
            bounds=(params['time_from'],params['time_to']);cursor=params.get('cursor','')
            _require(type(data.get('more')) is bool and type(data.get('next_cursor')) is str and data['more']==bool(data['next_cursor']),'Shopee terminal flag missing')
            rows=[{'order_sn':r.get('order_sn')} for r in data['order_list']]
            _require(all(_identity(r['order_sn']) for r in rows),'list identity missing')
            next_cursor=data['next_cursor'];page={'target':target,'endpoint':endpoint,'cursor':cursor,'nextCursor':next_cursor,'more':data['more'],'rows':rows}
            returned={'response':{'order_list':rows,'more':data['more'],'next_cursor':next_cursor}}
        key=(*bounds,cursor);_require(key not in seen and len(seen)<10000,'cursor loop or page limit');seen.add(key)
        page.update(windowStart=bounds[0],windowEnd=bounds[1]);_seal_receipt(page)
        blocks=envelope['blocks']
        if not blocks or (blocks[-1]['start'],blocks[-1]['end'])!=bounds:blocks.append({'start':bounds[0],'end':bounds[1],'pages':[]})
        blocks[-1]['pages'].append(page)
        return returned
    return call


def capture_orders(*, targets, cutoff, days, source_id, started_at, materialized_at, sessions, requesters):
    """Explicit in-memory capture through existing collectors. No auth/default IO.

    Caller supplies exact selected sessions and clocks; secrets never enter output.
    No CLI live mode is added. Missing dependencies fail before the first request.
    """
    _require(type(targets) is dict and set(targets)==set(REGIONS),'four explicit targets required')
    _require(type(days) is int and 30<=days<=366 and _identity(source_id),'capture window/source invalid')
    stamps=[datetime.fromisoformat(v) for v in (cutoff,started_at,materialized_at)]
    _require(all(v.tzinfo is not None for v in stamps) and stamps[0]<=stamps[1]<=stamps[2],'independent capture clocks invalid')
    _require(type(sessions) is dict and set(sessions)==set(REGIONS) and type(requesters) is dict and set(requesters)=={'tiktok','shopee'} and all(callable(v) for v in requesters.values()),'explicit sessions/requesters not ready')
    for r,t in targets.items():
        _require(type(t) is dict and set(t)=={'tiktok_shop_id','tiktok_cipher','shopee_shop_id'} and _identity(t['tiktok_shop_id']) and _identity(t['tiktok_cipher']) and type(t['shopee_shop_id']) is int and t['shopee_shop_id']>0,'explicit shop identity invalid')
        _require(type(sessions[r]) is dict and set(sessions[r])=={'tiktok','shopee'} and all(type(v) is str and v for v in sessions[r].values()),'selected sessions not ready')
    for k in ('tiktok_shop_id','tiktok_cipher','shopee_shop_id'):_require(len({t[k] for t in targets.values()})==4,'target reused across countries')
    end=int(stamps[0].timestamp());start=end-days*86400
    source={'schema':'supply-chain-captured-source/v2','kind':'orders','sourceId':source_id,'capturedAt':cutoff,'collectionStartedAt':started_at,'materializedAt':materialized_at,'days':days,'targets':targets,'regions':{}}
    for r,t in targets.items():
        source['regions'][r]={}
        for platform in ('tiktok','shopee'):
            target=t['tiktok_cipher'] if platform=='tiktok' else str(t['shopee_shop_id']);env={'target':target,'blocks':[]}
            if platform=='shopee':env['details']=[]
            requester=_receipt_requester(platform,target,requesters[platform],env,t['tiktok_shop_id'] if platform=='tiktok' else t['shopee_shop_id'])
            if platform=='tiktok':pull_tiktok_region(sessions[r][platform],target,start,end,requester=requester)
            else:pull_shopee_region(t['shopee_shop_id'],sessions[r][platform],start,end,requester=requester)
            rows=captured_order_rows(env,platform,target,start,end)
            aggregate=aggregate_tiktok_orders if platform=='tiktok' else aggregate_shopee_orders
            _,evidence=aggregate(rows,r)
            _require(not any(evidence.get(k,0) for k in ('orders_invalid','item_lines_unresolved','item_lines_invalid_quantity')),'unresolved order facts')
            source['regions'][r][platform]=env
    return source


def main() -> int:
    from core import auth as tiktok_auth
    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.shops import sync_shop_ids
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--regions", nargs="+", default=list(REGIONS))
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "supply_chain_order_demand_latest.json",
    )
    args = parser.parse_args()
    regions = tuple(dict.fromkeys(str(value).upper() for value in args.regions))
    if args.days < 30 or any(region not in REGIONS for region in regions):
        raise SystemExit("days must be >=30 and regions must be MY/TH/VN/PH")

    captured_at = datetime.now(timezone.utc)
    end = int(captured_at.timestamp())
    start = end - args.days * 86400
    output: dict[str, Any] = {
        "schemaVersion": "order_demand_snapshot_v1",
        "capturedAt": captured_at.isoformat(),
        "days": args.days,
        "countries": {},
        "networkReads": 0,
        "authWrites": "Shopee refresh only when required",
        "businessWrites": 0,
    }

    tiktok_token = tiktok_auth.access_token()
    ciphers, reads = _tiktok_shop_ciphers(tiktok_token)
    output["networkReads"] += reads
    shopee_ids = sync_shop_ids()

    for region in regions:
        if region not in ciphers or region not in shopee_ids:
            raise RuntimeError(f"{region} shop binding unavailable")
        print(f"[{region}] TikTok order pages...", flush=True)
        tiktok_orders, reads = pull_tiktok_region(
            tiktok_token, ciphers[region], start, end
        )
        output["networkReads"] += reads
        tk_rows, tk_evidence = aggregate_tiktok_orders(tiktok_orders, region)
        tk_snapshot = finalize_order_snapshot(
            tk_rows,
            region=region,
            platform="TikTok",
            captured_at=captured_at,
            days=args.days,
            evidence=tk_evidence,
        )
        print(
            f"[{region}] TikTok ready: {tk_evidence.get('orders_included', 0)} orders, "
            f"{len(tk_snapshot['facts'])} mapped SKUs",
            flush=True,
        )

        print(f"[{region}] Shopee order pages/details...", flush=True)
        shop_id = int(shopee_ids[region])
        shopee_token = ensure_shop_token(shop_id)
        shopee_orders, reads = pull_shopee_region(
            shop_id, shopee_token, start, end
        )
        output["networkReads"] += reads
        sp_rows, sp_evidence = aggregate_shopee_orders(shopee_orders, region)
        sp_snapshot = finalize_order_snapshot(
            sp_rows,
            region=region,
            platform="Shopee",
            captured_at=captured_at,
            days=args.days,
            evidence=sp_evidence,
        )
        print(
            f"[{region}] Shopee ready: {sp_evidence.get('orders_included', 0)} orders, "
            f"{len(sp_snapshot['facts'])} mapped SKUs",
            flush=True,
        )
        output["countries"][region] = {
            "tiktok": tk_snapshot,
            "shopee": sp_snapshot,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "READY",
                "regions": list(regions),
                "network_reads": output["networkReads"],
                "business_writes": 0,
                "output": str(args.output),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
