"""Original GET/checkpoints followed by first-stage QA/localization, closed I/O."""
import json
import time
import threading

import pytest

from shared_platform import workbench_publication_native as native
from shared_platform.native_parent_images import NativeParentImagesAdapter
from shared_platform.publication_paid_requests import PaidRequestBlocked
from test_native_parent_images import images_task, _bind, _runtime, FixtureClient, result_bytes
from test_round1_auto_freeze import public_settings
from test_round1_workspace_freeze import live


def _closed(value, monkeypatch, *, workflow=False):
    from modules.sourcing import brand_image_lingshi_generation as brand, lingshi_client
    from modules.sourcing import localized_image_ocr as ocr, localized_image_lingshi_generation as localized
    class Client(FixtureClient):
        ready = False
        later_ready = False
        gets = []
        def get_media_task(self, task):
            self.gets.append(task)
            if not self.ready and not (self.later_ready and len(self.gets) > 28):
                raise TimeoutError('owned accepted original master pending')
            return super().get_media_task(task)
    FixtureClient.calls = []
    FixtureClient.fail_chat = FixtureClient.bad_json = False
    monkeypatch.setattr(lingshi_client, 'LingshiClient', Client)
    monkeypatch.setattr(brand, '_download_result', result_bytes)
    monkeypatch.setitem(localized.generate_localized_reference_image.__kwdefaults__, 'result_loader', result_bytes)
    regions = [{'region_id': 'text-'+'b'*20, 'source_text': 'Decor', 'bbox': [.1, .1, .4, .2]}]
    monkeypatch.setattr(ocr, 'detect_english_text_regions',
        lambda raw: regions if raw == result_bytes('https://fixture.example/1.png') else [])
    original_script = NativeParentImagesAdapter._script
    def script(adapter, name):
        module = original_script(adapter, name)
        if name == 'prepare_product_images.py':
            monkeypatch.setattr(module, '_download_source', result_bytes)
        return module
    monkeypatch.setattr(NativeParentImagesAdapter, '_script', script)
    history = value['profile'].root.parent / (value['profile'].root.name + '-phase-continuation-history')
    _bind(value, history)
    if workflow:
        value['worker'].adapters['publication'](value['engine'],value['task'],value['token'],value['profile'])
        value['task'] = value['engine'].get(value['task']['task_id'])
        assert value['task']['execution_state'] == 'reconciliation_required'
        attempt = value['task']['checkpoint']['image_attempt']
        assert attempt['state'] == 'unknown' and attempt['number'] == 1
        first = attempt['result']
        assert first['read_recovery_anchor']['attempt_number'] == 1
    else:
        first = value['worker'].images_adapter.execute_images(value['task'], None, [], token=value['token'])
    assert first['status'] == 'unknown' and first['reason'] == 'R2_MASTER_GENERATION_INCOMPLETE'
    directory = history/'reports/product-preparation'/value['task']['scope']['offer_id']
    checkpoints = [json.loads(path.read_bytes()) for path in
        (directory/'brand-image-checkpoints-lingshi').glob('lingshi-brand-v2-*.json')]
    assert len(checkpoints) == 14 and all(row['status'] == 'SUBMITTED' for row in checkpoints)
    assert len({row['task_id'] for row in checkpoints}) == 14
    assert len(FixtureClient.calls) == 14 and all(row['kind'] == 'brand' for row in FixtureClient.calls)
    raw = {path: path.read_bytes() for path in (directory/'paid-requests').glob('raw-*.json')}
    assert len(raw) == 14
    return Client, directory, checkpoints, raw


def test_original_master_get_completion_continues_first_qa_translation_and_six_docs_once(images_task, monkeypatch):
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS
    from shared_platform.native_task_preparation import ExplicitNewTaskWorker
    v = images_task
    client, history, checkpoints, raw = _closed(v, monkeypatch, workflow=True)
    original_r1 = native.read_frozen(v['task'], v['profile'])
    client.later_ready = True
    before = len(client.gets)
    restarted = ExplicitNewTaskWorker(v['engine'],v['profile'],v['worker'].boundary)
    restarted.interval = .02
    try:
        restarted.start()
        deadline = time.monotonic()+60
        current = v['engine'].get(v['task']['task_id'])
        while current['steps'][1]['state'] != 'completed' and time.monotonic() < deadline:
            time.sleep(.02)
            current = v['engine'].get(v['task']['task_id'])
        assert current['steps'][1]['state'] == 'completed', current
    finally:
        restarted.close()
    assert set(client.gets[before:]).issuperset({row['task_id'] for row in checkpoints})
    assert sum(row['kind'] == 'brand' for row in FixtureClient.calls) == 14
    assert any(row['kind'] == 'qa' for row in FixtureClient.calls)
    assert any(row['kind'] == 'text' for row in FixtureClient.calls)
    assert any(row['kind'] == 'localized' for row in FixtureClient.calls)
    directory = v['profile'].root/'reports/product-preparation'/v['task']['scope']['offer_id']
    assert all((directory/name).is_file() for name in R2_DOCUMENTS.values())
    assert native.read_images(current, v['profile'])['native_r2'] == current['steps'][1]['checkpoint']['native_r2']
    assert native.read_frozen(current, v['profile']) == original_r1
    assert all(path.read_bytes() == body for path, body in raw.items())
    before_calls = list(FixtureClient.calls)
    assert restarted.images_adapter.queue_retained_recovery(current) is False
    assert FixtureClient.calls == before_calls
    summary = json.loads((directory/'paid-request-summary.json').read_bytes())
    assert summary['occupied'] == summary['confirmed'] == len(FixtureClient.calls)
    assert summary['unknown'] == 0 and summary['occupied'] <= 40
    events = v['engine'].store.events(v['task']['task_id'])
    queued = [e for e in events if e['event_type']=='r2_original_media_read_recovery_queued']
    assert len(queued) == 2 and sorted(e['detail']['read_cycle'] for e in queued) == [1,2]
    attempts = [e['detail']['checkpoint']['image_attempt'] for e in events
        if e['event_type']=='checkpoint_saved' and 'image_attempt' in e['detail']['checkpoint']]
    assert attempts and {a['number'] for a in attempts} == {1}
    assert len(v['child']) == 1


@pytest.mark.parametrize('blocker', ['pending-master', 'expired-lease', 'existing-unknown', 'reserved-next-business'])
def test_unproved_or_retained_phase_cannot_start_new_request_after_recovery(images_task, monkeypatch, blocker):
    v = images_task
    client, directory, checkpoints, raw = _closed(v, monkeypatch)
    if blocker != 'pending-master':
        client.ready = True
    if blocker == 'expired-lease':
        with v['engine'].transaction() as db:
            db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?', (v['task']['task_id'],))
    elif blocker in {'existing-unknown', 'reserved-next-business'}:
        with _runtime(v, directory.parents[2]) as runtime:
            context = runtime.paid_context
            key = context.reserve(purpose='image_quality_assurance', model='fixture-qa',
                business={'offer_id': context.offer_id, 'phase': 'owned-existing-qa'},
                request={'messages': ['original retained request'], 'model': 'fixture-qa'})
            if blocker == 'existing-unknown':
                def unknown():
                    raise TimeoutError('owned original QA lost reply')
                with pytest.raises(TimeoutError): context.invoke(key, unknown)
            retained = context.path.read_bytes()
    before = list(FixtureClient.calls)
    recovered = NativeParentImagesAdapter(v['engine'], v['profile'], v['worker'].boundary).recover_images(v['task'], token=v['token'])
    assert recovered['status'] == 'unknown', recovered
    assert FixtureClient.calls == before
    assert all(path.read_bytes() == body for path, body in raw.items())
    if blocker in {'existing-unknown', 'reserved-next-business'}:
        # Original image GET may enrich/confirm its own accepted receipt; the
        # retained QA reservation itself must remain exact and consume no POST.
        assert context.entry(key)['state'] == ('UNKNOWN' if blocker == 'existing-unknown' else 'RESERVED')
        assert context.path.read_bytes().startswith(retained)


def test_first_phase_cannot_relabel_a_completed_original_business_into_another_request(images_task):
    from shared_platform.native_parent_images import _ContinuationPaidContext
    v = images_task
    history = v['profile'].root.parent/'same-business-history'
    with _runtime(v,history) as runtime:
        original = runtime.paid_context
        business = {'offer_id':original.offer_id,'phase':'qa','batch':1}
        key = original.reserve(purpose='image_quality_assurance',model='fixture-qa',
            business=business,request={'messages':['original exact request']})
        calls = []
        original.invoke(key,lambda: calls.append('original POST') or {'choices':[]})
        original.record(key,'CONFIRMED')
        body = original.path.read_bytes()
        first = _ContinuationPaidContext(runtime=runtime,offer_id=original.offer_id,
            round1=original.round1,policy=original.policy,reports_root=runtime.paid_reports_root)
        first.allowed_purpose = 'image_quality_assurance'
        with pytest.raises(PaidRequestBlocked,match='R2_CONTINUATION_ORIGINAL_BUSINESS_EXISTS'):
            first.reserve(purpose='image_quality_assurance',model='fixture-qa',
                business=business,request={'messages':['changed request same original business']})
        assert original.path.read_bytes() == body and calls == ['original POST']
        assert first.summary()['occupied'] == 1


def test_same_business_cli_race_is_rechecked_inside_original_reservation_lock(images_task, monkeypatch):
    from shared_platform.native_parent_images import _ContinuationPaidContext
    from shared_platform.publication_paid_requests import PaidRequestContext
    v = images_task
    history = v['profile'].root.parent/'competing-business-history'
    with _runtime(v,history) as runtime:
        first = _ContinuationPaidContext(runtime=runtime,offer_id=runtime.paid_context.offer_id,
            round1=runtime.paid_context.round1,policy=runtime.paid_context.policy,
            reports_root=runtime.paid_reports_root)
        first.allowed_purpose = 'image_quality_assurance'
        original_cli = PaidRequestContext(offer_id=first.offer_id,round1=first.round1,
            policy=first.policy,reports_root=runtime.paid_reports_root)
        business = {'offer_id':first.offer_id,'phase':'master-qa','brand_id':'livelyhive-sea','batch':1}
        begin, attempted, finished = threading.Event(), threading.Event(), threading.Event()
        failures, rejections, posts = [], [], []
        cli_key = original_cli._key
        def keyed_competitor(**kwargs):
            value = cli_key(**kwargs)
            attempted.set()  # Original CLI has authorized and derived its key.
            return value
        monkeypatch.setattr(original_cli, '_key', keyed_competitor)
        def compete():
            try:
                assert begin.wait(5)
                try:
                    key = original_cli.reserve(purpose='image_quality_assurance',model='fixture-qa',
                        business=business,request={'messages':['competing original CLI request']})
                except PaidRequestBlocked as error:
                    rejections.append(str(error))
                else:
                    original_cli.invoke(key,lambda: posts.append('duplicate CLI POST') or {'choices':[]})
            except BaseException as error:
                failures.append(error)
            finally:
                finished.set()
        original_check = runtime.checked_root
        checks = []
        def race_at_actual_append_boundary():
            value = original_check()
            checks.append(value)
            if len(checks) == 2:
                # This second real lease/source check follows all first-stage
                # predicates and precedes RESERVE. The old two-lock version
                # called it after releasing its first lock: CLI would finish.
                begin.set()
                assert attempted.wait(5)
                assert not finished.wait(.1), 'original journal lock released before RESERVE'
            return value
        monkeypatch.setattr(runtime,'checked_root',race_at_actual_append_boundary)
        contender = threading.Thread(target=compete)
        contender.start()
        try:
            key = first.reserve(purpose='image_quality_assurance',model='fixture-qa',
                business=business,request={'messages':['same business different native request']})
        finally:
            contender.join(5)
        assert len(checks) == 2 and not contender.is_alive() and failures == [] and posts == []
        assert finished.is_set() and rejections == [
            'same business has an unknown paid attempt; changing prompt cannot bypass it']
        assert first.entry(key)['state'] == 'RESERVED'
        assert first.summary()['occupied'] == 1
        rows = [json.loads(line) for line in first.path.read_bytes().splitlines()]
        assert sum(row['event']=='RESERVE' for row in rows) == 1


@pytest.mark.parametrize('drift', ['target', 'source', 'lease'])
def test_native_translation_scope_rechecks_original_frozen_source_target_and_actual_lease(images_task, drift):
    v = images_task
    history = v['profile'].root.parent / ('translation-scope-history-' + drift)
    with _runtime(v, history) as runtime:
        path = runtime.reports_root / runtime.frozen['offer_id'] / 'first-review.json'
        body = path.read_bytes()
        document = json.loads(body)
        assert document['status'] == 'DECISION_REQUIRED'
        assert runtime.translation_targets(runtime.frozen['offer_id'], document) == [
            target for target in runtime.paid_context.round1['canonical_targets'] if target != 'miaoshou:COMMON']
        before = runtime.paid_context.path.read_bytes()
        if drift == 'target':
            document['target_selection']['requested'].remove('tiktok:LH_MY')
        elif drift == 'source':
            changed = dict(document, product_center_revision=document['product_center_revision'] + 1)
            path.write_text(json.dumps(changed), encoding='utf-8')
        else:
            with v['engine'].transaction() as db:
                db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?', (v['task']['task_id'],))
        expected = {'target': '^R2_TRANSLATION_CURRENT_SOURCE_CHANGED$',
                    'source': '^NATIVE_R1_FREEZE_INCOMPLETE$',
                    'lease': '^stale or invalid lease$'}[drift]
        with pytest.raises(ValueError, match=expected):
            runtime.translation_targets(runtime.frozen['offer_id'], document)
        assert runtime.paid_context.path.read_bytes() == before
        assert not (path.parent / 'brand-image-translation-plan.json').exists()
        if drift != 'source':
            assert path.read_bytes() == body


def test_legacy_translation_scope_still_requires_original_review_status(images_task, monkeypatch):
    v = images_task
    adapter = v['worker'].images_adapter
    producer = adapter._script('prepare_product_images.py')
    monkeypatch.setattr(producer, 'REPO_ROOT', v['profile'].root)
    directory = v['profile'].root / 'reports/product-preparation' / v['task']['scope']['offer_id']
    body = (directory / 'first-review.json').read_bytes()
    assert json.loads(body)['status'] == 'DECISION_REQUIRED'
    with pytest.raises(ValueError, match='^first review is not ready for translation scope approval$'):
        producer.freeze_conversation_approved_translation_scope(v['task']['scope']['offer_id'],
            generation={}, selected_review_numbers=[], dimension_only_numbers=[], approved_by='Kyle')
    assert (directory / 'first-review.json').read_bytes() == body
    assert not (directory / 'brand-image-translation-plan.json').exists()


def test_complete_master_report_without_original_artifact_cannot_authorize_first_qa(images_task, monkeypatch):
    from modules.sourcing.image_generation_checkpoint import ImageCheckpoint
    from shared_platform.native_parent_images import NativeImageRuntime
    v = images_task
    client, directory, checkpoints, raw = _closed(v,monkeypatch)
    client.ready = True
    original_continue = NativeImageRuntime._continue_phase
    class OwnedInterrupted(BaseException):
        pass
    def interrupt_before_first_qa(*args):
        raise OwnedInterrupted()
    monkeypatch.setattr(NativeImageRuntime,'_continue_phase',interrupt_before_first_qa)
    with pytest.raises(OwnedInterrupted):
        v['worker'].images_adapter.recover_images(v['task'],token=v['token'])
    report = v['profile'].root/'reports/product-preparation'/v['task']['scope']['offer_id']/'brand-image-generation.json'
    value = json.loads(report.read_bytes())
    assert value['status'] == 'BRAND_IMAGE_REVIEW_REQUIRED' and len(value['assets']) == 14
    original_posts = list(FixtureClient.calls)
    cp = ImageCheckpoint.from_path(value['assets'][0]['checkpoint_path'])
    cp.output_path.write_bytes(b'owned corrupted retained artifact')
    monkeypatch.setattr(NativeImageRuntime,'_continue_phase',original_continue)
    actual = v['worker'].images_adapter.recover_images(v['task'],token=v['token'])
    assert actual['status'] == 'unknown', actual
    assert FixtureClient.calls == original_posts
    assert all(path.read_bytes() == body for path,body in raw.items())
    assert not any(row['kind'] in {'qa','text','localized'} for row in FixtureClient.calls)
