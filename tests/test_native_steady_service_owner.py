"""Closed owner control-flow regression; no Windows Job, subprocess, HTTP or SQL."""
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class SteadyReadinessTest(unittest.TestCase):
    def test_optional_failures_keep_owner_alive_and_identity_errors_retire(self):
        script=Path(os.environ.get('ORBIT_TEST_OWNER_SCRIPT',Path(__file__).resolve().parents[1]/'scripts/native_steady_service_owner.py'))
        spec=importlib.util.spec_from_file_location('closed_service_owner',script)
        owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
        for scenario in ('supply_timeout','empty_catalog','missing_cache','source_mismatch','manifest_mismatch'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix='orbit-owner-') as temp:
                root=Path(temp);source=root/'source';source.mkdir();runtime=root/'runtime';runtime.mkdir()
                outputs=root/'owners';outputs.mkdir();head=getattr(owner,'SOURCE_HEAD','a'*40)
                expected_root=getattr(owner,'SOURCE_ROOT',source)
                ready=runtime/'ready.json'
                ready.write_text(json.dumps({'pid':502,'version':head,'port':49289,'code_root':str(expected_root)}),encoding='utf-8')
                config=root/'config.json'
                config.write_text(json.dumps({'code_version':head,'port':49289,'native_service_scope':'explicit-new-task-and-decision/v1',
                    'ready_path':str(ready),'operations_data_root':str(runtime),'manifest_digest':'b'*64}),encoding='utf-8')
                sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
                seal={'source_head':head,'source_root':str(expected_root),'port':49289,'config':{'path':str(config),'sha256':sha(config)},
                    'supervisor_python_path':sys.executable,'pins':{str(config):sha(config)},'owner_output_root':str(outputs),'installation_token':'closed-token'}
                seal_path=root/'seal.json';seal_path.write_text(json.dumps(seal),encoding='utf-8')
                state={'terminated':0,'running_observed':False,'exit':False,'get_paths':[]}
                class Child:
                    pid=501;_handle=1
                    def __init__(self):self.stdin=io.BytesIO()
                    def poll(self):return 0 if state['exit'] else None
                    def terminate(self):state['exit']=True
                    def wait(self,timeout=None):state['exit']=True;return 0
                child=Child()
                class Kernel:
                    def AssignProcessToJobObject(self,*args):return True
                    def TerminateJobObject(self,*args):state['terminated']+=1;state['exit']=True;return True
                    def CloseHandle(self,*args):return True
                class Socket:
                    def __enter__(self):return self
                    def __exit__(self,*args):pass
                    def setsockopt(self,*args):pass
                    def bind(self,*args):pass
                def get(port,path,marker):
                    self.assertEqual(port,49289);state['get_paths'].append(path)
                    row={'method':'GET','path':path,'passed':True,'http_status':200,'body_sha256':'c'*64}
                    if path=='/api/health':row['source_commit']='d'*40 if scenario=='source_mismatch' else head
                    if path=='/api/orbit/operations-runtime':
                        row.update(public_fields={'version':head,'manifest_digest':'e'*64 if scenario=='manifest_mismatch' else 'b'*64},native_decision_installed=True)
                    if path=='/supply-chain/' and scenario=='supply_timeout':row.update(passed=False,error_type='TimeoutError',error_code='PREVIEW_GET_FAILED',error_stage='headers')
                    if path.startswith('/api/catalog/skus'):
                        item={'entity_key':'owned-item','sku':'0001','image_key':'f'*64,'cost_object_shape':True,'cost_layers':1,'cost_status':'INHERITED'}
                        empty=scenario=='empty_catalog'
                        row.update(catalog_items=[] if empty else [item],catalog_total=0 if empty else 1,catalog_limit=50)
                    if path=='/profit':row['untrusted_raw_body']='MUST_NOT_PERSIST'
                    return row
                if scenario not in ('missing_cache','empty_catalog'):
                    cache=runtime/'image-cache';cache.mkdir();(cache/('f'*64+'.bin')).write_bytes(b'closed-image')
                    (cache/('f'*64+'.json')).write_text(json.dumps({'mime':'image/png'}),encoding='utf-8')
                    original_get=get
                    def get(port,path,marker):
                        row=original_get(port,path,marker)
                        if path.startswith('/api/catalog/image?key='):row.update(body_sha256=sha(cache/('f'*64+'.bin')),content_type='image/png')
                        return row
                def sleeping(seconds):
                    locator=json.loads((outputs/'current-owner.json').read_bytes());p=Path(locator['owner_path'])
                    record=json.loads(p.read_bytes())
                    self.assertEqual(record['status'],'RUNNING_FORMAL_SCOPED_OWNER')
                    self.assertEqual(state['terminated'],0)
                    self.assertTrue(all(row['passed'] for row in record['get_checks']))
                    self.assertEqual({row['path'] for row in record['get_checks']},{'/api/health','/api/orbit/operations-runtime'})
                    self.assertTrue(record['business_components_degraded'])
                    self.assertNotIn('MUST_NOT_PERSIST',p.read_text(encoding='utf-8'))
                    if scenario=='supply_timeout':self.assertEqual(record['component_readiness']['/supply-chain/']['status'],'DEGRADED')
                    if scenario=='empty_catalog':self.assertEqual(record['component_readiness']['historical_sku_0001']['status'],'DEGRADED')
                    if scenario=='missing_cache':self.assertEqual(record['component_readiness']['cached_image']['status'],'DEGRADED')
                    state['running_observed']=True
                    (Path(record['run'])/'STOP.json').write_text(json.dumps({'run':record['run'],'supervisor_pid':os.getpid(),'port':49289}),encoding='utf-8')
                with patch.object(owner,'job_api',return_value=(Kernel(),1,lambda value:value,lambda:0 if state['exit'] else 1,lambda:[501,502])), \
                     patch.object(owner.socket,'socket',return_value=Socket()), \
                     patch.object(owner.subprocess,'Popen',return_value=child), \
                     patch.object(owner,'get_one',side_effect=get), patch.object(owner.time,'sleep',side_effect=sleeping):
                    def invoke():
                        if 'seal_path' in inspect.signature(owner.steady_owner).parameters:owner.steady_owner(seal_path,sha(seal_path))
                        else:
                            with patch.object(owner,'sealed',return_value=seal):owner.steady_owner(sha(seal_path))
                    if scenario in ('source_mismatch','manifest_mismatch'):
                        with self.assertRaisesRegex(RuntimeError,'STEADY_REQUIRED_GET_FAILED|STEADY_RUNTIME_IDENTITY'):invoke()
                        self.assertFalse(state['running_observed']);self.assertEqual(state['terminated'],1)
                        self.assertNotIn('/supply-chain/',state['get_paths'])
                    else:
                        invoke();self.assertTrue(state['running_observed'])
                        # Only explicit matching STOP retires the owner after the persistence assertion.
                        self.assertEqual(state['terminated'],1)


if __name__=='__main__':unittest.main(verbosity=2)
