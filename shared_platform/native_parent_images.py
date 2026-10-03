"""Parent-owned R2 producer context for the exact admitted task and product ledger."""
from argparse import Namespace
from contextlib import contextmanager, ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path, PureWindowsPath
import re
import stat
from concurrent.futures import Future
from datetime import datetime, timezone

from shared_platform.publication_paid_requests import PaidRequestContext, PaidRequestBlocked


class _NativePaidContext(PaidRequestContext):
    """Recheck the actual parent lease/source immediately at each paid boundary."""
    def __init__(self, *, runtime, **kwargs):
        self._runtime = runtime
        super().__init__(**kwargs)

    def reserve(self, **kwargs):
        self._runtime.checked_root()
        return super().reserve(**kwargs)

    def invoke(self, key, call):
        self._runtime.checked_root()
        def checked_call():
            self._runtime.checked_root()
            return call()
        return super().invoke(key, checked_call)


class _ReadOnlyPaidContext(_NativePaidContext):
    def reserve(self, **kwargs):
        raise PaidRequestBlocked('R2_RECOVERY_CANNOT_RESERVE_OR_POST')

    def invoke(self, key, call):
        raise PaidRequestBlocked('R2_RECOVERY_CANNOT_RESERVE_OR_POST')


class _ContinuationPaidContext(_NativePaidContext):
    """Only first requests in a verified next phase; retained attempts never POST."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.allowed_purpose = None
        self._new_keys = set()

    def reserve(self, **kwargs):
        self._runtime.checked_root()
        if kwargs.get('purpose') != self.allowed_purpose or kwargs.get('attempt', 0) != 0:
            raise PaidRequestBlocked('R2_CONTINUATION_PHASE_NOT_VERIFIED')
        from modules.sourcing.image_generation_checkpoint import business_lock
        from shared_platform.publication_paid_requests import UNKNOWN
        purpose, model = kwargs['purpose'], kwargs['model']
        self.authorize(purpose, model)
        key, identity = self._key(purpose=purpose, business=kwargs['business'],
                                  request=kwargs['request'], attempt=0)
        # Original reservation contract, with stronger first-business and whole
        # product UNKNOWN checks in this same original journal/kernel lock.
        # Calling super().reserve here would recursively acquire a non-RLock.
        with business_lock(self.root, self.lock_key):
            rows, entries = self._load()
            self._initialize(rows)
            if any(row['business'] == identity['business'] for row in entries.values()):
                raise PaidRequestBlocked('R2_CONTINUATION_ORIGINAL_BUSINESS_EXISTS')
            if (self.root/f'raw-{key[:24]}.json').exists():
                self._raw(key)
                raise PaidRequestBlocked('a retained raw receipt without its reservation requires recovery')
            baseline = self._historical(rows)
            if any(row['provider'] == 'UNKNOWN' or row['state'] in {'UNKNOWN','ATTEMPTED'} for row in baseline):
                raise PaidRequestBlocked('R2_CONTINUATION_UNRESOLVED_PRODUCT_REQUEST')
            if any(row['state'] in UNKNOWN for row in entries.values()):
                raise PaidRequestBlocked('R2_CONTINUATION_UNRESOLVED_PRODUCT_REQUEST')
            occupied = len(baseline) + len(entries)
            if occupied >= self.cap:
                raise PaidRequestBlocked(f'PAID_BUDGET_EXHAUSTED: occupied {occupied}, cap {self.cap}; no provider call')
            self._runtime.checked_root()
            self._append(rows, 'RESERVE', key=key, request={**identity, 'model':model},
                         invocation_id=self.invocation_id, binding=self.binding, cap=self.cap)
        self._new_keys.add(key)
        return key

    def invoke(self, key, call):
        if key not in self._new_keys:
            raise PaidRequestBlocked('R2_CONTINUATION_CANNOT_POST_RETAINED_ATTEMPT')
        return super().invoke(key, call)


class _ParentImageExecutor:
    """Native generation remains on the parent thread which owns directory pins."""
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit(self, call, *args):
        future = Future()
        try:
            future.set_result(call(*args))
        except Exception as error:
            future.set_exception(error)
        return future


class _ReadOnlyClient:
    """Original GET methods only; no credentials are refreshed by Lingshi GETs."""
    _GETS = frozenset({'balance', 'list_skills', 'guide', 'list_models', 'model_detail', 'model_pricing', 'get_media_task'})

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        if name not in self._GETS:
            raise PaidRequestBlocked('R2_RECOVERY_PROVIDER_WRITE_FORBIDDEN')
        return getattr(self._client, name)


class NativeImageRuntime:
    """Created only inside the actual worker's parent hold; never an HTTP root input."""
    def __init__(self, adapter, task, token, frozen, *, history_root, history_digest, read_only=False):
        self.adapter, self.task, self.token = adapter, task, token
        self.frozen = frozen
        self.root = Path(adapter.profile.root)
        self.state_dir = self.root / 'data/new_product_workbench'
        self.reports_root = self.root / 'reports/product-preparation'
        self.policy_path = self.root / 'config/product_publication_autopilot_policy.json'
        self.config_path = self.root / 'config/lingshi.local.json'
        self.history_root, self.history_digest = history_root, history_digest
        self.paid_reports_root = history_root / 'reports/product-preparation'
        self.read_only = read_only
        self.policy_raw = self.policy_path.read_bytes()
        self.config_digest = None
        if self.config_path.is_file():
            self.config_digest = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        self.checked_root()
        from shared_platform.publication_autopilot import load_autopilot_policy
        from shared_platform.publication_rounds import load_round1_snapshot
        round1 = load_round1_snapshot(frozen['offer_id'], reports_root=self.reports_root)
        if round1['snapshot_digest'] != frozen['snapshot_digest']:
            raise ValueError('R2_PARENT_FROZEN_INPUT_CHANGED')
        policy = load_autopilot_policy(self.policy_path)
        if (policy['review_contract']['intermediate_human_approval_required'] is not False
                or policy['review_contract']['sole_human_gate'] != 'FINAL_MARKETPLACE_PUBLISH'):
            raise ValueError('R2_PARENT_POLICY_CONFLICT')
        context_type = _ReadOnlyPaidContext if read_only else _NativePaidContext
        self.paid_context = context_type(runtime=self, offer_id=frozen['offer_id'], round1=round1,
                                        policy=policy, reports_root=self.paid_reports_root)
        if read_only and not self.paid_context.path.is_file():
            raise PaidRequestBlocked('R2_ORIGINAL_PAID_JOURNAL_UNAVAILABLE')
        # Inventories the original product report/localized roots. No empty baseline
        # marker, private cap or new task identifier substitutes for this census.
        self.paid_context.ensure_ready()

    def checked_root(self):
        a = self.adapter
        if (a.profile.environment != 'stable' or not a.boundary.verified_for(
                a.engine, self.task, self.token, a.profile)):
            raise ValueError('R2_PARENT_LEASE_OR_HOLD_REQUIRED')
        with a.engine.transaction() as db:
            a.engine._lease(db,self.task['task_id'],self.token)
        from shared_platform import workbench_publication_native as native
        from shared_platform.operations_runtime import runtime_matches
        if not runtime_matches(a.profile):
            raise ValueError('R2_PARENT_SOURCE_CHANGED')
        native._server(a.profile)
        native._report_dir(a.profile,self.task['scope']['offer_id'])
        history_root, history_digest = a._history_binding()
        if (history_root,history_digest) != (self.history_root,self.history_digest):
            raise ValueError('R2_PAID_HISTORY_ROOT_BINDING_CHANGED')
        if native.read_frozen(self.task, a.profile) != self.frozen:
            raise ValueError('R2_PARENT_FROZEN_INPUT_CHANGED')
        a.boundary._check_leaf(self.policy_path)
        a.boundary._check_leaf(self.config_path)
        if self.policy_path.read_bytes() != self.policy_raw:
            raise ValueError('R2_PARENT_POLICY_CHANGED')
        if self.config_digest is not None and hashlib.sha256(self.config_path.read_bytes()).hexdigest() != self.config_digest:
            raise ValueError('R2_PARENT_PROVIDER_CONFIG_CHANGED')
        return self.root

    def client(self, *, timeout=180):
        self.checked_root()
        if self.config_digest is None:
            raise ValueError('R2_PROVIDER_CONFIG_UNAVAILABLE')
        from modules.sourcing.lingshi_client import LingshiClient
        client = LingshiClient.from_config(self.config_path, timeout=timeout)
        return _ReadOnlyClient(client) if self.read_only and type(self.paid_context) is _ReadOnlyPaidContext else client

    def retain_read_recovery_anchor(self):
        """Admission for another GET lease, never proof that R2 completed."""
        self.checked_root()
        rows, entries = self.paid_context._load()
        tasks = []
        for key, entry in entries.items():
            if entry['state'] in {'RESERVED','ATTEMPTED','UNKNOWN'}:
                return None
            if entry['purpose'] not in {'brand_image_generation','image_translation'}:
                continue
            raw = self.paid_context._raw(key)
            original = (raw or {}).get('data') or {}
            task_id = original.get('task_id')
            if type(task_id) is str and task_id.isdecimal():
                task_id = int(task_id)
            if type(task_id) is not int or task_id <= 0:
                return None
            tasks.append({'key':key,'task_id':task_id})
        if not tasks or not rows or self.paid_context.summary()['unknown']:
            return None
        anchor = {'task_id':self.task['task_id'], 'offer_id':self.frozen['offer_id'],
            'round1_snapshot_digest':self.frozen['snapshot_digest'],
            'history_binding_digest':self.history_digest,
            'journal_digest':rows[-1]['event_digest'], 'original_media_tasks':tasks}
        with self.adapter.engine.transaction() as db:
            row = self.adapter.engine._lease(db,self.task['task_id'],self.token)
            previous = json.loads(row['checkpoint_json']).get('image_attempt') or {}
            if previous.get('state') not in {'started','unknown'}:
                return None
            anchor['attempt_number'] = previous['number']
            self.adapter.engine._event(db,self.task['task_id'],'r2_paid_read_recovery_anchor',anchor)
        return anchor

    def _verify_completed_assets(self, producer, assets, *, phase):
        from modules.sourcing.image_generation_checkpoint import ImageCheckpoint, digest
        for row in assets:
            producer._validate_cached_asset(row, self.paid_context)
            if row.get('external_generation_count') == 0:
                continue  # Original producer has already checked its reuse plan/source.
            path = Path(row['checkpoint_path'])
            if path.parent != self.checkpoint_directory(self.frozen['offer_id'], phase):
                raise PaidRequestBlocked('R2_CONTINUATION_CHECKPOINT_ROOT_CHANGED')
            self.adapter.boundary._check_leaf(path)
            cp = ImageCheckpoint.from_path(path)
            self.adapter.boundary._check_leaf(cp.output_path)
            state = cp.read()
            receipt = state.get('receipt') or {}
            if (state['status'] != 'COMPLETED' or receipt.get('status') != 'COMPLETED'
                    or receipt.get('source_identity_complete') is not True
                    or receipt.get('request_digest') != cp.request_digest
                    or receipt.get('business_digest') != cp.business_digest
                    or receipt.get('task_id') != state['task_id']
                    or receipt.get('retry_attempt') != state['attempt']
                    or receipt.get('model') != cp.model
                    or receipt.get('output_digest') != row['artifact_digest']):
                raise PaidRequestBlocked('R2_CONTINUATION_ORIGINAL_RECEIPT_UNPROVEN')
            key = self.paid_context.image_key(cp, state, 'brand_image_generation' if phase == 'brand-image-checkpoints-lingshi' else 'image_translation')
            entry = self.paid_context.entry(key)
            raw = self.paid_context._raw(key)
            if (not entry or entry['state'] != 'CONFIRMED' or entry['binding'] != self.paid_context.binding
                    or entry.get('task_id') != state['task_id']
                    or entry.get('receipt_digest') != digest(receipt)
                    or not raw or str((raw.get('data') or {}).get('task_id')) != str(state['task_id'])):
                raise PaidRequestBlocked('R2_CONTINUATION_ORIGINAL_PAID_RECEIPT_UNPROVEN')

    def _continue_phase(self, producer, qa, generation, purpose):
        """Called only between actual original producers inside the parent hold."""
        if not self.read_only:
            return
        self.checked_root()
        if self.paid_context.summary()['unknown']:
            raise PaidRequestBlocked('R2_CONTINUATION_UNRESOLVED_PRODUCT_REQUEST')
        offer = self.frozen['offer_id']
        directory = self.reports_root / offer
        if (generation != producer._brand_generation_summary(offer, runtime=self)
                or generation.get('status') != 'BRAND_IMAGE_REVIEW_REQUIRED'
                or generation.get('round1_snapshot_digest') != self.frozen['snapshot_digest']):
            raise PaidRequestBlocked('R2_CONTINUATION_MASTER_PHASE_UNPROVEN')
        self._verify_completed_assets(producer, generation['assets'], phase='brand-image-checkpoints-lingshi')
        if purpose != 'image_quality_assurance':
            qa._passed_master_qa_digest(report_dir=directory, generation=generation, round1=self.paid_context.round1)
        if purpose == 'translation_quality_assurance':
            plan = json.loads((directory / 'brand-image-translation-plan.json').read_bytes())
            producer._validate_brand_translation_plan(offer, plan=plan, generation=generation)
            translated = json.loads((directory / 'brand-image-translation.json').read_bytes())
            if (translated.get('status') != 'LOCALIZED_IMAGE_REVIEW_REQUIRED'
                    or translated.get('generation_identity_digest') != plan['generation_identity_digest']
                    or len(translated.get('assets') or []) != len(plan['tasks'])):
                raise PaidRequestBlocked('R2_CONTINUATION_LOCALIZED_PHASE_UNPROVEN')
            self._verify_completed_assets(producer, translated['assets'], phase='brand-image-translation-checkpoints')
        if type(self.paid_context) is _ReadOnlyPaidContext:
            self.paid_context = _ContinuationPaidContext(runtime=self, offer_id=offer,
                round1=self.paid_context.round1, policy=self.paid_context.policy,
                reports_root=self.paid_reports_root)
        self.paid_context.allowed_purpose = purpose

    def generation_executor(self):
        self.checked_root()
        return _ParentImageExecutor()

    def current_master_after_localization(self, producer, qa, previous):
        """Reread the original producer's accounting update without rebinding assets."""
        self.checked_root()
        path = self.reports_root / self.frozen['offer_id'] / 'brand-image-generation.json'
        self.adapter.boundary._check_leaf(path)
        current = producer._brand_generation_summary(self.frozen['offer_id'], runtime=self)
        accounting_fields = {'paid_requests', 'completed_asset_generation_count', 'external_generation_count'}
        if (not isinstance(current, dict)
                or {k: v for k, v in current.items() if k not in accounting_fields}
                != {k: v for k, v in previous.items() if k not in accounting_fields}
                or current.get('paid_requests') != self.paid_context.summary()
                or current.get('completed_asset_generation_count') != sum(
                    int(row.get('external_generation_count') or 0) for row in current.get('assets') or [])
                or current.get('external_generation_count') != current['paid_requests']['new_this_invocation']):
            raise PaidRequestBlocked('R2_CONTINUATION_MASTER_REPORT_CHANGED')
        self._verify_completed_assets(producer, current['assets'], phase='brand-image-checkpoints-lingshi')
        qa._passed_master_qa_digest(report_dir=path.parent, generation=current, round1=self.paid_context.round1)
        self.checked_root()
        return current

    def translation_targets(self, offer_id, first_review):
        """Consume the original frozen technical review, not a draft status flag."""
        self.require_offer(offer_id)
        from shared_platform import publication_rounds, round1_workspace
        path = self.reports_root / self.frozen['offer_id'] / 'first-review.json'
        self.adapter.boundary._check_leaf(path)
        if json.loads(path.read_bytes()) != first_review:
            raise ValueError('R2_TRANSLATION_CURRENT_SOURCE_CHANGED')
        document = round1_workspace.read_preparation(offer_id, self.frozen['prepared_reference'])
        review = publication_rounds.validate_round1_reviewable(
            document['packet'], report_directory=path.parent)
        targets = list(review['target_selection']['requested'])
        if (review['offer_id'] != self.frozen['offer_id']
                or publication_rounds.canonical_digest(review) != self.paid_context.round1['first_review_digest']
                or targets != self.paid_context.round1['canonical_targets']
                or sorted((first_review.get('target_selection') or {}).get('requested') or []) != sorted(targets)):
            raise ValueError('R2_TRANSLATION_FROZEN_TARGETS_OR_SOURCE_CHANGED')
        # checked_root replays the original preparation/auto decision/snapshot
        # consumer, current source and actual lease again after these reads.
        self.checked_root()
        return [target for target in targets if target != 'miaoshou:COMMON']

    def qa_local_documents(self, generation, translation):
        """Project verified original checkpoint paths only for local native QA."""
        from copy import deepcopy
        from modules.sourcing.image_generation_checkpoint import ImageCheckpoint
        self.checked_root()
        directory = self.reports_root / self.frozen['offer_id']
        for name, document in (('brand-image-generation.json', generation),
                               ('brand-image-translation.json', translation)):
            path = directory / name
            self.adapter.boundary._check_leaf(path)
            if path.exists() and json.loads(path.read_bytes()) != document:
                raise ValueError('R2_QA_CURRENT_DOCUMENT_CHANGED')
        producer = self.adapter._script('prepare_product_images.py')
        projected = []
        for document, phase in ((generation, 'brand-image-checkpoints-lingshi'),
                                (translation, 'brand-image-translation-checkpoints')):
            self._verify_completed_assets(producer, document.get('assets') or [], phase=phase)
            value = deepcopy(document)
            for row in value.get('assets') or []:
                if row.get('external_generation_count') == 0:
                    continue
                cp = ImageCheckpoint.from_path(row['checkpoint_path'])
                if row.get('artifact_path') not in (None, str(cp.output_path)):
                    raise ValueError('R2_QA_ORIGINAL_ARTIFACT_PATH_CHANGED')
                self.adapter.boundary._check_leaf(cp.output_path)
                row['artifact_path'] = str(cp.output_path)
            projected.append(value)
        self.checked_root()
        return tuple(projected)

    def qa_model(self):
        self.checked_root()
        if self.config_digest is None:
            raise ValueError('R2_PROVIDER_CONFIG_UNAVAILABLE')
        value = json.loads(self.config_path.read_bytes()).get('image_qa_model')
        if not isinstance(value, str) or not value.strip():
            raise ValueError('R2_QA_MODEL_UNCONFIGURED')
        return value.strip()

    def checkpoint_directory(self, offer_id, phase):
        self.checked_root()
        if str(offer_id) != self.frozen['offer_id'] or phase not in {
                'brand-image-checkpoints-lingshi', 'brand-image-translation-checkpoints'}:
            raise ValueError('R2_ORIGINAL_CHECKPOINT_SCOPE_CHANGED')
        # Stable business/request identity and original task state stay beside
        # the one canonical product ledger when the source runtime changes.
        return self.paid_reports_root / str(offer_id) / phase

    def require_offer(self, offer_id):
        self.checked_root()
        if str(offer_id) != self.frozen['offer_id']:
            raise ValueError('R2_PARENT_OFFER_CHANGED')

    def retained_plan_path(self, current_path):
        self.checked_root()
        current_path = Path(current_path)
        if (current_path.parent != self.reports_root / self.frozen['offer_id']
                or current_path.name not in {'brand-image-reuse-plan.json', 'brand-image-translation-plan.json'}):
            raise ValueError('R2_RETAINED_PLAN_SCOPE_CHANGED')
        self.adapter.boundary._check_leaf(current_path)
        if current_path.exists():
            return current_path
        old_path = self.paid_context.directory / current_path.name
        self.adapter.boundary._check_leaf(old_path)
        # The original producer still validates this plan against current R1,
        # source bytes/generation identity, and the canonical paid plan binding.
        return old_path if old_path.exists() else current_path


class NativeParentImagesAdapter:
    def __init__(self, engine, profile, boundary):
        self.engine, self.profile, self.boundary = engine, profile, boundary

    def queue_retained_recovery(self, task):
        """Exact new POST plus an original service-captured media receipt event.

        This restores a GET lease only. Current files, paid history and stage
        completion are fully revalidated inside hold; no applied outcome is made.
        """
        if (task['task_id'] not in self.engine.explicit_new_post_task_ids()
                or task.get('version') != self.engine.release):
            return False
        with self.engine.transaction() as db:
            row = self.engine._row(db,task['task_id'])
            self.engine._require_version(row)
            checkpoint = json.loads(row['checkpoint_json'])
            previous = checkpoint.get('image_attempt') or {}
            anchor = (previous.get('result') or {}).get('read_recovery_anchor')
            steps = json.loads(row['steps_json'])
            if (row['state'] != 'reconciliation_required' or row['template'] != 'publication'
                    or steps[row['step_index']]['key'] != 'images' or row['external_started']
                    or row['worker'] or row['lease_token'] or row['action_json']
                    or json.loads(row['scope_json']) != task['scope']
                    or previous.get('state') != 'unknown' or type(anchor) is not dict
                    or anchor.get('task_id') != task['task_id']
                    or anchor.get('offer_id') != task['scope']['offer_id']
                    or anchor.get('attempt_number') != previous.get('number')
                    or not anchor.get('original_media_tasks')
                    or db.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',
                                  (task['task_id'],)).fetchone()):
                return False
            events = db.execute("SELECT detail_json FROM workbench_events WHERE task_id=? "
                "AND event_type='r2_paid_read_recovery_anchor' ORDER BY id DESC",
                (task['task_id'],)).fetchall()
            if not any(json.loads(event[0]) == anchor for event in events):
                return False
            reads = db.execute("SELECT detail_json,created_at FROM workbench_events WHERE task_id=? "
                "AND event_type='r2_original_media_read_recovery_queued' ORDER BY id",
                (task['task_id'],)).fetchall()
            reads = [event for event in reads
                     if json.loads(event['detail_json']).get('attempt_number') == previous['number']]
            # Durable per-original-attempt GET budget. Restart cannot reset the
            # count, interval or window and no history task is admitted here.
            now = datetime.now(timezone.utc)
            if reads:
                first = datetime.fromisoformat(reads[0]['created_at'].replace('Z','+00:00'))
                last = datetime.fromisoformat(reads[-1]['created_at'].replace('Z','+00:00'))
                if len(reads) >= 3 or (now-first).total_seconds() > 1800 or (now-last).total_seconds() < 5:
                    return False
            self.engine._state(db,task['task_id'],'queued','正在回读原图片任务，不重复提交生成请求')
            self.engine._event(db,task['task_id'],'r2_original_media_read_recovery_queued',
                {'attempt_number':previous['number'],'original_media_tasks':len(anchor['original_media_tasks']),
                 'read_cycle':len(reads)+1,'read_cycle_limit':3,'read_window_seconds':1800,
                 'paid_request_replayed':False})
        return True

    def _history_binding(self):
        """An installed service root binding, never a task/HTTP reset or zero marker."""
        path=Path(self.profile.root)/'config/product_publication_paid_history.local.json'
        try:
            self.boundary._check_leaf(path)
            raw=path.read_bytes()
            if len(raw)>32768:raise ValueError()
            def unique(pairs):
                result={}
                for key,value in pairs:
                    if key in result:raise ValueError()
                    result[key]=value
                return result
            value=json.loads(raw,object_pairs_hook=unique)
            if (type(value) is not dict or set(value)!={'schema_version','service_root','paid_history_root'}
                    or value['schema_version']!='product-paid-history-roots/v1'
                    or any(type(value[key]) is not str or not value[key] for key in ('service_root','paid_history_root'))
                    or PureWindowsPath(value['service_root'])!=PureWindowsPath(self.profile.root)):
                raise ValueError()
            supplied=PureWindowsPath(value['paid_history_root'])
            if (not supplied.is_absolute() or not re.fullmatch(r'[A-Za-z]:',supplied.drive)
                    or '..' in supplied.parts):
                raise ValueError()
            history_root=Path(supplied)
            # Never turn a missing historical install into an empty cap baseline.
            if not history_root.is_dir():raise ValueError()
        except (OSError,ValueError,TypeError,KeyError):
            raise PaidRequestBlocked('R2_PAID_HISTORY_ROOT_BINDING_REQUIRED') from None
        return history_root,hashlib.sha256(raw).hexdigest()

    def _script(self, name):
        path = Path(self.profile.root) / 'skills/prepare-product-images/scripts' / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('R2_PRODUCER_SOURCE_REDIRECTED')
        spec = importlib.util.spec_from_file_location('native_r2_' + name.replace('.', '_'), path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @contextmanager
    def _hold(self, task, token):
        from shared_platform import workbench_publication_native as native
        with self.boundary.hold(self.engine, task, token, self.profile):
            frozen = native.read_frozen(task, self.profile)
            if task['current_step'] != 'images' or frozen is None:
                raise ValueError('R2_EXACT_FROZEN_IMAGES_TASK_REQUIRED')
            root = Path(self.profile.root)
            offer = frozen['offer_id']
            config_paths=[root/'config',root/'skills/prepare-product-images/scripts']
            with self.boundary._pin(config_paths):
                history_root,history_digest=self._history_binding()
                history_paths=[history_root/'reports/product-preparation'/offer,
                               history_root/'data/localized_image_reviews'/offer,
                               history_root/'data/localized_image_packs'/offer]
                paths = [root / 'reports/product-preparation' / offer,
                     root / 'data/localized_image_reviews' / offer,
                     root / 'data/localized_image_packs' / offer,
                      root / 'outputs/image_suite_from_miaoshou' / offer]
                with self.boundary._pin(paths), self.boundary._pin(history_paths,release_parent=history_root), ExitStack() as pins:
                    for directory in set(paths+history_paths):
                        pending = [directory]
                        while pending:
                            current = pending.pop()
                            for path in current.iterdir():
                                info = path.lstat()
                                if getattr(info, 'st_file_attributes', 0) & 0x400 or path.is_symlink():
                                    raise ValueError('R2_PARENT_ARTIFACT_REDIRECTED')
                                if stat.S_ISDIR(info.st_mode):
                                    pins.enter_context(self.boundary._pin([path],release_parent=history_root))
                                    pending.append(path)
                                else:
                                    self.boundary._check_leaf(path)
                    for path in (root / 'config/product_publication_autopilot_policy.json',
                                 root / 'config/lingshi.local.json'):
                        self.boundary._check_leaf(path)
                    yield frozen,history_root,history_digest

    def execute_images(self, task, output_dir, notes, *, token):
        return self._execute(task, token, read_only=False)

    def recover_images(self, task, *, token):
        return self._execute(task, token, read_only=True)

    def _execute(self, task, token, *, read_only):
        try:
            return self._produce(task, token, read_only=read_only)
        except (PaidRequestBlocked, ValueError, OSError) as error:
            # Never expose private config, signed URLs or raw provider text.
            reason = re.match(r'[A-Z][A-Z0-9_]{2,}', str(error))
            return {'status': 'unknown' if read_only else 'blocked',
                    'reason': reason.group(0) if reason else 'R2_PROVIDER_OR_SOURCE_UNPROVEN',
                    'error_class': type(error).__name__, 'phase': 'R2_NATIVE_PARENT'}

    def _produce(self, task, token, *, read_only):
        from shared_platform import workbench_publication_native as native
        with self._hold(task, token) as (frozen,history_root,history_digest):
            runtime = NativeImageRuntime(self, task, token, frozen,history_root=history_root,
                                         history_digest=history_digest,read_only=read_only)
            producer = self._script('prepare_product_images.py')
            qa = self._script('run_automated_image_qa.py')
            directory = runtime.reports_root / frozen['offer_id']
            pending_original_task = False
            stage = 'MASTER_GENERATION'
            try:
                producer.run(Namespace(offer_id=frozen['offer_id'], execute_brand_generation=True,
                                       paid_policy=runtime.policy_path), runtime=runtime)
                generation = producer._brand_generation_summary(frozen['offer_id'], runtime=runtime)
                if not generation or generation.get('status') != 'BRAND_IMAGE_REVIEW_REQUIRED':
                    pending_original_task = bool(generation and generation.get('status') == 'PROVIDER_RECONCILIATION_REQUIRED')
                    raise PaidRequestBlocked('R2_MASTER_GENERATION_INCOMPLETE')
                runtime._continue_phase(producer, qa, generation, 'image_quality_assurance')
                stage = 'MASTER_QA'
                qa_result = qa.run(Namespace(offer_id=frozen['offer_id'], model=runtime.qa_model(),
                    assessment=None, paid_policy=runtime.policy_path, verified_local_assets=True), runtime=runtime)
                if qa_result['status'] != 'PASSED':
                    raise ValueError('R2_MASTER_QA_NOT_PASSED')
                runtime._continue_phase(producer, qa, generation, 'image_translation')
                stage = 'TRANSLATION_SCOPE_FREEZE'
                # The existing local OCR and translation predicate select only
                # actual translatable prose. Empty/invariant text remains reused.
                from modules.sourcing.localized_image_ocr import detect_english_text_regions
                from modules.sourcing.localized_image_auto_translation import _requires_translation
                targets = [target for target in runtime.paid_context.round1['canonical_targets']
                           if target != 'miaoshou:COMMON']
                numbers = []
                for row in generation['assets']:
                    if not any(producer.TARGET_LOCALE.get(target) in producer._brand_translation_locales(row['brand_id'])
                            and producer.TARGET_LOCALE.get(target) not in {'en-PH','en-GB'}
                            and ('homebloom-sea' if target.startswith('tiktok:HB_') else 'livelyhive-sea') == producer._brand_family(row['brand_id'])
                            for target in targets):
                        continue
                    runtime.checked_root()
                    raw=producer._download_source(row['public_url'])
                    producer._verify_asset_bytes(raw,row['artifact_digest'])
                    regions=detect_english_text_regions(raw)
                    if any(_requires_translation(region['source_text']) for region in regions):
                        numbers.append(row['review_number'])
                producer.freeze_conversation_approved_translation_scope(frozen['offer_id'],
                    generation=generation, selected_review_numbers=numbers, dimension_only_numbers=[],
                    approved_by='orbit-product-publication-default-v1', runtime=runtime)
                stage = 'LOCALIZED_GENERATION'
                producer.run(Namespace(offer_id=frozen['offer_id'], execute_paid=True,
                                       paid_policy=runtime.policy_path), runtime=runtime)
                generation = runtime.current_master_after_localization(producer, qa, generation)
                runtime._continue_phase(producer, qa, generation, 'translation_quality_assurance')
                stage = 'LOCALIZED_QA'
                qa_result = qa.run(Namespace(offer_id=frozen['offer_id'], model=runtime.qa_model(),
                    assessment=None, paid_policy=runtime.policy_path, verified_local_assets=True), runtime=runtime)
                if qa_result['status'] != 'PASSED':
                    raise ValueError('R2_LOCALIZED_QA_NOT_PASSED')
                stage = 'R2_DOCUMENTS_READBACK'
                receipt = native.read_images(task, self.profile)
                return {'status': 'prepared', 'result': {'missing_inputs': [],
                    'summary': '商品图片与本地化校验已完成', 'evidence_paths': [str(directory)]},
                    'native_r2': receipt['native_r2']}
            except (PaidRequestBlocked, ValueError, OSError) as error:
                result = {'status': 'unknown' if read_only or pending_original_task or runtime.paid_context.summary()['unknown'] else 'blocked',
                         'reason': (re.match(r'[A-Z][A-Z0-9_]{2,}',str(error)).group(0)
                                    if re.match(r'[A-Z][A-Z0-9_]{2,}',str(error)) else 'R2_PROVIDER_OR_SOURCE_UNPROVEN'),
                         'error_class':type(error).__name__, 'phase': 'R2_NATIVE_PARENT',
                         'failed_stage':stage}
                if result['status'] == 'unknown':
                    anchor = runtime.retain_read_recovery_anchor()
                    if anchor is not None:
                        result['read_recovery_anchor'] = anchor
                return result
