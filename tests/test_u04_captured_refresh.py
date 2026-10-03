from __future__ import annotations
import hashlib
import json
import subprocess
import sys
import os
from pathlib import Path
import pytest
from test_u04_refresh import project, cli, fixture_payload, write, refresh, command, environment


def captured_inputs(project):
    root, a, profile = project
    fixture = fixture_payload()
    sources = {}
    targets = {}
    def page(rows):
        return {'totalRows': len(rows), 'pages': [{'cursor': '', 'nextCursor': '', 'rows': rows}]}
    for kind in ('inventory', 'orders', 'inbound'):
        sources[kind] = {'schema': 'supply-chain-captured-source/v1', 'kind': kind,
                         'sourceId': 'manual-complete-fixture-' + kind, 'capturedAt': fixture[kind]['capturedAt'], 'regions': {}}
    sources['orders']['days'] = 31
    for region, warehouse in refresh.WAREHOUSES.items():
        targets[region] = {'warehouse': warehouse, 'tiktok': region+'-TK-SHOP', 'shopee': region+'-SP-SHOP'}
        sources['inventory']['regions'][region] = {'target': warehouse, **page([r for r in fixture['inventory']['records'] if r['warehouse'] == warehouse])}
        sources['orders']['regions'][region] = {platform: {'target': targets[region][platform], **page(fixture['orders']['countries'][region][platform])} for platform in ('tiktok', 'shopee')}
        sources['inbound']['regions'][region] = {'target': warehouse, **page(fixture['inbound']['regions'][region]['batches'])}
        sources['inbound']['regions'][region]['pages'][0]['rows'][0].update(createdAt='2026-09-01T00:00:00+00:00', estimatedAnchorAt='2026-09-05T00:00:00+00:00', estimatedSellableDate='2026-09-12', source='manual fixture batch detail')
        fixture['seed']['countries'][region][0].update(image='assets/sku-0001.jpg', manualNote='keep existing metadata', dimensionsCm=[10, 20, 1])
    profile.update(schema='supply-chain-refresh-profile/v2', captured={'sources': {}, 'targets': targets, 'output_root': 'applied'})
    for kind, value in {**sources, 'seed': fixture['seed']}.items():
        path = a/(kind+'.json'); write(path, value)
        profile['captured']['sources'][kind] = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    write(a/'profile.json', profile)
    (a/'applied/assets').mkdir(parents=True)
    (a/'applied/assets/sku-0001.jpg').write_bytes(b'owned fixture image bytes')
    return root, a, profile, sources


def resave(a, profile, kind, source):
    path=a/(kind+'.json');write(path,source)
    profile['captured']['sources'][kind]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();write(a/'profile.json',profile)


def test_captured_cli_stage_apply_and_repeat_preserve_data(project):
    root,a,p,s=captured_inputs(project)
    result, stage=cli(root,a,'--captured-stage')
    assert result.returncode==0, result.stderr+result.stdout
    assert stage['state']=='STAGED' and stage['network_reads']==0
    assert not (a/'applied/data.js').exists()
    output=a/'applied';output.mkdir(exist_ok=True);(output/'manual-overrides.json').write_text('{"local":"keep"}')
    (output/'assets/old.jpg').write_bytes(b'preserved image')
    result, applied=cli(root,a,'--captured-apply',stage['stage_digest']);assert result.returncode==0,applied
    before=(output/'data.js').read_bytes();assert b'keep existing metadata' in before
    assert (output/'manual-overrides.json').read_text()=='{"local":"keep"}'
    assert (output/'assets/old.jpg').read_bytes()==b'preserved image'
    data=json.loads(before.decode().removeprefix('window.SUPPLY_CHAIN_DATA = ').strip().removesuffix(';'))
    for region in refresh.REGIONS:
        row=data['countries'][region][0]
        assert row['inventory']['available']==5
        assert row['channels']['tiktok']['recent30Units']==1
        assert row['channels']['shopee']['recent30Units']==4
    assert data['snapshotDate']=='2026-09-05' and data['orderDemandCapturedAt']=='2026-09-04T08:00:00+00:00'
    result, repeated=cli(root,a,'--captured-apply',stage['stage_digest'])
    assert result.returncode==0 and repeated['state']=='REUSED' and (output/'data.js').read_bytes()==before


@pytest.mark.parametrize('failure',['missing_page','wrong_target','unknown_sku','missing_clock','inbound_mismatch','source_hash','missing_anchor','inventory_unknown','payment_window','reserved_output'])
def test_bad_captured_stage_leaves_last_good_output(project,failure):
    root,a,p,s=captured_inputs(project);out=a/'applied';(out/'data.js').write_text('last good')
    kind='orders'
    if failure=='missing_page':s[kind]['regions']['MY']['tiktok']['pages'][0]['nextCursor']='missing'
    elif failure=='wrong_target':s[kind]['regions']['MY']['tiktok']['target']='TH-other-shop'
    elif failure=='unknown_sku':s[kind]['regions']['MY']['tiktok']['pages'][0]['rows'][0]['line_items'][0]['seller_sku']='9999'
    elif failure=='missing_clock':s[kind]['capturedAt']='2026-09-04'
    elif failure=='inbound_mismatch':
        kind='inbound';s[kind]['regions']['MY']['pages'][0]['rows'][0]['totalUnits']=99
    elif failure=='missing_anchor':
        kind='inbound';s[kind]['regions']['MY']['pages'][0]['rows'][0].pop('estimatedAnchorAt')
    elif failure=='inventory_unknown':
        kind='inventory';s[kind]['regions']['MY']['pages'][0]['rows'][0]['seller_sku']='9999'
    elif failure=='payment_window':s[kind]['regions']['MY']['tiktok']['pages'][0]['rows'][0]['paid_time']=9999999999
    elif failure=='reserved_output':p['captured']['output_root']='captured-stages';write(a/'profile.json',p)
    else:p['captured']['sources']['orders']['sha256']='0'*64;write(a/'profile.json',p)
    if failure not in {'source_hash','reserved_output'}:resave(a,p,kind,s[kind])
    result,value=cli(root,a,'--captured-stage')
    assert result.returncode!=0 and not value['ok']
    assert (out/'data.js').read_text()=='last good'


def test_apply_process_crash_rolls_forward_original_stage_only(project):
    root,a,p,s=captured_inputs(project)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0
    guard=root/'outputs/guard/sitecustomize.py'
    guard.write_text(guard.read_text()+"\nimport os\n_original_replace=os.replace\ndef replace(src,dst):\n _original_replace(src,dst)\n if str(dst).replace('\\\\','/').endswith('/applied/data.js'):\n  os._exit(73)\nos.replace=replace\n",encoding='utf-8')
    run=subprocess.run(command(root,a,'--captured-apply',stage['stage_digest']),cwd=a,env=environment(root),capture_output=True,text=True,encoding='utf-8')
    write(a/'crash-command.json',{'command':run.args,'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
    assert run.returncode==73
    assert (a/'applied/data.js').exists() and not (a/'applied/inbound-plan.js').exists()
    assert json.loads((a/'captured-apply.json').read_text())['state']=='APPLYING'
    guard.write_text(guard.read_text().split('\nimport os\n_original_replace')[0],encoding='utf-8')
    result,recovered=cli(root,a,'--captured-apply',stage['stage_digest'])
    assert result.returncode==0 and recovered['recovered'] and recovered['dashboard_source_writes']==1
    assert json.loads((a/'captured-apply.json').read_text())['state']=='COMPLETE'


def test_local_edits_and_corrupt_stage_are_not_overwritten(project):
    root,a,p,s=captured_inputs(project)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0
    output=a/'applied';(output/'data.js').write_text('operator updated')
    result,value=cli(root,a,'--captured-apply',stage['stage_digest'])
    assert result.returncode!=0 and (output/'data.js').read_text()=='operator updated'
    (a/'captured-stages'/stage['stage_digest']/'data.js').write_text('corrupt staged file')
    result,value=cli(root,a,'--captured-apply',stage['stage_digest'])
    assert result.returncode!=0 and 'staged bytes changed' in value['detail']


def test_stage_failed_atomic_write_does_not_replace_good_apply(project):
    root,a,p,s=captured_inputs(project);out=a/'applied';before='window.SUPPLY_CHAIN_DATA = '+(a/'seed.json').read_text()+';\n';(out/'data.js').write_text(before)
    guard=root/'outputs/guard/sitecustomize.py'
    guard.write_text(guard.read_text()+"\nimport os\n_original_replace=os.replace\ndef replace(src,dst):\n if str(dst).endswith('stage.json'): raise OSError('injected stage replace failure')\n return _original_replace(src,dst)\nos.replace=replace\n",encoding='utf-8')
    result,value=cli(root,a,'--captured-stage');assert result.returncode!=0
    assert (out/'data.js').read_text()==before and not list((a/'captured-stages').glob('*/stage.json'))


def test_two_captured_pages_are_consumed_not_only_declared(project):
    root,a,p,s=captured_inputs(project);v=s['orders']['regions']['MY']['shopee'];raw=v['pages'][0]['rows']
    v['pages']=[{'cursor':'','nextCursor':'page-2','rows':raw[:1]}, {'cursor':'page-2','nextCursor':'','rows':raw[1:]}]
    resave(a,p,'orders',s['orders']);result,stage=cli(root,a,'--captured-stage');assert result.returncode==0
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())
    assert preview['data']['countries']['MY'][0]['channels']['shopee']['recent30Units']==4
    assert all(Path(path).is_relative_to(root) for path in preview['consumer_sources'])


def test_new_source_requires_current_seed_and_local_images(project):
    root,a,p,s=captured_inputs(project);result,stage=cli(root,a,'--captured-stage');assert result.returncode==0
    result,value=cli(root,a,'--captured-apply',stage['stage_digest']);assert result.returncode==0
    s['inventory']['sourceId']='new-source-version';resave(a,p,'inventory',s['inventory'])
    result,value=cli(root,a,'--captured-stage');assert result.returncode!=0 and 'seed is stale' in value['detail']
    current=(a/'applied/data.js').read_text();seed=json.loads(current.removeprefix('window.SUPPLY_CHAIN_DATA = ').strip().removesuffix(';'));resave(a,p,'seed',seed)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0
    (a/'applied/assets/sku-0001.jpg').unlink()
    result,value=cli(root,a,'--captured-apply',stage['stage_digest']);assert result.returncode!=0 and 'local seed image missing' in value['detail']


@pytest.mark.parametrize('binding_change', [False, True])
def test_applied_bytes_feed_existing_timeline_and_override_consumer(project, binding_change):
    root,a,p,s=captured_inputs(project);result,stage=cli(root,a,'--captured-stage');assert result.returncode==0
    result,value=cli(root,a,'--captured-apply',stage['stage_digest']);assert result.returncode==0
    node=Path('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
    assert node.is_file(), 'existing Node runtime required; do not silently skip consumer'
    source=Path(__file__).resolve().parents[1]
    def consume(mode):
        cmd=[str(node),str(source/'tests/u04_captured_consumers.cjs'),str(source),str(a/'applied'),mode]
        run=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8')
        write(a/('node-'+mode+'-command.json'),{'command':cmd,'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr,'source_sha256':hashlib.sha256((source/'domains/supply_chain_operations/dashboard/inbound-timeline.js').read_bytes()).hexdigest()})
        assert run.returncode==0,run.stderr
    consume('save')
    saved=(a/'applied/saved-storage.json').read_bytes()
    old_data=(a/'applied/data.js').read_bytes();old_plan=(a/'applied/inbound-plan.js').read_bytes()
    (a/'before-data.js').write_bytes(old_data);(a/'before-inbound-plan.js').write_bytes(old_plan)
    (a/'before-storage.json').write_bytes(saved)
    seed=json.loads(old_data.decode().removeprefix('window.SUPPLY_CHAIN_DATA = ').strip().removesuffix(';'));resave(a,p,'seed',seed)
    s['inventory']['sourceId']='second captured inventory';resave(a,p,'inventory',s['inventory'])
    if binding_change:
        for region in refresh.REGIONS:s['inbound']['regions'][region]['pages'][0]['rows'][0]['estimatedAnchorAt']='2026-09-06T00:00:00+00:00'
        resave(a,p,'inbound',s['inbound'])
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    result,value=cli(root,a,'--captured-apply',stage['stage_digest']);assert result.returncode==0,value
    assert (a/'applied/data.js').read_bytes()!=old_data
    assert ((a/'applied/inbound-plan.js').read_bytes()!=old_plan)==binding_change
    consume('changed' if binding_change else 'unchanged')
    assert (a/'applied/saved-storage.json').read_bytes()==saved
    result=json.loads((a/'applied/consumer-result.json').read_text());assert len(result['results'])==4


def test_captured_and_existing_refresh_share_actual_writer_lease(project):
    root,a,p,s=captured_inputs(project)
    scope=refresh.digest({'root':str(root.resolve()).casefold(),'artifact':str(a).casefold()})
    with refresh.business_lock(a,scope,timeout=0):
        result,value=cli(root,a,'--captured-stage')
        assert result.returncode!=0 and value['state']=='ALREADY_RUNNING'
    assert not (a/'captured-stages').exists()


@pytest.mark.parametrize('platform', ['tiktok', 'shopee'])
@pytest.mark.parametrize('missing', ['status', 'detail', 'empty_detail'])
def test_incomplete_captured_order_preserves_good_snapshot(project, platform, missing):
    root,a,p,s=captured_inputs(project)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0
    result,value=cli(root,a,'--captured-apply',stage['stage_digest']);assert result.returncode==0
    before={name:(a/'applied'/name).read_bytes() for name in ('data.js','inbound-plan.js')}
    seed=json.loads(before['data.js'].decode().removeprefix('window.SUPPLY_CHAIN_DATA = ').strip().removesuffix(';'))
    resave(a,p,'seed',seed)
    row=s['orders']['regions']['MY'][platform]['pages'][0]['rows'][0]
    key=('status' if platform=='tiktok' else 'order_status') if missing=='status' else ('line_items' if platform=='tiktok' else 'item_list')
    if missing=='empty_detail':row[key]=[]
    else:row.pop(key)
    resave(a,p,'orders',s['orders'])
    result,value=cli(root,a,'--captured-stage')
    assert result.returncode!=0, value
    assert 'captured order' in value['detail']
    assert {name:(a/'applied'/name).read_bytes() for name in before}==before


@pytest.mark.parametrize('platform,status', [('tiktok','CANCELLED'),('tiktok','UNPAID'),('tiktok','ON_HOLD'),('shopee','CANCELLED'),('shopee','UNPAID'),('shopee','IN_CANCEL')])
def test_explicitly_excluded_orders_need_no_detail(project, platform, status):
    root,a,p,s=captured_inputs(project)
    row=s['orders']['regions']['MY'][platform]['pages'][0]['rows'][0]
    row['status' if platform=='tiktok' else 'order_status']=status
    row.pop('line_items' if platform=='tiktok' else 'item_list')
    resave(a,p,'orders',s['orders'])
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())
    assert preview['data']['countries']['MY'][0]['channels'][platform]['recent30Units']==(0 if platform=='tiktok' else 2)
