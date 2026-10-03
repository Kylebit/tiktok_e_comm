"""Real Windows token/owner ACL identity; no provider, decision or DB writes."""
from dataclasses import replace
import ctypes as C
import json
from pathlib import Path
import time

import pytest

from shared_platform import native_windows_actor as actor
from shared_platform import local_operator_session as owner
from shared_platform.common_offer_authority_store import canonical_bytes, CommonAuthorityBlocked
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.r3_native_final_approval import read_native_actor_identity, NativeSoleDecisionConsumer


def _service(tmp_path, *, seconds=3600):
    root = tmp_path/'native-actor'
    assert root.resolve().is_relative_to(tmp_path.resolve())
    config = actor.NativeActorServiceConfig(root, 'Kyle', seconds)
    return actor.bootstrap_service(config)


def test_real_owner_bootstrap_and_consumer_reader_return_verified_identity(tmp_path):
    before = owner.current_windows_owner_sid()
    service = _service(tmp_path)
    grant = service.grant_same_user()
    reader = actor.NativeWindowsActorProfileReader(service, grant)
    result = read_native_actor_identity(reader)
    assert result['identity_verified'] is True
    assert result['owner_sid'] == before == owner.current_windows_owner_sid()
    assert result['logical_profile'] == 'Kyle' and result['trust_mode'] == actor.TRUST
    assert result['mapping_source'] == 'NATIVE_SAME_OWNER_STARTUP'
    assert result['mapping_digest'] == actor.digest((service.config.root/'native-owner.json').read_bytes())
    assert result['session_id'] == grant.session_id
    assert owner.verify_owner_only(service.config.root, before)['protected_dacl'] is True
    assert not list(tmp_path.rglob('*.db'))


def test_automatic_owner_file_handoff_and_same_owner_reconnect_need_no_approval(tmp_path):
    service = _service(tmp_path)
    handoff = service.bootstrap_to_owner_file(filename='startup-session.json')
    assert handoff == service.config.root/'startup-session.json'
    owner.verify_owner_only(handoff, owner.current_windows_owner_sid(), protected=False)
    first = service.grant_from_owner_file(filename='startup-session.json')
    old_identity = actor.NativeWindowsActorProfileReader(service, first).read_verified()
    restarted = actor.bootstrap_service(service.config)
    with pytest.raises(ApprovalBlocked, match='NATIVE_ACTOR_SESSION_INVALID_OR_EXPIRED'):
        actor.NativeWindowsActorProfileReader(restarted, first).read_verified()
    # A local trusted launcher reissues automatically; no human identity step.
    restarted.bootstrap_to_owner_file(filename='reconnected-session.json')
    fresh = restarted.grant_from_owner_file(filename='reconnected-session.json')
    current = read_native_actor_identity(actor.NativeWindowsActorProfileReader(restarted, fresh))
    assert current['owner_sid'] == old_identity['owner_sid']
    assert current['instance_id'] == old_identity['instance_id']
    assert current['session_id'] != old_identity['session_id']
    assert not list(tmp_path.rglob('*.db'))


def test_private_bootstrap_and_request_actor_fields_are_not_native_carriers(tmp_path):
    service = _service(tmp_path)
    valid = service.grant_same_user()
    private = owner.PrivateOperatorGrant(valid.session_id, valid.capability, valid.csrf)
    for forged in (private, {'approved_by':'Kyle','owner_sid':owner.current_windows_owner_sid()},
                   {'session_id':valid.session_id,'capability':valid.capability,'csrf':valid.csrf}):
        with pytest.raises(ApprovalBlocked, match='NATIVE_ACTOR_TRUSTED_LOCAL_CARRIER_REQUIRED'):
            actor.NativeWindowsActorProfileReader(service, forged).read_verified()
    with pytest.raises(ApprovalBlocked, match='NATIVE_ACTOR_SERVICE_READER_REQUIRED'):
        read_native_actor_identity({'approved_by':'Kyle'})


def test_correct_session_expiry_is_rejected_and_forged_expiry_stays_unauthenticated(tmp_path):
    service = _service(tmp_path)
    grant = service.grant_same_user()
    service._sessions[grant.session_id] = replace(service._sessions[grant.session_id],
                                                expires_at_epoch=int(time.time())-1)
    with pytest.raises(ApprovalBlocked, match='^NATIVE_ACTOR_SESSION_EXPIRED$'):
        actor.NativeWindowsActorProfileReader(service, grant).read_verified()
    forged = replace(grant, capability='different-secret')
    with pytest.raises(ApprovalBlocked, match='^NATIVE_ACTOR_SESSION_INVALID_OR_EXPIRED$'):
        actor.NativeWindowsActorProfileReader(service, forged).read_verified()
    fresh = service.grant_same_user()
    assert actor.NativeWindowsActorProfileReader(service, fresh).read_verified()['identity_verified'] is True


def test_same_acl_profile_byte_tamper_cannot_change_actor_mapping(tmp_path):
    service = _service(tmp_path)
    reader = actor.NativeWindowsActorProfileReader(service, service.grant_same_user())
    path = service.config.root/'native-owner.json'
    value = json.loads(path.read_bytes())
    value['instance_id'] = 'a'*32
    if value['instance_id'] == service._profile['instance_id']:
        value['instance_id'] = 'b'*32
    path.write_bytes(canonical_bytes(value))
    owner.verify_owner_only(path, owner.current_windows_owner_sid(), protected=False)
    with pytest.raises(ApprovalBlocked, match='NATIVE_ACTOR_PROFILE_INVALID_OR_CHANGED'):
        reader.read_verified()


def test_existing_private_owner_directory_is_not_adopted_or_rewritten(tmp_path):
    root = tmp_path/'private-owner'
    result = owner.create_owner_only_directory(root)
    marker = root/'owner.json'
    raw = canonical_bytes({'schema_version':'private-local-owner/v1',
        'owner_sid':result['owner_sid'],'evidence_kind':'SYNTHETIC_TEST_ONLY','instance_id':'owned-private'})
    marker.write_bytes(raw)
    with pytest.raises(ApprovalBlocked, match='NATIVE_ACTOR_OWNER_OR_PROFILE_UNVERIFIED'):
        actor.bootstrap_service(actor.NativeActorServiceConfig(root, 'Kyle'))
    assert marker.read_bytes() == raw and sorted(p.name for p in root.iterdir()) == ['owner.json']


def test_changed_effective_owner_fails_real_owner_acl_read(tmp_path, monkeypatch):
    service = _service(tmp_path)
    reader = actor.NativeWindowsActorProfileReader(service, service.grant_same_user())
    sid = owner.current_windows_owner_sid()
    # SID mismatch is injected; the subsequent native file ACL query is real.
    monkeypatch.setattr(owner, 'current_windows_owner_sid', lambda: sid+'-1234')
    with pytest.raises(ApprovalBlocked, match='NATIVE_ACTOR_OWNER_OR_PROFILE_UNVERIFIED'):
        reader.read_verified()


def test_real_extra_acl_ace_prevents_profile_from_representing_native_owner(tmp_path):
    service = _service(tmp_path)
    reader = actor.NativeWindowsActorProfileReader(service, service.grant_same_user())
    path = service.config.root/'native-owner.json'
    sid = owner.current_windows_owner_sid()
    a,k = owner._windows()
    def set_dacl(text):
        sd,acl = C.c_void_p(),C.c_void_p()
        present,defaulted = owner.W.BOOL(),owner.W.BOOL()
        assert a.ConvertStringSecurityDescriptorToSecurityDescriptorW(text,1,C.byref(sd),None)
        try:
            assert a.GetSecurityDescriptorDacl(sd,C.byref(present),C.byref(acl),C.byref(defaulted)) and present
            assert a.SetNamedSecurityInfoW(str(path),1,0x80000004,None,None,acl,None)==0
        finally:k.LocalFree(sd)
    try:
        set_dacl(f'D:P(A;;FA;;;{sid})(A;;GR;;;WD)')
        with pytest.raises(ApprovalBlocked, match='NATIVE_ACTOR_OWNER_OR_PROFILE_UNVERIFIED'):
            reader.read_verified()
    finally:set_dacl(f'D:P(A;;FA;;;{sid})')
    assert reader.read_verified()['owner_sid'] == sid


def test_carrier_bytes_and_unsafe_paths_never_accept_request_identity(tmp_path):
    service = _service(tmp_path)
    file = service.bootstrap_to_owner_file(filename='carrier.json')
    value = json.loads(file.read_bytes())
    value['approved_by'] = 'Kyle'
    file.write_bytes(canonical_bytes(value))
    with pytest.raises(ApprovalBlocked, match='NATIVE_ACTOR_HANDOFF_INVALID'):
        service.grant_from_owner_file(filename='carrier.json')
    for name in ('../outside.json','C:/outside.json','owner.json'):
        with pytest.raises(CommonAuthorityBlocked, match='LOCAL_HANDOFF_NAME_INVALID'):
            service.bootstrap_to_owner_file(filename=name)
    assert not (tmp_path/'outside.json').exists()


def test_verified_owner_does_not_bypass_existing_candidate_common_or_budget_guard(tmp_path):
    service = _service(tmp_path)
    grant = service.grant_same_user()
    reader = actor.NativeWindowsActorProfileReader(service, grant)
    assert read_native_actor_identity(reader)['identity_verified'] is True
    with pytest.raises(ApprovalBlocked, match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
        NativeSoleDecisionConsumer(None, actor_reader=reader).decide(grant,
            nonce='owned-not-a-decision', review_digest='owned-no-candidate')
    assert not list(tmp_path.rglob('*.db'))
