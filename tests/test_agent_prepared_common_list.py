from types import SimpleNamespace
import pytest
from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
from shared_platform.manual_common_creation import ManualCommonBlocked


def reader(pages):
    service=object.__new__(AgentPreparedCommonService);calls=[]
    def post(path,body):
        calls.append(body['pageNo'])
        return pages[body['pageNo']-1]
    service._post=post;service._detail=lambda identity:{'commonCollectBoxDetailId':identity}
    return service,calls


def page(rows,total):return {'result':'success','data':{'detailList':rows,'total':total}}
def row(i,sku='other'):return {'commonCollectBoxDetailId':i,'itemNum':sku}


def test_official_detail_list_paginates_and_matches_exact_seller():
    service,calls=reader([page([row(1),row(2,'2000431')],3),page([row(3)],3)])
    assert service._scan_common('2000431')==[{'commonCollectBoxDetailId':'2'}]
    assert calls==[1,2]


@pytest.mark.parametrize('pages',[
    [{'result':'success','data':{'list':[],'total':0}}],
    [page([row(1)],2),page([],2)],
    [page([row(1)],2),page([row(1)],2)],
    [page([row(1)],0)],
    [page([],None)],
])
def test_incomplete_ambiguous_or_wrong_shape_list_never_proves_unoccupied(pages):
    service,_=reader(pages)
    with pytest.raises(ManualCommonBlocked):service._scan_common('2000431')
