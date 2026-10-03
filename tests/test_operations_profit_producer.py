from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from test_workbench_profit_adapter import make_bundle
from shared_platform.operations_profit_producer import build_verified_reports
from domains.data_operations.profit_settlement import tiktok, shopee
from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage


def inputs(tmp_path, platform='tiktok', settled_at='2026-08-15T10:00:00+08:00'):
    b = make_bundle(tmp_path / 'inputs')
    entry = {k:v for k,v in b['entry'].items() if k != 'report'}
    entry['platform'] = platform
    source = json.loads(Path(entry['settlement']['path']).read_text())
    source.update(platform=platform)
    source['orders'][0]['settled_at'] = settled_at
    checksum = hashlib.sha256(json.dumps(source['orders'], sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    source.update(checksum=checksum, snapshot_id=platform + '-settlement:' + checksum)
    entry['settlement'] = b['write']('settlement.json', source)
    coverage = build_coverage(orders=source['orders'], settled_order_ids={'o1'}, start=date(2026,8,1), end=date(2026,8,31), as_of=date(2026,9,9), settlement_snapshot_id=source['snapshot_id'], site='MY', timezone_name='Asia/Kuala_Lumpur')
    coverage.update(platform=platform, schema_version=platform+'-order-settlement-coverage/v1')
    for key in ('coverage', 'month_coverage'):
        entry[key] = b['write'](key+'.json', coverage)
    for key in ('orders_source', 'advertising'):
        value = json.loads(Path(entry[key]['path']).read_text())
        value['platform'] = platform
        if key == 'advertising':
            raw = json.loads(Path(value['source_file']['path']).read_text())
            raw['platform'] = platform
            value['source_file'] = b['write']('original-ads.json', raw)
        entry[key] = b['write'](key+'.json', value)
    row = {'shop_id':'shop-MY','region':'MY','order_id':'o1','order_line_id':'l1','platform_sku':'p1','seller_sku':'660001','canonical_sku':'0001',
        'product_name':'Synthetic item','variant_name':'One','quantity':'1','currency':'MYR','net_settlement_amount':'10',
        'buyer_paid_product_amount':'12','buyer_cash_paid_product_amount':'12','product_sales_amount':'12','settlement_status':'settled',
        'occurred_at':'2026-08-01T10:00:00+08:00','settled_at':settled_at,'source_snapshot_id':source['snapshot_id'],
        'fee_items':[],'fulfillment':{'mode':'cross_border','classification_rule':'tiktok_my_sst_nonzero_cross_border/v1','sst_local':'1','evidence_source':'synthetic finance SST'}}
    entry['rows'] = b['write']('rows.json', [row])
    manifest = b['write']('producer-input.json', {'schema_version':'profit-producer-input/v1', 'reports':[entry]})
    return b, manifest, {**b['scope'], 'platforms':[platform]}, row


@pytest.mark.parametrize('platform', ['tiktok', 'shopee'])
def test_fixed_monthly_producer_and_restart_without_second_calculation(tmp_path, monkeypatch, platform):
    b, manifest, scope, row = inputs(tmp_path, platform, settled_at='2026-09-02T10:00:00+08:00')
    module = tiktok if platform == 'tiktok' else shopee
    actual = module.build_monthly_report
    calls = []
    def counted(*args, **kwargs):
        calls.append(kwargs)
        return actual(*args, **kwargs)
    monkeypatch.setattr(module, 'build_monthly_report', counted)
    output = tmp_path / 'fixed-worker-only'
    first = build_verified_reports(manifest, scope, output, {'code_version':'synthetic-release'})
    assert first['status'] == 'VERIFIED' and first['reused'] is False
    report = json.loads(Path(first['reports'][0]['path']).read_text())
    assert report['period']['basis'] == 'order_created_at'
    assert len(report['order_lines']) == 1
    assert report['order_lines'][0]['settled_at'].startswith('2026-09-02')
    assert first['executions'][0]['parameters']['period_basis'] == 'order_created_at'
    assert build_verified_reports(manifest, scope, output, {'code_version':'synthetic-release'})['reused']
    assert len(calls) == 1
    with pytest.raises(ValueError, match='different inputs or release'):
        build_verified_reports(manifest, scope, output, {'code_version':'different-release'})


def test_reject_agent_report_in_producer_input(tmp_path):
    b, manifest, scope, row = inputs(tmp_path)
    data = json.loads(Path(manifest['path']).read_text())
    data['reports'][0]['report'] = b['entry']['report']
    manifest = b['write']('producer-input.json', data)
    with pytest.raises(ValueError, match='unsupported inputs'):
        build_verified_reports(manifest, scope, tmp_path/'fixed', {'code_version':'fixture'})
    assert not (tmp_path/'fixed/producer-receipt.json').exists()


def test_fixed_builder_does_not_accept_wrong_source_quantity(tmp_path):
    b, manifest, scope, row = inputs(tmp_path)
    data = json.loads(Path(manifest['path']).read_text())
    row['quantity'] = '9'
    data['reports'][0]['rows'] = b['write']('rows.json', [row])
    manifest = b['write']('producer-input.json', data)
    with pytest.raises(ValueError, match='SKU/quantity'):
        build_verified_reports(manifest, scope, tmp_path/'fixed', {'code_version':'fixture'})
    assert not (tmp_path/'fixed/producer-receipt.json').exists()
