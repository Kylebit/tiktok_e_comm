"""Retained original-task R1/R2 admission with truthful separate source roles.

No new product task, approval, image work, COMMON call or source identity is
created. A startup installation binds an original e090 task/profile separately
from the running controller and completed R2 producer. The original executor
alone may adopt the real retained stage receipts using its normal lease.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import subprocess

from shared_platform.operations_runtime import RuntimeProfile, runtime_matches
from shared_platform.release_store import ReleaseStore
from shared_platform.workbench_engine import WorkbenchEngine


@dataclass(frozen=True)
class SourceRole:
    name: str
    profile: RuntimeProfile


@dataclass(frozen=True)
class RetainedFile:
    path: Path
    sha256: str

    def read(self, root):
        from shared_platform.immutable_approval_files import require_local_path
        require_local_path(self.path, root=root)
        raw = self.path.read_bytes()
        if not re.fullmatch(r'[0-9a-f]{64}', self.sha256) or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError('ORIGINAL_CONTINUATION_RETAINED_FILE_CHANGED')
        return raw


@dataclass(frozen=True)
class OriginalContinuationPins:
    task_id: str
    offer_id: str
    original_worker: str
    prepared_reference: str
    r2_identity_json: str
    quiescence_event_id: int
    quiescence_detail_sha256: str
    roles: tuple[SourceRole, ...]
    documents: tuple[RetainedFile, ...]
    original_state: RetainedFile


class OriginalPreparedTaskContinuation:
    def __init__(self, engine, pins, *, release_store_path, catalog_path=None):
        if type(engine) is not WorkbenchEngine or type(pins) is not OriginalContinuationPins:
            raise ValueError('ORIGINAL_CONTINUATION_CONCRETE_BINDING_REQUIRED')
        if (not pins.task_id or not pins.original_worker or not re.fullmatch(r'[0-9]{1,32}', pins.offer_id)
                or type(pins.quiescence_event_id) is not int or pins.quiescence_event_id < 1
                or not re.fullmatch(r'[0-9a-f]{64}', pins.quiescence_detail_sha256)
                or not re.fullmatch(r'r1-prepared:[0-9a-f]{64}', pins.prepared_reference)
                or tuple(role.name for role in pins.roles) != ('original', 'r1_controller', 'r1_recovery', 'r2_producer', 'service')
                or any(type(role) is not SourceRole or type(role.profile) is not RuntimeProfile for role in pins.roles)):
            raise ValueError('ORIGINAL_CONTINUATION_STARTUP_PINS_INVALID')
        self.engine, self.pins = engine, pins
        self.original_profile = pins.roles[0].profile
        self.r2_profile = pins.roles[3].profile
        self.service_profile = pins.roles[4].profile
        self.release_store = ReleaseStore(Path(release_store_path))
        self.catalog_path = Path(catalog_path).resolve(strict=True) if catalog_path is not None else None
        expected = {'code_version': self.original_profile.version, 'environment': self.original_profile.environment,
                    'manifest_digest': self.original_profile.manifest_digest}
        if (engine.release != expected or self.original_profile.environment != 'stable'
                or engine.store.path.resolve() != (self.original_profile.data_root / 'tasks.db').resolve()
                or any(not role.profile.manifest_digest for role in pins.roles)):
            raise ValueError('ORIGINAL_CONTINUATION_RELEASE_OR_LEDGER_CHANGED')

    def verify_sources(self):
        # The runtime's actual ROOT/version remains the new controller identity.
        # Each retained role keeps its genuine root/head/manifest.
        for role in self.pins.roles:
            if not runtime_matches(role.profile):
                raise ValueError('ORIGINAL_CONTINUATION_SOURCE_CHANGED:' + role.name)
            status = subprocess.run(['git', 'status', '--porcelain=v1', '-uall'], cwd=role.profile.root,
                capture_output=True, text=True, check=True, timeout=15).stdout.strip()
            if status:
                raise ValueError('ORIGINAL_CONTINUATION_DIRTY_SOURCE:' + role.name)
        from modules.products import server
        if Path(server.ROOT).resolve() != self.service_profile.root.resolve():
            raise ValueError('ORIGINAL_CONTINUATION_SERVICE_ROOT_CHANGED')

    def _original_task(self):
        with sqlite3.connect(self.engine.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            row = self.engine._row(db, self.pins.task_id)
            self.engine._require_version(row)
            event = db.execute('SELECT task_id,event_type,detail_json FROM workbench_events WHERE id=?',
                               (self.pins.quiescence_event_id,)).fetchone()
            if (event is None or event['task_id'] != self.pins.task_id or event['event_type'] != 'prepared_writer_quiesced'
                    or hashlib.sha256(event['detail_json'].encode()).hexdigest() != self.pins.quiescence_detail_sha256):
                raise ValueError('ORIGINAL_CONTINUATION_A2_EVENT_CHANGED')
            # Existing projection reads this same snapshot; it does not expire.
            task = self.engine._project(db, row)
            if task['scope'].get('offer_id') != self.pins.offer_id:
                raise ValueError('ORIGINAL_CONTINUATION_TASK_OFFER_CHANGED')
            return task

    def read_preparation(self):
        from shared_platform.round1_workspace import _decode
        with self.release_store._connect_readonly() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM round1_workspace_preparations WHERE reference=? AND offer_id=?',
                             (self.pins.prepared_reference, self.pins.offer_id)).fetchone()
            return _decode(row, connection=db)

    def read_frozen(self, task, profile):
        from shared_platform import publication_rounds as rounds
        from shared_platform.internal_catalog_sku import internal_sku
        from shared_platform.workbench_publication_native import _task_owns_snapshot
        self.verify_sources()
        if profile != self.original_profile or task.get('task_id') != self.pins.task_id or task != self._original_task():
            raise ValueError('ORIGINAL_CONTINUATION_TASK_OR_ROLE_CHANGED')
        state = json.loads(self.pins.original_state.read(self.original_profile.root))
        snapshot = rounds.validate_round2_input(self.pins.offer_id, state,
            reports_root=self.original_profile.root / 'reports/product-preparation')
        prepared = self.read_preparation()
        targets = sorted(snapshot['canonical_targets'])
        approval = state.get('product_approval') or {}
        review = state.get('review') or {}
        if (snapshot.get('status') != 'APPROVED' or snapshot['offer_id'] != self.pins.offer_id
                or task['scope']['shops'] != targets
                or prepared['reference'] != self.pins.prepared_reference
                or prepared['scope']['offer_id'] != self.pins.offer_id
                or sorted(prepared['scope']['requested_targets']) != targets
                or approval.get('round1_prepared_reference') != self.pins.prepared_reference
                or approval.get('round1_review_digest') != snapshot['first_review_digest']
                or rounds.canonical_digest(prepared['packet']) != snapshot['first_review_digest']
                or review.get('fields_locked') is not True
                or prepared['approval_fingerprint'] != snapshot['product_approval_fingerprint']):
            raise ValueError('ORIGINAL_CONTINUATION_RETAINED_R1_CHANGED')
        _task_owns_snapshot(task, profile, offer=self.pins.offer_id, targets=targets,
            prepared_reference=self.pins.prepared_reference, snapshot_digest=snapshot['snapshot_digest'],
            approved_at=snapshot['approved_at'])
        seller = str(snapshot['fact_snapshot']['product_facts'].get('seller_sku') or '')
        sku = internal_sku(seller)
        if not re.fullmatch(r'[0-9]{4}', sku) or review.get('seller_sku') != seller:
            raise ValueError('ORIGINAL_CONTINUATION_FROZEN_SKU_CHANGED')
        return {'offer_id': self.pins.offer_id, 'targets': targets, 'snapshot_digest': snapshot['snapshot_digest'],
                'prepared_reference': self.pins.prepared_reference, 'internal_sku': sku, 'seller_sku': seller}

    def read_review(self, task, profile, offer_id, reports_root, current_review, snapshot):
        from shared_platform.publication_rounds import canonical_digest
        frozen = self.read_frozen(task, profile)
        if (offer_id != self.pins.offer_id or Path(reports_root).resolve() != (self.r2_profile.root / 'reports/product-preparation').resolve()
                or frozen['snapshot_digest'] != snapshot['snapshot_digest']):
            raise ValueError('ORIGINAL_CONTINUATION_R2_ROOT_OR_R1_CHANGED')
        prepared = self.read_preparation()
        packet = prepared['packet']
        if canonical_digest(packet) != snapshot['first_review_digest'] or canonical_digest(current_review) != snapshot['first_review_digest']:
            raise ValueError('ORIGINAL_CONTINUATION_PREPARED_REVIEW_CHANGED')
        return packet

    def read_documents(self):
        from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, load_r2_documents, validate_r2_identity
        from shared_platform.workbench_publication_native import NativeR2PreparedReviewReader
        self.verify_sources()
        expected = self.r2_profile.root / 'reports/product-preparation' / self.pins.offer_id
        if (len(self.pins.documents) != len(R2_DOCUMENTS)
                or {file.path.resolve() for file in self.pins.documents} != {expected / name for name in R2_DOCUMENTS.values()}):
            raise ValueError('ORIGINAL_CONTINUATION_EXACT_SIX_DOCUMENTS_REQUIRED')
        for file in self.pins.documents:
            file.read(self.r2_profile.root)
        task = self._original_task()
        documents = load_r2_documents(self.pins.offer_id, reports_root=expected.parent,
            native_review_reader=NativeR2PreparedReviewReader(task, self.original_profile))
        if validate_r2_identity(documents) != json.loads(self.pins.r2_identity_json):
            raise ValueError('ORIGINAL_CONTINUATION_FULL_R2_IDENTITY_CHANGED')
        return documents

    def adopt_retained_stage_receipts(self, task, token, *, sku_reader):
        """Original leased executor only; no maintenance claim or provider call."""
        from shared_platform.retained_sku_namespace import RetainedSkuNamespaceReader
        from shared_platform.publication_r3_image_bridge import validate_r2_identity
        if type(sku_reader) is not RetainedSkuNamespaceReader or sku_reader.continuation is not self:
            raise ValueError('ORIGINAL_CONTINUATION_REAL_SKU_READER_REQUIRED')
        with self.engine.transaction() as db:
            row = self.engine._lease(db, self.pins.task_id, token)
            if row['worker'] != self.pins.original_worker:
                raise ValueError('ORIGINAL_CONTINUATION_ORIGINAL_WRITER_REQUIRED')
        task = self._original_task()
        frozen = self.read_frozen(task, self.original_profile)
        documents = self.read_documents()
        identity = validate_r2_identity(documents)
        sku_reader.read_verified(frozen['internal_sku'])
        if task['current_step'] == 'facts':
            self.engine.bind_scope(self.pins.task_id, token, {**task['scope'], 'skus': [frozen['internal_sku']]})
            self.engine.complete_step(self.pins.task_id, token, expected_step='facts', checkpoint={'native_r1': frozen})
            task = self._original_task()
        if task['current_step'] == 'images':
            self.engine.complete_step(self.pins.task_id, token, expected_step='images',
                checkpoint={'native_r1': frozen, 'native_r2': identity})
        current = self._original_task()
        if current['current_step'] != 'release':
            raise ValueError('ORIGINAL_CONTINUATION_RETAINED_STAGE_POSITION_CONFLICT')
        return current


_INSTALLED = None


def install_original_continuation(binding):
    global _INSTALLED
    if type(binding) is not OriginalPreparedTaskContinuation:
        raise ValueError('ORIGINAL_CONTINUATION_CONCRETE_BINDING_REQUIRED')
    binding.verify_sources()
    if _INSTALLED is not None and _INSTALLED is not binding:
        raise ValueError('ORIGINAL_CONTINUATION_ALREADY_INSTALLED')
    _INSTALLED = binding
    try:
        binding.read_documents()
    except BaseException:
        _INSTALLED = None
        raise


def configured_original_continuation(offer_id):
    binding = _INSTALLED
    if binding is None or str(offer_id) != binding.pins.offer_id:
        return None
    binding.verify_sources()
    return binding
