from pathlib import Path
from datetime import datetime,timezone,timedelta
import unittest,sys,os,json,hashlib,tempfile,base64,sqlite3
from email.message import Message
from types import SimpleNamespace

PACKAGE=Path(__file__).parent
CODE=Path(os.environ.get('ORBIT_HOME_CANDIDATE_ROOT',str(PACKAGE.parent if (PACKAGE.parent/'shared_platform').is_dir() else PACKAGE/'work'))).resolve()
SAFE=Path(tempfile.mkdtemp(prefix='oh-home-contracts-'))
SAFE.mkdir(parents=True,exist_ok=True)
os.environ['ORBIT_HIVE_SETTINGS']=str(CODE/'config/settings.example.json')
os.environ['ORBIT_WORKBENCH_STORE_PATH']=str(SAFE/'never-default.db')
sys.path.insert(0,str(CODE))
from shared_platform.workbench_engine import WorkbenchEngine,ReceiptSnapshotUnavailable
from shared_platform.workbench_observations import read_registry,image_bytes

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWV0AAAAASUVORK5CYII=')
class Contracts(unittest.TestCase):
 def setUp(self):
  self.root=Path(tempfile.mkdtemp(prefix='case-',dir=SAFE)).resolve()
  assert self.root.is_relative_to(SAFE.resolve())
  self.now=datetime.now(timezone.utc)
  self.proof=self.root/'source.json';self.proof.write_text('{"accepted":true}',encoding='utf-8')
  self.image=self.root/'product.png';self.image.write_bytes(PNG)
  self.ref={'label':'Isolated producer receipt','path':str(self.proof),'sha256':hashlib.sha256(self.proof.read_bytes()).hexdigest()}
 def lineage(self,identity):
  source=self.root/('product-source-'+identity['kind']+'-'+identity['id']+'.json')
  source.write_text(json.dumps({'schema':'orbit-product-source/v1','identity':identity,'name':'Synthetic product A','images':[{'file':str(self.image),'sha256':hashlib.sha256(PNG).hexdigest(),'status':'ORIGINAL_BYTES_DOWNLOADED'}]}),encoding='utf8')
  reference={'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
  proof=self.root/('lineage-'+identity['kind']+'-'+identity['id']+'.json')
  proof.write_text(json.dumps({'schema':'orbit-product-observation-lineage/v1','identity':identity,'display_name':'Synthetic product A','source_title':'Synthetic product A','name_source':reference,'image_source':reference}),encoding='utf8')
  return {'path':str(proof),'sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}
 def item(self,kind='publication',identity='4039931504'):
  return {'work_id':'test-'+kind,'kind':kind,'title':'Synthetic contract work','goal':'Verify exact identity',
   'stage':'Retained checkpoint','handover':'accepted','last_reported_state':'prepared',
   'observed_at':self.now.isoformat(),'sources':[self.ref],
   'products':[{'identity':{'kind':'offer_id','id':identity},'name':'Synthetic product A','name_verified':True,'lineage':self.lineage({'kind':'offer_id','id':identity}),
    'thumbnail':{'path':str(self.image),'sha256':hashlib.sha256(PNG).hexdigest(),'product_identity':{'kind':'offer_id','id':identity}}}]}
 def registry(self,items):
  path=self.root/'manifest.json';path.write_text(json.dumps({'schema':'orbit-work-observations/v1','complete':False,'items':items}),encoding='utf-8')
  return read_registry(str(path),now=self.now)
 def test_all_accepted_kinds_and_unknown_kind_are_preserved(self):
  kinds=['publication','replenishment','engineering','cleanup','creator','video','profit','research']
  result=self.registry([self.item(k) for k in kinds])
  self.assertEqual(kinds,[x['kind'] for x in result['items']]);self.assertFalse(result['complete'])
  self.assertTrue(all(x['live_status']=='unknown' for x in result['items']))
 def test_exact_image_identity_and_hash(self):
  result=self.registry([self.item()]);p=result['items'][0]['products'][0]
  self.assertEqual('verified',p['image_status']);raw,mime=image_bytes(result,p['image_url'].rsplit('/',1)[1])
  self.assertEqual(PNG,raw);self.assertEqual('image/png',mime)
 def test_retired_product_image_cannot_be_borrowed(self):
  item=self.item();item['products'][0]['thumbnail']['product_identity']['id']='retired-product'
  result=self.registry([item]);p=result['items'][0]['products'][0]
  self.assertEqual('identity_conflict',p['image_status']);self.assertIsNone(p['image_url']);self.assertEqual({},result['assets'])
 def test_missing_and_changed_images_fail_closed(self):
  item=self.item();del item['products'][0]['thumbnail']
  self.assertEqual('missing',self.registry([item])['items'][0]['products'][0]['image_status'])
  item=self.item();self.image.write_bytes(b'changed')
  self.assertEqual('unavailable',self.registry([item])['items'][0]['products'][0]['image_status'])
 def test_product_switch_changes_the_image_identity_even_same_bytes(self):
  first=self.registry([self.item(identity='4039931504')])['items'][0]['products'][0]
  second=self.registry([self.item(identity='4039931505')])['items'][0]['products'][0]
  self.assertNotEqual(first['image_url'],second['image_url'])
 def test_common_collect_box_is_not_a_canonical_offer_identity(self):
  item=self.item();item['products'][0]['identity']['kind']='common_collect_box_id'
  item['products'][0]['thumbnail']['product_identity']['kind']='offer_id'
  row=self.registry([item])['items'][0]
  self.assertEqual('identity_conflict',row['products'][0]['image_status'])
  item['products'][0]['thumbnail']['product_identity']['kind']='common_collect_box_id'
  item['products'][0]['lineage']=self.lineage(item['products'][0]['identity'])
  self.assertEqual('verified',self.registry([item])['items'][0]['products'][0]['image_status'])
 def test_complete_scope_requires_each_original_session_and_verified_source(self):
  item=self.item();item['source_session_id']='original-A';path=self.root/'scope.json'
  data={'schema':'orbit-work-observations/v1','complete':True,'accepted_session_ids':['original-A','original-B'],'items':[item]}
  authority=self.root/'authority.json';authority.write_text(json.dumps({'schema':'orbit-work-scope/v1','sessions':[{'session_id':'original-A'},{'session_id':'original-B'}]}),encoding='utf8')
  data['scope_ref']={'path':str(authority),'sha256':hashlib.sha256(authority.read_bytes()).hexdigest()}
  path.write_text(json.dumps(data),encoding='utf-8')
  self.assertFalse(read_registry(str(path),now=self.now)['complete'])
  authority.write_text(json.dumps({'schema':'orbit-work-scope/v1','sessions':[{'session_id':'original-A'}]}),encoding='utf8')
  data['scope_ref']['sha256']=hashlib.sha256(authority.read_bytes()).hexdigest()
  data['accepted_session_ids']=['original-A'];path.write_text(json.dumps(data),encoding='utf-8')
  self.assertTrue(read_registry(str(path),now=self.now)['complete'])
  self.proof.write_text('changed',encoding='utf-8')
  self.assertFalse(read_registry(str(path),now=self.now)['complete'])
 def test_readonly_http_rejects_write_and_foreign_origin_before_any_engine_access(self):
  from shared_platform.operations_http import handle
  class Handler:
   path='/api/orbit/home/tasks';client_address=('127.0.0.1',12345);server=SimpleNamespace(server_port=9000)
   def __init__(self,origin=None):
    self.headers=Message();self.headers['Host']='127.0.0.1:9000'
    if origin:self.headers['Origin']=origin
   def _json(self,code,data):self.response=(code,data)
  h=Handler();self.assertTrue(handle(h,method='POST',runtime=object()));self.assertEqual(405,h.response[0])
  h=Handler('http://outside.example');self.assertTrue(handle(h,method='GET',runtime=object()));self.assertEqual(403,h.response[0])
 def test_changed_source_suppresses_decision_and_result(self):
  item=self.item();item['result_url']='/product-workspace?offer_id=4039931504';item['closed']=True
  item['user_decision']={'kind':'final_review','label':'Review','reason':'Bound receipt','url':'/review'}
  self.proof.write_text('{"accepted":false}',encoding='utf-8')
  row=self.registry([item])['items'][0]
  self.assertEqual('changed_or_unavailable',row['source_status']);self.assertIsNone(row['user_decision']);self.assertIsNone(row['result_url']);self.assertFalse(row['closed'])
  self.assertFalse(row['products'][0]['name_verified']);self.assertIsNone(row['products'][0]['image_url'])
 def test_refresh_does_not_renew_observation_and_stale_has_no_decision(self):
  item=self.item();item['observed_at']=(self.now-timedelta(hours=2)).isoformat()
  item['user_decision']={'kind':'final_review','label':'Review','reason':'Old receipt','url':'/review'}
  row=self.registry([item])['items'][0]
  self.assertTrue(row['stale']);self.assertIsNone(row['user_decision']);self.assertEqual(item['observed_at'],row['observed_at'])
 def test_unbound_is_not_an_empty_success(self):
  result=read_registry(None,now=self.now);self.assertEqual('UNBOUND',result['status']);self.assertFalse(result['complete'])
 def test_cross_origin_result_and_future_time_are_rejected(self):
  item=self.item();item['result_url']='//outside.example/path'
  with self.assertRaises(ValueError):self.registry([item])
  item=self.item();item['observed_at']=(self.now+timedelta(seconds=5)).isoformat()
  with self.assertRaises(ValueError):self.registry([item])
 def test_snapshot_never_expires_lease_or_changes_ledger(self):
  now=[1000.0];release={'code_version':'fixture','environment':'preview','manifest_digest':'fixture'}
  engine=WorkbenchEngine(self.root/'tasks.db',release,clock=lambda:now[0])
  task=engine.create({'template':'publication','title':'Synthetic expired lease','source_key':'case','scope':{'offer_id':'123','shops':[],'skus':[]}})
  engine.register_executor('fixture-worker',['publication'],release,ttl=30)
  engine.claim(task['task_id'],'fixture-worker',ttl=30)
  now[0]+=100
  before=(hashlib.sha256(engine.store.path.read_bytes()).hexdigest(),engine.store.path.stat().st_mtime_ns)
  engine._expire=lambda conn:(_ for _ in ()).throw(AssertionError('lease maintenance forbidden'))
  for i in range(3):
   self.assertEqual(1,len(engine.read_dashboard_snapshot()['tasks']))
   self.assertEqual(task['task_id'],engine.read_task_snapshot(task['task_id'])['task']['task_id'])
   self.assertEqual([],engine.read_runtime_status()['executor']['templates'])
  after=(hashlib.sha256(engine.store.path.read_bytes()).hexdigest(),engine.store.path.stat().st_mtime_ns)
  self.assertEqual(before,after)
  self.assertFalse(any(Path(str(engine.store.path)+suffix).exists() for suffix in ['-wal','-shm','-journal']))
 def test_snapshot_refuses_journal_instead_of_maintaining_it(self):
  engine=WorkbenchEngine(self.root/'tasks.db',{'code_version':'fixture','environment':'preview','manifest_digest':'fixture'})
  with engine.transaction():pass
  sidecar=Path(str(engine.store.path)+'-wal');sidecar.write_bytes(b'fixture journal')
  with self.assertRaises(ReceiptSnapshotUnavailable):engine.read_dashboard_snapshot()
  self.assertEqual(b'fixture journal',sidecar.read_bytes())

if __name__=='__main__':unittest.main(verbosity=2)
