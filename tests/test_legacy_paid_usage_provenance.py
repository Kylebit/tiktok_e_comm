"""Original legacy metadata is usage evidence, never new request authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from modules.sourcing.image_generation_checkpoint import digest
from shared_platform.publication_paid_requests import (PaidRequestBlocked, PaidRequestContext,
    discover_usage_baseline, validate_usage_baseline)
from tests.test_publication_paid_entry import OFFER, png, policy, round1, write, read


def _fixture(tmp_path, *, localized=False, count=1):
    root = tmp_path / 'original-history'
    directory = root / 'reports/product-preparation' / OFFER
    snapshot = round1()
    write(directory / 'round1-approved-snapshot.json', snapshot)
    folder = directory / ('brand-image-translation-checkpoints' if localized else 'brand-image-checkpoints-lingshi')
    folder.mkdir(parents=True)
    rows, checkpoints = [], []
    for i in range(count):
        kind = 'localized' if localized else 'brand'
        identity = digest({'original_fixture_request': i, 'kind': kind})
        cp = folder / ('lingshi-' + ('' if localized else 'brand-') + identity + '.json')
        raw = png(i + 1)
        cp.with_suffix('.png').write_bytes(raw)
        output_digest = hashlib.sha256(raw).hexdigest()
        task = 11000 + i
        receipt = {'status': 'COMPLETED', 'provider': 'lingshi-media/v1', 'model': 'tt-image-2',
                   'task_id': task, 'client_business_id': kind + '-' + identity[:40],
                   'request_attempted': True, 'outcome_unknown': False, 'external_generation_count': 1,
                   'output_digest': output_digest, 'cost': 0.0413}
        checkpoint = {'schema_version': kind + '-image-lingshi-checkpoint/v1',
                      'identity_digest': identity, 'client_business_id': receipt['client_business_id'],
                      'status': 'COMPLETED', 'task_id': task, 'output_digest': output_digest, 'receipt': receipt}
        write(cp, checkpoint)
        row = {'brand_id': 'livelyhive-sea', 'role': 'cover', 'status': 'COMPLETED',
               'provider': receipt['provider'], 'model': receipt['model'], 'cost': receipt['cost'],
               'artifact_digest': 'sha256:' + output_digest, 'external_generation_count': 1}
        if localized: row.update(locale='ms-MY', provider_task_id=task)
        else: row.update(task_id=task, artifact_path=str(cp.with_suffix('.png')))
        rows.append(row); checkpoints.append(cp)
    name = 'brand-image-translation.json' if localized else 'brand-image-generation.json'
    report = {'schema_version': 'brand-image-translation/v1' if localized else 'brand-image-generation/v1',
              'offer_id': OFFER, 'assets': rows}
    if not localized: report['round1_snapshot_digest'] = snapshot['snapshot_digest']
    write(directory / name, report)
    return root, directory, snapshot, checkpoints, directory / name


def _discover(value):
    root, directory, snapshot, _cp, _report = value
    return discover_usage_baseline(repo_root=root, offer_id=OFFER, directory=directory,
                                   policy=policy(), round1=snapshot)


def _original_bytes(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*')
            if p.is_file() and 'paid-requests' not in p.relative_to(directory).parts}


@pytest.mark.parametrize('localized', [False, True], ids=['master-v1', 'localized-v1'])
def test_original_v1_mapping_is_retained_once_without_invented_request_fields(tmp_path, localized):
    value = _fixture(tmp_path, localized=localized)
    before = _original_bytes(value[1])
    baseline = _discover(value)
    checked = validate_usage_baseline(baseline, offer_id=OFFER)
    assert len(checked['records']) == 1
    record = checked['records'][0]
    assert record['state'] == 'CONFIRMED'
    assert len(record['legacy_usage_provenance']) == 1
    proof = record['legacy_usage_provenance'][0]
    assert proof['authority'] == 'HISTORICAL_USAGE_ONLY_NO_REQUEST_REPLAY_OR_NEW_GRANT'
    assert proof['business']['offer_id'] == OFFER
    assert 'business_proof' not in record
    assert _original_bytes(value[1]) == before
    cp = read(value[3][0])
    assert not {'offer_id', 'business_digest', 'kind', 'request_digest', 'attempt'} & set(cp)


def test_rework_eight_original_references_do_not_add_eight_paid_requests(tmp_path):
    value = _fixture(tmp_path, count=8)
    rows = read(value[4])['assets']
    write(value[1] / 'brand-image-rework.json', {'schema_version': 'brand-image-rework/v1',
        'offer_id': OFFER, 'superseded_assets': deepcopy(rows[:4]), 'replacement_assets': deepcopy(rows[4:])})
    before = _original_bytes(value[1])
    baseline = validate_usage_baseline(_discover(value), offer_id=OFFER)
    assert len(baseline['records']) == 8
    assert sum(len(r['legacy_usage_provenance']) for r in baseline['records']) == 8
    assert _original_bytes(value[1]) == before
    ctx = PaidRequestContext(offer_id=OFFER, round1=value[2], policy=policy(8),
                             reports_root=value[1].parent, usage_baseline=baseline)
    ctx.ensure_ready()
    assert ctx.summary()['occupied'] == 8
    journal = ctx.path.read_bytes()
    with pytest.raises(PaidRequestBlocked, match='PAID_BUDGET_EXHAUSTED'):
        ctx.reserve(purpose='brand_image_generation', model='tt-image-2',
                    business={'kind': 'new-fixture-only'}, request={'unused': True})
    assert ctx.path.read_bytes() == journal
    assert _original_bytes(value[1]) == before


@pytest.mark.parametrize('change', ['missing-report', 'foreign-offer', 'wrong-role', 'wrong-task',
                                    'wrong-provider', 'wrong-fee', 'changed-png', 'changed-r1', 'rehashed-r1-role', 'unknown'],
                         ids=str)
def test_incomplete_or_conflicting_legacy_sources_stay_specific_gaps(tmp_path, change):
    value = _fixture(tmp_path)
    cp_path, report_path = value[3][0], value[4]
    report = read(report_path)
    cp = read(cp_path)
    if change == 'missing-report': report_path.unlink()
    elif change == 'foreign-offer': report['offer_id'] = '9000099'; write(report_path, report)
    elif change == 'wrong-role': report['assets'][0]['role'] = 'unrequested'; write(report_path, report)
    elif change == 'wrong-task': report['assets'][0]['task_id'] += 1; write(report_path, report)
    elif change == 'wrong-provider': report['assets'][0]['provider'] = 'other-provider'; write(report_path, report)
    elif change == 'wrong-fee': report['assets'][0]['cost'] += 1; write(report_path, report)
    elif change == 'changed-png': cp_path.with_suffix('.png').write_bytes(png(99))
    elif change == 'changed-r1':
        snapshot = read(value[1] / 'round1-approved-snapshot.json')
        snapshot['offer_id'] = '9000099'; write(value[1] / 'round1-approved-snapshot.json', snapshot)
    elif change == 'rehashed-r1-role':
        snapshot = read(value[1] / 'round1-approved-snapshot.json')
        snapshot['image_plan']['brand_plans'][0]['generated_assets'][0]['role'] = 'not-the-original-role'
        snapshot.pop('snapshot_digest')
        from shared_platform.publication_rounds import canonical_digest
        snapshot['snapshot_digest'] = canonical_digest(snapshot)
        write(value[1] / 'round1-approved-snapshot.json', snapshot)
    else:
        cp['status'] = 'SUBMISSION_UNKNOWN'; cp['receipt']['outcome_unknown'] = True; write(cp_path, cp)
    before = _original_bytes(value[1])
    with pytest.raises(PaidRequestBlocked, match='PRIOR_PAID_USAGE_GAPS.*legacy paid usage provenance'):
        _discover(value)
    assert _original_bytes(value[1]) == before
    assert not (value[1] / 'paid-requests/events.jsonl').exists()


@pytest.mark.parametrize('change', ['checkpoint', 'report', 'png', 'proof'], ids=str)
def test_retained_baseline_revalidates_legacy_sources_even_when_report_was_seen_first(tmp_path, change):
    value = _fixture(tmp_path)
    baseline = _discover(value)
    assert baseline['records'][0]['request_pointer'] == '/assets/0'
    assert baseline['records'][0]['legacy_usage_provenance']
    if change == 'checkpoint':
        cp = read(value[3][0]); cp['receipt']['cost'] += 1; write(value[3][0], cp)
    elif change == 'report':
        report = read(value[4]); report['assets'][0]['artifact_digest'] = 'sha256:' + '0' * 64; write(value[4], report)
    elif change == 'png': value[3][0].with_suffix('.png').write_bytes(png(98))
    else: baseline['records'][0]['legacy_usage_provenance'][0]['business']['offer_id'] = '9000099'
    with pytest.raises(PaidRequestBlocked, match='LEGACY_USAGE_'):
        validate_usage_baseline(baseline, offer_id=OFFER)
    assert not (value[1] / 'paid-requests/events.jsonl').exists()