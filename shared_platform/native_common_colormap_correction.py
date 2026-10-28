"""One standing-authority-derived correction of a captured colorMap rejection.

The two UNKNOWN attempts remain immutable. This is a new finite plan, never
replay, zero-write certification, CREATE authority or marketplace approval.
"""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from hashlib import sha256
import json,re,sqlite3
from shared_platform import native_common_baseline_successor as baseline

KEY='native_common_colormap_correction'
SCHEMA='native-common-colormap-correction-decision/v1'
RELATION_SCHEMA='native-common-colormap-correction/v1'
REJECTION='存在SKU时，颜色规格（colorMap）必填'
_fail=baseline._fail
_digest=baseline._digest


@dataclass(frozen=True)
class PinnedColorMapDecision:
    path: Path
    sha256: str

    def read(self):
        if not self.path.is_absolute() or not re.fullmatch('[0-9a-f]{64}',str(self.sha256)):
            _fail('COMMON_COLORMAP_TECHNICAL_PIN_REQUIRED')
        raw=self.path.read_bytes()
        if sha256(raw).hexdigest()!=self.sha256:_fail('COMMON_COLORMAP_TECHNICAL_PIN_CHANGED')
        from shared_platform.native_common_signing_context import _unique_object
        value=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_unique_object)
        required=dict(schema_version=SCHEMA,decision_status='STANDING_AUTHORITY_DERIVED',actor='Kyle',
            action_class='AUTHORIZE_ONE_KNOWN_COLORMAP_REJECTION_CORRECTION',maximum_new_edit_attempts=1,
            combined_edit_attempt_cap=3,combined_create_attempt_cap=3,further_create_authorized=False,
            both_unknowns_retained=True,zero_write_proven=False,same_task_and_preparation_root_required=True,
            candidate_content_changes_authorized=False,marketplace_approval=False,standing_authority=True,new_unknown_stops=True)
        if type(value) is not dict or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in required.items()):
            _fail('COMMON_COLORMAP_FINITE_TECHNICAL_DECISION_REQUIRED')
        for name in ('original_adoption_digest','original_payload_digest','original_ledger_sha256','current_detail_sha256',
                     'rejected_payload_digest','rejected_ledger_sha256','failure_evidence_digest','response_canonical_sha256'):
            if not re.fullmatch('[0-9a-f]{64}',str(value.get(name))):_fail('COMMON_COLORMAP_EXACT_SCOPE_REQUIRED')
        for name in ('task_id','offer_id','original_plan_id','original_run_id','common_detail_id','rejected_plan_id','rejected_run_id'):
            if type(value.get(name)) is not str or not value[name]:_fail('COMMON_COLORMAP_EXACT_SCOPE_REQUIRED')
        old=baseline.PinnedBaselineDecision(**_pin(value.get('baseline_decision'))).read()
        if any(value.get(k)!=old[k] for k in ('task_id','offer_id','original_adoption_digest','original_plan_id',
                'original_run_id','original_payload_digest','original_ledger_sha256','common_detail_id','current_detail_sha256')):
            _fail('COMMON_COLORMAP_ORIGINAL_DECISION_CHANGED')
        pin=_pin(value.get('standing_authority_pin'));raw=pin['path'].read_bytes()
        if sha256(raw).hexdigest()!=pin['sha256']:_fail('COMMON_COLORMAP_STANDING_AUTHORITY_CHANGED')
        authority=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_unique_object)
        scope=authority.get('immediate_scope') or {}
        if (authority.get('schema_version')!='miaoshou-unpublished-draft-edit-standing-authority/v1'
            or authority.get('action_class')!='STANDING_PERMISSION_TO_EDIT_UNPUBLISHED_PREPARATION_DRAFTS'
            or authority.get('actor')!='Kyle' or authority.get('standing_authority') is not True
            or any(scope.get(k)!=value[k] for k in ('task_id','offer_id','common_detail_id'))):
            _fail('COMMON_COLORMAP_STANDING_AUTHORITY_REQUIRED')
        if type(value.get('variant_bindings')) is not list or not value['variant_bindings']:
            _fail('COMMON_COLORMAP_FROZEN_VARIANT_BINDINGS_REQUIRED')
        return value


def _pin(value):
    if type(value) is not dict or set(value)!={'path','sha256'} or not Path(value['path']).is_absolute() or not re.fullmatch('[0-9a-f]{64}',str(value['sha256'])):
        _fail('COMMON_COLORMAP_TECHNICAL_PIN_REQUIRED')
    return dict(path=Path(value['path']),sha256=value['sha256'])


def installed_decision(config):
    pin=config.get('agent_prepared_common_colormap_decision')
    if pin is None:return None
    reader=PinnedColorMapDecision(**_pin(pin));reader.read();return reader


def relation(reader):
    if type(reader) is not PinnedColorMapDecision:_fail('COMMON_COLORMAP_FINITE_TECHNICAL_DECISION_REQUIRED')
    return {**reader.read(),'schema_version':RELATION_SCHEMA,'decision_path':str(reader.path),'decision_sha256':reader.sha256}


def rejected_snapshot(db,reader):
    """Verify historical stored bytes/binding, never apply a new DTO to old wire."""
    decision=reader.read();oldreader=baseline.PinnedBaselineDecision(**_pin(decision['baseline_decision']))
    original,_=baseline.original_snapshot(db,oldreader.read())
    plan=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(decision['rejected_plan_id'],)).fetchone()
    run=db.execute('SELECT * FROM release_runs WHERE run_id=? AND plan_id=?',(decision['rejected_run_id'],decision['rejected_plan_id'])).fetchone()
    target=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(decision['rejected_run_id'],'miaoshou:COMMON')).fetchone()
    if (plan is None or run is None or target is None or run['approval_id'] is not None or run['technical_execution_state']!='UNKNOWN'
        or target['status']!='RUNNING' or target['attempts']!=1 or plan['payload_digest']!=decision['rejected_payload_digest']
        or plan['product_id']!=decision['offer_id'] or _digest(json.loads(plan['payload_json']))!=decision['rejected_payload_digest']
        or json.loads(plan['payload_json'])!=baseline.successor_payload(json.loads(original['payload_json']),oldreader)):
        _fail('COMMON_COLORMAP_REJECTED_PARENT_CHANGED')
    from shared_platform import native_common_technical_execution as technical
    binding=technical._binding(run)
    snapshot=dict(run=dict(run),target=dict(target))
    for table in ('release_target_submissions','release_target_failure_events','release_target_readbacks'):
        snapshot[table]=[dict(row) for row in db.execute('SELECT * FROM '+table+' WHERE run_id=? ORDER BY target_label',(run['run_id'],))]
    failures=snapshot['release_target_failure_events']
    if snapshot['release_target_submissions'] or snapshot['release_target_readbacks'] or len(failures)!=1:
        _fail('COMMON_COLORMAP_EXACT_REJECTION_REQUIRED')
    row=failures[0];value=json.loads(row['evidence_json'])
    if (_digest(snapshot)!=decision['rejected_ledger_sha256'] or sha256(row['evidence_json'].encode()).hexdigest()!=row['evidence_digest']
        or row['evidence_digest']!=decision['failure_evidence_digest'] or value.get('schema_version')!='native-common-edit-outcome/v1'
        or value.get('binding')!=binding or value.get('run_id')!=run['run_id'] or value.get('attempt')!=1
        or value.get('outcome')!='BUSINESS_REJECTED' or value.get('provider_result')!='fail' or value.get('provider_reason')!=REJECTION
        or value.get('response_canonical_sha256')!=decision['response_canonical_sha256']
        or value.get('zero_write_proven') is not False or value.get('request_attempted') is not True):
        _fail('COMMON_COLORMAP_EXACT_REJECTION_REQUIRED')
    return plan,snapshot


def frozen_projection(payload,first,decision,assets):
    """Bind each first SKU key part to its frozen R1 label and R2 image."""
    english=[row['copy'] for row in first['targets'] if row['target'] in {'tiktok:LH_PH','shopee:PH'}]
    if not english or not str(english[0].get('specification_name') or '').strip():_fail('COMMON_COLORMAP_FROZEN_SPEC_NAME_REQUIRED')
    english_by_model={row['seller_sku']:row['display_name'] for row in english[0].get('variants') or []}
    selected=(payload.get('product_facts') or {}).get('selected_skus') or []
    r1={row['source_key']:row for row in first['product_facts']['skus']}
    rows=decision['variant_bindings'];bykey={row.get('variant_key'):row for row in rows}
    if len(bykey)!=len(rows) or set(bykey)!={row['key'] for row in selected}:_fail('COMMON_COLORMAP_VARIANT_ALIGNMENT_CHANGED')
    urls=[image.get('image_url') for image in payload.get('images') or []]
    colors={};digests=[];roles=[]
    for sku in selected:
        key=sku['key'];parts=key.strip(';').split(';');row=bykey[key];frozen=r1.get(key) or {}
        matches=[image for image in assets if image.get('role')==row.get('role') and image.get('image_url')==row.get('image_url')
            and image.get('artifact_digest')==row.get('artifact_digest')]
        if (len(parts)!=1 or not parts[0] or parts[0] in colors or len(matches)!=1
            or row.get('model_sku')!=sku.get('model_sku') or row.get('model_sku')!=frozen.get('seller_sku')
            or row.get('name')!=frozen.get('approved_display_name') or row.get('name')!=sku.get('label')
            or row.get('english_display_name')!=english_by_model.get(row.get('model_sku'))
            or urls.count(row.get('image_url'))!=1 or not str(row.get('image_url') or '').startswith('https://')
            or not re.fullmatch('sha256:[0-9a-f]{64}',str(row.get('artifact_digest')))):
            _fail('COMMON_COLORMAP_VARIANT_ALIGNMENT_CHANGED')
        colors[parts[0]]=dict(name=row['name'],imgUrls=[row['image_url']],imgUrl=row['image_url'])
        digests.append(row['artifact_digest']);roles.append(row['role'])
    if len(set(digests))!=len(rows) or len(set(roles))!=len(rows):_fail('COMMON_COLORMAP_VARIANT_IMAGE_AMBIGUOUS')
    return dict(colorPropName=english[0]['specification_name'],colorMap=colors)


def generation_assets(repo_root,payload):
    from shared_platform.publication_r3_image_bridge import _brand_generation_identity_digest,_canonical_digest,_brand_family
    from shared_platform.immutable_approval_files import require_local_path
    from shared_platform.publication_image_qa import is_completed_image_asset
    root=Path(repo_root);path=root/'reports/product-preparation'/payload['product_id']/'brand-image-generation.json'
    require_local_path(path,root=root);generation=json.loads(path.read_bytes())
    identity=payload['r3_stage_binding']['r2_identity']
    if (_brand_generation_identity_digest(generation)!=identity['generation_identity_digest']
        or _canonical_digest(generation)!=identity['generation_digest']):_fail('COMMON_COLORMAP_FROZEN_R2_CHANGED')
    return [dict(role=r['role'],image_url=r['public_url'],artifact_digest=r['artifact_digest'])
        for r in generation['assets'] if _brand_family(r['brand_id'])=='livelyhive-sea' and is_completed_image_asset(r)]


def correction_payload(db,original,reader,*,repo_root):
    decision=reader.read();parent,_=rejected_snapshot(db,reader)
    oldreader=baseline.PinnedBaselineDecision(**_pin(decision['baseline_decision']))
    if baseline.successor_payload(original,oldreader)!=json.loads(parent['payload_json']):_fail('COMMON_COLORMAP_FROZEN_PAYLOAD_CHANGED')
    from shared_platform.native_common_budget_facts import _stored_preparation
    prepared=_stored_preparation(db,decision['offer_id'],original['r3_stage_binding']['native_preparation_source']['prepared_reference'])
    result=deepcopy(json.loads(parent['payload_json']))
    result[KEY]=dict(relation=relation(reader),dto=frozen_projection(result,prepared['packet'],decision,generation_assets(repo_root,result)))
    from shared_platform.publication_r3_image_bridge import common_stage_plan_id
    result['plan_id']=common_stage_plan_id(result,offer_id=decision['offer_id'])
    return result


def _service(store):
    service=baseline._service(store);reader=getattr(service,'colormap_decision',None)
    if type(reader) is not PinnedColorMapDecision:_fail('COMMON_COLORMAP_FINITE_TECHNICAL_DECISION_REQUIRED')
    decision=reader.read()
    if decision['baseline_decision']!=dict(path=str(service.baseline_decision.path),sha256=service.baseline_decision.sha256):
        _fail('COMMON_COLORMAP_ORIGINAL_DECISION_CHANGED')
    with sqlite3.connect((Path(service.profile.data_root)/'tasks.db').resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT record_json FROM agent_prepared_source_successors_v6 WHERE task_id=?',(service.task_id,)).fetchone()
    if row is None or json.loads(row[0]).get('correction_relation')!=relation(reader):_fail('COMMON_COLORMAP_SIGNED_V6_REQUIRED')
    return service


def select_payload(store,payload):
    service=_service(store)
    with store._connect_readonly() as db:
        db.execute('BEGIN');return correction_payload(db,payload,service.colormap_decision,repo_root=service.profile.root)


def context(store,db,payload):
    service=_service(store);reader=service.colormap_decision;decision=reader.read()
    original,_=baseline.original_snapshot(db,service.baseline_decision.read())
    if payload!=correction_payload(db,json.loads(original['payload_json']),reader,repo_root=service.profile.root):_fail('COMMON_COLORMAP_FROZEN_PAYLOAD_CHANGED')
    return decision


def dto(payload):
    """Only new relation-bearing plans change the builder; old hashes stay old."""
    return deepcopy(payload[KEY]['dto']) if KEY in payload else {}
