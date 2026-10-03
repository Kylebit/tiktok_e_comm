"""One service-owned transport boundary for the exact COMMON EDIT endpoint.

Registration is local code coverage, never provider permission. Native account
and complete baseline coverage producers remain required by the original
technical admission. No constructor, marker or caller dictionary opens a write.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from copy import deepcopy
import hashlib

from shared_platform.publication_runtime_config import StartupConfig
from shared_platform.release_store import ReleaseStore, ReleaseAuthorizationError
from shared_platform import native_common_technical_execution as technical
from shared_platform.publication_runtime_config import _read

EDIT_PATH = '/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail'
_ACTIVE = None
_DISPATCH = ContextVar('native_common_edit_dispatch', default=None)
_EDIT_RESPONSE_CAPTURE = ContextVar('native_common_edit_response_capture', default=None)
REGISTERED_SOURCE_PATHS = (
    'shared_platform/native_common_edit_boundary.py',
    'modules/products/server.py', 'modules/products/release_adapters.py',
    'modules/sourcing/new_product_workbench.py', 'modules/miaoshou/oneclick_release.py',
    'modules/miaoshou/client.py',
)


class CommonEditBlocked(ReleaseAuthorizationError):
    """A proved local refusal before transport, not an unknown provider result."""


def _deny(reason):
    raise CommonEditBlocked(reason)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


@dataclass(frozen=True)
class CommonEditInstallationFacts:
    registration_status: str
    schema_status: str
    registered_source_digest: str | None = None
    plan_id: str | None = None
    preparation_root_id: str | None = None
    mutation_digest: str | None = None
    execution_authority: bool = False

    def diagnostic(self):
        return {'schema_version': 'native-common-edit-registration/v1',
                'registration_status': self.registration_status,
                'schema_status': self.schema_status,
                'registered_source_digest': self.registered_source_digest,
                'plan_id': self.plan_id, 'preparation_root_id': self.preparation_root_id,
                'mutation_digest': self.mutation_digest,
                'coverage_scope': 'NAMED_SERVICE_COMMON_EDIT_ENTRY_POINTS_ONLY',
                'provider_edit_permission': 'UNKNOWN',
                'execution_authority': False}


@dataclass(frozen=True)
class NativeCommonServiceCoverage:
    registered_source_digest: str
    plan_id: str
    preparation_root_id: str
    mutation_digest: str

    def diagnostic(self):
        return {'schema_version':'native-service-common-path-coverage/v1',
            'scope':'THIS_SERVICE_PREPARED_BASELINE_ONLY',
            'registered_source_digest':self.registered_source_digest,
            'plan_id':self.plan_id, 'preparation_root_id':self.preparation_root_id,
            'mutation_digest':self.mutation_digest,
            'observed_schema':'EXACT_NATIVE_TECHNICAL_SCHEMA',
            'provider_edit_permission':'UNKNOWN', 'external_offer_history':'NOT_ASSERTED'}


class NativeCommonEditBoundary:
    """Created only by trusted service startup; never installs a schema."""
    def __init__(self, config):
        if type(config) is not StartupConfig:
            _deny('COMMON_EDIT_SERVICE_STARTUP_REQUIRED')
        self._config = config
        self._source_root = Path(__file__).resolve().parents[1]
        self._source_digest = self._read_source_digest()

    def _read_source_digest(self):
        return sha256(_canonical({name: sha256(_read(self._source_root, name)).hexdigest()
                                  for name in REGISTERED_SOURCE_PATHS})).hexdigest()

    def installation_facts(self, store, plan_id=None):
        if self is not _ACTIVE or type(store) is not ReleaseStore or not store.path.is_file():
            return CommonEditInstallationFacts('NOT_INSTALLED', 'UNKNOWN')
        if self._read_source_digest() != self._source_digest:
            return CommonEditInstallationFacts('REGISTERED_SOURCE_CHANGED', 'UNKNOWN')
        # Existing mode=ro helper never creates the missing DB or tables.
        with store._connect_readonly() as db:
            db.execute('BEGIN')
            schema = technical._schema_installed(db)
            root_id = mutation_digest = None
            if plan_id is not None:
                row = db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',
                                 (plan_id,)).fetchone()
                if row is None:
                    _deny('COMMON_EDIT_PERSISTED_PLAN_MISSING')
                payload = json.loads(row['payload_json'])
                from shared_platform.r3_common_source_facts import NativeCommonSourceReader
                from shared_platform.native_common_budget_facts import _validate_plan_source
                NativeCommonSourceReader(store).read_source_facts(db, plan_id)
                source = _validate_plan_source(db, payload)
                if (payload.get('targets') != [technical.COMMON] or not source
                        or source['preparation_root']['status'] != 'PERSISTED_NATIVE_PREPARATION_ROOT'):
                    _deny('COMMON_EDIT_PREPARATION_ROOT_UNKNOWN')
                root_id = source['preparation_root']['root_id']
                from modules.products.release_adapters import _immutable_miaoshou_common_draft
                mutation_digest = sha256(_canonical(_immutable_miaoshou_common_draft(payload))).hexdigest()
        return CommonEditInstallationFacts('REGISTERED_NAMED_BOUNDARIES',
            'INSTALLED' if schema else 'UNKNOWN', self._source_digest,
            plan_id, root_id, mutation_digest)

    def bind_post(self, store, plan_id, post):
        if (self is not _ACTIVE or type(store) is not ReleaseStore
                or not store.path.is_file() or type(plan_id) is not str or not plan_id
                or not callable(post)):
            _deny('COMMON_EDIT_NATIVE_PLAN_AND_STORE_REQUIRED')
        return ManagedCommonTransport(self, store, plan_id, post)

    def same_snapshot_coverage(self, store, db, plan_id):
        """Observed active service paths and schema for this exact baseline.

        No caller marker, installer assertion, other connection or provider
        permission participates in these local facts.
        """
        from shared_platform.r3_common_source_facts import NativeCommonSourceReader
        from shared_platform.native_common_budget_facts import _validate_plan_source
        if self is not _ACTIVE or type(store) is not ReleaseStore or not store.path.is_file():
            _deny('COMMON_EDIT_BOUNDARY_NOT_INSTALLED')
        if self._read_source_digest() != self._source_digest:
            _deny('COMMON_EDIT_REGISTERED_SOURCE_CHANGED')
        NativeCommonSourceReader(store).validate_context(db)
        if not technical._schema_installed(db):
            _deny('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
        row = db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?', (plan_id,)).fetchone()
        if row is None:
            _deny('COMMON_EDIT_PERSISTED_PLAN_MISSING')
        payload = json.loads(row['payload_json'])
        source = _validate_plan_source(db, payload)
        if payload.get('targets') != [technical.COMMON] or not source:
            _deny('COMMON_EDIT_PREPARATION_ROOT_UNKNOWN')
        from modules.products.release_adapters import _immutable_miaoshou_common_draft
        return NativeCommonServiceCoverage(self._source_digest, plan_id,
            source['preparation_root']['root_id'],
            sha256(_canonical(_immutable_miaoshou_common_draft(payload))).hexdigest())

    def _pinned_config(self, store):
        """Read the trusted StartupConfig path, never ambient config fallback."""
        from modules.products import server
        from shared_platform.native_common_signing_context import _unique_object
        reader = server._service_common_signing_context_reader(store)
        if self is not _ACTIVE or reader is None or reader._config is not self._config:
            _deny('COMMON_EDIT_SERVICE_SIGNING_CONTEXT_REQUIRED')
        before = reader._signing_input()
        raw = _read(self._config.root, 'config/miaoshou.local.json')
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
        if hashlib.sha256(raw).digest() != before[0] or reader._signing_input() != before:
            _deny('COMMON_EDIT_CONFIG_CHANGED_DURING_READ')
        return value, before

    def _dispatch(self, transport, body, call):
        if self is not _ACTIVE:
            _deny('COMMON_EDIT_BOUNDARY_NOT_INSTALLED')
        if self._read_source_digest() != self._source_digest:
            _deny('COMMON_EDIT_REGISTERED_SOURCE_CHANGED')
        if transport._native_claim is not None:
            _deny('COMMON_EDIT_UNKNOWN_OR_COMPLETED_NOT_REDISPATCHED')
        store, plan_id = transport._store, transport._plan_id
        # Fail closed before mode=rw or reserve when genuine native facts are
        # unavailable. In particular, private transaction mechanics are not a
        # service account/coverage grant.
        try:
            technical._preflight_source_readonly(store, plan_id)
            with technical._existing_transaction(store) as db:
                if self._pinned_config(store)[1] != transport._pinned_input:
                    _deny('COMMON_EDIT_PINNED_CONFIG_CHANGED')
                facts = technical._service_facts(store, db, plan_id)
                self._check_mutation(db, facts, body, transport._current_response)
                if self._pinned_config(store)[1] != transport._pinned_input:
                    _deny('COMMON_EDIT_PINNED_CONFIG_CHANGED')
                reserved = technical._reserve_in_transaction(store, db, facts)
                consumed = technical._consume_in_transaction(db, facts, reserved['run_id'])
                if not consumed['consumed'] or consumed['state'] != 'UNKNOWN':
                    _deny('COMMON_EDIT_UNKNOWN_OR_COMPLETED_NOT_REDISPATCHED')
                target = db.execute('SELECT attempts FROM release_target_runs WHERE run_id=? AND target_label=?',
                                    (consumed['run_id'], technical.COMMON)).fetchone()
                claim = (consumed['run_id'], target['attempts'])
        except ReleaseAuthorizationError as error:
            raise CommonEditBlocked(str(error)) from error
        # UNKNOWN is committed before any transport. An exception or process
        # restart therefore never reissues a possibly dispatched mutation.
        transport._native_claim = claim
        permit = (self, transport, sha256(_canonical(body)).digest())
        token = _DISPATCH.set(permit)
        capture_token = _EDIT_RESPONSE_CAPTURE.set((self, transport, deepcopy(body)))
        try:
            return call()
        finally:
            _EDIT_RESPONSE_CAPTURE.reset(capture_token)
            _DISPATCH.reset(token)

    @staticmethod
    def _check_mutation(db, facts, body, current_response):
        if (type(body) is not dict or set(body) !=
                {'commonCollectBoxDetailId', 'editCommonCollectBoxDetail', 'ossMd5'}
                or str(body['commonCollectBoxDetailId']) != facts.offer_id
                or type(body['editCommonCollectBoxDetail']) is not dict):
            _deny('COMMON_EDIT_EXACT_MUTATION_REQUIRED')
        row = db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',
                         (facts.plan_id,)).fetchone()
        from modules.products.release_adapters import _build_immutable_common_edit
        if type(current_response) is not dict or current_response.get('result') != 'success':
            _deny('COMMON_EDIT_CURRENT_ENVELOPE_MISSING')
        current = current_response.get('data') or {}
        if type(current.get('editCommonCollectBoxDetail')) is not dict or not current.get('ossMd5'):
            _deny('COMMON_EDIT_CURRENT_ENVELOPE_MISSING')
        # Reuse the exact original adapter builder, including untouched provider
        # fields, selected SKU mapping, frozen logistics and labels. A legacy
        # image/cost mutation cannot hide in extra envelope fields.
        expected = {'commonCollectBoxDetailId': int(facts.offer_id),
            'editCommonCollectBoxDetail': _build_immutable_common_edit(
                json.loads(row['payload_json']), current['editCommonCollectBoxDetail']),
            'ossMd5': str(current['ossMd5'])}
        if _canonical(body) != _canonical(expected):
            _deny('COMMON_EDIT_MUTATION_CHANGED')


class ManagedCommonTransport:
    def __init__(self, boundary, store, plan_id, post):
        self._boundary, self._store, self._plan_id, self._post = boundary, store, plan_id, post
        self._current_response = None
        self._native_claim = None
        self._accepted_response_retained = False
        self._accepted_response_digest = None
        self._pinned_input = None

    def retain_readback(self, evidence):
        """Bind the original observed packet to this transport's durable claim."""
        require_managed_transport(self)
        if self._native_claim is None:
            _deny('COMMON_EDIT_SAME_LEDGER_CLAIM_REQUIRED')
        run_id, attempt = self._native_claim
        if self._accepted_response_digest is not None:
            from modules.products.release_adapters import bind_native_common_readback
            evidence = bind_native_common_readback(evidence, {**evidence,
                'prior_external_write_evidence_digest':self._accepted_response_digest})
        return technical.retain_readback(self._store, self._plan_id, run_id, attempt, evidence)

    def retain_read_failure(self):
        if not self._accepted_response_retained:
            return False
        run_id, attempt = self._native_claim
        try:
            technical.retain_read_failure(self._store, self._plan_id, run_id, attempt,
                                          'COMMON_ACCEPTED_EDIT_READBACK_UNPROVEN')
            return True
        except ReleaseAuthorizationError:
            # Retention failure never changes UNKNOWN or suppresses the original
            # provider/readback exception at the HTTP boundary.
            return False

    def __call__(self, path, body=None, **kwargs):
        from modules.products.release_adapters import MIAOSHOU_COMMON_DETAIL_PATH
        normalized = '/' + str(path).lstrip('/')
        if normalized == EDIT_PATH and kwargs:
            _deny('COMMON_EDIT_TRANSPORT_CONFIG_OVERRIDE_FORBIDDEN')
        if normalized in {EDIT_PATH, MIAOSHOU_COMMON_DETAIL_PATH}:
            if any(k in kwargs for k in ('cfg','app_key','app_secret','base_url')):
                _deny('COMMON_EDIT_CALLER_SIGNING_OVERRIDE_FORBIDDEN')
            config, identity = self._boundary._pinned_config(self._store)
            if self._pinned_input is not None and identity != self._pinned_input:
                _deny('COMMON_EDIT_PINNED_CONFIG_CHANGED')
            self._pinned_input = identity
            kwargs = {**kwargs, 'cfg':config}
        if '/' + str(path).lstrip('/') != EDIT_PATH:
            value = self._post(path, body, **kwargs)
            from modules.products.release_adapters import MIAOSHOU_COMMON_DETAIL_PATH
            if ('/' + str(path).lstrip('/') == MIAOSHOU_COMMON_DETAIL_PATH
                    and type(body) is dict
                    and set(body) == {'commonCollectBoxDetailId'}):
                self._current_response = deepcopy(value)
            return value
        return self._boundary._dispatch(self, body,
            lambda: self._post(path, body, **kwargs))


def require_managed_transport(post):
    if (type(post) is not ManagedCommonTransport or post._boundary is not _ACTIVE
            or _ACTIVE is None):
        _deny('COMMON_EDIT_PERSISTED_NATIVE_BASELINE_REQUIRED')


def native_edit_response_capture(path):
    """Only the already consumed, exact service request can retain acceptance."""
    capture = _EDIT_RESPONSE_CAPTURE.get()
    if '/' + str(path).lstrip('/') != EDIT_PATH or capture is None:
        return None
    active, transport, body = capture
    require_managed_transport(transport)
    if active is not _ACTIVE or transport._native_claim is None:
        _deny('COMMON_EDIT_SAME_LEDGER_CLAIM_REQUIRED')
    return capture


def retain_native_edit_response(path, request_bytes, packet):
    capture = native_edit_response_capture(path)
    if capture is None:
        _deny('COMMON_EDIT_WIRE_CAPTURE_CONTEXT_REQUIRED')
    active, transport, body = capture
    if _canonical(json.loads(request_bytes)) != _canonical(body):
        _deny('COMMON_EDIT_WIRE_REQUEST_CHANGED')
    if active._pinned_config(transport._store)[1] != transport._pinned_input:
        _deny('COMMON_EDIT_PINNED_CONFIG_CHANGED')
    run_id, attempt = transport._native_claim
    transport._accepted_response_digest = technical._retain_edit_acceptance(transport._store, transport._plan_id,
        run_id, attempt, body, packet)
    transport._accepted_response_retained = True


def require_edit_dispatch(path, body):
    if '/' + str(path).lstrip('/') != EDIT_PATH:
        return
    permit = _DISPATCH.get()
    if (permit is None or permit[0] is not _ACTIVE or _ACTIVE is None
            or type(permit[1]) is not ManagedCommonTransport
            or permit[2] != sha256(_canonical(body)).digest()):
        _deny('COMMON_EDIT_SAME_LEDGER_CLAIM_REQUIRED')
    # One committed UNKNOWN claim permits one exact native transport call.
    # A callback cannot issue the same POST twice within the context.
    _DISPATCH.set(None)


def install_service_boundary(config):
    global _ACTIVE
    previous = _ACTIVE
    _ACTIVE = NativeCommonEditBoundary(config)
    return previous, _ACTIVE


def restore_service_boundary(previous, installed):
    global _ACTIVE
    if _ACTIVE is installed:
        _ACTIVE = previous


def service_boundary():
    if _ACTIVE is None:
        _deny('COMMON_EDIT_BOUNDARY_NOT_INSTALLED')
    return _ACTIVE
