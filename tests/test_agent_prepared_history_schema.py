"""R1-only schema maintenance never becomes authority to skip history reads."""
import sqlite3

import pytest

from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform.release_store import ReleaseStore

TABLES={
    'release_plans':'CREATE TABLE release_plans (plan_id TEXT PRIMARY KEY, product_id TEXT)',
    'release_runs':'CREATE TABLE release_runs (plan_id TEXT)',
    'release_approvals':'CREATE TABLE release_approvals (plan_id TEXT)',
}


def store(tmp_path,names=()):
    path=tmp_path/'r1-only.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE original_r1 (reference TEXT)')
        db.execute("INSERT INTO original_r1 VALUES ('original-preparation')")
        for name in names:db.execute(TABLES[name])
    return ReleaseStore(path)


def test_source_preflight_reports_absent_schema_without_claiming_empty_history(tmp_path):
    ledger=store(tmp_path);before=ledger.path.read_bytes()
    result=adoption._verify_business_history(ledger,'new-B',allow_uninitialized=True)
    assert result=={'status':'UNINITIALIZED_BUSINESS_SCHEMA_REQUIRES_MAINTENANCE',
                    'empty_business_history_verified':False}
    assert ledger.path.read_bytes()==before


def test_default_adoption_still_requires_installed_history_schema(tmp_path):
    ledger=store(tmp_path)
    with pytest.raises(adoption.AdoptionBlocked,match='ADOPTION_BUSINESS_HISTORY_SCHEMA_REQUIRED'):
        adoption._verify_business_history(ledger,'new-B')


@pytest.mark.parametrize('mask',range(1,7))
def test_partial_schema_cannot_be_adopted_or_treated_as_initialization(tmp_path,mask):
    names=[name for bit,name in enumerate(TABLES) if mask & (1<<bit)]
    ledger=store(tmp_path,names)
    for allowed in (False,True):
        with pytest.raises(adoption.AdoptionBlocked,match='ADOPTION_BUSINESS_HISTORY_SCHEMA_INCOMPLETE'):
            adoption._verify_business_history(ledger,'new-B',allow_uninitialized=allowed)


def test_strict_history_is_rechecked_after_schema_maintenance(tmp_path):
    ledger=store(tmp_path)
    assert adoption._verify_business_history(ledger,'new-B',allow_uninitialized=True)['empty_business_history_verified'] is False
    with sqlite3.connect(ledger.path) as db:
        for statement in TABLES.values():db.execute(statement)
    assert adoption._verify_business_history(ledger,'new-B')['empty_business_history_verified'] is True


@pytest.mark.parametrize('history,code',[
    ('release_runs','ADOPTION_EXISTING_BUSINESS_RUN_REQUIRES_RECONCILIATION'),
    ('release_approvals','ADOPTION_EXISTING_HUMAN_DECISION_FORBIDDEN'),
])
def test_existing_exact_offer_history_stays_blocked_in_both_phases(tmp_path,history,code):
    ledger=store(tmp_path,TABLES)
    with sqlite3.connect(ledger.path) as db:
        db.execute("INSERT INTO release_plans VALUES ('original','new-B')")
        db.execute('INSERT INTO '+history+" VALUES ('original')")
    for allowed in (False,True):
        with pytest.raises(adoption.AdoptionBlocked,match=code):
            adoption._verify_business_history(ledger,'new-B',allow_uninitialized=allowed)


def test_other_offer_history_does_not_replace_exact_product_scope(tmp_path):
    ledger=store(tmp_path,TABLES)
    with sqlite3.connect(ledger.path) as db:
        db.execute("INSERT INTO release_plans VALUES ('original-A','other-offer')")
        db.execute("INSERT INTO release_runs VALUES ('original-A')")
    assert adoption._verify_business_history(ledger,'new-B')['empty_business_history_verified'] is True
