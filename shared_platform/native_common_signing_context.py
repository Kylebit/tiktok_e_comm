"""Service-owned COMMON signing identity and retained read context.

These facts distinguish the R1 Shopee source account from the COMMON signing
application. Retained-only reads never claim current acceptance. An explicit
native read_current operation obtains a new signed READ comparison; it never
claims edit permission, installation coverage or execution authority. Neither
path writes a database or file, and startup performs no provider request.
"""
from dataclasses import dataclass, field, replace
import hashlib
import json
import re
import sqlite3
import time

from modules.miaoshou.client import OPEN_BASE_URL, COMMON_DETAIL_OBSERVATION_PATH
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.publication_runtime_config import StartupConfig, _read
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.release_store import ReleaseStore, ReleaseStoreError
from shared_platform.native_common_budget_facts import _validate_plan_source
from shared_platform.native_common_retained_completion import read_retained_completion


@dataclass(frozen=True)
class NativeCommonSigningContextFacts:
    status: str
    reason: str | None = None
    offer_id: str | None = None
    preparation_root_id: str | None = None
    r1_source_account_digest: str | None = None
    common_signing_context_digest: str | None = None
    retained_readback_digest: str | None = None
    retained_readback_verified_at: str | None = None
    current_read_acceptance: str = 'UNKNOWN'
    current_comparison_digest: str | None = None
    current_fields_match: bool | None = None
    current_observed_at_epoch: int | None = None
    current_observation_bytes: bytes | None = field(default=None, repr=False)

    def diagnostic(self):
        return {'schema_version': 'native-common-signing-context-facts/v1',
            'status': self.status, 'reason': self.reason, 'offer_id': self.offer_id,
            'preparation_root_id': self.preparation_root_id,
            'r1_source_account_digest': self.r1_source_account_digest,
            'common_signing_context_digest': self.common_signing_context_digest,
            'endpoint': OPEN_BASE_URL + COMMON_DETAIL_OBSERVATION_PATH,
            'retained_readback_digest': self.retained_readback_digest,
            'retained_readback_verified_at': self.retained_readback_verified_at,
            'provider_account_number': 'UNKNOWN', 'current_read_acceptance': self.current_read_acceptance,
            'current_comparison_digest': self.current_comparison_digest,
            'current_fields_match': self.current_fields_match,
            'current_observed_at_epoch': self.current_observed_at_epoch,
            'write_endpoint_permission': 'UNKNOWN', 'coverage_authority': 'UNKNOWN',
            'execution_authority': False}


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('COMMON_SIGNING_CONFIG_DUPLICATE_KEY')
        result[name] = value
    return result


class NativeCommonSigningContextReader:
    """Inject from native StartupConfig; no HTTP-supplied receipt/reader fields.

    The existing exact SQLite snapshot and original retained packet validators
    are reused. A type or constructor is not a provider permission grant.
    """
    def __init__(self, config, store):
        if type(config) is not StartupConfig or type(store) is not ReleaseStore:
            raise TypeError('COMMON_SIGNING_SERVICE_CONTEXT_REQUIRED')
        self._config, self._store = config, store

    def _signing_input(self):
        raw = _read(self._config.root, 'config/miaoshou.local.json')
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
        if (type(value) is not dict or type(value.get('app_id')) is not str
                or not value['app_id'].strip() or type(value.get('app_secret')) is not str
                or not value['app_secret'].strip()
                or type(value.get('base_url', OPEN_BASE_URL)) is not str
                or value.get('base_url', OPEN_BASE_URL).rstrip('/') != OPEN_BASE_URL):
            raise ValueError('COMMON_SIGNING_CONFIG_INVALID')
        app_scope = hashlib.sha256(('miaoshou-app-scope:' + value['app_id']).encode()).hexdigest()
        return hashlib.sha256(raw).digest(), app_scope

    def read(self, db, common_plan_id, *, common_run_id=None):
        """Return useful identity/read facts, never reserve/dispatch permission."""
        try:
            if not self._store.path.is_file():
                raise ValueError('COMMON_SIGNING_STORE_UNAVAILABLE')
            reader = NativeCommonSourceReader(self._store)
            reader.validate_context(db)
            config_digest, app_scope = self._signing_input()
            row = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (common_plan_id,)).fetchone()
            if row is None:
                raise ValueError('COMMON_SIGNING_PLAN_MISSING')
            payload = json.loads(row['payload_json'])
            if payload.get('targets') != ['miaoshou:COMMON']:
                raise ValueError('COMMON_SIGNING_TARGET_SCOPE_INVALID')
            # Original reader validates canonical plan bytes and immutable R1,
            # even when no future market plan or COMMON run exists yet.
            reader.read_source_facts(db, common_plan_id)
            source = _validate_plan_source(db, payload)
            if not source or source['preparation_root']['status'] != 'PERSISTED_NATIVE_PREPARATION_ROOT':
                raise ValueError('COMMON_SIGNING_PREPARATION_SOURCE_UNKNOWN')
            facts = NativeCommonSigningContextFacts('PINNED_COMMON_SIGNING_CONTEXT',
                offer_id=source['offer_id'], preparation_root_id=source['preparation_root']['root_id'],
                r1_source_account_digest=source['account_identity_digest'],
                common_signing_context_digest=app_scope)
            if common_run_id is not None:
                completion = read_retained_completion(reader, db, common_plan_id, common_run_id)
                readback = db.execute('SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?',
                    (common_run_id, 'miaoshou:COMMON')).fetchone()
                evidence = json.loads(readback['evidence_json'])
                packet = evidence['native_common_observation']
                if (packet['credential_scope_digest'] != app_scope
                        or packet['endpoint'] != OPEN_BASE_URL + COMMON_DETAIL_OBSERVATION_PATH
                        or packet['detail_id'] != source['offer_id']
                        or completion.preparation_root_id != facts.preparation_root_id
                        or completion.account_identity_digest != facts.r1_source_account_digest
                        or not re.fullmatch(r'[0-9a-f]{64}', completion.readback_digest)):
                    raise ValueError('COMMON_SIGNING_RETAINED_CONTEXT_CHANGED')
                facts = NativeCommonSigningContextFacts('PINNED_APP_AND_RETAINED_COMMON_READ_CONTEXT',
                    offer_id=facts.offer_id, preparation_root_id=facts.preparation_root_id,
                    r1_source_account_digest=facts.r1_source_account_digest,
                    common_signing_context_digest=app_scope,
                    retained_readback_digest=completion.readback_digest,
                    retained_readback_verified_at=readback['verified_at'])
            if self._signing_input() != (config_digest, app_scope):
                raise ValueError('COMMON_SIGNING_CONFIG_CHANGED_DURING_READ')
            return facts
        except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError,
                ReleaseStoreError, ApprovalBlocked, sqlite3.Error) as error:
            # Do not expose config values, credentials, raw bytes or paths.
            code = str(error) if str(error).startswith('COMMON_SIGNING_') else 'COMMON_SIGNING_CONTEXT_UNVERIFIED'
            return NativeCommonSigningContextFacts('UNKNOWN', code)

    def read_current(self, db, common_plan_id, *, common_run_id=None):
        """Explicit fixed signed READ, only when a native caller requests it.

        This is a provider read operation; it is never performed by startup,
        the retained-only getter, or the constructor. A live endpoint rejection
        cannot be replaced by an old accepted packet. Its exact private bytes
        permit later same-baseline freeze consumers to recheck the comparison.
        """
        facts = self.read(db, common_plan_id, common_run_id=common_run_id)
        if facts.status == 'UNKNOWN':
            return facts
        try:
            from modules.miaoshou.client import NativeCommonDetailObserver, MiaoshouBusinessRejectedError
            from modules.products.release_adapters import readback_miaoshou_common
            before = self._signing_input()
            raw = _read(self._config.root, 'config/miaoshou.local.json')
            value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
            if hashlib.sha256(raw).digest() != before[0] or before[1] != facts.common_signing_context_digest:
                raise ValueError('COMMON_SIGNING_CONFIG_CHANGED_DURING_READ')
            row = db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?', (common_plan_id,)).fetchone()
            evidence = readback_miaoshou_common(json.loads(row['payload_json']),
                observation_reader=NativeCommonDetailObserver(value))
            packet = evidence['native_common_observation']
            if (packet['credential_scope_digest'] != facts.common_signing_context_digest
                    or packet['detail_id'] != facts.offer_id
                    or packet['endpoint'] != OPEN_BASE_URL + COMMON_DETAIL_OBSERVATION_PATH
                    or self._signing_input() != before):
                raise ValueError('COMMON_SIGNING_CURRENT_CONTEXT_CHANGED')
            encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode('utf-8')
            # Acceptance of READ is separate from field equality and permission
            # to EDIT. A mismatched accepted detail is not a usable baseline.
            return replace(facts, status='CURRENT_SIGNED_COMMON_READ_ACCEPTED',
                current_read_acceptance='ACCEPTED', current_fields_match=evidence.get('verified') is True,
                current_comparison_digest=hashlib.sha256(encoded).hexdigest(),
                current_observed_at_epoch=int(time.time()),
                current_observation_bytes=encoded)
        except MiaoshouBusinessRejectedError as error:
            code = error.code if type(error.code) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,96}', error.code) else 'REJECTED'
            return replace(facts, status='UNKNOWN', reason='COMMON_SIGNING_CURRENT_READ_REJECTED:'+code,
                current_read_acceptance='REJECTED')
        except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError,
                RuntimeError, ReleaseStoreError, ApprovalBlocked, sqlite3.Error):
            return replace(facts, status='UNKNOWN', reason='COMMON_SIGNING_CURRENT_READ_UNAVAILABLE')


def inspect_service_signing_context(reader, db, common_plan_id, *, common_run_id=None):
    if type(reader) is not NativeCommonSigningContextReader:
        return NativeCommonSigningContextFacts('UNKNOWN', 'COMMON_SIGNING_SERVICE_READER_REQUIRED')
    return reader.read(db, common_plan_id, common_run_id=common_run_id)
