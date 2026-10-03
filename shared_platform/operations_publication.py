"""Connect native preparation, controlled image work and original domain readback."""
import hashlib
import json
from pathlib import Path
from shared_platform import workbench_publication_native as native
from shared_platform import workbench_publication_adapter as registered
from shared_platform.publication_r2_review import has_registration


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _input_events(engine, task_id):
    return [event for event in reversed(engine.store.events(task_id)) if event['event_type']=='input_provided']


def _image_inputs(task,profile):
    directory=Path(profile.root)/'reports/product-preparation'/task['scope']['offer_id']
    return _digest([(name,hashlib.sha256((directory/name).read_bytes()).hexdigest()) for name in
                    ('brand-image-generation.json','brand-image-translation-plan.json','localized-image-review.json') if (directory/name).is_file()])


def _url(task,step):
    return '/product-workspace?offer_id='+task['scope']['offer_id']+'&round='+('images' if step=='images' else 'final')+'#originalPublicationReview'


def _native_receipt(task,profile):
    images=native.read_images(task,profile)
    adopted=next(((step.get('checkpoint') or {}).get('native_r2') for step in task.get('steps',[]) if step.get('key')=='images'),None)
    if adopted is not None and images['native_r2']!=adopted:
        raise ValueError('completed native image identity changed')
    if task['current_step']=='images':return images
    identity={**images['native_r1'],'targets':[target for target in images['native_r1']['targets'] if target!='miaoshou:COMMON']}
    if not identity['targets']:
        raise ValueError('native marketplace target scope is empty')
    release=registered._read_release(identity['offer_id'],profile)
    receipt=registered._release_receipt(release,identity,require_readback=task['current_step']=='readback')
    previous=next(((step.get('checkpoint') or {}).get('release_receipt') for step in task.get('steps',[]) if step.get('key')=='release'),None)
    if previous and receipt and any(previous.get(key)!=receipt.get(key) for key in ('plan_id','approval_id','candidate_digest')):
        raise ValueError('native final release identity changed before readback')
    return {**images,'release_receipt':receipt} if receipt else None


def _registered(task, profile):
    checkpoints=[task.get('checkpoint') or {},*[step.get('checkpoint') or {} for step in task.get('steps',[])]]
    binding=((task.get('required_action') or task.get('pending_observation') or {}).get('receipt_binding') or {}).get('adapter')
    if binding=='publication-native/v1' or any('native_r1' in c or 'native_preparation' in c for c in checkpoints):return False
    if binding=='publication/v1' or any('publication_identity' in c for c in checkpoints):return True
    return has_registration(task['scope'].get('offer_id'),runtime_root=profile.root)


def bindings(bridge=None, *, filesystem_boundary=None, image_bridge=None):
    def images(engine,task,token,profile):
        if native.read_frozen(task,profile) is None:
            raise ValueError('the approved first-round snapshot is missing or withdrawn')
        try: receipt=native.read_images(task,profile)
        except FileNotFoundError: receipt=None
        if receipt:
            engine.complete_step(task['task_id'],token,expected_step='images',checkpoint=receipt)
            return
        selected_images = image_bridge if image_bridge is not None else bridge
        if selected_images is None or not callable(getattr(selected_images,'execute_images',None)):
            engine.fail(task['task_id'],token,'图片 Skill 的受控执行器未配置，需要部署者连接当前执行器')
            return
        from shared_platform.native_parent_images import NativeParentImagesAdapter
        if type(selected_images) is NativeParentImagesAdapter and (
                selected_images.engine is not engine or selected_images.profile is not profile
                or selected_images.boundary is not filesystem_boundary):
            raise ValueError('R2_PARENT_IMAGES_ADAPTER_CONFLICT')
        previous=task.get('checkpoint',{}).get('image_attempt') or {}
        fingerprint=_image_inputs(task,profile)
        input_events=_input_events(engine,task['task_id'])
        input_digest=_digest(input_events)
        new_input=bool(previous) and previous.get('input_digest',_digest([]))!=input_digest
        if previous and previous.get('state')!='returned':
            from shared_platform.native_parent_images import NativeParentImagesAdapter
            if type(selected_images) is NativeParentImagesAdapter:
                result=selected_images.recover_images(task,token=token)
                if result['status']=='prepared':
                    receipt=native.read_images(task,profile)
                    engine.complete_step(task['task_id'],token,expected_step='images',checkpoint=receipt)
                    return
            engine.observe_reconciliation(task['task_id'],token,'上一轮图片调用结果未知，核对原会话和付费使用量后恢复')
            return
        if previous and previous.get('inputs')==fingerprint and not new_input:
            result=previous.get('result') or {}
            if result.get('status')!='prepared' or not result.get('result',{}).get('missing_inputs'):
                engine.fail(task['task_id'],token,result.get('reason') or '上一轮图片输出未通过域校验，需要执行者修复，不能伪装待用户审核')
                return
        if not previous or previous.get('inputs')!=fingerprint or new_input:
            number=1+int(previous.get('number',0))
            output=profile.data_root/'artifacts'/task['task_id']/('images-'+str(number))
            attempt={'state':'started','inputs':fingerprint,'input_digest':input_digest,'number':number,'output':str(output)}
            engine.record_checkpoint(task['task_id'],token,{'image_attempt':attempt})
            task=engine.get(task['task_id'])
            notes=[e['detail']['note'] for e in input_events]
            from shared_platform.native_parent_images import NativeParentImagesAdapter
            if type(selected_images) is NativeParentImagesAdapter:
                result=selected_images.execute_images(task,output,notes,token=token)
            else:
                result=selected_images.execute_images(task,output,notes)
            attempt.update(state='returned' if result['status']!='unknown' else 'unknown',result=result,inputs=_image_inputs(task,profile))
            engine.record_checkpoint(task['task_id'],token,{'image_attempt':attempt})
            task=engine.get(task['task_id'])
            if result['status']=='unknown':
                engine.observe_reconciliation(task['task_id'],token,result['reason']);return
            if result['status']!='prepared':
                engine.fail(task['task_id'],token,result['reason']);return
            try: receipt=native.read_images(task,profile)
            except FileNotFoundError: receipt=None
            if receipt:
                engine.complete_step(task['task_id'],token,expected_step='images',checkpoint=receipt);return
            if not result['result']['missing_inputs']:
                engine.fail(task['task_id'],token,'图片执行返回后仍没有有效 R2 业务回执，需要执行者修复并核对原任务');return
            previous=attempt
        returned=previous.get('result') or {}
        missing=returned.get('result',{}).get('missing_inputs') or []
        kind='review' if returned.get('required_action_kind')=='image_review' else 'input'
        engine.wait_for_user(task['task_id'],token,kind=kind,label='审核商品图片' if kind=='review' else '补充图片处理资料',
            reason='；'.join(str(item) for item in missing) or '在原图片阶段完成当前图片选择；保存后的域回执会唤醒后续步骤。',url=_url(task,'images'),
            receipt_binding={'adapter':'publication-native/v1','step':'images','inputs':previous['inputs'],'native_r1':native.read_frozen(task,profile)})

    def later(engine,task,token,profile):
        try: receipt=_native_receipt(task,profile)
        except registered.PublicationReconciliationRequired as error:
            engine.observe_reconciliation(task['task_id'],token,str(error));return
        if receipt:
            engine.complete_step(task['task_id'],token,expected_step=task['current_step'],checkpoint=receipt,result_url=_url(task,'final') if task['current_step']=='readback' else '')
            return
        if task['current_step']=='release':
            from shared_platform import operations_publication_common as common
            if not common.run(engine,task,token,profile):return
        binding={'adapter':'publication-native/v1','step':task['current_step'],'native_r1':native.read_frozen(task,profile)}
        if task['current_step']=='readback':
            engine.await_domain(task['task_id'],token,label='等待平台结果',reason='已提交操作等待正式平台回读，不重复执行。',url=_url(task,'final'),receipt_binding=binding)
        else:
            engine.wait_for_user(task['task_id'],token,kind='review',label='审核最终发布包',reason='在原最终审核页核对准确商品和店铺范围，沿用已有批准与逐平台执行回执。',url=_url(task,'final'),receipt_binding=binding)

    def observe_later(engine,task,profile):
        action=task.get('required_action') or task.get('pending_observation') or {}
        frozen=action.get('receipt_binding') or {}
        if native.read_frozen(task,profile)!=frozen.get('native_r1'):
            raise ValueError('publication first-round identity changed while waiting')
        try: receipt=_native_receipt(task,profile)
        except registered.PublicationReconciliationRequired as error:
            engine.observe_reconciliation(task['task_id'],None,str(error),action_id=action['action_id']);return False
        except FileNotFoundError: receipt=None
        changed=task['current_step']=='images' and _image_inputs(task,profile)!=frozen.get('inputs')
        if not receipt and not changed:return False
        value={'receipt_id':'native-stage:'+_digest([frozen,receipt,_image_inputs(task,profile)]),'action_id':action['action_id'],'receipt':receipt}
        def verify(incoming,binding):
            if binding!=frozen or native.read_frozen(task,profile)!=frozen.get('native_r1'):return False
            if incoming['receipt'] is not None:return _native_receipt(task,profile)==incoming['receipt']
            return task['current_step']=='images' and _image_inputs(task,profile)!=binding['inputs']
        return engine.accept_domain_receipt(task['task_id'],value,verify)

    def run(engine,task,token,profile):
        if _registered(task,profile):
            if task['current_step']=='release':
                from shared_platform import operations_publication_common as common
                if not common.run(engine,task,token,profile):return
            return registered.run(engine,task,token,profile)
        if task['current_step']=='facts':
            from shared_platform import operations_publication_prepare as preparation
            try:
                if filesystem_boundary is not None:
                    if filesystem_boundary.run(engine,task,token,profile,facts_adapter=bridge):return
                elif preparation.run(engine,task,token,profile,bridge=bridge):return
                return native.run(engine,task,token,profile,image_stage=images,later_stage=later)
            except native.NativeR1ReconciliationRequired as error:
                engine.observe_reconciliation(task['task_id'],token,str(error))
                return
        return native.run(engine,task,token,profile,image_stage=images,later_stage=later)

    def observe(engine,task,profile):
        if task['template']!='publication':return False
        if (task['execution_state']=='reconciliation_required'
                and (task.get('checkpoint') or {}).get('r1_auto_intent')):
            from shared_platform import operations_publication_prepare as preparation
            return preparation.recover_technical(engine,task,profile)
        from shared_platform import operations_publication_common as common
        common_result=common.observe(engine,task,profile)
        if common_result is not None:return common_result
        if _registered(task,profile):return registered.observe(engine,task,profile)
        return native.observe(engine,task,profile,later_observer=observe_later)
    return run,observe
