"""One explicitly risk-authorized image successor, retaining the old UNKNOWN.

This is not billing reconciliation. It cannot release occupancy, relabel an old
request or authorize another successor when the first successor is unresolved.
The original paid boundary still owns every reservation and provider call.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from modules.sourcing.image_generation_checkpoint import (
    ImageCheckpoint, atomic_json, business_lock, digest, inspect_image_checkpoint,
)
from shared_platform.publication_paid_requests import PaidRequestBlocked, UNKNOWN, _read, _sha

SCHEMA = 'unknown-image-successor-grant/v1'
GRANT_NAME = 'unknown-image-successor-grant.json'
RECEIPT_NAME = 'completed-unknown-image-successors.json'


def _require(ok, reason):
    if not ok:
        raise PaidRequestBlocked('IMAGE_SUCCESSOR_' + reason)


def _file(path):
    path = Path(path)
    _require(path.is_absolute() and path.is_file(), 'ABSOLUTE_RETAINED_FILE_REQUIRED')
    _require(not any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)), 'REDIRECTED_FILE')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decision(context, path, sha):
    _require(_file(path) == _sha(sha), 'DECISION_BYTES_CHANGED')
    value = _read(Path(path))
    # A persistent conversation receipt is supplied by the task owner. It is
    # independently hashed, never inferred from a provider/no-charge boolean.
    scope = deepcopy(value.get('image_unknown_successor_authorization', value))
    _require(scope.get('action_class') == 'AUTHORIZE_UNKNOWN_IMAGE_SUCCESSOR'
        and scope.get('offer_id') == context.offer_id
        and scope.get('round1_snapshot_digest') == context.binding['round1_snapshot_digest']
        and type(scope.get('maximum_new_requests')) is int
        and 1 <= scope['maximum_new_requests'] <= 5
        and type(scope.get('per_role_maximum_new_requests')) is int
        and scope['per_role_maximum_new_requests'] == 1
        and scope.get('risk_may_duplicate_original_charge') is True
        and scope.get('unknown_outcome_retained') is True
        and type(scope.get('shared_cap')) is int
        and scope['shared_cap'] == context.cap, 'DECISION_SCOPE_MISMATCH')
    keys = scope.get('original_unknown_keys')
    if keys is None and isinstance(scope.get('original_unknown_attempts'), list):
        keys = [r.get('ledger_key') for r in scope['original_unknown_attempts']]
    _require(isinstance(keys, list) and len(keys) == len(set(keys)) == scope['maximum_new_requests'], 'EXACT_ORIGINAL_KEYS_REQUIRED')
    for key in keys: _sha(key)
    scope['original_unknown_keys'] = keys
    for name in ('round1_snapshot_file', 'source_identity_binding'):
        if name in scope:
            reference = scope[name]
            _require(_file(reference['path']) == _sha(reference['sha256']), 'DECISION_SOURCE_BYTES_CHANGED')
    return scope


def _old(context, row, entries):
    path = Path(row['original_checkpoint_path'])
    _require(path.parent == context.directory/'brand-image-checkpoints-lingshi', 'ORIGINAL_CHECKPOINT_ROOT')
    observed = inspect_image_checkpoint(path)
    _require(observed == row['original_checkpoint_observed'], 'ORIGINAL_CHECKPOINT_CHANGED')
    cp = ImageCheckpoint.from_path(path)
    state = cp.read()
    key = context.image_key(cp, state, 'brand_image_generation')
    entry = entries.get(key)
    _require(key == row['original_paid_key'] and entry is not None
        and entry['state'] == 'UNKNOWN' and entry['provider'] == 'lingshi'
        and entry['binding'] == context.binding and entry['attempt'] == state['attempt']
        and entry['model'] == cp.model == row['model']
        and entry['request_digest'] == digest({'checkpoint_request_digest': cp.request_digest})
        and entry['business'] == {'kind': 'brand', 'business_digest': cp.business_digest}
        and state['status'] == 'SUBMISSION_UNKNOWN' and not state.get('task_id')
        and cp.request_digest == row['original_request_digest']
        and cp.business_digest == row['business_digest'], 'ORIGINAL_UNKNOWN_BINDING_CHANGED')
    expected_business = digest({'kind': 'brand', 'offer_id': context.offer_id,
        'brand_id': row['brand_id'], 'role': row['role']})
    _require(cp.business_digest == expected_business, 'EXACT_BRAND_ROLE_REQUIRED')
    return cp


def _successor_digest(row, authorization_digest):
    return digest({'business_digest': row['business_digest'],
        'original_request_digest': row['original_request_digest'],
        'unknown_successor_authorization_digest': authorization_digest})


def prepare_unknown_image_successor_grant(context, *, decision_path, decision_sha256, checkpoint_paths):
    """Local preparation only, after receiving the exact persistent user receipt."""
    context.ensure_ready()
    decision_path = Path(decision_path).absolute()
    scope = _decision(context, decision_path, decision_sha256)
    roles = {(b['id'], r['role']) for b in context.round1['image_plan']['brand_plans']
        for r in b['generated_assets']}
    with context.phase_lock():
        with business_lock(context.root, context.lock_key):
            _events, entries = context._load()
            rows = []
            for path in checkpoint_paths:
                cp = ImageCheckpoint.from_path(Path(path).absolute())
                state = cp.read()
                matches = [(brand, role) for brand, role in roles if
                    digest({'kind': 'brand', 'offer_id': context.offer_id,
                        'brand_id': brand, 'role': role}) == cp.business_digest]
                _require(len(matches) == 1, 'ORIGINAL_ROLE_NOT_IN_R1')
                row = {'brand_id': matches[0][0], 'role': matches[0][1], 'model': cp.model,
                    'original_paid_key': context.image_key(cp, state, 'brand_image_generation'),
                    'original_checkpoint_path': str(cp.path),
                    'original_checkpoint_observed': inspect_image_checkpoint(cp.path),
                    'original_request_digest': cp.request_digest, 'business_digest': cp.business_digest,
                    'maximum_new_requests': 1}
                _old(context, row, entries)
                rows.append(row)
            rows.sort(key=lambda r: r['original_paid_key'])
            _require(sorted(r['original_paid_key'] for r in rows) == sorted(scope['original_unknown_keys']), 'DECISION_KEYS_MISMATCH')
            core = {'schema_version': SCHEMA, 'offer_id': context.offer_id, 'binding': context.binding,
                'decision_path': str(decision_path), 'decision_sha256': _sha(decision_sha256),
                'risk_may_duplicate_original_charge': True, 'unknown_outcomes_retained': True,
                'shared_cap': context.cap, 'rows': rows}
            authorization_digest = digest(core)
            grant = deepcopy(core)
            grant['authorization_digest'] = authorization_digest
            for row in grant['rows']:
                row['successor_request_digest'] = _successor_digest(row, authorization_digest)
            grant['digest'] = digest(grant)
            path = context.root/GRANT_NAME
            if path.exists():
                _require(_read(path) == grant, 'EXISTING_GRANT_PRESERVED')
            else: atomic_json(path, grant)
    return path


def validate_grant(context, path, *, entries=None):
    path = Path(path).absolute()
    _require(path == context.root/GRANT_NAME, 'CANONICAL_GRANT_REQUIRED')
    sha = _file(path); grant = _read(path)
    unsigned = deepcopy(grant); seal = unsigned.pop('digest', None)
    _require(seal == digest(unsigned) and grant.get('schema_version') == SCHEMA
        and grant.get('offer_id') == context.offer_id and grant.get('binding') == context.binding
        and grant.get('risk_may_duplicate_original_charge') is True
        and grant.get('unknown_outcomes_retained') is True and grant.get('shared_cap') == context.cap, 'GRANT_BINDING_CHANGED')
    scope = _decision(context, grant['decision_path'], grant['decision_sha256'])
    core = deepcopy(unsigned); authorization_digest = core.pop('authorization_digest', None)
    for row in core['rows']: row.pop('successor_request_digest', None)
    _require(digest(core) == authorization_digest, 'AUTHORIZATION_DIGEST_CHANGED')
    _require(sorted(r['original_paid_key'] for r in grant['rows']) == sorted(scope['original_unknown_keys'])
        and len({(r['brand_id'], r['role']) for r in grant['rows']}) == len(grant['rows']), 'GRANT_KEYS_CHANGED')
    entries = context._load()[1] if entries is None else entries
    for row in grant['rows']:
        _old(context, row, entries)
        retained = [r for r in scope.get('original_unknown_attempts', [])
            if r.get('ledger_key') == row['original_paid_key']]
        if scope.get('original_unknown_attempts') is not None:
            _require(len(retained) == 1
                and retained[0].get('checkpoint_sha256') == row['original_checkpoint_observed']['checkpoint_digest']
                and Path(retained[0].get('checkpoint_path', '')) == Path(row['original_checkpoint_path'])
                and retained[0].get('request_digest') == row['original_request_digest']
                and retained[0].get('brand_id') == row['brand_id'] and retained[0].get('role') == row['role'],
                'DECISION_ORIGINAL_CHECKPOINT_MISMATCH')
        _require(type(row['maximum_new_requests']) is int and row['maximum_new_requests'] == 1
            and row['successor_request_digest'] == _successor_digest(row, authorization_digest), 'SUCCESSOR_IDENTITY_CHANGED')
    return grant, sha


def successor_checkpoint(context, original, *, brand_id, role):
    grant, sha = validate_grant(context, context.unknown_successor_grant)
    rows = [r for r in grant['rows'] if (r['brand_id'], r['role']) == (brand_id, role)]
    _require(len(rows) == 1, 'ROLE_OUTSIDE_GRANT')
    row = rows[0]
    _require(original.path == Path(row['original_checkpoint_path'])
        and original.request_digest == row['original_request_digest']
        and original.model == row['model'] and original.source_identity_complete, 'PROMPT_MODEL_OR_REFERENCE_CHANGED')
    cp = ImageCheckpoint(original.root, kind='brand',
        business_identity={'offer_id': context.offer_id, 'brand_id': brand_id, 'role': role},
        request_identity={'original_request_digest': original.request_digest,
            'unknown_successor_authorization_digest': grant['authorization_digest']},
        model=original.model, source_identity_complete=True)
    cp.unknown_successor = {'grant_path': str(context.unknown_successor_grant), 'grant_sha256': sha,
        'grant_digest': grant['digest'], 'original_paid_key': row['original_paid_key'],
        'successor_request_digest': cp.request_digest}
    return cp


def validate_successor(context, checkpoint, *, entries=None):
    basis = getattr(checkpoint, 'unknown_successor', None)
    if basis is None: return None
    grant, sha = validate_grant(context, basis['grant_path'], entries=entries)
    _require(sha == basis.get('grant_sha256') and grant['digest'] == basis.get('grant_digest'), 'GRANT_BYTES_CHANGED')
    rows = [r for r in grant['rows'] if r['original_paid_key'] == basis.get('original_paid_key')]
    _require(len(rows) == 1, 'ORIGINAL_KEY_CHANGED')
    row = rows[0]
    _require(checkpoint.kind == 'brand' and checkpoint.root == context.directory/'brand-image-checkpoints-lingshi'
        and checkpoint.request_digest == row['successor_request_digest'] == basis.get('successor_request_digest')
        and checkpoint.business_digest == row['business_digest'] and checkpoint.model == row['model']
        and checkpoint.source_identity_complete, 'CHECKPOINT_BINDING_CHANGED')
    return row


def validate_successor_reservation(context, basis, *, business, request, model, attempt, entries):
    grant, sha = validate_grant(context, basis['grant_path'], entries=entries)
    rows = [r for r in grant['rows'] if r['original_paid_key'] == basis.get('original_paid_key')]
    _require(len(rows) == 1 and sha == basis.get('grant_sha256')
        and grant['digest'] == basis.get('grant_digest'), 'RESERVATION_GRANT_CHANGED')
    row = rows[0]
    _require(attempt == 0 and model == row['model']
        and business == {'kind': 'brand', 'business_digest': row['business_digest']}
        and request == {'checkpoint_request_digest': row['successor_request_digest']}
        and basis.get('successor_request_digest') == row['successor_request_digest'], 'RESERVATION_IDENTITY_CHANGED')
    return row


def completed_successor_receipt(context, *, persist=False):
    """Proof usable only by image adoption, never by merchant action recovery."""
    from modules.sourcing.image_generation_checkpoint import inspect_image_checkpoint
    with business_lock(context.root, context.lock_key):
        events, entries = context._load()
        grant, sha = validate_grant(context, context.root/GRANT_NAME, entries=entries)
        unknowns = {k for k, e in entries.items() if e['state'] in UNKNOWN}
        _require(not any(r['provider'] == 'UNKNOWN' or r['state'] in {'UNKNOWN','ATTEMPTED'}
            for r in context._historical(events)), 'HISTORICAL_UNKNOWN_NOT_COVERED')
        _require(unknowns == {r['original_paid_key'] for r in grant['rows']}, 'UNAUTHORIZED_UNKNOWN_REMAINS')
        proofs = []
        for row in grant['rows']:
            path = context.directory/'brand-image-checkpoints-lingshi'/('lingshi-brand-v2-'+row['successor_request_digest'][:24]+'.json')
            cp = ImageCheckpoint.from_path(path); validate_successor(context, cp, entries=entries)
            state = cp.read(); key = context.image_key(cp, state, 'brand_image_generation')
            entry = entries.get(key); receipt = state.get('receipt') or {}; raw = context._raw(key)
            _require(state['status'] == 'COMPLETED' and entry and entry['state'] == 'CONFIRMED'
                and entry['binding'] == context.binding and entry.get('unknown_successor') == cp.unknown_successor
                and entry.get('receipt_digest') == digest(receipt) and receipt.get('source_identity_complete') is True
                and receipt.get('request_digest') == cp.request_digest
                and receipt.get('business_digest') == cp.business_digest and receipt.get('model') == cp.model
                and receipt.get('task_id') == state.get('task_id') == entry.get('task_id')
                and receipt.get('output_digest') == 'sha256:'+_file(cp.output_path)
                and str(((raw or {}).get('data') or {}).get('task_id')) == str(state['task_id']), 'SUCCESSOR_NOT_CONFIRMED')
            proofs.append({'original_paid_key': row['original_paid_key'], 'successor_paid_key': key,
                'successor_checkpoint_observed': inspect_image_checkpoint(path), 'artifact_sha256': _file(cp.output_path)})
        result = {'schema_version': 'completed-unknown-image-successors/v1', 'offer_id': context.offer_id,
            'binding': context.binding, 'grant_path': str(context.root/GRANT_NAME), 'grant_sha256': sha,
            'covered_original_keys': sorted(unknowns), 'unknown_outcomes_retained': True,
            'original_occupied_retained': True, 'successors': proofs}
        result['digest'] = digest(result)
        if persist: atomic_json(context.root/RECEIPT_NAME, result)
        return result


def verify_successor_adoption(context):
    """Reread the exact completed image proof and return files for origin pins."""
    proof = completed_successor_receipt(context)
    receipt_path = context.root/RECEIPT_NAME
    _require(_read(receipt_path) == proof, 'COMPLETION_RECEIPT_CHANGED')
    grant, _ = validate_grant(context, context.root/GRANT_NAME)
    paths = [receipt_path, context.root/GRANT_NAME]
    for row, successor in zip(grant['rows'], proof['successors']):
        old = ImageCheckpoint.from_path(row['original_checkpoint_path'])
        new = ImageCheckpoint.from_path(successor['successor_checkpoint_observed']['checkpoint_path'])
        paths.extend([old.path, old.identity_path, old.journal_path,
            new.path, new.identity_path, new.journal_path, new.output_path,
            context.root/('raw-'+successor['successor_paid_key'][:24]+'.json')])
    generation = _read(context.directory/'brand-image-generation.json')
    _require(generation.get('unknown_successor_receipt') ==
        {'path':str(receipt_path),'digest':proof['digest']}, 'GENERATION_COMPLETION_PROOF_CHANGED')
    roles = {(b['id'], r['role']) for b in context.round1['image_plan']['brand_plans'] for r in b['generated_assets']}
    assets = generation.get('assets') or []
    _require(len(assets) == len(roles) and {(r['brand_id'],r['role']) for r in assets} == roles,
        'COMPLETE_FROZEN_GALLERY_REQUIRED')
    for asset in assets:
        cp = ImageCheckpoint.from_path(asset['checkpoint_path']); state = cp.read()
        _require(cp.kind == 'brand' and cp.root == context.directory/'brand-image-checkpoints-lingshi'
            and cp.business_digest == digest({'kind':'brand','offer_id':context.offer_id,
                'brand_id':asset['brand_id'],'role':asset['role']})
            and state['status'] == 'COMPLETED' and cp.source_identity_complete
            and state.get('receipt',{}).get('output_digest') == asset['artifact_digest']
            and state.get('receipt',{}).get('public_url') == asset['public_url']
            and asset['artifact_digest'] == 'sha256:'+_file(cp.output_path)
            and Path(asset['artifact_path']) == cp.output_path, 'GALLERY_ASSET_CHANGED')
        key = context.image_key(cp, state, 'brand_image_generation')
        entry = context.entry(key); raw = context._raw(key)
        _require(entry and entry['state'] == 'CONFIRMED' and entry['binding'] == context.binding
            and entry.get('receipt_digest') == digest(state['receipt'])
            and str(((raw or {}).get('data') or {}).get('task_id')) == str(state['task_id']), 'GALLERY_PAID_RECEIPT_CHANGED')
        paths.extend([cp.path,cp.identity_path,cp.journal_path,cp.output_path,
            context.root/('raw-'+key[:24]+'.json')])
    return {'receipt':proof,
        'source_files':{str(p.relative_to(context.directory.parents[2])):_file(p) for p in paths},
        'external_files':{grant['decision_path']:grant['decision_sha256']}}
