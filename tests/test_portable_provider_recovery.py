from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from modules.sourcing.image_generation_checkpoint import digest
from modules.tools import duoplus, tikhub


def scope(**changes):
    return duoplus.install_scope(**dict(tenant_id='tenant-a', profile_digest='a'*64, origin=duoplus.DEFAULT_BASE_URL,
        image_ids=['device-a'], app_id='app-a', app_version_id='version-a', package='com.example.app', **changes))


def authorization(bound):
    return {'authorization_id': 'prior-user-request', 'instruction_ref': 'fixture://prior-approved-scope', 'scope': bound}


class FakeDuo:
    base_url = duoplus.DEFAULT_BASE_URL
    def __init__(self): self.installs = []; self.fail = False; self.crash = False; self.present = True; self.mismatch = False
    def apps(self, **kwargs):
        return {'code': 200, 'data': {'total_page': 1, 'list': [{'id': 'app-a', 'pkg': 'com.example.other' if self.mismatch else 'com.example.app',
            'version_list': [{'id': 'version-a'}]}]}}
    def post(self, path, payload):
        self.installs.append((path, payload))
        if self.crash: raise SystemExit('simulated process death after provider accepted')
        if self.fail: raise TimeoutError('fixture timeout')
        return {'code': 200, 'data': {'message': 'Success'}}
    def installed_apps(self, device):
        return {'code': 200, 'data': {'list': ['com.example.app'] if self.present else []}}


def run(client, root, bound=None, **kwargs):
    bound = bound or scope()
    return duoplus.execute_install(client, artifact_root=root, scope=bound, authorization=authorization(bound), **kwargs)


def test_duoplus_repeated_existing_authorization_installs_once_and_preserves_version_limit(tmp_path):
    client = FakeDuo()
    first = run(client, tmp_path); second = run(client, tmp_path)
    assert len(client.installs) == 1
    assert first['new_install_request_count'] == 1 and second['new_install_request_count'] == 0
    assert second['status'] == 'PACKAGE_PRESENT_VERSION_UNVERIFIED'
    assert second['readbacks'][0]['version_verified'] is False


@pytest.mark.parametrize('crash', [False, True])
def test_duoplus_unknown_and_crash_do_not_repeat_when_package_is_present(tmp_path, crash):
    client = FakeDuo(); client.crash = crash; client.fail = not crash
    with pytest.raises((duoplus.DuoPlusError, SystemExit)): run(client, tmp_path)
    result = run(client, tmp_path, reconcile_only=True)
    assert len(client.installs) == 1 and result['status'] == 'UNKNOWN'
    assert result['readbacks'][0]['package_present'] is True
    assert result['new_install_request_count'] == 0


def test_duoplus_unknown_device_blocks_changed_app_scope(tmp_path):
    client = FakeDuo(); client.fail = True
    with pytest.raises(duoplus.DuoPlusError): run(client, tmp_path)
    bound = scope(); bound['app_id'] = 'app-b'
    with pytest.raises(duoplus.DuoPlusError, match='unresolved'): run(client, tmp_path, bound)
    assert len(client.installs) == 1


def test_duoplus_exact_scope_rejects_other_tenant_authorization_before_reads(tmp_path):
    client = FakeDuo(); wrong = scope(); wrong['tenant_id'] = 'tenant-b'
    with pytest.raises(duoplus.DuoPlusError, match='authorization'):
        duoplus.execute_install(client, artifact_root=tmp_path, scope=scope(), authorization=authorization(wrong))
    assert not client.installs and not list(tmp_path.iterdir())


def test_duoplus_app_catalog_mismatch_cannot_install(tmp_path):
    client = FakeDuo(); client.mismatch = True
    with pytest.raises(duoplus.DuoPlusError, match='app list'): run(client, tmp_path)
    assert not client.installs


def test_duoplus_concurrent_same_scope_submits_once(tmp_path):
    client = FakeDuo()
    with ThreadPoolExecutor(max_workers=2) as workers:
        rows = list(workers.map(lambda _: run(client, tmp_path), range(2)))
    assert len(client.installs) == 1 and sum(r['new_install_request_count'] for r in rows) == 1


def test_duoplus_damaged_ledger_is_retained(tmp_path):
    client = FakeDuo(); first = run(client, tmp_path)
    from pathlib import Path
    ledger = Path(first['ledger_path']); ledger.write_text('{broken', encoding='utf-8')
    with pytest.raises(duoplus.DuoPlusError, match='damaged'): run(client, tmp_path)
    assert ledger.read_text(encoding='utf-8') == '{broken' and len(client.installs) == 1


def tikhub_scope(plan):
    return {'tenant_id': 'tenant-a', 'profile_digest': 'a'*64, 'plan_digest': digest(plan), 'maximum_paid_requests': 2}


def collect(root, fn):
    plan = tikhub.preview(keyword='fixture', region='TH', video_count=20)
    return tikhub.collect(plan=plan, tenant_id='tenant-a', profile_digest='a'*64, artifact_root=root,
                          authorization=authorization(tikhub_scope(plan)), api_key='fixture-only', request_fn=fn)


def test_tikhub_two_calls_are_durable_and_resume_with_zero_new_calls(tmp_path):
    calls = []
    def fake(path, params, key): calls.append(path); return {'data': []}, {'endpoint': path, 'attempts': [{'attempt': 1}]}
    first = collect(tmp_path, fake); second = collect(tmp_path, fake)
    assert len(calls) == 2 and first['new_paid_request_count'] == 2 and second['new_paid_request_count'] == 0
    assert second['paid_request_count'] == 2 and second['rights'] == 'reference_only_no_publication_authority'


def test_tikhub_second_unknown_retains_first_raw_and_never_requeries(tmp_path):
    calls = []
    def fake(path, params, key):
        calls.append(path)
        if len(calls) == 2: raise TimeoutError()
        return {'data': ['retained']}, {'endpoint': path}
    with pytest.raises(RuntimeError, match='unknown'): collect(tmp_path, fake)
    raw = next(tmp_path.rglob('products.raw.json')).read_bytes()
    with pytest.raises(RuntimeError, match='unknown'): collect(tmp_path, fake)
    assert len(calls) == 2 and next(tmp_path.rglob('products.raw.json')).read_bytes() == raw


@pytest.mark.parametrize('count', [0, 21, True])
def test_tikhub_rejects_unbounded_or_bool_count(count):
    with pytest.raises(ValueError): tikhub.preview(keyword='fixture', region='TH', video_count=count)


def test_tikhub_analysis_preserves_sample_not_market_totals_and_missing_values():
    empty = tikhub.analyze({'products': [], 'videos': []})
    assert empty['products']['sum_not_market_total'] is None and empty['videos']['median_views'] is None
    result = tikhub.analyze({'products': [{'sold_count': 0}, {'sold_count': 0}], 'videos': [{'play_count': None}]})
    assert result['products']['sum_not_market_total'] == 0 and result['products']['top3_share_of_sample_sum'] is None
