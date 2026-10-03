import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def entry(name):
    path = ROOT/'skills/apply-product-discounts/scripts'/f'{name}.py'
    spec = importlib.util.spec_from_file_location('b4b_'+name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('channel', ['tiktok','shopee'])
def test_legacy_ready_forged_digest_and_execute_flag_never_reach_transport(channel, capsys):
    module = entry('execute_official_discount_plan')
    def forbidden(*a, **kw): pytest.fail('legacy writer called a transport')
    module._default_transport = forbidden
    module.shop_post = forbidden
    fn = getattr(module, 'execute_'+channel)
    action = {'target_label':channel+':PH','status':'READY','action_digest':'forged','discount_percent':30}
    for _ in range(2):
        result = fn([action], execute=True)
        assert result['status'] == 'BLOCKED_LEGACY_DIRECT_EXECUTION'
        assert result['external_write_count'] == 0
    assert module.main(['--channel',channel,'--execute','--plan','does-not-exist.json']) == 2
    assert json.loads(capsys.readouterr().out)['external_write_count'] == 0


def test_identical_title_cannot_make_other_brand_product_an_exact_sku(tmp_path, monkeypatch):
    module = entry('recover_miaoshou_product_identity')
    for name in ('OFFICIAL','MIAOSHOU','OUTPUT'): monkeypatch.setattr(module,name,tmp_path/(name+'.json'))
    module.OFFICIAL.write_text(json.dumps({'stores':[{'region':'PH','products':[{'product_id':'official-A','title':'Blue Wall Sticker','seller_skus':['0954'],'main_image':'https://fixture.example/'+'a'*32+'~x'}]}]}))
    module.MIAOSHOU.write_text(json.dumps({'stores':[{'region':'PH','shop_name':'HomeBloom','products':[{'product_id':'unrelated-B','title':'Blue Wall Sticker','main_image':'https://fixture.example/'+'b'*32+'~x'}]}]}))
    assert module.main([]) == 0
    row = json.loads(module.OUTPUT.read_text())['rows'][0]
    assert row['status'] == 'REFERENCE_ONLY'
    assert row['seller_skus'] == [] and row['suggested_seller_skus'] == ['0954']
    assert row['identity_verified'] is False


def test_operator_reference_cannot_become_an_approved_batch(tmp_path, monkeypatch):
    module = entry('build_batch_discount_plan')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'MIAOSHOU_IDENTITY_EVIDENCE', tmp_path/'absent.json')
    monkeypatch.setattr(module, 'EXISTING_SKU_EVIDENCE', tmp_path/'absent2.json')
    report = tmp_path/'fixture.json'
    report.write_text(json.dumps({'source':'tiktok', 'stores':[{'region':'PH','shop_name':'LivelyHive','products':[
        {'product_id':'product-1','seller_skus':['0954'],'title':'Fixture'}], 'activities':[{'activity_id':'activity-1'}]}]}))
    value = module.build_plan([report], policy_discount=30, policy_reference='unapproved-operator-proposal')
    assert value['approval_status'] == 'NOT_APPROVED'
    action = value['actions'][0]
    assert action['status'] == 'REFERENCE_REQUIRES_FROZEN_PLAN'
    assert action['discount_percent'] is None
    assert action['approval_status'] == 'NOT_APPROVED'


def test_browser_reference_preserves_blocked_rows_and_missing_targets():
    module = entry('export_miaoshou_browser_batch')
    value = module.export_batch({'actions':[{'target_label':'tiktok:HB_PH','product_id':'1','status':'BLOCKED_IDENTITY'}]}, {'tiktok:HB_PH','tiktok:GB'})
    assert value['approval_status'] == 'NOT_APPROVED'
    assert value['actions'][0]['source_status'] == 'BLOCKED_IDENTITY'
    assert value['missing_targets'] == ['tiktok:GB']


def test_inspection_keeps_valid_target_when_another_target_lacks_frozen_price():
    from tests.test_b4b_discount_contract import payload
    value = payload()
    value['targets'] = ['tiktok:LH_PH', 'shopee:PH']
    result = entry('inspect_discount_plan').inspect_plan(value)
    assert result['ready'] is False and result['execution_authorized'] is False
    assert len(result['actions']) == 2
    assert result['actions'][0]['ready'] is True
    assert result['actions'][0]['discount_percent'] == 25
    assert result['actions'][1]['ready'] is False
