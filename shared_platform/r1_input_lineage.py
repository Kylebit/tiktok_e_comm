"""Bounded technical provenance for the existing R1 captured-v2 path only.

This records local input identity, not provider truth or business authority.
No settings/database payloads or capture bodies are included in the manifest.
"""
from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SCHEMA = 'orbit-r1-input-lineage/v1'
MANIFEST_NAME = 'r1-input-lineage.json'
MAX_FILES = 64
MAX_INPUT_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_PROFILE_BYTES = 256 * 1024
CAPTURE_ROOTS = ('state_dir', 'data_root', 'source_outputs_root', 'content_outputs_root', 'output_root')
ROOT_FIELDS = ('source_root', 'config_root', 'settings_path', *CAPTURE_ROOTS,
               'catalog_database', 'release_store_path', 'report_store_path')
_active = ContextVar('r1_lineage_capture', default=None)


class R1LineageError(ValueError):
    """Only fixed technical diagnostics may cross the CLI boundary."""


def _fail(code):
    raise R1LineageError('R1_LINEAGE_' + code) from None


def _digest(value):
    from shared_platform.publication_rounds import canonical_digest
    return canonical_digest(value)


def _same_path(left, right):
    return (isinstance(left, str) and isinstance(right, str)
            and os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right)))


def _path(value, *, missing=False):
    if not isinstance(value, (str, Path)):
        _fail('PATH_INVALID')
    path = Path(value)
    if (not path.is_absolute() or '..' in path.parts or path.as_posix().startswith('//')
            or len(path.parents) > 32 or (os.name == 'nt' and
            (len(path.drive) != 2 or not path.drive[0].isalpha() or path.drive[1] != ':'))):
        _fail('PATH_UNSAFE')
    try:
        for item in (*reversed(path.parents), path):
            try:
                info = item.lstat()
            except FileNotFoundError:
                if missing:
                    continue
                _fail('INPUT_MISSING')
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                _fail('LINK_REJECTED')
            if item == path and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                _fail('LINK_REJECTED')
            if item != path and not stat.S_ISDIR(info.st_mode):
                _fail('PATH_UNSAFE')
        return path
    except OSError:
        _fail('INPUT_UNREADABLE')


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def _read(path, limit):
    path = _path(path)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            _fail('PATH_UNSAFE')
        if before.st_size > limit:
            _fail('SIZE_LIMIT')
        with path.open('rb') as stream:
            if _identity(os.fstat(stream.fileno())) != _identity(before):
                _fail('INPUT_CHANGED')
            raw = stream.read(limit + 1)
            if _identity(os.fstat(stream.fileno())) != _identity(before):
                _fail('INPUT_CHANGED')
        if len(raw) > limit:
            _fail('SIZE_LIMIT')
        if _identity(_path(path).lstat()) != _identity(before):
            _fail('INPUT_CHANGED')
        return raw, _identity(before)
    except OSError:
        _fail('INPUT_UNREADABLE')


def _json(raw):
    try:
        value = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(value, dict):
            _fail('DOCUMENT_INVALID')
        return value
    except (ValueError, UnicodeError):
        _fail('DOCUMENT_INVALID')


def _producer_binding(producer, roots):
    required = {'source_root', 'source_head', 'profile_path', 'profile_sha256'}
    if not isinstance(producer, dict) or set(producer) != required:
        _fail('PRODUCER_INVALID')
    raw, _ = _read(producer['profile_path'], MAX_PROFILE_BYTES)
    if hashlib.sha256(raw).hexdigest() != producer['profile_sha256']:
        _fail('PROFILE_CHANGED')
    profile = _json(raw)
    if profile.get('schema') != 'orbit-agent-entry/v2':
        _fail('PROFILE_MISMATCH')
    if profile.get('expected_source_head') != producer['source_head']:
        _fail('SOURCE_CHANGED')
    for name in ROOT_FIELDS:
        # No settings/DB reads, unused workbench/Lingshi fields, or new root scan.
        if not _same_path(profile.get(name), roots.get(name)):
            _fail('ROOT_MISMATCH')
    if producer['source_root'] != roots['source_root']:
        _fail('ROOT_MISMATCH')
    from scripts.repo_bound_agent_entry import git
    source = _path(producer['source_root'])
    try:
        if not _same_path(git(source, 'rev-parse', '--show-toplevel'), str(source)):
            _fail('SOURCE_CHANGED')
        if git(source, 'rev-parse', 'HEAD') != producer['source_head'] or git(source, 'status', '--porcelain=v1', '-uall'):
            _fail('SOURCE_CHANGED')
    except R1LineageError:
        raise
    except (ValueError, OSError):
        _fail('SOURCE_UNAVAILABLE')


def _role(path, roots):
    selected = [(name, Path(roots[name])) for name in CAPTURE_ROOTS
                if path.is_relative_to(Path(roots[name]))]
    if not selected:
        _fail('INPUT_OUTSIDE_ROOT')
    return max(selected, key=lambda pair: len(pair[1].parts))[0]


def _capture_origin(path, roots):
    """Only the exact upstream detail cache; missing receipts stay legacy."""
    match = re.fullmatch(r'([0-9]{1,32})_miaoshou\.json',path.name)
    if not match or path.parent != Path(roots['state_dir']):
        return None
    sidecar = _path(path.with_name(match[1]+'_capture-origin.json'), missing=True)
    if not sidecar.exists():
        return None
    from shared_platform.source_capture_origin import inspect_detail_capture, CaptureOriginError
    try:
        return inspect_detail_capture(path,roots=roots)['reference']
    except CaptureOriginError as error:
        _fail('CAPTURE_ORIGIN_'+str(error).removeprefix('SOURCE_CAPTURE_ORIGIN_'))


def _audit(event, args):
    capture = _active.get()
    if capture is not None and not capture.busy and event == 'open' and args:
        path, mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)) and (
                (isinstance(mode, str) and 'r' in mode) or
                (mode is None and isinstance(flags, int) and not flags & (os.O_WRONLY | os.O_RDWR))):
            capture.opened(Path(os.fsdecode(path)))


sys.addaudithook(_audit)


class CapturedInputs:
    """Observe only actual selected JSON opens while the v2 producer is active."""

    def __init__(self, bound, offer_id, *, sidecars=()):
        if re.fullmatch(r'[0-9]+', offer_id) is None:
            _fail('OFFER_INVALID')
        self.offer_id = offer_id
        self.roots = {name: str(bound[name]) for name in ROOT_FIELDS}
        self.producer = {name: bound[name] for name in
                         ('source_root', 'source_head', 'profile_path', 'profile_sha256')}
        self.sidecars = {str(_path(path)): str(_path(path).parent) for path in sidecars if path is not None}
        self.inputs = {}
        self.busy = False

    def __enter__(self):
        self.token = _active.set(self)
        return self

    def __exit__(self, *_):
        _active.reset(self.token)

    def opened(self, path):
        if path.suffix.lower() != '.json':
            return
        if not path.is_absolute():
            path = Path(os.path.abspath(path))
        if str(path) in {self.producer['profile_path'], *(self.roots[name] for name in
                ('settings_path','catalog_database','release_store_path','report_store_path'))}:
            return
        # Settings/source configuration is never selected as a captured input.
        selected = str(path) in self.sidecars or any(path.is_relative_to(Path(self.roots[name])) for name in CAPTURE_ROOTS)
        if not selected:
            source = Path(self.roots['source_root'])
            # Source-owned rules are bound by the clean Git identity. Runtime
            # data/capture/report fallback under that source is never accepted
            # in place of the explicit captured roots.
            if path.is_relative_to(source) and path.relative_to(source).parts[0] not in ('data','outputs','reports'):
                return
            _fail('INPUT_OUTSIDE_ROOT')
        key = str(path)
        self.busy = True
        try:
            # A missing optional input was not read. Preserve the caller's
            # existing fallback; missing previously recorded inputs fail in
            # the consumer instead of being manufactured into this manifest.
            try:
                path.lstat()
            except FileNotFoundError:
                return
            if key not in self.inputs and len(self.inputs) >= MAX_FILES:
                _fail('FILE_COUNT_LIMIT')
            role = 'explicit_sidecar' if key in self.sidecars else _role(path, self.roots)
            raw, identity = _read(path, MAX_INPUT_BYTES)
            row = {'path':key, 'role':role, 'bytes':len(raw),
                   'sha256':'sha256:'+hashlib.sha256(raw).hexdigest(), 'identity':identity}
            origin = _capture_origin(path,self.roots)
            if origin is not None:
                row['capture_origin'] = origin
            if role == 'explicit_sidecar':
                row['selected_parent'] = self.sidecars[key]
            if key in self.inputs and self.inputs[key] != row:
                _fail('INPUT_CHANGED')
            self.inputs[key] = row
        finally:
            self.busy = False

    def manifest(self, packet, packet_path):
        _producer_binding(self.producer, self.roots)
        state = str(Path(self.roots['state_dir'])/(self.offer_id+'.json'))
        if state not in self.inputs:
            _fail('STATE_NOT_CONSUMED')
        for row in self.inputs.values():
            raw, identity = _read(row['path'], MAX_INPUT_BYTES)
            if identity != row['identity'] or 'sha256:'+hashlib.sha256(raw).hexdigest() != row['sha256']:
                _fail('INPUT_CHANGED')
            if 'capture_origin' in row and _capture_origin(Path(row['path']),self.roots) != row['capture_origin']:
                _fail('CAPTURE_ORIGIN_CHANGED')
        path = _path(packet_path, missing=True)
        if not path.is_relative_to(Path(self.roots['output_root'])):
            _fail('OUTPUT_OUTSIDE_ROOT')
        body = {'schema':SCHEMA, 'mode':'r1-existing-captured-v2', 'offer_id':self.offer_id,
                'producer':self.producer, 'roots':self.roots,
                'field_propagation':{name:('captured JSON reads' if name in CAPTURE_ROOTS else
                    'source/profile path identity; database/settings payload is not fingerprinted') for name in ROOT_FIELDS},
                'inputs':sorted(self.inputs.values(), key=lambda row: row['path']),
                'packet_path':str(path), 'packet_digest':_digest(packet),
                'scope':'Local selected JSON identity only; no provider freshness, database-content or business authority proof'}
        body['manifest_digest'] = _digest(body)
        if len(json.dumps(body, ensure_ascii=False).encode('utf-8')) > MAX_MANIFEST_BYTES:
            _fail('SIZE_LIMIT')
        return body


def inspect_review_lineage(review, report_directory):
    """Verify a new reference, or explicitly report the unchanged legacy path."""
    ref = review.get('input_lineage_manifest')
    if ref is None:
        return {'status':'UNVERIFIED_LEGACY', 'reason':'R1_LINEAGE_MANIFEST_ABSENT'}
    if not isinstance(ref, dict) or set(ref) != {'file', 'digest'} or ref['file'] != MANIFEST_NAME:
        _fail('REFERENCE_INVALID')
    directory = _path(report_directory)
    raw, _ = _read(directory/MANIFEST_NAME, MAX_MANIFEST_BYTES)
    body = _json(raw)
    unsigned = dict(body)
    supplied = unsigned.pop('manifest_digest', None)
    if supplied != _digest(unsigned) or supplied != ref['digest']:
        _fail('MANIFEST_CHANGED')
    if set(body) != {'schema','mode','offer_id','producer','roots','field_propagation','inputs',
                     'packet_path','packet_digest','scope','manifest_digest'} or body['schema'] != SCHEMA or body['mode'] != 'r1-existing-captured-v2':
        _fail('MANIFEST_INVALID')
    packet = dict(review)
    packet.pop('input_lineage_manifest')
    if body['offer_id'] != str(review.get('offer_id')) or body['packet_digest'] != _digest(packet):
        _fail('PACKET_CHANGED')
    if not isinstance(body['roots'], dict) or set(body['roots']) != set(ROOT_FIELDS):
        _fail('ROOT_MISMATCH')
    roots = body['roots']
    packet_path = _path(body['packet_path'])
    if packet_path.parent != directory or not packet_path.is_relative_to(Path(roots['output_root'])):
        _fail('OUTPUT_OUTSIDE_ROOT')
    _producer_binding(body['producer'], roots)
    rows = body['inputs']
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        _fail('FILE_COUNT_LIMIT')
    seen = set()
    origins = 0
    legacy_caches = 0
    for row in rows:
        allowed_row = {'path','role','bytes','sha256','identity'}
        if not isinstance(row, dict) or set(row) not in (allowed_row,
                allowed_row|{'selected_parent'},allowed_row|{'capture_origin'}):
            _fail('INPUT_INVALID')
        path = _path(row['path'])
        if str(path) in seen:
            _fail('INPUT_INVALID')
        seen.add(str(path))
        if row['role'] == 'explicit_sidecar':
            if row.get('selected_parent') != str(path.parent):
                _fail('INPUT_OUTSIDE_ROOT')
        elif row['role'] != _role(path, roots):
            _fail('ROOT_MISMATCH')
        raw, identity = _read(path, MAX_INPUT_BYTES)
        if (row['bytes'] != len(raw) or row['sha256'] != 'sha256:'+hashlib.sha256(raw).hexdigest()
                or row['identity'] != identity):
            _fail('INPUT_CHANGED')
        if 'capture_origin' in row:
            if _capture_origin(path,roots) != row['capture_origin']:
                _fail('CAPTURE_ORIGIN_CHANGED')
            origins += 1
        elif re.fullmatch(r'[0-9]{1,32}_miaoshou\.json',path.name) and path.parent == Path(roots['state_dir']):
            legacy_caches += 1
    if str(Path(roots['state_dir'])/(body['offer_id']+'.json')) not in seen:
        _fail('STATE_NOT_CONSUMED')
    return {'status':'VERIFIED_CAPTURED_V2', 'manifest_digest':supplied,
            'source_head':body['producer']['source_head'], 'profile_sha256':body['producer']['profile_sha256'],
            'capture_origin_status':('VERIFIED_LOCAL_PRODUCER_OUTPUT' if origins and not legacy_caches else
                'PARTIALLY_VERIFIED_LOCAL_PRODUCER_OUTPUT' if origins else 'UNVERIFIED_LEGACY'),
            'verified_capture_origin_count':origins,
            'scope':body['scope']}


def inspect_snapshot_lineage(snapshot, directory):
    """Bind a downstream snapshot back to its exact reviewed packet."""
    raw, _ = _read(Path(directory)/'first-review.json', MAX_INPUT_BYTES)
    review = _json(raw)
    if (review.get('input_lineage_manifest') != snapshot.get('input_lineage_manifest')
            or _digest(review) != snapshot.get('first_review_digest')):
        _fail('PACKET_CHANGED')
    proof = inspect_review_lineage(review, directory)
    # Pure identity projection from the exact reviewed packet. This matches
    # build_round1_snapshot and does not re-run category, price, stock, policy,
    # provider or approval decisions. The no-manifest legacy branch never
    # reaches this function.
    from shared_platform.publication_rounds import canonical_targets_to_workbench_sites
    targets = list((review.get('target_selection') or {}).get('requested') or [])
    image_plan = deepcopy(dict(review.get('image_execution_plan') or {}))
    image_plan['status'] = 'APPROVED'
    facts = {
        'product_facts':deepcopy(review.get('product_facts') or {}),
        'shared_review_facts':deepcopy(review.get('shared_review_facts') or {}),
        'publication_stock_policy':deepcopy(review.get('publication_stock_policy') or {}),
        'targets':deepcopy(review.get('targets') or []),
        'platform_categories':deepcopy(review.get('platform_categories') or []),
        'copy_review_sets':deepcopy(review.get('copy_review_sets') or []),
        'content_groups':deepcopy(review.get('content_groups') or {}),
    }
    if 'category_evidence_binding' in review:
        facts['category_evidence_binding'] = deepcopy(review['category_evidence_binding'])
    projection = {
        'status':'APPROVED',
        'offer_id':str(review.get('offer_id') or '').strip(),
        'reviewed_product_center_revision':int(review.get('product_center_revision') or 0),
        'canonical_targets':targets,
        'workbench_tiktok_sites':canonical_targets_to_workbench_sites(targets),
        'image_plan':image_plan,
        'image_plan_digest':_digest(image_plan),
        'fact_snapshot':facts,
        'publication_stock_policy':deepcopy(review.get('publication_stock_policy') or {}),
        'external_write_count':0,
        'next_round':'ROUND2_IMAGES_ONLY',
    }
    if _digest({name:snapshot.get(name) for name in projection}) != _digest(projection):
        _fail('FROZEN_PROJECTION_CHANGED')
    return proof
