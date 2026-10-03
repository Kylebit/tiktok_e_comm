"""Exercise the real no-live consumer with synthetic caches and poisoned I/O."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'skills/delist-products-by-sku/scripts/delist_products_by_sku.py'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def case(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('delist_offline_synthetic', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'REPO_ROOT', tmp_path)
    # Local catalog and Ozon readers have dedicated tests elsewhere. This seam
    # prevents this regression from opening any workstation database/snapshot.
    monkeypatch.setattr(module, '_local_tiktok_rows', lambda skus: [])
    monkeypatch.setattr(module, '_ozon_rows', lambda skus: [])
    from core import auth, shops, api_client, http_retry
    calls = []
    def poison(name):
        def stopped(*args, **kwargs):
            calls.append(name)
            raise RuntimeError('forbidden synthetic I/O: '+name)
        return stopped
    for owner, name in ((auth, 'access_token'), (auth, 'refresh_access_token'),
            (shops, 'list_shops'), (api_client, 'get'), (api_client, 'post'),
            (http_retry, 'urlopen'), (urllib.request, 'urlopen'),
            (socket, 'create_connection'), (socket.socket, 'connect')):
        monkeypatch.setattr(owner, name, poison(name))
    for name in ('_live_tiktok_rows', '_live_shopee_rows', '_live_verify'):
        monkeypatch.setattr(module, name, poison(name))
    return SimpleNamespace(module=module, root=tmp_path, calls=calls, poison=poison,
        auth=auth, shops=shops)


def history(case):
    write(case.root/'reports/product-preparation/synthetic-offer/first-review.json',
        {'seller_sku':'fixture-7001'})
    write(case.root/'reports/product-publication/synthetic-offer/run/description-media-repair.json',
        {'targets':[{'target_label':'tiktok:LH_PH','product_id':'synthetic-product',
            'shop_name':'Synthetic LivelyHive','after':{'status':'ACTIVATE'}}]})


def cached_row(platform='tiktok', *, skus=('7001',)):
    label={'tiktok':'tiktok:LH_PH','shopee':'shopee:PH','ozon':'ozon:RU'}[platform]
    return {'target_label':label,'platform':platform,'product_id':'synthetic-product',
        'shop_cipher':'synthetic-cipher','requested_skus':['7001'],
        'all_product_skus':list(skus),'current_status':'ACTIVATE',
        'identity_source':'synthetic-local-cache','action':'synthetic-action'}


def target(plan, label='tiktok:LH_PH'):
    return next(row for row in plan['targets'] if row['target_label']==label)


def test_no_live_historical_cache_never_authenticates(case):
    """RED on the original consumer: swallowed auth error still records a call."""
    history(case)
    plan = case.module.build_plan(('7001',), live=False)
    assert case.calls == []
    assert all(row['executable'] is False for row in plan['targets'])


def test_no_live_catalog_cache_positive_is_not_ready(case, monkeypatch):
    """RED on the original consumer: a complete cached row became READY."""
    monkeypatch.setattr(case.module, '_local_tiktok_rows', lambda skus:[cached_row()])
    plan = case.module.build_plan(('7001',), live=False)
    row = target(plan)
    assert row['status'] == 'BLOCKED_LIVE_READ_REQUIRED'
    assert row['executable'] is False
    assert case.calls == []


def test_no_live_history_preserves_diagnostic_candidate_without_shop_auth(case):
    history(case)
    plan = case.module.build_plan(('7001',), live=False)
    row = target(plan)
    assert row['product_id'] == 'synthetic-product'
    assert row['identity_source'].startswith('reports/product-publication/')
    assert row['status'] == 'BLOCKED_LIVE_READ_REQUIRED'
    assert not row.get('shop_cipher')
    assert case.calls == []


@pytest.mark.parametrize('platform', ['shopee','ozon'])
def test_other_cached_platform_matches_remain_non_executable(case, monkeypatch, platform):
    helper = '_shopee_rows' if platform=='shopee' else '_ozon_rows'
    monkeypatch.setattr(case.module, helper, lambda skus:[cached_row(platform)])
    plan = case.module.build_plan(('7001',), live=False)
    row = target(plan, 'shopee:PH' if platform=='shopee' else 'ozon:RU')
    assert row['status']=='BLOCKED_LIVE_READ_REQUIRED' and row['executable'] is False
    assert case.calls==[]


def test_missing_cache_blocks_without_asserting_provider_not_found(case):
    plan = case.module.build_plan(('7001',), live=False)
    assert plan['planning_mode']=='OFFLINE_DIAGNOSTIC'
    assert target(plan)['status']=='BLOCKED_MISSING_IDENTITY'
    assert target(plan, 'ozon:RU')['status']=='BLOCKED_MISSING_IDENTITY'
    assert target(plan, 'shopee:PH')['status']=='NEEDS_PROVIDER_DISCOVERY'
    assert all(row['blocked'] is True and row['executable'] is False for row in plan['targets'])
    assert case.calls==[]


@pytest.mark.parametrize('all_skus,status', [
    (['7001','7999'],'BLOCKED_MIXED_PRODUCT'),
    ([], 'BLOCKED_INCOMPLETE_SKU_SET'),
])
def test_offline_still_preserves_exact_sku_blockers(case, monkeypatch, all_skus, status):
    row=cached_row(skus=all_skus)
    row['requested_skus']=all_skus
    monkeypatch.setattr(case.module, '_local_tiktok_rows', lambda skus:[row])
    plan=case.module.build_plan(('7001',),live=False)
    assert target(plan)['status']==status and target(plan)['executable'] is False


def test_execute_rejects_offline_plan_before_operation_ledger_or_provider(case, monkeypatch):
    from shared_platform import operations_domain_guard
    monkeypatch.setattr(operations_domain_guard, 'begin_delisting', case.poison('operation-ledger'))
    monkeypatch.setattr(case.module, '_execute_one', case.poison('execute-row'))
    plan=case.module.build_plan(('7001',), live=False)
    # Even a re-digested accidental READY upgrade cannot convert diagnostic mode.
    plan['targets'][0].update(status='READY', executable=True)
    plan.pop('plan_digest');plan['plan_digest']=case.module._digest(plan)
    with pytest.raises(ValueError, match='OFFLINE_DIAGNOSTIC_PLAN_NOT_EXECUTABLE'):
        case.module.execute(plan)
    assert case.calls==[]


def test_offline_digest_mutation_still_fails_before_mode_or_ledger(case, monkeypatch):
    from shared_platform import operations_domain_guard
    monkeypatch.setattr(operations_domain_guard, 'begin_delisting', case.poison('operation-ledger'))
    plan=case.module.build_plan(('7001',),live=False)
    plan['requested_skus']=['7999']
    with pytest.raises(ValueError, match='plan digest mismatch'):
        case.module.execute(plan)
    assert case.calls==[]


def test_cli_no_live_writes_only_local_diagnostic_plan(case, monkeypatch):
    monkeypatch.setattr('sys.argv', [str(SCRIPT),'plan','--sku','7001','--no-live'])
    assert case.module.main()==0
    output=case.root/'reports/product-delisting/7001/delist-plan.json'
    plan=json.loads(output.read_text(encoding='utf-8'))
    case.module._verify_plan(plan)
    assert plan['planning_mode']=='OFFLINE_DIAGNOSTIC'
    assert all(row['executable'] is False for row in plan['targets'])
    assert [p.relative_to(case.root).as_posix() for p in case.root.rglob('*') if p.is_file()]==[
        'reports/product-delisting/7001/delist-plan.json']
    assert case.calls==[]


def test_live_history_keeps_existing_authorized_shop_binding(case, monkeypatch):
    history(case)
    calls=[]
    monkeypatch.setattr(case.auth, 'access_token', lambda: calls.append('synthetic-token') or 'synthetic')
    monkeypatch.setattr(case.shops, 'list_shops', lambda token:
        calls.append(('synthetic-shops',token)) or [
            {'name':'LivelyHive','region':'PH','id':'synthetic-shop','cipher':'synthetic-cipher'}])
    rows=case.module._historical_tiktok_rows(('7001',))
    assert rows[0]['shop_cipher']=='synthetic-cipher'
    assert calls==['synthetic-token',('synthetic-shops','synthetic')]
    assert case.calls==[]
