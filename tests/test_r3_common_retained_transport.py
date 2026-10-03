"""N1 source-consumer contract: synthetic transport and owned SQLite only.

These records exercise the real retained packet/store path, not authenticated
account authority or a historical budget. No private authority marker is used.
"""
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.r3_frozen_review_producer import DomainReviewBlocked


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


@contextmanager
def snapshot(store):
    db = sqlite3.connect(f'file:{store.path.as_posix()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def retained_rows(db):
    return {r[0]: [tuple(v) for v in db.execute('SELECT * FROM "' + r[0] + '"')]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


@pytest.fixture
def native_history(tmp_path, monkeypatch):
    from owned_common_review_fixtures import native_completed_marketplace
    with native_completed_marketplace(tmp_path, monkeypatch, pretty_wire=True) as (_, _, store, view, io):
        run_id = view['common']['run']['run_id']
        with snapshot(store) as db:
            row = db.execute('SELECT evidence_json FROM release_target_readbacks WHERE run_id=? AND target_label=?',
                             (run_id, 'miaoshou:COMMON')).fetchone()
            packet = json.loads(row['evidence_json'])['native_common_observation']
        wire = base64.b64decode(packet['wire_base64'])
        assert io.mutations == 1 and wire == io.responses[-1]
        assert wire.startswith(b'{\n')  # Preserve noncanonical actual signed READ bytes.
        yield store, view['marketplace']['plan']['plan_id'], wire


def common_record(facts):
    return next(r for r in facts.records if r.table == 'release_target_readbacks'
                and r.identity[0] == facts.graph.common_run_id)


def extra_retained_native_row(store, market_id):
    """Keep corruption off the current graph so outer graph digests still pass."""
    with snapshot(store) as db:
        facts = NativeCommonSourceReader(store).read_source_facts(db, market_id)
        original = json.loads(common_record(facts).evidence_bytes)
    offer = facts.graph.offer_id
    extra = store.create_plan({'plan_id': 'n1-extra-retained-common', 'product_id': offer,
        'seller_sku': '1099', 'product_package_id': 'n1-historical-product',
        'content_package_id': 'n1-historical-content', 'product_revision': 1,
        'targets': ['miaoshou:COMMON']})
    store.approve_plan(extra['plan_id'], user_approved=True, approved_by='Kyle',
                       confirmation_token=extra['confirmation_token'])
    run = store.start_run(extra['plan_id'])
    store.begin_target(run['run_id'], 'miaoshou:COMMON')
    original.pop('stored_common_lineage')
    store.record_target_success(run['run_id'], 'miaoshou:COMMON', external_id=offer,
                                readback_evidence=original)
    evidence = next(t for t in store.get_run(run['run_id'])['targets']
                    if t['target_label'] == 'miaoshou:COMMON')['readback']['evidence']
    return run['run_id'], evidence


def replace_extra_evidence(store, run_id, evidence):
    # Adversarial owned-db setup recomputes the outer digest. Inner packet and
    # immutable native lineage still need validation by the source consumer.
    raw = canonical(evidence)
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE release_target_readbacks SET evidence_json=?,evidence_digest=? '
                   'WHERE run_id=? AND target_label=?',
                   (raw.decode('utf-8'), hashlib.sha256(raw).hexdigest(),
                    run_id, 'miaoshou:COMMON'))


def test_retained_native_packet_reopens_as_typed_transport_and_comparison_without_authority(native_history):
    from modules.miaoshou.client import CommonDetailObservation
    from shared_platform.release_store import ReleaseStore
    store, market_id, wire = native_history
    with snapshot(store) as db:
        before = retained_rows(db)
        facts = NativeCommonSourceReader(store).read_source_facts(db, market_id)
        record = common_record(facts)
        evidence = json.loads(record.evidence_bytes)
        observed = getattr(record, 'transport_observation', None)
        assert isinstance(observed, CommonDetailObservation), 'retained native packet must be exposed as typed transport facts'
        assert observed.wire_bytes == wire == observed.business_bytes
        assert observed.receipt() == {k: v for k, v in evidence['native_common_observation'].items()
                                     if k != 'comparison_sha256'}
        assert getattr(record, 'comparison_bytes', None) == canonical({k: v for k, v in evidence.items()
            if k not in {'native_common_observation', 'stored_common_lineage'}})
        assert getattr(record, 'lineage_bytes', None) == canonical(evidence['stored_common_lineage'])
        assert retained_rows(db) == before
    reopened = ReleaseStore(store.path)
    with snapshot(reopened) as db:
        again = NativeCommonSourceReader(reopened).read_source_facts(db, market_id)
        assert common_record(again) == record
        assert again.official_provenance == again.budget_status == 'UNKNOWN'
        assert again.execution_authority is False and record.official_response_bytes is None
        with pytest.raises(DomainReviewBlocked, match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
            NativeCommonSourceReader(reopened).read_verified(db, 'unadmitted', market_id)


def test_native_packet_tamper_is_rejected_even_with_recomputed_outer_record_digest(native_history):
    store, market_id, wire = native_history
    run_id, evidence = extra_retained_native_row(store, market_id)
    evidence['native_common_observation']['wire_base64'] = base64.b64encode(wire + b' ').decode()
    replace_extra_evidence(store, run_id, evidence)
    with snapshot(store) as db:
        before = retained_rows(db)
        with pytest.raises(DomainReviewBlocked, match='COMMON_SOURCE_NATIVE_OBSERVATION_INVALID'):
            NativeCommonSourceReader(store).read_source_facts(db, market_id)
        assert retained_rows(db) == before


def test_native_packet_cannot_cross_run_or_offer_in_retained_history(native_history):
    store, market_id, wire = native_history
    run_id, original = extra_retained_native_row(store, market_id)
    for wrong in ('run', 'offer'):
        evidence = deepcopy(original)
        if wrong == 'run':
            evidence['stored_common_lineage']['run_id'] = 'different-retained-run'
        else:
            evidence['stored_common_lineage']['offer_id'] = '1'
            evidence['native_common_observation']['detail_id'] = '1'
        replace_extra_evidence(store, run_id, evidence)
        with snapshot(store) as db:
            before = retained_rows(db)
            with pytest.raises(DomainReviewBlocked, match='COMMON_SOURCE_NATIVE_OBSERVATION_INVALID'):
                NativeCommonSourceReader(store).read_source_facts(db, market_id)
            assert retained_rows(db) == before


def test_normalized_history_remains_unobserved_and_cannot_authorize_native_review(tmp_path, monkeypatch):
    from modules.products import server
    from test_b4b_publication_preview import reviewed_marketplace
    monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY', None)
    documents, dashboard, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    store.create_plan(market['plan']['payload'])
    with snapshot(store) as db:
        before = retained_rows(db)
        facts = NativeCommonSourceReader(store).read_source_facts(db, market['plan']['plan_id'])
        record = common_record(facts)
        assert 'native_common_observation' not in json.loads(record.evidence_bytes)
        assert record.official_response_bytes is None
        assert all(getattr(record, field, None) is None for field in
                   ('transport_observation', 'comparison_bytes', 'lineage_bytes'))
        assert facts.source_coverage == 'LOCAL_RETAINED_ONLY'
        assert facts.official_provenance == facts.budget_status == 'UNKNOWN'
        assert facts.execution_authority is False and not hasattr(facts, 'budget_digest')
        with pytest.raises(DomainReviewBlocked, match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
            NativeCommonSourceReader(store).read_verified(db, 'unadmitted', market['plan']['plan_id'])
        assert retained_rows(db) == before
