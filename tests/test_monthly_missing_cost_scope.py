from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import unittest
from copy import deepcopy
from domains.data_operations.profit_settlement.local_catalog import LocalCatalogSnapshot, CatalogQualityIssue
from domains.data_operations.profit_settlement.monthly_missing_cost_scope import resolve_monthly_missing_cost_policy

def catalog(code='ambiguous_seller_sku_scope',candidates=(),records=()):
 return LocalCatalogSnapshot({}, {}, {'0933':candidates}, {}, {}, {}, 'synthetic:test', '2026-09-01T00:00:00+00:00',(CatalogQualityIssue(code,'0933','identity','fixture'),),cost_records_by_sku={'0933':records},blocked_skus=('0933',))
def evidence():
 return {'platform':'tiktok','site':'TH','shop_id':'synthetic-shop','orders':[{'order_id':'order-1','shop_id':'synthetic-shop','region':'TH','order_created_at':'2026-08-03T10:00:00+07:00','settlement_status':'settled','items':[{'platform_sku':'variant-1','seller_sku':'990933'}]}]}
def run(c=None,e=None,allow=True):
 return resolve_monthly_missing_cost_policy(c or catalog(),e or evidence(),{'0933'},site='TH',start=date(2026,8,1),end=date(2026,8,31),allow_missing_default=allow)
class MonthlyCostScopeTests(unittest.TestCase):
 def test_authorized_missing_internal_sku(self):
  result,receipt=run();self.assertEqual(result.values['0933']['unit_cost_cny'],'5');self.assertEqual(receipt['eligible_skus'],['0933']);self.assertEqual(result.warnings[0].code,'missing_cost_default_5_selected')
 def test_without_authority_rejected(self):
  with self.assertRaises(ValueError):run(allow=False)
 def test_conflict_not_high_or_five(self):
  result,receipt=run(catalog('canonical_sku_cost_conflict',(Decimal('5'),Decimal('5.5'))));self.assertNotIn('0933',result.values)
 def test_conflict_even_if_original_unblocked(self):
  c=replace(catalog('conflicting_cost',(Decimal('5'),Decimal('5.5'))),blocked_skus=(),costs_by_sku={'0933':Decimal('5')})
  result,_=run(c);self.assertNotIn('0933',result.values)
 def test_invalid_variant_rejected(self):self.assertNotIn('0933',run(catalog('invalid_variant_identity'))[0].values)
 def test_missing_shop_rejected(self):self.assertNotIn('0933',run(catalog('missing_shop_mapping'))[0].values)
 def test_alias_ambiguity_rejected(self):self.assertNotIn('0933',run(catalog('ambiguous_full_identity_or_alias'))[0].values)
 def test_unknown_issue_rejected(self):self.assertNotIn('0933',run(catalog('future_unknown_issue'))[0].values)
 def test_ambiguous_variant_record_rejected(self):self.assertNotIn('0933',run(catalog(records=({'currency':'CNY','matching_basis':'ambiguous_variant_key'},)))[0].values)
 def test_foreign_currency_record_rejected(self):self.assertNotIn('0933',run(catalog(records=({'currency':'USD','matching_basis':'exact_variant_identity'},)))[0].values)
 def test_missing_same_order_platform_sku(self):
  e=evidence();e['orders'][0]['items'][0]['platform_sku']='';self.assertNotIn('0933',run(e=e)[0].values)
 def test_wrong_shop(self):
  e=evidence();e['orders'][0]['shop_id']='other';self.assertNotIn('0933',run(e=e)[0].values)
 def test_conflicting_same_order_variant(self):
  e=evidence();e['orders'][0]['items'].append({'platform_sku':'variant-1','seller_sku':'990934'});self.assertNotIn('0933',run(e=e)[0].values)
 def test_future_month_not_authorized(self):
  e=evidence();e['orders'][0]['order_created_at']='2026-09-03T10:00:00+07:00';self.assertNotIn('0933',run(e=e)[0].values)
 def test_does_not_mutate_catalog(self):
  c=catalog();run(c);self.assertEqual(c.blocked_skus,('0933',));self.assertEqual(c.costs_by_sku,{})
 def test_cross_statement_seller_conflict_rejected(self):
  e=evidence();other=deepcopy(e['orders'][0]);other['items'][0]['seller_sku']='990934';e['orders'].append(other)
  self.assertNotIn('0933',run(e=e)[0].values)
 def test_cross_statement_raw_identity_conflict_same_suffix_rejected(self):
  e=evidence();other=deepcopy(e['orders'][0]);other['items'][0]['seller_sku']='880933';e['orders'].append(other)
  self.assertNotIn('0933',run(e=e)[0].values)
 def test_repeated_statement_same_identity_allowed(self):
  e=evidence();e['orders'].append(deepcopy(e['orders'][0]));self.assertIn('0933',run(e=e)[0].values)
if __name__=='__main__':unittest.main()
