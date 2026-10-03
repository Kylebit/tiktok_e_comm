"""Adopt reviewed deterministic repairs without inventing a provider generation.

Preparation writes only an isolated QA bundle. Activation retains old documents,
rechecks user choices/source hashes and consumes real upload and QA evidence.
No provider, upload, budget, approval or marketplace APIs are called here.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse

from shared_platform import publication_r2_review as review
from shared_platform.publication_takeover import local_path
from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, validate_r2_identity

RECEIPT='r2-adopted-assets-receipt.json'


def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _raw(value):return review._bytes(value)
def _read(path):return json.loads(local_path(path).read_bytes())
def _require(value,code):
    if not value:raise ValueError('R2_ADOPTION_'+code)


def _snapshot(runtime_root,offer_id,expected_revision):
    project,registration,_,candidate,decision=review._load(offer_id,runtime_root)
    _require(type(expected_revision) is int and decision['revision']==expected_revision,'REVIEW_REVISION_CHANGED')
    _require(all(row['action']=='keep' for row in decision['decisions']),'KEEP_REQUIRED')
    reports=project/'reports/product-preparation'/offer_id
    originals={name:(reports/name).read_bytes() for name in R2_DOCUMENTS.values()}
    return project,registration,candidate,decision,originals


def _translation(registration,candidate,decision,original,uploads):
    rows=review._rows(candidate)
    _require(set(uploads)=={'local-footer:'+r['output_sha256'] for r in rows},'UPLOAD_COVERAGE')
    translated=deepcopy(json.loads(original))
    old={(r.get('source_review_number'),r.get('brand_id'),r.get('locale')):r for r in translated['assets']}
    assets=[]
    for row in rows:
        identity='local-footer:'+row['output_sha256'];upload=uploads[identity]
        url=urlparse(upload.get('public_url',''))
        _require(url.scheme=='https' and bool(url.hostname) and not url.username and not url.password,'PUBLIC_HTTPS_REQUIRED')
        _require(upload.get('artifact_digest')=='sha256:'+row['output_sha256']
                 and isinstance(upload.get('upload_receipt_ref'),str) and upload['upload_receipt_ref'],'UPLOAD_IDENTITY')
        readback=local_path(upload['readback_path'])
        _require(_sha(readback.read_bytes())==upload.get('readback_sha256'),'UPLOAD_READBACK_DRIFT')
        from PIL import Image
        with Image.open(row['output_path']) as approved,Image.open(readback) as delivered:
            _require(delivered.format in {'PNG','JPEG','WEBP'} and approved.width*delivered.height==approved.height*delivered.width,'DELIVERY_FORMAT_OR_RATIO')
            dimensions={'source':list(approved.size),'delivered':list(delivered.size),'format':delivered.format}
        # Resolution acceptance remains an explicit frozen-workflow requirement,
        # never an implicit permission to lower a source or platform threshold.
        if upload['readback_sha256']!=row['output_sha256']:
            _require(upload.get('transformation')=='official-media-transcode'
                and upload.get('dimensions')==dimensions,'DELIVERY_TRANSFORM_RECEIPT')
        previous=old[(row['review_number'],row['brand_id'],row['locale'])]
        # A local repair is a distinct asset, not the old provider task's output.
        assets.append({'source_review_number':row['review_number'],'brand_id':row['brand_id'],'role':row['role'],
            'locale':row['locale'],'status':'COMPLETED','artifact_id':identity,
            'artifact_path':str(readback),'artifact_digest':'sha256:'+upload['readback_sha256'],
            'public_url':upload['public_url'],'provider':'deterministic-local-footer',
            'external_generation_count':0,'upload_receipt':deepcopy(upload),
            'local_provenance':{'schema_version':'adopted-footer-repair/v1','binding_sha256':registration['binding_sha256'],
                'candidate_code_commit':candidate.get('candidate_code_commit'),'master_sha256':row['master_sha256'],
                'protected_pixels_verified':row['protected_pixels_verified'],'footer_text':row['footer_text'],
                'reviewed_artifact_digest':'sha256:'+row['output_sha256'],'reviewed_artifact_path':row['output_path'],
                'delivery_dimensions':dimensions,
                'decision_revision':decision['revision'],'image_id':row['image_id'],
                'superseded_asset':deepcopy(previous)}})
    translated['assets']=assets
    translated['status']='LOCALIZED_IMAGE_REVIEW_REQUIRED'
    translated['local_adoption']={'schema_version':'r2-local-adoption/v1','historical_status':json.loads(original).get('status'),
        'binding_sha256':registration['binding_sha256'],'decision_revision':decision['revision'],
        'new_paid_requests':0,'budget_authority_changed':False}
    # Existing counters, policy blocker evidence and failed provider records remain intact.
    return translated


def _reviewed_translation(translated):
    result=deepcopy(translated)
    for asset in result['assets']:
        provenance=asset['local_provenance']
        asset.update(artifact_digest=provenance['reviewed_artifact_digest'],artifact_path=provenance['reviewed_artifact_path'])
    return result


def prepare_adoption(*,runtime_root,offer_id,expected_revision,uploaded_assets_path,output_dir):
    project,registration,candidate,decision,originals=_snapshot(runtime_root,offer_id,expected_revision)
    output=local_path(output_dir)
    _require(not output.is_relative_to(project) and not output.is_relative_to(local_path(registration['source_root'])),'OUTPUT_ISOLATION')
    uploads_raw=local_path(uploaded_assets_path).read_bytes();uploads=json.loads(uploads_raw)
    _require(set(uploads)=={'uploaded_assets'},'UPLOAD_MANIFEST')
    translated=_translation(registration,candidate,decision,originals['brand-image-translation.json'],uploads['uploaded_assets'])
    bundle={'schema_version':'r2-adoption-preparation/v1','offer_id':offer_id,'runtime_root':str(local_path(runtime_root)),
        'binding_sha256':registration['binding_sha256'],'decision_revision':decision['revision'],
        'original_hashes':{name:_sha(raw) for name,raw in originals.items()},
        'uploaded_assets_path':str(local_path(uploaded_assets_path)),'uploaded_assets_sha256':_sha(uploads_raw),
        'translation_sha256':_sha(_raw(translated)),'publication_authorized':False}
    output.mkdir(parents=True,exist_ok=True)
    for name,raw in {**originals,'brand-image-translation.json':_raw(translated),
                     'reviewed-original-translation.json':_raw(_reviewed_translation(translated)),'adoption-preparation.json':_raw(bundle)}.items():
        path=output/name
        if path.exists():_require(path.read_bytes()==raw,'PREPARATION_CONFLICT')
        else:path.write_bytes(raw)
    return {'bundle_path':str(output/'adoption-preparation.json'),'reports_directory':str(output),
            'artifact_digests':sorted({a['artifact_digest'] for a in json.loads(originals['brand-image-generation.json'])['assets']+translated['assets']}),
            'status':'QA_REQUIRED','new_paid_requests':0}


def adopt_prepared(*,bundle_path,qa_path,qa_sha256,assessment_path,original_qa_path=None,original_assessment_path=None):
    bundle=_read(bundle_path);offer=bundle['offer_id'];runtime=bundle['runtime_root']
    _require(bundle.get('schema_version')=='r2-adoption-preparation/v1' and bundle.get('publication_authorized') is False,'BUNDLE_INVALID')
    project=review.registered_project(offer,runtime_root=runtime)
    with review._decision_lock(project.parent/'decision.lock'):
        project,registration,candidate,decision,current=_snapshot(runtime,offer,bundle['decision_revision'])
        _require(registration['binding_sha256']==bundle['binding_sha256'],'BINDING_CHANGED')
        archive=project.parent/'adoption-history'/bundle['translation_sha256'][:16]
        originals={name:(archive/name).read_bytes() if (archive/name).exists() else raw for name,raw in current.items()}
        _require({name:_sha(raw) for name,raw in originals.items()}==bundle['original_hashes'],'SOURCE_REPORT_CHANGED')
        uploads_raw=local_path(bundle['uploaded_assets_path']).read_bytes()
        _require(_sha(uploads_raw)==bundle['uploaded_assets_sha256'],'UPLOAD_MANIFEST_CHANGED')
        translated=_translation(registration,candidate,decision,originals['brand-image-translation.json'],json.loads(uploads_raw)['uploaded_assets'])
        _require(_sha(_raw(translated))==bundle['translation_sha256'],'PREPARATION_CHANGED')
        qa_raw=local_path(qa_path).read_bytes();_require(_sha(qa_raw)==qa_sha256,'QA_FILE_CHANGED')
        qa=json.loads(qa_raw);assessment=_read(assessment_path)
        documents={key:json.loads(originals[name]) for key,name in R2_DOCUMENTS.items()}
        documents.update(translation_result=translated,image_qa=qa)
        from shared_platform.publication_image_qa import build_automated_image_qa
        calculated=build_automated_image_qa(round1_snapshot=documents['round1_snapshot'],generation=documents['generation_result'],
            translation=translated,visual_assessment=assessment)
        _require(calculated['status']=='PASSED' and all(qa.get(k)==v for k,v in calculated.items() if k!='qa_digest'),'QA_EVIDENCE_MISMATCH')
        reviewed=_reviewed_translation(translated)
        same_assets=all(a['artifact_digest']==b['artifact_digest'] for a,b in zip(reviewed['assets'],translated['assets']))
        _require(same_assets or (original_qa_path and original_assessment_path),'ORIGINAL_QA_REQUIRED')
        original_qa=_read(original_qa_path) if original_qa_path else qa
        original_assessment=_read(original_assessment_path) if original_assessment_path else assessment
        checked_original=build_automated_image_qa(round1_snapshot=documents['round1_snapshot'],generation=documents['generation_result'],
            translation=reviewed,visual_assessment=original_assessment)
        _require(checked_original['status']=='PASSED' and all(original_qa.get(k)==v for k,v in checked_original.items() if k!='qa_digest'),'ORIGINAL_QA_MISMATCH')
        for asset in documents['generation_result']['assets']+translated['assets']:
            _require('sha256:'+_sha(local_path(asset['artifact_path']).read_bytes())==asset['artifact_digest'],'ASSET_BYTES_CHANGED')
        identity=validate_r2_identity(documents)
        updated={'brand-image-translation.json':_raw(translated),'automated-image-qa.json':qa_raw}
        receipt={'schema_version':'r2-adopted-assets/v1','offer_id':offer,'binding_sha256':bundle['binding_sha256'],
            'decision_revision':decision['revision'],'original_hashes':bundle['original_hashes'],
            'active_hashes':{name:_sha(raw) for name,raw in updated.items()},'r2_identity':identity,
            'original_qa':original_qa,'original_assessment':original_assessment,
            'delivery_assessment':assessment,'assessment_sha256':_sha(local_path(assessment_path).read_bytes()),'new_paid_requests':0,'publication_authorized':False}
        reports=project/'reports/product-preparation'/offer
        for name,raw in current.items():
            _require(raw==originals[name] or (name in updated and raw==updated[name]),'ACTIVE_REPORT_CHANGED')
        archive.mkdir(parents=True,exist_ok=True)
        for name,raw in originals.items():
            target=archive/name
            if target.exists():_require(target.read_bytes()==raw,'HISTORY_CHANGED')
            else:target.write_bytes(raw)
        # Receipt is committed last. A partial activation cannot pass the loader.
        for name,raw in {**updated,RECEIPT:_raw(receipt)}.items():
            target=reports/name
            if target.exists() and target.read_bytes()==raw:continue
            temp=target.with_name(target.name+'.tmp-'+uuid4().hex)
            with temp.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,target)
        return receipt


def validate_adoption_receipt(reports,documents,registration,decision):
    try:receipt=_read(Path(reports)/RECEIPT)
    except (OSError,ValueError):raise ValueError('R2_ADOPTION_RECEIPT_REQUIRED') from None
    _require(receipt.get('schema_version')=='r2-adopted-assets/v1' and receipt.get('publication_authorized') is False,'RECEIPT_REQUIRED')
    _require(receipt.get('offer_id')==registration['offer_id'] and receipt.get('binding_sha256')==registration['binding_sha256']
        and receipt.get('decision_revision')==decision['revision'],'RECEIPT_SCOPE')
    for name,digest in receipt['active_hashes'].items():
        _require(name in {'brand-image-translation.json','automated-image-qa.json'} and _sha((Path(reports)/name).read_bytes())==digest,'ACTIVE_RECEIPT_DRIFT')
    _require(set(receipt['active_hashes'])=={'brand-image-translation.json','automated-image-qa.json'},'RECEIPT_COVERAGE')
    _require(receipt['r2_identity']==validate_r2_identity(documents),'RECEIPT_IDENTITY')
