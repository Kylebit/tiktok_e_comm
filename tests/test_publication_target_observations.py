"""Local retained-byte mechanics only; no real provider/account authority."""
from copy import deepcopy
import hashlib
import json

import pytest

from shared_platform.product_publication_reports import ProductPublicationReportStore, public_publication_report
from shared_platform.publication_target_observations import (
    RetainedTargetReadback, _lineage, collect_references, consume_target, retained_reference,
)
from test_product_publication_report_store import _report_payload


def report_payload(status='PUBLISHED'):
    report = _report_payload(status=status)
    report.update(schema_version='product-publication-report/v2',
        execution_identity={'skill_digest': '1'*64, 'git_commit': '2'*40, 'code_digest': '3'*64},
        release_authorization={'candidate_digest': '4'*64, 'approval_digest': '5'*64})
    label = 'tiktok:LH_PH'
    report['targets'] = [{'target_label': label, 'status': status, 'evidence': {
        'target_label': label, 'status': status, 'stage': 'READBACK',
        'provider_code': 'LOCAL_FIXTURE', 'provider_reason': 'Local retained bytes',
        'request_attempted': True, 'outcome_unknown': False, 'external_write_count': 0}}]
    platform = report['summary']['platforms'][0]
    platform.update(target_count=1, verified_count=int(status=='PUBLISHED'),
                    processing_count=int(status=='PROCESSING'), failed_count=int(status=='FAILED'))
    return report


class ClosedLocalReader:
    """Writes/reopens only tmp_path packets and raw synthetic transport bytes."""
    def __init__(self, root, observed_status='PUBLISHED'):
        self.root = root
        self.status = observed_status
        self.calls = []

    def retained_for_report(self, *, report, target_label):
        self.calls.append(('retain', target_label))
        raw = json.dumps({'local_fixture_status': self.status, 'label': target_label}).encode()
        packet = {'schema_version': 'retained-target-readback/v1',
            'lineage': _lineage(report, target_label), 'observed_status': self.status,
            'observed_at': '2026-10-03T00:00:00+00:00',
            'response_sha256': hashlib.sha256(raw).hexdigest(), 'response_byte_count': len(raw),
            'source_reference': 'closed-fixture:response',
            'account_authority': 'NOT_ESTABLISHED', 'execution_authority': False}
        retained = RetainedTargetReadback(packet, raw)
        reference = retained_reference(report, target_label, retained)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / (reference['packet_digest']+'.json')).write_text(json.dumps(packet), encoding='utf-8')
        (self.root / (reference['packet_digest']+'.body')).write_bytes(raw)
        return retained

    def read_retained(self, *, reference, report, target_label):
        self.calls.append(('read', target_label))
        stem = self.root / reference['packet_digest']
        return RetainedTargetReadback(json.loads(stem.with_suffix('.json').read_text(encoding='utf-8')),
                                      stem.with_suffix('.body').read_bytes())


@pytest.mark.parametrize('observed_status', ['PUBLISHED', 'PROCESSING', 'UNAVAILABLE'])
def test_report_reopens_target_reference_and_raw_bytes_without_granting_account_authority(tmp_path, observed_status):
    report = report_payload()
    reader = ClosedLocalReader(tmp_path/'retained', observed_status)
    report['target_observations'] = collect_references(report, reader)
    store = ProductPublicationReportStore(tmp_path/'reports.db', reports_root=tmp_path/'reports')
    store.store_report(report)
    reopened = ProductPublicationReportStore(store.path, reports_root=store.reports_root)
    persisted = reopened.get_report(report_id=report['report_id'], offer_id=report['offer_id'])
    assert persisted['target_observations'] == report['target_observations']
    result = consume_target(persisted, 'tiktok:LH_PH', reader)
    assert result['observed_status'] == observed_status
    assert result['observation_retention_verified'] is True
    assert result['official_success'] is False
    assert result['account_authority'] == 'NOT_ESTABLISHED'
    assert result['observation_blocker'] == 'TARGET_ACCOUNT_AUTHORITY_NOT_ESTABLISHED'
    assert 'target_observations' not in public_publication_report(persisted)


@pytest.mark.parametrize('damage', ['bytes', 'packet', 'missing_body'])
def test_reopened_reference_fails_closed_on_actual_retained_damage(tmp_path, damage):
    report = report_payload(); reader = ClosedLocalReader(tmp_path/'retained')
    report['target_observations'] = collect_references(report, reader)
    stem = reader.root/report['target_observations'][0]['packet_digest']
    if damage == 'bytes': stem.with_suffix('.body').write_bytes(b'wrong same source')
    elif damage == 'missing_body': stem.with_suffix('.body').unlink()
    else:
        packet = json.loads(stem.with_suffix('.json').read_text(encoding='utf-8'))
        packet['observed_status'] = 'PROCESSING'
        stem.with_suffix('.json').write_text(json.dumps(packet), encoding='utf-8')
    result = consume_target(report, 'tiktok:LH_PH', reader)
    assert result['official_success'] is False and result['observation_retention_verified'] is False
    assert result['observed_status'] is None
    assert result['observation_blocker'] == 'TARGET_OBSERVATION_UNAVAILABLE_OR_CHANGED'


@pytest.mark.parametrize('identity', ['run_id', 'plan_id', 'snapshot', 'approval', 'target'])
def test_reference_from_another_lineage_is_rejected_before_report_database_creation(tmp_path, identity):
    report = report_payload(); reader = ClosedLocalReader(tmp_path/'retained')
    report['target_observations'] = collect_references(report, reader)
    if identity == 'snapshot': report['snapshot']['digest'] = '6'*64
    elif identity == 'approval': report['release_authorization']['approval_digest'] = '6'*64
    elif identity == 'target': report['target_observations'][0]['target_label'] = 'tiktok:LH_MY'
    else: report[identity] = 'other-' + identity
    store = ProductPublicationReportStore(tmp_path/'reports.db', reports_root=tmp_path/'reports')
    with pytest.raises(ValueError, match='TARGET_OBSERVATION'):
        store.store_report(report)
    assert not store.path.exists()


def test_naked_official_dictionary_cannot_become_a_retained_observation(tmp_path):
    report = report_payload()
    reader = ClosedLocalReader(tmp_path/'retained')
    reader.retained_for_report = lambda **kwargs: {'official': True, 'verified': True, 'status': 'PUBLISHED'}
    assert collect_references(report, reader) == []
    with pytest.raises(ValueError, match='RETAINED_BYTES_REQUIRED'):
        retained_reference(report, 'tiktok:LH_PH', {'official': True})


def test_references_do_not_supply_a_missing_native_reader_or_cover_other_targets(tmp_path):
    report = report_payload(); reader = ClosedLocalReader(tmp_path/'retained')
    report['target_observations'] = collect_references(report, reader)
    uninstalled = consume_target(report, 'tiktok:LH_PH', None)
    assert uninstalled['observation_blocker'] == 'TARGET_OBSERVATION_READER_UNAVAILABLE'
    assert not uninstalled['official_success']
    report['targets'].append({**deepcopy(report['targets'][0]), 'target_label': 'tiktok:LH_MY'})
    other = consume_target(report, 'tiktok:LH_MY', reader)
    assert other['observation_reference'] is None
    assert other['observed_status'] is None and not other['official_success']
    assert reader.calls == [('retain', 'tiktok:LH_PH')]


@pytest.mark.parametrize('field,value', [('account_authority', 'VERIFIED'), ('execution_authority', True)])
def test_retained_packet_cannot_self_grant_account_or_execution_authority(tmp_path, field, value):
    report = report_payload(); reader = ClosedLocalReader(tmp_path/'retained')
    retained = reader.retained_for_report(report=report, target_label='tiktok:LH_PH')
    forged = deepcopy(retained.packet); forged[field] = value
    with pytest.raises(ValueError, match='TARGET_OBSERVATION_BINDING_INVALID'):
        retained_reference(report, 'tiktok:LH_PH', RetainedTargetReadback(forged, retained.response_bytes))


def test_duplicate_references_cannot_cover_two_targets(tmp_path):
    report = report_payload(); reader = ClosedLocalReader(tmp_path/'retained')
    refs = collect_references(report, reader)
    report['target_observations'] = refs + deepcopy(refs)
    store = ProductPublicationReportStore(tmp_path/'reports.db', reports_root=tmp_path/'reports')
    with pytest.raises(ValueError, match='TARGET_OBSERVATION_TARGET_UNBOUND'):
        store.store_report(report)
    assert not store.path.exists()


@pytest.mark.parametrize('observed_status', ['PUBLISHED', 'PROCESSING', 'UNAVAILABLE'])
def test_real_persisted_projection_distinguishes_reported_and_retained_status(tmp_path, monkeypatch, observed_status):
    from test_r3_status_projection import approved_context, seed_historical_report, state
    from modules.products import server
    store, data, _, market, snapshot = approved_context(tmp_path, monkeypatch)
    reports = server._product_publication_report_store()
    reader = ClosedLocalReader(tmp_path/'retained', observed_status)
    reports.target_observation_reader = reader
    original = reports.store_report
    def store_with_retained_reference(report):
        value = deepcopy(report)
        value['target_observations'] = collect_references(value, reader)
        return original(value)
    monkeypatch.setattr(reports, 'store_report', store_with_retained_reference)
    monkeypatch.setattr(server, '_product_publication_report_store', lambda: reports)
    receipt = seed_historical_report(store, data, market, snapshot, state='PUBLISHED')
    monkeypatch.setattr(reports, 'store_report', original)
    from shared_platform.product_publication_runner import ProductPublicationRunner
    before_replay_calls = list(reader.calls)
    def forbidden_dispatch(*args, **kwargs):
        pytest.fail('reading immutable observations must not redispatch')
    replay = ProductPublicationRunner(release_store=store, report_store=reports).run(
        run_id='status-run', offer_id=data['offer_id'], plan_id=data['plan_id'],
        platform_scope=('TIKTOK',), platform_executors={'TIKTOK': forbidden_dispatch},
        execution_identity=receipt.report['execution_identity'],
        release_candidate=market['candidate'], final_approval=market['approval'])
    assert replay.replayed is True
    assert replay.report['target_observations'] == receipt.report['target_observations']
    assert reader.calls == before_replay_calls
    projected = state(data)
    target = next(t for t in projected['target_results'] if t['target_label'].startswith('tiktok:'))
    assert target['reported_status'] == 'PUBLISHED'
    assert target['observed_status'] == observed_status
    assert target['observation_retention_verified'] is True
    assert target['official_success'] is False
    assert target['status'] == 'RECONCILIATION_REQUIRED'
    assert target['blockers'] == ['TARGET_ACCOUNT_AUTHORITY_NOT_ESTABLISHED']
    assert all(not t['official_success'] for t in projected['target_results'])
