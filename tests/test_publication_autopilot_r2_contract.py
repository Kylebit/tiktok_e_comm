"""Retained policy contract; historical examples do not activate paid preparation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from shared_platform.publication_autopilot import validate_autopilot_policy

EXAMPLE=Path(__file__).resolve().parents[1]/'skills/prepare-product-images/references/historical-autopilot-policy.example.json'

def fixture_policy():
    value=json.loads(EXAMPLE.read_text(encoding='utf-8'))
    value['status']='ACTIVE'
    value['policy_id']='offline-autopilot-contract-fixture-v1'
    value['authority']={'kind':'explicit_conversation_approval','approved_by':'offline fixture','approved_at':'2026-09-05','scope':'Offline contract only'}
    return value

def test_historical_example_is_not_effective():
    with pytest.raises(ValueError):validate_autopilot_policy(json.loads(EXAMPLE.read_text(encoding='utf-8')))

def test_existing_attributed_policy_contract_retains_40_and_all_purposes():
    value=fixture_policy()
    assert validate_autopilot_policy(value)==value
    assert value['paid_models']['maximum_confirmed_requests_per_product']==40

@pytest.mark.parametrize('cap',[0,-1,True,40.5,'40'])
def test_invalid_paid_cap_rejected(cap):
    value=fixture_policy();value['paid_models']['maximum_confirmed_requests_per_product']=cap
    with pytest.raises(ValueError):validate_autopilot_policy(value)

@pytest.mark.parametrize('purposes',[[],[''],['image_translation','image_translation']])
def test_ambiguous_purposes_rejected(purposes):
    value=fixture_policy();value['paid_models']['allowed_purposes']=purposes
    with pytest.raises(ValueError):validate_autopilot_policy(value)
