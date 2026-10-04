"""One existing detail-cache producer's unsigned local output receipt.

Reuse R1's bounded path/read/digest rules. This is not provider authentication,
freshness, authority, or a replacement for the missing workbench state.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from uuid import uuid4

from shared_platform.r1_input_lineage import (
    _digest, _identity, _json, _path, _read, _same_path,
    MAX_INPUT_BYTES, MAX_MANIFEST_BYTES,
)

SOURCE_CAPTURE_ORIGIN_CONTRACT = 'orbit-source-capture-origin/v1'
ROLE_ROOTS = ('state_dir','data_root','source_outputs_root','content_outputs_root')
ENTRY = 'modules/sourcing/miaoshou_precollect.py'
OPERATION = 'import_common_collect_detail'


class CaptureOriginError(ValueError):
    pass


def _fail(code):
    raise CaptureOriginError('SOURCE_CAPTURE_ORIGIN_'+code) from None


def origin_path(cache):
    cache=Path(cache)
    if re.fullmatch(r'[0-9]{1,32}_miaoshou\.json',cache.name) is None:
        _fail('CACHE_PATH_INVALID')
    return cache.with_name(cache.name[:-len('_miaoshou.json')]+'_capture-origin.json')


def _source(producer):
    try:
        _checked_source(producer)
    except CaptureOriginError:
        raise
    except (OSError,ValueError,subprocess.SubprocessError):
        _fail('SOURCE_UNAVAILABLE')


def _checked_source(producer):
    if (not isinstance(producer,dict) or set(producer)!= {'source_root','source_head','entry','operation'}
            or producer['entry']!=ENTRY or producer['operation']!=OPERATION
            or not isinstance(producer['source_head'],str) or re.fullmatch(r'[0-9a-f]{40}',producer['source_head']) is None):
        _fail('PRODUCER_INVALID')
    from scripts.repo_bound_agent_entry import git, source_constant_contract
    root=_path(producer['source_root'])
    if not root.is_dir():_fail('SOURCE_INVALID')
    if (not _same_path(git(root,'rev-parse','--show-toplevel'),str(root))
            or git(root,'rev-parse','HEAD')!=producer['source_head']
            or git(root,'status','--porcelain=v1','-uall')):
        _fail('SOURCE_CHANGED')
    git(root,'ls-files','--error-unmatch','--',ENTRY,'shared_platform/source_capture_origin.py')
    for relative in (ENTRY,'shared_platform/source_capture_origin.py'):
        source_constant_contract(_path(root/relative),'SOURCE_CAPTURE_ORIGIN_CONTRACT',
            SOURCE_CAPTURE_ORIGIN_CONTRACT,'SOURCE_CAPTURE_ORIGIN_SOURCE_CAPABILITY_MISSING')


def _atomic(path,raw):
    path=_path(path,missing=True)
    if path.exists() and not path.is_file():_fail('OUTPUT_PATH_INVALID')
    temporary=path.with_name('.capture-origin-'+uuid4().hex+'.tmp')
    _path(temporary,missing=True)
    try:
        with temporary.open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        _path(path,missing=True)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


class DetailCaptureWriter:
    """Opt-in source/roots checked before transport, rechecked before writing."""
    def __init__(self,binding,state_dir,producer_file,offer_id):
        if not isinstance(binding,dict) or set(binding)!= {'source_root','expected_source_head',*ROLE_ROOTS}:
            _fail('BINDING_INVALID')
        if re.fullmatch(r'[0-9]{1,32}',offer_id) is None:_fail('OFFER_INVALID')
        source=_path(binding['source_root'])
        if (source!=_path(Path(producer_file).parents[2]) or
                source!=_path(Path(__file__).parents[1])):
            _fail('LOADED_SOURCE_MISMATCH')
        self.producer={'source_root':str(source),'source_head':binding['expected_source_head'],'entry':ENTRY,'operation':OPERATION}
        self.roots={name:str(_path(binding[name])) for name in ROLE_ROOTS}
        if any(not Path(value).is_dir() for value in self.roots.values()):_fail('ROOT_INVALID')
        if state_dir is None or not _same_path(str(_path(state_dir)),self.roots['state_dir']):_fail('ROOT_MISMATCH')
        self.offer_id=offer_id
        self.cache=_path(Path(self.roots['state_dir'])/(offer_id+'_miaoshou.json'),missing=True)
        self.sidecar=_path(origin_path(self.cache),missing=True)
        for path in (self.cache,self.sidecar):
            if path.exists() and not stat.S_ISREG(path.lstat().st_mode):_fail('OUTPUT_PATH_INVALID')
        _source(self.producer)

    def write(self,payload):
        raw=json.dumps(payload,ensure_ascii=False,indent=2).encode('utf-8')
        if len(raw)>MAX_INPUT_BYTES:_fail('SIZE_LIMIT')
        _source(self.producer)
        _atomic(self.cache,raw)
        actual,identity=_read(self.cache,MAX_INPUT_BYTES)
        if actual!=raw:_fail('CACHE_CHANGED')
        body={'schema':SOURCE_CAPTURE_ORIGIN_CONTRACT,'offer_id':self.offer_id,
              'producer':self.producer,'roots':self.roots,
              'files':[{'path':str(self.cache),'role':'state_dir','bytes':len(actual),
                  'sha256':'sha256:'+hashlib.sha256(actual).hexdigest(),'identity':identity}],
              'created_at':datetime.now(timezone.utc).isoformat(),
              'scope':'Unsigned local detail-cache output identity only; other role roots are explicit metadata, not outputs. No provider freshness, authority or complete R1 state proof.'}
        body['manifest_digest']=_digest(body)
        encoded=(json.dumps(body,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        if len(encoded)>MAX_MANIFEST_BYTES:_fail('SIZE_LIMIT')
        _source(self.producer)
        _atomic(self.sidecar,encoded)
        return {'file':str(self.sidecar),'digest':body['manifest_digest']}


def inspect_detail_capture(cache,*,roots=None,reference=None):
    """No discovery/backfill: inspect only this exact known cache's sidecar."""
    cache=_path(cache);sidecar=_path(origin_path(cache),missing=True)
    if reference is not None and (not isinstance(reference,dict) or set(reference)!= {'file','digest'}
            or not _same_path(reference['file'],str(sidecar))):_fail('REFERENCE_INVALID')
    if not sidecar.exists():
        if reference is not None:_fail('MANIFEST_MISSING')
        return {'status':'UNVERIFIED_LEGACY','reason':'SOURCE_CAPTURE_ORIGIN_ABSENT'}
    raw,_=_read(sidecar,MAX_MANIFEST_BYTES);body=_json(raw)
    if set(body)!= {'schema','offer_id','producer','roots','files','created_at','scope','manifest_digest'} or body['schema']!=SOURCE_CAPTURE_ORIGIN_CONTRACT:
        _fail('MANIFEST_INVALID')
    unsigned=dict(body);supplied=unsigned.pop('manifest_digest')
    if supplied!=_digest(unsigned):_fail('MANIFEST_CHANGED')
    if reference is not None and reference['digest']!=supplied:_fail('MANIFEST_CHANGED')
    if not isinstance(body['roots'],dict) or set(body['roots'])!=set(ROLE_ROOTS):_fail('ROOT_MISMATCH')
    for name in ROLE_ROOTS:
        role=_path(body['roots'][name])
        if not role.is_dir() or roots is not None and not _same_path(str(role),roots.get(name)):_fail('ROOT_MISMATCH')
    if cache!=Path(body['roots']['state_dir'])/(str(body['offer_id'])+'_miaoshou.json'):_fail('CACHE_PATH_INVALID')
    rows=body['files']
    if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict) or set(rows[0])!= {'path','role','bytes','sha256','identity'}:
        _fail('FILES_INVALID')
    row=rows[0]
    if not _same_path(row['path'],str(cache)) or row['role']!='state_dir':_fail('CACHE_PATH_INVALID')
    actual,identity=_read(cache,MAX_INPUT_BYTES)
    if row['bytes']!=len(actual) or row['identity']!=identity or row['sha256']!='sha256:'+hashlib.sha256(actual).hexdigest():_fail('CACHE_CHANGED')
    _source(body['producer'])
    return {'status':'VERIFIED_LOCAL_PRODUCER_OUTPUT','reference':{'file':str(sidecar),'digest':supplied},'producer':body['producer'],'scope':body['scope']}
