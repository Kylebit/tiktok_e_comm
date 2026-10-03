"""Owned, closed provider seams around real parent leases and original R2 producers."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

import pytest

from shared_platform import publication_rounds, workbench_publication_native as native
from shared_platform.native_parent_images import NativeImageRuntime, NativeParentImagesAdapter
from shared_platform.publication_paid_requests import PaidRequestBlocked, load_paid_context
from test_native_parent_facts import _leased, _child
from test_round1_auto_freeze import public_settings
from test_round1_workspace_freeze import live
from test_publication_paid_entry import FixtureClient, ROLES, result_bytes

SOURCE = Path(__file__).resolve().parents[1]
R2_SOURCE_PATHS = (
    'shared_platform/native_parent_images.py',
    'shared_platform/native_task_preparation.py',
    'shared_platform/operations_publication.py',
    'shared_platform/publication_rounds.py',
    'modules/sourcing/new_product_workbench.py',
    'modules/sourcing/localized_image_auto_translation.py',
    'skills/prepare-product-images/scripts/prepare_product_images.py',
    'skills/prepare-product-images/scripts/run_automated_image_qa.py',
)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def _image_plan():
    return {'schema_version': 'first-review-image-plan/v1', 'status': 'PROPOSED',
        'source_actions': [], 'generated_assets': [],
        'translation_plan': {'status': 'DEFERRED_UNTIL_ALL_IMAGES_GENERATED',
            'decision_basis': 'REVIEW_ALL_GENERATED_IMAGES_FIRST', 'note': 'Review original generated assets first'},
        'brand_plans': [{'id': brand, 'label': brand, 'target_group': brand,
            'generation_mode': 'NEW_SET_VIA_IMAGE_API', 'positioning': 'Owned wall decal image plan',
            'reference_positions': [1],
            'generated_assets': [{'role': role, 'quantity': 1, 'brief': 'Faithful owned ' + role} for role in ROLES],
            'category_guidance': [{'platform': 'tiktok', 'category_id': '600338',
                'category_zh': '墙贴', 'recommendation': 'Show actual wall decal and three room scenes'}]}
            for brand in ('livelyhive-sea', 'homebloom-sea')],
        'summary': {'translation_positions': [], 'localized_output_count': 0,
                    'net_new_output_count': 14, 'paid_generation_required': True}}


@pytest.fixture
def images_task(live, monkeypatch):
    # The real R1 helper commits its original 17 fixed sources and calculates
    # runtime_manifest over all actual copied R2 sources too, without bypassing
    # runtime_matches or adding credentials/data to that Git snapshot.
    for name in R2_SOURCE_PATHS:
        destination = live['root'] / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((SOURCE / name).read_bytes())
        assert hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256((SOURCE / name).read_bytes()).digest()
    _write(live['root'] / 'config/product_publication_autopilot_policy.json',
           json.loads((SOURCE / 'config/product_publication_autopilot_policy.json').read_bytes()))
    _write(live['root'] / 'config/lingshi.local.json', {'image_qa_model': 'fixture-qa'})
    engine, profile, worker, token, _ = _leased(live, monkeypatch)
    child = _child(monkeypatch, _image_plan())
    task = engine.get(next(iter(worker._task_ids)))
    worker.adapters['publication'](engine, task, token, profile)
    task = engine.get(task['task_id'])
    assert task['steps'][0]['state'] == 'completed' and task['current_step'] == 'images'
    assert native.read_frozen(task, profile) == task['steps'][0]['checkpoint']['native_r1']
    assert not task['required_action'] and len(child) == 1
    assert type(worker.images_adapter) is NativeParentImagesAdapter
    value = {'live': live, 'engine': engine, 'profile': profile, 'worker': worker,
             'task': task, 'token': token, 'child': child}
    try:
        yield value
    finally:
        worker.close()


def _bind(value, history):
    history.mkdir(parents=True, exist_ok=True)
    _write(value['profile'].root / 'config/product_publication_paid_history.local.json',
        {'schema_version': 'product-paid-history-roots/v1',
         'service_root': str(value['profile'].root), 'paid_history_root': str(history)})


@contextmanager
def _runtime(value, history, *, read_only=False):
    _bind(value, history)
    adapter = value['worker'].images_adapter
    with adapter._hold(value['task'], value['token']) as (frozen, root, digest):
        yield NativeImageRuntime(adapter, value['task'], value['token'], frozen,
            history_root=root, history_digest=digest, read_only=read_only)


def _request(context, number=0):
    return context.reserve(purpose='brand_image_generation', model='gpt-image-2',
        business={'offer_id': context.offer_id, 'brand': 'livelyhive-sea', 'role': 'owned-'+str(number)},
        request={'prompt': 'Owned fixed source '+str(number), 'source_sha256': 'a'*64})


def _original_cli_context(value, history):
    """Use the unchanged CLI factory at the old root before native consumption."""
    offer = value['task']['scope']['offer_id']
    current = value['profile'].root
    snapshot = publication_rounds.load_round1_snapshot(offer,
        reports_root=current / 'reports/product-preparation')
    history.mkdir(parents=True, exist_ok=True)
    _write(history / 'config/product_publication_autopilot_policy.json',
           json.loads((current / 'config/product_publication_autopilot_policy.json').read_bytes()))
    _write(history / 'reports/product-preparation' / offer / 'round1-approved-snapshot.json', snapshot)
    context = load_paid_context(offer_id=offer, round1=snapshot, repo_root=history)
    assert type(context).__name__ == 'PaidRequestContext'
    context.ensure_ready()
    return context


def test_default_images_factory_requires_installed_history_before_empty_baseline_or_provider(images_task):
    v = images_task
    v['worker'].adapters['publication'](v['engine'], v['task'], v['token'], v['profile'])
    actual = v['engine'].get(v['task']['task_id'])
    failures = [e for e in v['engine'].store.events(actual['task_id']) if e['event_type'] == 'execution_failed']
    assert actual['execution_state'] == 'failed' and not actual['required_action']
    assert failures[0]['detail']['reason'] == 'R2_PAID_HISTORY_ROOT_BINDING_REQUIRED'
    assert not (v['profile'].root / 'reports/product-preparation' / v['task']['scope']['offer_id'] / 'paid-requests/events.jsonl').exists()
    assert len(v['child']) == 1 and len(v['live']['fake'].calls) == 7


@pytest.mark.parametrize('damage', ['lease', 'config', 'frozen'])
def test_native_reserved_request_rechecks_current_lease_source_before_original_invoke_without_post(images_task, damage):
    v = images_task
    history = v['profile'].root.parent / ('paid-original-'+damage)
    with _runtime(v, history) as runtime:
        key = _request(runtime.paid_context)
        if damage == 'lease':
            with v['engine'].transaction() as db:
                db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?', (v['task']['task_id'],))
        elif damage == 'config':
            _write(runtime.config_path, {'image_qa_model': 'changed'})
        else:
            snapshot = runtime.reports_root / runtime.frozen['offer_id'] / 'round1-approved-snapshot.json'
            snapshot.write_bytes(snapshot.read_bytes().replace(b'APPROVED', b'CORRUPTED', 1))
        calls = []
        with pytest.raises((ValueError, PaidRequestBlocked)):
            runtime.paid_context.invoke(key, lambda: calls.append('POST') or {'data': {'task_id': 1}})
        assert calls == [] and runtime.paid_context.entry(key)['state'] == 'RESERVED'
        assert runtime.paid_context.summary()['occupied'] == 1


def test_native_history_mapping_keeps_same_offer_prior_unknown_and_cap_across_new_source_root(images_task):
    v = images_task
    history = v['profile'].root.parent / 'canonical-paid-history'
    original = _original_cli_context(v, history)
    key = _request(original)
    calls = []
    def lost_reply():
        calls.append('POST')
        raise TimeoutError('owned lost reply')
    with pytest.raises(TimeoutError): original.invoke(key, lost_reply)
    raw = original.path.read_bytes()
    assert original.summary()['unknown'] == 1
    with _runtime(v, history, read_only=True) as recovered:
        assert recovered.root != history and recovered.paid_context.path.read_bytes() == raw
        assert recovered.paid_context.summary()['occupied'] == recovered.paid_context.summary()['unknown'] == 1
        with pytest.raises(PaidRequestBlocked, match='R2_RECOVERY_CANNOT_RESERVE_OR_POST'):
            _request(recovered.paid_context, 1)
        with pytest.raises(PaidRequestBlocked, match='R2_RECOVERY_CANNOT_RESERVE_OR_POST'):
            recovered.paid_context.invoke(key, lost_reply)
        assert calls == ['POST'] and recovered.paid_context.path.read_bytes() == raw
        assert not (recovered.reports_root / recovered.frozen['offer_id'] / 'paid-requests/events.jsonl').exists()


def test_actual_invoke_callback_rechecks_expired_lease_after_attempted_and_preserves_unknown(images_task, monkeypatch):
    v = images_task
    history = v['profile'].root.parent / 'late-lease-history'
    with _runtime(v, history) as runtime:
        key = _request(runtime.paid_context)
        original_append = runtime.paid_context._append
        def expire_after_actual_attempt(rows, event, **kwargs):
            result = original_append(rows, event, **kwargs)
            if event == 'ATTEMPTED':
                with v['engine'].transaction() as db:
                    db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?', (v['task']['task_id'],))
            return result
        monkeypatch.setattr(runtime.paid_context, '_append', expire_after_actual_attempt)
        calls = []
        with pytest.raises(ValueError, match='stale or invalid lease'):
            runtime.paid_context.invoke(key, lambda: calls.append('POST') or {'data': {'task_id': 1}})
        assert calls == [] and runtime.paid_context.entry(key)['state'] == 'UNKNOWN'
        assert runtime.paid_context.summary()['occupied'] == runtime.paid_context.summary()['unknown'] == 1


@pytest.mark.parametrize('leaf', ['config', 'producer'])
def test_native_phase_rechecks_same_bytes_multilink_leaf_without_a_provider_call(images_task, leaf):
    from shared_platform.native_task_preparation import R1FilesystemUnavailable
    v = images_task
    history = v['profile'].root.parent / ('linked-leaf-history-'+leaf)
    with _runtime(v, history) as runtime:
        key = _request(runtime.paid_context)
        path = runtime.config_path if leaf == 'config' else v['profile'].root / 'skills/prepare-product-images/scripts/prepare_product_images.py'
        body = path.read_bytes()
        twin = path.with_name(path.name+'.owned-link')
        os.link(path, twin)
        assert path.read_bytes() == body and path.stat().st_nlink == 2
        calls = []
        try:
            if leaf == 'config':
                with pytest.raises(R1FilesystemUnavailable, match='R1_PARENT_FILE_REPARSE_OR_MULTILINK'):
                    runtime.paid_context.invoke(key, lambda: calls.append('POST'))
            else:
                with pytest.raises(ValueError, match='R2_PRODUCER_SOURCE_REDIRECTED'):
                    v['worker'].images_adapter._script('prepare_product_images.py')
            assert calls == [] and runtime.paid_context.entry(key)['state'] == 'RESERVED'
            assert runtime.paid_context.summary()['occupied'] == 1
        finally:
            twin.unlink()


def test_native_context_restored_known_request_replays_original_wire_without_a_second_post(images_task):
    v = images_task
    history = v['profile'].root.parent / 'canonical-known-history'
    old = _original_cli_context(v, history)
    calls = []
    args = {'purpose': 'image_quality_assurance', 'model': 'fixture-qa',
        'messages': [{'role': 'user', 'content': 'Owned exact immutable image QA request'}],
        'business': {'offer_id': old.offer_id, 'phase': 'qa'},
        'call': lambda: calls.append('POST') or {'choices': [{'message': {'content': 'original'}}]}}
    first = old.chat(**args)
    original = old.path.read_bytes()
    with _runtime(v, history, read_only=True) as recovered:
        assert recovered.paid_context.chat(**args) == first
        assert calls == ['POST'] and recovered.paid_context.path.read_bytes() == original
        assert recovered.paid_context.summary()['occupied'] == 1


def test_service_history_foreign_binding_cannot_reset_original_unknown_into_empty_current_root(images_task):
    v = images_task
    history = v['profile'].root.parent / 'original-foreign-history'
    with _runtime(v, history) as runtime:
        _request(runtime.paid_context)
        original = runtime.paid_context.path.read_bytes()
    path = v['profile'].root / 'config/product_publication_paid_history.local.json'
    binding = json.loads(path.read_bytes())
    binding['service_root'] = str(history)
    _write(path, binding)
    result = v['worker'].images_adapter.execute_images(v['task'], None, [], token=v['token'])
    assert result['status'] == 'blocked' and result['reason'] == 'R2_PAID_HISTORY_ROOT_BINDING_REQUIRED'
    assert (history / 'reports/product-preparation' / v['task']['scope']['offer_id'] / 'paid-requests/events.jsonl').read_bytes() == original
    assert not (v['profile'].root / 'reports/product-preparation' / v['task']['scope']['offer_id'] / 'paid-requests/events.jsonl').exists()


def test_original_product_cap_is_not_reset_by_new_profile_reports_root(images_task):
    v = images_task
    history = v['profile'].root.parent / 'canonical-cap-history'
    old = _original_cli_context(v, history)
    for number in range(40):
        _request(old, number)
    original = old.path.read_bytes()
    assert old.cap == old.summary()['occupied'] == 40
    with _runtime(v, history) as restored:
        with pytest.raises(PaidRequestBlocked, match='PAID_BUDGET_EXHAUSTED'):
            _request(restored.paid_context, 40)
        assert restored.paid_context.path.read_bytes() == original
        assert restored.paid_context.summary()['occupied'] == 40
        assert not (restored.reports_root / restored.frozen['offer_id'] / 'paid-requests/events.jsonl').exists()


def test_native_original_producer_uses_canonical_checkpoint_and_known_task_read_only(images_task, monkeypatch):
    from modules.sourcing import brand_image_lingshi_generation as brand
    v = images_task
    history = v['profile'].root.parent / 'canonical-original-producer'
    class AcceptedPending(FixtureClient):
        gets = []
        def get_media_task(self, task):
            self.gets.append(task)
            raise TimeoutError('owned accepted original task pending')
    FixtureClient.calls = []
    monkeypatch.setattr(brand, '_download_result', result_bytes)
    old = _original_cli_context(v, history)
    source = history / 'owned-reference.png'
    source.write_bytes(result_bytes('https://fixture.example/source.png'))
    directory = old.directory / 'brand-image-checkpoints-lingshi'
    args = {'offer_id': old.offer_id, 'brand_id': 'livelyhive-sea',
        'brand_label': 'livelyhive-sea', 'positioning': 'Owned wall decal image plan',
        'role': 'cover', 'brief': 'Faithful owned cover', 'product_identity': 'Owned frozen wall decal',
        'source_paths': [source], 'checkpoint_dir': directory, 'client': AcceptedPending()}
    with pytest.raises(TimeoutError):
        brand.generate_brand_image(**args, paid_context=old)
    original_posts = list(FixtureClient.calls)
    states = list(directory.glob('lingshi-*.json'))
    assert len(original_posts) == len(states) == 1
    state = json.loads(states[0].read_bytes())
    assert state['status'] == 'SUBMITTED' and state['task_id']
    original_ledger = old.path.read_bytes()
    original_events = [json.loads(line) for line in original_ledger.splitlines()]
    raw_receipts = {path: path.read_bytes() for path in old.root.glob('raw-*.json')}
    assert len(raw_receipts) == 1
    occupied = old.summary()['occupied']
    with _runtime(v, history, read_only=True) as recovered:
        assert recovered.checkpoint_directory(recovered.frozen['offer_id'], 'brand-image-checkpoints-lingshi') == states[0].parent
        before = len(AcceptedPending.gets)
        with pytest.raises(TimeoutError):
            brand.generate_brand_image(**args, paid_context=recovered.paid_context)
        assert AcceptedPending.gets[before:] == [state['task_id']]
        assert FixtureClient.calls == original_posts and recovered.paid_context.summary()['occupied'] == occupied
        recovered_events = [json.loads(line) for line in old.path.read_bytes().splitlines()]
        assert recovered_events[:-1] == original_events
        added = recovered_events[-1]
        assert added['event'] == 'RECEIVED' and added['key'] == original_events[-1]['key']
        assert added['details']['task_id'] == state['task_id']
        assert added['details']['raw_digest'] == original_events[-1]['details']['raw_digest']
        assert all(path.read_bytes() == raw for path, raw in raw_receipts.items())
        after = json.loads(states[0].read_bytes())
        assert {field: after[field] for field in ('status', 'task_id', 'attempt', 'request_digest', 'business_digest')} == {
            field: state[field] for field in ('status', 'task_id', 'attempt', 'request_digest', 'business_digest')}
        assert not (recovered.reports_root / recovered.frozen['offer_id'] / 'brand-image-checkpoints-lingshi').exists()


@pytest.mark.parametrize('text_mode', ['reuse-only', 'one-translatable-master'])
def test_actual_default_worker_images_produces_six_original_documents_and_completes_without_human_gate(images_task, monkeypatch, text_mode):
    from modules.sourcing import brand_image_lingshi_generation as brand, lingshi_client, localized_image_ocr
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS
    v = images_task
    history = v['profile'].root.parent / ('completed-original-images-history-' + text_mode)
    _bind(v, history)
    FixtureClient.calls = []
    FixtureClient.fail_chat = FixtureClient.bad_json = False
    monkeypatch.setattr(lingshi_client, 'LingshiClient', FixtureClient)
    monkeypatch.setattr(brand, '_download_result', result_bytes)
    regions = [{'region_id': 'text-'+'b'*20, 'source_text': 'Decor', 'bbox': [.1, .1, .4, .2]}]
    monkeypatch.setattr(localized_image_ocr, 'detect_english_text_regions',
        lambda raw: regions if text_mode == 'one-translatable-master' and raw == result_bytes('https://fixture.example/1.png') else [])
    from modules.sourcing import localized_image_lingshi_generation as localized
    monkeypatch.setitem(localized.generate_localized_reference_image.__kwdefaults__, 'result_loader', result_bytes)
    original_script = v['worker'].images_adapter._script
    def closed_script(name):
        module = original_script(name)
        if name == 'prepare_product_images.py':
            monkeypatch.setattr(module, '_download_source', result_bytes)
        return module
    monkeypatch.setattr(v['worker'].images_adapter, '_script', closed_script)
    original_r1 = (v['profile'].root / 'reports/product-preparation' / v['task']['scope']['offer_id'] / 'round1-approved-snapshot.json').read_bytes()
    v['worker'].adapters['publication'](v['engine'], v['task'], v['token'], v['profile'])
    current = v['engine'].get(v['task']['task_id'])
    assert current['steps'][1]['state'] == 'completed' and current['current_step'] == 'release'
    assert not current['required_action'] and len(v['child']) == 1
    receipt = native.read_images(current, v['profile'])
    assert receipt['native_r2'] == current['steps'][1]['checkpoint']['native_r2']
    directory = v['profile'].root / 'reports/product-preparation' / v['task']['scope']['offer_id']
    assert all((directory / name).is_file() for name in R2_DOCUMENTS.values())
    assert (directory / 'round1-approved-snapshot.json').read_bytes() == original_r1
    assert [row['kind'] for row in FixtureClient.calls].count('brand') == 14
    assert all(row['kind'] in {'brand', 'qa', 'text', 'localized'} for row in FixtureClient.calls)
    translation_plan = json.loads((directory / 'brand-image-translation-plan.json').read_bytes())
    translation = json.loads((directory / 'brand-image-translation.json').read_bytes())
    if text_mode == 'one-translatable-master':
        assert {item['review_number'] for item in translation_plan['tasks']} == {1}
        assert any(row['kind'] == 'text' for row in FixtureClient.calls)
        assert any(row['kind'] == 'localized' for row in FixtureClient.calls)
        assert translation['assets'] and all(row['source_review_number'] == 1 for row in translation['assets'])
    else:
        assert not any(row['kind'] in {'text', 'localized'} for row in FixtureClient.calls)
    accounting = json.loads((directory / 'paid-request-summary.json').read_bytes())
    assert accounting['occupied'] == accounting['confirmed'] == len(FixtureClient.calls)
    assert accounting['unknown'] == 0 and accounting['occupied'] <= 40
    assert not (directory / 'paid-requests/events.jsonl').exists()
    assert len(v['live']['fake'].calls) == 7
