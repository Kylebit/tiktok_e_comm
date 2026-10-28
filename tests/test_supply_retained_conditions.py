import copy,json
import pytest
from test_u04_refresh import project,cli
from test_u04_captured_refresh import captured_inputs,resave
from test_supply_retained_global_refresh import module,globals_pair,order_source

def complete_v2_source(p,s):
    source=order_source(p,s)
    exception=source.pop('mappingExceptions')[0]
    pull=module('pull_order_demand')
    for block in source['regions']['MY']['tiktok']['blocks']:
        for page in block['pages']:
            for row in page['rows']:
                if row['id']==exception['orderId']:
                    del row['line_items'][exception['lineIndex']]
                    pull._seal_receipt(page)
    source['schema']='supply-chain-captured-source/v2'
    source.pop('demandWindowPolicy')
    return source

def test_global_inventory_conditions_survive_complete_mappings(project):
    root,a,p,s=captured_inputs(project)
    m,iv,ib=globals_pair()
    resave(a,p,'inventory',m.capture_global_browser(iv))
    resave(a,p,'inbound',m.capture_global_browser(ib,m.capture_global_browser(iv)))
    resave(a,p,'orders',complete_v2_source(p,s))
    result,stage=cli(root,a,'--captured-stage')
    assert result.returncode==0,stage
    data=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())['data']
    assert data['sourceReadiness']['conditional']
    assert any(v['code']=='NON_ATOMIC_BROWSER_OBSERVATIONS' for v in data['sourceReadiness']['conditions'])
    assert data['capturedRefreshSources']['inventory']['globalPageRowCounts']==[2,2]

def test_dashboard_keeps_exception_reference_without_raw_order_number(project):
    root,a,p,s=captured_inputs(project)
    source=order_source(p,s)
    resave(a,p,'orders',source)
    result,stage=cli(root,a,'--captured-stage')
    assert result.returncode==0,stage
    data=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())['data']
    public=data['orderMappingExceptions'][0]
    assert 'orderId' not in public
    assert public['orderRefSha256']==module('captured_refresh').digest(source['mappingExceptions'][0]['orderId'])
    assert source['mappingExceptions'][0]['orderId']

def test_independent_order_collection_clocks_cannot_be_relabelled(project):
    root,a,p,s=captured_inputs(project)
    source=order_source(p,s)
    source['platformCollectionClocks']={v:{'capturedAt':source['capturedAt'],'collectionStartedAt':'2026-09-08T00:00:00+00:00','materializedAt':source['materializedAt']} for v in ('tiktok','shopee')}
    resave(a,p,'orders',source)
    result,stage=cli(root,a,'--captured-stage')
    assert result.returncode!=0,'invalid independent collection clocks accepted'
    assert not list((a/'captured-stages').glob('*/stage.json')) if (a/'captured-stages').exists() else True

@pytest.mark.parametrize('empty',[True,False])
def test_admitted_region_total_matches_verified_batch_and_inventory_totals(project,empty):
    root,a,p,s=captured_inputs(project)
    if empty:
        m,iv,ib=globals_pair()
        inventory=m.capture_global_browser(iv)
        resave(a,p,'inventory',inventory)
        resave(a,p,'inbound',m.capture_global_browser(ib,inventory))
    result,stage=cli(root,a,'--captured-stage')
    assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())
    for region,plan in preview['inbound']['regions'].items():
        batches=sum(batch['totalUnits'] for batch in plan['batches'])
        inventory=sum(row['inventory']['inbound'] for row in preview['data']['countries'][region])
        assert plan['totalUnits']==batches==inventory==(0 if empty else 3)
