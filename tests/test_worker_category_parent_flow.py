"""Private parent R1 orchestration using the real domain and fake official GETs."""

from copy import deepcopy

import pytest

from shared_platform.worker_category_facts_transport import VerifiedFactsCategoryTransport
from shared_platform.worker_category_parent_flow import ParentR1CategoryFlow
from test_worker_category_bridge import fixture


def _flow(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    transport = VerifiedFactsCategoryTransport(engine, store, worker_id='worker')
    parent = ParentR1CategoryFlow(bridge, transport, worker_id='worker')
    context = {key: body[key] for key in (
        'context_digest', 'offer_id', 'product_center_revision',
        'requested_targets', 'source_region', 'account_identity_digest')}
    return engine, task_id, lease, context, fake, store, parent


def _selection(projection):
    option = projection['options'][0]
    attributes = []
    for attribute in option['attributes']:
        if attribute['kind'] == 'TEXT':
            attributes.append({'attribute_identity_digest': attribute['attribute_identity_digest'],
                               'text_value': 'Explicit text'})
        elif attribute['required']:
            values = attribute['values'] if attribute['kind'] == 'MULTI_SELECT' else attribute['values'][:1]
            attributes.append({'attribute_identity_digest': attribute['attribute_identity_digest'],
                               'option_identity_digests': [v['option_identity_digest'] for v in values]})
    return {'options_reference': projection['options_reference'],
            'options_digest': projection['options_digest'],
            'selected_category_identity': option['category_identity_digest'],
            'attribute_selections': attributes}


def test_parent_options_choice_capture_handoff_and_replay(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, parent = _flow(tmp_path, monkeypatch)
    task = engine.get(task_id)
    options = parent.options(task, lease_token=lease, context=context)
    assert options['status'] == 'SUCCEEDED' and options['dispatched_this_call']
    assert options['projection']['status'] == 'AWAITING_SELECTION'
    assert len(fake.calls) == 5
    repeated = parent.options(task, lease_token=lease, context=context)
    assert repeated['status'] == 'SUCCEEDED' and not repeated['dispatched_this_call']
    assert repeated['projection'] == options['projection'] and len(fake.calls) == 5
    choice = _selection(options['projection'])
    capture = parent.capture(task, lease_token=lease, selection=choice)
    assert capture['status'] == 'SUCCEEDED' and capture['dispatched_this_call']
    evidence = capture['verified_capture']
    assert evidence['task_id'] == task_id
    assert evidence['capture_request_id'] == capture['request_id']
    assert evidence['observer_reference'] == evidence['observation']['observer_reference']
    assert evidence == parent.verified_capture(task, lease_token=lease)
    assert len(fake.calls) == 7
    repeated_capture = parent.capture(task, lease_token=lease, selection=choice)
    assert repeated_capture['status'] == 'SUCCEEDED'
    assert not repeated_capture['dispatched_this_call']
    assert repeated_capture['verified_capture'] == evidence
    assert len(fake.calls) == 7


def test_parent_requires_structured_selection_before_any_capture_get(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, parent = _flow(tmp_path, monkeypatch)
    task = engine.get(task_id)
    with pytest.raises(ValueError, match='options'):
        parent.capture(task, lease_token=lease, selection={
            'options_reference': 'fabricated', 'options_digest': 'fabricated',
            'selected_category_identity': 'fabricated', 'attribute_selections': []})
    assert fake.calls == [] and not store.path.exists()
    projection = parent.options(task, lease_token=lease, context=context)['projection']
    choice = _selection(projection)
    choice['selected_category_identity'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError, match='CATEGORY_SELECTION_INVALID'):
        parent.capture(task, lease_token=lease, selection=choice)
    assert len(fake.calls) == 5
    with pytest.raises(ValueError, match='trusted category capture absent'):
        parent.verified_capture(task, lease_token=lease)


def test_parent_timeout_is_unknown_and_never_replays_or_hands_off(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, parent = _flow(tmp_path, monkeypatch)
    task = engine.get(task_id)
    projection = parent.options(task, lease_token=lease, context=context)['projection']
    original = fake.merchant_get
    def timeout(path, params):
        if len(fake.calls) == 6:
            raise TimeoutError('synthetic official GET timeout')
        return original(path, params)
    fake.merchant_get = timeout
    choice = _selection(projection)
    first = parent.capture(task, lease_token=lease, selection=choice)
    assert first['status'] == 'UNKNOWN' and first['dispatched_this_call']
    assert 'verified_capture' not in first and len(fake.calls) == 6
    repeated = parent.capture(task, lease_token=lease, selection=choice)
    assert repeated['status'] == 'UNKNOWN' and not repeated['dispatched_this_call']
    assert len(fake.calls) == 6
    with pytest.raises(ValueError):
        parent.verified_capture(task, lease_token=lease)


def test_parent_lease_version_and_shop_scope_fail_before_get(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, parent = _flow(tmp_path, monkeypatch)
    task = engine.get(task_id)
    with pytest.raises(ValueError):
        parent.options(task, lease_token='forged', context=context)
    stale = deepcopy(task)
    stale['version']['code_version'] = 'b' * 40
    with pytest.raises(ValueError, match='version'):
        parent.options(stale, lease_token=lease, context=context)
    altered = deepcopy(task)
    altered['scope']['shops'].append('shopee:VN')
    with pytest.raises(ValueError, match='scope'):
        parent.options(altered, lease_token=lease, context=context)
    outside = deepcopy(context)
    outside['requested_targets'].append('shopee:VN')
    with pytest.raises(ValueError, match='target'):
        parent.options(task, lease_token=lease, context=outside)
    assert fake.calls == [] and not store.path.exists()


def test_parent_options_timeout_cannot_be_converted_to_selection(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, parent = _flow(tmp_path, monkeypatch)
    task = engine.get(task_id)
    original = fake.merchant_get
    def timeout(path, params):
        if len(fake.calls) == 2:
            raise TimeoutError('synthetic official GET timeout')
        return original(path, params)
    fake.merchant_get = timeout
    first = parent.options(task, lease_token=lease, context=context)
    assert first['status'] == 'UNKNOWN' and 'projection' not in first
    assert len(fake.calls) == 2
    repeat = parent.options(task, lease_token=lease, context=context)
    assert repeat['status'] == 'UNKNOWN' and not repeat['dispatched_this_call']
    assert len(fake.calls) == 2
    with pytest.raises(ValueError, match='options result is not verified'):
        parent.capture(task, lease_token=lease, selection={
            'options_reference': 'unknown', 'options_digest': 'unknown',
            'selected_category_identity': 'unknown', 'attribute_selections': []})


def test_parent_capture_recheck_failure_has_no_handoff_or_replay(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, parent = _flow(tmp_path, monkeypatch)
    task = engine.get(task_id)
    projection = parent.options(task, lease_token=lease, context=context)['projection']
    choice = _selection(projection)
    fake.attribute_rows_by_category[101].append({
        'attribute_id': 99, 'original_attribute_name': 'New optional',
        'is_mandatory': False, 'input_type': 'SINGLE_SELECT',
        'attribute_value_list': [{'value_id': 1, 'original_value_name': 'One'}]})
    first = parent.capture(task, lease_token=lease, selection=choice)
    assert first['status'] == 'FAILED' and first['dispatched_this_call']
    assert 'verified_capture' not in first and len(fake.calls) == 7
    repeated = parent.capture(task, lease_token=lease, selection=choice)
    assert repeated['status'] == 'FAILED' and not repeated['dispatched_this_call']
    assert len(fake.calls) == 7
    with pytest.raises(ValueError):
        parent.verified_capture(task, lease_token=lease)


def test_parent_expired_lease_blocks_capture_and_handoff(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, parent = _flow(tmp_path, monkeypatch)
    task = engine.get(task_id)
    projection = parent.options(task, lease_token=lease, context=context)['projection']
    with engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET lease_until=? WHERE task_id=?',
                     (engine.clock() - 1, task_id))
    with pytest.raises(ValueError):
        parent.capture(task, lease_token=lease, selection=_selection(projection))
    with pytest.raises(ValueError):
        parent.verified_capture(task, lease_token=lease)
    assert len(fake.calls) == 5
