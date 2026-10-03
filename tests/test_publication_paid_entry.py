"""Exercise actual R2 scheduling, wrappers, text parser and QA against offline I/O."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import threading

import pytest
from PIL import Image
from modules.sourcing import brand_image_lingshi_generation as brand
from modules.sourcing import localized_image_lingshi_generation as localized
from modules.sourcing import localized_image_auto_translation as translator
from modules.sourcing import lingshi_client
from shared_platform import publication_rounds as rounds
from shared_platform.publication_paid_requests import PaidRequestContext, PaidRequestBlocked, load_paid_context

ROOT=Path(__file__).resolve().parents[1]
OFFER='9000052'
ROLES=['cover','pattern_detail','living_room_scene','bedroom_scene','wall_scene','size_comparison','installation']


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def png(number=0):
    out=BytesIO()
    Image.new('RGB',(16,16),(number%255,number*7%255,99)).save(out,format='PNG')
    return out.getvalue()


def result_bytes(url):
    number=url.rsplit('/',1)[1].split('.')[0]
    return png(int(number)) if number.isdigit() else png()


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def policy(cap=40):
    value=read(ROOT/'skills/prepare-product-images/references/historical-autopilot-policy.example.json')
    value.pop('source_evidence')
    value.update(status='ACTIVE',policy_id='OFFLINE-FIXTURE-POLICY')
    value['authority']={'kind':'explicit_conversation_approval','approved_by':'fixture-user','approved_at':'2026-09-05',
                        'scope':'Offline synthetic product workflow only; no real provider authority'}
    value['paid_models']['maximum_confirmed_requests_per_product']=cap
    return value


def round1(offer=OFFER):
    brands=[{'id':ident,'label':ident,'positioning':'fixture positioning','reference_positions':[1],
             'generated_assets':[{'role':role,'quantity':1,'brief':f'fixture {role}'} for role in ROLES]}
            for ident in ('livelyhive-sea','homebloom-sea')]
    plan={'status':'APPROVED','translation_plan':{'status':'DEFERRED_UNTIL_ALL_IMAGES_GENERATED'},'brand_plans':brands}
    value={'schema_version':'round1-approved-snapshot/v1','status':'APPROVED','offer_id':offer,
           'approved_by':'Kyle','product_approval_id':'fixture-approval','product_approval_fingerprint':'fixture-fingerprint',
           'canonical_targets':['tiktok:LH_MY','tiktok:LH_TH','tiktok:LH_VN','tiktok:HB_MY','tiktok:HB_TH','tiktok:HB_VN','ozon:RU','tiktok:MX'],
           'workbench_tiktok_sites':['lh_my','lh_th','lh_vn','hb_my','hb_th','hb_vn','mx'],
           'image_plan':plan,'fact_snapshot':{'product_facts':{'title':'Synthetic product'}}}
    value['snapshot_digest']=rounds.canonical_digest(value)
    return value


class FixtureClient:
    calls=[]
    lock=threading.Lock()
    fail_chat=False
    bad_json=False
    @classmethod
    def from_config(cls,*args,**kwargs):return cls()
    def record(self,kind):
        with self.lock:
            self.calls.append({'kind':kind})
            return len(self.calls)
    def balance(self):return {'balance':100,'unit':'fixture'}
    def list_skills(self):return {'fixture':True}
    def guide(self):return {'fixture':True}
    def list_models(self,kind):return {'models':[{'name':'gpt-image-2','available_for_this_key':True}]}
    def model_detail(self,model):return {'params':[{'name':'images'},{'name':'size','options':[{'value':'2048x2048'}]},
                                                  {'name':'quality','options':[{'value':'medium'}]}]}
    def model_pricing(self,model,**kwargs):return {'channel_groups':[{'is_active':True,'in_key_whitelist':True,'min_price':0.05}]}
    def create_media_generation(self,**kwargs):
        n=self.record('localized' if kwargs['prompt'].startswith('Edit the supplied') else 'brand')
        return {'code':200,'data':{'task_id':n}}
    def get_media_task(self,task):return {'is_final':True,'result_url':f'https://fixture.example/{task}.png','cost':0.05}
    def chat_completions(self,**kwargs):
        is_text=kwargs['model']=='gpt-5.4-nano'
        self.record('text' if is_text else 'qa')
        if self.fail_chat:raise TimeoutError('offline synthetic timeout')
        if is_text:
            request=json.loads(kwargs['messages'][-1]['content'].split('\n',1)[1])
            translations={loc:[{'region_id':r['region_id'],'source_text':r['source_text'],
                               'translated_text':{'th-TH':'สวย','ru-RU':'Красиво'}.get(loc,'Decor')}
                              for r in request['regions']] for loc in translator.AUTO_TRANSLATION_LOCALES}
            content={'schema_version':translator.SCHEMA_VERSION,'translations':translations}
        else:
            content={'status':'PASSED','checks':[{'code':c,'status':'PASSED','evidence':'offline fixture'}
                     for c in ('FACTUAL_ALIGNMENT','OCR_LANGUAGE','DUPLICATION')],'asset_findings':[]}
        return {'choices':[{'message':{'content':'{invalid' if self.bad_json else json.dumps(content,ensure_ascii=False)}}],
                'usage':{'total_tokens':10}}


class Workflow:
    def __init__(self,root,monkeypatch,cap=40,initialize=True):
        self.root=root
        self.directory=root/'reports/product-preparation'/OFFER
        self.entry=load('s02b_real_r2_entry',ROOT/'skills/prepare-product-images/scripts/prepare_product_images.py')
        self.qa=load('s02b_real_r2_qa',ROOT/'skills/prepare-product-images/scripts/run_automated_image_qa.py')
        self.entry.REPO_ROOT=root
        self.qa.ROOT=root
        self.round1=round1() if initialize else read(self.directory/'round1-approved-snapshot.json')
        self.state={'review':{'image_actions':[{'url':'https://fixture.example/source.png','action':'keep'}],
                             'selected_sites':self.round1['workbench_tiktok_sites']},
                    'product_approval':{'status':'approved','approval_id':'fixture-approval','input_fingerprint':'fixture-fingerprint'}}
        if initialize:
            write(self.directory/'round1-approved-snapshot.json',self.round1)
            write(self.directory/'first-review.json',{'image_execution_plan':self.round1['image_plan']})
            write(self.directory/'brand-image-reuse-plan.json',{'schema_version':'brand-image-reuse-plan/v1','offer_id':OFFER,'status':'APPROVED',
                'items':[{'brand_id':b['id'],'role':r,'decision':'GENERATE'} for b in self.round1['image_plan']['brand_plans'] for r in ROLES]})
            write(root/'config/product_publication_autopilot_policy.json',policy(cap))
        from modules.sourcing import new_product_workbench as workbench
        from modules.sourcing import localized_image_ocr as ocr
        self.original_ocr=ocr.detect_english_text_regions
        monkeypatch.setattr(workbench,'load_state',lambda offer:self.state if offer==OFFER else pytest.fail('wrong product read'))
        monkeypatch.setattr(rounds,'REPORTS_ROOT',root/'reports/product-preparation')
        monkeypatch.setattr(lingshi_client,'LingshiClient',FixtureClient)
        monkeypatch.setattr(localized,'LingshiClient',FixtureClient)
        monkeypatch.setattr(brand,'LingshiClient',FixtureClient)
        monkeypatch.setattr(translator,'LingshiClient',FixtureClient)
        monkeypatch.setattr(self.qa,'LingshiClient',FixtureClient)
        monkeypatch.setattr(self.entry,'_download_source',result_bytes)
        real_brand_generate=brand.generate_brand_image
        def traced_brand(**kwargs):
            try:return real_brand_generate(**kwargs)
            except Exception:
                import traceback
                with (self.root/'fixture-errors.txt').open('a',encoding='utf-8') as handle:
                    handle.write(traceback.format_exc())
                raise
        monkeypatch.setattr(brand,'generate_brand_image',traced_brand)
        monkeypatch.setattr(brand,'_download_result',result_bytes)
        monkeypatch.setitem(localized.generate_localized_reference_image.__kwdefaults__,'result_loader',result_bytes)
        monkeypatch.setattr(ocr,'detect_english_text_regions',lambda raw:[{'region_id':'text-'+'b'*20,'source_text':'Decor','bbox':[.1,.1,.4,.2]}])
        FixtureClient.calls=[]
        FixtureClient.fail_chat=False
        FixtureClient.bad_json=False
    def args(self,**flags):
        d=dict(offer_id=OFFER,execute_paid=False,execute_brand_generation=False,confirm_paid_generation=False,
               approve_all=False,approved_by='orbit-product-publication-default-v1',execute_miaoshou=False,finalize_release_handoff=False,
               reuse_plan=None,translation_plan=None,image_provider='lingshi')
        return argparse.Namespace(**{**d,**flags})
    def context(self):
        return load_paid_context(offer_id=OFFER,round1=self.round1,repo_root=self.root)
    def masters(self):
        return self.entry.run(self.args(execute_brand_generation=True))
    def translation_plan(self):
        generation=read(self.directory/'brand-image-generation.json')
        locales=list(translator.AUTO_TRANSLATION_LOCALES)
        plan=self.entry.build_approved_brand_translation_plan(OFFER,generation=generation,
            selections={1:locales,2:locales,3:locales,8:['ms-MY','th-TH','vi-VN'],9:['ms-MY','th-TH','vi-VN'],10:['ms-MY','th-TH']},approved_by='orbit-product-publication-default-v1')
        write(self.directory/'brand-image-translation-plan.json',plan)
        return plan
    def visual(self,localized_assets=False):
        generation=read(self.directory/'brand-image-generation.json')
        translation=read(self.directory/'brand-image-translation.json') if localized_assets else {'assets':[]}
        return self.qa._visual_assessment(client=FixtureClient(),model='fixture-qa',offer_id=OFFER,round1=self.round1,
            generation=generation,translation=translation,raw_attempt_path=self.directory/'qa-raw.json',paid_context=self.context())
    def master_qa(self):
        return self.qa.run(argparse.Namespace(offer_id=OFFER,model='fixture-qa',assessment=None))


@pytest.fixture
def workflow(tmp_path_factory,monkeypatch):
    return Workflow(tmp_path_factory.mktemp('w'),monkeypatch)


def test_B01_actual_r2_52_workload_stops_before_provider_41(workflow):
    w=workflow
    approval=(w.directory/'round1-approved-snapshot.json').read_bytes()
    assert w.masters()['completed_brand_image_count']==14
    w.master_qa()
    w.translation_plan()
    with pytest.raises(PaidRequestBlocked,match='PAID_BUDGET_EXHAUSTED'):
        w.entry.run(w.args(execute_paid=True))
    assert len(FixtureClient.calls)==40
    accounting=w.context().summary()
    assert accounting['occupied']==accounting['attempted']==accounting['confirmed']==40
    assert accounting['unknown']==0
    assert (w.directory/'round1-approved-snapshot.json').read_bytes()==approval
    assert len(read(w.directory/'brand-image-translation.json')['assets'])==16
    assert read(w.directory/'paid-request-summary.json')['occupied']==40


def test_cold_start_auto_zero_inventory_and_preflight_is_zero_paid(workflow):
    w=workflow
    result=w.entry.run(w.args())
    assert result['status']=='BRAND_IMAGE_GENERATION_REQUIRED'
    assert not FixtureClient.calls
    assert not (w.directory/'paid-requests/events.jsonl').exists()
    result=w.masters()
    assert result['paid_requests']['historical_occupied']==0
    first=json.loads((w.directory/'paid-requests/events.jsonl').read_text().splitlines()[0])
    assert first['baseline']['records']==[] and first['baseline']['inventory']


@pytest.mark.parametrize('mode',['timeout','parse'])
def test_B07_actual_translation_chat_unknown_or_raw_parse_never_resubmits(workflow,mode):
    w=workflow
    w.masters(); w.translation_plan()
    FixtureClient.fail_chat=mode=='timeout'
    FixtureClient.bad_json=mode=='parse'
    before=len(FixtureClient.calls)
    with pytest.raises(Exception):w.entry.run(w.args(execute_paid=True))
    assert len(FixtureClient.calls)==before+1
    FixtureClient.fail_chat=False; FixtureClient.bad_json=False
    with pytest.raises(Exception):w.entry.run(w.args(execute_paid=True))
    assert len(FixtureClient.calls)==before+1
    assert w.context().summary()['occupied']==before+1
    if mode=='parse':
        assert any('{invalid' in p.read_text() for p in (w.directory/'paid-requests').glob('raw-*.json'))


def test_B09_changed_approved_source_bytes_invalidates_technical_plan(workflow,monkeypatch):
    w=workflow
    w.masters()
    old=len(FixtureClient.calls)
    monkeypatch.setattr(w.entry,'_download_source',lambda url:png(100))
    with pytest.raises(PaidRequestBlocked,match='TECHNICAL_PLAN_DRIFT'):
        w.masters()
    assert len(FixtureClient.calls)==old


def test_B14_unknown_legacy_metadata_never_creates_zero_budget(workflow):
    w=workflow
    path=w.directory/'old-paid-raw.json'
    path.write_bytes(b'{malformed retained raw')
    before=path.read_bytes()
    with pytest.raises(PaidRequestBlocked,match='PRIOR_PAID_USAGE_GAPS'):
        w.masters()
    assert not FixtureClient.calls and path.read_bytes()==before


def test_B02_actual_r2_authorized_rework_produces_new_task_and_counts_one(workflow):
    w=workflow
    write(w.root/'config/product_publication_autopilot_policy.json',policy(100))
    w.masters(); w.master_qa(); w.translation_plan()
    w.entry.run(w.args(execute_paid=True))
    w.visual(True)
    prior=read(w.directory/'brand-image-translation.json')
    old=next(row for row in prior['assets'] if row['source_review_number']==1 and row['locale']=='ms-MY')
    before=len(FixtureClient.calls)
    result=w.entry.run(w.args(retry_localized_review_number=1,retry_locale='ms-MY',retry_failure_code='OCR_LANGUAGE',retry_authorized_by='Kyle'))
    assert len(FixtureClient.calls)==before+1
    assert result['artifact_digest']!=old['artifact_digest']
    assert result['new_paid_request_count']==result['external_generation_count']==1
    report=read(w.directory/'brand-image-translation.json')
    assert report['retry_attempts'][-1]['superseded_asset']['provider_task_id']==old['provider_task_id']
    assert list((w.directory/'brand-image-translation-checkpoints').glob('attempt-*.png'))
    assert result['paid_requests']['occupied']==before+1


def test_B08_actual_retry_limit_and_zero_remaining_budget(workflow):
    w=workflow
    write(w.root/'config/product_publication_autopilot_policy.json',policy(100))
    w.masters();w.translation_plan();w.entry.run(w.args(execute_paid=True))
    retry=w.args(retry_localized_review_number=1,retry_locale='ms-MY',retry_failure_code='OCR_LANGUAGE',retry_authorized_by='Kyle')
    for attempt in (1,2,3):
        before=len(FixtureClient.calls)
        assert w.entry.run(retry)['attempt_number']==attempt
        assert len(FixtureClient.calls)==before+1
    with pytest.raises(ValueError,match='limit of three'):
        w.entry.run(retry)
    write(w.root/'config/product_publication_autopilot_policy.json',policy(len(FixtureClient.calls)))
    with pytest.raises(PaidRequestBlocked,match='PAID_BUDGET_EXHAUSTED'):
        w.entry.run(w.args(retry_localized_review_number=2,retry_locale='ms-MY',retry_failure_code='OCR_LANGUAGE',retry_authorized_by='Kyle'))


def test_B13_retired_consumers_block_before_config_or_business_read(monkeypatch):
    from modules.sourcing import new_product_workbench as wb
    from modules.sourcing import image_suite_plan as planner
    def forbidden(*a,**kw):pytest.fail('unwired consumer reached config or business store')
    monkeypatch.setattr(wb,'_localized_image_review_store',forbidden)
    monkeypatch.setattr(wb,'_localized_image_pack_store',forbidden)
    for function in (wb.generate_localized_image_review,wb.auto_translate_localized_images):
        with pytest.raises(PaidRequestBlocked,match='PAID_CONTEXT_REQUIRED'):
            function(OFFER,expected_revision=1,source_bytes_by_url={},confirm_paid_generation=True)
    with pytest.raises(PaidRequestBlocked,match='PAID_CONTEXT_REQUIRED'):
        planner.chat_completions([{'role':'user','content':'fixture'}])
    with pytest.raises(ValueError,match='retired'):
        planner._load_toapis_config()


def test_B14_real_r1_builder_documents_are_not_paid_usage(workflow):
    w=workflow
    builder=load('s02b_actual_r1_builder',ROOT/'skills/prepare-product-publication/scripts/prepare_product_publication.py')
    preview={'ok':True,'offer_id':OFFER,'revision':1,'source':{'title_source':'Synthetic product','cost_cny':8.5,
        'weight_kg':.2,'package_cm':[20,10,3],'seller_sku':'fixture','skus':[{'key':'a','seller_sku':'fixture'}],
        'images':[{'url':'https://fixture.example/source.png'}]},
        'review':{'selected_sites':['lh_ph'],'title':'Synthetic product','seller_sku':'fixture','cost_cny':8.5,
                  'weight_kg':.2,'package_cm':[20,10,3],'selected_sku_keys':['a'],'sku_label_overrides':{'a':'20cm'},'fields_locked':True},
        'pricing':{'sea':[{'id':'lh_ph','region':'PH','currency':'PHP','list_price':199}]},
        'product_facts':{'ready':True,'blockers':[]},'workflow':{'blockers':[]},'content_package':{'generated_review_images':[]}}
    packet=builder.prepare_offer(offer_id=OFFER,requested_targets=['lh_ph'],preview_builder=lambda offer:preview)
    assert packet['request_attempted'] is False
    builder._write_text_atomic(w.directory/'first-review.json',json.dumps(packet,ensure_ascii=False))
    (w.directory/'review_report.html').write_text('<html><p>Local R1 review, no provider call.</p></html>')
    (w.directory/'QA.md').write_text('Local R1 review. Pricing is an estimate, not a request receipt.')
    write(w.directory/'category-review.json',{'offer_id':OFFER,'status':'CONFIRMED','provider':'official-catalog','request_attempted':False})
    result=w.masters()
    assert result['completed_brand_image_count']==14
    assert result['paid_requests']['historical_occupied']==0


def test_B14_zero_generation_never_hides_a_nested_unknown(workflow):
    w=workflow
    path=w.directory/'.lingshi-old-result.json'
    doc={'offer_id':OFFER,'assets':[{'external_generation_count':0,'status':'COMPLETED'}],
         'prior_request':{'provider':'lingshi','status':'SUBMISSION_UNKNOWN','request_attempted':True}}
    write(path,doc)
    raw=path.read_bytes()
    result=w.masters()
    assert result['completed_brand_image_count']==0
    assert w.context().summary()['historical_occupied']==w.context().summary()['unknown']==1
    assert path.read_bytes()==raw and not FixtureClient.calls


def worker(w,mode,cut='',wait=True):
    import os,subprocess
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    command=[sys.executable,'-B',str(ROOT/'tests/paid_r2_worker.py'),str(w.root),mode,cut]
    process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if not wait:return process
    stdout,stderr=process.communicate(timeout=40)
    assert process.returncode==(71 if cut else 0),(stdout,stderr)
    return process


def boundary_rows(w):
    path=w.root/'provider-boundary.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def single_role(w):
    w.round1['image_plan']['brand_plans']=w.round1['image_plan']['brand_plans'][:1]
    w.round1['image_plan']['brand_plans'][0]['generated_assets']=w.round1['image_plan']['brand_plans'][0]['generated_assets'][:1]
    w.round1.pop('snapshot_digest')
    w.round1['snapshot_digest']=rounds.canonical_digest(w.round1)
    write(w.directory/'round1-approved-snapshot.json',w.round1)
    write(w.directory/'first-review.json',{'image_execution_plan':w.round1['image_plan']})
    write(w.directory/'brand-image-reuse-plan.json',{'schema_version':'brand-image-reuse-plan/v1','offer_id':OFFER,'status':'APPROVED',
        'items':[{'brand_id':'livelyhive-sea','role':'cover','decision':'GENERATE'}]})


def test_B03_B04_fresh_process_completed_entry_reuses_without_post(workflow):
    w=workflow
    single_role(w)
    worker(w,'brand')
    assert len(boundary_rows(w))==1
    approval=(w.directory/'round1-approved-snapshot.json').read_bytes()
    first=read(w.directory/'brand-image-generation.json')['assets']
    worker(w,'brand');worker(w,'brand')
    assert len(boundary_rows(w))==1
    assert read(w.directory/'brand-image-generation.json')['assets']==first
    assert read(w.directory/'paid-request-summary.json')['new_this_invocation']==0
    assert (w.directory/'round1-approved-snapshot.json').read_bytes()==approval


def test_B05_actual_processes_compete_for_last_slot(workflow):
    w=workflow
    write(w.root/'config/product_publication_autopilot_policy.json',policy(39))
    w.masters();w.master_qa();w.translation_plan()
    with pytest.raises(PaidRequestBlocked,match='PAID_BUDGET_EXHAUSTED'):w.entry.run(w.args(execute_paid=True))
    assert len(FixtureClient.calls)==39
    write(w.root/'config/product_publication_autopilot_policy.json',policy(40))
    processes=[worker(w,'translation',wait=False) for _ in range(2)]
    for process in processes:
        stdout,stderr=process.communicate(timeout=40)
        assert process.returncode==0,(stdout,stderr)
    assert len(boundary_rows(w))==1
    assert w.context().summary()['occupied']==40
    reports=[read(p) for p in w.root.glob('worker-*.json')]
    assert len(reports)==2 and all(not r['external_attempts'] for r in reports)
    assert all('PAID_BUDGET_EXHAUSTED' in r['error'] for r in reports)


@pytest.mark.parametrize('cut',['reserve','submitting','post','raw','ack','completed'])
def test_B06_actual_entry_process_crashes_preserve_budget_and_never_repost(workflow,cut):
    w=workflow
    single_role(w)
    worker(w,'brand',cut)
    count=len(boundary_rows(w))
    assert count==(0 if cut in {'reserve','submitting'} else 1)
    worker(w,'brand')
    assert len(boundary_rows(w))==count
    summary=w.context().summary()
    assert summary['occupied']==1 and summary['new_this_invocation']==0
    if cut in {'reserve','submitting','post'}:
        assert summary['unknown']==1
    else:
        assert summary['unknown']==0 and summary['confirmed']==1
        assert read(w.directory/'brand-image-generation.json')['assets'][0]['status']=='COMPLETED'


@pytest.mark.parametrize('mode',['timeout','parse'])
def test_B07_actual_qa_raw_or_unknown_never_resubmits(workflow,mode):
    w=workflow
    w.masters()
    FixtureClient.fail_chat=mode=='timeout';FixtureClient.bad_json=mode=='parse'
    with pytest.raises(Exception):w.master_qa()
    before=len(FixtureClient.calls)
    FixtureClient.fail_chat=False;FixtureClient.bad_json=False
    with pytest.raises(Exception):w.master_qa()
    assert len(FixtureClient.calls)==before
    assert w.context().summary()['occupied']==before


def test_B14_complete_history_deduplicates_tasks_and_preserves_budget_on_extension(workflow):
    w=workflow
    asset={'status':'COMPLETED','provider':'lingshi-media/v1','provider_task_id':501,'external_generation_count':1,
           'brand_id':'livelyhive-sea','role':'cover'}
    historical={'offer_id':OFFER,'assets':[asset]}
    write(w.directory/'history.json',historical);write(w.directory/'history-copy.json',historical)
    write(w.directory/'r1-model.json',{'offer_id':OFFER,'request_attempted':True,'provider':'lingshi','purpose':'title_and_copy_generation',
        'usage':{'total_tokens':11},'choices':[{'message':{'content':'fixture title'}}]})
    old=(w.directory/'history.json').read_bytes()
    w.masters()
    summary=w.context().summary()
    assert summary['historical_occupied']==2 and summary['occupied']==16
    write(w.root/'config/product_publication_autopilot_policy.json',policy(100))
    assert w.context().summary()['occupied']==16
    assert (w.directory/'history.json').read_bytes()==old


def test_B14_nested_namesake_ledger_directory_is_not_excluded(workflow):
    w=workflow
    path=w.directory/'history/paid-requests/unknown.json'
    write(path,{'offer_id':OFFER,'prior':{'provider':'toapis','status':'SUBMISSION_UNKNOWN','request_attempted':True}})
    w.masters()
    assert not FixtureClient.calls and w.context().summary()['historical_occupied']==1


@pytest.mark.skipif(sys.platform!='win32',reason='Windows junction boundary')
def test_B14_junction_inventory_never_reads_external_sentinel(workflow):
    import _winapi
    w=workflow
    external=w.root/'outside-scope';external.mkdir()
    sentinel=external/'sentinel.json';sentinel.write_text('{DO NOT READ}')
    junction=w.directory/'redirected-history'
    _winapi.CreateJunction(str(external),str(junction))
    reads=[]
    active=True
    def guard(event,args):
        if active and event=='open' and isinstance(args[0],str) and Path(args[0]).name=='sentinel.json':
            reads.append(args[0]);raise AssertionError('junction escaped the controlled inventory')
    sys.addaudithook(guard)
    try:
        with pytest.raises(PaidRequestBlocked,match='non-redirected'):
            w.masters()
        assert not reads and not FixtureClient.calls
    finally:
        active=False


def reconciliation_evidence(context,key,checkpoint_path=None,**outcome):
    observed=context.inspect_request(key,checkpoint_path=checkpoint_path)
    result={'observed':observed,'verified_at':datetime.now(timezone.utc).isoformat(),'verified_by':'offline-upstream-fixture',
            'evidence_ref':'audit://fixture/provider-reconciliation','evidence_sha256':'sha256:'+'a'*64,**outcome}
    if checkpoint_path:
        result['checkpoint_evidence']={**observed['checkpoint'],**{k:v for k,v in result.items() if k not in {'observed'} }}
    return result


def test_B06_local_reconciliation_binds_unknown_image_then_recovers_existing_task(workflow):
    w=workflow
    single_role(w)
    worker(w,'brand','post')
    context=w.context()
    _rows,entries=context._load()
    key=next(iter(entries))
    cp=next((w.directory/'brand-image-checkpoints-lingshi').glob('lingshi-brand-v2-*.json'))
    evidence=reconciliation_evidence(context,key,cp,outcome='task_verified_for_request',task_id=1001)
    bad=deepcopy(evidence);bad['observed']['entry']['business']={'wrong':'product'}
    with pytest.raises(PaidRequestBlocked,match='stale'):context.reconcile_request(evidence=bad)
    context.reconcile_request(evidence=evidence)
    worker(w,'brand')
    assert len(boundary_rows(w))==1
    assert context.summary()['unknown']==0 and context.summary()['occupied']==1


def test_B07_bound_no_charge_allows_next_chat_attempt_without_refunding_unknown(workflow):
    w=workflow
    w.masters();w.translation_plan()
    FixtureClient.fail_chat=True
    with pytest.raises(Exception):w.entry.run(w.args(execute_paid=True))
    context=w.context();_rows,entries=context._load()
    key=next(k for k,r in entries.items() if r['state']=='UNKNOWN')
    evidence=reconciliation_evidence(context,key,outcome='no_task_no_charge',task_id=None,charge_status='none')
    context.reconcile_request(evidence=evidence)
    assert context.summary()['occupied']==15 and context.summary()['unknown']==0
    FixtureClient.fail_chat=False
    with pytest.raises(PaidRequestBlocked,match='PAID_BUDGET_EXHAUSTED'):w.entry.run(w.args(execute_paid=True))
    assert len(FixtureClient.calls)==40


def test_B09_explicit_technical_rebuild_reuses_new_approval_and_keeps_old_charge(workflow):
    w=workflow
    single_role(w);w.masters()
    old_approval=(w.directory/'round1-approved-snapshot.json').read_bytes()
    old_report=(w.directory/'brand-image-generation.json').read_bytes()
    old_ledger=(w.directory/'paid-requests/events.jsonl').read_bytes()
    w.round1['fact_snapshot']['product_facts']['title']='New independently approved fixture facts'
    w.round1.pop('snapshot_digest');w.round1['snapshot_digest']=rounds.canonical_digest(w.round1)
    write(w.directory/'round1-approved-snapshot.json',w.round1)
    with pytest.raises(PaidRequestBlocked,match='TECHNICAL_PLAN_DRIFT'):w.masters()
    proposal=next((w.directory/'paid-requests').glob('proposal-*.json'))
    receipt=w.context().activate_plan_rebuild(proposal)
    assert any(Path(row['archive']).read_bytes()==old_report for row in receipt['archived'])
    assert (w.directory/'paid-requests/events.jsonl').read_bytes().startswith(old_ledger)
    current=(w.directory/'round1-approved-snapshot.json').read_bytes()
    assert current!=old_approval
    assert w.masters()['completed_brand_image_count']==1
    assert len(FixtureClient.calls)==w.context().summary()['occupied']==2
    assert (w.directory/'round1-approved-snapshot.json').read_bytes()==current


def test_B09_model_purpose_change_and_unknown_rebuild_are_blocked(workflow):
    w=workflow
    single_role(w);worker(w,'brand','post')
    changed=policy();changed['paid_models']['models_by_purpose']={'brand_image_generation':['gpt-image-2']}
    write(w.root/'config/product_publication_autopilot_policy.json',changed)
    with pytest.raises(PaidRequestBlocked,match='TECHNICAL_PLAN_DRIFT'):w.masters()
    proposal=next((w.directory/'paid-requests').glob('proposal-*.json'))
    with pytest.raises(PaidRequestBlocked,match='unresolved'):w.context().activate_plan_rebuild(proposal)
    assert len(boundary_rows(w))==1


def test_B09_reference_changed_during_entry_is_rejected_before_post(workflow,monkeypatch):
    w=workflow
    single_role(w)
    reads=[]
    def changing_source(url):
        reads.append(url)
        return png(0 if len(reads)==1 else 100)
    monkeypatch.setattr(w.entry,'_download_source',changing_source)
    w.masters()
    assert not FixtureClient.calls
    assert 'reference bytes changed' in read(w.directory/'brand-image-generation.json')['provider_safe_error']


def test_B10_actual_role_job_isolates_proven_corrupt_business_and_new_role_runs(workflow):
    w=workflow
    single_role(w);w.masters()
    first=read(w.directory/'brand-image-generation.json')['assets'][0]
    sidecar=Path(first['checkpoint_path']).with_suffix('.identity')
    sidecar.write_bytes(b'preserved damaged A sidecar')
    w.round1['image_plan']['brand_plans'][0]['generated_assets'].append({'role':'pattern_detail','quantity':1,'brief':'new B fixture'})
    w.round1.pop('snapshot_digest');w.round1['snapshot_digest']=rounds.canonical_digest(w.round1)
    write(w.directory/'round1-approved-snapshot.json',w.round1)
    write(w.directory/'brand-image-reuse-plan.json',{'schema_version':'brand-image-reuse-plan/v1','offer_id':OFFER,'status':'APPROVED',
        'items':[{'brand_id':'livelyhive-sea','role':role,'decision':'GENERATE'} for role in ('cover','pattern_detail')]})
    with pytest.raises(PaidRequestBlocked):w.masters()
    w.context().activate_plan_rebuild(next((w.directory/'paid-requests').glob('proposal-*.json')))
    w.masters()
    assert len(FixtureClient.calls)==2 and w.context().summary()['occupied']==2
    assert sidecar.read_bytes()==b'preserved damaged A sidecar'
    checkpoints=list(sidecar.parent.glob('lingshi-brand-v2-*.json'))
    assert any(read(path)['status']=='COMPLETED' and read(path)['task_id']==2 for path in checkpoints)


def test_B12_actual_same_business_unknown_never_bypassed_by_prompt_change(workflow):
    w=workflow
    single_role(w);worker(w,'brand','post')
    w.round1['image_plan']['brand_plans'][0]['generated_assets'][0]['brief']='Different approved wording'
    w.round1.pop('snapshot_digest');w.round1['snapshot_digest']=rounds.canonical_digest(w.round1)
    write(w.directory/'round1-approved-snapshot.json',w.round1)
    with pytest.raises(PaidRequestBlocked,match='TECHNICAL_PLAN_DRIFT'):w.masters()
    proposal=next((w.directory/'paid-requests').glob('proposal-*.json'))
    with pytest.raises(PaidRequestBlocked,match='unresolved'):w.context().activate_plan_rebuild(proposal)
    assert len(boundary_rows(w))==1 and w.context().summary()['unknown']==1


def test_B06_no_charge_image_seam_allows_one_bound_next_attempt(workflow):
    w=workflow
    single_role(w);worker(w,'brand','reserve')
    context=w.context();_rows,entries=context._load();key=next(iter(entries))
    cp=next((w.directory/'brand-image-checkpoints-lingshi').glob('lingshi-brand-v2-*.json'))
    evidence=reconciliation_evidence(context,key,cp,outcome='no_task_no_charge',task_id=None,charge_status='none')
    context.reconcile_request(evidence=evidence)
    worker(w,'brand')
    assert len(boundary_rows(w))==1
    assert context.summary()['occupied']==2 and context.summary()['unknown']==0


def test_B11_actual_roles_recover_independent_v1_task_bindings_without_create(workflow,monkeypatch):
    from modules.sourcing import image_generation_checkpoint as cp
    import uuid
    w=Workflow(ROOT.parent/'orbithive-audit-20260905'/('v1-'+uuid.uuid4().hex[:8]),monkeypatch)
    single_role(w)
    w.round1['image_plan']['brand_plans'][0]['generated_assets'].append({'role':'pattern_detail','quantity':1,'brief':'fixture pattern_detail'})
    w.round1.pop('snapshot_digest');w.round1['snapshot_digest']=rounds.canonical_digest(w.round1)
    write(w.directory/'round1-approved-snapshot.json',w.round1)
    write(w.directory/'brand-image-reuse-plan.json',{'schema_version':'brand-image-reuse-plan/v1','offer_id':OFFER,'status':'APPROVED',
        'items':[{'brand_id':'livelyhive-sea','role':role,'decision':'GENERATE'} for role in ('cover','pattern_detail')]})
    def unknown_create(self,**kwargs):
        self.record('brand')
        raise TimeoutError('fixture provider accepted, acknowledgement lost')
    monkeypatch.setattr(FixtureClient,'create_media_generation',unknown_create)
    w.masters()
    assert len(FixtureClient.calls)==2
    context=w.context();_rows,entries=context._load()
    targets=[];original={}
    for role,task in [('cover',101),('pattern_detail',202)]:
        business={'offer_id':OFFER,'brand_id':'livelyhive-sea','role':role}
        target=next(cp.ImageCheckpoint.from_path(path) for path in (w.directory/'brand-image-checkpoints-lingshi').glob('lingshi-brand-v2-*.json')
            if read(path)['business_digest']==cp.digest({'kind':'brand',**business}))
        identity={'schema_version':'lingshi-brand-image/v1',**business,'brief':'original fixture','source_identities':['https://fixture.example/source.png'],
                  'product_reference_count':1,'model':'gpt-image-2','size':'2048x2048','quality':'medium'}
        sha=cp.digest(identity);path=target.root/f'lingshi-brand-{sha}.json'
        cp.atomic_json(path,{'schema_version':'brand-image-lingshi-checkpoint/v1','identity_digest':sha,
            'client_business_id':f'brand-{sha[:40]}','status':'SUBMITTED','task_id':task})
        original[path]=path.read_bytes()
        proof={**cp.inspect_checkpoint_ownership(path),'business_digest':target.business_digest,'request_digest':target.request_digest,'provider':cp.PROVIDER,
            'verified_at':datetime.now(timezone.utc).isoformat(),'verified_by':'offline upstream fixture','evidence_ref':'audit://fixture/original-v1',
            'evidence_sha256':'sha256:'+'e'*64}
        cp.bind_checkpoint_ownership(path,business_identity=business,request_digest=target.request_digest,evidence=proof,legacy_identity=identity)
        targets.append((target,task,path))
    for target,task,path in targets:
        assert set(target.legacy_records())=={path.name}
        key=next(key for key,row in entries.items() if row['business']['business_digest']==target.business_digest)
        context.reconcile_request(evidence=reconciliation_evidence(context,key,target.path,outcome='task_verified_for_request',task_id=task))
    result=w.masters()
    assert result['completed_brand_image_count']==2
    assert len(FixtureClient.calls)==2
    assert {read(target.path)['task_id'] for target,_task,_path in targets}=={101,202}
    assert all(path.read_bytes()==raw for path,raw in original.items())
    assert context.summary()['occupied']==2 and context.summary()['unknown']==0


def test_B13_actual_workbench_translator_checkpoint_and_bundle_keep_lingshi_receipts(workflow,monkeypatch):
    from modules.sourcing import new_product_workbench as wb
    from modules.sourcing.image_generation_checkpoint import digest
    from test_localized_image_workbench import _snapshot,_FakeOcr,_image_bytes
    w=workflow
    from modules.sourcing import localized_image_ocr as ocr
    monkeypatch.setattr(ocr,'detect_english_text_regions',w.original_ocr)
    monkeypatch.setattr(wb,'resolve_offer_key',lambda value:str(value))
    monkeypatch.setattr(wb,'LOCALIZED_IMAGE_PACKS_DIR',w.root/'data/localized_image_packs')
    snapshot=_snapshot();snapshot['offer_id']=OFFER
    class Store:
        def active_plan_for_product(self,offer):return {'plan_id':'fixture-legacy-approved','product_id':offer,'status':'APPROVED'}
        def approved_publication_snapshot(self,**kwargs):return snapshot
    current=wb.initialize_localized_image_project(OFFER,release_store=Store())
    urls=current['project']['base_package']['ordered_image_urls']
    for index,url in enumerate(urls):
        current=wb.scan_localized_image_text(OFFER,expected_revision=current['project']['revision'],source_url=url,
            source_bytes=_image_bytes(),ocr_engine=_FakeOcr() if index==0 else lambda image:([],None))
    bridge={'offer_id':OFFER,'round1_snapshot_digest':w.round1['snapshot_digest'],
        'legacy_base_digest':digest(current['project']['base_package']),
        'tasks':[{'source_url':url,'source_digest':hashlib.sha256(_image_bytes()).hexdigest(),'brand_id':'livelyhive-sea',
                  'role':('cover' if index==0 else 'pattern_detail'),'locale':locale}
                 for index,url in enumerate(urls) for locale in translator.AUTO_TRANSLATION_LOCALES]}
    result=wb.auto_translate_localized_images(OFFER,expected_revision=current['project']['revision'],
        source_bytes_by_url={url:_image_bytes() for url in urls},confirm_paid_generation=True,paid_context=w.context(),approved_bridge=bridge)
    assert len(FixtureClient.calls)==6
    automatic=result['project']['automatic_translation']
    assert automatic['provider']==translator.PROVIDER and automatic['model']==translator.MODEL
    assert automatic['paid_requests']['occupied']==6
    receipt=result['project']['packs']['th-TH']['images'][0]['preview']['generation_receipt']
    assert receipt['provider']=='lingshi-media/v1' and receipt['paid_request']['key']
    old=deepcopy(result['project'])
    repeated=wb.auto_translate_localized_images(OFFER,expected_revision=result['project']['revision'],source_bytes_by_url={},
        confirm_paid_generation=True,paid_context=w.context(),approved_bridge=bridge)
    assert len(FixtureClient.calls)==6 and repeated['project']==old


def test_B14_known_complete_checkpoint_and_report_import_once_then_actual_run_continues(workflow,monkeypatch,tmp_path_factory):
    import shutil
    w=workflow;single_role(w);w.masters()
    old=read(w.directory/'brand-image-generation.json')['assets'][0]
    old_path=Path(old['checkpoint_path'])
    retained={path:path.read_bytes() for path in old_path.parent.iterdir() if path.is_file() and path.suffix!='.lock'}
    new=Workflow(tmp_path_factory.mktemp('h'),monkeypatch)
    destination=new.directory/'brand-image-checkpoints-lingshi';destination.mkdir()
    for path in retained:shutil.copy2(path,destination/path.name)
    write(new.directory/'history.json',{'offer_id':OFFER,'assets':[old]})
    result=new.masters()
    assert result['completed_brand_image_count']==14
    assert len(FixtureClient.calls)==13
    assert result['paid_requests']['historical_occupied']==1 and result['paid_requests']['occupied']==14
    assert all(path.read_bytes()==raw for path,raw in retained.items())


def test_B13_actual_review_consumer_retains_exact_lingshi_budget_receipts(workflow,monkeypatch):
    from modules.sourcing import new_product_workbench as wb,localized_image_ocr as ocr
    from test_localized_image_review import _snapshot
    from test_localized_image_workbench import _FakeOcr,_image_bytes
    w=workflow
    monkeypatch.setattr(ocr,'detect_english_text_regions',w.original_ocr)
    monkeypatch.setattr(wb,'resolve_offer_key',lambda value:str(value))
    monkeypatch.setattr(wb,'LOCALIZED_IMAGE_REVIEWS_DIR',w.root/'data/localized_image_reviews')
    snapshot=_snapshot();snapshot['offer_id']=OFFER
    store=wb._localized_image_review_store()
    project=store.initialize(snapshot,selected_positions=[1])
    url=project['tasks'][0]['source_url']
    bridge={'offer_id':OFFER,'round1_snapshot_digest':w.round1['snapshot_digest'],'legacy_snapshot_digest':project['approved_snapshot_digest'],
        'tasks':[{'source_url':url,'source_digest':hashlib.sha256(_image_bytes()).hexdigest(),'brand_id':'livelyhive-sea','role':'cover','locale':locale}
                 for locale in translator.AUTO_TRANSLATION_LOCALES]}
    result=wb.generate_localized_image_review(OFFER,expected_revision=project['revision'],source_bytes_by_url={url:_image_bytes()},
        confirm_paid_generation=True,ocr_engine=_FakeOcr(),paid_context=w.context(),approved_bridge=bridge)
    assert len(FixtureClient.calls)==6
    assert all(row['generation_receipt']['provider']=='lingshi-media/v1' and row['generation_receipt']['paid_request']['key'] for row in result['review']['tasks'])
    project=store.load(OFFER)
    task=project['tasks'][0]
    rejected=store.decide(OFFER,expected_revision=project['revision'],task_id=task['task_id'],decision='RETRY')
    with pytest.raises(PaidRequestBlocked,match='LEGACY_REWORK_BRIDGE_REQUIRED'):
        wb.generate_localized_image_review(OFFER,expected_revision=rejected['revision'],source_bytes_by_url={url:_image_bytes()},
            confirm_paid_generation=True,ocr_engine=_FakeOcr(),paid_context=w.context(),approved_bridge=bridge)
    assert len(FixtureClient.calls)==6


def test_B09_rebuild_active_pointer_crash_resumes_receipt_without_deleting_new_output(workflow):
    w=workflow;single_role(w);w.masters()
    w.round1['fact_snapshot']['product_facts']['title']='New approved facts'
    w.round1.pop('snapshot_digest');w.round1['snapshot_digest']=rounds.canonical_digest(w.round1)
    write(w.directory/'round1-approved-snapshot.json',w.round1)
    with pytest.raises(PaidRequestBlocked):w.masters()
    proposal=next((w.directory/'paid-requests').glob('proposal-*.json'))
    worker(w,'rebuild','rebuild-active')
    assert not (w.directory/'round2-technical-invalidation.json').exists()
    w.masters()
    new=(w.directory/'brand-image-generation.json').read_bytes()
    before=(w.directory/'paid-requests/events.jsonl').read_bytes()
    w.context().activate_plan_rebuild(proposal)
    assert (w.directory/'brand-image-generation.json').read_bytes()==new
    assert (w.directory/'paid-requests/events.jsonl').read_bytes()==before
    assert read(w.directory/'round2-technical-invalidation.json')['proposal_digest']==read(proposal)['digest']


def test_B13_actual_planning_seam_uses_same_budget_and_replays_raw(workflow):
    from modules.sourcing import image_suite_plan as planner
    w=workflow
    approved=policy();approved['paid_models']['allowed_purposes'].append('image_planning')
    write(w.root/'config/product_publication_autopilot_policy.json',approved)
    args=dict(model='fixture-qa',business_identity={'offer_id':OFFER,'phase':'image-planning'})
    first=planner.chat_completions([{'role':'user','content':'fixture plan'}],paid_context=w.context(),**args)
    second=planner.chat_completions([{'role':'user','content':'fixture plan'}],paid_context=w.context(),**args)
    assert first==second and len(FixtureClient.calls)==1
    assert w.context().summary()['occupied']==1


def test_B08_exact_local_qa_rework_rejects_wrong_snapshot_and_creates_one_new_attempt(workflow):
    w=workflow;write(w.root/'config/product_publication_autopilot_policy.json',policy(100))
    w.masters();w.translation_plan();w.entry.run(w.args(execute_paid=True))
    asset=read(w.directory/'brand-image-translation.json')['assets'][0]
    assessment={'status':'FAILED','checks':[{'code':code,'status':'FAILED' if code=='OCR_LANGUAGE' else 'PASSED','evidence':'offline verified review'}
        for code in ('FACTUAL_ALIGNMENT','OCR_LANGUAGE','DUPLICATION')],
        'asset_findings':[{'artifact_digest':asset['artifact_digest'],'code':'OCR_LANGUAGE','finding':'fixture rejected exact artifact'}]}
    file=w.directory/'verified-assessment.json';write(file,assessment)
    w.qa.run(argparse.Namespace(offer_id=OFFER,model='fixture-qa',assessment=file))
    qa_path=w.directory/'automated-image-qa.json';qa=read(qa_path)
    retry=w.args(retry_localized_review_number=asset['source_review_number'],retry_locale=asset['locale'],retry_failure_code='OCR_LANGUAGE',retry_authorized_by='existing-policy')
    wrong=deepcopy(qa);wrong['round1_snapshot_digest']='sha256:'+'f'*64;wrong.pop('qa_digest');wrong['qa_digest']=rounds.canonical_digest(wrong);write(qa_path,wrong)
    before=len(FixtureClient.calls)
    with pytest.raises(ValueError,match='QA retry receipt'):w.entry.run(retry)
    assert len(FixtureClient.calls)==before
    write(qa_path,qa)
    result=w.entry.run(retry)
    assert result['new_paid_request_count']==1 and len(FixtureClient.calls)==before+1


def test_B14_bound_history_resolution_preserves_old_unknown_bytes_and_occupied_slot(workflow):
    w=workflow
    source=w.directory/'old-request.json'
    write(source,{'offer_id':OFFER,'attempt':{'provider':'lingshi','status':'SUBMISSION_UNKNOWN','request_attempted':True}})
    raw=source.read_bytes();w.masters()
    context=w.context();observed=context.inspect_history(0)
    evidence={'observed':observed,'outcome':'no_task_no_charge','task_id':None,'charge_status':'none','provider':'lingshi',
        'verified_by':'offline upper reconciliation','verified_at':datetime.now(timezone.utc).isoformat(),
        'evidence_ref':'audit://fixture/historical-reconciliation','evidence_sha256':'sha256:'+'d'*64}
    wrong=deepcopy(evidence);wrong['observed']['offer_id']='another-product'
    with pytest.raises(PaidRequestBlocked,match='stale'):context.reconcile_history(evidence=wrong)
    context.reconcile_history(evidence=evidence)
    assert context.summary()['occupied']==1 and context.summary()['unknown']==0
    assert w.masters()['completed_brand_image_count']==14
    assert context.summary()['occupied']==15 and source.read_bytes()==raw
