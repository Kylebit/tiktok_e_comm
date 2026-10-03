"""Evidence completeness/association under concurrency, not financial model tests."""
from concurrent.futures import ThreadPoolExecutor
import ast
import gc
import json
from pathlib import Path
import weakref

import pytest
from fixture_record_log import FixtureRecordLog


def retained(folder, kind):
    return json.loads((folder / (kind + '.json')).read_text(encoding='utf-8'))


def test_every_duplicate_response_is_retained_and_linked_to_its_request(tmp_path):
    log = FixtureRecordLog(tmp_path)
    payload = {'schema_version': 'SYNTHETIC-only', 'nested': {'amount': 6, 'unknown': None},
               'image': 'data:image/png;base64,synthetic'}
    ids = [log.request('POST', '/api/profit-center/captured-review') for _ in range(46)]
    for request_id in reversed(ids):
        log.response(200, payload, '/api/profit-center/captured-review', request_id)
    requests, responses = retained(tmp_path, 'requests'), retained(tmp_path, 'responses')
    assert len(requests) == len(responses) == 46
    assert [r['request_id'] for r in requests] == ids
    assert [r['request_id'] for r in responses] == list(reversed(ids))
    assert all(r['payload'] == payload and r['status'] == 200 for r in responses)
    assert not list(tmp_path.glob('*.tmp'))


def test_concurrent_records_never_interleave_or_lose_response_association(tmp_path):
    log = FixtureRecordLog(tmp_path)
    def exchange(index):
        path = '/synthetic/' + str(index)
        request_id = log.request('POST', path)
        log.response(200, {'index': index}, path, request_id)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(exchange, range(40)))
    requests, responses = retained(tmp_path, 'requests'), retained(tmp_path, 'responses')
    by_id = {r['request_id']: r for r in requests}
    assert len(by_id) == len(requests) == len(responses) == 40
    assert {r['payload']['index'] for r in responses} == set(range(40))
    assert all(by_id[r['request_id']]['path'] == r['path'] for r in responses)


def test_completed_payloads_are_not_retained_in_memory_or_reencoded_as_history(tmp_path, monkeypatch):
    class Payload(dict):
        pass
    payload = Payload({'synthetic': 'x' * 76000})
    previous = weakref.ref(payload)
    log = FixtureRecordLog(tmp_path)
    request_id = log.request('POST', '/synthetic')
    def forbidden_whole_history(*args, **kwargs):
        raise AssertionError('No full-history json.dumps allowed')
    monkeypatch.setattr(json, 'dumps', forbidden_whole_history)
    log.response(200, payload, '/synthetic', request_id)
    del payload
    gc.collect()
    assert previous() is None
    assert retained(tmp_path, 'responses')[0]['payload']['synthetic'] == 'x' * 76000


def test_encoding_failure_preserves_complete_previous_rows_and_does_not_consume_index(tmp_path):
    log = FixtureRecordLog(tmp_path)
    request_id = log.request('POST', '/synthetic')
    log.response(200, {'known': 1}, '/synthetic', request_id)
    before = (tmp_path / 'responses.json').read_bytes()
    with pytest.raises(TypeError):
        log.response(200, {'not_json': object()}, '/synthetic', request_id)
    assert (tmp_path / 'responses.json').read_bytes() == before
    assert log.counts['responses'] == 1
    assert not list(tmp_path.glob('*.tmp'))


def test_unknown_request_cannot_create_unassociated_response(tmp_path):
    log = FixtureRecordLog(tmp_path)
    with pytest.raises(ValueError):
        log.response(200, {'known': 1}, '/synthetic', 1)
    assert retained(tmp_path, 'responses') == []


def test_actual_fixture_json_hook_keeps_exact_wire_status_and_payload(tmp_path):
    root = Path(__file__).resolve().parents[1]
    module = ast.parse((root / 'tests/u05_finance_preview.py').read_text(encoding='utf-8'))
    main = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    guarded = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == 'Guarded')
    hook = next(n for n in guarded.body if isinstance(n, ast.FunctionDef) and n.name == '_json')
    selected = ast.ClassDef(name='Guarded', bases=[ast.Name(id='Base', ctx=ast.Load())], keywords=[],
                            body=[hook], decorator_list=[])
    tree = ast.fix_missing_locations(ast.Module(body=[selected], type_ignores=[]))
    calls = []
    class Base:
        def _json(self, status, payload):
            calls.append((status, payload))
            return 'original-wire-result'
    log = FixtureRecordLog(tmp_path)
    request_id = log.request('POST', '/synthetic')
    namespace = {'Base': Base, 'record_log': log, 'save': lambda: None}
    exec(compile(tree, str(root / 'tests/u05_finance_preview.py'), 'exec'), namespace)
    handler = namespace['Guarded']()
    handler.path = '/synthetic'
    handler.fixture_request_id = request_id
    payload = {'unknown': None, 'amount': 6}
    assert handler._json(200, payload) == 'original-wire-result'
    assert calls == [(200, payload)] and calls[0][1] is payload
    assert retained(tmp_path, 'responses') == [dict(status=200, payload=payload,
                                                   path='/synthetic', request_id=request_id)]
