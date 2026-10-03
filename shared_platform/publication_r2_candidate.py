"""Inspect a separately frozen footer correction without promoting it into R2.

No report persistence, upload, provider client, keep receipt or R3 input is
created here. The caller supplies the exact binding digest it wants reviewed.
"""
import hashlib
from io import BytesIO
from pathlib import Path
import re

from shared_platform.publication_takeover import TakeoverError, local_path, read_document
from shared_platform.publication_r3_image_bridge import TARGET_LOCALE, _brand_family


def _require(condition, code):
    if not condition:
        raise TakeoverError('R2_CANDIDATE_' + code)


def _key(row, number='review_number'):
    _require(isinstance(row, dict) and type(row.get(number)) is int, 'ROUTE_INVALID')
    fields = (row[number], row.get('brand_id'), row.get('locale'))
    _require(all(isinstance(v, str) and v for v in fields[1:]), 'ROUTE_INVALID')
    return fields


def _index(rows, number='review_number'):
    _require(isinstance(rows, list) and 0 < len(rows) <= 100, 'COVERAGE_INVALID')
    indexed = {_key(row, number): row for row in rows}
    _require(len(indexed) == len(rows), 'DUPLICATE_ROUTE')
    return indexed


def _asset(path, digest, observed):
    path = local_path(path)
    _require(isinstance(digest,str) and re.fullmatch(r'[0-9a-f]{64}',digest), 'DIGEST_INVALID')
    _require(path.is_file() and path.stat().st_size <= 32 * 1024 * 1024, 'ASSET_MISSING')
    raw = path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == digest, 'ASSET_DRIFT')
    observed[str(path)] = digest
    return raw


def _protected_pixels(source, output, row):
    from PIL import Image
    with Image.open(BytesIO(source)) as a, Image.open(BytesIO(output)) as b:
        _require(a.size == b.size and max(a.size) <= 4096, 'DIMENSIONS_INVALID')
        width,height=a.size
        box,footer=row.get('protected_region'),row.get('footer_region')
        _require(isinstance(box,list) and isinstance(footer,list) and len(box)==len(footer)==4
                 and all(type(n) is int for n in box+footer), 'REGION_INVALID')
        _require(box[:3]==[0,0,width] and 0<box[3]<height
                 and footer[:3]==[0,box[3],width] and box[3]<footer[3]<=height, 'REGION_INVALID')
        a,b=a.convert('RGBA'),b.convert('RGBA')
        protected=a.crop(box).tobytes()
        _require(protected == b.crop(box).tobytes()
                 and hashlib.sha256(protected).hexdigest() == row.get('protected_region_sha256'),
                 'ARTWORK_CHANGED')
        # Both regions use PIL half-open boxes: right/bottom are excluded.
        # The first row outside the footer must also remain unchanged.
        bottom=(0,footer[3],width,height)
        if bottom[1]<height:
            _require(a.crop(bottom).tobytes()==b.crop(bottom).tobytes(), 'OUTSIDE_FOOTER_CHANGED')


def inspect_candidate(*, binding_path, expected_sha256, takeover):
    """Return pending review routes; the formal R2 consumer result stays intact."""
    try:
        return _inspect_candidate(binding_path, expected_sha256, takeover)
    except (KeyError, TypeError, AttributeError, OSError, ValueError) as error:
        if isinstance(error, TakeoverError):
            raise
        raise TakeoverError('R2_CANDIDATE_DOCUMENT_INVALID') from None


def _inspect_candidate(binding_path, expected_sha256, takeover):
    _require(takeover.get('status')=='READ_ONLY_BOUND' and takeover.get('round1_identity_valid') is True,
             'TAKEOVER_REQUIRED')
    path=local_path(binding_path)
    observed=dict(takeover['observed_files'])
    for filename,digest in observed.items():
        _require(hashlib.sha256(local_path(filename).read_bytes()).hexdigest()==digest, 'EVIDENCE_CHANGED')
    binding=read_document(path,observed)
    _require(observed[str(path)]==expected_sha256, 'BINDING_DRIFT')
    directory=local_path(takeover['report_root'])
    r1=read_document(directory/'round1-approved-snapshot.json',observed)
    plan_path=directory/'brand-image-translation-plan.json'
    plan=read_document(plan_path,observed)
    generation=read_document(directory/'brand-image-generation.json',observed)
    translated=read_document(directory/'brand-image-translation.json',observed)
    _require(binding.get('schema_version')=='r2-localization-candidate-binding/v1'
             and binding.get('offer_id')==takeover['offer_id']
             and all(doc.get('offer_id')==takeover['offer_id'] for doc in (r1,plan,generation,translated)),
             'OFFER_CONFLICT')
    _require(binding.get('status')=='PENDING_USER_VISUAL_QA'
             and all(binding.get('accepted_actions',{}).get(k) is False for k in
                     ('user_keep','formal_report_write','workbench_write','miaoshou_write','marketplace_write')),
             'PENDING_STATE_REQUIRED')
    _require(type(binding.get('revision')) is int
             and binding['revision']==takeover['approved_revision']==takeover['current_revision']
             and binding.get('approved_fact_snapshot_sha256')==r1.get('product_approval_fingerprint')
             and binding.get('round1_snapshot_sha256')==observed[str(directory/'round1-approved-snapshot.json')]
             and binding.get('translation_plan_sha256')==observed[str(plan_path)], 'PREDECESSOR_DRIFT')
    manifest_path=local_path(binding['candidate_manifest']['path'])
    _require(manifest_path.is_relative_to(path.parent), 'MANIFEST_PATH_ESCAPE')
    manifest=read_document(manifest_path,observed)
    _require(observed[str(manifest_path)]==binding['candidate_manifest'].get('sha256')
             and manifest.get('schema_version')=='deterministic-footer-localization-candidate/v1'
             and manifest.get('offer_id')==takeover['offer_id']
             and manifest.get('status')=='VISUAL_QA_REQUIRED', 'MANIFEST_DRIFT')
    routes,rows,tasks=(_index(binding.get('routes')),_index(manifest.get('rows')),_index(plan.get('tasks')))
    prior=_index(translated.get('assets'),'source_review_number')
    _require(set(routes)==set(rows)==set(tasks)==set(prior)
             and type(plan.get('approved_task_count')) is int
             and plan['approved_task_count']==len(tasks), 'COVERAGE_CONFLICT')
    masters=generation.get('assets')
    _require(isinstance(masters,list) and all(isinstance(r,dict) and type(r.get('review_number')) is int
             for r in masters), 'MASTER_INVALID')
    by_number={r['review_number']:r for r in masters}
    _require(len(by_number)==len(masters), 'MASTER_DUPLICATED')
    result=[]
    for key,route in routes.items():
        row,task,old=rows[key],tasks[key],prior[key]
        master=by_number.get(key[0],{})
        digest=route.get('master_sha256')
        _require(master.get('brand_id')==key[1]
                 and route.get('role')==task.get('role')==master.get('role')==old.get('role')
                 and isinstance(digest,str)
                 and master.get('artifact_digest')==task.get('source_artifact_digest')=='sha256:'+digest
                 and row.get('source_sha256')==digest
                 and task.get('source_url')==master.get('public_url')
                 and type(route.get('historical_provider_task_id')) is int
                 and route['historical_provider_task_id']>0
                 and route['historical_provider_task_id']==old.get('provider_task_id'), 'SOURCE_CONFLICT')
        output_name=route.get('output')
        _require(isinstance(output_name,str) and re.fullmatch(r'[A-Za-z0-9_.-]+',output_name)
                 and output_name not in {'.','..'}, 'OUTPUT_PATH_ESCAPE')
        output_path=local_path(row['output_path'])
        _require(output_path==manifest_path.parent/output_name
                 and row.get('output_sha256')==route.get('output_sha256')
                 and row.get('protected_region_sha256')==route.get('protected_region_sha256'), 'OUTPUT_CONFLICT')
        source_path=local_path(row['source_path'])
        _require(source_path.is_relative_to(directory), 'SOURCE_PATH_ESCAPE')
        source=_asset(source_path,digest,observed)
        output=_asset(output_path,route['output_sha256'],observed)
        _protected_pixels(source,output,row)
        targets=[label for label in takeover['targets'] if TARGET_LOCALE.get(label)==key[2]
            and _brand_family(key[1])==('homebloom-sea' if label.startswith('tiktok:HB_') else 'livelyhive-sea')]
        _require(bool(targets), 'TARGET_ROUTE_CONFLICT')
        result.append({'review_number':key[0],'brand_id':key[1],'locale':key[2],'role':route['role'],
            'targets':targets,'output_path':str(output_path),'output_sha256':route['output_sha256'],
            'master_sha256':digest,'footer_text':row.get('footer_text'),
            'protected_pixels_verified':True,'status':'PENDING_USER_VISUAL_QA'})
    for filename,digest in observed.items():
        _require(hashlib.sha256(local_path(filename).read_bytes()).hexdigest()==digest, 'EVIDENCE_CHANGED')
    return {'schema_version':'publication-r2-candidate-review/v1','offer_id':takeover['offer_id'],
        'status':'PENDING_USER_VISUAL_QA','binding_sha256':expected_sha256,
        'candidate_code_commit':binding.get('candidate_code_commit'),
        'candidate_asset_count':len(result),'keep_count':0,'execution_authority':False,'r3_ready':False,
        'business_writes':0,'paid_requests':0,'routes':result,'observed_files':observed,
        'next_action':'REVIEW_EXACT_CANDIDATE_IMAGES'}
