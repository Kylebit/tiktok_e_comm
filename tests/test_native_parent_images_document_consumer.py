"""Actual frozen preparation and canonical local QA paths, closed transports."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from shared_platform import workbench_publication_native as native
from shared_platform.publication_r3_image_bridge import load_r2_documents
from test_native_parent_images import images_task, _runtime, FixtureClient
from test_native_parent_images_phase_continuation import _closed
from test_round1_auto_freeze import public_settings
from test_round1_workspace_freeze import live


def test_original_prepared_review_is_consumed_without_changing_current_draft_or_snapshot(images_task):
    v = images_task
    directory = v['profile'].root / 'reports/product-preparation' / v['task']['scope']['offer_id']
    paths = [directory / name for name in ('first-review.json', 'round1-approved-snapshot.json')]
    original = {path: path.read_bytes() for path in paths}
    current, snapshot = (json.loads(original[path]) for path in paths)
    assert current['status'] == 'DECISION_REQUIRED'
    reader = native.NativeR2PreparedReviewReader(v['task'], v['profile'])
    review = reader.read_verified(snapshot['offer_id'], directory.parent, current, snapshot)
    from shared_platform.publication_rounds import canonical_digest
    assert review['status'] == 'FIRST_REVIEW_READY'
    assert canonical_digest(review) == snapshot['first_review_digest']
    assert review['target_selection']['requested'] == snapshot['canonical_targets']
    assert all(path.read_bytes() == raw for path, raw in original.items())
    assert not (directory / 'paid-requests').exists()


@pytest.mark.parametrize('drift', ['foreign-snapshot', 'raw-source', 'foreign-root'])
def test_native_prepared_reader_rejects_different_original_or_current_source(images_task, drift):
    v = images_task
    directory = v['profile'].root / 'reports/product-preparation' / v['task']['scope']['offer_id']
    raw_path = directory / 'first-review.json'
    original = raw_path.read_bytes()
    current = json.loads(original)
    snapshot = json.loads((directory / 'round1-approved-snapshot.json').read_bytes())
    root = directory.parent
    if drift == 'foreign-snapshot':
        snapshot['snapshot_digest'] = 'sha256:' + '0' * 64
        error = 'NATIVE_R2_PREPARED_SOURCE_CONFLICT'
    elif drift == 'raw-source':
        current['product_center_revision'] += 1
        error = 'NATIVE_R2_CURRENT_REVIEW_CHANGED'
    else:
        root = v['profile'].root / 'foreign-reports'
        error = 'NATIVE_R2_PREPARED_SOURCE_CONFLICT'
    with pytest.raises(ValueError, match=error):
        native.NativeR2PreparedReviewReader(v['task'], v['profile']).read_verified(
            snapshot['offer_id'], root, current, snapshot)
    assert raw_path.read_bytes() == original
    assert not (directory / 'paid-requests').exists()


def test_caller_approval_dictionary_cannot_select_native_document_projection(tmp_path):
    with pytest.raises(ValueError, match='NATIVE_R2_PREPARED_READER_REQUIRED'):
        load_r2_documents('3828811808', reports_root=tmp_path,
            native_review_reader={'approved': True, 'prepared_reference': 'caller'})
    assert list(tmp_path.iterdir()) == []


def test_localized_qa_reads_original_completed_output_and_rejects_foreign_checkpoint(images_task, monkeypatch):
    v = images_task
    client, history_directory, _, _ = _closed(v, monkeypatch)
    history = history_directory.parents[2]
    client.ready = True
    result = v['worker'].images_adapter.recover_images(v['task'], token=v['token'])
    assert result['status'] == 'prepared', result
    directory = v['profile'].root / 'reports/product-preparation' / v['task']['scope']['offer_id']
    generation_path, translated_path = (directory / name for name in
        ('brand-image-generation.json', 'brand-image-translation.json'))
    generation = json.loads(generation_path.read_bytes())
    original = translated_path.read_bytes()
    translated = json.loads(original)
    assert len(translated['assets']) == 3 and all('artifact_path' not in row for row in translated['assets'])
    before_calls = list(FixtureClient.calls)
    journal = history_directory / 'paid-requests/events.jsonl'
    original_journal = journal.read_bytes()
    with _runtime(v, history, read_only=True) as runtime:
        _, projected = runtime.qa_local_documents(generation, translated)
        assert all(row['artifact_path'] == str(Path(row['checkpoint_path']).with_suffix('.png'))
                   for row in projected['assets'])
        assert translated_path.read_bytes() == original
        forged = deepcopy(translated)
        forged['assets'][0]['checkpoint_path'] = generation['assets'][0]['checkpoint_path']
        translated_path.write_text(json.dumps(forged), encoding='utf-8')
        try:
            with pytest.raises(ValueError, match='cached image business identity drifted'):
                runtime.qa_local_documents(generation, forged)
        finally:
            translated_path.write_bytes(original)
    assert FixtureClient.calls == before_calls and journal.read_bytes() == original_journal


def test_lost_lease_cannot_project_qa_files_or_start_a_new_request(images_task):
    v = images_task
    history = v['profile'].root.parent / (v['profile'].root.name + '-qa-lost-lease-history')
    with _runtime(v, history) as runtime:
        journal = runtime.paid_context.path
        before = journal.read_bytes()
        before_calls = list(FixtureClient.calls)
        with v['engine'].transaction() as db:
            db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?', (v['task']['task_id'],))
        with pytest.raises(ValueError, match='stale or invalid lease'):
            runtime.qa_local_documents({'assets': []}, {'assets': []})
    assert journal.read_bytes() == before and FixtureClient.calls == before_calls
