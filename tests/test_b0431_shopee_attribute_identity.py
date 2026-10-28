"""Replay the original five GET captures; stop at the first uncaptured read."""
from copy import deepcopy
from hashlib import sha256
import json,os,socket
from pathlib import Path
from types import SimpleNamespace
import pytest

from modules.shopee import global_v4_live_runtime as live
from modules.shopee.global_v4_executor import project_shopee_global_v4_command,_validate_preparation,ShopeeGlobalV4Error
from modules.shopee.global_plan_candidate import _read_attribute_tree


@pytest.fixture
def captured(monkeypatch):
    folder=os.environ.get('ORBIT_B0431_SAVE_CAPTURED')
    if not folder:pytest.skip('explicit original captured files not requested')
    def deny(*args,**kwargs):raise AssertionError('No wire permitted')
    monkeypatch.setattr(socket.socket,'connect',deny)
    folder=Path(folder)
    summary=json.loads((folder/'SHOPEE_MY_GLOBAL_READONLY_V2.json').read_text(encoding='utf-8'))
    reads=[]
    for item in summary['GET_calls']:
        path=folder/Path(item['response_ref']['path']).name;raw=path.read_bytes()
        assert sha256(raw).hexdigest()==item['response_ref']['sha256'];reads.append((item,json.loads(raw)))
    raw=Path(os.environ['ORBIT_B0431_APPROVED_SNAPSHOT']).read_bytes()
    assert sha256(raw).hexdigest()=='3e41486796ec38f8f59fa73655abf1bc3deb1df27d62c5222e38baa5d4d02fb1'
    command=project_shopee_global_v4_command(json.loads(raw))
    attrs=_read_attribute_tree(SimpleNamespace(merchant_get=lambda path,params:deepcopy(reads[4][1])),101154)
    return command,reads,attrs


def test_original_five_GET_flow_passes_category_attributes_then_stops_at_uncaptured_read(captured,monkeypatch):
    command,reads,attrs=captured
    assert len(command['category_decision']['category']['path'])==3 and len(command['approved_category']['path'])==1
    selected,missing=live._approved_required_attributes(command,attrs,selected_category_id='101154')
    assert selected==command['category_decision']['required_attributes'] and missing==[]
    calls=[]
    class UncapturedRead(BaseException):pass
    def replay(path,identity,token,params=None,**kwargs):
        index=len(calls);calls.append((path,dict(params or {})))
        if index==len(reads):raise UncapturedRead('Next official brand/location READ not captured; no fake fact supplied')
        item,response=reads[index]
        assert path==item['endpoint'] and dict(params or {})==item['params']
        return deepcopy(response)
    from modules.shopee import client
    monkeypatch.setattr(client,'merchant_get',replay);monkeypatch.setattr(client,'shop_get',replay)
    with pytest.raises(UncapturedRead):live._default_official_fact_reader(command,
        dict(region='MY',shop_id=1561117812,merchant_id=4970102,shop_token='OFFLINE',merchant_token='OFFLINE'))
    assert len(calls)==6 and calls[-1][0].endswith('/get_brand_list')


def test_identity_digest_attributes_and_full_path_guards_remain(captured):
    command,_,attrs=captured
    for key,value in [('id','999'),('name','wrong')]:
        bad=deepcopy(command);bad['approved_category'][key]=value
        with pytest.raises(live.ShopeeGlobalV4LiveRuntimeError):live._approved_required_attributes(bad,attrs,selected_category_id='101154')
    bad=deepcopy(command);bad['category_decision']['decision_digest']='sha256:'+'0'*64
    with pytest.raises(live.ShopeeGlobalV4LiveRuntimeError):live._approved_required_attributes(bad,attrs,selected_category_id='101154')
    bad=deepcopy(command);bad['category_decision']['required_attributes'][0]['attribute_value_list'][0]['value_id']=999999
    unsigned={key:value for key,value in bad['category_decision'].items() if key!='decision_digest'}
    bad['category_decision']['decision_digest']='sha256:'+sha256(json.dumps({'schema_version':'shopee-global-category-decision/v1',**unsigned},
        ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with pytest.raises(live.ShopeeGlobalV4LiveRuntimeError):live._approved_required_attributes(bad,attrs,selected_category_id='101154')
    # Synthetic warehouse only exercises the existing later full-path guard; not an official fact assertion.
    facts=dict(authority='SHOPEE_OFFICIAL',category=deepcopy(command['category_decision']['category']),
        required_attributes=deepcopy(command['category_decision']['required_attributes']),missing_required_attributes=[],
        warehouse=dict(location_id='OFFLINE',display_name=command['policy']['warehouse']['display_name']))
    _validate_preparation(command,facts)
    facts['category']['path']=facts['category']['path'][-1:]
    with pytest.raises(ShopeeGlobalV4Error,match='approved category facts drifted'):_validate_preparation(command,facts)
