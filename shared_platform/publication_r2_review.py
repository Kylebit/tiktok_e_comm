"""Register one frozen candidate and persist explicit image choices locally.

Registration is preparation, never adoption. The original evidence is read only;
R3 consumes the adoption record beside this product's projected R2 documents.
"""
import hashlib
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from shared_platform.publication_takeover import inspect_publication, local_path, read_document

ADOPTION = 'r2-candidate-adoption.json'
REGISTRATION = 'r2-candidate-registration.json'
R2_REVIEW_RUNTIME_ROOT_ENV = 'ORBIT_R2_REVIEW_RUNTIME_ROOT'


def review_runtime_root(default_runtime_root):
    """Resolve the read-only adopted-candidate registry selected at startup."""
    selected = os.environ.get(R2_REVIEW_RUNTIME_ROOT_ENV)
    if not selected:
        return local_path(default_runtime_root)
    path = Path(selected).expanduser()
    _require(path.is_absolute(), 'RUNTIME_ROOT_NOT_ABSOLUTE')
    resolved = local_path(path)
    _require(resolved.is_dir(), 'RUNTIME_ROOT_MISSING')
    return resolved


def _require(value, code):
    if not value:
        raise ValueError('R2_REVIEW_' + code)


def _offer(value):
    _require(type(value) is str and re.fullmatch(r'[0-9]{1,32}', value), 'OFFER_INVALID')
    return value


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _directory(offer_id, runtime_root):
    return local_path(runtime_root) / 'data/r2_candidate_reviews' / _offer(offer_id)


def registered_project(offer_id, *, runtime_root):
    directory = _directory(offer_id, runtime_root)
    _require(local_path(directory / 'registration.json').is_file(), 'NOT_REGISTERED')
    return local_path(directory / 'project')


def has_registration(offer_id, *, runtime_root):
    if type(offer_id) is not str or not re.fullmatch(r'[0-9]{1,32}',offer_id):
        return False
    return local_path(_directory(offer_id, runtime_root) / 'registration.json').is_file()


def _candidate(registration):
    from shared_platform.publication_r2_candidate import inspect_candidate
    takeover = inspect_publication(offer_id=registration['offer_id'], data_root=registration['source_root'])
    result = inspect_candidate(binding_path=registration['binding_path'],
        expected_sha256=registration['binding_sha256'], takeover=takeover)
    return takeover, result


def register_candidate(*, runtime_root, source_root, offer_id, binding_path, expected_sha256):
    """Explicit local installation only. GET never calls this function."""
    offer_id = _offer(offer_id)
    directory = _directory(offer_id, runtime_root)
    registration = {'schema_version':'r2-review-registration/v1', 'offer_id':offer_id,
        'runtime_root':str(local_path(runtime_root)), 'source_root':str(local_path(source_root)),
        'binding_path':str(local_path(binding_path)), 'binding_sha256':expected_sha256}
    takeover, result = _candidate(registration)
    existing = directory / 'registration.json'
    if local_path(existing).exists():
        old = read_document(existing, {})
        _require(all(old.get(k) == v for k,v in registration.items()), 'REGISTRATION_CONFLICT')
        return review_view(offer_id, runtime_root=runtime_root)
    _require(not directory.exists(), 'INCOMPLETE_REGISTRATION')
    original = local_path(source_root)
    project_files = {}
    # An explicit product allowlist: no database, credentials or policy import.
    names = ('round1-approved-snapshot.json','first-review.json','round1-auto-decision.json',
        'brand-image-generation.json','brand-image-translation-plan.json',
        'brand-image-translation.json','automated-image-qa.json','round2-blocker.json')
    for name in names:
        path = local_path(original / 'reports/product-preparation' / offer_id / name)
        if path.is_file():
            project_files[f'reports/product-preparation/{offer_id}/{name}'] = path.read_bytes()
    state = read_document(original / 'data/new_product_workbench' / (offer_id + '.json'), {})
    first = json.loads(project_files[f'reports/product-preparation/{offer_id}/first-review.json'])
    facts = first.get('product_facts') or {}
    review = state.get('review') or {}
    # Source preview is derived only from this frozen product, retaining its origin.
    state['source'] = state.get('source') or {
        'offer_id':offer_id, 'title_source':takeover['title'],
        'source_mode':'miaoshou', 'source_authority':'FROZEN_ROUND1_EVIDENCE',
        'images':[{'url':row['url'], 'kind':row.get('kind','main')} for row in review.get('image_actions',[]) if row.get('url')],
        'skus':[{'key':row['source_key'], 'name':row['approved_display_name'], 'price':row.get('cost_cny')}
            for row in facts.get('skus',[])],
    }
    state_path = f'data/new_product_workbench/{offer_id}.json'
    project_files[state_path] = _bytes(state)
    immutable = [state_path] + [f'reports/product-preparation/{offer_id}/{n}' for n in names[:3]]
    registration.update(source_files=result['observed_files'],
        projection_hashes={p:_hash(project_files[p]) for p in immutable if p in project_files},
        snapshot_digest=takeover['snapshot_digest'], approved_revision=takeover['approved_revision'],
        targets=takeover['targets'], seller_sku=takeover['seller_sku'])
    # Build away from the live name, then install the complete registration once.
    staging = directory.with_name(directory.name + '.tmp-' + uuid4().hex)
    local_path(staging).mkdir(parents=True)
    for relative, raw in project_files.items():
        path = staging / 'project' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    record = _new_record(registration, result)
    reports = staging / 'project/reports/product-preparation' / offer_id
    (reports / ADOPTION).write_bytes(_bytes(record))
    (reports / REGISTRATION).write_bytes(_bytes(registration))
    (staging / 'registration.json').write_bytes(_bytes(registration))
    # Verify again after copying; failures cannot install a partially bound candidate.
    _candidate(registration)
    staging.rename(directory)
    return review_view(offer_id, runtime_root=runtime_root)


def _rows(result):
    return [dict(row, image_id=_hash(_bytes([row['review_number'],row['brand_id'],row['locale']]))[:24])
        for row in result['routes']]


def _new_record(registration, result):
    return {'schema_version':'r2-candidate-adoption/v1','offer_id':registration['offer_id'],
        'binding_sha256':registration['binding_sha256'], 'snapshot_digest':registration['snapshot_digest'],
        'approved_revision':registration['approved_revision'], 'revision':0,
        'publication_authorized':False, 'last_request':None,
        'decisions':[dict(image_id=r['image_id'],output_sha256=r['output_sha256'],targets=r['targets'],action='review') for r in _rows(result)]}


def _load(offer_id, runtime_root):
    project = registered_project(offer_id, runtime_root=runtime_root)
    registration = read_document(project.parent / 'registration.json', {})
    _require(registration.get('offer_id') == offer_id and registration.get('runtime_root') == str(local_path(runtime_root)), 'REGISTRATION_CONFLICT')
    for relative,digest in registration['projection_hashes'].items():
        path = local_path(project / relative)
        _require(path.is_relative_to(project) and _hash(path.read_bytes()) == digest, 'PROJECTION_DRIFT')
    reports = project / 'reports/product-preparation' / offer_id
    _require(read_document(reports / REGISTRATION,{}) == registration, 'REGISTRATION_CONFLICT')
    takeover, result = _candidate(registration)
    _require(result['observed_files']==registration['source_files'], 'SOURCE_DRIFT')
    record = read_document(reports / ADOPTION,{})
    expected = _new_record(registration, result)
    _require(all(record.get(k) == expected[k] for k in ('schema_version','offer_id','binding_sha256','snapshot_digest','approved_revision','publication_authorized')),
        'ADOPTION_IDENTITY_CONFLICT')
    _require(type(record.get('revision')) is int and record['revision'] >= 0, 'REVISION_INVALID')
    _decisions(record.get('decisions'),expected['decisions'])
    return project, registration, takeover, result, record


def _decisions(rows, expected):
    _require(isinstance(rows,list) and len(rows)==len(expected), 'COVERAGE_INVALID')
    _require(all(isinstance(r,dict) for r in rows), 'DECISION_INVALID')
    for row, bound in zip(rows,expected):
        _require(set(row)=={'image_id','output_sha256','targets','action'} and
            all(row.get(k)==bound[k] for k in ('image_id','output_sha256','targets')) and
            row.get('action') in {'review','keep','remove'}, 'DECISION_CONFLICT')


def _view(registration, takeover, result, record):
    images=[]
    for row,decision in zip(_rows(result),record['decisions']):
        safe={k:v for k,v in row.items() if k not in {'output_path','master_sha256','protected_pixels_verified','status'}}
        safe.update(action=decision['action'], local_url=f"/api/product-workspace/r2-candidate/image?offer_id={registration['offer_id']}&image_id={row['image_id']}&binding={registration['binding_sha256']}")
        images.append(safe)
    consumer={'status':'BLOCKED','code':'R2_CANDIDATE_REVIEW_REQUIRED','execution_authority':False}
    if all(row['action']=='keep' for row in record['decisions']):
        from shared_platform.publication_r3_image_bridge import load_r2_documents, validate_r2_identity
        try:
            project=registered_project(registration['offer_id'],runtime_root=registration['runtime_root'])
            docs=load_r2_documents(registration['offer_id'],reports_root=project/'reports/product-preparation')
            consumer={'status':'PASSED','identity':validate_r2_identity(docs),'execution_authority':False}
        except ValueError as error:
            code=str(error)
            consumer['code']=code if re.fullmatch(r'R2_[A-Z_]+',code) else 'R2_DOCUMENT_CONTRACT_INVALID'
    return {'offer_id':registration['offer_id'],'seller_sku':registration['seller_sku'],
        'binding_sha256':registration['binding_sha256'],'revision':record['revision'],
        'approved_revision':registration['approved_revision'],'targets':registration['targets'],
        'images':images,'keep_count':sum(r['action']=='keep' for r in images),
        'publication_authorized':False,'r2_consumer':consumer,'r2_source_consumer':takeover['r2_consumer']}


def review_view(offer_id, *, runtime_root):
    _,registration,takeover,result,record = _load(_offer(offer_id),runtime_root)
    return _view(registration,takeover,result,record)


def _master_report(registration):
    path=local_path(registration['source_root'])/'reports/product-preparation'/registration['offer_id']/'brand-image-generation.json'
    observed={}
    report=read_document(path,observed)
    _require(observed[str(path)]==registration['source_files'].get(str(path)), 'SOURCE_DRIFT')
    return report


def master_image_bytes(offer_id, image_id, binding, *, runtime_root):
    """Display one recorded master; completion never creates a keep decision."""
    _, registration, *_ = _load(_offer(offer_id), runtime_root)
    _require(binding == registration['binding_sha256'], 'BINDING_CONFLICT')
    report=_master_report(registration)
    rows=[r for r in report.get('assets',[]) if str(r.get('review_number'))==image_id]
    _require(len(rows)==1, 'MASTER_IDENTITY_CONFLICT')
    row=rows[0]
    path=local_path(row['artifact_path'])
    _require(path.is_relative_to(local_path(registration['source_root'])/'reports/product-preparation'/offer_id), 'MASTER_PATH_CONFLICT')
    _require(path.stat().st_size<=32*1024*1024, 'MASTER_IMAGE_LIMIT')
    raw=path.read_bytes()
    _require('sha256:'+_hash(raw)==row['artifact_digest'] and raw.startswith(b'\x89PNG\r\n\x1a\n'), 'MASTER_IMAGE_CONFLICT')
    return raw


@contextmanager
def _decision_lock(path):
    # Keep one stable file: unlinking it lets a new writer lock a different inode.
    # The OS owns the lock while this descriptor is open and releases it on exit.
    fd=os.open(local_path(path),os.O_CREAT|os.O_RDWR,0o600)
    locked=False
    try:
        if os.name=='nt':
            import msvcrt
            os.lseek(fd,0,os.SEEK_SET)
            acquire=lambda:msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            release=lambda:msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            acquire=lambda:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            release=lambda:fcntl.flock(fd,fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as error:
            if error.errno in {errno.EACCES,errno.EAGAIN,errno.EDEADLK}:
                raise ValueError('R2_REVIEW_SAVE_IN_PROGRESS') from None
            raise
        locked=True
        # Windows permits locking beyond EOF. Initialise only while holding it;
        # never truncate or replace an existing lock file, including legacy ones.
        if os.fstat(fd).st_size==0:
            os.write(fd,b'\0')
            os.fsync(fd)
        yield
    finally:
        try:
            if locked:
                os.lseek(fd,0,os.SEEK_SET)
                release()
        finally:
            os.close(fd)


def decide(body, *, runtime_root):
    _require(isinstance(body,dict) and set(body)=={'offer_id','binding_sha256','expected_revision','decisions'}, 'REQUEST_INVALID')
    offer_id=_offer(body.get('offer_id'))
    project,registration,takeover,result,record = _load(offer_id,runtime_root)
    _require(body.get('binding_sha256')==registration['binding_sha256'], 'BINDING_CONFLICT')
    _require(type(body.get('expected_revision')) is int and body['expected_revision']>=0, 'REVISION_INVALID')
    _decisions(body.get('decisions'),record['decisions'])
    _require(body['expected_revision']==record['revision'] or record['last_request']==body, 'REVISION_CONFLICT')
    # Cross-process single writer; stale different requests cannot replace a decision.
    with _decision_lock(project.parent / 'decision.lock'):
        project,registration,takeover,result,record = _load(offer_id,runtime_root)
        if record['last_request']==body:
            return _view(registration,takeover,result,record)
        _require(body['expected_revision']==record['revision'], 'REVISION_CONFLICT')
        record.update(revision=record['revision']+1,decisions=body['decisions'],last_request=body)
        path=local_path(project / 'reports/product-preparation' / offer_id / ADOPTION)
        temporary=path.with_suffix('.tmp-'+uuid4().hex)
        with temporary.open('xb') as stream:
            stream.write(_bytes(record));stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        return review_view(offer_id,runtime_root=runtime_root)


def image_bytes(offer_id, image_id, binding, *, runtime_root):
    _,registration,_,result,_ = _load(_offer(offer_id),runtime_root)
    _require(binding==registration['binding_sha256'], 'BINDING_CONFLICT')
    matching=[r for r in _rows(result) if r['image_id']==image_id]
    _require(len(matching)==1, 'IMAGE_UNKNOWN')
    raw=local_path(matching[0]['output_path']).read_bytes()
    _require(_hash(raw)==matching[0]['output_sha256'], 'IMAGE_DRIFT')
    return raw


def require_adopted_assets(offer_id, reports_root, documents):
    """R3's actual loader invokes this before the existing QA identity checks."""
    marker=local_path(Path(reports_root) / _offer(offer_id) / REGISTRATION)
    if not marker.exists():
        _require(not marker.with_name(ADOPTION).exists() and not (Path(reports_root).parent.parent.parent/'registration.json').exists(), 'REGISTRATION_MISSING')
        return
    registration=read_document(marker,{})
    project,_,_,result,record=_load(offer_id,registration['runtime_root'])
    _require(local_path(reports_root)==project/'reports/product-preparation', 'CONSUMER_ROOT_CONFLICT')
    if not all(row['action']=='keep' for row in record['decisions']):
        raise ValueError('R2_CANDIDATE_REVIEW_REQUIRED')
    if documents is None:
        return
    assets=(documents.get('translation_result') or {}).get('assets') or []
    for row in result['routes']:
        matches=[a for a in assets if (a.get('source_review_number'),a.get('brand_id'),a.get('locale')) ==
            (row['review_number'],row['brand_id'],row['locale'])]
        if len(matches)!=1 or ((matches[0].get('local_provenance') or {}).get('reviewed_artifact_digest')
                or matches[0].get('artifact_digest'))!='sha256:'+row['output_sha256']:
            raise ValueError('R2_ADOPTED_ASSET_RECEIPT_REQUIRED')
    from shared_platform.publication_r2_adoption import validate_adoption_receipt
    validate_adoption_receipt(project/'reports/product-preparation'/offer_id,documents,registration,record)
