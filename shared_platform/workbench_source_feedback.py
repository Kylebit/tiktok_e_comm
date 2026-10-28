"""Read-only allowlisted producer receipts -> single coordinator observation writer.

No imports of business runtime, no SQL, auth, provider request, or task dispatch.
Source receipt clocks remain source clocks; observer heartbeat is separate.
"""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,re,stat,uuid

from shared_platform.workbench_observation_updates import update_observation
from shared_platform.workbench_observations import _time

WORKS={'product-4039931504':'01a1066a-bdd4-77ad-b3d7-8750d0d08021',
       'product-engineering-continuation':'01a101b6-0ab6-72cb-9a61-d557641ba023',
       'supply-completion':'01a10a18-a6f2-75ca-ba3c-8e6583aa1002'}

SOURCE_FILENAMES = {'engineering': 'ACTUAL_INDEPENDENT_RECEIVER.json',
                    'supply': 'handoff-receipt.json'}
SOURCE_NAMES = {'engineering': {'ACTUAL_INDEPENDENT_RECEIVER.json'},
                'supply': {'handoff-receipt.json', 'DELIVERY_RECEIPT.json'}}
SOURCE_LANES = ('product_business', 'engineering', 'supply')


def local_file(path):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith('\\\\'):
        raise ValueError('explicit local absolute receipt path required')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('reparse receipt path rejected')
        if part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('reparse receipt path rejected')
    return path


def load_profile(path):
    """Only coordinator-selected producer files, never paths within receipts."""
    data = json.loads(local_file(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema') != 'orbit-source-feedback-profile/v1':
        raise ValueError('unsupported feedback profile')
    declared = data.get('sources')
    if not isinstance(declared, dict) or set(declared) - set(SOURCE_LANES):
        raise ValueError('unrecognized source lane')
    sources = {}
    for kind, item in declared.items():
        if item is None:
            continue
        if kind == 'product_business':
            raise ValueError('no verified business receipt adapter; use dated coordinator update')
        if not isinstance(item, dict) or set(item) != {'path'}:
            raise ValueError('producer source must be an explicit receipt path')
        source = local_file(item['path'])
        if source.name not in SOURCE_NAMES[kind]:
            raise ValueError('producer receipt filename is not allowlisted')
        sources[kind] = source
    return sources

def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.parent/('.tmp-'+uuid.uuid4().hex[:12])
    pending.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(pending,path)

def source_events(kind,raw):
    data=json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('producer receipt must be an object')
    if kind=='engineering':
        if (data.get('schema_version')!='actual-independent-native-receiver-continuation/v1'
            or data.get('workspace_offer_id')!='4039931504'
            or data.get('supplier_source_offer_id')!='1050277205528'
            or data.get('status')!='ORIGINAL_INDEPENDENT_PRIVATE_INTAKE_AND_OWNER_CHECKPOINT_VERIFIED'
            or data.get('private_task_not_formal') is not True
            or data.get('actual_formal_business_registration') is not False
            or type(data.get('shared_business_writes')) is not int
            or data.get('shared_business_writes')!=0
            or not re.fullmatch(r'[0-9a-f]{40}', str(data.get('source_head', '')))):
            raise ValueError('unsupported or changed product receipt; coordinator verification required')
        return data['as_of_utc'],{
          'product-4039931504':{
            'stage':'准备未完成；终审候选未形成',
            'last_reported_state':'business_preparation_incomplete_receiver_fix_available',
            'blocker':'采用接收库修复，继续核定入档与类目。',
            'user_decision':None,'closed':False,
            'evidence_notes':['原业务接收库问题的修复已在独立真实副本贯通；仅为工程交付，正式商品未登记、终审候选未形成。','工程回执时间 '+data['as_of_utc']+'；不以测试数量提升业务阶段。','历史付费用量与剩余额度仍为 UNKNOWN；此记录不授予业务写入或付费权限。']},
          'product-engineering-continuation':{
            'stage':'接收库修复已交付；正式未采用',
            'last_reported_state':'independent_receiver_fix_delivered',
            'blocker':'将接收库修复交给原商品任务接续。',
            'closed':False,'user_decision':None,
            'evidence_notes':['候选 '+data['source_head']+'；只在完整原数据库副本中验证。','私有任务 '+data['private_task_id']+' 不属正式队列；正式业务登记为 false。','原输入保留，费用与真实类目仍未解决。']}}
    if kind=='supply':
        if data.get('schema') == 'retained-original-supply-delivery/v1':
            if (data.get('state') != 'DELIVERED_PRIVATE_ORIGINAL_HANDLER_CANDIDATE_NOT_FORMAL'
                or not re.fullmatch(r'[0-9a-f]{40}', str(data.get('sourceHead', '')))
                or data.get('formalActivation') is not False
                or data.get('seayaAtomicSnapshot') is not False
                or data.get('exportRows') != 47
                or data.get('mappingExceptionCount') != 1
                or data.get('mappingExceptionCountry') != 'MY'
                or data.get('seayaGlobalInventoryPages') != [100, 8]
                or data.get('seayaGlobalInboundPages') != [38]
                or any(type(data.get(key)) is not int or data[key] != 0 for key in
                       ('businessWrites', 'databaseWrites', 'procurementActions', 'stockActions', 'authWrites', 'networkProviderReads'))):
                raise ValueError('changed original-page delivery requires coordinator verification')
            return data['recordedAt'], {'supply-completion': {
                'stage': '原页补货候选已交付；正式未采用',
                'last_reported_state': 'original_supply_candidate_delivered_not_formal',
                'blocker': '核验组合原页与数据准入，准备统一正式采用。',
                'closed': False, 'user_decision': None,
                'evidence_notes': ['原供应链候选 '+data['sourceHead']+' 已交付；原Handler界面，正式采用为false。',
                    '订单使用各国当地9/5–10/4完整30日；31日运输窗口原件保留，趋势7/8/15分段。',
                    '全仓库存108行、入库38行是独立非原子观察；MY空SKU条件异常保留，原费用结算未重采。',
                    '可编辑47行清单、零计划和内嵌图片导出属工程交付；仍需组合源码/数据根的独立验收。',
                    '采购、库存、共享队列执行不在本回执授权范围。']}}
        if (data.get('schema')!='supply-completion-handoff/v2'
            or not isinstance(data.get('unchanged'), dict)
            or not isinstance(data.get('formalService'), dict)
            or data.get('outcome')!='AUTHORIZED_SHOPEE_REFRESH_AND_EIGHT_STORE_READS_COMPLETE__ORIGINAL_ROUTE_RETAINED__FULL_FRESH_ADMISSION_PENDING'
            or type(data.get('unchanged',{}).get('businessWrites')) is not int
            or data.get('unchanged',{}).get('businessWrites')!=0
            or data.get('formalService',{}).get('candidateAdopted') is not False):
            raise ValueError('unsupported or changed supply receipt; coordinator verification required')
        return data['recordedAt'],{'supply-completion':{
          'stage':'八店订单已补齐；页面待接入',
          'last_reported_state':'eight_store_reads_complete_admission_pending',
          'blocker':'接入已补齐的库存与订单，核对缺失商品资料。',
          'closed':False,'user_decision':None,
          'evidence_notes':['10/5 来源回执已补齐八店订单；现正式页面仍为9/30快照，候选未采用。','库存分页与订单窗口各保留原时间；不是当前实时库存。','MY 空 Seller SKU、TH 0957/0983 展示资料及全仓证据准入仍需接续。','采购、入库执行没有本次验收证据；不据此触发业务。']}}
    raise ValueError('source adapter not allowlisted')

def tick(manifest,health_path,state_dir,sources):
    state_dir.mkdir(parents=True,exist_ok=True)
    journal_path=state_dir/'seen.json'
    journal=json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
    if not isinstance(journal, dict):
        raise ValueError('observer journal must be an object')
    outcomes=[{'source': 'product_business', 'status': 'not_configured',
               'reason': 'No independently verified business receipt source'}]
    for kind in SOURCE_FILENAMES:
        if kind not in sources:
            outcomes.append({'source': kind, 'status': 'not_configured'})
    for kind,path in sources.items():
        try:
            if kind not in SOURCE_FILENAMES:
                raise ValueError('source adapter not allowlisted')
            path = local_file(path)
            if path.name not in SOURCE_NAMES[kind]:
                raise ValueError('producer receipt filename is not allowlisted')
            stat=path.stat()
            if not path.is_absolute() or not 0<stat.st_size<=512*1024 or path.is_symlink():raise ValueError('invalid source')
            raw=path.read_bytes();after=path.stat()
            if (stat.st_size,stat.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('source changed during read')
            sha=hashlib.sha256(raw).hexdigest()
            if journal.get(kind,{}).get('sha256')==sha:
                retained=state_dir/'originals'/f'{kind}-{sha}.json'
                if not retained.is_file() or hashlib.sha256(retained.read_bytes()).hexdigest()!=sha:
                    raise ValueError('retained original receipt changed or unavailable')
                outcomes.append({'source':kind,'status':'unchanged'});continue
            stamp,events=source_events(kind,raw)
            if _time(stamp)>datetime.now(timezone.utc):raise ValueError('future producer clock')
            original=state_dir/'originals'/f'{kind}-{sha}.json'
            original.parent.mkdir(exist_ok=True)
            if not original.exists():original.write_bytes(raw)
            source_ref={'path':str(original.resolve()),'sha256':sha,'label':'Original '+kind+' producer receipt, immutable observation copy'}
            applied=[]
            for work_id,changes in events.items():
                data=json.loads(manifest.read_text(encoding='utf-8'))
                item=next(i for i in data['items'] if i['work_id']==work_id)
                if item['source_session_id']!=WORKS[work_id]:raise ValueError('accepted source session changed')
                if _time(stamp)<=_time(item['observed_at']):continue
                # Retain old immutable evidence; add the newly read original receipt.
                refs={ (r['path'],r['sha256']):r for r in item.get('sources',[]) }
                refs[(source_ref['path'],source_ref['sha256'])]=source_ref
                changes={**changes,'sources':list(refs.values())}
                update={'schema':'orbit-work-observation-update/v1','work_id':work_id,'source_session_id':WORKS[work_id],
                        'observed_at':stamp,'changes':changes}
                receipt=state_dir/'updates'/f'{hashlib.sha256(work_id.encode()).hexdigest()[:8]}-{sha[:16]}.json';receipt.parent.mkdir(exist_ok=True)
                atomic(receipt,update)
                envelope={**update,'receipt_ref':{'path':str(receipt.resolve()),'sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}}
                outcome=update_observation(manifest,envelope,expected_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),apply=True)
                applied.append(outcome)
            journal[kind]={'sha256':sha,'producer_observed_at':stamp,'applied':applied,'checked_at':datetime.now(timezone.utc).isoformat()}
            outcomes.append({'source':kind,'status':'updated' if applied else 'retained-newer-record','work_ids':[a['work_id'] for a in applied]})
        except (OSError,ValueError,KeyError,TypeError,StopIteration) as exc:
            outcomes.append({'source':kind,'status':'needs_verification','error_type':type(exc).__name__})
    atomic(journal_path,journal)
    health={'schema':'orbit-source-feedback-health/v1','checked_at':datetime.now(timezone.utc).isoformat(),
            'state':'observing','pid':os.getpid(),'poll_seconds':5,'page_refresh_seconds':15,
            'source_work_ids':list(WORKS),'source_count':len(sources),'accepted_task_count':7,
            'source_lane_count':3,
            'missing_source_lanes':[row['source'] for row in outcomes if row['status']=='not_configured'],
            'manual_task_count':4,
            'business_execution_heartbeat':False,'outcomes':outcomes}
    atomic(health_path,health)
    return health


def read_feedback_health(path):
    """Public heartbeat metadata only. Missing source is not business readiness."""
    unavailable = {'schema': 'orbit-source-feedback-health/v1', 'state': 'unavailable',
                   'checked_at': None, 'business_execution_heartbeat': False}
    if not path:
        return unavailable
    try:
        selected = local_file(path)
        if not 0 < selected.stat().st_size <= 64 * 1024:
            return unavailable
        value = json.loads(selected.read_text(encoding='utf-8'))
        if (not isinstance(value, dict)
                or value.get('schema') != 'orbit-source-feedback-health/v1'
                or value.get('business_execution_heartbeat') is not False
                or _time(value.get('checked_at')) > datetime.now(timezone.utc)):
            return unavailable
        fields = ('schema', 'state', 'checked_at', 'poll_seconds', 'page_refresh_seconds',
                  'source_work_ids', 'source_count', 'accepted_task_count', 'source_lane_count',
                  'missing_source_lanes', 'manual_task_count', 'business_execution_heartbeat')
        public = {key: value[key] for key in fields if key in value}
        public['outcomes'] = [{key: row[key] for key in ('source', 'status', 'error_type') if key in row}
                              for row in value.get('outcomes', []) if isinstance(row, dict)]
        return public
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable

