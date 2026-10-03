"""Coverage of explicit local captures, never a provider completeness assertion."""
from collections import Counter
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
from pathlib import Path
from .tiktok_coverage import build_coverage

SCOPE = ('platform','site','shop_id','start','end','timezone')

def require(value, code):
    if not value: raise ValueError(code)

def stamp(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    require(result.tzinfo is not None,'timezone_missing')
    return result

def read_local(value):
    path=Path(value);require(path.is_absolute(),'absolute_path_required')
    raw=path.read_bytes();require(len(raw)<=16*1024*1024,'capture_too_large')
    return json.loads(raw),sha256(raw).hexdigest()

def page_rows(day):
    total=day.get('total_rows');pages=day.get('pages')
    require(type(total) is int and total>=0 and isinstance(pages,list) and pages,'page_evidence_missing')
    cursor='';seen=set();rows=[]
    for i,page in enumerate(pages):
        require(page.get('cursor')==cursor and cursor not in seen,'page_chain_invalid');seen.add(cursor)
        require(isinstance(page.get('rows'),list),'page_rows_missing');rows.extend(page['rows'])
        cursor=page.get('next_cursor');require(type(cursor) is str and bool(cursor)==(i<len(pages)-1),'page_chain_incomplete')
    require(len(rows)==total,'page_total_mismatch')
    return rows

def captured_coverage(profile,evidence,evidence_sha):
    start,end=(date.fromisoformat(profile[k]) for k in ('start','end'))
    zone=stamp('2000-01-01T00:00:00'+profile['timezone']).tzinfo
    require(0<=(end-start).days<=366,'coverage_period_too_long')
    days=[(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    result={'schema_version':'profit-period-coverage/v1','status':'unknown','scope':{k:profile[k] for k in SCOPE},
            'proof_kind':'local_capture_internal_consistency_only','streams':{},'days':[], 'issues':[], 'order_settlement':None}
    observed=Counter();settled_by_day={day:[] for day in days}
    observed_invalid=False
    for row in evidence.get('orders',[]):
        try:
            day=stamp(row['settled_at']).astimezone(zone).date().isoformat()
            if day in settled_by_day:
                require(type(row.get('order_id')) is str and row['order_id'].strip(),'settlement_identity_missing')
                observed[day]+=1;settled_by_day[day].append((row['order_id'],stamp(row['settled_at']).isoformat()))
        except (KeyError,ValueError,TypeError,AttributeError):observed_invalid=True
    for day in days:
        result['days'].append({'date':day,'orders':{'state':'unknown','count':None},'settlements':{'state':'unknown','count':None,'observed_rows':observed[day]}})
    if not profile.get('coverage_path'):
        result['issues'].append('coverage_not_selected');return result
    try:
        capture,digest=read_local(profile['coverage_path']);result['source_sha256']=digest
        require(capture.get('schema_version')=='profit-captured-coverage/v1','coverage_schema_invalid')
        require(capture.get('scope')==result['scope'] and capture.get('evidence_sha256')==evidence_sha,'coverage_scope_or_source_mismatch')
        require(set(capture['streams'])=={'orders','settlements'},'coverage_streams_missing')
        complete={};all_rows={}
        for kind,basis in [('orders','order_created_at'),('settlements','settled_at')]:
            stream=capture['streams'][kind];as_of=stamp(stream['as_of'])
            require(stream.get('time_basis')==basis and type(stream.get('source_id')) is str and stream['source_id'].strip(),'stream_identity_missing')
            result['streams'][kind]={'source_id':stream['source_id'],'as_of':stream['as_of'],'time_basis':basis}
            by_day={item['date']:item for item in stream['days']}
            require(len(by_day)==len(stream['days']) and set(by_day)<=set(days),'duplicate_or_outside_day')
            complete[kind]=True;all_rows[kind]=[];identities=set()
            for target in result['days']:
                day=target['date']
                try:
                    require(day in by_day,'day_missing')
                    rows=page_rows(by_day[day])
                    require(as_of>=datetime.combine(date.fromisoformat(day)+timedelta(days=1),time.min,zone),'as_of_before_day_end')
                    daily_ids=[]
                    for row in rows:
                        require(type(row) is dict and type(row.get('order_id')) is str and bool(row['order_id'].strip()),'row_identity_missing')
                        when=stamp(row[basis]);require(when.astimezone(zone).date().isoformat()==day and when<=as_of,'row_time_mismatch')
                        key=row['order_id'] if kind=='orders' else (row['order_id'],when.isoformat())
                        require(key not in identities,'duplicate_record');identities.add(key)
                        if kind=='orders':require(type(row.get('order_status')) is str and bool(row['order_status'].strip()),'order_lifecycle_missing')
                        daily_ids.append(key)
                    if kind=='settlements':
                        require(not observed_invalid and Counter(daily_ids)==Counter(settled_by_day[day]),'settlement_rows_not_reconciled')
                    target[kind].update(state='complete_capture',count=len(rows));all_rows[kind].extend(rows)
                except (ValueError,KeyError,TypeError,AttributeError) as exc:
                    complete[kind]=False;result['issues'].append({'stream':kind,'date':day,'code':str(exc) if type(exc) is ValueError else 'invalid_daily_evidence'})
        if all(complete.values()):
            result['status']='complete_capture'
            if profile['platform']=='tiktok':
                result['order_settlement']=build_coverage(orders=all_rows['orders'],settled_order_ids={r['order_id'] for r in all_rows['settlements']},start=start,end=end,
                    as_of=end,settlement_snapshot_id=evidence_sha,site=profile['site'],timezone_name=profile['timezone'])
                result['order_settlement']['scope_limitation']='settlements_within_selected_period_only; not proof of no settlement after period'
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        # Envelope failure invalidates all completeness claims, never observations.
        for row in result['days']:
            for kind in ('orders','settlements'):row[kind].update(state='unknown',count=None)
        result['issues'].append('invalid_coverage_envelope');result['status']='unknown'
    return result
