"""Actual existing detail-cache writer -> actual R1 lineage; E fixtures only."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from test_agent_entry_binding import ROOT, synthetic, isolated_entry_env
from test_agent_r1_path_binding import v2, install_actual_r1_with_dashboard_stub, commit_fixture

ROLE_ROOTS = ('state_dir','data_root','source_outputs_root','content_outputs_root')
CAPTURE_SCRIPT = r'''
import json,sys,socket,sqlite3,urllib.request
from pathlib import Path
binding=json.loads(Path(sys.argv[1]).read_text())
sys.path.insert(0,binding['source_root'])
counts={'network':0,'sql':0,'auth':0,'detail_stub':0}
def poison(kind):
    def reject(*a,**k):
        counts[kind]+=1
        raise AssertionError('REAL_'+kind+'_FORBIDDEN')
    return reject
socket.create_connection=poison('network');socket.socket.connect=poison('network')
urllib.request.urlopen=poison('network');sqlite3.connect=poison('sql')
from modules.sourcing import miaoshou_precollect as producer
producer._load_config=poison('auth')
def detail(path,body):
    assert path==producer.DETAIL_PATH and body=={'commonCollectBoxDetailId':123}
    counts['detail_stub']+=1
    return {'data':{'title':'Synthetic captured lamp','price':5,'weight':0.3,
        'packageLength':10,'packageWidth':20,'packageHeight':30,
        'sourceList':[{'sourceItemId':'123','sourceItemUrl':'https://example.invalid/123'}],
        'imgUrls':['https://example.invalid/synthetic.jpg'],'notes':'Synthetic secret body excluded from metadata'}}
key,payload=producer.import_common_collect_detail('123',post=detail,state_key='123',
    state_dir=Path(binding['state_dir']),origin_binding=binding)
assert key=='123' and counts=={'network':0,'sql':0,'auth':0,'detail_stub':1}
print(json.dumps({'key':key,'ref':payload['capture_origin_manifest'],'counts':counts}))
'''


@pytest.fixture
def origin_case(v2,tmp_path):
    root,profile,invoke=v2
    install_actual_r1_with_dashboard_stub(root,profile)
    for relative in ('modules/sourcing/miaoshou_precollect.py','shared_platform/source_capture_origin.py'):
        if (ROOT/relative).exists():
            target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/relative,target)
    config=root/'core/config.py'
    config.write_text(config.read_text().replace("raise RuntimeError('DOMAIN_IMPORT_FORBIDDEN')",'')+
        "\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\n",encoding='utf-8')
    stub=root/'shared_platform/release_control.py'
    stub.write_text(stub.read_text().replace(
        "assert state['offer_id']==offer_id",
        "assert state['offer_id']==offer_id\n    captured=json.loads((Path(paths['state_dir'])/(offer_id+'_miaoshou.json')).read_text())\n    assert captured['normalized']['title']=='Synthetic captured lamp'"),encoding='utf-8')
    profile['expected_source_head']=commit_fixture(root)
    state=Path(profile['state_dir'])/'123.json'
    state.write_text(json.dumps({'offer_id':'123','_revision':7,'source':{'title_source':'Synthetic captured lamp',
        'cost_cny':5,'weight_kg':0.3,'package_cm':[10,20,30]}}),encoding='utf-8')
    binding={name:profile[name] for name in ('source_root','expected_source_head',*ROLE_ROOTS)}
    binding_path=tmp_path/'origin-binding.json';binding_path.write_text(json.dumps(binding),encoding='utf-8')
    decoy=tmp_path/'decoy-cwd';decoy.mkdir()
    return root,profile,invoke,binding,binding_path,decoy


def capture(case):
    root,profile,invoke,binding,binding_path,decoy=case
    result=subprocess.run([sys.executable,'-B','-c',CAPTURE_SCRIPT,str(binding_path)],cwd=decoy,
        env=isolated_entry_env(),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr+result.stdout
    return json.loads(result.stdout)


def test_actual_detail_writer_closes_origin_to_actual_r1_consumer(origin_case):
    root,profile,invoke,binding,binding_path,decoy=origin_case
    produced=capture(origin_case)
    sidecar=Path(produced['ref']['file'])
    assert sidecar==Path(profile['state_dir'])/'123_capture-origin.json'
    origin=json.loads(sidecar.read_text(encoding='utf-8'))
    assert origin['producer']['source_head']==profile['expected_source_head']
    assert origin['roots']=={name:str(Path(profile[name])) for name in ROLE_ROOTS}
    assert len(origin['files'])==1 and origin['files'][0]['path']==str(Path(profile['state_dir'])/'123_miaoshou.json')
    assert 'Synthetic secret body' not in sidecar.read_text(encoding='utf-8')
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode==0,result.stderr+result.stdout
    directory=Path(profile['output_root'])/'product-preparation/123'
    packet=json.loads((directory/'first-review.json').read_text(encoding='utf-8'))
    from shared_platform.r1_input_lineage import inspect_review_lineage
    proof=inspect_review_lineage(packet,directory)
    assert proof['capture_origin_status']=='VERIFIED_LOCAL_PRODUCER_OUTPUT'
    lineage=json.loads((directory/'r1-input-lineage.json').read_text(encoding='utf-8'))
    row=next(row for row in lineage['inputs'] if row['path'].endswith('123_miaoshou.json'))
    assert row['capture_origin']['file']==str(sidecar)
    assert not (root/'data/new_product_workbench/123_miaoshou.json').exists()
    assert not (decoy/'data').exists()


@pytest.mark.parametrize(('change','diagnostic'),[
    ('head','SOURCE_CAPTURE_ORIGIN_SOURCE_CHANGED'),
    ('loaded-source','SOURCE_CAPTURE_ORIGIN_LOADED_SOURCE_MISMATCH'),
    ('state-root','SOURCE_CAPTURE_ORIGIN_ROOT_MISMATCH'),
    ('missing-root','R1_LINEAGE_INPUT_MISSING'),
    ('source-drift','SOURCE_CAPTURE_ORIGIN_SOURCE_CHANGED'),
    ('partial-binding','SOURCE_CAPTURE_ORIGIN_BINDING_REQUIRED'),
])
def test_origin_binding_rejected_before_transport_or_cache_write(origin_case,tmp_path,change,diagnostic):
    root,profile,invoke,binding,binding_path,decoy=origin_case
    before={p.name:p.read_bytes() for p in Path(profile['state_dir']).iterdir()}
    if change=='head':binding['expected_source_head']='0'*40
    elif change=='loaded-source':binding['source_root']=str(decoy)
    elif change=='state-root':
        other=tmp_path/'other-state';other.mkdir();binding['state_dir']=str(other)
    elif change=='missing-root':binding['content_outputs_root']=str(tmp_path/'missing-content')
    elif change=='source-drift':(root/'modules/sourcing/miaoshou_precollect.py').write_text('# source drift\n'+(root/'modules/sourcing/miaoshou_precollect.py').read_text(),encoding='utf-8')
    binding_path.write_text(json.dumps(binding),encoding='utf-8')
    script=CAPTURE_SCRIPT.split('key,payload=',1)[0].replace(
        "sys.path.insert(0,binding['source_root'])","sys.path.insert(0,sys.argv[4])")+'''
try:
    producer.import_common_collect_detail('123',post=detail,state_key='123',
        state_dir=Path(sys.argv[2]),origin_binding=None if sys.argv[3]=='partial-binding' else binding)
except ValueError as error:
    print(json.dumps({'diagnostic':str(error),'counts':counts}))
else:
    raise AssertionError('unsafe capture binding accepted')
'''
    result=subprocess.run([sys.executable,'-B','-c',script,str(binding_path),profile['state_dir'],change,str(root)],
        cwd=decoy,env=isolated_entry_env(),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr+result.stdout
    outcome=json.loads(result.stdout)
    assert outcome['diagnostic']==diagnostic
    assert outcome['counts']=={'network':0,'sql':0,'auth':0,'detail_stub':0}
    assert before=={p.name:p.read_bytes() for p in Path(profile['state_dir']).iterdir()}


@pytest.mark.parametrize(('change','diagnostic'),[
    ('cache','CAPTURE_ORIGIN_CACHE_CHANGED'),
    ('manifest','CAPTURE_ORIGIN_MANIFEST_CHANGED'),
    ('root','CAPTURE_ORIGIN_ROOT_MISMATCH'),
    ('producer','CAPTURE_ORIGIN_SOURCE_CHANGED'),
])
def test_actual_r1_rejects_capture_origin_drift_without_packet(origin_case,change,diagnostic):
    root,profile,invoke,binding,binding_path,decoy=origin_case
    produced=capture(origin_case);sidecar=Path(produced['ref']['file'])
    body=json.loads(sidecar.read_text(encoding='utf-8'))
    if change=='cache':(Path(profile['state_dir'])/'123_miaoshou.json').write_text('{}',encoding='utf-8')
    else:
        if change=='manifest':body['scope']='tampered'
        elif change=='root':body['roots']['data_root']=profile['source_outputs_root']
        else:body['producer']['source_head']='0'*40
        if change!='manifest':
            from shared_platform.publication_rounds import canonical_digest
            body.pop('manifest_digest');body['manifest_digest']=canonical_digest(body)
        sidecar.write_text(json.dumps(body),encoding='utf-8')
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode==2 and 'R1_LINEAGE_'+diagnostic in result.stdout+result.stderr
    assert not (Path(profile['output_root'])/'product-preparation/123/first-review.json').exists()


@pytest.mark.parametrize('change',['removed','replaced'])
def test_frozen_r1_reference_does_not_downgrade_when_origin_changes(origin_case,change):
    root,profile,invoke,binding,binding_path,decoy=origin_case
    produced=capture(origin_case)
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode==0,result.stdout+result.stderr
    directory=Path(profile['output_root'])/'product-preparation/123'
    packet=json.loads((directory/'first-review.json').read_text(encoding='utf-8'))
    sidecar=Path(produced['ref']['file'])
    if change=='removed':sidecar.unlink()
    else:
        from shared_platform.publication_rounds import canonical_digest
        body=json.loads(sidecar.read_text(encoding='utf-8'))
        body['created_at']='synthetic replacement'
        body.pop('manifest_digest');body['manifest_digest']=canonical_digest(body)
        sidecar.write_text(json.dumps(body),encoding='utf-8')
    from shared_platform.source_capture_origin import inspect_detail_capture,CaptureOriginError
    expected='MANIFEST_MISSING' if change=='removed' else 'MANIFEST_CHANGED'
    with pytest.raises(CaptureOriginError,match='SOURCE_CAPTURE_ORIGIN_'+expected):
        inspect_detail_capture(Path(profile['state_dir'])/'123_miaoshou.json',roots=binding,reference=produced['ref'])
    from shared_platform.r1_input_lineage import inspect_review_lineage,R1LineageError
    with pytest.raises(R1LineageError,match='R1_LINEAGE_CAPTURE_ORIGIN_CHANGED'):
        inspect_review_lineage(packet,directory)


def test_legacy_writer_preserves_stale_origin_and_consumer_rejects(origin_case):
    root,profile,invoke,binding,binding_path,decoy=origin_case
    produced=capture(origin_case)
    sidecar=Path(produced['ref']['file']);before=sidecar.read_bytes()
    script=CAPTURE_SCRIPT.split('key,payload=',1)[0]+'''
producer.CACHE_DIR=Path(binding['state_dir'])  # Only this synthetic legacy default.
key,payload=producer.import_common_collect_detail('123',post=detail,state_key='123')
assert 'capture_origin_manifest' not in payload
print(json.dumps({'counts':counts}))
'''
    result=subprocess.run([sys.executable,'-B','-c',script,str(binding_path)],cwd=decoy,
        env=isolated_entry_env(),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr+result.stdout
    assert json.loads(result.stdout)['counts']=={'network':0,'sql':0,'auth':0,'detail_stub':1}
    assert sidecar.read_bytes()==before  # Old writers cannot erase the failed proof.
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode==2 and 'R1_LINEAGE_CAPTURE_ORIGIN_CACHE_CHANGED' in result.stdout+result.stderr
    assert not (Path(profile['output_root'])/'product-preparation/123/first-review.json').exists()


def test_capture_does_not_manufacture_missing_workbench_state(origin_case):
    root,profile,invoke,binding,binding_path,decoy=origin_case
    state=Path(profile['state_dir'])/'123.json';state.unlink()
    produced=capture(origin_case)
    assert Path(produced['ref']['file']).exists() and not state.exists()
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode==2 and 'R1_CAPTURED_STATE_MISSING' in result.stdout+result.stderr
    assert not state.exists() and not (Path(profile['output_root'])/'product-preparation/123/first-review.json').exists()


def test_original_default_detail_writer_remains_unverified_legacy(origin_case):
    root,profile,invoke,binding,binding_path,decoy=origin_case
    script=CAPTURE_SCRIPT.split('key,payload=',1)[0]+'''
producer.CACHE_DIR=Path(binding['state_dir'])  # Only this synthetic legacy default.
key,payload=producer.import_common_collect_detail('123',post=detail,state_key='123')
assert 'capture_origin_manifest' not in payload
print(json.dumps({'counts':counts}))
'''
    result=subprocess.run([sys.executable,'-B','-c',script,str(binding_path)],cwd=decoy,
        env=isolated_entry_env(),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr+result.stdout
    assert json.loads(result.stdout)['counts']=={'network':0,'sql':0,'auth':0,'detail_stub':1}
    assert not (Path(profile['state_dir'])/'123_capture-origin.json').exists()
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode==0,result.stdout+result.stderr
    directory=Path(profile['output_root'])/'product-preparation/123'
    packet=json.loads((directory/'first-review.json').read_text(encoding='utf-8'))
    from shared_platform.r1_input_lineage import inspect_review_lineage
    assert inspect_review_lineage(packet,directory)['capture_origin_status']=='UNVERIFIED_LEGACY'
