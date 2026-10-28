"""Hermetic source-to-observation contracts; no account, service or shared DB."""
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import subprocess
import sys

import pytest

from shared_platform import workbench_source_feedback as feedback
from shared_platform.operations_http import handle


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def chain(tmp_path):
    now = datetime.now(timezone.utc)
    stamp = (now - timedelta(minutes=5)).isoformat()
    old = (now - timedelta(minutes=10)).isoformat()
    original = write(tmp_path / 'original-observation.json', {'accepted': True})
    scope = write(tmp_path / 'scope.json', {'schema': 'orbit-work-scope/v1',
        'sessions': [{'session_id': session} for session in feedback.WORKS.values()]})
    items = [{'work_id': work, 'source_session_id': session, 'kind': 'engineering',
        'handover': 'accepted', 'stage': 'Before receipt', 'observed_at': old,
        'sources': [original], 'owner_label': 'original owner', 'operations_task_id': None}
        for work, session in feedback.WORKS.items()]
    items.append({'work_id': 'other-work', 'source_session_id': 'other-session',
        'handover': 'pending', 'stage': 'Keep original', 'observed_at': old, 'sources': [original]})
    manifest = tmp_path / 'observations.json'
    write(manifest, {'schema': 'orbit-work-observations/v1', 'scope_ref': scope, 'items': items})
    engineer = tmp_path / 'engineering' / feedback.SOURCE_FILENAMES['engineering']
    supply = tmp_path / 'supply' / feedback.SOURCE_FILENAMES['supply']
    write(engineer, {'schema_version': 'actual-independent-native-receiver-continuation/v1',
        'workspace_offer_id': '4039931504', 'supplier_source_offer_id': '1050277205528',
        'status': 'ORIGINAL_INDEPENDENT_PRIVATE_INTAKE_AND_OWNER_CHECKPOINT_VERIFIED',
        'private_task_not_formal': True, 'actual_formal_business_registration': False,
        'shared_business_writes': 0, 'as_of_utc': stamp, 'source_head': 'a' * 40,
        'private_task_id': 'SYNTHETIC-PRIVATE-ONLY'})
    write(supply, {'schema': 'supply-completion-handoff/v2', 'recordedAt': stamp,
        'outcome': 'AUTHORIZED_SHOPEE_REFRESH_AND_EIGHT_STORE_READS_COMPLETE__ORIGINAL_ROUTE_RETAINED__FULL_FRESH_ADMISSION_PENDING',
        'unchanged': {'businessWrites': 0}, 'formalService': {'candidateAdopted': False}})
    profile = tmp_path / 'feedback-profile.json'
    write(profile, {'schema': 'orbit-source-feedback-profile/v1', 'sources': {
        'product_business': None, 'engineering': {'path': str(engineer)}, 'supply': {'path': str(supply)}}})
    return SimpleNamespace(root=tmp_path, manifest=manifest, profile=profile,
        sources={'engineering': engineer, 'supply': supply},
        state=tmp_path / 'state', health=tmp_path / 'health.json', stamp=stamp)


def tick(c):
    return feedback.tick(c.manifest, c.health, c.state, c.sources)


def statuses(result):
    return {row['source']: row['status'] for row in result['outcomes']}


def test_updates_exact_three_records_preserving_authority_and_other_work(chain):
    before = json.loads(chain.manifest.read_bytes())
    originals = {kind: path.read_bytes() for kind, path in chain.sources.items()}
    result = tick(chain)
    after = json.loads(chain.manifest.read_bytes())
    assert statuses(result) == {'product_business': 'not_configured', 'engineering': 'updated', 'supply': 'updated'}
    assert result['missing_source_lanes'] == ['product_business']
    assert result['business_execution_heartbeat'] is False
    assert result['source_count'] == 2
    assert len(before['items']) == len(after['items'])
    for old, new in zip(before['items'], after['items']):
        for key in ('work_id', 'source_session_id', 'handover', 'owner_label', 'operations_task_id'):
            assert new.get(key) == old.get(key)
        if old['work_id'] not in feedback.WORKS:
            assert new == old
        else:
            assert new['observed_at'] == chain.stamp
            assert new['user_decision'] is None
            assert new['closed'] is False
    assert after['items'][0]['stage'] == '准备未完成；终审候选未形成'
    assert originals == {kind: path.read_bytes() for kind, path in chain.sources.items()}


def test_same_bytes_and_mtime_never_renew_business_evidence(chain):
    tick(chain)
    before = chain.manifest.read_bytes()
    for path in chain.sources.values():
        path.touch()
    assert statuses(tick(chain))['engineering'] == 'unchanged'
    assert chain.manifest.read_bytes() == before


def test_changed_immutable_copy_is_disclosed_without_renewing_observation(chain):
    tick(chain)
    before = chain.manifest.read_bytes()
    next((chain.state / 'originals').glob('engineering-*.json')).write_text('{}')
    assert statuses(tick(chain))['engineering'] == 'needs_verification'
    assert chain.manifest.read_bytes() == before


@pytest.mark.parametrize('change', ['wrong_product', 'formal_registration', 'unknown_schema', 'future_clock', 'non_object'])
def test_unsupported_product_evidence_preserves_record_and_reports_gap(chain, change):
    tick(chain)
    before = chain.manifest.read_bytes()
    path = chain.sources['engineering']
    data = json.loads(path.read_bytes())
    if change == 'wrong_product': data['workspace_offer_id'] = 'other-product'
    if change == 'formal_registration': data['actual_formal_business_registration'] = True
    if change == 'unknown_schema': data['schema_version'] = 'unknown'
    if change == 'future_clock': data['as_of_utc'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    if change == 'non_object': data = []
    write(path, data)
    assert statuses(tick(chain))['engineering'] == 'needs_verification'
    assert chain.manifest.read_bytes() == before


def test_older_receipt_does_not_replace_newer_business_record(chain):
    data = json.loads(chain.manifest.read_bytes())
    data['items'][0]['observed_at'] = datetime.now(timezone.utc).isoformat()
    data['items'][0]['stage'] = 'Newer original business receipt'
    write(chain.manifest, data)
    tick(chain)
    assert json.loads(chain.manifest.read_bytes())['items'][0] == data['items'][0]


def test_existing_manifest_writer_is_preserved(chain):
    before = chain.manifest.read_bytes()
    lock = chain.manifest.with_name(chain.manifest.name + '.update.lock')
    lock.write_text('other-writer')
    result = tick(chain)
    assert statuses(result)['engineering'] == 'needs_verification'
    assert statuses(result)['supply'] == 'needs_verification'
    assert chain.manifest.read_bytes() == before
    assert lock.read_text() == 'other-writer'


def test_source_session_conflict_never_rebinds_original_task(chain):
    data = json.loads(chain.manifest.read_bytes())
    data['items'][0]['source_session_id'] = 'other-source-session'
    write(chain.manifest, data)
    before = chain.manifest.read_bytes()
    assert statuses(tick(chain))['engineering'] == 'needs_verification'
    after = json.loads(chain.manifest.read_bytes())
    assert after['items'][0] == data['items'][0]
    assert before != chain.manifest.read_bytes()  # independent supply receipt may advance


def test_profile_does_not_guess_missing_business_source_or_read_settings(chain):
    assert feedback.load_profile(chain.profile) == chain.sources
    write(chain.profile, {'schema': 'orbit-source-feedback-profile/v1', 'sources': {
        'engineering': {'path': str(chain.root / 'settings.json')}}})
    with pytest.raises(ValueError, match='filename'):
        feedback.load_profile(chain.profile)
    write(chain.profile, {'schema': 'orbit-source-feedback-profile/v1', 'sources': {
        'product_business': {'path': str(chain.sources['engineering'])}}})
    with pytest.raises(ValueError, match='business receipt'):
        feedback.load_profile(chain.profile)


def test_missing_producer_reports_gap_without_fabricated_update(chain):
    chain.sources['engineering'].unlink()
    result = tick(chain)
    assert statuses(result)['engineering'] == 'needs_verification'
    assert json.loads(chain.manifest.read_bytes())['items'][0]['stage'] == 'Before receipt'


@pytest.mark.parametrize('formal', [False, True])
def test_original_supply_delivery_updates_candidate_stage_only(chain, formal):
    receipt = chain.root / 'supply' / 'DELIVERY_RECEIPT.json'
    write(receipt, {'schema': 'retained-original-supply-delivery/v1',
        'state': 'DELIVERED_PRIVATE_ORIGINAL_HANDLER_CANDIDATE_NOT_FORMAL',
        'sourceHead': 'b' * 40, 'recordedAt': chain.stamp,
        'formalActivation': formal, 'seayaAtomicSnapshot': False,
        'exportRows': 47, 'mappingExceptionCount': 1, 'mappingExceptionCountry': 'MY',
        'seayaGlobalInventoryPages': [100, 8], 'seayaGlobalInboundPages': [38],
        **{key: 0 for key in ('businessWrites', 'databaseWrites', 'procurementActions',
                             'stockActions', 'authWrites', 'networkProviderReads')}})
    chain.sources['supply'] = receipt
    result = tick(chain)
    item = json.loads(chain.manifest.read_bytes())['items'][2]
    if formal:
        assert statuses(result)['supply'] == 'needs_verification'
        assert item['stage'] == 'Before receipt'
    else:
        assert statuses(result)['supply'] == 'updated'
        assert item['stage'] == '原页补货候选已交付；正式未采用'
        assert item['user_decision'] is None
        assert item['closed'] is False


def test_heartbeat_get_is_public_readonly_and_never_touches_engine(chain, monkeypatch):
    result = tick(chain)
    result['private_path'] = 'DO-NOT-EXPOSE'
    result['outcomes'][1]['private_token'] = 'DO-NOT-EXPOSE'
    write(chain.health, result)
    monkeypatch.setenv('ORBIT_WORK_FEEDBACK_HEALTH', str(chain.health))
    class Handler:
        path = '/api/orbit/home/feedback'
        client_address = ('127.0.0.1', 1234)
        server = SimpleNamespace(server_port=9000)
        headers = Message()
        def _json(self, status, value): self.result = (status, value)
    class NoRuntime:
        def __getattr__(self, name): raise AssertionError('feedback touched runtime: ' + name)
    handler = Handler()
    handler.headers['Host'] = '127.0.0.1:9000'
    assert handle(handler, method='GET', runtime=NoRuntime())
    assert handler.result[0] == 200
    assert 'DO-NOT-EXPOSE' not in json.dumps(handler.result)
    assert handler.result[1]['business_execution_heartbeat'] is False
    assert handle(handler, method='POST', runtime=NoRuntime())
    assert handler.result[0] == 405


def test_cli_once_and_existing_observer_lock_do_not_start_second_writer(chain):
    script = Path(__file__).resolve().parents[1] / 'scripts/observe_work_feedback.py'
    command = [sys.executable, '-I', '-B', '-X', 'utf8', str(script), '--once',
        '--profile', str(chain.profile), '--manifest', str(chain.manifest),
        '--state-dir', str(chain.state), '--health-file', str(chain.health)]
    first = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert first.returncode == 0, first.stderr
    assert not (chain.state / 'observer.lock').exists()
    (chain.state / 'observer.lock').write_text('another-writer')
    before = chain.manifest.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert second.returncode != 0
    assert (chain.state / 'observer.lock').read_text() == 'another-writer'
    assert chain.manifest.read_bytes() == before
