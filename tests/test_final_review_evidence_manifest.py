"""Synthetic evidence inventory checks; no runtime database or provider access."""

import json
import os
from pathlib import Path
import sqlite3

import pytest

from shared_platform import final_review_evidence_manifest as collector


OFFER = '3828811808'
TARGETS = ('tiktok:LH_MY',)


def _write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def _fixture(root):
    reports = root / 'reports' / OFFER
    reports.mkdir(parents=True)
    png = reports / 'master.png'
    png.write_bytes(b'\x89PNG\r\n\x1a\n' + b'1234')
    localized = reports / 'localized.png'
    localized.write_bytes(b'\x89PNG\r\n\x1a\n' + b'4321')
    for key, name in collector.R2_DOCUMENTS.items():
        value = {'offer_id': OFFER}
        if key == 'round1_snapshot':
            value['canonical_targets'] = list(TARGETS)
        if key == 'first_review':
            value['target_selection'] = {'requested': list(TARGETS)}
        if key in {'generation_result', 'translation_result'}:
            asset = png if key == 'generation_result' else localized
            value['assets'] = [{'artifact_path': str(asset), 'artifact_digest':
                                'sha256:' + collector.hashlib.sha256(asset.read_bytes()).hexdigest()}]
        if key == 'translation_plan':
            value.update(tasks=[{'review_number': 1}], approved_task_count=1)
        if key == 'translation_result':
            value.update(status='LOCALIZED_IMAGE_REVIEW_REQUIRED',
                         approved_tasks=[{'review_number': 1}], approved_task_count=1)
        _write(reports / name, value)
    state = root / 'state.json'
    _write(state, {'offer_id': OFFER, '_revision': 3})
    databases = []
    for name in ('shop.db', 'release.db', 'tasks.db'):
        path = root / name
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE evidence (value TEXT)')
            connection.execute("INSERT INTO evidence VALUES ('synthetic')")
        databases.append(path)
    return {'offer_id': OFFER, 'targets': TARGETS, 'reports_root': reports.parent,
            'product_state': state, 'catalog_db': databases[0],
            'release_db': databases[1], 'task_db': databases[2]}, png


def _tree(root):
    result = {}
    for path in (root, *sorted(root.rglob('*'))):
        info = path.stat()
        result[str(path)] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                             path.read_bytes() if path.is_file() else None)
    return result


def test_audited_manifest_is_versioned_and_source_is_unchanged(tmp_path):
    inputs, _ = _fixture(tmp_path)
    unrelated = tmp_path / 'reports' / '999'
    unrelated.mkdir()
    (unrelated / 'other.txt').write_text('unrelated', encoding='utf-8')
    before = _tree(tmp_path)
    result = collector.collect_final_review_evidence_manifest(**inputs)
    assert result['schema_version'] == 'final-review-evidence-manifest/v1'
    assert result['coverage'] == 'AUDITED_FILES_ONLY'
    assert result['execution_authority'] is False
    assert result['product_revision'] == 3
    assert len(result['sources']) == 12  # six reports, two images, state, three DBs
    assert result['manifest_digest'].startswith('sha256:')
    assert _tree(tmp_path) == before  # access time is intentionally excluded


def test_report_replacement_between_sequential_reads_is_rejected(tmp_path, monkeypatch):
    inputs, _ = _fixture(tmp_path)
    original = collector._capture_file
    changed = False

    def injected(path, *, database=False):
        nonlocal changed
        result = original(path, database=database)
        if path.name == 'first-review.json' and not changed:
            changed = True
            replacement = path.with_suffix('.replacement')
            _write(replacement, {'offer_id': OFFER, 'target_selection': {'requested': list(TARGETS)},
                                 'changed': True})
            os.replace(replacement, path)
        return result

    monkeypatch.setattr(collector, '_capture_file', injected)
    with pytest.raises(ValueError, match='DIRECTORY_DRIFT|SOURCE_DRIFT|SOURCE_UNSTABLE'):
        collector.collect_final_review_evidence_manifest(**inputs)
    assert changed


def test_image_bytes_drift_is_rejected(tmp_path, monkeypatch):
    inputs, image = _fixture(tmp_path)
    original = collector._capture_file
    changed = False

    def injected(path, *, database=False):
        nonlocal changed
        if path.name == 'state.json' and not changed:
            changed = True
            image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'5678')
        return original(path, database=database)

    monkeypatch.setattr(collector, '_capture_file', injected)
    with pytest.raises(ValueError, match='SOURCE_DRIFT|SOURCE_UNSTABLE'):
        collector.collect_final_review_evidence_manifest(**inputs)
    assert changed


def test_product_revision_drift_is_rejected(tmp_path, monkeypatch):
    inputs, _ = _fixture(tmp_path)
    original = collector._capture_file
    changed = False

    def injected(path, *, database=False):
        nonlocal changed
        if path.name == 'shop.db' and not changed:
            changed = True
            _write(inputs['product_state'], {'offer_id': OFFER, '_revision': 4})
        return original(path, database=database)

    monkeypatch.setattr(collector, '_capture_file', injected)
    with pytest.raises(ValueError, match='SOURCE_DRIFT|SOURCE_UNSTABLE'):
        collector.collect_final_review_evidence_manifest(**inputs)
    assert changed


def test_wal_and_mixed_identity_are_rejected(tmp_path):
    inputs, _ = _fixture(tmp_path)
    Path(str(inputs['catalog_db']) + '-wal').write_bytes(b'active WAL')
    with pytest.raises(ValueError, match='SIDECAR_PRESENT'):
        collector.collect_final_review_evidence_manifest(**inputs)
    Path(str(inputs['catalog_db']) + '-wal').unlink()
    report = inputs['reports_root'] / OFFER / 'automated-image-qa.json'
    _write(report, {'offer_id': '999'})
    with pytest.raises(ValueError, match='OFFER_CONFLICT'):
        collector.collect_final_review_evidence_manifest(**inputs)


def test_target_and_hardlink_conflicts_are_rejected(tmp_path):
    inputs, image = _fixture(tmp_path)
    with pytest.raises(ValueError, match='TARGET_CONFLICT'):
        collector.collect_final_review_evidence_manifest(**dict(inputs, targets=('ozon:RU',)))
    os.link(image, tmp_path / 'alternate.png')
    with pytest.raises(ValueError, match='SOURCE_UNSTABLE'):
        collector.collect_final_review_evidence_manifest(**inputs)


def test_not_required_translation_records_zero_assets_and_reason(tmp_path):
    inputs, _ = _fixture(tmp_path)
    reports = inputs['reports_root'] / OFFER
    plan = reports / collector.R2_DOCUMENTS['translation_plan']
    result = reports / collector.R2_DOCUMENTS['translation_result']
    _write(plan, {'offer_id': OFFER, 'tasks': [], 'approved_task_count': 0})
    _write(result, {'offer_id': OFFER, 'status': 'NOT_REQUIRED', 'assets': [],
                    'approved_tasks': [], 'approved_task_count': 0})

    manifest = collector.collect_final_review_evidence_manifest(**inputs)

    assert manifest['coverage'] == 'AUDITED_FILES_ONLY'
    assert manifest['execution_authority'] is False
    assert manifest['translation_assets'] == {
        'count': 0, 'absence_reason': 'NO_APPROVED_TRANSLATION_TASKS'
    }
    assert len(manifest['sources']) == 11  # six reports, one master, state, three DBs


@pytest.mark.parametrize('status,task_count', [
    ('LOCALIZED_IMAGE_REVIEW_REQUIRED', 1),
    ('NOT_REQUIRED', 1),
])
def test_required_translation_asset_absence_is_rejected(tmp_path, status, task_count):
    inputs, _ = _fixture(tmp_path)
    reports = inputs['reports_root'] / OFFER
    result = reports / collector.R2_DOCUMENTS['translation_result']
    _write(result, {'offer_id': OFFER, 'status': status, 'assets': [],
                    'approved_tasks': [{'review_number': 1}],
                    'approved_task_count': task_count})

    with pytest.raises(ValueError, match='IMAGE_COVERAGE_UNKNOWN'):
        collector.collect_final_review_evidence_manifest(**inputs)


def test_partial_translation_asset_coverage_is_rejected(tmp_path):
    inputs, _ = _fixture(tmp_path)
    reports = inputs['reports_root'] / OFFER
    plan = reports / collector.R2_DOCUMENTS['translation_plan']
    result = reports / collector.R2_DOCUMENTS['translation_result']
    tasks = [{'review_number': 1}, {'review_number': 2}]
    _write(plan, {'offer_id': OFFER, 'tasks': tasks, 'approved_task_count': 2})
    receipt = json.loads(result.read_text(encoding='utf-8'))
    receipt.update(approved_tasks=tasks, approved_task_count=2)
    _write(result, receipt)  # one recorded image for two approved tasks

    with pytest.raises(ValueError, match='IMAGE_COVERAGE_UNKNOWN'):
        collector.collect_final_review_evidence_manifest(**inputs)


def test_zero_translation_without_explicit_approval_scope_is_rejected(tmp_path):
    inputs, _ = _fixture(tmp_path)
    reports = inputs['reports_root'] / OFFER
    result = reports / collector.R2_DOCUMENTS['translation_result']
    _write(result, {'offer_id': OFFER, 'status': 'NOT_REQUIRED', 'assets': [],
                    'approved_tasks': [], 'approved_task_count': 0})

    with pytest.raises(ValueError, match='IMAGE_COVERAGE_UNKNOWN'):
        collector.collect_final_review_evidence_manifest(**inputs)
