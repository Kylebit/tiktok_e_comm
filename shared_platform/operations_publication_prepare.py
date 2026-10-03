"""Prepare actual first-round facts for technical adoption or missing-input handling.

The controlled agent may prepare local facts and official-read candidates. Only
the existing round1_workspace producer can declare a complete review packet.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from shared_platform import workbench_publication_native as native


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _current(task,profile):
    preparation=native.prepare_facts(task,profile,prepare_review=False)
    packet=json.loads(Path(preparation['review_path']).read_text(encoding='utf-8'))
    return preparation,packet


def _review(task,profile,preparation,packet):
    blockers=packet.get('blockers')
    if (not isinstance(blockers,list) or any(code!='CATEGORY_RECEIPT_UNAVAILABLE' for code in blockers)
            or packet.get('status') not in {'DECISION_REQUIRED','FIRST_REVIEW_READY'}):
        return None
    from shared_platform import round1_workspace
    module=round1_workspace._module(native._server(profile))
    try:
        image_plan,_,_=round1_workspace._image_input(task['scope']['offer_id'])
        # Missing-plan placeholders belong to facts preparation, not adoption.
        if image_plan is None:
            return None
        module._safe_image_execution_plan(image_plan)
    except (round1_workspace.Round1WorkspaceError,module.PreparationError):
        return None
    result=native._prepare_workspace_review(task,profile,preparation)
    review=result.get('round1_prepared_review') or {}
    actual=review.get('packet') or {}
    if (not result.get('prepared_reference') or review.get('prepared_reference')!=result['prepared_reference']
            or review.get('status')!='PREPARED' or actual.get('status')!='FIRST_REVIEW_READY'
            or actual.get('blockers') or actual.get('offer_id')!=task['scope']['offer_id']
            or sorted(actual.get('target_selection',{}).get('requested') or [])!=sorted(task['scope']['shops'])):
        return None
    return result


def run(engine,task,token,profile,bridge=None, *, filesystem_boundary=None):
    """Return False only for an already approved R1 handled by native.run."""
    if task['current_step']!='facts':
        return False
    if native.read_frozen(task,profile):
        return False
    task_id=task['task_id']
    previous=(task.get('checkpoint') or {}).get('facts_attempt') or {}
    if previous and previous.get('state')!='returned':
        from shared_platform.native_parent_facts import NativeParentFactsAdapter
        from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
        if (type(bridge) is not NativeParentFactsAdapter
                or type(filesystem_boundary) is not NativeR1FilesystemBoundary
                or not filesystem_boundary.verified_for(engine,task,token,profile)):
            engine.observe_reconciliation(task_id,token,'上一轮商品准备会话结果未知，请核对原会话后恢复，不重复启动')
            return True
        events=[event for event in reversed(engine.store.events(task_id)) if event['event_type']=='input_provided']
        if previous.get('input_digest',_digest([])) != _digest(events):
            engine.observe_reconciliation(task_id,token,'原商品准备输入已变化，保留原会话结果，不重复执行')
            return True
        result=bridge.recover_facts(task,Path(previous['output']),
            [event['detail']['note'] for event in events],lease_token=token)
        previous={**previous,'state':'returned' if result.get('status')=='prepared' else 'unknown','result':result}
        engine.record_checkpoint(task_id,token,{**task['checkpoint'],'facts_attempt':previous})
        if result.get('status')!='prepared':
            engine.observe_reconciliation(task_id,token,result.get('reason') or '原商品准备结果尚未证实，不重复执行')
            return True
    preparation,packet=_current(task,profile)
    review=_review(task,profile,preparation,packet)
    events=[event for event in reversed(engine.store.events(task_id)) if event['event_type']=='input_provided']
    fingerprint=_digest(packet);notes_digest=_digest(events)
    if not review:
        if bridge is None:
            engine.fail(task_id,token,'首轮商品事实仍需执行者准备，当前受控事实执行器未连接；尚不是待用户审核状态')
            return True
        if (not previous or previous.get('inputs')!=fingerprint or previous.get('input_digest',_digest([]))!=notes_digest):
            number=1+int(previous.get('number',0))
            output=profile.data_root/'artifacts'/task_id/('facts-'+str(number))
            attempt={'state':'started','number':number,'output':str(output),'inputs':fingerprint,'input_digest':notes_digest}
            engine.record_checkpoint(task_id,token,{'native_preparation':preparation,'facts_attempt':attempt})
            result=bridge.execute_facts(task,output,[event['detail']['note'] for event in events],
                                        lease_token=token)
            attempt.update(state='unknown' if result.get('status')=='unknown' else 'returned',result=result)
            engine.record_checkpoint(task_id,token,{'native_preparation':preparation,'facts_attempt':attempt})
            if result.get('status')=='unknown':
                engine.observe_reconciliation(task_id,token,result.get('reason') or '商品准备会话结果未知')
                return True
            if result.get('status')=='observed':
                engine.observe_reconciliation(task_id,token,
                    'R1 事实子任务仅完成只读观察；父进程安全落盘尚未实现，不能进入审核')
                return True
            if result.get('status')!='prepared':
                engine.fail(task_id,token,result.get('reason') or '商品准备执行失败')
                return True
            preparation,packet=_current(task,profile)
            attempt['inputs']=_digest(packet)
            engine.record_checkpoint(task_id,token,{'native_preparation':preparation,'facts_attempt':attempt})
            previous=attempt
        result=previous.get('result') or {}
        if result.get('status')!='prepared':
            engine.fail(task_id,token,result.get('reason') or '首轮准备原执行未成功，请修复技术问题')
            return True
        missing=result.get('result',{}).get('missing_inputs') or []
        if missing:
            engine.wait_for_user(task_id,token,kind='input',label='补充商品准备资料',reason='；'.join(str(item) for item in missing),
                url='/product-workspace?offer_id='+task['scope']['offer_id']+'&round=first#originalPublicationReview')
            return True
        review=_review(task,profile,preparation,packet)
        if not review:
            engine.fail(task_id,token,'商品执行器返回后仍只有未完成的首轮资料，尚无完整审核包；请执行者继续修复准备结果')
            return True
    from shared_platform import operations_runtime, publication_autopilot, publication_rounds, round1_workspace
    checkpoint={'native_preparation':review}
    if previous:checkpoint['facts_attempt']=previous
    try:
        policy=publication_autopilot.load_autopilot_policy()
        if (policy.get('status')!='ACTIVE'
                or policy.get('review_contract',{}).get('intermediate_human_approval_required') is not False):
            raise ValueError('R1_AUTO_POLICY_UNAVAILABLE')
        release={'code_version':profile.version,'environment':profile.environment,
                 'manifest_digest':profile.manifest_digest}
        intent={'schema_version':'r1-task-technical-intent/v1','task_id':task_id,
                'offer_id':task['scope']['offer_id'],'targets':sorted(task['scope']['shops']),
                'prepared_reference':review['prepared_reference'],'release':release,
                'policy_digest':publication_rounds.canonical_digest(policy),
                'review_digest':review['round1_prepared_review']['review_digest']}
        intent['intent_digest']=publication_rounds.canonical_digest(intent)
        checkpoint['r1_auto_intent']=intent
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        engine.record_checkpoint(task_id,token,checkpoint)
        engine.observe_reconciliation(task_id,token,'R1 技术政策或准备身份不可用：'+str(error))
        return True
    # The durable intent/checkpoint must precede the domain's technical CAS.
    engine.record_checkpoint(task_id,token,checkpoint)
    from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
    pinned = (type(filesystem_boundary) is NativeR1FilesystemBoundary
              and filesystem_boundary.verified_for(engine,task,token,profile))
    if not pinned and not operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED:
        engine.observe_reconciliation(task_id,token,
            'R1 Windows 文件系统隔离尚未验证，已停止技术采纳；不请求中间人工审核')
        return True
    try:
        current=engine.get(task_id)
        if (current['version']!=release or current['version']!=engine.release
                or current['checkpoint']!=checkpoint):
            raise native.NativeR1ReconciliationRequired('R1_TASK_RELEASE_OR_CHECKPOINT_CHANGED')
        request={'offer_id':task['scope']['offer_id'],
                 'prepared_reference':review['prepared_reference'],
                 'policy_digest':intent['policy_digest']}
        result=round1_workspace.auto_freeze(native._server(profile),request)
        if (result.get('status')!='FROZEN' or result.get('human_approval') is not False
                or result.get('external_write_count')!=0 or result.get('persisted_readback') is not True
                or result.get('offer_id')!=request['offer_id']
                or result.get('prepared_reference')!=request['prepared_reference']):
            raise native.NativeR1ReconciliationRequired('R1_AUTO_FREEZE_READBACK_INCOMPLETE')
        frozen=native.read_frozen(engine.get(task_id),profile)
        if (not frozen or frozen['snapshot_digest']!=result['snapshot']['snapshot_digest']
                or frozen['prepared_reference']!=request['prepared_reference']):
            raise native.NativeR1ReconciliationRequired('R1_TASK_FREEZE_IDENTITY_CONFLICT')
    except (native.NativeR1ReconciliationRequired, round1_workspace.Round1WorkspaceError,
            ValueError, OSError, KeyError, TypeError) as error:
        engine.observe_reconciliation(task_id,token,'R1 技术采纳需要对账：'+str(error))
        return True
    engine.bind_scope(task_id,token,{**current['scope'],'skus':[frozen['internal_sku']]})
    engine.complete_step(task_id,token,expected_step='facts',checkpoint={'native_r1':frozen})
    return True


def recover_technical(engine, task, profile, *, filesystem_boundary=None):
    """Once per worker start, recover only the original durable R1 intent."""
    from shared_platform import operations_runtime, publication_rounds, round1_workspace
    from shared_platform.immutable_approval_files import require_local_path

    from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
    pinned=(type(filesystem_boundary) is NativeR1FilesystemBoundary
            and filesystem_boundary.verified_for(engine,task,None,profile))
    if not pinned and not operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED:
        return False
    record = engine.read_r1_technical_recovery(task['task_id'])
    original, intent = record['task'], record['intent']
    profile_release={'code_version':profile.version,'environment':profile.environment,
                     'manifest_digest':profile.manifest_digest}
    if (original != task or original['version'] != engine.release
            or original['version'] != profile_release
            or original['scope']['offer_id'] != intent['offer_id']):
        raise native.NativeR1ReconciliationRequired('R1_RECOVERY_TASK_CHANGED')
    try:
        server = native._server(profile)
        document = round1_workspace.read_preparation(intent['offer_id'], intent['prepared_reference'])
        if (document['scope']['offer_id'] != intent['offer_id']
                or sorted(document['scope']['requested_targets']) != intent['targets']
                or publication_rounds.canonical_digest(document['packet']) != intent['review_digest']):
            raise native.NativeR1ReconciliationRequired('R1_RECOVERY_PREPARATION_CHANGED')
        directory = native._report_dir(profile, intent['offer_id'])
        decision_path = directory / 'round1-auto-decision.json'
        require_local_path(decision_path, root=publication_rounds.REPORTS_ROOT)
        decision = publication_rounds.validate_round1_auto_decision(
            json.loads(decision_path.read_text(encoding='utf-8')), document['packet'])
        if decision.get('policy_digest') != intent['policy_digest']:
            raise native.NativeR1ReconciliationRequired('R1_RECOVERY_POLICY_DIGEST_CHANGED')
        result = round1_workspace.auto_freeze(server, {
            'offer_id': intent['offer_id'], 'prepared_reference': intent['prepared_reference'],
            'policy_digest': intent['policy_digest']})
        frozen = native.read_frozen(original, profile)
        if (result.get('status') != 'FROZEN' or result.get('persisted_readback') is not True
                or result.get('human_approval') is not False
                or result.get('external_write_count') != 0 or not frozen
                or frozen['snapshot_digest'] != result['snapshot']['snapshot_digest']
                or frozen['prepared_reference'] != intent['prepared_reference']):
            raise native.NativeR1ReconciliationRequired('R1_RECOVERY_READBACK_CONFLICT')
        engine.resume_r1_technical_recovery(task['task_id'],
            intent_digest=intent['intent_digest'], frozen=frozen)
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        engine.record_r1_technical_recovery_blocked(task['task_id'],
            intent_digest=intent['intent_digest'], code=type(error).__name__)
        return False
