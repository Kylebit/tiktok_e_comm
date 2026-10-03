import importlib.util
from pathlib import Path
from datetime import date
from copy import deepcopy
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'domains/data_operations/skills/manage-profit-settlement/scripts/build_tiktok_monthly_from_evidence.py'
spec=importlib.util.spec_from_file_location('monthly_binding_builder',SCRIPT)
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
def fixture():
 e={'platform':'tiktok','site':'PH','shop_id':'synthetic','snapshot_id':'e1','orders':[{'order_id':'o1','shop_id':'synthetic','region':'PH','order_created_at':'2026-08-01T01:00:00+08:00','settlement_status':'settled'}]}
 c={'platform':'tiktok','site':'PH','shop_id':'synthetic','settlement_snapshot_id':'e1','snapshot_id':'c1','created_period':{'start':'2026-08-01','end':'2026-08-31','timezone':'Asia/Manila'},'all_non_cancelled_orders_settled':True,'counts':{'created_orders':1,'settled_orders':1,'cancelled_orders':0,'cancelled_with_settlement':0,'cancelled_without_settlement':0,'unsettled_non_cancelled':0},'receipt':{'invalid_order_row_count':0,'pagination_segment_count':1,'pagination_terminal_page_count':1,'pagination_termination':'empty_next_page_token_per_segment'},'settled_orders':[{'order_id':'o1'}]}
 return e,c
def check(e,c):builder._validate_coverage_inputs(e,c,'PH',date(2026,8,1),date(2026,8,31))
class CoverageBindingTests(unittest.TestCase):
 def test_complete_exact_coverage(self):check(*fixture())
 def test_other_shop(self):
  e,c=fixture();c['shop_id']='other'
  with self.assertRaises(ValueError):check(e,c)
 def test_other_period(self):
  e,c=fixture();c['created_period']['start']='2026-07-01'
  with self.assertRaises(ValueError):check(e,c)
 def test_other_snapshot(self):
  e,c=fixture();c['settlement_snapshot_id']='e2'
  with self.assertRaises(ValueError):check(e,c)
 def test_missing_enumeration(self):
  e,c=fixture();c['receipt']={}
  with self.assertRaises(ValueError):check(e,c)
 def test_incomplete_pages(self):
  e,c=fixture();c['receipt']['pagination_terminal_page_count']=0
  with self.assertRaises(ValueError):check(e,c)
 def test_parent_missing_from_coverage(self):
  e,c=fixture();c['settled_orders'][0]['order_id']='other'
  with self.assertRaises(ValueError):check(e,c)
 def test_false_complete(self):
  e,c=fixture();c['counts']['unsettled_non_cancelled']=1;c['counts']['created_orders']=2
  with self.assertRaises(ValueError):check(e,c)
 def test_incomplete_coverage_can_remain_needs_review(self):
  e,c=fixture();c['counts']['unsettled_non_cancelled']=1;c['counts']['created_orders']=2;c['all_non_cancelled_orders_settled']=False;check(e,c)
 def test_cross_statement_conflict_before_adapter(self):
  e,c=fixture();e['orders'][0]['items']=[{'platform_sku':'v1','seller_sku':'990933'}];other=deepcopy(e['orders'][0]);other['items'][0]['seller_sku']='880933';e['orders'].append(other)
  with self.assertRaises(ValueError):check(e,c)
 def test_finance_missing_claimed_settled_parent(self):
  e,c=fixture();e['orders']=[]
  with self.assertRaises(ValueError):check(e,c)
 def test_negative_pagination_segment(self):
  e,c=fixture();c['receipt']['pagination_segment_count']=-1;c['receipt']['pagination_terminal_page_count']=-1
  with self.assertRaises(ValueError):check(e,c)
 def test_boolean_pagination_segment(self):
  e,c=fixture();c['receipt']['pagination_segment_count']=True;c['receipt']['pagination_terminal_page_count']=True
  with self.assertRaises(ValueError):check(e,c)
 def test_boolean_invalid_row_count(self):
  e,c=fixture();c['receipt']['invalid_order_row_count']=False
  with self.assertRaises(ValueError):check(e,c)
 def test_legacy_cli_rejects_v2_coverage_until_migrated(self):
  e,c=fixture();c['schema_version']='tiktok-order-settlement-coverage/v2';c['date_basis']='site_local_order_created_at'
  with self.assertRaisesRegex(ValueError,'cannot consume v2'):check(e,c)
 def test_legacy_cli_rejects_offset_day_different_from_site_day(self):
  e,c=fixture();e['orders'][0]['order_created_at']='2026-07-31T18:30:00+00:00'
  with self.assertRaisesRegex(ValueError,'reconcile as v2'):check(e,c)
if __name__=='__main__':unittest.main()
