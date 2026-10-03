import sqlite3
import pytest
from test_private_local_final_review import _ready
from shared_platform.common_offer_authority_store import CommonAuthorityBlocked


@pytest.mark.parametrize("drift", ["missing", "wrong-definition"])
@pytest.mark.parametrize("action", ["migrate", "prepare"])
def test_private_trigger_drift_blocks_existing_migration_and_business_transaction(tmp_path, drift, action):
    store, grant, _ = _ready(tmp_path)
    with sqlite3.connect(store.authority.store.path) as db:
        db.execute("DROP TRIGGER private_final_candidates_no_update")
        if drift == "wrong-definition":
            db.execute("CREATE TRIGGER private_final_candidates_no_update BEFORE UPDATE ON private_final_candidates BEGIN SELECT 1; END")
    with pytest.raises(CommonAuthorityBlocked, match="PRIVATE_FINAL_SCHEMA"):
        if action == "migrate":
            store.migrate()
        else:
            store.prepare(grant, reservation_id="private-reservation")


def test_schema_literal_case_change_is_not_whitespace_equivalence(tmp_path):
    store, grant, _ = _ready(tmp_path)
    with sqlite3.connect(store.authority.store.path) as db:
        original = db.execute("SELECT sql FROM sqlite_master WHERE name='private_final_decisions'").fetchone()[0]
        assert "'SYNTHETIC_TEST_ONLY'" in original
        triggers = [row[0] for row in db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='private_final_decisions'")]
        db.execute("DROP TABLE private_final_decisions")
        db.execute(original.replace("'SYNTHETIC_TEST_ONLY'", "'synthetic_test_only'"))
        for sql in triggers:
            db.execute(sql)
    with pytest.raises(CommonAuthorityBlocked, match="PRIVATE_FINAL_SCHEMA"):
        store.prepare(grant, reservation_id="private-reservation")
