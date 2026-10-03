"""Shared ledger for legacy and workbench commerce writers on one deployment."""
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine


def engine_for(root):
    if os.environ.get('ORBIT_OPERATIONS_DATA_ROOT'):
        profile = RuntimeProfile.capture(root)
        if profile.environment != 'stable':
            raise ValueError('preview environment cannot coordinate commerce writes')
        return WorkbenchEngine(profile.data_root / 'tasks.db', {'code_version': profile.version, 'environment': profile.environment, 'manifest_digest': profile.manifest_digest})
    pointer = Path(os.environ.get('ORBIT_OPERATIONS_PROFILE') or str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'OrbitHive/operations-runtime.json'))
    if not pointer.is_file():
        return None  # Older unconfigured deployments retain existing behaviour.
    payload = json.loads(pointer.read_text(encoding='utf-8'))
    if payload.get('schema_version') != 'orbit-operations-profile/v1' or payload.get('environment') != 'stable':
        raise ValueError('invalid shared operations profile')
    # An ambient personal profile must not turn an unrelated worktree or a
    # synthetic provider test into a writer on the stable coordination ledger.
    source = payload.get('source_root')
    additional = payload.get('authorized_source_roots', [])
    if not source:
        raise ValueError('shared operations caller binding missing; migrate profile source_root explicitly')
    if not isinstance(additional, list):
        raise ValueError('shared operations caller bindings must be a list of absolute directories')
    def canonical_directory(value):
        if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
            raise ValueError('invalid shared operations caller directory')
        path = Path(value)
        if not path.is_absolute():
            raise ValueError('shared operations caller directory must be absolute')
        try:
            path = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError('shared operations caller directory does not resolve') from error
        if not path.is_dir():
            raise ValueError('shared operations caller binding must name a directory')
        return os.path.normcase(str(path))
    authorized = {canonical_directory(value) for value in [source, *additional]}
    if canonical_directory(root) not in authorized:
        raise ValueError('shared operations caller is not authorized by this profile')
    state = Path(payload['data_root']).resolve(strict=True)
    return WorkbenchEngine(state / 'tasks.db', payload['release_identity'])


def begin_delisting(plan, root, *, operation_owner=None):
    engine = engine_for(root)
    if engine is None:
        return None
    skus = plan.get('requested_skus') or []
    shops = sorted({r['target_label'] for r in plan.get('targets', []) if r.get('executable')})
    operation = 'delisting:' + plan['plan_digest']
    receipt = engine.begin_domain_operation(operation, skus=skus, shops=shops, owner_task_id=operation_owner)
    return engine, operation, receipt


def finish_delisting(guard, result, *, target_labels=None):
    rows = result.get('targets') or []
    if target_labels is not None:
        expected = set(target_labels)
        rows = [row for row in rows if row.get('target_label') in expected]
        complete = expected and {row.get('target_label') for row in rows} == expected
    else:
        complete = bool(rows)
    if guard is not None and complete and all(r.get('verified') is True for r in rows):
        digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        guard[0].complete_domain_operation(guard[1], provider_readback_ref='delisting-result:' + digest)


def _publication_operation_id(plan_id, target_labels, retry_attempt=None, recovery_continuation=None):
    operation = 'publication:' + str(plan_id) + ':' + hashlib.sha256(json.dumps(sorted(target_labels)).encode()).hexdigest()
    if retry_attempt is not None:
        if (not isinstance(retry_attempt, dict)
                or set(retry_attempt) != {'run_id', 'retry_of_run_id', 'source_evidence_digest'}
                or any(not isinstance(value, str) or not value.strip() for value in retry_attempt.values())):
            raise ValueError('publication retry operation identity is invalid')
        attempt_digest = hashlib.sha256(json.dumps(retry_attempt, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        operation += ':retry:' + attempt_digest
    if recovery_continuation is not None:
        if (not isinstance(recovery_continuation, dict)
                or recovery_continuation.get('schema_version') != 'shopee-recovery-continuation/v1'
                or recovery_continuation.get('target_scope') != list(target_labels)
                or not str(recovery_continuation.get('receipt_digest') or '').startswith('sha256:')):
            raise ValueError('publication recovery continuation identity is invalid')
        operation += ':continuation:' + hashlib.sha256(json.dumps(
            recovery_continuation, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return operation


def begin_publication(plan, target_labels, root, *, retry_attempt=None, recovery_continuation=None):
    engine = engine_for(root)
    if engine is None:
        return None
    from shared_platform.internal_catalog_sku import internal_sku
    sku = internal_sku(plan.get('seller_sku'))
    if not sku.isdigit() or len(sku) != 4:
        raise ValueError('publication coordination needs authoritative internal SKU')
    operation = _publication_operation_id(plan['plan_id'], target_labels, retry_attempt, recovery_continuation)
    owners = [t['task_id'] for t in engine.dashboard()['tasks'] if t['template'] == 'publication' and t['scope'].get('offer_id') == str(plan.get('product_id')) and t['scope'].get('skus') == [sku] and set(t['scope'].get('shops', [])) == set(plan.get('targets', [])) and t['execution_state'] not in {'completed', 'cancelled'}]
    if len(owners) > 1:
        raise ValueError('multiple publication tasks own the same frozen scope')
    result = engine.begin_domain_operation(operation, skus=[sku], shops=list(target_labels), owner_task_id=owners[0] if owners else None)
    if not result['acquired']:
        raise ValueError('existing publication operation requires reconciliation before another submission')
    return engine, operation, result


def _load_recovery_source(manifest, name):
    paths = manifest.get('source_file_paths') or {}
    digests = manifest.get('source_file_sha256') or {}
    if set(paths) != set(digests) or name not in paths:
        raise ValueError('Shopee recovery source evidence set conflicts')
    path = Path(paths[name]).resolve(strict=True)
    raw = path.read_bytes()
    actual = 'sha256:' + hashlib.sha256(raw).hexdigest()
    if actual != digests[name]:
        raise ValueError('Shopee recovery source evidence digest conflicts')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Shopee recovery source evidence is invalid') from error
    if not isinstance(value, dict):
        raise ValueError('Shopee recovery source evidence is invalid')
    return value


def _begin_initial_shopee_recovery_handover(*, engine, snapshot, offer_id,
                                             snapshot_digest, selected, sku,
                                             retry_attempt, expected_run_id, manifest, candidate,
                                             approval, allowed_source_roots,
                                             owner_task_id):
    """Validate and atomically replace the exact unresolved predecessor lock."""
    from shared_platform.shopee_regional_recovery import validate_recovery_manifest
    checked = validate_recovery_manifest(
        manifest, snapshot=snapshot, candidate=candidate, approval=approval,
        allowed_source_roots=allowed_source_roots)
    if checked != manifest or checked.get('continuation') is not None:
        raise ValueError('initial Shopee recovery authority is invalid')
    shopee_labels = [row['target_label'] for row in snapshot.get('publication_targets', [])
                     if row.get('target_label', '').startswith('shopee:')]
    if (checked.get('offer_id') != offer_id
            or checked.get('plan_id') != snapshot.get('plan_id')
            or checked.get('execution_snapshot_digest') != snapshot_digest
            or checked.get('target_labels') != list(selected)
            or not isinstance(expected_run_id, str) or not expected_run_id
            or retry_attempt != {
                'run_id': expected_run_id,
                'retry_of_run_id': checked.get('prior_run_id'),
                'source_evidence_digest': checked.get('prior_report_digest'),
            }
            or expected_run_id == checked.get('prior_run_id')
            or len(shopee_labels) != 4
            or set(shopee_labels) != {'shopee:PH', 'shopee:MY', 'shopee:TH', 'shopee:VN'}
            or list(selected) != [label for label in shopee_labels if label != 'shopee:PH']
            or checked.get('zero_write_preflight') != {
                'completed': True, 'external_write_count': 0}
            or checked.get('forbidden_operations') != [
                'create_publish_task', 'upload_image', 'global_mutation']):
        raise ValueError('Shopee recovery authority conflicts with approved domain scope')
    prior = _load_recovery_source(checked, 'prior_report')
    global_readback = _load_recovery_source(checked, 'global_readback')
    regional_readback = _load_recovery_source(checked, 'regional_readback')
    prior_rows = prior.get('targets') or []
    expected_status = {label: ('PUBLISHED' if label == 'shopee:PH' else 'FAILED')
                       for label in shopee_labels}
    if (prior.get('schema_version') != 'product-publication-report/v2'
            or prior.get('status') != 'PARTIAL'
            or prior.get('offer_id') != offer_id
            or prior.get('plan_id') != snapshot.get('plan_id')
            or (prior.get('snapshot') or {}).get('digest') != snapshot_digest
            or prior.get('run_id') != checked.get('prior_run_id')
            or prior.get('report_id') != 'publication-report:' + checked.get('prior_run_id', '')
            or [row.get('target_label') for row in prior_rows] != shopee_labels
            or {row.get('target_label'): row.get('status') for row in prior_rows} != expected_status):
        raise ValueError('Shopee predecessor report is not the exact partial publication')
    global_rows = ((global_readback.get('response') or {}).get('response') or {}).get(
        'published_item') or []
    global_labels = ['shopee:' + str(row.get('shop_region') or '').upper()
                     for row in global_rows if isinstance(row, dict)]
    by_label = {'shopee:' + str(row.get('shop_region') or '').upper(): row
                for row in global_rows if isinstance(row, dict)}
    targets = {row.get('target_label'): row for row in checked.get('targets') or []}
    regional = {row.get('target_label'): row
                for row in regional_readback.get('targets') or [] if isinstance(row, dict)}
    if (len(global_rows) != 4 or len(set(global_labels)) != 4
            or set(by_label) != set(shopee_labels)
            or str(by_label['shopee:PH'].get('item_status')).upper() not in {'1', 'NORMAL'}
            or not str(by_label['shopee:PH'].get('item_id') or '').isdigit()
            or int(str(by_label['shopee:PH'].get('item_id') or '0')) <= 0
            or not str(by_label['shopee:PH'].get('shop_id') or '').isdigit()
            or int(str(by_label['shopee:PH'].get('shop_id') or '0')) <= 0
            or set(targets) != set(selected) or set(regional) != set(selected)):
        raise ValueError('Shopee official predecessor readback scope conflicts')
    for label in selected:
        fact, observed, published = targets[label], regional[label], by_label[label]
        models = observed.get('models') or []
        if (str(published.get('item_status')).upper() not in {'8', 'UNLIST'}
                or str(published.get('shop_id') or '') != str(fact.get('shop_id') or '')
                or str(published.get('item_id') or '') != str(fact.get('item_id') or '')
                or str(observed.get('shop_id') or '') != str(fact.get('shop_id') or '')
                or str(observed.get('item_id') or '') != str(fact.get('item_id') or '')
                or str(observed.get('item_status') or '').upper() != 'UNLIST'
                or len(models) != 1
                or str(models[0].get('model_id') or '') != str(fact.get('model_id') or '')
                or str(models[0].get('model_sku') or '') != sku
                or str(models[0].get('model_status') or '').upper() != 'MODEL_NORMAL'):
            raise ValueError('Shopee official predecessor target readback drifted')
    old_operation = _publication_operation_id(snapshot.get('plan_id') or snapshot_digest,
                                               shopee_labels)
    operation = _publication_operation_id(snapshot.get('plan_id') or snapshot_digest,
                                           selected, retry_attempt)
    authority_digest = hashlib.sha256(json.dumps({
        'manifest_digest': checked.get('manifest_digest'),
        'prior_run_id': checked.get('prior_run_id'),
        'prior_report_digest': checked.get('prior_report_digest'),
        'source_file_sha256': checked.get('source_file_sha256'),
        'old_operation_id': old_operation,
        'new_operation_id': operation,
    }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    reference = 'shopee-first-recovery-handover:' + authority_digest
    result = engine.supersede_domain_operation(
        old_operation, operation, previous_skus=[sku], previous_shops=shopee_labels,
        skus=[sku], shops=list(selected), owner_task_id=owner_task_id,
        reconciliation_ref=reference)
    return engine, operation, result


def begin_snapshot_publication(release_store, offer_id, snapshot_digest, platform, root,
                               *, retry_attempt=None, target_scope=None, recovery_continuation=None,
                               recovery_authorization=None, recovery_candidate=None,
                               recovery_approval=None, recovery_source_roots=None,
                               expected_run_id=None):
    if engine_for(root) is None:
        return None
    snapshot = release_store.approved_publication_snapshot(offer_id=offer_id, snapshot_digest=snapshot_digest)
    if not isinstance(snapshot, dict):
        raise ValueError('approved publication identity unavailable for task coordination')
    targets = [r['target_label'] for r in snapshot.get('publication_targets', [])]
    selected = [label for label in targets if label.split(':', 1)[0].upper() == platform]
    if target_scope is not None:
        scoped = list(target_scope)
        if not scoped or len(scoped) != len(set(scoped)) or any(label not in selected for label in scoped):
            raise ValueError('publication coordination target scope conflicts')
        selected = scoped
    from shared_platform.internal_catalog_sku import internal_sku
    sku_keys = {internal_sku(row.get('seller_sku')) for row in snapshot.get('skus', [])}
    if len(sku_keys) != 1 or not next(iter(sku_keys), ''):
        raise ValueError('approved snapshot must identify one internal product SKU')
    plan = {'plan_id': snapshot.get('plan_id') or snapshot_digest, 'product_id': offer_id,
            'seller_sku': next(iter(sku_keys)),
            'targets': targets}
    if recovery_authorization is not None and recovery_continuation is None:
        engine = engine_for(root)
        owners = [t['task_id'] for t in engine.dashboard()['tasks']
                  if t['template'] == 'publication'
                  and t['scope'].get('offer_id') == str(offer_id)
                  and t['scope'].get('skus') == [next(iter(sku_keys))]
                  and set(t['scope'].get('shops', [])) == set(targets)
                  and t['execution_state'] not in {'completed', 'cancelled'}]
        if len(owners) > 1:
            raise ValueError('multiple publication tasks own the same frozen scope')
        if not isinstance(recovery_candidate, dict) or not isinstance(recovery_approval, dict) \
                or not recovery_source_roots:
            raise ValueError('deep Shopee recovery authority is required for domain handover')
        return _begin_initial_shopee_recovery_handover(
            engine=engine, snapshot=snapshot, offer_id=offer_id,
            snapshot_digest=snapshot_digest, selected=selected,
            sku=next(iter(sku_keys)), retry_attempt=retry_attempt,
            expected_run_id=expected_run_id,
            manifest=recovery_authorization, candidate=recovery_candidate,
            approval=recovery_approval, allowed_source_roots=recovery_source_roots,
            owner_task_id=owners[0] if owners else None)
    return begin_publication(plan, selected, root, retry_attempt=retry_attempt,
                             recovery_continuation=recovery_continuation)


def finish_snapshot_recovery_reconciliation(release_store, offer_id, snapshot_digest, root, *,
                                            target_scope, retry_attempt, receipt,
                                            run_id, report_id, manifest_digest,
                                            allowed_evidence_roots):
    """Release one recovery domain lock only for a terminal exact receipt gate."""
    from shared_platform.shopee_recovery_reconciliations import (
        reconciliation_gate, validate_reconciliation_receipt,
    )
    checked_receipt = validate_reconciliation_receipt(
        receipt, allowed_evidence_roots=allowed_evidence_roots)
    gate = reconciliation_gate(
        checked_receipt, run_id=run_id, report_id=report_id, manifest_digest=manifest_digest,
        allowed_evidence_roots=allowed_evidence_roots)
    if not isinstance(gate, dict) or gate.get('result') not in {'CONVERGED', 'REMAINING_DIFF'} \
            or gate.get('attempt_closed') is not True or gate.get('mutation_lock') is not False:
        raise ValueError('terminal Shopee recovery reconciliation is required')
    receipt_digest = str(gate.get('receipt_digest') or '')
    if not receipt_digest.startswith('sha256:') or len(receipt_digest) != 71:
        raise ValueError('Shopee recovery reconciliation receipt identity is invalid')
    validation_inputs = checked_receipt.get('validation_inputs') or {}
    frozen_manifest = validation_inputs.get('manifest') or {}
    frozen_attempt = validation_inputs.get('attempt') or {}
    expected_retry = {
        'run_id': frozen_attempt.get('run_id'),
        'retry_of_run_id': frozen_manifest.get('prior_run_id'),
        'source_evidence_digest': frozen_manifest.get('prior_report_digest'),
    }
    if retry_attempt != expected_retry:
        raise ValueError('Shopee recovery domain attempt conflicts with terminal receipt')
    snapshot = release_store.approved_publication_snapshot(
        offer_id=offer_id, snapshot_digest=snapshot_digest)
    if not isinstance(snapshot, dict):
        raise ValueError('approved publication identity unavailable for task coordination')
    all_targets = [row['target_label'] for row in snapshot.get('publication_targets', [])]
    selected = list(target_scope)
    shopee_targets = [label for label in all_targets if label.split(':', 1)[0].upper() == 'SHOPEE']
    if not selected or len(selected) != len(set(selected)) or any(label not in shopee_targets for label in selected):
        raise ValueError('publication coordination target scope conflicts')
    if (frozen_attempt.get('offer_id') != offer_id
            or frozen_attempt.get('plan_id') != snapshot.get('plan_id')
            or (frozen_attempt.get('snapshot') or {}).get('digest') != snapshot_digest
            or frozen_manifest.get('offer_id') != offer_id
            or frozen_manifest.get('plan_id') != snapshot.get('plan_id')
            or frozen_manifest.get('execution_snapshot_digest') != snapshot_digest
            or frozen_manifest.get('target_labels') != selected):
        raise ValueError('Shopee recovery receipt conflicts with approved domain scope')
    engine = engine_for(root)
    if engine is None:
        return None
    operation = _publication_operation_id(snapshot.get('plan_id') or snapshot_digest, selected, retry_attempt)
    difference_digest = hashlib.sha256(json.dumps(
        gate.get('new_manifest', {}).get('exact_remaining_differences', []),
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    reference = ':'.join(('shopee-recovery-reconciliation', receipt_digest.removeprefix('sha256:'),
                          gate['result'], difference_digest))
    engine.complete_domain_operation(operation, provider_readback_ref=reference)
    return {'operation_id': operation, 'provider_readback_ref': reference,
            'receipt_digest': receipt_digest, 'result': gate['result']}


def finish_reportless_shopee_recovery_reconciliation(
    release_store, offer_id, snapshot_digest, root, *, receipt, target_scope,
    run_store, report_store, snapshot, candidate, approval,
    allowed_evidence_roots,
):
    """Close one reportless recovery lock after full durable reconstruction."""
    from shared_platform.shopee_reportless_recovery_reconciliations import (
        validate_reportless_reconciliation_receipt,
    )
    checked = validate_reportless_reconciliation_receipt(
        receipt, run_store=run_store, report_store=report_store,
        snapshot=snapshot, candidate=candidate, approval=approval,
        allowed_evidence_roots=allowed_evidence_roots,
    )
    identity = checked['authority_identity']
    approved = release_store.approved_publication_snapshot(
        offer_id=offer_id, snapshot_digest=snapshot_digest)
    selected = list(target_scope)
    if (checked.get('result') != 'REMAINING_DIFF'
            or checked.get('attempt_closed') is not True
            or checked.get('mutation_lock') is not False
            or checked['new_manifest'].get('authorized') is not False
            or checked['new_manifest'].get('target_scope') != selected
            or identity.get('target_labels') != selected
            or identity.get('offer_id') != offer_id
            or identity.get('plan_id') != approved.get('plan_id')
            or identity.get('snapshot_digest') != snapshot_digest):
        raise ValueError('reportless Shopee recovery receipt conflicts with domain scope')
    retry_attempt = {
        'run_id': checked['attempt']['run_id'],
        'retry_of_run_id': identity['prior_run_id'],
        'source_evidence_digest': identity['prior_report_digest'],
    }
    engine = engine_for(root)
    if engine is None:
        return None
    operation = _publication_operation_id(approved['plan_id'], selected, retry_attempt)
    difference_digest = hashlib.sha256(json.dumps(
        checked['new_manifest']['exact_remaining_differences'],
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    reference = ':'.join(('shopee-reportless-recovery-reconciliation',
                          checked['receipt_digest'].removeprefix('sha256:'),
                          'REMAINING_DIFF', difference_digest))
    engine.complete_domain_operation(operation, provider_readback_ref=reference)
    return {'operation_id': operation, 'provider_readback_ref': reference,
            'receipt_digest': checked['receipt_digest'], 'result': 'REMAINING_DIFF',
            'new_manifest': deepcopy(checked['new_manifest'])}


def finish_known_zero_shopee_recovery(
    release_store, offer_id, snapshot_digest, root, *, receipt, target_scope,
    receipt_validation,
):
    """Close the exact continuation lock after a durable zero-dispatch report."""
    from shared_platform.shopee_known_zero_recovery import validate_known_zero_receipt
    checked = validate_known_zero_receipt(receipt, **receipt_validation)
    manifest = checked['manifest']; identity = checked['run_identity']
    approved = release_store.approved_publication_snapshot(
        offer_id=offer_id, snapshot_digest=snapshot_digest)
    selected = list(target_scope)
    if (checked.get('result') != 'KNOWN_ZERO_PREFLIGHT'
            or checked.get('attempt_closed') is not True
            or checked.get('mutation_lock') is not False
            or checked.get('provider_mutation_dispatch_attempted') is not False
            or checked.get('external_write_count') != 0
            or checked['new_manifest'].get('authorized') is not False
            or checked['new_manifest'].get('target_scope') != selected
            or manifest.get('target_labels') != selected
            or manifest.get('offer_id') != offer_id
            or manifest.get('plan_id') != approved.get('plan_id')
            or manifest.get('execution_snapshot_digest') != snapshot_digest):
        raise ValueError('known-zero Shopee recovery receipt conflicts with domain scope')
    skus = {str(row.get('model_sku') or '').strip() for row in manifest.get('targets') or []}
    if len(skus) != 1 or not next(iter(skus), ''):
        raise ValueError('known-zero Shopee recovery SKU identity conflicts')
    engine = engine_for(root)
    if engine is None: return None
    retry_attempt = {
        'run_id': identity['run_id'],
        'retry_of_run_id': manifest['direct_predecessor_run_id'],
        'source_evidence_digest': manifest['preflight_digest'],
    }
    operation = _publication_operation_id(
        approved['plan_id'], selected, retry_attempt=retry_attempt,
        recovery_continuation=manifest['continuation'])
    reference = 'shopee-known-zero-recovery:' + checked['receipt_digest'].removeprefix('sha256:')
    engine.complete_domain_operation_exact(operation, skus=sorted(skus), shops=selected,
                                           provider_readback_ref=reference)
    return {'operation_id': operation, 'provider_readback_ref': reference,
            'receipt_digest': checked['receipt_digest'], 'result': checked['result'],
            'new_manifest': deepcopy(checked['new_manifest']), 'run_id': identity['run_id']}


def begin_common(plan, root):
    engine = engine_for(root)
    if engine is None:return None
    from shared_platform.internal_catalog_sku import internal_sku
    payload=plan.get('payload') or {}
    sku=internal_sku(payload.get('seller_sku'))
    stage=payload.get('r3_stage_binding') or {}
    shops=[s for s in stage.get('marketplace_targets',[]) if s!='miaoshou:COMMON']
    if len(sku)!=4 or not sku.isdigit() or not shops or plan.get('targets')!=['miaoshou:COMMON']:
        raise ValueError('COMMON coordination requires exact frozen SKU and marketplace scope')
    owners=[t for t in engine.dashboard()['tasks'] if t['template']=='publication'
            and t['scope'].get('offer_id')==str(plan.get('product_id'))
            and t['scope'].get('skus')==[sku]
            and {s for s in t['scope'].get('shops',[]) if s!='miaoshou:COMMON'}==set(shops)
            and t['execution_state'] not in {'completed','cancelled'}]
    if len(owners)>1:raise ValueError('ambiguous COMMON task ownership')
    operation='publication-common:'+plan['plan_id']
    from shared_platform.release_store import default_release_store
    run = default_release_store().get_run('release-run:' + plan['payload_digest'][:24])
    target = next((row for row in (run or {}).get('targets', []) if row['target_label']=='miaoshou:COMMON'), {})
    if target.get('attempts', 0) > 1:
        with engine.transaction() as conn:
            for attempt in range(1,target['attempts']):
                previous_id=operation if attempt==1 else operation+':attempt:'+str(attempt)
                prior=conn.execute('SELECT * FROM workbench_domain_operations WHERE operation_id=?',(previous_id,)).fetchone()
                if not prior or prior['state']!='completed' or not prior['readback_ref'].startswith('local-not-dispatched:'):
                    raise ValueError('COMMON retry requires all prior attempts durably reconciled as not dispatched')
        operation += ':attempt:' + str(target['attempts'])
    result=engine.begin_domain_operation(operation,skus=[sku],shops=shops,owner_task_id=owners[0]['task_id'] if owners else None)
    if not result['acquired']:raise ValueError('COMMON operation already exists; reconcile original readback before any resubmission')
    return engine,operation,result


def finish_common(guard, evidence):
    if guard is not None and evidence.get('verified') is True:
        digest=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
        guard[0].complete_domain_operation(guard[1],provider_readback_ref='common-readback:'+digest)


def reconcile_common(plan, evidence, root):
    """Release an existing unknown reservation only after original domain verification."""
    engine=engine_for(root)
    if engine is None or evidence.get('verified') is not True:return
    operation='publication-common:'+plan['plan_id']
    from shared_platform.release_store import default_release_store
    run=default_release_store().get_run('release-run:'+plan['payload_digest'][:24])
    target=next((row for row in (run or {}).get('targets',[]) if row['target_label']=='miaoshou:COMMON'),{})
    if target.get('attempts',0)>1:
        operation += ':attempt:' + str(target['attempts'])
    with engine.transaction() as connection:
        old=connection.execute('SELECT state FROM workbench_domain_operations WHERE operation_id=?',(operation,)).fetchone()
    if old and old['state']=='inflight':
        finish_common((engine,operation,{}),evidence)
