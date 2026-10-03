"""Existing real policy through owned startup configuration, no provider calls."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json

import pytest
from modules.products import server
from shared_platform import local_operator_session
from shared_platform import publication_common_standing_policy as policy
from shared_platform import publication_common_write_admission as admission
from shared_platform.publication_runtime_config import capture_startup_config
from test_common_admission_retained_source import prepared_common
from test_b4b_common_stage import context

SOURCE = Path(__file__).resolve().parents[1] / 'config/product_publication_autopilot_policy.json'


def installed(tmp_path, monkeypatch, raw=None):
    path = tmp_path / 'config/product_publication_autopilot_policy.json'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(SOURCE.read_bytes() if raw is None else raw)
    monkeypatch.setattr(server, '_COMMON_STANDING_POLICY_READER', None)
    config = capture_startup_config(root=tmp_path, environ={})
    reader = server._install_service_common_standing_policy(config)
    assert type(reader) is policy.NativeCommonStandingPolicyReader
    assert server._service_common_standing_policy_reader() is reader
    return path, reader


def test_existing_native_policy_retained_plan_reads_cap_without_authorizing_write(tmp_path, monkeypatch):
    store, plan, _ = prepared_common(tmp_path, monkeypatch)
    path, reader = installed(tmp_path, monkeypatch)
    before = store.path.read_bytes()
    result = admission.inspect_common_write_admission(plan, store=store, policy_reader=reader)
    facts = result['standing_policy_facts']
    assert facts['status'] == 'KNOWN_LOCAL_USER_INTENT'
    assert facts['raw_digest'] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert facts['maximum_confirmed_writes'] == 1
    assert facts['confirmed_write_count'] == facts['account_authority'] == 'UNKNOWN'
    assert facts['coverage_authority'] == facts['schema_installation'] == 'UNKNOWN'
    assert facts['execution_authority'] is False
    assert result['source_facts']['status'] == 'RETAINED_IDENTITY_VERIFIED'
    assert result['status'] == 'BLOCKED' and result['execution_authority'] is False
    assert result['final_review_available'] is False and result['external_writes_performed'] == []
    assert 'COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN' not in result['blockers']
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in result['blockers']
    assert result['authority_facts']['account_authority'] == 'UNKNOWN'
    assert store.path.read_bytes() == before and path.read_bytes() == SOURCE.read_bytes()


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'missing-common', 'bool-cap', 'changed-cap', 'write-class', 'duplicate-key', 'missing-review-contract', 'non-standing-kind', 'pairs-root', 'missing-policy-id'])
def test_native_policy_missing_or_invalid_does_not_supply_standing_authority(tmp_path, monkeypatch, damage):
    path, reader = installed(tmp_path, monkeypatch)
    value = json.loads(path.read_bytes())
    if damage == 'missing':
        path.unlink()
    elif damage == 'malformed':
        path.write_bytes(b'{')
    elif damage == 'duplicate-key':
        path.write_bytes(path.read_bytes().replace(b'"miaoshou": {', b'"miaoshou": {}, "miaoshou": {'))
    elif damage == 'pairs-root':
        path.write_text(json.dumps(list(value.items())), encoding='utf-8')
    else:
        if damage == 'missing-common':
            value.pop('miaoshou')
        elif damage == 'bool-cap':
            value['miaoshou']['maximum_confirmed_writes_per_product'] = True
        elif damage == 'changed-cap':
            value['miaoshou']['maximum_confirmed_writes_per_product'] = 2
        elif damage == 'missing-review-contract':
            value['policy_id'] = 'different-otherwise-valid-policy'
            value.pop('review_contract')
        elif damage == 'non-standing-kind':
            value['authority']['kind'] = 'explicit_conversation_approval'
        elif damage == 'missing-policy-id':
            value.pop('policy_id')
        else:
            value['miaoshou']['marketplace_draft_or_publish_not_authorized'] = False
        path.write_text(json.dumps(value), encoding='utf-8')
    result = admission.inspect_common_write_admission(None, policy_reader=reader)
    assert result['standing_policy_facts']['status'] == 'UNKNOWN'
    assert result['standing_policy_facts']['maximum_confirmed_writes'] == 'UNKNOWN'
    assert 'COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN' in result['blockers']
    assert result['execution_authority'] is False and result['status'] == 'BLOCKED'
    assert not (tmp_path / 'data').exists()


def test_caller_dict_and_changed_effective_windows_owner_cannot_supply_policy(tmp_path, monkeypatch):
    _, reader = installed(tmp_path, monkeypatch)
    dictionary = {'status': 'KNOWN_LOCAL_USER_INTENT', 'maximum_confirmed_writes': 1,
                  'verified': True, 'policy': json.loads(SOURCE.read_bytes())}
    assert admission.inspect_common_write_admission(None, policy_reader=dictionary)[
        'standing_policy_facts']['reason'] == 'COMMON_POLICY_SERVICE_READER_REQUIRED'
    monkeypatch.setattr(local_operator_session, 'current_windows_owner_sid', lambda: 'different-effective-owner')
    result = admission.inspect_common_write_admission(None, policy_reader=reader)
    assert result['standing_policy_facts']['status'] == 'UNKNOWN'
    assert result['standing_policy_facts']['reason'] == 'COMMON_POLICY_WINDOWS_OWNER_CHANGED'
    assert result['execution_authority'] is False
    monkeypatch.setattr(local_operator_session, 'current_windows_owner_sid', lambda: '')
    with pytest.raises(ValueError, match='COMMON_POLICY_WINDOWS_OWNER_UNAVAILABLE'):
        policy.NativeCommonStandingPolicyReader(capture_startup_config(root=tmp_path, environ={}))
    assert server._install_service_common_standing_policy(
        capture_startup_config(root=tmp_path, environ={})) is None
    assert admission.inspect_common_write_admission(None, policy_reader=
        server._service_common_standing_policy_reader())['execution_authority'] is False


def test_actual_http_entry_consumes_only_service_policy_and_keeps_provider_closed(tmp_path, monkeypatch):
    from test_b4b_common_stage import CommonTransport, approve
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch, synthetic_technical_authority=False)
    exact = approve(request)
    path, _ = installed(tmp_path, monkeypatch)
    before = store.path.read_bytes()
    code, result = server._prepare_miaoshou_release({**exact, 'confirm_miaoshou_write': True})
    assert code == 409 and result['error'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED'
    assert result['standing_policy_facts']['status'] == 'KNOWN_LOCAL_USER_INTENT'
    assert result['standing_policy_facts']['raw_digest'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['authority_facts']['standing_policy_authority'] == 'KNOWN_LOCAL_USER_INTENT'
    assert result['execution_authority'] is False and io.mutations == 0
    assert store.path.read_bytes() == before
