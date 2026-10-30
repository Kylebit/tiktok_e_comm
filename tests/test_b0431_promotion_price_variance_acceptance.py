"""Real R87 authority/full56-table clone; no API, schema or original DB write."""
from copy import deepcopy
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from shared_platform import promotion_price_variance_acceptance as acceptance
from shared_platform.oneclick_release_controlplane import OneClickReleaseStore,SystemicIdentityError

BASE='cebc945d44b4e957c7ebadd5e65a0dc779806278'
JOB='oneclick-job:77e380e66ba67bdcf7571213'


def pin(path):return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path,doc):
    path.write_text(json.dumps(doc,ensure_ascii=False,sort_keys=True,separators=(',',':')),encoding='utf-8')
    return pin(path)


def image(store):
    with store._connect() as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name:[dict(r) for r in db.execute('SELECT * FROM "'+name+'"')] for name in names}


@pytest.fixture
def captured(tmp_path,monkeypatch):
    supplied=os.environ.get('ORBIT_B0431_PRICE_ACCEPTANCE_INPUTS')
    if not supplied:pytest.skip('explicit private R87 full clone not supplied')
    nb=Path(supplied);folder=nb/'targets/native-shopee-price-variance-acceptance-20261030/current-inputs-001'
    source=folder/'CURRENT_RELEASE_STORE_FULL.private.db';sourcepin=pin(source)
    assert sourcepin['sha256']=='4c3d751f7b1a14d166acb8528bc079942a40202369e8caf011f29a1d37af1409'
    auth=pin(nb/'USER_SHOPEE_PH_TH_EXISTING_PRICE_ACCEPTANCE_AUTHORITY_R87.json')
    assert auth['sha256']=='6202e47fd135121d5661d6328a1f88c75c796f1268bbabd9c859f6f952a9fe50'
    doc=acceptance._pin(auth);view=acceptance._pin(doc['official_readback_ref'])
    assert doc['official_readback_ref']['sha256']=='9c946538809608f52689c6ee6d58eec1393f4d079a1633c12e72389b6d7d020c'
    mapping=json.loads((folder/'PH_TH_ORIGINAL_OUTCOME_AND_THREE_MODEL_MAPPING.private.json').read_bytes())
    outcomes={r['target']:r['original_outcome_receipt_digest'] for r in mapping}
    shutil.copy2(source,tmp_path/'PRIVATE_FULL56_CLONE.db');store=OneClickReleaseStore(tmp_path/'PRIVATE_FULL56_CLONE.db')
    with store._connect() as db:
        commands={r['target_label']:json.loads(r['command_json'])['payload'] for r in db.execute(
            'SELECT target_label,command_json FROM oneclick_release_targets WHERE job_id=?',(JOB,)) if r['target_label'] in outcomes}
    raw=subprocess.check_output(['git','-C',str(Path(__file__).resolve().parents[1]),'show',BASE+':shared_platform/promotion_price_variance_acceptance.py']).decode()
    old={'__name__':'shared_platform.ORIGINAL_SINGLE_MODEL_ACCEPTANCE'};exec(compile(raw,'ORIGINAL_CEBC_ACCEPTANCE','exec'),old)
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('Network forbidden'))
    yield SimpleNamespace(store=store,authority=auth,doc=doc,view=view,outcomes=outcomes,commands=commands,old=old,old_source=raw,tmp=tmp_path)
    assert pin(source)==sourcepin


def test_real_R87_three_models_append_only_once_and_old_v1_reader(captured,monkeypatch):
    c=captured;before=image(c.store)
    # Actual original implementation rejects this exact authority/command shape.
    with pytest.raises(KeyError,match='model_id'):
        c.old['record_existing_price_acceptance'](c.store,authorization_ref=c.authority,
            expected_outcome_receipt_digests=c.outcomes)
    assert image(c.store)==before
    result=c.store.record_existing_promotion_price_acceptance(authorization_ref=c.authority,
        expected_outcome_receipt_digests=c.outcomes)
    assert result['acceptance_events_appended']==2 and result['external_writes_performed']==[]
    after=image(c.store)
    for table in before:
        if table not in {'oneclick_release_jobs','oneclick_release_events','sqlite_sequence'}:
            assert after[table]==before[table],table
    assert len(after['oneclick_release_events'])==len(before['oneclick_release_events'])+2
    assert after['oneclick_release_events'][:-2]==before['oneclick_release_events']
    for old,new in zip(before['oneclick_release_jobs'],after['oneclick_release_jobs']):
        assert {k:v for k,v in old.items() if k not in {'status','updated_at','completed_at'}}=={
            k:v for k,v in new.items() if k not in {'status','updated_at','completed_at'}}
    # The deployed cebc reader and job projection must consume the unchanged v1 event.
    monkeypatch.setattr(acceptance,'accepted_existing_price',c.old['accepted_existing_price'])
    projected=c.store.get_job(job_id=JOB)
    assert projected['phase']=='SUCCEEDED'
    with c.store._connect() as db:
        for row in db.execute('SELECT * FROM oneclick_release_targets WHERE job_id=?',(JOB,)):
            if row['target_label'] not in c.outcomes:continue
            value=acceptance.accepted_existing_price(db,row)
            assert value['schema_version']==acceptance.SCHEMA
            assert len(value['official_observation']['model_ids'])==3 and row['status']=='RECONCILIATION_REQUIRED'
            assert row['dispatch_count']==1 and row['pending_write_intent_json'] is not None
            assert value['original_unknown_and_write_occupancy_preserved'] is True
    again=c.store.record_existing_promotion_price_acceptance(authorization_ref=c.authority,
        expected_outcome_receipt_digests=c.outcomes)
    assert again['idempotent'] is True and again['acceptance_events_appended']==0
    assert image(c.store)==after
    source=Path(acceptance.__file__).read_text(encoding='utf-8-sig')
    segment=lambda s:ast.get_source_segment(s,next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name=='accepted_existing_price'))
    assert segment(source)==segment(c.old_source)
    assert acceptance.EVENT==c.old['EVENT'] and acceptance.SCHEMA==c.old['SCHEMA']


def test_missing_duplicate_wrong_identity_SKU_and_prices_rollback(captured):
    c=captured;before=image(c.store)
    changes=[lambda d:d['targets'][0]['model_ids'].pop(),
        lambda d:d['targets'][0]['model_ids'].__setitem__(1,d['targets'][0]['model_ids'][0]),
        lambda d:d['targets'][0]['model_ids'].__setitem__(0,999),
        lambda d:d['seller_skus'].__setitem__(0,'WRONG_SKU'),
        lambda d:d['targets'][0].update(item_id=1),
        lambda d:d['targets'][0].update(accepted_official_price='310'),
        lambda d:d['targets'][0].update(original_list_price='442')]
    for i,change in enumerate(changes):
        doc=deepcopy(c.doc);change(doc)
        with pytest.raises(SystemicIdentityError):
            c.store.record_existing_promotion_price_acceptance(authorization_ref=write(c.tmp/f'BAD_AUTH_{i}.json',doc),
                expected_outcome_receipt_digests=c.outcomes)
        assert image(c.store)==before
    bad={**c.outcomes,'promotion:shopee:PH':'0'*64}
    with pytest.raises(SystemicIdentityError,match='OUTCOME_IDENTITY_CHANGED'):
        c.store.record_existing_promotion_price_acceptance(authorization_ref=c.authority,expected_outcome_receipt_digests=bad)
    assert image(c.store)==before


def test_real_pages_each_model_price_coverage_and_legacy_single_model(captured):
    c=captured;target=c.doc['targets'][0];command=c.commands[target['target']]
    current=acceptance._official_member(c.view,target,command)
    assert len(current['model_ids'])==3 and current['accepted_price']=='309'
    def altered_view(change):
        view=deepcopy(c.view);record=next(r for r in view['reads'] if r['target']==target['target'])
        raw=acceptance._pin(record['response_ref']);member=next(i for i in raw['response']['item_list'] if i['item_id']==command['item_id'])
        change(raw['response'],member);record['response_ref']=write(c.tmp/'ALTERED_OFFICIAL_FIXTURE.json',raw)
        return view
    for change in (lambda page,m:m['model_list'].pop(),
                   lambda page,m:m['model_list'].append(deepcopy(m['model_list'][0])),
                   lambda page,m:m['model_list'][0].update(model_id=999),
                   lambda page,m:m['model_list'][0].update(model_promotion_price=308),
                   lambda page,m:m['model_list'][0].update(model_original_price=440),
                   lambda page,m:page.update(more=True),
                   lambda page,m:page.update(start_time=page['start_time']+1),
                   lambda page,m:page['item_list'].append(deepcopy(m))):
        with pytest.raises(SystemicIdentityError):acceptance._official_member(altered_view(change),target,command)
    # Explicit single-Model contract fixture (not an actual403 business acceptance).
    single=deepcopy(command);single['model_ids']=[command['model_ids'][0]]
    one={k:v for k,v in target.items() if k not in {'model_ids'}};one['model_id']=single['model_ids'][0]
    view=altered_view(lambda page,m:m.update(model_list=[r for r in m['model_list'] if r['model_id']==one['model_id']]))
    assert acceptance._official_member(view,one,single)==c.old['_official_member'](view,one,single)
