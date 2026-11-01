"""Real new-task result handoff consumes real owned draft checkpoint bytes.

Authorization/SDK transport are isolated edges here. The result consumer,
filename mapping, checkpoint producer/reader and UNKNOWN classification run.
The full new-task native fork separately covers the actual authority/ledger.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from modules.miaoshou.tiktok_v4_drafts import DraftWriteFact, EXPECTED_SHOP_ID_BY_TARGET
from shared_platform import native_new_task_v4_targets as native
from shared_platform import native_final_mutation_budget as budgets
from shared_platform.product_publication_live_dependencies import TikTokV4DraftCheckpointStore

LABEL='tiktok:LH_MY'
SHOP=str(EXPECTED_SHOP_ID_BY_TARGET[LABEL])


def execute_owned(monkeypatch,tmp_path,*,events=('ACCEPTED',),unknown=False,tamper=False):
    plan={'plan_id':'owned-plan','payload_digest':'a'*64}
    snapshot={'schema_version':'approved-publication-snapshot/v4','offer_id':'987654321',
              'product_revision':3,'snapshot_digest':'sha256:'+'b'*64}
    candidate={'write_budget':{'TIKTOK':{}}}
    budget={'reservations':[{'mutation_target_label':LABEL,'outcome':'UNKNOWN' if unknown else 'ACCEPTED'}]}
    ledger=SimpleNamespace(snapshot=lambda:deepcopy(budget))
    active=SimpleNamespace(decision_id='owned-decision',store=SimpleNamespace(
        get_run=lambda _: {'run_id':'release-run:'+'a'*24}),runtime=SimpleNamespace(
        supervisor=SimpleNamespace(root=tmp_path)))
    request=SimpleNamespace(channel='tiktok',target_label=LABEL)
    monkeypatch.setattr(native,'_approved_inputs',lambda *_:(plan,snapshot,candidate))
    monkeypatch.setattr(native,'_catalog_sink',lambda *_:None)
    monkeypatch.setattr(budgets,'build_native_budget_ledger',lambda *_,**__:ledger)
    calls=[]
    def dependencies(active,platform,ledger,references,observations):
        def execute(native_request):
            calls.append(native_request)
            store=TikTokV4DraftCheckpointStore(tmp_path)
            for index,outcome in enumerate(events):
                label='tiktok:LH_TH' if outcome=='OTHER_TARGET' else LABEL
                shop=str(EXPECTED_SHOP_ID_BY_TARGET[label])
                detail='12345' if outcome!='SECOND_IDENTITY' else '54321'
                value='ACCEPTED' if outcome in {'OTHER_TARGET','SECOND_IDENTITY'} else outcome
                store.record_fact(native_request,label,DraftWriteFact('SAVE_DRAFT',value,
                    detail_id=detail,shop_id=shop))
            if tamper:
                path=store._path(native_request);body=json.loads(path.read_text())
                body['events'][0]['detail_id']='98765';path.write_text(json.dumps(body))
            return {'targets':[{'target_label':LABEL,'status':'PROCESSING','evidence':{'outcome_unknown':unknown}}],
                    'dispatch_attempted':True,'readback_completed':False,'external_write_count':None if unknown else 1}
        return execute
    monkeypatch.setattr(native,'_dependencies',dependencies)
    return native._execute(active,request),calls


def test_new_task_processing_retains_real_draft_provider_identity(monkeypatch,tmp_path):
    result,calls=execute_owned(monkeypatch,tmp_path)
    assert len(calls)==1
    assert result.submission_accepted is True
    assert result.succeeded is True and result.readback_verified is False
    assert result.external_reference=='miaoshou:tiktok:'+SHOP+':12345'
    assert result.readback_evidence['outcome_unknown'] is False
    assert result.readback_evidence['verified'] is False


@pytest.mark.parametrize('kwargs',[
    {'events':()},
    {'events':('ACCEPTED','SECOND_IDENTITY')},
    {'events':('OTHER_TARGET',)},
    {'events':('UNKNOWN',),'unknown':True},
    {'events':('ACCEPTED',),'unknown':True},
    {'tamper':True},
])
def test_unproven_ambiguous_unknown_or_tampered_never_becomes_accepted(monkeypatch,tmp_path,kwargs):
    result,calls=execute_owned(monkeypatch,tmp_path,**kwargs)
    assert len(calls)==1
    assert result.submission_accepted is False and result.readback_verified is False
    assert result.external_reference is None
    assert result.readback_evidence['outcome_unknown'] is True


def test_repeated_confirmation_of_same_identity_is_unambiguous(monkeypatch,tmp_path):
    result,_=execute_owned(monkeypatch,tmp_path,events=('ACCEPTED','ACCEPTED'))
    assert result.submission_accepted is True
    assert result.external_reference=='miaoshou:tiktok:'+SHOP+':12345'


@pytest.mark.parametrize('platform',['SHOPEE','OZON'])
def test_other_platform_original_reference_deduplication_is_unchanged(platform):
    from shared_platform.native_prepared_v4_targets import _provider_reference
    request=SimpleNamespace(platform=platform)
    assert _provider_reference(request,'unused',['first','first','second'])=='first,second'
    assert _provider_reference(request,'unused',[]) is None
