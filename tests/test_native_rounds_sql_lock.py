"""Real current-round lineage must not retain a reader while nested readers run."""
import sqlite3
from test_round1_workspace_freeze import live
from test_native_sole_final_decision import _completed
from shared_platform.native_sole_final_service import check_current_rounds


def test_current_round_loader_allows_independent_writer_commit(live,monkeypatch,tmp_path):
    service,market,*_=_completed(live,monkeypatch,tmp_path)
    from shared_platform import workbench_publication_native as native
    original=native.load_service_r2_documents;observed=[]
    def load(*args,**kwargs):
        with sqlite3.connect(service.store.path,timeout=0) as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('CREATE TABLE independent_round_probe (value INTEGER)')
            try:writer.commit()
            except sqlite3.OperationalError:
                observed.append('locked');writer.rollback()
            else:observed.append('committed')
        return original(*args,**kwargs)
    monkeypatch.setattr(native,'load_service_r2_documents',load)
    from modules.products import server
    check_current_rounds(service.store,market['plan_id'],operations=server._NATIVE_FINAL_SERVICE._operations)
    assert observed==['committed']
