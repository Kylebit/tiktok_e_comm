from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_operations_publication_common import setup
from modules.products import server
from shared_platform.operations_publication_common import _binding
from shared_platform import operations_publication as publication


def test_registered_completed_receipts_bind_real_common_plan(tmp_path,monkeypatch):
    engine,task_id,profile,transport,request,store=setup(tmp_path,monkeypatch)
    common=server._preview_r3_common_stage(request)[1]['common']
    task=engine.get(task_id)
    identity={'offer_id':task['scope']['offer_id'],'snapshot_digest':common['plan']['payload']['r3_stage_binding']['round1_snapshot_digest'],'binding_sha256':'b'*64}
    r2=common['plan']['payload']['r3_stage_binding']['r2_identity']
    task['checkpoint']={}
    task['steps'][0]['checkpoint']={'publication_identity':deepcopy(identity)}
    task['steps'][1]['checkpoint']={'publication_identity':deepcopy(identity),'image_receipt':{'binding_sha256':'b'*64,'consumer_identity':deepcopy(r2)}}
    assert _binding(task,common)['round1_snapshot_digest']==identity['snapshot_digest']
    task['steps'][1]['checkpoint']['image_receipt']['binding_sha256']='different'
    with pytest.raises(ValueError,match='R1/R2'):_binding(task,common)
    task['steps'][0]['checkpoint']['publication_identity'].pop('binding_sha256')
    task['steps'][1]['checkpoint']['publication_identity'].pop('binding_sha256')
    task['steps'][1]['checkpoint']['image_receipt'].pop('binding_sha256')
    with pytest.raises(ValueError,match='R1/R2'):_binding(task,common)
    assert transport.mutations==0


def test_registered_release_runs_common_before_final_review(monkeypatch):
    from shared_platform import operations_publication_common as common
    calls=[]
    monkeypatch.setattr(publication,'_registered',lambda *a:True)
    monkeypatch.setattr(common,'run',lambda *a:calls.append('common') or True)
    monkeypatch.setattr(publication.registered,'run',lambda *a:calls.append('registered'))
    run,_=publication.bindings(None)
    run(None,{'current_step':'release'},None,None)
    assert calls==['common','registered']
    calls.clear()
    monkeypatch.setattr(common,'run',lambda *a:calls.append('common') or False)
    run(None,{'current_step':'release'},None,None)
    assert calls==['common']
