"""Separate startup scope for one retained task; no new-task/historical worker."""
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.original_prepared_continuation import (
    SourceRole, RetainedFile, OriginalContinuationPins, OriginalPreparedTaskContinuation,
)
from shared_platform.prepared_target_consumer import PreparedTargetConsumerPins, PreparedTargetReductionConsumer

SCOPE = 'original-prepared-task-continuation/v1'
PREPARATION_POST_PATHS = frozenset({
    '/api/product-workspace/r3-common/preview',
    '/api/product-workspace/miaoshou-draft/commit',
    '/api/product-workspace/r3-marketplace/preview',
})


def admit_preparation_request(http, path, data):
    """Delegate one installed original task, never grant domain write authority."""
    from modules.products import server
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.original_prepared_continuation import configured_original_continuation
    from shared_platform.prepared_target_consumer import configured_target_consumer
    final = getattr(http, 'native_final_review', None)
    capability = getattr(http, 'native_service_capability', None) or {}
    if (path not in PREPARATION_POST_PATHS or type(data) is not dict
            or type(final) is not NativeSoleFinalService
            or final is not server._NATIVE_FINAL_SERVICE or final._closed or final._stopping):
        raise ValueError('ORIGINAL_PREPARATION_SERVICE_NOT_INSTALLED')
    operations = final._operations
    binding = getattr(operations, 'original_continuation', None)
    if (type(binding) is not OriginalPreparedTaskContinuation
            or binding.pins.offer_id != '4039931504'
            or binding.pins.task_id != 'TASK-20261006-001'):
        raise ValueError('ORIGINAL_PREPARATION_CONTINUATION_REQUIRED')
    offer = data.get('offer_id')
    if type(offer) is not str or offer != binding.pins.offer_id:
        raise ValueError('ORIGINAL_PREPARATION_OFFER_CHANGED')
    if (capability.get('status') != 'REGISTERED' or capability.get('scope') != SCOPE
            or capability.get('original_task_id') != binding.pins.task_id
            or capability.get('offer_id') != offer
            or capability.get('original_task_release') != binding.engine.release
            or capability.get('original_executor_only') is not True
            or capability.get('new_task_worker_enabled') is not False
            or capability.get('historical_task_scan') is not False
            or operations.engine is not binding.engine
            or operations.profile is not binding.original_profile
            or configured_original_continuation(offer) is not binding):
        raise ValueError('ORIGINAL_PREPARATION_STARTUP_BINDING_CHANGED')
    consumer = configured_target_consumer(offer)
    if (type(consumer) is not PreparedTargetReductionConsumer
            or consumer.engine is not binding.engine
            or consumer.pins.task_id != binding.pins.task_id
            or consumer.pins.prepared_reference != binding.pins.prepared_reference
            or consumer.service_root.resolve() != binding.service_profile.root.resolve()):
        raise ValueError('ORIGINAL_PREPARATION_TARGET_CONSUMER_CHANGED')
    if getattr(binding, 'image_successor', None) is not None and path != '/api/product-workspace/r3-marketplace/preview':
        raise ValueError('ORIGINAL_IMAGES_SUCCESSOR_COMMON_READONLY_BASELINE_ONLY')
    binding.read_documents()  # Existing full retained R1/R2 byte/domain check.
    record = consumer.read_verified(offer, r2_identity=json.loads(binding.pins.r2_identity_json))
    effective = (record.get('reinstatement') or record)['effective_scope']
    if not effective.get('shops'):
        raise ValueError('ORIGINAL_PREPARATION_EFFECTIVE_SCOPE_UNAVAILABLE')
    expected = {'offer_id'} if path.endswith('/r3-marketplace/preview') else {
        'offer_id', 'release_stage', 'publication_targets'}
    if path.endswith('/miaoshou-draft/commit'):
        expected |= {'plan_id', 'confirmation_token', 'confirm_miaoshou_write'}
    if (set(data) != expected or (path != '/api/product-workspace/r3-marketplace/preview'
            and (data.get('release_stage') != 'R3_COMMON'
                 or data.get('publication_targets') != ['miaoshou:COMMON']))):
        raise ValueError('ORIGINAL_PREPARATION_REQUEST_SCOPE_CHANGED')
    if path.endswith('/miaoshou-draft/commit'):
        if (data.get('confirm_miaoshou_write') is not True
                or type(data.get('plan_id')) is not str or not data['plan_id']
                or type(data.get('confirmation_token')) is not str or not data['confirmation_token']):
            raise ValueError('ORIGINAL_PREPARATION_COMMON_PLAN_REQUIRED')
        plan = binding.release_store.get_plan(data['plan_id'])
        payload = (plan or {}).get('payload') or {}
        stage = payload.get('r3_stage_binding') or {}
        source = stage.get('native_preparation_source') or {}
        identity = json.loads(binding.pins.r2_identity_json)
        if (not plan or plan.get('product_id') != offer
                or plan.get('confirmation_token') != data['confirmation_token']
                or payload.get('targets') != ['miaoshou:COMMON']
                or type(source) is not dict or source.get('offer_id') != offer
                or source.get('prepared_reference') != binding.pins.prepared_reference
                or source.get('round1_snapshot_digest') != identity['round1_snapshot_digest']
                or source.get('targets') != record['original_scope']['shops']
                or stage.get('r2_identity') != identity):
            raise ValueError('ORIGINAL_PREPARATION_COMMON_PLAN_CHANGED')
        consumer.validate_payload(payload, target_labels=['miaoshou:COMMON'])
    return data


def load_binding(reference, *, service_root, operations_path, release_path, catalog_path):
    from shared_platform.immutable_approval_files import require_local_path
    if type(reference) is not dict or set(reference) != {'path', 'sha256'}:
        raise ValueError('ORIGINAL_SERVICE_CONFIG_REFERENCE_REQUIRED')
    path = Path(reference['path'])
    require_local_path(path, root=path.parent)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('ORIGINAL_SERVICE_CONFIG_DIGEST_CHANGED')
    config = json.loads(raw)
    keys = {'schema', 'task_id', 'offer_id', 'original_worker', 'prepared_reference', 'r2_identity',
            'quiescence_event_id', 'quiescence_detail_sha256', 'roles', 'documents', 'original_state',
            'target_request_digest', 'target_reduction_digest'}
    optional = {'target_reinstatement_digest', 'image_successor_ref'}
    if type(config) is not dict or not keys <= set(config) or set(config) - keys - optional or config['schema'] != SCOPE:
        raise ValueError('ORIGINAL_SERVICE_CONFIG_SHAPE_INVALID')
    roles = tuple(SourceRole(value['name'], RuntimeProfile(Path(value['root']), Path(value['data_root']),
        value['environment'], value['head'], value['manifest_digest'])) for value in config['roles'])
    pins = OriginalContinuationPins(config['task_id'], config['offer_id'], config['original_worker'],
        config['prepared_reference'], json.dumps(config['r2_identity']), config['quiescence_event_id'],
        config['quiescence_detail_sha256'], roles,
        tuple(RetainedFile(Path(value['path']), value['sha256']) for value in config['documents']),
        RetainedFile(Path(config['original_state']['path']), config['original_state']['sha256']))
    original = roles[0].profile
    engine = WorkbenchEngine(Path(operations_path), {'code_version': original.version,
        'environment': original.environment, 'manifest_digest': original.manifest_digest})
    binding = OriginalPreparedTaskContinuation(engine, pins, release_store_path=release_path, catalog_path=catalog_path)
    if (binding.service_profile.root.resolve() != Path(service_root).resolve()
            or binding.engine.store.path.resolve() != Path(operations_path).resolve()):
        raise ValueError('ORIGINAL_SERVICE_CURRENT_ROOT_OR_LEDGER_CHANGED')
    image_successor = None
    if 'image_successor_ref' in config:
        from shared_platform.original_images_successor import OriginalImagesSuccessor
        image_successor = OriginalImagesSuccessor(config['image_successor_ref'], binding)
        binding.image_successor = image_successor
    target_identity = (json.dumps(image_successor.old_identity) if image_successor else pins.r2_identity_json)
    consumer = PreparedTargetReductionConsumer(engine, PreparedTargetConsumerPins(pins.task_id, pins.offer_id,
        config['target_reduction_digest'], config['target_request_digest'], json.dumps(engine.release),
        pins.prepared_reference, target_identity, pins.original_worker,
        config.get('target_reinstatement_digest')), service_root=service_root)
    if image_successor is not None:
        consumer.image_successor = image_successor
    return binding, consumer


@contextmanager
def installed_original_service(application, http, config, paths):
    from shared_platform import original_prepared_continuation as retained
    from shared_platform import prepared_target_consumer as targets
    from shared_platform import local_operator_session as owner
    from shared_platform.native_common_edit_boundary import install_service_boundary, restore_service_boundary
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.native_sole_final_decision import check_schema
    from shared_platform.native_common_technical_execution import _schema_installed
    from shared_platform.native_windows_actor import NativeActorServiceConfig
    from shared_platform.native_service_lifecycle import _RETIREMENT_UNKNOWN
    binding, consumer = load_binding(config['original_continuation'], service_root=application.ROOT,
        operations_path=paths['operations_data_root'] / 'tasks.db', release_path=paths['release_store_path'],
        catalog_path=paths['catalog_database'])
    if _RETIREMENT_UNKNOWN:
        raise ValueError('NATIVE_SERVICE_RETIREMENT_UNKNOWN')
    # This permission is an explicit coordinator-sealed startup decision. Never
    # inferred from source repair, an HTTP request or an existing directory.
    if config.get('native_ephemeral_state_creation_authorized') is not True:
        raise ValueError('ORIGINAL_NATIVE_EPHEMERAL_STATE_CREATION_NOT_AUTHORIZED')
    root = paths['operations_data_root'] / 'native-final-owner'
    if not root.is_dir():
        raise ValueError('ORIGINAL_NATIVE_EXISTING_ROOT_UNAVAILABLE')
    owner.verify_owner_only(root, owner.current_windows_owner_sid())
    with binding.release_store._connect_readonly() as db:
        db.execute('BEGIN')
        if not _schema_installed(db): raise ValueError('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
        check_schema(db)
    old_retained, old_targets = retained._INSTALLED, targets._INSTALLED
    fields = ('_COMMON_STANDING_POLICY_READER', '_COMMON_DETAIL_OBSERVER_FACTORY', '_COMMON_SIGNING_CONTEXT_CONFIG', '_NATIVE_FINAL_SERVICE')
    previous = {name: getattr(application, name) for name in fields}
    def restore():
        retained._INSTALLED, targets._INSTALLED = old_retained, old_targets
        for name, value in previous.items(): setattr(application, name, value)
    def close(final):
        try: final.close()
        except BaseException:
            _RETIREMENT_UNKNOWN.append(final)
            http.native_service_retirement_unknown = final
            raise
    with ExitStack() as stack:
        stack.callback(restore)
        retained.install_original_continuation(binding)
        targets.install_prepared_target_consumer(consumer)
        startup = application.R3_STARTUP_CONFIG
        application._install_service_common_standing_policy(startup)
        application._install_service_common_detail_observer(startup)
        application._install_service_common_signing_context(startup)
        old_boundary, boundary = install_service_boundary(startup)
        stack.callback(restore_service_boundary, old_boundary, boundary)
        operations = SimpleNamespace(engine=binding.engine, profile=binding.original_profile,
                                     worker_enabled=False, original_continuation=binding)
        final = NativeSoleFinalService(binding.release_store, NativeActorServiceConfig(root, 'Kyle'),
            operations=operations, new_decision_execution_enabled=True)
        stack.callback(close, final)
        final.start()
        http.native_final_review = final
        application._NATIVE_FINAL_SERVICE = final
        yield {'scope': SCOPE, 'final': final, 'binding': binding, 'target_consumer': consumer,
               'worker': None, 'historical_task_scan': False, 'original_executor_only': True}
