import json,tempfile,unittest
from pathlib import Path
from datetime import date
from domains.data_operations.profit_settlement.frozen_monthly_fx import load_frozen_monthly_fx
class FrozenFxTests(unittest.TestCase):
 def payload(self):return {'schema_version':'monthly-average-fx/v1','period':{'start':'2026-08-01','end':'2026-08-31'},'calendar_day_count':31,'snapshot_id':'synthetic','provider':'fixture','methodology':'calendar average','as_of':'2026-08-31T23:59:59+00:00','rates_cny':{'CNY':'1','THB':'0.2'},'sample_count_by_currency':{'THB':31}}
 def load(self,payload):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'fx.json';p.write_text(json.dumps(payload),encoding='utf-8');return load_frozen_monthly_fx(p,date(2026,8,1),date(2026,8,31))
 def test_exact_complete_fixture(self):
  fx,h=self.load(self.payload());self.assertEqual(str(fx.snapshot.get('THB')),'0.2');self.assertEqual(len(h),64)
 def test_wrong_period(self):
  p=self.payload();p['period']['end']='2026-08-30'
  with self.assertRaises(ValueError):self.load(p)
 def test_missing_day(self):
  p=self.payload();p['sample_count_by_currency']['THB']=30
  with self.assertRaises(ValueError):self.load(p)
 def test_nan(self):
  p=self.payload();p['rates_cny']['THB']='NaN'
  with self.assertRaises(ValueError):self.load(p)
 def test_wrong_schema(self):
  p=self.payload();p['schema_version']='latest-fx'
  with self.assertRaises(ValueError):self.load(p)
if __name__=='__main__':unittest.main()
