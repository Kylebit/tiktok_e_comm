from datetime import datetime,timedelta
import json
from copy import deepcopy
import pytest
from test_u04_refresh import refresh,fixture_payload,project,write,cli
from test_u04_captured_refresh import captured_inputs,resave

def capture(case='', mutate_order=None):
    pull=refresh.module('pull_order_demand');fixture=fixture_payload();transport=refresh.FixtureRequests(fixture['orders'])
    if mutate_order:
        for country in fixture['orders']['countries'].values():mutate_order(country['tiktok'][0])
    targets={r:{'tiktok_shop_id':r+'-EXPLICIT-ID','tiktok_cipher':r,'shopee_shop_id':n+1} for n,r in enumerate(refresh.REGIONS)}
    if case=='empty':
        for r in refresh.REGIONS:fixture['orders']['countries'][r]={'tiktok':[],'shopee':[]}
    def tk(endpoint,token,query,body):
        if case=='cursor_loop':return {'code':0,'data':{'orders':[],'next_page_token':'loop'}}
        result=transport.tiktok(endpoint,token,query,body);data=result['data']
        if case=='missing_terminal':data.pop('next_page_token')
        if case=='wrong_shop':data['shop_cipher']='wrong'
        if case=='wrong_shop_id':data['shop_id']='OTHER-ID'
        if data['orders']:
            row=data['orders'][0]
            if case=='conflicting_duplicate':data['orders'].append({**row,'status':'DIFFERENT'})
            if case=='identical_duplicate':data['orders'].append(deepcopy(row))
            if case=='wrong_window':row['create_time']=body['create_time_lt']
            if case=='missing_status':row.pop('status')
            if case=='missing_detail':row.pop('line_items')
            if case=='private_fields':row.update(buyer_email='PRIVATE-SENTINEL',access_token='PRIVATE-SENTINEL');row['line_items'][0]['recipient']='PRIVATE-SENTINEL'
        return result
    def sp(endpoint,shop,token,params):
        result=transport.shopee(endpoint,shop-1,token,params);data=result['response']
        if endpoint.endswith('get_order_list'):
            if case=='missing_more':data.pop('more')
        elif data['order_list']:
            if case=='wrong_detail_id':data['order_list'][0]['order_sn']='WRONG-SAME-COUNT'
            if case=='missing_detail_row':data['order_list'].pop()
        return result
    end=datetime.fromisoformat(fixture['orders']['capturedAt'])
    result=pull.capture_orders(targets=targets,cutoff=end.isoformat(),days=31,source_id='synthetic-complete-order-capture',started_at=end.isoformat(),materialized_at=(end+timedelta(seconds=5)).isoformat(),sessions={r:{'tiktok':'synthetic-session','shopee':'synthetic-session'} for r in refresh.REGIONS},requesters={'tiktok':tk,'shopee':sp})
    return result,pull,targets

def test_explicit_order_capture_feeds_existing_stage(project):
    root,a,p,s=captured_inputs(project);source,pull,targets=capture()
    for r in refresh.REGIONS:p['captured']['targets'][r].update(tiktok=targets[r]['tiktok_cipher'],shopee=str(targets[r]['shopee_shop_id']))
    resave(a,p,'orders',source)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())
    for r in refresh.REGIONS:
        row=preview['data']['countries'][r][0]
        assert row['channels']['tiktok']['recent30Units']==1 and row['channels']['shopee']['recent30Units']==4
    assert not (a/'applied/data.js').exists()

@pytest.mark.parametrize('case',['cursor_loop','missing_terminal','wrong_shop','wrong_shop_id','conflicting_duplicate','wrong_window','missing_status','missing_detail','missing_more','wrong_detail_id','missing_detail_row'])
def test_bad_live_shaped_response_has_no_capture(case):
    with pytest.raises(ValueError):capture(case)

@pytest.mark.parametrize('case',['empty','private_fields','identical_duplicate'])
def test_complete_empty_redaction_and_exact_duplicate(case):
    source,pull,targets=capture(case)
    assert 'PRIVATE-SENTINEL' not in json.dumps(source) and 'synthetic-session' not in json.dumps(source)
    end=int(datetime.fromisoformat(source['capturedAt']).timestamp())
    for r in refresh.REGIONS:
        for platform in ('tiktok','shopee'):
            env=source['regions'][r][platform]
            assert len(env['blocks'])==(5 if platform=='tiktok' else 3)
            rows=pull.captured_order_rows(env,platform,env['target'],end-31*86400,end)
            assert len(rows)==(0 if case=='empty' else 2)
            assert all(b['pages'][0]['cursor']=='' and b['pages'][-1]['nextCursor']=='' for b in env['blocks'])
    assert source['materializedAt']!=source['capturedAt']

@pytest.mark.parametrize('case',['block_gap','block_overlap','wrong_target','missing_page','wrong_detail_id','missing_status','missing_detail','materialized_clock','target_binding','private_field','receipt_hash'])
def test_tampered_capture_is_rejected_by_actual_stage(project,case):
    root,a,p,s=captured_inputs(project);source,pull,targets=capture()
    for r in refresh.REGIONS:p['captured']['targets'][r].update(tiktok=targets[r]['tiktok_cipher'],shopee=str(targets[r]['shopee_shop_id']))
    tk=source['regions']['MY']['tiktok'];sp=source['regions']['MY']['shopee'];row=next(r for b in tk['blocks'] for page in b['pages'] for r in page['rows'])
    if case=='block_gap':tk['blocks'].pop(1)
    elif case=='block_overlap':tk['blocks'][1]['start']-=1
    elif case=='wrong_target':tk['target']='wrong'
    elif case=='missing_page':tk['blocks'][-1]['pages'].pop()
    elif case=='wrong_detail_id':sp['details'][0]['rows'][0]['order_sn']='WRONG'
    elif case=='missing_status':row.pop('status')
    elif case=='missing_detail':row.pop('line_items')
    elif case=='materialized_clock':source['materializedAt']='2026-09-01T00:00:00+00:00'
    elif case=='target_binding':source['targets']['MY']['tiktok_cipher']='wrong'
    elif case=='receipt_hash':tk['blocks'][0]['pages'][0]['receiptSha256']='0'*64
    else:row['buyer']='PRIVATE-SENTINEL'
    # Repin the modified transcript to test semantic validation, not just digest rejection.
    if case!='receipt_hash':
        for region in source['regions'].values():
            for env in region.values():
                for block in env['blocks']:
                    for page in block['pages']:pull._seal_receipt(page)
                for receipt in env.get('details',[]):pull._seal_receipt(receipt)
    resave(a,p,'orders',source);out=a/'applied';(out/'data.js').write_text('last good')
    result,value=cli(root,a,'--captured-stage');assert result.returncode!=0 and not value['ok']
    assert (out/'data.js').read_text()=='last good' and not (a/'captured-stages').exists()

def test_no_injected_session_or_targets_fails_before_any_request():
    pull=refresh.module('pull_order_demand');calls=[]
    with pytest.raises(ValueError):pull.capture_orders(targets={},cutoff='2026-09-04T08:00:00+00:00',days=31,source_id='synthetic',started_at='2026-09-04T08:00:00+00:00',materialized_at='2026-09-04T08:00:01+00:00',sessions={},requesters={'tiktok':lambda *a:calls.append(a),'shopee':lambda *a:calls.append(a)})
    assert not calls

def test_complete_four_country_empty_capture_stages_zero_with_proof(project):
    root,a,p,s=captured_inputs(project);source,pull,targets=capture('empty')
    for r in refresh.REGIONS:p['captured']['targets'][r].update(tiktok=targets[r]['tiktok_cipher'],shopee=str(targets[r]['shopee_shop_id']))
    resave(a,p,'orders',source);result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())
    assert all(row['channels'][plat]['recent30Units']==0 for rows in preview['data']['countries'].values() for row in rows for plat in ('tiktok','shopee'))

@pytest.mark.parametrize('lifecycle',['COMPLETED','UNPAID','CANCELLED','ON_HOLD','is_on_hold_order','is_sample_order','is_replacement_order'])
@pytest.mark.parametrize('payment',['missing',None,0])
def test_payment_sentinel_preserves_actual_capture_stage(project,lifecycle,payment):
    def mutate(row):
        if payment!='missing':row['paid_time']=payment
        if lifecycle.startswith('is_'):row[lifecycle]=True
        else:row['status']=lifecycle
        if lifecycle!='COMPLETED':row.pop('line_items')
    root,a,p,s=captured_inputs(project);source,pull,targets=capture(mutate_order=mutate)
    for r in refresh.REGIONS:p['captured']['targets'][r].update(tiktok=targets[r]['tiktok_cipher'],shopee=str(targets[r]['shopee_shop_id']))
    resave(a,p,'orders',source)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())
    for rows in preview['data']['countries'].values():
        assert rows[0]['channels']['tiktok']['recent30Units']==(1 if lifecycle=='COMPLETED' else 0)
        assert rows[0]['channels']['shopee']['recent30Units']==4
    assert not (a/'applied/data.js').exists()

@pytest.mark.parametrize('payment',[False,True,'0','',0.0,-1,1,1788508800,[],{}])
@pytest.mark.parametrize('excluded',[False,True])
def test_invalid_payment_rejected_in_capture_and_resealed_stage(project,payment,excluded):
    def mutate(row):
        row['paid_time']=payment
        if excluded:row['status']='UNPAID';row.pop('line_items',None)
    with pytest.raises(ValueError):capture(mutate_order=mutate)
    root,a,p,s=captured_inputs(project);source,pull,targets=capture()
    for r in refresh.REGIONS:p['captured']['targets'][r].update(tiktok=targets[r]['tiktok_cipher'],shopee=str(targets[r]['shopee_shop_id']))
    for block in source['regions']['MY']['tiktok']['blocks']:
        for page in block['pages']:
            for row in page['rows']:mutate(row)
            pull._seal_receipt(page)
    resave(a,p,'orders',source);out=a/'applied';(out/'data.js').write_text('last good')
    result,value=cli(root,a,'--captured-stage');assert result.returncode!=0 and not value['ok']
    assert (out/'data.js').read_text()=='last good' and not (a/'captured-stages').exists()
