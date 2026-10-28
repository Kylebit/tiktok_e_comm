"""Actual approved B, query-only: only the isolated reader is replaced.

This verifies the reader against the retained main profile and genuine signed
history. It does not install a new runtime/source event or resume publication.
"""
from pathlib import Path
import os,subprocess,sys


ACTUAL=r'''
from copy import deepcopy
from hashlib import sha256
import importlib.util,json,os,socket,sqlite3,sys
from pathlib import Path
root=Path(os.environ['ORBIT_READER_ACTUAL_ROOT']);new=Path(sys.argv[1]);out=Path(os.environ['ORBIT_READER_OUTPUT'])
sys.path.insert(0,str(root));os.environ['ORBIT_RELEASE_STORE_PATH']=str(root/'data/orbit_platform.db')
from shared_platform import workbench_publication_native as native,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile
from shared_platform import release_store,publication_r3_image_bridge as bridge
from modules.sourcing import new_product_workbench as workbench
task_root=Path(os.environ['ORBIT_READER_ACTUAL_TASK_ROOT']);plan='r3-marketplace:8412287877065586543:07707b4b7901ca9b97b08acd';task_id='TASK-20261023-001'
original_connect=sqlite3.connect;blocked=[]
def ro_connect(database,*a,**kw):
 if not kw.get('uri') or '?mode=ro' not in str(database):raise AssertionError('Read-only test rejected writable SQLite')
 db=original_connect(database,*a,**kw);db.execute('PRAGMA query_only=ON');return db
sqlite3.connect=ro_connect
def deny(*a,**kw):
 blocked.append('network-or-mutation');raise RuntimeError('READONLY_TEST_BLOCKED_NETWORK_OR_MUTATION')
socket.socket.connect=deny;release_store.ReleaseStore._connect=deny;workbench.save_state=deny
def ro(path):
 d=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);d.row_factory=sqlite3.Row;return d
def snapshot():
 with ro(root/'data/orbit_platform.db') as d:
  d.execute('BEGIN');tables={}
  for table in ('release_plans','native_sole_final_nonces','native_sole_final_decisions','release_approvals','approved_publication_snapshots','publication_business_snapshots','release_runs'):
   rows=[dict(r) for r in d.execute('SELECT * FROM '+table+' WHERE plan_id=?',(plan,))]
   tables[table]=dict(count=len(rows),sha256=sha256(adoption._bytes(rows)).hexdigest())
  assert d.execute('SELECT status FROM release_plans WHERE plan_id=?',(plan,)).fetchone()[0]=='APPROVED'
  assert tables['native_sole_final_decisions']['count']==tables['release_approvals']['count']==1
  assert tables['release_runs']['count']==0
 with ro(task_root/'tasks.db') as d:
  d.execute('BEGIN');r=dict(d.execute('SELECT * FROM workbench_execution WHERE task_id=?',(task_id,)).fetchone())
  assert all(r[k] is None for k in ('worker','lease_token','lease_until')) and not r['external_started']
  tables['task']=sha256(adoption._bytes(r)).hexdigest()
  tables['events']=sha256(adoption._bytes([dict(r) for r in d.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))])).hexdigest()
 return tables
before=snapshot()
with ro(task_root/'tasks.db') as d:
 r=d.execute('SELECT * FROM workbench_execution WHERE task_id=?',(task_id,)).fetchone()
 task={'task_id':task_id,**{k:json.loads(r[k+'_json']) for k in ('scope','checkpoint','steps','version')},'pending_observation':json.loads(r['action_json'])}
profile=RuntimeProfile(root,task_root,'stable',task['version']['code_version'],task['version']['manifest_digest'])
assert profile.version=='3f3be5474498a22730b768a48aa0e2e76268de96'
original=native.read_frozen
try:
 original(task,profile)
 raise AssertionError('Original stale-legacy reader unexpectedly passed')
except ValueError as e:
 assert str(e)=='NATIVE_R1_FREEZE_INCOMPLETE',str(e)
 baseline=str(e)
baseline_blocked=len(blocked)
spec=importlib.util.spec_from_file_location('isolated_frozen_reader_under_test',new);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
# Keep the genuine main service/global profile; replace only the tested reader.
native.read_frozen=module.read_frozen
frozen=native.read_frozen(task,profile)
assert frozen['offer_id']==task['scope']['offer_id'] and frozen['targets']==sorted(task['scope']['shops'])
assert len(frozen['targets'])==15 and frozen['snapshot_digest']==task['checkpoint']['native_r1']['snapshot_digest']
assert frozen['prepared_reference']==task['checkpoint']['native_preparation']['prepared_reference']
assert frozen['internal_sku']=='0431' and frozen['seller_sku']=='2000431'
documents=bridge.load_r2_documents(frozen['offer_id'],reports_root=root/'reports/product-preparation',
 native_review_reader=native.NativeR2PreparedReviewReader(task,profile))
identity=bridge.validate_r2_identity(documents);assert identity==task['checkpoint']['native_r2']
assert len(blocked)==baseline_blocked,'New adopted reader attempted legacy network/mutation'
negative=[]
for change in ('no_adoption','forged_pin','wrong_snapshot','wrong_reference'):
 t=deepcopy(task)
 if change=='no_adoption':t['checkpoint'].pop('native_r1_adoption')
 elif change=='forged_pin':t['checkpoint']['native_r1_adoption']['record_digest']='0'*64
 elif change=='wrong_snapshot':t['checkpoint']['native_r1']['snapshot_digest']='sha256:'+'0'*64
 else:t['checkpoint']['native_preparation']['prepared_reference']='r1-prepared:'+'0'*64
 try:
  native.read_frozen(t,profile);raise AssertionError('Unbound reader accepted '+change)
 except (ValueError,KeyError) as error:
  if change=='no_adoption':assert str(error)=='NATIVE_R1_FREEZE_INCOMPLETE'
  negative.append(dict(case=change,error=str(error)))
after=snapshot();assert before==after,'Actual approved state or source history changed'
result=dict(schema_version='actual-approved-adopted-frozen-reader-regression/v1',status='PASS_READER_ONLY',
 actual_profile_head=profile.version,reader_under_test=str(new),reader_sha256=sha256(new.read_bytes()).hexdigest(),
 baseline_error=baseline,original_signed_target_count=15,effective_marketplace_target_count_unchanged=9,
 frozen=frozen,r2_identity_digest=identity['identity_digest'],negative_cases=negative,preserved_row_hashes=after,
 baseline_blocked_network_or_mutation_attempts=baseline_blocked,new_adopted_reader_blocked_attempts=0,
 provider_calls=0,paid_calls=0,actual_database_writes=0,service_operations=0,decision_or_resume_POSTs=0,
 limitation='Actual retained main profile/history verified; only the isolated reader function replaced in a subprocess. No new-head source append/runtime install/publication resume was tested.')
out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(dict(status=result['status'],baseline_error=baseline,negative_cases=len(negative),approved_decision_preserved=True)))
'''


def test_actual_approved_adoption_reader_and_nonadoption_fail_closed(tmp_path):
    script=tmp_path/'actual-approved-reader.py';script.write_text(ACTUAL,encoding='utf-8')
    reader=Path(__file__).resolve().parents[1]/'shared_platform/workbench_publication_native.py'
    result=subprocess.run([sys.executable,'-I','-B','-X','utf8',str(script),str(reader)],text=True,capture_output=True,timeout=90)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert '"status": "PASS_READER_ONLY"' in result.stdout
