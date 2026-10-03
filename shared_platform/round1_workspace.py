"""Local R1 HTTP consumer: immutable prepared inputs, existing approval, disk readback.

The browser supplies references, never review facts or approval state. A preparation
is also the durable operation identity; recovery never repeats the approval write.
"""
from copy import deepcopy
from contextlib import contextmanager
import importlib.util
import base64
import hashlib
import os
import stat
import json
import re
import sqlite3
import uuid
from pathlib import Path

from shared_platform import publication_rounds as rounds
from shared_platform.round1_category_evidence import digest
from shared_platform import release_store


def default_release_store():
    return release_store.default_release_store()


class Round1WorkspaceError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@contextmanager
def _transaction():
    store = default_release_store()
    store.path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(store.path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS round1_workspace_preparations '
                           '(request_id TEXT PRIMARY KEY, offer_id TEXT NOT NULL, reference TEXT UNIQUE NOT NULL, '
                           'request_json TEXT NOT NULL, document_json TEXT NOT NULL, document_digest TEXT NOT NULL)')
        connection.execute("CREATE TRIGGER IF NOT EXISTS round1_workspace_no_update BEFORE UPDATE ON round1_workspace_preparations BEGIN SELECT RAISE(ABORT,'immutable R1 preparation'); END")
        connection.execute("CREATE TRIGGER IF NOT EXISTS round1_workspace_no_delete BEFORE DELETE ON round1_workspace_preparations BEGIN SELECT RAISE(ABORT,'immutable R1 preparation'); END")
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _decode(row, *, connection=None):
    if row is None:
        raise Round1WorkspaceError('R1_PREPARATION_NOT_FOUND')
    document = json.loads(row['document_json'])
    identity = {key:value for key,value in document.items() if key != 'reference'}
    if (digest(document) != row['document_digest'] or document['reference'] != row['reference']
            or document['reference'] != 'r1-prepared:' + digest(identity)[7:]):
        raise Round1WorkspaceError('R1_PREPARATION_CORRUPT')
    _check_preparation_root(document, connection=connection)
    return document


def read_preparation(offer_id, reference=None, request_id=None):
    store = default_release_store()
    if not store.path.is_file():
        raise Round1WorkspaceError('R1_PREPARATION_NOT_FOUND')
    with store._connect_readonly() as connection:
        try:
            key, value = ('reference', reference) if reference else ('request_id', request_id)
            row = connection.execute(f'SELECT * FROM round1_workspace_preparations WHERE {key}=? AND offer_id=?',
                                     (value, offer_id)).fetchone()
        except sqlite3.OperationalError as error:
            if 'no such table' in str(error):
                raise Round1WorkspaceError('R1_PREPARATION_NOT_FOUND') from None
            raise
        return _decode(row, connection=connection)


PREPARATION_ROOT_SCHEMA = 'native-preparation-root/v1'


def _unknown_preparation_root(reason):
    return {'schema_version': PREPARATION_ROOT_SCHEMA, 'status': 'UNKNOWN',
            'reason': reason, 'execution_authority': False}


def _check_preparation_root(document, *, connection=None):
    root = document.get('preparation_root')
    if root is None:  # Historical preparations have no service-owned root.
        return _unknown_preparation_root('LEGACY_PREPARATION_ROOT_UNKNOWN')
    if type(root) is not dict or root.get('schema_version') != PREPARATION_ROOT_SCHEMA or root.get('execution_authority') is not False:
        raise Round1WorkspaceError('R1_PREPARATION_ROOT_CORRUPT')
    if root.get('status') == 'UNKNOWN':
        if set(root) != {'schema_version', 'status', 'reason', 'execution_authority'} or type(root['reason']) is not str:
            raise Round1WorkspaceError('R1_PREPARATION_ROOT_CORRUPT')
        return deepcopy(root)
    if (set(root) != {'schema_version', 'status', 'root_id', 'offer_id', 'origin_request_id', 'execution_authority'}
            or root['status'] != 'PERSISTED_NATIVE_PREPARATION_ROOT'
            or type(root['root_id']) is not str or not re.fullmatch(r'r1-cycle:[0-9a-f]{32}', root['root_id'])
            or root['offer_id'] != document['scope']['offer_id']
            or type(root['origin_request_id']) is not str):
        raise Round1WorkspaceError('R1_PREPARATION_ROOT_CORRUPT')
    if connection is None:
        store = default_release_store()
        if not store.path.is_file():
            raise Round1WorkspaceError('R1_PREPARATION_ROOT_UNAVAILABLE')
        with store._connect_readonly() as db:
            return _check_preparation_root(document, connection=db)
    row = connection.execute('SELECT * FROM round1_workspace_preparations WHERE request_id=? AND offer_id=?',
                             (root['origin_request_id'], root['offer_id'])).fetchone()
    if row is None:
        raise Round1WorkspaceError('R1_PREPARATION_ROOT_UNAVAILABLE')
    anchor = json.loads(row['document_json'])
    identity = {key:value for key,value in anchor.items() if key != 'reference'}
    if (digest(anchor) != row['document_digest'] or anchor['reference'] != row['reference']
            or anchor['reference'] != 'r1-prepared:' + digest(identity)[7:]
            or anchor.get('preparation_root') != root or anchor['request_id'] != root['origin_request_id']
            or anchor['scope']['offer_id'] != root['offer_id']):
        raise Round1WorkspaceError('R1_PREPARATION_ROOT_CORRUPT')
    return deepcopy(root)


def _technical_root_continuation(server, document, state):
    if server is None:
        return False
    from shared_platform.publication_runtime_config import _read
    directory = rounds.report_dir(document['scope']['offer_id'])
    relative = (directory / 'round1-auto-decision.json').relative_to(rounds.REPORTS_ROOT)
    decision = json.loads(_read(rounds.REPORTS_ROOT, relative.as_posix()).decode('utf-8'))
    review = rounds.validate_round1_reviewable(document['packet'], report_directory=directory)
    rounds.validate_round1_auto_decision(decision, review)
    phase, observed = _auto_recheck(server, document, decision)
    return phase == 'APPROVED' and observed == state


def _preparation_root(connection, *, scope, state, unbound_packet, request_id, server=None):
    """Persist only an initial root or a proven continuation, never quota."""
    rows = connection.execute('SELECT * FROM round1_workspace_preparations WHERE offer_id=?',
                              (scope['offer_id'],)).fetchall()
    current = (state.get('product_approval') or {}).get('round1_prepared_reference')
    if current:
        row = next((row for row in rows if row['reference'] == current), None)
        if row is None:
            return _unknown_preparation_root('CURRENT_PREPARATION_ROOT_UNKNOWN')
        prior = _decode(row, connection=connection)
        try:
            _same_state(prior, state)
        except Round1WorkspaceError:
            try:
                technical = _technical_root_continuation(server, prior, state)
            except (ValueError, TypeError, KeyError, OSError):
                technical = False
            if not technical:
                return _unknown_preparation_root('CURRENT_PREPARATION_CONTINUATION_UNKNOWN')
        if (prior['scope']['account_identity_digest'] != scope['account_identity_digest']
                or prior['scope']['source_region'] != scope['source_region']
                or sorted(prior['scope']['requested_targets']) != sorted(scope['requested_targets'])
                or _semantic_packet(prior['unbound_packet']) != _semantic_packet(unbound_packet)):
            return _unknown_preparation_root('CURRENT_PREPARATION_CONTINUATION_UNKNOWN')
        return _check_preparation_root(prior, connection=connection)
    # A changed request id or byte-only candidate formatting is a continuation
    # when the same actual unapproved state and semantic preparation remain.
    matching = []
    for row in rows:
        prior = _decode(row, connection=connection)
        if (prior['state_before'] == state
                and prior['scope']['account_identity_digest'] == scope['account_identity_digest']
                and prior['scope']['source_region'] == scope['source_region']
                and sorted(prior['scope']['requested_targets']) == sorted(scope['requested_targets'])
                and _semantic_packet(prior['unbound_packet']) == _semantic_packet(unbound_packet)):
            matching.append(_check_preparation_root(prior, connection=connection))
    if matching:
        if any(root != matching[0] for root in matching):
            return _unknown_preparation_root('PREPARATION_ROOT_CONTINUATION_AMBIGUOUS')
        return matching[0]
    if rows:
        # No automatic Offer-lifetime root and no invented independent cycle.
        # Technical preparation remains possible while budget provenance is unknown.
        return _unknown_preparation_root('PREPARATION_CYCLE_ORIGIN_UNKNOWN')
    return {'schema_version': PREPARATION_ROOT_SCHEMA,
            'status': 'PERSISTED_NATIVE_PREPARATION_ROOT',
            'root_id': 'r1-cycle:' + uuid.uuid4().hex, 'offer_id': scope['offer_id'],
            'origin_request_id': request_id, 'execution_authority': False}


def preparation_root_facts(document):
    """Read the same persisted root; no account, policy, count or quota claim."""
    persisted = read_preparation(document['scope']['offer_id'], document['reference'])
    if _json(persisted) != _json(document):
        raise Round1WorkspaceError('R1_PREPARATION_ROOT_DOCUMENT_MISMATCH')
    root = _check_preparation_root(persisted)
    return {**root, 'confirmed_write_count': 'UNKNOWN',
            'maximum_confirmed_writes': 'UNKNOWN', 'coverage_authority': 'UNKNOWN'}


def _module(server):
    path = Path(__file__).resolve().parents[1] / 'skills/prepare-product-publication/scripts/prepare_product_publication.py'
    spec = importlib.util.spec_from_file_location('_round1_workspace_prepare', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _image_input(offer):
    path = rounds.report_dir(offer) / 'first-review.json'
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
        if document['offer_id'] != offer:
            raise ValueError()
        return document['image_execution_plan'], digest(document), document['product_center_revision']
    except (OSError, ValueError, KeyError, TypeError):
        raise Round1WorkspaceError('R1_IMAGE_PLAN_UNAVAILABLE') from None


def _candidate_file_identity(info):
    # CPython Windows path stat reports creation time as ctime, while fstat
    # reports ChangeTime. Compare the same creation-time semantics on both.
    timestamp = getattr(info, 'st_birthtime_ns', None) if os.name == 'nt' else info.st_ctime_ns
    if type(timestamp) is not int:
        raise Round1WorkspaceError('R1_CANDIDATE_INPUT_INVALID')
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, timestamp)


def _candidate_input(offer):
    """Read the fixed service-owned sidecar; metadata is a read fence only."""
    from shared_platform.immutable_approval_files import require_local_path, ImmutableFileError
    path = rounds.report_dir(offer) / 'first-review-candidate-plan.json'
    limit = 1024 * 1024
    try:
        def parent_identity():
            info = require_local_path(path.parent, root=rounds.REPORTS_ROOT, allow_directory=True)
            if info is None or not stat.S_ISDIR(info.st_mode):
                raise Round1WorkspaceError('R1_CANDIDATE_SOURCE_PARENT_UNAVAILABLE')
            return info.st_dev, info.st_ino, info.st_mode

        def file_identity(info):
            return _candidate_file_identity(info)

        before_parent = parent_identity()
        before = require_local_path(path, root=rounds.REPORTS_ROOT)
        if before is None:
            # The helper can return None for ANY missing ancestor. Only a
            # missing final entry under the same existing parent is absence.
            if parent_identity() != before_parent:
                raise Round1WorkspaceError('R1_CANDIDATE_INPUT_CHANGED')
            try:
                path.lstat()
            except FileNotFoundError:
                if parent_identity() != before_parent:
                    raise Round1WorkspaceError('R1_CANDIDATE_INPUT_CHANGED')
                return {'schema_version': 'round1-candidate-input/v1',
                        'relative_name': path.name, 'present': False,
                        'byte_length': 0, 'raw_sha256': None, 'raw_base64': None}, None
            raise Round1WorkspaceError('R1_CANDIDATE_INPUT_CHANGED')
        if before.st_size > limit:
            raise Round1WorkspaceError('R1_CANDIDATE_INPUT_INVALID')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if file_identity(opened) != file_identity(before):
                raise Round1WorkspaceError('R1_CANDIDATE_INPUT_CHANGED')
            raw = stream.read(limit + 1)
            closed = os.fstat(stream.fileno())
        after = require_local_path(path, root=rounds.REPORTS_ROOT)
        if (len(raw) > limit or after is None
                or not (file_identity(before) == file_identity(opened)
                        == file_identity(closed) == file_identity(after))
                or parent_identity() != before_parent or len(raw) != after.st_size):
            raise Round1WorkspaceError('R1_CANDIDATE_INPUT_CHANGED')
        candidate = json.loads(raw)
        if type(candidate) is not dict:
            raise Round1WorkspaceError('R1_CANDIDATE_INPUT_INVALID')
        return {'schema_version': 'round1-candidate-input/v1',
                'relative_name': path.name, 'present': True,
                'byte_length': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
                'raw_base64': base64.b64encode(raw).decode('ascii')}, candidate
    except (OSError, ValueError, UnicodeError, ImmutableFileError) as error:
        if isinstance(error, Round1WorkspaceError):
            raise
        raise Round1WorkspaceError('R1_CANDIDATE_INPUT_INVALID') from error


def _checked_candidate_input(document):
    """A legacy row cannot adopt a new source or acquire execution authority."""
    current = _candidate_input(document['scope']['offer_id'])
    if 'candidate_input' not in document:
        if current[0]['present']:
            raise Round1WorkspaceError('R1_CANDIDATE_INPUT_UNBOUND')
    elif current[0] != document['candidate_input']:
        raise Round1WorkspaceError('R1_CANDIDATE_INPUT_CHANGED')
    return current


def _packet(server, scope, *, bound, candidate_input=None):
    from shared_platform.release_control import build_release_dashboard
    plan, image_digest, image_revision = _image_input(scope['offer_id'])
    if candidate_input is None:
        candidate_input = _candidate_input(scope['offer_id'])
    module = _module(server)
    if candidate_input[1] is not None:
        try:
            module._safe_candidate_plan(candidate_input[1], scope['requested_targets'])
        except module.PreparationError as error:
            raise Round1WorkspaceError('R1_CANDIDATE_INPUT_INVALID') from error
    packet = module.prepare_offer(
        offer_id=scope['offer_id'], requested_targets=scope['requested_targets'],
        image_execution_plan=plan, category_source_region=scope['source_region'],
        category_observation=scope['observer_reference'] if bound else None,
        category_account_digest=scope['account_identity_digest'] if bound else None,
        candidate_plan=candidate_input[1],
        preview_builder=lambda offer: build_release_dashboard(offer_id=offer))
    return packet, image_digest, image_revision


def _semantic_packet(packet):
    # The one documented transition is Product Center approval revision n -> n+1.
    # No fact/target/price/image/category input is removed from this comparison.
    value = deepcopy(packet)
    value.pop('product_center_revision', None)
    return value


def _same_state(document, state):
    before = document['state_before']
    if state == before:
        return 'PREPARED'
    approval = state.get('product_approval') or {}
    if approval.get('round1_prepared_reference') != document['reference']:
        raise Round1WorkspaceError('R1_STATE_CHANGED')
    expected = deepcopy(before)
    expected.update(offer_id=document['scope']['offer_id'], _revision=before['_revision'] + 1,
                    updated_at=state.get('updated_at'), product_approval=approval)
    expected['review'].update(fields_locked=True, seller_sku=approval.get('seller_sku'))
    if (state != expected or approval.get('status') != 'approved'
            or approval.get('approved_by') != document['actor']
            or approval.get('input_fingerprint') != document['approval_fingerprint']
            or approval.get('round1_review_digest') != digest(document['packet'])):
        raise Round1WorkspaceError('R1_APPROVAL_SCOPE_CHANGED')
    return 'APPROVED'


def _recheck(server, document):
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform.round1_category_observations import resolve_record
    scope = document['scope']
    state = workbench.load_state(scope['offer_id'])
    phase = _same_state(document, state)
    if server._local_product_approval_actor() != document['actor']:
        raise Round1WorkspaceError('R1_ACTOR_CHANGED')
    # Rebuild actual current inputs, without overlaying an old revision on them.
    candidate_input = _checked_candidate_input(document)
    packet, image_digest, image_revision = _packet(server, scope, bound=False, candidate_input=candidate_input)
    if (image_digest != document['image_digest'] or image_revision != scope['product_center_revision']
            or _semantic_packet(packet) != _semantic_packet(document['unbound_packet'])
            or packet['product_center_revision'] != state['_revision']):
        raise Round1WorkspaceError('R1_PREPARED_INPUT_CHANGED')
    account = server._round1_category_context(dict(scope, product_center_revision=state['_revision']))['source_account']
    if (account.get('readiness') == 'UNKNOWN' or
            account.get('account_identity_digest') not in {None, scope['account_identity_digest']}):
        raise Round1WorkspaceError('R1_ACCOUNT_CHANGED')
    # The receipt remains bound to the reviewed revision. The exact approved-state
    # transition above proves that no facts changed during the revision increment.
    resolve_record(default_release_store(), scope['observer_reference'], review=document['packet'],
                   account_identity_digest=scope['account_identity_digest'])
    return phase, state


def prepare(server, data):
    from modules.sourcing import new_product_workbench as workbench
    keys = {'offer_id','product_center_revision','requested_targets','source_region',
            'account_identity_digest','context_digest','observer_reference','request_id'}
    if (type(data) is not dict or set(data) != keys
            or type(data['request_id']) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', data['request_id'])
            or type(data['offer_id']) is not str or not re.fullmatch(r'[0-9]{1,32}', data['offer_id'])
            or type(data['product_center_revision']) is not int or data['product_center_revision'] < 0):
        raise Round1WorkspaceError('R1_PREPARE_REQUEST_INVALID')
    with server._product_workbench_lock(data['offer_id']), workbench._state_write_lock(data['offer_id']), _transaction() as connection:
        existing = connection.execute('SELECT * FROM round1_workspace_preparations WHERE request_id=?', (data['request_id'],)).fetchone()
        if existing is not None:
            if existing['request_json'] != _json(data):
                raise Round1WorkspaceError('R1_REQUEST_ID_CONFLICT')
            document = _decode(existing, connection=connection)
            phase, _ = _recheck(server, document)
            result = projection(document)
            result['status'] = phase
            return result
        scope = {k: v for k,v in data.items() if k not in {'request_id','context_digest'}}
        context = server._round1_category_context(scope)
        if (context['context_digest'] != data['context_digest'] or context['source_account']['readiness'] == 'UNKNOWN'
                or context['source_account']['account_identity_digest'] not in {None, data['account_identity_digest']}):
            raise Round1WorkspaceError('R1_CONTEXT_CHANGED')
        state = workbench.load_state(data['offer_id'])
        if state['_revision'] != data['product_center_revision']:
            raise Round1WorkspaceError('R1_REVISION_CHANGED')
        candidate_input = _candidate_input(scope['offer_id'])
        packet, image_digest, image_revision = _packet(server, scope, bound=True, candidate_input=candidate_input)
        unbound, _, _ = _packet(server, scope, bound=False, candidate_input=candidate_input)
        if image_revision != state['_revision'] or packet['product_center_revision'] != state['_revision']:
            raise Round1WorkspaceError('R1_IMAGE_PLAN_STALE')
        if sorted(rounds.canonical_targets_to_workbench_sites(scope['requested_targets'])) != sorted(state['review'].get('selected_sites', [])):
            raise Round1WorkspaceError('R1_SAVED_TARGET_SCOPE_MISMATCH')
        rounds.validate_round1_reviewable(packet, report_directory=rounds.report_dir(data['offer_id']))
        from shared_platform.release_control import build_release_dashboard
        dashboard = build_release_dashboard(offer_id=data['offer_id'])
        preview = dashboard.get('approval_rehearsal') or dashboard.get('approval') or {}
        proposed = (preview.get('state_patch_preview') or {}).get('product_approval') or {}
        if not preview.get('ready') or not proposed.get('input_fingerprint'):
            raise Round1WorkspaceError('R1_PRODUCT_APPROVAL_UNREADY')
        preparation_root = _preparation_root(connection, scope=scope, state=state,
            unbound_packet=unbound, request_id=data['request_id'], server=server)
        document = {'scope':scope,'packet':packet,'unbound_packet':unbound,'image_digest':image_digest,
                    'preparation_root': preparation_root,
                    'candidate_input':candidate_input[0],
                    'state_before':state,'actor':server._local_product_approval_actor(),
                    'approval_fingerprint':proposed['input_fingerprint'],'request_id':data['request_id']}
        document['reference'] = 'r1-prepared:' + digest(document)[7:]
        if workbench.load_state(data['offer_id']) != state:
            raise Round1WorkspaceError('R1_STATE_CHANGED')
        _checked_candidate_input(document)
        connection.execute('INSERT INTO round1_workspace_preparations VALUES (?,?,?,?,?,?)',
                           (data['request_id'],data['offer_id'],document['reference'],_json(data),_json(document),digest(document)))
        return projection(document)


def projection(document):
    return {'ok':True,'status':'PREPARED','prepared_reference':document['reference'],
            'request_id':document['request_id'],'packet':document['packet'],
            'review_digest':digest(document['packet']),'approval_actor':document['actor'],
            'external_write_count':0}


def status(server, offer, reference=None, request_id=None):
    document = read_preparation(offer, reference, request_id)
    from modules.sourcing import new_product_workbench as workbench
    observed = workbench.load_state(offer)
    technical = (observed.get('product_approval') or {}).get('approved_by') == rounds.AUTOPILOT_ACTOR
    decision = None
    if technical:
        try:
            from shared_platform.immutable_approval_files import require_local_path
            path = rounds.report_dir(offer) / 'round1-auto-decision.json'
            require_local_path(path, root=rounds.REPORTS_ROOT)
            decision = json.loads(path.read_text(encoding='utf-8'))
            review = rounds.validate_round1_reviewable(document['packet'], report_directory=rounds.report_dir(offer))
            rounds.validate_round1_auto_decision(decision, review)
            phase, state = _auto_recheck(server, document, decision)
        except Exception:
            # The Product Center flag alone must never appear actionable when
            # the durable decision or original prepared facts cannot be proved.
            result = projection(document)
            result.update(ok=False, status='RECONCILIATION_REQUIRED',
                          code='R1_AUTO_IDENTITY_REQUIRES_RECONCILIATION',
                          current_revision=observed.get('_revision'))
            return result
    else:
        phase, state = _recheck(server, document)
    result = projection(document)
    result.update(status=phase, current_revision=state['_revision'])
    path = rounds.report_dir(offer) / 'round1-approved-snapshot.json'
    if path.is_file():
        if phase != 'APPROVED':
            raise Round1WorkspaceError('R1_SNAPSHOT_SCOPE_CONFLICT')
        expected = (rounds.build_round1_snapshot(first_review=document['packet'], state=state,
                    approved_by=rounds.AUTOPILOT_ACTOR, approved_at=decision['decided_at'],
                    report_directory=rounds.report_dir(offer), decision_receipt=decision)
                    if technical else _snapshot(document, state))
        actual = json.loads(path.read_text(encoding='utf-8'))
        if actual != expected:
            raise Round1WorkspaceError('R1_SNAPSHOT_SCOPE_CONFLICT')
        result.update(status='FROZEN', snapshot=actual, persisted_readback=True)
    elif technical and phase == 'APPROVED':
        # A durable technical CAS is not a usable round-1 result until its
        # immutable snapshot has been recovered and read back.
        result.update(ok=False, status='APPROVED_NOT_FROZEN',
                      code='R1_PERSISTENCE_REQUIRES_RECONCILIATION')
    return result


def _snapshot(document, state):
    return rounds.build_round1_snapshot(first_review=document['packet'], state=state,
        approved_by=state['product_approval']['approved_by'],
        approved_at=state['product_approval']['approved_at'],
        report_directory=rounds.report_dir(document['scope']['offer_id']))


def freeze(server, data, *, approve=False):
    from modules.sourcing import new_product_workbench as workbench
    base = {'offer_id','prepared_reference'}
    if type(data) is not dict or set(data) != (base | {'user_approved','approved_by'} if approve else base):
        raise Round1WorkspaceError('R1_FREEZE_REQUEST_INVALID')
    document = read_preparation(data['offer_id'], data['prepared_reference'])
    if approve and (data['user_approved'] is not True or data['approved_by'] != document['actor']):
        raise Round1WorkspaceError('R1_APPROVAL_ACTOR_INVALID')
    with server._product_approval_lock, server._product_workbench_lock(data['offer_id']), workbench._state_write_lock(data['offer_id']):
        phase, state = _recheck(server, document)
        if phase == 'PREPARED':
            if not approve:
                raise Round1WorkspaceError('R1_APPROVAL_REQUIRED')
            code, response = server._approve_product_workspace_locally_locked({
                'offer_id':data['offer_id'],'expected_revision':state['_revision'],
                'approved_by':document['actor'],'user_approved':True}, round1_binding={
                    'round1_prepared_reference':document['reference'], 'round1_review_digest':digest(document['packet'])})
            # Approval may have saved before a dashboard refresh failed. Only actual
            # durable state can establish that transition; no response fabrication.
            phase, state = _recheck(server, document)
            if phase != 'APPROVED':
                raise Round1WorkspaceError('R1_APPROVAL_NOT_SAVED')
        snapshot = _snapshot(document, state)
        try:
            rounds.persist_round1_snapshot(snapshot)
        except (OSError, ValueError):
            result = projection(document)
            result.update(ok=False,status='APPROVED_NOT_FROZEN',code='R1_PERSISTENCE_REQUIRES_RECONCILIATION',current_revision=state['_revision'])
            return result
        return status(server, data['offer_id'], document['reference'])


def _auto_recheck(server, document, decision):
    """Re-read the prepared inputs and the exact technical state transition."""
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform.round1_category_observations import resolve_record

    scope = document['scope']
    state = workbench.load_state(scope['offer_id'])
    before = document['state_before']
    if state == before:
        phase = 'PREPARED'
    else:
        approval = state.get('product_approval') or {}
        expected = deepcopy(before)
        expected.update(offer_id=scope['offer_id'], _revision=before['_revision'] + 1,
                        updated_at=state.get('updated_at'), product_approval=approval)
        expected['review'].update(fields_locked=True, seller_sku=approval.get('seller_sku'))
        if (state != expected or approval.get('status') != 'approved'
                or approval.get('approved_by') != rounds.AUTOPILOT_ACTOR
                or approval.get('approval_authority') != 'ACTIVE_AUTOPILOT_POLICY'
                or approval.get('decision_digest') != decision['decision_digest']
                or approval.get('approved_at') != decision['decided_at']
                or approval.get('round1_prepared_reference') != document['reference']
                or approval.get('round1_review_digest') != digest(document['packet'])
                or approval.get('input_fingerprint') != document['approval_fingerprint']):
            raise Round1WorkspaceError('R1_AUTO_STATE_CONFLICT')
        phase = 'APPROVED'
    if server._local_product_approval_actor() != document['actor']:
        raise Round1WorkspaceError('R1_ACTOR_CHANGED')
    candidate_input = _checked_candidate_input(document)
    packet, image_digest, image_revision = _packet(server, scope, bound=False, candidate_input=candidate_input)
    if (image_digest != document['image_digest'] or image_revision != scope['product_center_revision']
            or _semantic_packet(packet) != _semantic_packet(document['unbound_packet'])
            or packet['product_center_revision'] != state['_revision']):
        raise Round1WorkspaceError('R1_PREPARED_INPUT_CHANGED')
    account = server._round1_category_context(dict(scope, product_center_revision=state['_revision']))['source_account']
    if (account.get('readiness') == 'UNKNOWN' or
            account.get('account_identity_digest') not in {None, scope['account_identity_digest']}):
        raise Round1WorkspaceError('R1_ACCOUNT_CHANGED')
    resolve_record(default_release_store(), scope['observer_reference'], review=document['packet'],
                   account_identity_digest=scope['account_identity_digest'])
    return phase, state


def auto_freeze(server, data):
    """Adopt complete R1 facts under the active technical policy, never as Kyle.

    The immutable decision precedes the Product Center CAS. A crash between
    the decision, state and snapshot is recoverable only for the same source,
    revision and policy; no repeated state write is needed after the CAS.
    This is a domain entry, deliberately not an HTTP approval route or worker.
    """
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform import publication_autopilot
    from shared_platform.immutable_approval_files import persist_immutable_bytes, require_local_path
    from shared_platform.release_control import build_release_dashboard

    if (type(data) is not dict or set(data) != {'offer_id', 'prepared_reference', 'policy_digest'}
            or type(data['offer_id']) is not str or not re.fullmatch(r'[0-9]{1,32}', data['offer_id'])
            or type(data['prepared_reference']) is not str or not data['prepared_reference'].startswith('r1-prepared:')
            or type(data['policy_digest']) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}', data['policy_digest'])):
        raise Round1WorkspaceError('R1_AUTO_REQUEST_INVALID')
    offer = data['offer_id']
    with server._product_approval_lock, server._product_workbench_lock(offer), workbench._state_write_lock(offer):
        document = read_preparation(offer, data['prepared_reference'])
        directory = rounds.report_dir(offer)
        require_local_path(directory / 'first-review.json', root=rounds.REPORTS_ROOT)
        require_local_path(directory / 'round1-approved-snapshot.json', root=rounds.REPORTS_ROOT)
        review = rounds.validate_round1_reviewable(document['packet'], report_directory=directory)
        if (review.get('status') != 'FIRST_REVIEW_READY' or review.get('blockers') != []
                or review.get('product_center_revision') != document['scope']['product_center_revision']
                or review.get('target_selection', {}).get('requested') != document['scope']['requested_targets']):
            raise Round1WorkspaceError('R1_AUTO_REVIEW_INCOMPLETE')
        # The preparation producer owns parcel/cost and per-target price checks.
        material = _module(server)._material_review_blockers(
            product_facts=review.get('product_facts') or {}, targets=review.get('targets') or [],
            image_plan=review['image_execution_plan'],
            requested_targets=document['scope']['requested_targets'],
            content_groups=review.get('content_groups'))
        selected_skus = {str(row.get('seller_sku') or '') for row in
                         (review.get('product_facts') or {}).get('skus') or [] if isinstance(row, dict)}
        if (not selected_skus or '' in selected_skus
                or (len(selected_skus) > 1 and any(
                    not (row.get('price') or {}).get('sku_prices') for row in review.get('targets') or []
                    if row.get('target') != 'miaoshou:COMMON'))):
            material.append('R1_AUTO_SKU_PRICE_COVERAGE_INCOMPLETE')
        if material:
            raise Round1WorkspaceError('R1_AUTO_MATERIAL_INCOMPLETE')
        if (directory / 'round1-approved-snapshot.json').exists() and not (directory / 'round1-auto-decision.json').is_file():
            raise Round1WorkspaceError('R1_EXISTING_SNAPSHOT_CONFLICT')
        decision_path = directory / 'round1-auto-decision.json'
        require_local_path(decision_path, root=rounds.REPORTS_ROOT)
        if decision_path.is_file():
            try:
                decision = json.loads(decision_path.read_text(encoding='utf-8'))
                rounds.validate_round1_auto_decision(decision, review)
            except (ValueError, TypeError, KeyError, OSError) as error:
                raise Round1WorkspaceError('R1_AUTO_DECISION_CONFLICT') from error
            if decision.get('policy_digest') != data['policy_digest']:
                raise Round1WorkspaceError('R1_AUTO_POLICY_CHANGED')
        else:
            # A new decision always needs the current, active policy. A
            # committed old decision may be recovered below without granting
            # a second approval under a changed policy.
            policy = publication_autopilot.load_autopilot_policy()
            if (policy.get('status') != 'ACTIVE' or rounds.canonical_digest(policy) != data['policy_digest']
                    or policy['review_contract']['intermediate_human_approval_required'] is not False):
                raise Round1WorkspaceError('R1_AUTO_POLICY_CHANGED')
            # No authority is written until every source and dashboard check passes.
            _recheck(server, document)
            decision = rounds.build_round1_auto_decision(review, policy=policy)
        phase, state = _auto_recheck(server, document, decision)
        if phase == 'PREPARED':
            # An immutable decision alone is not an approval. A pre-CAS retry
            # must still be authorized by the policy active right now.
            policy = publication_autopilot.load_autopilot_policy()
            if (policy.get('status') != 'ACTIVE' or rounds.canonical_digest(policy) != data['policy_digest']
                    or policy['review_contract']['intermediate_human_approval_required'] is not False
                    or decision['policy_id'] != policy['policy_id']):
                raise Round1WorkspaceError('R1_AUTO_POLICY_CHANGED')
            if (directory / 'round1-approved-snapshot.json').exists():
                raise Round1WorkspaceError('R1_EXISTING_SNAPSHOT_CONFLICT')
            dashboard = build_release_dashboard(offer_id=offer)
            preview = dashboard.get('approval_rehearsal') or dashboard.get('approval') or {}
            proposed = (preview.get('state_patch_preview') or {}).get('product_approval') or {}
            seller_sku = str((dashboard.get('product') or {}).get('seller_sku_candidate') or '').strip()
            if (not preview.get('ready') or preview.get('warnings')
                    or not re.fullmatch(r'[0-9]{1,32}', seller_sku)
                    or not proposed.get('input_fingerprint')
                    or proposed['input_fingerprint'] != document['approval_fingerprint']
                    or int((dashboard.get('product') or {}).get('revision') or -1) != state['_revision']
                    or state.get('product_approval')):
                raise Round1WorkspaceError('R1_AUTO_PRODUCT_PREVIEW_UNREADY')
            if rounds.canonical_digest(publication_autopilot.load_autopilot_policy()) != data['policy_digest']:
                raise Round1WorkspaceError('R1_AUTO_POLICY_CHANGED')
            if not decision_path.is_file():
                encoded = (json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
                persist_immutable_bytes(decision_path, encoded, root=rounds.REPORTS_ROOT)
            approval = {**dict(proposed), 'approval_id': f"product-auto-approval:{offer}:{seller_sku}:{decision['decision_digest'][7:23]}",
                        'package_id': f'product:{offer}:{seller_sku}', 'status': 'approved',
                        'subject_type': 'product', 'subject_id': offer, 'seller_sku': seller_sku,
                        'approved_by': rounds.AUTOPILOT_ACTOR, 'approved_at': decision['decided_at'],
                        'approval_authority': 'ACTIVE_AUTOPILOT_POLICY', 'decision_digest': decision['decision_digest'],
                        'approval_warnings_acknowledged': [],
                        'source_reference': f"workbench:{offer}:revision:{state['_revision']}",
                        'round1_prepared_reference': document['reference'],
                        'round1_review_digest': digest(document['packet'])}
            next_state = deepcopy(state)
            next_state['review']['seller_sku'] = seller_sku
            next_state['review']['fields_locked'] = True
            next_state['product_approval'] = approval
            try:
                # Only Product Center's commercial approval fact changes here.
                # Mirroring unrelated content state can mutate a second offer.
                workbench.save_state(offer, next_state, mirror_content=False)
            except Exception as error:
                try:
                    observed_phase, _ = _auto_recheck(server, document, decision)
                except Exception:
                    raise Round1WorkspaceError('R1_AUTO_STATE_OUTCOME_UNKNOWN') from error
                if observed_phase != 'APPROVED':
                    raise Round1WorkspaceError('R1_AUTO_STATE_NOT_SAVED') from error
        phase, state = _auto_recheck(server, document, decision)
        if phase != 'APPROVED':
            raise Round1WorkspaceError('R1_AUTO_STATE_OUTCOME_UNKNOWN')
        snapshot = rounds.build_round1_snapshot(first_review=review, state=state,
            approved_by=rounds.AUTOPILOT_ACTOR, approved_at=decision['decided_at'],
            report_directory=directory, decision_receipt=decision)
        path = directory / 'round1-approved-snapshot.json'
        encoded = (json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
        try:
            persist_immutable_bytes(path, encoded, root=rounds.REPORTS_ROOT)
            if rounds.validate_round2_input(offer, workbench.load_state(offer)) != snapshot:
                raise Round1WorkspaceError('R1_AUTO_SNAPSHOT_READBACK_CONFLICT')
        except (OSError, ValueError) as error:
            raise Round1WorkspaceError('R1_AUTO_APPROVED_NOT_FROZEN') from error
        return {'ok': True, 'status': 'FROZEN', 'offer_id': offer,
                'prepared_reference': document['reference'], 'decision_digest': decision['decision_digest'],
                'snapshot': snapshot, 'persisted_readback': True, 'external_write_count': 0,
                'human_approval': False}
