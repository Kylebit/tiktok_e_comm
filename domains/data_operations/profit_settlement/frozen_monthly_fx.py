"""Read a complete, exact-period monthly FX artifact without network access."""
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from dataclasses import dataclass

@dataclass(frozen=True)
class MonthlyAverageFx:
    snapshot: object
    evidence: dict
from .shared_inputs import FxSnapshot

def load_frozen_monthly_fx(path, start, end):
    raw=Path(path).read_bytes()
    payload=json.loads(raw)
    if payload.get('schema_version')!='monthly-average-fx/v1' or payload.get('period')!={'start':start.isoformat(),'end':end.isoformat()}:
        raise ValueError('monthly FX schema/period mismatch')
    days=(end-start).days+1
    if payload.get('calendar_day_count')!=days or not payload.get('snapshot_id') or not payload.get('provider') or not payload.get('methodology'):
        raise ValueError('monthly FX lineage/calendar coverage missing')
    rates=payload.get('rates_cny') or {}
    if not rates or str(rates.get('CNY'))!='1':raise ValueError('monthly FX requires CNY base=1')
    for currency,value in rates.items():
        number=Decimal(str(value))
        if not number.is_finite() or number<=0:raise ValueError('monthly FX rate must be finite positive')
        if currency!='CNY' and (payload.get('sample_count_by_currency') or {}).get(currency)!=days:
            raise ValueError('monthly FX daily coverage incomplete')
    snapshot=FxSnapshot.from_mapping(rates,source=payload['provider']+'; '+payload['methodology'],as_of=payload['as_of'],snapshot_id=payload['snapshot_id'])
    return MonthlyAverageFx(snapshot,payload),sha256(raw).hexdigest()
