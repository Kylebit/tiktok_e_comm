"""Legacy sidecars are diagnostic input only; live progression is covered over HTTP/CLI."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/product_publication_workflow.py'


def _module():
    spec = importlib.util.spec_from_file_location('product_publication_workflow', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('state', ['MISSING', 'FIRST_REVIEW_READY', 'PENDING_GENERATION',
    'READY_FOR_REVIEW', 'VERIFIED', 'APPROVED', 'READY_TO_PUBLISH'])
def test_legacy_sidecar_states_never_supply_current_authority(state):
    module = _module()
    result = module.classify_workflow(offer_id='3900000001',
        first_review={'status': state}, localized={'review': {'status': state,
            'approval_intent': {'approved_by': 'Kyle'},
            'miaoshou_pre_review_sync': {'status': 'VERIFIED', 'verified': True}}},
        handoff={'status': state, 'plan_id': 'omnichannel:abc', 'snapshot_digest': 'sha256:' + 'a'*64})
    assert result['stage'] == 'RECONCILIATION_REQUIRED'
    assert result['next_command'] is None
    assert result['approval_recorded'] is False and result['miaoshou_verified'] is False


def test_legacy_status_diagnostic_does_not_leak_urls_or_provider_payloads():
    result = _module().classify_workflow(offer_id='3900000001',
        localized={'raw_provider_payload': {'url': 'https://secret.example/image'}})
    rendered = json.dumps(result).lower()
    assert 'https://' not in rendered and 'provider_payload' not in rendered
    assert set(result) == {'schema_version', 'offer_id', 'stage', 'requires_reconciliation',
        'reason', 'next_command', 'approval_recorded', 'miaoshou_verified'}


@pytest.mark.parametrize('offer', ['../3900000001', '3900000001&execute=1', '９００００５２'])
def test_default_status_rejects_invalid_identity_before_transport(offer):
    with pytest.raises(ValueError, match='ASCII digits'):
        _module().status_offer(offer)


@pytest.mark.parametrize('status', ['READBACK_ONLY', 'SUPERSEDED', 'PARTIAL'])
def test_stable_execution_without_ready_platform_never_recompiles_handoff(monkeypatch,status):
    module=_module()
    view={'schema_version':'publication-stages/v1','ok':True,'offer_id':'123',
        'common':{'status':'VERIFIED'},'marketplace':{'status':status,
            'platforms':[{'platform':'OZON','next_action':'READ_EXISTING_REPORT'}]}}
    calls=[]
    def read(path,**kwargs):calls.append((path,kwargs));return 200,view
    monkeypatch.setattr(module,'request_publication_stage',read)
    result=module.status_offer('123',base_url='http://127.0.0.1:12345')
    assert result['stage']=='MARKETPLACE_'+status
    assert result['next_command'] is None
    assert len(calls)==1 and 'data' not in calls[0][1]
