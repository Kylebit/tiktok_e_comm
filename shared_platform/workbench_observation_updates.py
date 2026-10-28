"""Coordinator-only, dated observation maintenance; never execution authority."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
from shared_platform.workbench_observations import _bytes, _time, read_registry

FIELDS = {'stage', 'last_reported_state', 'blocker', 'evidence_notes', 'user_decision',
          'closed', 'result_url', 'products', 'sources'}


def update_observation(manifest_path, update, *, expected_sha256, apply=False, now=None):
    path = Path(manifest_path)
    if not path.is_absolute():
        raise ValueError('explicit manifest path required')
    raw = _bytes({'path':str(path), 'sha256':expected_sha256})
    data = json.loads(raw)
    if update.get('schema') != 'orbit-work-observation-update/v1':
        raise ValueError('invalid producer update')
    changes = update.get('changes')
    if not isinstance(changes,dict) or not changes or set(changes)-FIELDS:
        raise ValueError('update cannot change accepted identity, owner, handover or execution mapping')
    matches = [i for i in data.get('items',[]) if i.get('work_id')==update.get('work_id')]
    if len(matches)!=1 or matches[0].get('source_session_id')!=update.get('source_session_id'):
        raise ValueError('exact original work/session required')
    old = matches[0]
    observed = _time(update.get('observed_at'))
    if observed <= _time(old.get('observed_at')):
        raise ValueError('observation must advance from a new dated producer receipt')
    ref = update.get('receipt_ref')
    receipt = json.loads(_bytes(ref))
    if (receipt.get('schema') != 'orbit-work-observation-update/v1'
            or any(receipt.get(k)!=update.get(k) for k in ('work_id','source_session_id','observed_at','changes'))):
        raise ValueError('dated producer receipt must bind all proposed changes')
    new = copy.deepcopy(data)
    item = next(i for i in new['items'] if i['work_id']==old['work_id'])
    item.update(copy.deepcopy(changes), observed_at=observed.isoformat())
    item['sources'] = [*item.get('sources',[]), {**ref,'label':'Dated coordinator/producer observation update'}]
    encoded = (json.dumps(new,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    candidate = path.with_name(path.name + '.candidate-' + os.urandom(8).hex())
    lock = path.with_name(path.name+'.update.lock')
    acquired = False
    try:
        if apply:
            fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f: f.write(str(os.getpid()))
            acquired=True
        with candidate.open('xb') as f:
            f.write(encoded);f.flush();os.fsync(f.fileno())
        projected=read_registry(candidate,now=now)
        row=next(i for i in projected['items'] if i['work_id']==old['work_id'])
        if projected.get('scope_status')!='verified' or row['source_status']!='verified':
            raise ValueError('scope or producer evidence unavailable; keep original manifest')
        if any((input_p.get('thumbnail') and p['image_status']!='verified') or
               (p.get('name_verified') is False and input_p.get('name_verified') is True)
               for p,input_p in zip(row['products'],item.get('products',[]))):
            raise ValueError('product lineage needs repair before accepting update')
        # Compare again while holding the single-coordinator writer lock.
        _bytes({'path':str(path),'sha256':expected_sha256})
        if apply:
            os.replace(candidate,path)
        return {'applied':apply,'work_id':old['work_id'],'observed_at':row['observed_at'],
                'sha256':hashlib.sha256(encoded).hexdigest(),'complete':projected['complete']}
    finally:
        if candidate.exists(): candidate.unlink()
        if acquired: lock.unlink()
