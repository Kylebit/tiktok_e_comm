"""Corrupted private restoration records do not become native authority.

Fixture corruption is explicit: immutable triggers are restored byte-for-byte
before the actual read, which remains read-only. No production storage is used.
"""
import json
import sqlite3
import pytest

from shared_platform.common_offer_authority_store import canonical_bytes, digest, CommonAuthorityBlocked
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.private_final_decision_store import _frozen
from shared_platform.r3_native_final_approval import NativeSoleDecisionConsumer, read_native_approval_binding
from test_r3_native_final_approval import prepared, table_rows


@pytest.mark.parametrize('damage', ['missing', 'json', 'acl'])
def test_actor_storage_failure_is_stable_and_consumes_nothing(tmp_path, monkeypatch, damage):
    from shared_platform import r3_native_final_approval as native
    decisions, actor, grant, review, data, fake = prepared(tmp_path, monkeypatch)
    before = table_rows(decisions, ['private_final_nonces', 'private_final_decisions',
        'private_domain_final_approvals', 'release_approvals', 'approved_publication_snapshots'])
    if damage == 'missing':
        actor.path.unlink()
    elif damage == 'json':
        actor.path.write_bytes(b'{invalid fixture json')
    else:
        def denied(*args, **kwargs):
            raise PermissionError('fixture internal ACL details must not escape')
        monkeypatch.setattr(native, 'verify_owner_only', denied)
    with pytest.raises(ApprovalBlocked, match='^NATIVE_ACTOR_PROFILE_BINDING_INVALID$') as caught:
        NativeSoleDecisionConsumer(decisions, actor_reader=actor).decide(
            grant, nonce=review['nonce'], review_digest=review['review_digest'])
    assert caught.value.__cause__ is None
    assert table_rows(decisions, before) == before and fake.mutations == 1


def corrupt_private_restoration(store, table, sql, args):
    # Model damaged restored bytes, not a supported mutation API. The final
    # schema retains every original immutable guard for the actual consumer.
    with sqlite3.connect(store.path) as db:
        triggers = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
        for name, _ in triggers:
            assert name.replace('_', '').isalnum()
            db.execute('DROP TRIGGER '+name)
        db.execute(sql, args)
        for _, statement in triggers:
            db.execute(statement)


@pytest.mark.parametrize('damage', ['plan_offer', 'plan_targets', 'approval_review',
    'decision_review', 'envelope_decision', 'snapshot_offer'])
def test_actual_cross_table_damage_is_rejected_readonly(tmp_path, monkeypatch, damage):
    decisions, actor, grant, review, data, fake = prepared(tmp_path, monkeypatch)
    NativeSoleDecisionConsumer(decisions, actor_reader=actor).decide(
        grant, nonce=review['nonce'], review_digest=review['review_digest'])
    key = review['review_digest']
    with decisions._transaction(readonly=True) as db:
        frozen = _frozen(db.execute('SELECT frozen_json FROM private_final_candidates WHERE review_digest=?', (key,)).fetchone()[0].encode())
        decision_id = db.execute('SELECT decision_id FROM private_final_decisions WHERE review_digest=?', (key,)).fetchone()[0]
        encoded = db.execute('SELECT domain_binding_json FROM private_domain_final_approvals WHERE review_digest=?', (key,)).fetchone()[0]
    changes = {
        'plan_offer': ('release_plans', 'UPDATE release_plans SET product_id=? WHERE plan_id=?', ('wrong-offer', data['plan_id'])),
        'plan_targets': ('release_plans', 'UPDATE release_plans SET target_labels_json=? WHERE plan_id=?', (json.dumps(list(reversed(frozen.targets))), data['plan_id'])),
        'approval_review': ('private_domain_final_approvals', 'UPDATE private_domain_final_approvals SET review_digest=? WHERE review_digest=?', ('wrong-review', key)),
        'decision_review': ('private_final_decisions', 'UPDATE private_final_decisions SET review_digest=? WHERE review_digest=?', ('wrong-review', key)),
        'snapshot_offer': ('approved_publication_snapshots', 'UPDATE approved_publication_snapshots SET offer_id=? WHERE plan_id=?', ('wrong-offer', data['plan_id'])),
    }
    if damage == 'envelope_decision':
        value = json.loads(encoded); value['decision_id'] = 'wrong-decision'
        raw = canonical_bytes(value)
        changes[damage] = ('private_domain_final_approvals', 'UPDATE private_domain_final_approvals SET domain_binding_json=?,domain_binding_sha256=? WHERE review_digest=?', (raw.decode(), digest(raw), key))
    corrupt_private_restoration(decisions.authority.store, *changes[damage])
    before = table_rows(decisions, ['private_final_candidates', 'private_final_decisions',
        'private_domain_final_approvals', 'release_plans', 'release_approvals', 'approved_publication_snapshots', 'private_final_nonces'])
    with decisions._transaction(readonly=True) as db:
        approval = db.execute('SELECT * FROM private_domain_final_approvals WHERE decision_id=?', (decision_id,)).fetchone()
        decision = db.execute('SELECT * FROM private_final_decisions WHERE decision_id=?', (decision_id,)).fetchone()
        with pytest.raises(ApprovalBlocked, match='^NATIVE_APPROVAL_'):
            read_native_approval_binding(db, approval, decision, frozen)
    assert table_rows(decisions, before) == before and fake.mutations == 1


@pytest.mark.parametrize('raw', ['{broken-json', '[]', 'null'])
def test_corrupt_stored_binding_has_stable_private_rejection(tmp_path, monkeypatch, raw):
    decisions, actor, grant, review, data, fake = prepared(tmp_path, monkeypatch)
    NativeSoleDecisionConsumer(decisions, actor_reader=actor).decide(
        grant, nonce=review['nonce'], review_digest=review['review_digest'])
    corrupt_private_restoration(decisions.authority.store, 'private_domain_final_approvals',
        'UPDATE private_domain_final_approvals SET domain_binding_json=?,domain_binding_sha256=?',
        (raw, digest(raw.encode())))
    before = table_rows(decisions, ['private_final_nonces', 'private_domain_final_approvals', 'release_approvals'])
    with pytest.raises(CommonAuthorityBlocked, match='^PRIVATE_DOMAIN_APPROVAL_INVALID$'):
        decisions.approval(grant, review_digest=review['review_digest'])
    assert table_rows(decisions, before) == before and fake.mutations == 1


def test_missing_decision_is_stable_binding_rejection(tmp_path, monkeypatch):
    decisions, actor, grant, review, data, fake = prepared(tmp_path, monkeypatch)
    NativeSoleDecisionConsumer(decisions, actor_reader=actor).decide(
        grant, nonce=review['nonce'], review_digest=review['review_digest'])
    with decisions._transaction(readonly=True) as db:
        approval = db.execute('SELECT * FROM private_domain_final_approvals').fetchone()
        frozen = _frozen(db.execute('SELECT frozen_json FROM private_final_candidates').fetchone()[0].encode())
        with pytest.raises(ApprovalBlocked, match='^NATIVE_APPROVAL_BYTES_INVALID$'):
            read_native_approval_binding(db, approval, None, frozen)
    assert fake.mutations == 1
