"""Native Product Center preparation and R1 observation; no approval authority.

The original Product Center owns prepare/freeze and all marketplace actions.
Callbacks for later stages must use their domain contracts, never task status.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class NativeR1ReconciliationRequired(ValueError):
    """The task may observe this R1 lineage, but cannot adopt or rewrite it."""


def _server(profile):
    from modules.products import server
    if Path(server.ROOT).resolve() != Path(profile.root).resolve():
        raise ValueError('NATIVE_PUBLICATION_ROOT_CONFLICT')
    return server


def _scope(task):
    scope = task['scope']
    offer, targets = scope.get('offer_id'), scope.get('shops') or []
    if not isinstance(offer, str) or not re.fullmatch(r'[0-9]{1,32}', offer):
        raise ValueError('NATIVE_OFFER_REQUIRED')
    from shared_platform.publication_rounds import CANONICAL_TIKTOK_TO_WORKBENCH
    allowed = set(CANONICAL_TIKTOK_TO_WORKBENCH) | {'shopee:' + site for site in ('MY', 'TH', 'VN', 'PH')} | {'ozon:RU', 'miaoshou:COMMON'}
    if (not targets or any(not isinstance(target, str) or target not in allowed for target in targets)
            or not set(targets) - {'miaoshou:COMMON'}):
        raise ValueError('NATIVE_EXACT_TARGETS_REQUIRED')
    return offer, sorted(targets)


def _report_dir(profile, offer):
    from shared_platform import publication_rounds
    expected = Path(profile.root) / 'reports/product-preparation' / offer
    actual = publication_rounds.report_dir(offer)
    if actual.resolve() != expected.resolve() or actual.is_symlink():
        raise ValueError('NATIVE_REPORT_ROOT_CONFLICT')
    return actual


def _task_owns_snapshot(task, profile, *, offer, targets, prepared_reference, snapshot_digest, approved_at):
    """Require the original task's durable event, not copyable task JSON."""
    from shared_platform.immutable_approval_files import require_local_path

    release = {'code_version': getattr(profile, 'version', None),
               'environment': getattr(profile, 'environment', None),
               'manifest_digest': getattr(profile, 'manifest_digest', None)}
    if (not all(isinstance(value, str) for value in release.values())
            or not isinstance(task.get('task_id'), str) or not task['task_id']
            or task.get('version') != release or not prepared_reference
            or not getattr(profile, 'data_root', None)):
        raise NativeR1ReconciliationRequired('NATIVE_R1_TASK_RELEASE_OR_REFERENCE_REQUIRED')
    database = Path(profile.data_root) / 'tasks.db'
    try:
        approved_time = datetime.fromisoformat(approved_at.replace('Z', '+00:00'))
        if approved_time.tzinfo is None:
            raise ValueError('approval time has no timezone')
        approved_time = approved_time.astimezone(timezone.utc)
        require_local_path(database, root=profile.data_root)
        if not database.is_file():
            raise OSError('task ledger unavailable')
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')  # one read snapshot for task state and owner events
            row = conn.execute('SELECT scope_json,version_json,checkpoint_json,action_json,steps_json '
                               'FROM workbench_execution WHERE task_id=? AND template=?',
                               (task['task_id'], 'publication')).fetchone()
            if row is None:
                raise NativeR1ReconciliationRequired('NATIVE_R1_TASK_ORIGIN_UNAVAILABLE')
            scope = json.loads(row['scope_json'])
            version = json.loads(row['version_json'])
            checkpoint = json.loads(row['checkpoint_json'])
            action = json.loads(row['action_json']) if row['action_json'] else None
            steps = json.loads(row['steps_json'])
            if (scope != task.get('scope') or version != release
                    or checkpoint != (task.get('checkpoint') or {})
                    or action != (task.get('required_action') or task.get('pending_observation'))
                    or scope.get('offer_id') != offer
                    or sorted(scope.get('shops') or []) != targets):
                raise NativeR1ReconciliationRequired('NATIVE_R1_TASK_LEDGER_CONFLICT')
            owner = None
            for event in conn.execute(
                    'SELECT e.task_id,e.detail_json,e.created_at,x.scope_json FROM workbench_events e '
                    'JOIN workbench_execution x ON x.task_id=e.task_id '
                    "WHERE e.event_type='checkpoint_saved' AND x.template='publication' ORDER BY e.id"):
                event_scope = json.loads(event['scope_json'])
                event_checkpoint = json.loads(event['detail_json']).get('checkpoint') or {}
                if (event_scope.get('offer_id') == offer and
                        (event_checkpoint.get('native_preparation') or {}).get('prepared_reference') == prepared_reference
                        and datetime.fromisoformat(event['created_at'].replace('Z', '+00:00')).astimezone(timezone.utc) <= approved_time):
                    owner = event['task_id']
                    break
            if owner != task['task_id']:
                raise NativeR1ReconciliationRequired('NATIVE_R1_ORIGINAL_TASK_OWNER_CONFLICT')
            if action is not None:
                evidence = conn.execute('SELECT detail_json FROM workbench_events '
                    "WHERE task_id=? AND event_type IN ('user_action_required','domain_observation_pending') ",
                    (task['task_id'],)).fetchall()
                if not any(json.loads(event['detail_json']) == action for event in evidence):
                    raise NativeR1ReconciliationRequired('NATIVE_R1_TASK_ACTION_ORIGIN_CONFLICT')
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as error:
        if isinstance(error, NativeR1ReconciliationRequired):
            raise
        raise NativeR1ReconciliationRequired('NATIVE_R1_TASK_LEDGER_UNAVAILABLE') from error
    prepared = (checkpoint.get('native_preparation') or {}).get('prepared_reference')
    if prepared is not None:
        if prepared != prepared_reference:
            raise NativeR1ReconciliationRequired('NATIVE_R1_TASK_PREPARATION_CONFLICT')
        binding = (action or {}).get('receipt_binding') or {}
        if action and (binding.get('adapter') != 'publication-native/v1'
                       or binding.get('step') != 'facts'
                       or binding.get('offer_id') != offer
                       or sorted(binding.get('targets') or []) != targets
                       or binding.get('prepared_reference', prepared) != prepared):
            raise NativeR1ReconciliationRequired('NATIVE_R1_TASK_ACTION_CONFLICT')
        return
    facts = next(((step.get('checkpoint') or {}).get('native_r1') for step in steps
                  if step.get('key') == 'facts' and step.get('state') == 'completed'), None)
    if (not isinstance(facts, dict) or facts.get('offer_id') != offer
            or facts.get('targets') != targets
            or facts.get('prepared_reference') != prepared_reference
            or facts.get('snapshot_digest') != snapshot_digest):
        raise NativeR1ReconciliationRequired('NATIVE_R1_TASK_BINDING_REQUIRED')


def read_frozen(task, profile):
    """Return only the actual still-current domain snapshot, or no approval."""
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform import publication_rounds, round1_workspace
    offer, targets = _scope(task)
    server = _server(profile)
    directory = _report_dir(profile, offer)
    if not (directory / 'round1-approved-snapshot.json').exists():
        return None
    state = workbench.load_state(offer)
    snapshot = publication_rounds.validate_round2_input(offer, state)
    if snapshot.get('status') != 'APPROVED' or snapshot.get('offer_id') != offer or sorted(snapshot.get('canonical_targets') or []) != targets:
        raise ValueError('NATIVE_R1_SCOPE_CONFLICT')
    ref = (state.get('product_approval') or {}).get('round1_prepared_reference')
    if ref:
        status = round1_workspace.status(server, offer, reference=ref)
        if status.get('status') != 'FROZEN' or status.get('snapshot') != snapshot:
            raise ValueError('NATIVE_R1_FREEZE_INCOMPLETE')
    _task_owns_snapshot(task, profile, offer=offer, targets=targets,
                        prepared_reference=ref, snapshot_digest=snapshot['snapshot_digest'],
                        approved_at=snapshot['approved_at'])
    previous = (task.get('checkpoint') or {}).get('native_r1')
    if not previous:
        previous = next(((step.get('checkpoint') or {}).get('native_r1') for step in task.get('steps', [])
                         if step.get('key') == 'facts'), None)
    if previous and previous['snapshot_digest'] != snapshot['snapshot_digest']:
        raise ValueError('NATIVE_R1_SUPERSEDED')
    from shared_platform.internal_catalog_sku import internal_sku
    seller = str(((snapshot.get('fact_snapshot') or {}).get('product_facts') or {}).get('seller_sku') or '')
    canonical = internal_sku(seller)
    if not re.fullmatch(r'[0-9]{4}', canonical):
        raise ValueError('NATIVE_INTERNAL_SKU_REQUIRED')
    return {'offer_id': offer, 'targets': targets, 'snapshot_digest': snapshot['snapshot_digest'],
            'prepared_reference': ref, 'internal_sku': canonical, 'seller_sku': seller}


def prepare_facts(task, profile, *, prepare_review=True):
    """Run the existing read/local-only Skill and persist its review packet."""
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform.release_control import build_release_dashboard
    offer, _ = _scope(task)
    server = _server(profile)
    # Bootstrap takes the non-reentrant product lock itself. Run it before our
    # atomic preparation section, then re-read everything under both locks.
    state = workbench.load_state(offer)
    if not int(state.get('_revision') or 0):
        status, result = server._collect_product_workspace_locally({'offer_id': offer})
        if status not in {200, 201} or not result.get('ok'):
            raise ValueError('NATIVE_BOOTSTRAP_FAILED:' + str(result.get('error') or status))
    with server._product_workbench_lock(offer), workbench._state_write_lock(offer):
        result = _prepare_facts_locked(task, profile, preview_builder=lambda value: build_release_dashboard(offer_id=value))
    return _prepare_workspace_review(task, profile, result) if prepare_review else result


def _prepare_workspace_review(task, profile, result):
    """Reuse one exact official observation, never manufacture a reference."""
    from shared_platform import round1_workspace
    server = _server(profile)
    offer, targets = _scope(task)
    packet = json.loads(Path(result['review_path']).read_text(encoding='utf-8'))
    region = (packet.get('category_review_context') or {}).get('source_region')
    if region not in {'MY', 'TH', 'VN', 'PH'}:
        return {**result, 'preparation_blocker': 'R1_CATEGORY_SOURCE_REGION_REQUIRED'}
    data = {'offer_id': offer, 'product_center_revision': result['product_center_revision'],
            'requested_targets': targets, 'source_region': region}
    context = server._round1_category_context(data)
    account = context['source_account']
    candidates = [row for row in context['observations'] if row.get('status') == 'REUSABLE'
                  and row.get('account_identity_digest') == account.get('account_identity_digest')]
    if account.get('readiness') != 'READY' or len(candidates) != 1:
        return {**result, 'preparation_blocker': 'R1_EXACT_OFFICIAL_CATEGORY_OBSERVATION_REQUIRED'}
    data.update(account_identity_digest=account['account_identity_digest'], context_digest=context['context_digest'],
                observer_reference=candidates[0]['observer_reference'])
    data['request_id'] = 'native-' + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    prepared = round1_workspace.prepare(server, data)
    return {**result, 'prepared_reference': prepared['prepared_reference'],
            'round1_prepared_review': prepared}


def _prepare_facts_locked(task, profile, *, preview_builder):
    from shared_platform import round1_workspace
    from shared_platform import publication_rounds
    from modules.sourcing import new_product_workbench as workbench
    offer, targets = _scope(task)
    server = _server(profile)
    directory = _report_dir(profile, offer)
    path = directory / 'first-review.json'
    if path.is_symlink():
        raise ValueError('NATIVE_REVIEW_SYMLINK')
    previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if previous and previous.get('offer_id') != offer:
        raise ValueError('NATIVE_REVIEW_OFFER_CONFLICT')
    # Never overwrite the input of an existing approved snapshot.
    if (directory / 'round1-approved-snapshot.json').exists():
        raise ValueError('NATIVE_APPROVED_REVIEW_IMMUTABLE')
    decision_path = directory / 'round1-auto-decision.json'
    approval = workbench.load_state(offer).get('product_approval') or {}
    if (decision_path.is_symlink() or decision_path.exists()
            or approval.get('approved_by') == publication_rounds.AUTOPILOT_ACTOR):
        # The domain owns completing/reconciling this exact old decision. A
        # second preparation could overwrite its immutable first-review input.
        raise NativeR1ReconciliationRequired('NATIVE_R1_TECHNICAL_FREEZE_REQUIRES_RECONCILIATION')
    module = round1_workspace._module(server)
    region = (previous.get('category_review_context') or {}).get('source_region')
    if region not in {'MY', 'TH', 'VN', 'PH'}:
        # This chooses only a read source among requested markets; it cannot
        # add a publication target or authorize a category selection.
        regions = sorted({target.rsplit('_', 1)[-1].rsplit(':', 1)[-1] for target in targets}
                         & {'MY', 'TH', 'VN', 'PH'})
        region = regions[0] if regions else None
    options = {'category_source_region': region} if region else {}
    for key, filename in [('image_execution_plan', 'first-review-image-plan.json'), ('candidate_plan', 'first-review-candidate-plan.json')]:
        sidecar = directory / filename
        if sidecar.is_symlink():
            raise ValueError('NATIVE_PLAN_SYMLINK')
        value = json.loads(sidecar.read_text(encoding='utf-8')) if sidecar.exists() else previous.get(key)
        if key == 'image_execution_plan' and not sidecar.exists() and isinstance(value, dict) and value.get('status') == 'USER_DECISION_REQUIRED':
            value = None  # Producer's missing-plan placeholder is not a proposed plan.
        if value is not None:
            options[key] = value
    packet = module.prepare_offer(offer_id=offer, requested_targets=targets, preview_builder=preview_builder, **options)
    if packet.get('offer_id') != offer or sorted((packet.get('target_selection') or {}).get('requested') or []) != targets:
        raise ValueError('NATIVE_PREPARATION_SCOPE_CONFLICT')
    directory.mkdir(parents=True, exist_ok=True)
    module._write_text_atomic(path, json.dumps(packet, ensure_ascii=False, indent=2) + '\n')
    return {'offer_id': offer, 'targets': targets, 'review_path': str(path),
            'product_center_revision': packet.get('product_center_revision'), 'status': packet.get('status')}


class NativeR2PreparedReviewReader:
    """Re-read the actual task-owned R1 preparation; never accept approval JSON."""

    def __init__(self, task, profile):
        self.task, self.profile = task, profile

    def read_verified(self, offer_id, reports_root, current_review, snapshot):
        from shared_platform import publication_rounds, round1_workspace
        from shared_platform.immutable_approval_files import require_local_path
        frozen = read_frozen(self.task, self.profile)
        root = Path(self.profile.root) / 'reports/product-preparation'
        if (not frozen or frozen['offer_id'] != offer_id
                or Path(reports_root).resolve() != root.resolve()
                or snapshot['snapshot_digest'] != frozen['snapshot_digest']):
            raise ValueError('NATIVE_R2_PREPARED_SOURCE_CONFLICT')
        path = root / offer_id / 'first-review.json'
        require_local_path(path, root=root)
        if json.loads(path.read_bytes()) != current_review:
            raise ValueError('NATIVE_R2_CURRENT_REVIEW_CHANGED')
        document = round1_workspace.read_preparation(offer_id, frozen['prepared_reference'])
        review = publication_rounds.validate_round1_reviewable(document['packet'], report_directory=path.parent)
        if (publication_rounds.canonical_digest(review) != snapshot['first_review_digest']
                or review['offer_id'] != offer_id
                or review['target_selection']['requested'] != snapshot['canonical_targets']
                or sorted((current_review.get('target_selection') or {}).get('requested') or []) != frozen['targets']
                or read_frozen(self.task, self.profile) != frozen):
            raise ValueError('NATIVE_R2_PREPARED_REVIEW_CHANGED')
        return review


def load_service_r2_documents(offer_id, operations=None):
    """Resolve the original preparation owner from trusted startup, never HTTP.

    Products without a native origin retain the original raw-document contract.
    Once an origin exists, any owner/version/source failure is terminal here.
    This reads a precise prepared reference, not a dashboard or runnable queue.
    """
    from shared_platform.publication_r3_image_bridge import load_r2_documents
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform import publication_rounds as rounds, round1_workspace
    if operations is None:
        state = workbench.load_state(offer_id)
        snapshot = rounds.validate_round2_input(offer_id, state)
    else:
        from types import SimpleNamespace
        from shared_platform.operations_runtime import RuntimeProfile
        from shared_platform.workbench_engine import WorkbenchEngine
        if type(operations) is not SimpleNamespace:
            raise ValueError('NATIVE_R2_SERVICE_RUNTIME_REQUIRED')
        engine, profile = operations.engine, operations.profile
        if type(engine) is not WorkbenchEngine or type(profile) is not RuntimeProfile:
            raise ValueError('NATIVE_R2_SERVICE_RUNTIME_REQUIRED')
        _server(profile)
        if (engine.store.path.resolve() != (profile.data_root / 'tasks.db').resolve()
                or engine.release != {'code_version': profile.version, 'environment': profile.environment,
                                      'manifest_digest': profile.manifest_digest}):
            raise ValueError('NATIVE_R2_SERVICE_RUNTIME_CHANGED')
        state = workbench.load_state(offer_id, state_dir=profile.root / 'data/new_product_workbench')
        snapshot = rounds.validate_round2_input(offer_id, state,
            reports_root=profile.root / 'reports/product-preparation')
    approval = state.get('product_approval') or {}
    reference = approval.get('round1_prepared_reference')
    legacy = (snapshot.get('approval_authority') == 'EXPLICIT_CONVERSATION_APPROVAL'
              and snapshot.get('approved_by') == 'Kyle'
              and not snapshot.get('decision_receipt_digest')
              and approval.get('status') == 'approved' and approval.get('approved_by') == 'Kyle')
    if reference:
        prepared = round1_workspace.read_preparation(offer_id, reference)
        legacy = (legacy and prepared['actor'] == 'Kyle'
                  and rounds.canonical_digest(prepared['packet']) == snapshot['first_review_digest']
                  and approval.get('round1_review_digest') == snapshot['first_review_digest']
                  and round1_workspace._same_state(prepared, state) == 'APPROVED') if legacy else False
    if operations is None:
        if not legacy:
            raise ValueError('NATIVE_R2_SERVICE_RUNTIME_REQUIRED')
        return load_r2_documents(offer_id)
    automatic = (approval.get('approved_by') == rounds.AUTOPILOT_ACTOR
                 or approval.get('approval_authority') == 'ACTIVE_AUTOPILOT_POLICY'
                 or bool(approval.get('decision_digest'))
                 or snapshot.get('approval_authority') == 'ACTIVE_AUTOPILOT_POLICY'
                 or bool(snapshot.get('decision_receipt_digest')))
    reference = approval.get('round1_prepared_reference')
    if not reference:
        if automatic or not legacy:
            raise ValueError('NATIVE_R2_ORIGINAL_REFERENCE_UNAVAILABLE')
        return load_r2_documents(offer_id)
    from shared_platform.immutable_approval_files import require_local_path
    database = profile.data_root / 'tasks.db'
    require_local_path(database, root=profile.data_root)
    if not database.is_file():
        raise ValueError('NATIVE_R2_TASK_LEDGER_UNAVAILABLE')
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        owners = db.execute(
            'SELECT DISTINCT e.task_id FROM workbench_events e JOIN workbench_execution x ON x.task_id=e.task_id '
            "WHERE e.event_type='checkpoint_saved' AND x.template='publication' "
            "AND json_extract(e.detail_json,'$.checkpoint.native_preparation.prepared_reference')=? "
            "ORDER BY e.task_id LIMIT 2", (reference,)).fetchall()
        if not owners:
            if automatic or not legacy:
                raise ValueError('NATIVE_R2_ORIGINAL_OWNER_UNAVAILABLE')
            return load_r2_documents(offer_id)
        if len(owners) != 1:
            raise ValueError('NATIVE_R2_ORIGINAL_OWNER_AMBIGUOUS')
        row = engine._row(db, owners[0]['task_id'])
        engine._require_version(row)
        task = engine._project(db, row)
        if task['scope'].get('offer_id') != offer_id:
            raise ValueError('NATIVE_R2_ORIGINAL_OWNER_OFFER_CHANGED')
    return load_r2_documents(offer_id, reports_root=profile.root / 'reports/product-preparation',
                             native_review_reader=NativeR2PreparedReviewReader(task, profile))


def read_images(task, profile):
    """Read all six native R2 documents through the actual domain consumer."""
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, load_r2_documents, validate_r2_identity
    frozen = read_frozen(task, profile)
    if not frozen:
        raise ValueError('NATIVE_R1_REQUIRED')
    directory = _report_dir(profile, frozen['offer_id'])
    for name in R2_DOCUMENTS.values():
        path = directory / name
        if path.is_symlink():
            raise ValueError('NATIVE_R2_SOURCE_SYMLINK')
        if not path.is_file():
            raise FileNotFoundError('NATIVE_R2_DOCUMENT_NOT_YET_PRODUCED:' + name)
    documents = load_r2_documents(frozen['offer_id'], reports_root=Path(profile.root) / 'reports/product-preparation',
                                  native_review_reader=NativeR2PreparedReviewReader(task, profile))
    identity = validate_r2_identity(documents)
    if identity.get('round1_snapshot_digest') != frozen['snapshot_digest']:
        raise ValueError('NATIVE_R2_R1_CONFLICT')
    return {'native_r1': frozen, 'native_r2': identity}


def run(engine, task, token, profile, *, image_stage=None, later_stage=None):
    offer, targets = _scope(task)
    step, task_id = task['current_step'], task['task_id']
    url = '/product-workspace?offer_id=' + offer + '&round=first#originalPublicationReview'
    if step != 'facts':
        callback = image_stage if step == 'images' else later_stage
        if callback is None:
            raise ValueError('NATIVE_DOMAIN_STAGE_ADAPTER_REQUIRED:' + step)
        return callback(engine, task, token, profile)
    frozen = read_frozen(task, profile)
    if frozen:
        engine.bind_scope(task_id, token, {**task['scope'], 'skus': [frozen['internal_sku']], 'shops': targets})
        engine.complete_step(task_id, token, expected_step='facts', checkpoint={'native_r1': frozen})
        return
    preparation = prepare_facts(task, profile)
    engine.record_checkpoint(task_id, token, {'native_preparation': preparation})
    engine.wait_for_user(task_id, token, kind='review', label='审核首轮商品事实',
        reason='在原商品上架页面核对事实、目标店铺、文案、价格和图片计划，并完成首轮确认。',
        url=url, receipt_binding={'adapter': 'publication-native/v1', 'step': 'facts', 'offer_id': offer, 'targets': targets})


def observe(engine, task, profile, *, later_observer=None):
    action = task.get('required_action') or task.get('pending_observation') or {}
    binding = action.get('receipt_binding') or {}
    if binding.get('adapter') != 'publication-native/v1':
        return False
    if binding.get('step') != 'facts':
        return later_observer(engine, task, profile) if later_observer else False
    try:
        frozen = read_frozen(task, profile)
    except NativeR1ReconciliationRequired as error:
        engine.observe_reconciliation(task['task_id'], None, str(error), action_id=action['action_id'])
        return True
    if not frozen:
        from modules.sourcing import new_product_workbench as workbench
        from shared_platform import publication_rounds
        offer, targets = _scope(task)
        directory = _report_dir(profile, offer)
        approval = workbench.load_state(offer).get('product_approval') or {}
        pending = ((directory / 'round1-auto-decision.json').exists()
                   or (directory / 'round1-auto-decision.json').is_symlink()
                   or approval.get('approved_by') == publication_rounds.AUTOPILOT_ACTOR)
        if (pending and not (directory / 'round1-approved-snapshot.json').is_file()
                and binding.get('offer_id') == offer
                and sorted(binding.get('targets') or []) == targets):
            engine.observe_reconciliation(task['task_id'], None,
                'R1 技术决定已落盘但快照未完成；按原准备回执对账，不再请求首轮人审',
                action_id=action['action_id'])
            return True
        return False
    receipt = {'receipt_id': 'native-r1:' + frozen['snapshot_digest'], 'native_r1': frozen}
    def verify(value, expected):
        current = read_frozen(task, profile)
        return bool(current and current == value['native_r1'] and current['offer_id'] == expected['offer_id'] and current['targets'] == expected['targets']
                    and (not expected.get('prepared_reference') or current.get('prepared_reference') == expected['prepared_reference']))
    return engine.accept_domain_receipt(task['task_id'], receipt, verify)
