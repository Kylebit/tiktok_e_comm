"""Prepare R3 COMMON under existing domain authority, then expose a real final review.

run returns True only when the caller may continue its final-review handling.
It never approves a plan, executes marketplaces, or completes a publication task.
observe returns None for actions owned by another adapter.
"""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from typing import Mapping
from shared_platform.internal_catalog_sku import internal_sku

ADAPTER='publication-common/v1'


class PostCommonReadinessBlocked(ValueError):
    """The task cannot hand a review to the user from current evidence."""

    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(';'.join(diagnostic['blockers']))


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def _server(profile, server_module):
    if server_module is None:
        from modules.products import server as server_module
    if Path(server_module.ROOT).resolve()!=Path(profile.root).resolve():
        raise ValueError('COMMON 服务与当前任务代码根不一致')
    return server_module


def _read(server, task):
    code,view=server._publication_stages_for_request({'offer_id':task['scope']['offer_id']})
    if str(view.get('offer_id'))!=task['scope']['offer_id']:
        raise ValueError('COMMON 阶段返回了不同商品身份')
    if view.get('stage')=='RECONCILIATION_REQUIRED' or (view.get('common') or {}).get('status')=='RECONCILIATION_REQUIRED':
        return view
    if (code!=200 or view.get('ok') is not True) and (view.get('common') or {}).get('status')!='VERIFIED':
        raise ValueError('COMMON 准备资料未通过当前域校验，需执行者修复；没有可批准的最终发布包')
    return view


def _binding(task, common):
    plan=common.get('plan') or {};payload=plan.get('payload') or {}
    sku=internal_sku(plan.get('seller_sku') or payload.get('seller_sku'))
    stage=payload.get('r3_stage_binding') or {}
    stage_targets=stage.get('marketplace_targets')
    task_targets=task['scope'].get('shops')
    checkpoints=[task.get('checkpoint') or {},*[(s.get('checkpoint') or {}) for s in task.get('steps',[])]]
    frozen_r1=next((c['native_r1'] for c in checkpoints if c.get('native_r1')),None)
    frozen_r2=next((c['native_r2'] for c in checkpoints if c.get('native_r2')),None)
    if frozen_r1 is None and frozen_r2 is None:
        # Registered reviewed products retain the same immutable identities under
        # their existing adapter's receipt names; do not invent native receipts.
        completed={s['key']:s.get('checkpoint') or {} for s in task.get('steps',[]) if s.get('state')=='completed'}
        first=completed.get('facts',{}).get('publication_identity') or {}
        images=completed.get('images',{})
        image_first=images.get('publication_identity') or {}
        image_receipt=images.get('image_receipt') or {}
        if (first and first==image_first and first.get('offer_id')==task['scope']['offer_id']
                and re.fullmatch(r'[0-9a-f]{64}',str(first.get('binding_sha256') or ''))
                and first.get('binding_sha256')==image_receipt.get('binding_sha256')):
            frozen_r1={'snapshot_digest':first.get('snapshot_digest')}
            frozen_r2=image_receipt.get('consumer_identity')
    if (str(plan.get('product_id'))!=task['scope']['offer_id'] or plan.get('targets')!=['miaoshou:COMMON']
            or not plan.get('plan_id') or not plan.get('payload_digest')
            or stage.get('schema_version')!='r3-common-stage/v1'
            or type(stage_targets) is not list or type(task_targets) is not list
            or any(type(s) is not str or not s for s in stage_targets)
            or not stage_targets or sorted(stage_targets)!=task_targets
            or ('miaoshou:COMMON' in stage_targets and not (
                frozen_r1 and frozen_r1.get('prepared_reference')
                and (stage.get('native_preparation_source') or {}).get('prepared_reference')))
            or len(set(stage_targets))!=len(stage_targets)
            or task['scope'].get('skus')!=[sku] or len(sku)!=4 or not sku.isdigit()
            or not stage.get('round1_snapshot_digest')):
        raise ValueError('COMMON 计划未绑定当前商品、内部 SKU、R1 与完整目标范围')
    if (not frozen_r1 or frozen_r1.get('snapshot_digest')!=stage['round1_snapshot_digest']
            or not frozen_r2 or frozen_r2!=stage.get('r2_identity')):
        raise ValueError('COMMON 计划与此任务已完成的 R1/R2 冻结证据不一致')
    return {'adapter':ADAPTER,'offer_id':task['scope']['offer_id'],'plan_id':plan['plan_id'],
            'payload_digest':plan['payload_digest'],'scope_digest':_digest(task['scope']),
            'round1_snapshot_digest':stage['round1_snapshot_digest']}



def _verified_binding(task, common, profile):
    """Service-read preparation lineage only; this grants no write authority."""
    from shared_platform import workbench_publication_native as native
    from shared_platform import round1_workspace
    from shared_platform.round1_category_evidence import digest

    binding = _binding(task, common)
    source = {'schema_version': 'common-preparation-source/v1',
              'status': 'UNKNOWN', 'execution_authority': False}
    checkpoints = [task.get('checkpoint') or {},
                   *[(step.get('checkpoint') or {}) for step in task.get('steps', [])]]
    # Old registered/normalized receipts may carry a snapshot digest without
    # claiming a native preparation. Preserve them as UNKNOWN, never as native.
    native_claim = any(
        (checkpoint.get('native_r1') or {}).get('prepared_reference')
        or (checkpoint.get('native_preparation') or {}).get('prepared_reference')
        for checkpoint in checkpoints)
    if native_claim:
        # The real reader checks current immutable preparation/snapshot and the
        # earliest original durable owner event; caller checkpoints cannot do so.
        frozen = native.read_frozen(task, profile)
        if frozen is not None:
            prepared = round1_workspace.read_preparation(
                frozen['offer_id'], reference=frozen['prepared_reference'])
            scope = prepared['scope']
            if (frozen['offer_id'] != binding['offer_id']
                    or frozen['snapshot_digest'] != binding['round1_snapshot_digest']
                    or scope['offer_id'] != frozen['offer_id']
                    or sorted(scope['requested_targets']) != frozen['targets']):
                raise native.NativeR1ReconciliationRequired('COMMON_PREPARATION_SOURCE_CONFLICT')
            source = {**source, 'status': 'VERIFIED_NATIVE_R1',
                      'owner_task_id': task['task_id'],
                      'prepared_reference': frozen['prepared_reference'],
                      'preparation_digest': digest(prepared),
                      'snapshot_digest': frozen['snapshot_digest'],
                      'targets': frozen['targets'],
                      'account_identity_digest': scope['account_identity_digest'],
                      'source_region': scope['source_region'],
                      'preparation_root': round1_workspace.preparation_root_facts(prepared)}
    stage=(common.get('plan') or {}).get('payload',{}).get('r3_stage_binding') or {}
    if 'miaoshou:COMMON' in (stage.get('marketplace_targets') or []):
        original=stage.get('native_preparation_source') or {}
        if (source['status']!='VERIFIED_NATIVE_R1'
                or source.get('snapshot_digest')!=original.get('round1_snapshot_digest')
                or any(source.get(key)!=original.get(key) for key in (
                    'prepared_reference','preparation_digest','account_identity_digest','targets'))
                or round1_workspace._check_preparation_root(prepared)!=original.get('preparation_root')):
            raise native.NativeR1ReconciliationRequired('COMMON_PREPARATION_SOURCE_CONFLICT')
    return {**binding, 'preparation_source': source}


def _unknown(view):
    return view.get('stage')=='RECONCILIATION_REQUIRED' or (view.get('common') or {}).get('status')=='RECONCILIATION_REQUIRED'


def inspect_post_common_readiness(task, view):
    """Inert task projection; current sources cannot prove historical COMMON budget.

    The stage view proves one durable run at most. It has no offer-wide,
    reservation-inclusive COMMON attempt ledger, so a label such as VERIFIED
    cannot confer the sole marketplace review. Keep existing evidence visible
    while this transition is blocked for a server-owned producer.
    """
    scope = task.get('scope') if isinstance(task, Mapping) else None
    common = view.get('common') if isinstance(view, Mapping) else None
    market = view.get('marketplace') if isinstance(view, Mapping) else None
    scope = scope if isinstance(scope, Mapping) else {}
    common = common if isinstance(common, Mapping) else {}
    market = market if isinstance(market, Mapping) else {}
    candidate = market.get('preview') or market.get('candidate') or {}
    candidate = candidate if isinstance(candidate, Mapping) else {}
    plan = market.get('plan') or {}
    plan = plan if isinstance(plan, Mapping) else {}
    manifest = candidate.get('review_manifest') or {}
    manifest = manifest if isinstance(manifest, Mapping) else {}
    shops = scope.get('shops')
    targets = [label for label in shops if label != 'miaoshou:COMMON'] if type(shops) is list else []
    plan_targets = plan.get('targets')
    blockers = []
    if (not targets or any(type(label) is not str or not label for label in targets)
            or len(set(targets)) != len(targets)):
        blockers.append('POST_COMMON_TARGET_SCOPE_INVALID')
    manifest_rows = manifest.get('targets')
    manifest_targets = ([row.get('target_label') for row in manifest_rows]
                        if type(manifest_rows) is list
                        and all(isinstance(row, Mapping) for row in manifest_rows) else None)
    if (type(plan_targets) is not list or not plan_targets
            or any(type(label) is not str or not label for label in plan_targets)
            or len(set(plan_targets)) != len(plan_targets)
            or any(type(label) is not str or not label for label in targets)
            or sorted(plan_targets) != sorted(targets)
            or market.get('status') != 'APPROVAL_REQUIRED'
            or candidate.get('status') != 'READY_FOR_FINAL_REVIEW'
            or not candidate.get('candidate_digest')
            or plan.get('product_id') != scope.get('offer_id')
            or market.get('targets') != plan_targets
            or candidate.get('target_labels') != plan_targets
            or manifest_targets != plan_targets
            or not manifest.get('manifest_digest')):
        blockers.append('POST_COMMON_COMPLETE_CANDIDATE_UNPROVEN')
    run = common.get('run')
    rows = run.get('targets') if isinstance(run, Mapping) else None
    common_rows = ([row for row in rows if isinstance(row, Mapping)
                    and row.get('target_label') == 'miaoshou:COMMON']
                   if type(rows) is list else [])
    readback = common_rows[0].get('readback') if len(common_rows) == 1 else None
    evidence = readback.get('evidence') if isinstance(readback, Mapping) else None
    checks = evidence.get('checks') if isinstance(evidence, Mapping) else None
    if (common.get('status') != 'VERIFIED' or len(common_rows) != 1
            or common_rows[0].get('status') != 'SUCCEEDED'
            or not isinstance(evidence, Mapping)
            or evidence.get('source') != 'miaoshou_open_api'
            or evidence.get('verified') is not True
            or not isinstance(checks, Mapping) or not checks
            or any(value is not True for value in checks.values())
            or not readback.get('evidence_digest') or not readback.get('verified_at')):
        blockers.append('COMMON_OFFICIAL_FIELD_READBACK_UNPROVEN')
    # Neither this task checkpoint nor the stage view carries a trusted,
    # offer-wide COMMON reservation/write history. A new server-owned producer
    # and contract are required before this blocker can ever be removed.
    blockers.append('COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN')
    return {'schema_version': 'post-common-task-readiness/v1', 'status': 'BLOCKED',
            'blockers': blockers, 'final_review_available': False,
            'execution_authority': False, 'external_writes_performed': []}


def _final_ready(task, view):
    diagnostic = inspect_post_common_readiness(task, view)
    raise PostCommonReadinessBlocked(diagnostic)


def _post_common_readiness(server,task,view):
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    installed=getattr(server,'_NATIVE_FINAL_SERVICE',None)
    if type(installed) is NativeSoleFinalService and installed._new_decision_execution_enabled:
        try:return installed.final_review_readiness(task,view)
        except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
            return {'schema_version':'post-common-native-readiness/v1','status':'BLOCKED',
                'blockers':[str(error)],'final_review_available':False,
                'execution_authority':False,'external_writes_performed':[]}
    return inspect_post_common_readiness(task,view)


def _native_request_readiness(server,task,view):
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    installed=getattr(server,'_NATIVE_FINAL_SERVICE',None)
    if type(installed) is not NativeSoleFinalService or not installed._new_decision_execution_enabled:
        return {'status':'BLOCKED'}
    try:return installed.common_request_readiness(task,view)
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
        return {'status':'BLOCKED','blockers':[str(error)]}


def _materialize_final(server, task, view):
    market=view.get('marketplace') or {};plan=market.get('plan') or {}
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    installed=getattr(server,'_NATIVE_FINAL_SERVICE',None)
    native_completed=(type(installed) is NativeSoleFinalService and installed._new_decision_execution_enabled
        and (view.get('common') or {}).get('status') in {'VERIFIED','RETAINED_TECHNICAL_BASELINE'})
    if not (market.get('preview') or market.get('candidate')) and (
            market.get('status')=='APPROVAL_REQUIRED' and plan or native_completed):
        code,refreshed=server._preview_r3_marketplace_stage({'offer_id':task['scope']['offer_id']})
        replacement=(refreshed.get('marketplace') or {}).get('plan') or {}
        if (code!=200 or not replacement.get('plan_id') or plan and (
                replacement.get('plan_id')!=plan.get('plan_id')
                or replacement.get('payload_digest')!=plan.get('payload_digest'))):
            if native_completed:
                raise PostCommonReadinessBlocked({'status':'BLOCKED',
                    'blockers':((refreshed.get('marketplace') or {}).get('blockers') or
                        [refreshed.get('error') or 'NATIVE_FINAL_CANDIDATE_SOURCE_INCOMPLETE']),
                    'execution_authority':False,'final_review_available':False})
            raise ValueError('原最终审核候选无法按相同冻结计划恢复，需核对资料变化')
        view=refreshed
    diagnostic=_post_common_readiness(server,task,view)
    if diagnostic.get('status')=='READY':return True
    raise PostCommonReadinessBlocked(diagnostic)


def _materialize_or_block(engine, server, task, token, view, profile):
    try:
        return _materialize_final(server, task, view)
    except PostCommonReadinessBlocked as error:
        engine.record_checkpoint(task['task_id'], token,
            {**(task.get('checkpoint') or {}), 'post_common_diagnostic': error.diagnostic})
        current = engine.get(task['task_id'])
        if current['version'] != task['version'] or current['version'] != engine.release:
            raise ValueError('COMMON 当前任务版本已变化')
        if current['scope'] != task['scope']:
            raise ValueError('COMMON 当前任务范围已变化')
        task = current
        with engine.transaction() as db:
            row = engine._lease(db, task['task_id'], token)
            if json.loads(row['scope_json']) != task['scope']:
                raise ValueError('COMMON 当前任务范围已变化')
        engine.await_domain(task['task_id'],token,label='终审候选待准备',
            reason=str(error),receipt_binding=_verified_binding(task,view.get('common') or {},profile))
        return False


def _common_technical_admission(binding, plan, *, server):
    from shared_platform.publication_common_write_admission import inspect_common_write_admission
    admission=inspect_common_write_admission(plan, store=server._release_store(),
        policy_reader=server._service_common_standing_policy_reader())
    expected={'offer_id':binding['offer_id'],'plan_id':binding['plan_id'],
              'payload_digest':binding['payload_digest']}
    if admission['binding']!=expected:
        admission={**admission,'status':'BLOCKED',
            'blockers':list(dict.fromkeys([*admission['blockers'],
                                           'COMMON_TECHNICAL_BINDING_INVALID']))}
    if (binding.get('preparation_source') or {}).get('status') != 'VERIFIED_NATIVE_R1':
        admission = {**admission, 'status': 'BLOCKED',
                     'blockers': list(dict.fromkeys([*admission['blockers'],
                                                    'COMMON_PREPARATION_SOURCE_UNKNOWN']))}
    return {**admission,'binding':binding}


def _block_missing_common_authority(engine, task, token, diagnostic):
    engine.record_checkpoint(task['task_id'],token,
        {**(task.get('checkpoint') or {}), 'common_technical_admission':diagnostic})
    engine.await_domain(task['task_id'],token,label='COMMON 技术证据待核',
        reason=';'.join(diagnostic['blockers']),receipt_binding=diagnostic['binding'])
    return False


def run(engine,task,token,profile,*,server_module=None):
    server=_server(profile,server_module);view=_read(server,task)
    if _unknown(view):
        engine.observe_reconciliation(task['task_id'],token,'原 COMMON 结果尚未核验，保留原操作并停止重复同步')
        return False
    common=view.get('common') or {};binding=_verified_binding(task,common,profile)
    operation='publication-common:'+binding['plan_id']
    if common.get('status')=='VERIFIED':
        return _materialize_or_block(engine,server,task,token,view,profile)
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    installed=getattr(server,'_NATIVE_FINAL_SERVICE',None)
    if (type(installed) is NativeSoleFinalService and installed._new_decision_execution_enabled
            and common.get('status') in {'APPROVAL_REQUIRED','TECHNICAL_CONDITIONS_UNKNOWN','RETAINED_TECHNICAL_BASELINE'}
            and (binding.get('preparation_source') or {}).get('status')=='VERIFIED_NATIVE_R1'):
        return installed.prepare_common_for_task(engine,task,token,profile,binding,common['plan'])
    if common.get('status') in {'APPROVAL_REQUIRED', 'TECHNICAL_CONDITIONS_UNKNOWN', 'RETAINED_TECHNICAL_BASELINE'}:
        return _block_missing_common_authority(
            engine,task,token,_common_technical_admission(binding,common['plan'],server=server))
    if common.get('status')!='READY_TO_SYNC':
        raise ValueError('COMMON 当前步骤不能自动执行，需要核对原域失败原因')
    plan=common['plan']
    if plan.get('status')!='APPROVED' or not plan.get('approval') or not plan.get('confirmation_token'):
        raise ValueError('COMMON 缺少精确原域批准，不得由任务代签')
    if profile.environment!='stable':
        raise ValueError('预览环境不能执行真实 COMMON 同步')
    # No provider mutation until a server-owned, Offer-wide budget and standing
    # policy producer is wired into this boundary. Preserve the approved plan
    # for reconciliation; do not convert it into another human review.
    admission=_common_technical_admission(binding,plan,server=server)
    if admission['status']!='READY':
        return _block_missing_common_authority(engine,task,token,admission)
    previous=(task.get('checkpoint') or {}).get('common_attempt')
    recovery=(task.get('checkpoint') or {}).get('common_config_recovery') or {}
    target=next((r for r in (common.get('run') or {}).get('targets',[]) if r['target_label']=='miaoshou:COMMON'),{})
    failure=(target.get('latest_failure_evidence') or {})
    recovered=(recovery.get('plan_id')==plan['plan_id'] and recovery.get('failure_attempt')==target.get('attempts')
        and recovery.get('local_evidence_ref')=='local-not-dispatched:'+str(failure.get('evidence_digest') or '')
        and (failure.get('evidence') or {}).get('schema_version') in {'common-local-config-not-dispatched/v1','common-local-variant-key-not-dispatched/v1'})
    if previous and previous.get('state')!='verified' and not recovered:
        engine.observe_reconciliation(task['task_id'],token,'COMMON 原提交已存在，需只读核验后继续')
        return False
    history=list((task.get('checkpoint') or {}).get('common_attempt_history') or [])
    if recovered and previous:history.append(previous)
    engine.record_checkpoint(task['task_id'],token,{**(task.get('checkpoint') or {}),'common_attempt_history':history,
        'common_attempt':{'operation_id':operation,'binding':binding,'state':'started'}})
    # Only this exact current domain plan supplies the token. No approval endpoint is called.
    try:
        code,result=server._prepare_miaoshou_release({'offer_id':task['scope']['offer_id'],
            'plan_id':plan['plan_id'],'confirmation_token':plan['confirmation_token'],
            'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON'],'confirm_miaoshou_write':True})
        after=_read(server,task)
    except Exception:
        engine.observe_reconciliation(task['task_id'],token,'COMMON 调用未确定返回，核验原计划与官方回执后恢复，禁止重发')
        return False
    if _unknown(after) or (after.get('common') or {}).get('status')!='VERIFIED':
        engine.observe_reconciliation(task['task_id'],token,'COMMON 同步未获得精确已核验回执，保留原操作等待对账')
        return False
    if _verified_binding(task,after['common'],profile)!=binding:
        engine.observe_reconciliation(task['task_id'],token,'COMMON 返回的冻结身份发生变化，必须核对原操作')
        return False
    run_record=after['common'].get('run') or {}
    # The single server-owned business boundary owns the shared domain guard.
    # Reserving here as well would make the same server operation reject itself.
    engine.record_checkpoint(task['task_id'],token,{**(task.get('checkpoint') or {}),'common_attempt':{'operation_id':operation,'binding':binding,'state':'verified','readback_digest':_digest(run_record)}})
    return _materialize_or_block(engine,server,task,token,after,profile)


def observe(engine,task,profile,*,server_module=None):
    if task.get('execution_state')!='waiting_domain':return None
    action=task.get('pending_observation') or {}
    if action.get('kind')!='observe':return None
    binding=action.get('receipt_binding') or {}
    if binding.get('adapter')!=ADAPTER:return None
    server=_server(profile,server_module);view=_read(server,task)
    if _unknown(view):
        engine.observe_reconciliation(task['task_id'],None,'原 COMMON 结果需要对账，不重复提交',action_id=action['action_id'])
        return False
    common=view.get('common') or {}
    if _verified_binding(task,common,profile)!=binding:raise ValueError('COMMON 审核期间冻结计划或任务范围已变化')
    if common.get('status')=='READY_TO_SYNC':
        plan=common.get('plan') or {}
        if plan.get('status')!='APPROVED' or not plan.get('approval'):return False
        admission=_common_technical_admission(binding,plan,server=server)
        if admission.get('status')!='READY' or not admission.get('receipt_digest'):return False
        evidence=admission['receipt_digest']
    elif common.get('status')=='VERIFIED':
        diagnostic=_post_common_readiness(server,task,view)
        if diagnostic.get('status')!='READY' or not diagnostic.get('receipt_digest'):return False
        evidence=diagnostic['receipt_digest']
    elif common.get('status') in {'APPROVAL_REQUIRED','TECHNICAL_CONDITIONS_UNKNOWN','RETAINED_TECHNICAL_BASELINE'}:
        diagnostic=_native_request_readiness(server,task,view)
        if diagnostic.get('status')!='READY' or not diagnostic.get('receipt_digest'):return False
        evidence=diagnostic['receipt_digest']
    else:return False
    receipt={'receipt_id':'common-ready:'+_digest([binding,common.get('status'),evidence]),'action_id':action['action_id']}
    def verify(value,expected):
        current=_read(server,task);latest=current.get('common') or {}
        if value!=receipt or expected!=binding or _unknown(current) or _verified_binding(task,latest,profile)!=binding:
            return False
        if latest.get('status')!=common.get('status'):return False
        if latest.get('status')=='READY_TO_SYNC':
            latest_plan=latest.get('plan') or {}
            if latest_plan.get('status')!='APPROVED' or not latest_plan.get('approval'):return False
            latest_diagnostic=_common_technical_admission(binding,latest_plan,server=server)
        elif latest.get('status')=='VERIFIED':
            latest_diagnostic=_post_common_readiness(server,task,current)
        else:
            latest_diagnostic=_native_request_readiness(server,task,current)
        return latest_diagnostic.get('status')=='READY' and latest_diagnostic.get('receipt_digest')==evidence
    return engine.accept_domain_receipt(task['task_id'],receipt,verify)
