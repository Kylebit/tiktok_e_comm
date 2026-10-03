"""Incomplete real first-review inputs must reach facts, not workspace adoption."""
import json
from pathlib import Path

import pytest

from shared_platform import operations_publication_prepare as preparation
from test_native_parent_facts import _leased, live


@pytest.mark.parametrize('case', ['missing', 'placeholder', 'incomplete'],
                         ids=['missing', 'placeholder', 'incomplete'])
def test_real_leased_review_does_not_adopt_an_incomplete_image_plan(live, monkeypatch, case):
    engine, profile, worker, token, _ = _leased(live, monkeypatch)
    task = engine.get(next(iter(worker._task_ids)))
    try:
        with worker.boundary.hold(engine, task, token, profile):
            current, packet = preparation._current(task, profile)
            path = Path(current['review_path'])
            actual = json.loads(path.read_bytes())
            assert actual['image_execution_plan']['status'] == 'USER_DECISION_REQUIRED'
            if case == 'missing':
                actual.pop('image_execution_plan')
            elif case == 'incomplete':
                actual['image_execution_plan'] = {
                    'schema_version': 'first-review-image-plan/v1', 'status': 'PROPOSED'}
            path.write_text(json.dumps(actual), encoding='utf-8')
            before = path.read_bytes()
            def forbidden(*args, **kwargs):
                pytest.fail('Incomplete image inputs must not create a workspace preparation')
            monkeypatch.setattr(preparation.native, '_prepare_workspace_review', forbidden)
            assert preparation._review(task, profile, current, actual) is None
            assert path.read_bytes() == before
            assert not preparation.native.read_frozen(task, profile)
            assert 'facts_attempt' not in (engine.get(task['task_id']).get('checkpoint') or {})
    finally:
        worker.close()
