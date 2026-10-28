import hashlib, json, os, sys, tempfile, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
CODE=Path(os.environ.get('ORBIT_HOME_CANDIDATE_ROOT',ROOT.parent if (ROOT.parent/'shared_platform').is_dir() else ROOT/'work'))
sys.path.insert(0,str(CODE))
from shared_platform.workbench_observations import read_registry, image_bytes
from shared_platform.workbench_observation_updates import update_observation

class IndependentContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
        self.p=Path(self.tmp.name)
        self.now=datetime.now(timezone.utc)
        self.identity={'kind':'common_collect_box_id','id':'123'}
        self.pic=self.write('source.jpg',b'\xff\xd8\xfforiginal')
        self.state=self.json('state.json',{'offer_id':'123','source':{'offer_id':'123','source_url':'https://detail.1688.com/offer/456.html','title_source':'真实纸架'}})
        self.download=self.json('download.json',{'schema':'new-product-source-image-download/v1','common_id':'123','source_id':'456','images':[{'status':'ORIGINAL_BYTES_DOWNLOADED','file':'source.jpg','sha256':self.pic['sha256']}]})
        self.lineage=self.json('lineage.json',{'schema':'orbit-product-observation-lineage/v1','identity':self.identity,'display_name':'纸架','source_identity':{'kind':'supplier_offer_id','id':'456'},'source_title':'真实纸架','name_source':self.state,'image_source':self.download})
        self.scope=self.json('scope.json',{'schema':'orbit-work-scope/v1','sessions':[{'session_id':'session-a'},{'session_id':'session-b'}]})
        self.receipt=self.json('receipt.json',{'work_id':'a','source_session_id':'session-a','observed_at':self.now.isoformat(),'stage':'准备中'})
        self.item={'work_id':'a','source_session_id':'session-a','kind':'publication','handover':'accepted','observed_at':self.now.isoformat(),'stage':'准备中','sources':[self.receipt],'products':[{'identity':self.identity,'name':'纸架','name_verified':True,'lineage':self.lineage,'thumbnail':{**self.pic,'product_identity':self.identity}}]}
        self.data={'schema':'orbit-work-observations/v1','complete':True,'accepted_session_ids':['session-a'],'scope_ref':self.scope,'items':[self.item]}
        self.path=self.p/'observations.json'
    def tearDown(self): self.tmp.cleanup()
    def write(self,name,raw):
        p=self.p/name;p.write_bytes(raw)
        return {'path':str(p),'sha256':hashlib.sha256(raw).hexdigest()}
    def json(self,name,data): return self.write(name,json.dumps(data,ensure_ascii=False).encode())
    def read(self):
        self.path.write_text(json.dumps(self.data),encoding='utf8')
        return read_registry(self.path,now=self.now+timedelta(seconds=1))
    def test_self_declared_scope_does_not_hide_missing_session(self):
        value=self.read();self.assertFalse(value['complete']);self.assertEqual(value['missing_session_ids'],['session-b'])
    def test_no_scope_proof_never_complete(self):
        del self.data['scope_ref'];self.assertFalse(self.read()['complete'])
    def test_original_receipts_bind_real_image_and_name(self):
        p=self.read()['items'][0]['products'][0]
        self.assertEqual((p['image_status'],p['name_verified']),('verified',True))
    def test_same_identity_claim_cannot_borrow_other_image(self):
        self.item['products'][0]['thumbnail']={**self.write('other.jpg',b'\xff\xd8\xffforeign'),'product_identity':self.identity}
        self.assertNotEqual(self.read()['items'][0]['products'][0]['image_status'],'verified')
    def test_name_cannot_be_substituted_after_proof(self):
        self.item['products'][0]['name']='其他商品'
        self.assertFalse(self.read()['items'][0]['products'][0]['name_verified'])
    def test_replaced_original_source_removes_image(self):
        value=self.read(); asset=value['items'][0]['products'][0]['image_url'].rsplit('/',1)[1]
        Path(self.download['path']).write_text('{}')
        with self.assertRaises(ValueError): image_bytes(value,asset)
    def test_failed_scope_source_reports_unavailable(self):
        Path(self.scope['path']).write_text('{}')
        v=self.read();self.assertFalse(v['complete']);self.assertEqual(v['scope_status'],'changed_or_unavailable')
    def envelope(self,**changes):
        v={'schema':'orbit-work-observation-update/v1','work_id':'a','source_session_id':'session-a','observed_at':(self.now+timedelta(seconds=2)).isoformat(),'changes':changes or {'stage':'新回执已完成'}}
        return {**v,'receipt_ref':self.json('update-receipt.json',v)}
    def apply_update(self,v,**options):
        self.read()
        return update_observation(self.path,v,expected_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),now=self.now+timedelta(seconds=5),**options)
    def test_dated_update_changes_actual_backend_state_and_keeps_identity(self):
        v=self.envelope();self.apply_update(v,apply=True)
        r=read_registry(self.path,now=self.now+timedelta(seconds=5))['items'][0]
        self.assertEqual(r['stage'],'新回执已完成');self.assertEqual(r['source_session_id'],'session-a');self.assertTrue(r['products'][0]['name_verified'])
    def test_validate_only_leaves_bytes_and_timestamp_unchanged(self):
        self.read();raw=self.path.read_bytes();before=self.path.stat().st_mtime_ns
        update_observation(self.path,self.envelope(),expected_sha256=hashlib.sha256(raw).hexdigest(),now=self.now+timedelta(seconds=5))
        self.assertEqual(self.path.read_bytes(),raw);self.assertEqual(self.path.stat().st_mtime_ns,before)
    def test_old_revision_cannot_overwrite_new_observation(self):
        self.read()
        with self.assertRaises(ValueError): update_observation(self.path,self.envelope(),expected_sha256='0'*64,apply=True)
    def test_timestamp_only_refresh_without_new_receipt_rejected(self):
        v=self.envelope();v['receipt_ref']=self.receipt
        with self.assertRaises(ValueError):self.apply_update(v,apply=True)
    def test_owner_or_execution_mapping_cannot_change(self):
        with self.assertRaises(ValueError):self.apply_update(self.envelope(operations_task_id='old-queued-task'),apply=True)
    def test_future_observation_never_applied(self):
        v=self.envelope();v['observed_at']=(self.now+timedelta(days=1)).isoformat()
        v['receipt_ref']=self.json('future.json',{k:x for k,x in v.items() if k!='receipt_ref'})
        with self.assertRaises(ValueError):self.apply_update(v,apply=True)
    def test_active_other_manifest_writer_is_not_overwritten(self):
        self.read();raw=self.path.read_bytes();lock=self.path.with_name(self.path.name+'.update.lock');lock.write_text('other-writer')
        with self.assertRaises(FileExistsError): update_observation(self.path,self.envelope(),expected_sha256=hashlib.sha256(raw).hexdigest(),apply=True)
        self.assertEqual(raw,self.path.read_bytes());self.assertTrue(lock.exists())
    def test_invalid_lineage_object_is_unavailable_without_server_error(self):
        self.item['products'][0]['lineage']=self.json('invalid-lineage.json',[])
        p=self.read()['items'][0]['products'][0];self.assertFalse(p['name_verified']);self.assertEqual(p['image_status'],'unavailable')
    def test_invalid_registry_object_is_refused(self):
        self.path.write_text('[]')
        with self.assertRaises(ValueError):read_registry(self.path,now=self.now)
    def test_invalid_product_row_is_refused(self):
        self.item['products']=['bad-row']
        with self.assertRaises(ValueError):self.read()
    def test_invalid_scope_object_is_unavailable(self):
        self.data['scope_ref']=self.json('invalid-scope.json',[])
        self.assertEqual(self.read()['scope_status'],'changed_or_unavailable')

if __name__=='__main__': unittest.main(verbosity=2)
