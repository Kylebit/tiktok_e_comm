"""Project one explicit native decision onto its original durable owner task.

This never claims a worker lease, scans runnable tasks, publishes, or invents a
readback. Original target rows remain the result authority. The task's original
R1 owner event and current immutable native source must agree before any event.
"""
import json
import hashlib


def project_explicit_decision_progress(runtime,decision_id,phase):
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import workbench_publication_native as native
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform.final_review_server_admission import ApprovalBlocked
    if type(runtime) is not NativeSoleFinalService or phase not in {'QUEUED','RUNNING','RECORDED'}:
        raise ApprovalBlocked('NATIVE_TASK_PROGRESS_SERVICE_REQUIRED')
    operations=runtime._operations
    if operations is None:return {'status':'TASK_CONTEXT_UNAVAILABLE'}
    engine,profile=operations.engine,operations.profile
    if type(engine) is not WorkbenchEngine:
        raise ApprovalBlocked('NATIVE_TASK_ENGINE_REQUIRED')
    with runtime.operation() as service:
        owner=service._actor()['owner_sid']
        with runtime.store._connect_readonly() as db:
            db.execute('BEGIN')
            receipt=service._decision(db,decision_id,owner_sid=owner)
            row=db.execute('SELECT plan_id,review_json FROM native_sole_final_decisions WHERE decision_id=?',
                           (decision_id,)).fetchone()
            review=json.loads(row['review_json'])
            plan=db.execute('SELECT product_id,payload_json,payload_digest FROM release_plans WHERE plan_id=?',
                            (row['plan_id'],)).fetchone()
            run_id='release-run:'+plan['payload_digest'][:24]
            market_run=(runtime.store._run_in_transaction(db,run_id)
                if db.execute('SELECT 1 FROM release_runs WHERE run_id=?',(run_id,)).fetchone() else None)
        state=workbench.load_state(plan['product_id'])
        reference=(state.get('product_approval') or {}).get('round1_prepared_reference')
        if not reference:raise ApprovalBlocked('NATIVE_TASK_PREPARATION_REFERENCE_MISSING')
        # This is an origin lookup for the explicitly decided immutable source,
        # not a queue scan or replay of another task.
        with engine.transaction() as connection:
            origin=connection.execute(
                'SELECT e.task_id FROM workbench_events e JOIN workbench_execution x ON x.task_id=e.task_id '
                "WHERE e.event_type='checkpoint_saved' AND x.template='publication' "
                "AND json_extract(e.detail_json,'$.checkpoint.native_preparation.prepared_reference')=? "
                "AND json_extract(x.scope_json,'$.offer_id')=? ORDER BY e.id LIMIT 1",
                (reference,plan['product_id'])).fetchone()
            if origin is None:raise ApprovalBlocked('NATIVE_TASK_ORIGINAL_OWNER_MISSING')
            stored=engine._row(connection,origin['task_id'])
            engine._require_version(stored)
            task=engine._project(connection,stored)
            identity=engine._review_identity(connection,origin['task_id'])
        frozen=native.read_frozen(task,profile)
        if (frozen is None or frozen['prepared_reference']!=reference
                or frozen['snapshot_digest']!=review['round1_digest']
                or tuple(label for label in frozen['targets'] if label!='miaoshou:COMMON')!=tuple(sorted(review['targets']))):
            raise ApprovalBlocked('NATIVE_TASK_DECISION_SOURCE_CHANGED')
        if task['current_step'] not in {'release','readback'}:
            return {'status':'ORIGINAL_TASK_STEP_NOT_APPLICABLE','task_id':task['task_id']}
        # Preserve the existing single-final-review action/nonce ownership
        # contract where that task was enrolled; a native decision is verified
        # from storage instead of being renamed to a private approval receipt.
        if phase=='QUEUED' and identity['review_mode']=='single-final-review/v1' and task.get('required_action'):
            action=task['required_action'];binding=action.get('receipt_binding') or {}
            def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            incoming={'receipt_id':f'final-review:{decision_id}:{task["task_id"]}:{action["action_id"]}',
                'task_id':task['task_id'],'action_id':action['action_id'],'generation':binding['generation'],
                'step_index':2,'binding_digest':hashlib.sha256(canonical(binding).encode()).hexdigest(),
                'decision_id':decision_id,'contract_digest':receipt['review_digest'],
                'common_plan_id':review['common_plan_id'],'preview_digest':review['candidate_digest']}
            def verify(value,original):
                with runtime.store._connect_readonly() as db:
                    db.execute('BEGIN');actual=service._decision(db,decision_id,owner_sid=owner)
                return (value['decision_id']==actual['decision_id']
                    and value['contract_digest']==actual['review_digest']
                    and original.get('offer_id')==review['offer_id']
                    and original.get('round1_digest')==review['round1_digest']
                    and original.get('round2_digest')==review['round2_digest']
                    and original.get('common_plan_id')==review['common_plan_id']
                    and original.get('preview_digest')==review['candidate_digest']
                    and sorted(original.get('targets') or [])==sorted(review['targets']))
            engine.accept_final_review_receipt(task['task_id'],incoming,verify)
            with engine.transaction() as connection:
                stored=engine._row(connection,task['task_id'])
                task=engine._project(connection,stored)
        targets=(market_run or {}).get('targets') or []
        from modules.products import server
        unknown=any(target['status'] in {'RUNNING','RECONCILIATION_REQUIRED'} or (
            target['status']=='FAILED' and target['attempts']>0
            and not server._generic_tiktok_safe_retry_target(target)) for target in targets)
        all_verified=bool(targets) and all(row['status']=='SUCCEEDED' and row.get('readback') for row in targets)
        completion_checkpoint=None
        if all_verified:
            from shared_platform import operations_publication
            completion_checkpoint=operations_publication._native_receipt(task,profile)
            from shared_platform.workbench_publication_adapter import _release_receipt
            from modules.products import server
            code,display=server._publication_stages_for_request({'offer_id':review['offer_id'],'plan_id':row['plan_id']})
            if (code!=200 or completion_checkpoint is None
                    or _release_receipt(display,{'offer_id':review['offer_id'],'targets':list(review['targets'])},require_readback=True) is None):
                raise ApprovalBlocked('NATIVE_TASK_OFFICIAL_COMPLETION_UNVERIFIED')
        label=('平台结果待核对' if unknown else '平台结果已回读' if all_verified else
               '正在执行原已批准任务' if phase in {'QUEUED','RUNNING'} else '等待平台结果')
        action={'kind':'observe','action_id':'native-final:'+decision_id,'label':label,
            'reason':'已保存唯一终审决定，系统沿原任务自动重核；不重复请求批准或未知写入。',
            'url':f'/product-workspace?offer_id={plan["product_id"]}&round=final',
            'receipt_binding':{'adapter':'publication-native/v1','step':task['current_step'],'native_r1':frozen}}
        with engine.transaction() as connection:
            current=engine._row(connection,task['task_id']);engine._require_version(current)
            if (current['checkpoint_json']!=stored['checkpoint_json']
                    or current['action_json']!=stored['action_json'] or current['step_index']!=stored['step_index']):
                raise ApprovalBlocked('NATIVE_TASK_PROGRESS_COMPARE_AND_SWAP_CHANGED')
            if (current['worker'] or current['external_started']
                    or connection.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>'completed'",(task['task_id'],)).fetchone()):
                raise ApprovalBlocked('NATIVE_TASK_ACTIVE_OWNER_REQUIRES_RECONCILIATION')
            checkpoint=json.loads(current['checkpoint_json'])
            checkpoint['native_final_decision']={'decision_id':decision_id,
                'approval_id':receipt['release_approval_id'],'plan_id':row['plan_id'],
                'phase':phase,'official_readback_complete':all_verified,'outcome_unknown':unknown,
                'target_results':[{'target_label':target['target_label'],'status':target['status'],
                                   'attempts':target['attempts']} for target in targets]}
            connection.execute('UPDATE workbench_execution SET action_json=?,checkpoint_json=? WHERE task_id=?',
                (json.dumps(action,ensure_ascii=False,sort_keys=True,separators=(',',':')),
                 json.dumps(checkpoint,ensure_ascii=False,sort_keys=True,separators=(',',':')),task['task_id']))
            engine._state(connection,task['task_id'],'reconciliation_required' if unknown else 'waiting_domain',
                '原平台操作结果未知，保留原尝试；系统只读核对，不重复写入。' if unknown else '')
            engine._event(connection,task['task_id'],'domain_observation_pending',action)
            engine._event(connection,task['task_id'],'native_final_execution_progress',checkpoint['native_final_decision'])
            if completion_checkpoint is not None:
                steps=json.loads(current['steps_json'])
                result_url=action['url']
                for step in steps[current['step_index']:]:
                    if step['key'] not in {'release','readback'}:
                        raise ApprovalBlocked('NATIVE_TASK_TERMINAL_STEPS_CHANGED')
                    step.update(state='completed',checkpoint=completion_checkpoint,
                        completion_digest=hashlib.sha256(json.dumps([completion_checkpoint,result_url],
                            ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())
                    engine._event(connection,task['task_id'],'step_completed',
                        {'step':step['key'],'checkpoint':completion_checkpoint})
                connection.execute('UPDATE workbench_execution SET steps_json=?,step_index=?,action_json=NULL,result_url=? WHERE task_id=?',
                    (json.dumps(steps,ensure_ascii=False,sort_keys=True,separators=(',',':')),len(steps),result_url,task['task_id']))
                engine._state(connection,task['task_id'],'completed')
                connection.execute('DELETE FROM workbench_resource_locks WHERE task_id=?',(task['task_id'],))
        return {'status':'ORIGINAL_TASK_UPDATED','task_id':task['task_id'],'phase':phase}
