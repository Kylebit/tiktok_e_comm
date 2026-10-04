"""Actual captured-v2 producer and bounded lineage consumer; synthetic only."""
import json
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import pytest

from test_agent_entry_binding import synthetic
from test_agent_r1_path_binding import v2, install_actual_r1_with_dashboard_stub, commit_fixture


def produce(v2, *, ready=False):
    root, profile, invoke = v2
    install_actual_r1_with_dashboard_stub(root, profile)
    state = Path(profile['state_dir'])/'123.json'
    state.write_text(json.dumps({'offer_id':'123','_revision':7,
        'source':{'title_source':'Synthetic lamp','cost_cny':5,'weight_kg':0.3,
                  'package_cm':[10,20,30]}}), encoding='utf-8')
    if ready:
        stub=root/'shared_platform/release_control.py'
        body=stub.read_text(encoding='utf-8').replace(
            "return {'revision':state['_revision'],'review':{'selected_sites':['lh_my']},'source':state['source']}",
            "return {'ok':True,'revision':state['_revision'],'review':{'selected_sites':['lh_my']},'source':state['source'],'pricing_review':{'target_pricing':{'lh_my':{'list_price':19,'currency':'MYR'}}}}")
        stub.write_text(body,encoding='utf-8')
        profile['expected_source_head']=commit_fixture(root)
        plan=Path(profile['output_root'])/'product-preparation/123/first-review-image-plan.json'
        plan.parent.mkdir(parents=True)
        plan.write_text(json.dumps({'schema_version':'first-review-image-plan/v1','status':'PROPOSED',
            'source_actions':[],'generated_assets':[],
            'summary':{'translation_positions':[],'localized_output_count':0,'net_new_output_count':0,'paid_generation_required':False}}),encoding='utf-8')
    result = invoke(check=False, arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode == 0, result.stderr+result.stdout
    directory = Path(profile['output_root'])/'product-preparation/123'
    packet = json.loads((directory/'first-review.json').read_text(encoding='utf-8'))
    if ready:assert packet['status']=='FIRST_REVIEW_READY',packet['blockers']
    return root, profile, directory, packet, state


def test_actual_captured_r1_emits_consumed_input_lineage(v2,monkeypatch):
    # Typical JSON profiles use forward slashes on Windows; the checker returns
    # native paths. Equivalent spellings must retain one bound identity.
    for name in ('source_root','config_root','settings_path','state_dir','data_root',
            'source_outputs_root','content_outputs_root','output_root',
            'catalog_database','release_store_path','report_store_path'):
        v2[1][name]=Path(v2[1][name]).as_posix()
    root, profile, directory, packet, state = produce(v2)
    assert 'input_lineage_manifest' in packet, 'actual captured producer loses source/profile/input lineage'
    manifest = json.loads((directory/'r1-input-lineage.json').read_text(encoding='utf-8'))
    assert manifest['producer']['source_head'] == profile['expected_source_head']
    assert Path(manifest['roots']['state_dir']) == Path(profile['state_dir'])
    assert any(row['path'] == str(state) and row['role'] == 'state_dir' for row in manifest['inputs'])
    assert 'Synthetic lamp' not in json.dumps(manifest)
    assert json.loads((Path(profile['data_root'])/'dashboard-paths.json').read_text(encoding='utf-8'))['synthetic_safety'] == {'network':0,'sql':0}
    from shared_platform.r1_input_lineage import inspect_review_lineage
    original_open=Path.open
    forbidden={Path(profile[name]) for name in ('settings_path','catalog_database','release_store_path','report_store_path')}
    def guarded_open(path,*args,**kwargs):
        assert path not in forbidden,'lineage must not open settings or database payload'
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded_open)
    proof = inspect_review_lineage(packet, directory)
    assert proof['status'] == 'VERIFIED_CAPTURED_V2'
    assert proof['profile_sha256'] == hashlib.sha256(Path(manifest['producer']['profile_path']).read_bytes()).hexdigest()
    # These selected store paths are references; they contain invalid synthetic
    # DB bytes and must never be opened or fingerprinted by lineage validation.
    assert not any(row['path'] in (profile['settings_path'],profile['catalog_database'],
        profile['release_store_path'],profile['report_store_path']) for row in manifest['inputs'])


@pytest.mark.parametrize(('change','diagnostic'),[
    ('manifest','MANIFEST_CHANGED'),('capture','INPUT_CHANGED'),
    ('profile','PROFILE_CHANGED'),('source','SOURCE_CHANGED'),('root','ROOT_MISMATCH'),
])
def test_consumer_rejects_exact_provenance_drift(v2, change, diagnostic):
    root, profile, directory, packet, state = produce(v2)
    from shared_platform.r1_input_lineage import inspect_review_lineage, R1LineageError
    from shared_platform.publication_rounds import canonical_digest
    path = directory/'r1-input-lineage.json'
    body = json.loads(path.read_text(encoding='utf-8'))
    if change == 'manifest':
        body['scope'] = 'tampered synthetic metadata'
        path.write_text(json.dumps(body),encoding='utf-8')
    elif change == 'capture':
        state.write_text('{"offer_id":"123","changed":true}',encoding='utf-8')
    elif change == 'profile':
        Path(body['producer']['profile_path']).write_text('{}',encoding='utf-8')
    elif change == 'source':
        (root/'core/config.py').write_text('# synthetic drift',encoding='utf-8')
    elif change == 'root':
        body['roots']['state_dir'] = profile['source_outputs_root']
        body.pop('manifest_digest')
        body['manifest_digest'] = canonical_digest(body)
        packet['input_lineage_manifest']['digest'] = body['manifest_digest']
        path.write_text(json.dumps(body),encoding='utf-8')
    with pytest.raises(R1LineageError, match='R1_LINEAGE_'+diagnostic):
        inspect_review_lineage(packet, directory)


def test_missing_manifest_is_distinct_from_unverified_legacy(v2):
    _, _, directory, packet, _ = produce(v2)
    from shared_platform.r1_input_lineage import inspect_review_lineage, R1LineageError
    missing_directory=directory/'missing-proof';missing_directory.mkdir()
    with pytest.raises(R1LineageError,match='R1_LINEAGE_INPUT_MISSING'):
        inspect_review_lineage(packet,missing_directory)
    packet.pop('input_lineage_manifest')
    assert inspect_review_lineage(packet,directory) == {
        'status':'UNVERIFIED_LEGACY','reason':'R1_LINEAGE_MANIFEST_ABSENT'}


def test_recorded_input_hardlink_rejected_before_hash(v2):
    _, _, directory, packet, state = produce(v2)
    from shared_platform.r1_input_lineage import inspect_review_lineage, R1LineageError
    os.link(state,state.parent/'synthetic-alias.json')
    with pytest.raises(R1LineageError,match='R1_LINEAGE_LINK_REJECTED'):
        inspect_review_lineage(packet,directory)


def test_producer_size_limit_stops_without_packet(v2):
    root, profile, invoke = v2
    install_actual_r1_with_dashboard_stub(root,profile)
    state=Path(profile['state_dir'])/'123.json'
    state.write_bytes(b' '+b'0'*(1024*1024))
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode==2 and 'R1_LINEAGE_SIZE_LIMIT' in result.stdout
    assert not (Path(profile['output_root'])/'product-preparation/123/first-review.json').exists()


def test_actual_open_count_is_bounded_without_tree_traversal(tmp_path):
    from shared_platform.r1_input_lineage import CapturedInputs, ROOT_FIELDS, R1LineageError
    roots={name:str(tmp_path/name) for name in ROOT_FIELDS}
    data=Path(roots['data_root']);data.mkdir()
    bound={**roots,'source_head':'a'*40,'profile_path':str(tmp_path/'profile.json'),'profile_sha256':'b'*64}
    paths=[]
    for index in range(65):
        path=data/f'selected-{index}.json';path.write_text('{}',encoding='utf-8');paths.append(path)
    capture=CapturedInputs(bound,'123')
    with pytest.raises(R1LineageError,match='R1_LINEAGE_FILE_COUNT_LIMIT'):
        with capture:
            for path in paths:
                path.read_text(encoding='utf-8')
    assert len(capture.inputs)==64


def actual_snapshot(v2):
    _, _, directory, packet, _ = produce(v2,ready=True)
    from shared_platform.publication_rounds import build_round1_snapshot, build_round1_auto_decision, AUTOPILOT_ACTOR
    decision=build_round1_auto_decision(packet,policy={'status':'ACTIVE','policy_id':'synthetic-only',
        'automatic_steps':['fact_normalization','category_candidate_resolution','title_and_copy_generation','pricing_compilation']})
    state={'_revision':8,'review':{'selected_sites':['lh_my']},'product_approval':{
        'status':'approved','approved_by':AUTOPILOT_ACTOR,'approval_authority':'ACTIVE_AUTOPILOT_POLICY',
        'decision_digest':decision['decision_digest'],'approval_id':'synthetic-frozen',
        'input_fingerprint':'sha256:'+'c'*64}}
    snapshot=build_round1_snapshot(first_review=packet,state=state,approved_by=AUTOPILOT_ACTOR,
        report_directory=directory,decision_receipt=decision)
    assert snapshot['human_approval'] is False and snapshot['fact_snapshot']['product_facts']['cost_cny']==5
    return directory,packet,snapshot


def test_snapshot_reader_binds_packet_digest_and_preserves_legacy(v2):
    directory,packet,snapshot=actual_snapshot(v2)
    from shared_platform.publication_rounds import canonical_digest, load_round1_snapshot
    from shared_platform.r1_input_lineage import R1LineageError
    path=directory/'round1-approved-snapshot.json';path.write_text(json.dumps(snapshot),encoding='utf-8')
    reports=directory.parent
    assert load_round1_snapshot('123',reports_root=reports)==snapshot
    packet['source_fixture_change']='synthetic change'
    (directory/'first-review.json').write_text(json.dumps(packet),encoding='utf-8')
    with pytest.raises(R1LineageError,match='R1_LINEAGE_PACKET_CHANGED'):
        load_round1_snapshot('123',reports_root=reports)
    snapshot.pop('input_lineage_manifest');snapshot.pop('snapshot_digest')
    snapshot['snapshot_digest']=canonical_digest(snapshot)
    path.write_text(json.dumps(snapshot),encoding='utf-8')
    assert load_round1_snapshot('123',reports_root=reports)==snapshot


def test_actual_frozen_facts_cannot_be_rewritten_with_a_new_self_digest(v2):
    directory,packet,snapshot=actual_snapshot(v2)
    from shared_platform.publication_rounds import canonical_digest,load_round1_snapshot
    from shared_platform.r1_input_lineage import R1LineageError
    for field in ('fact','target','image','stock','fact-json-type'):
        changed=deepcopy(snapshot)
        if field=='fact':changed['fact_snapshot']['product_facts']['cost_cny']=999
        elif field=='target':changed['canonical_targets']=['tiktok:LH_TH']
        elif field=='image':changed['image_plan']['source_actions']=[{'position':1,'action':'REMOVE'}]
        elif field=='stock':changed['publication_stock_policy']['quantity_per_sku']=999
        elif field=='fact-json-type':changed['fact_snapshot']['product_facts']['source_image_count']=False
        changed.pop('snapshot_digest')
        changed['snapshot_digest']=canonical_digest(changed)
        (directory/'round1-approved-snapshot.json').write_text(json.dumps(changed),encoding='utf-8')
        with pytest.raises(R1LineageError,match='R1_LINEAGE_FROZEN_PROJECTION_CHANGED'):
            load_round1_snapshot('123',reports_root=directory.parent)


@pytest.mark.parametrize(('change','diagnostic'),[
    ('capture','INPUT_CHANGED'),('profile','PROFILE_CHANGED'),('source','SOURCE_CHANGED'),
])
def test_actual_producer_rejects_drift_before_persisting_packet(v2,change,diagnostic):
    root,profile,invoke=v2
    install_actual_r1_with_dashboard_stub(root,profile)
    state=Path(profile['state_dir'])/'123.json'
    state.write_text(json.dumps({'offer_id':'123','_revision':7,'source':{}}),encoding='utf-8')
    selected={'capture':state,'profile':root.parent/'profile.json','source':root/'core/config.py'}[change]
    stub=root/'shared_platform/release_control.py'
    text=stub.read_text(encoding='utf-8')
    text=text.replace('    trace={',f'    Path({str(selected)!r}).write_text("{{}}", encoding="utf-8")\n    trace={{')
    stub.write_text(text,encoding='utf-8')
    profile['expected_source_head']=commit_fixture(root)
    result=invoke(check=False,arguments=['--offer-id','123','--targets','tiktok:LH_MY'])
    assert result.returncode==2 and 'R1_LINEAGE_'+diagnostic in result.stdout,result.stdout+result.stderr
    assert not (Path(profile['output_root'])/'product-preparation/123/first-review.json').exists()


def test_actual_capture_open_outside_selected_roots_rejected(tmp_path):
    from shared_platform.r1_input_lineage import CapturedInputs, ROOT_FIELDS, R1LineageError
    roots={name:str(tmp_path/name) for name in ROOT_FIELDS}
    bound={**roots,'source_head':'a'*40,'profile_path':str(tmp_path/'profile.json'),'profile_sha256':'b'*64}
    outside=tmp_path/'123.json';outside.write_text('{}',encoding='utf-8')
    with pytest.raises(R1LineageError,match='R1_LINEAGE_INPUT_OUTSIDE_ROOT'):
        with CapturedInputs(bound,'123'):
            outside.read_text(encoding='utf-8')
