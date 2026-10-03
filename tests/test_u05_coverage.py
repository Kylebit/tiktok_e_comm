import json,hashlib
from pathlib import Path
from datetime import date,timedelta
import pytest
from test_i05_profit_facts import captured_profile
from domains.data_operations.profit_settlement.captured_consumer import build_captured_weekly
from test_i05_profit_facts import settlement_row,snapshots
from domains.data_operations.profit_settlement.tiktok import build_monthly_estimated_report
from domains.data_operations.profit_settlement.knowledge_base import ProfitKnowledgeBase

def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def coverage_fixture(profile):
    evidence=Path(profile['evidence_path']);raw=json.loads(evidence.read_text())
    coverage={'schema_version':'profit-captured-coverage/v1','scope':{k:profile[k] for k in ('platform','site','shop_id','start','end','timezone')},'evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),'streams':{}}
    for kind,basis in [('orders','order_created_at'),('settlements','settled_at')]:
        stream={'source_id':'SYNTHETIC-'+kind,'as_of':'2026-08-10T00:00:00+07:00','time_basis':basis,'days':[]}
        for n in range(7):
            day=(date(2026,8,3)+timedelta(days=n)).isoformat()
            rows=[{'order_id':r['order_id'],'settled_at':r['settled_at']} for r in raw['orders'] if r['settled_at'][:10]==day] if kind=='settlements' else []
            if kind=='orders' and n==0:rows=[{'order_id':raw['orders'][0]['order_id'] if raw['orders'] else 'UNSETTLED','order_created_at':day+'T12:00:00+07:00','order_status':'COMPLETED'}, {'order_id':'CANCELLED','order_created_at':day+'T12:00:00+07:00','order_status':'CANCELLED'}]
            stream['days'].append({'date':day,'total_rows':len(rows),'pages':[{'cursor':'','next_cursor':'','rows':rows}]})
        coverage['streams'][kind]=stream
    path=evidence.parent/'daily-coverage.json';write(path,coverage);profile['coverage_path']=str(path)
    return coverage

def test_captured_period_end_is_unknown_without_daily_evidence(tmp_path):
    profile=captured_profile(tmp_path);result=build_captured_weekly(profile)
    assert result['coverage']['days'][-1]['settlements']['count'] is None
    assert result['coverage']['status']=='unknown'

def test_complete_zero_and_unsettled_use_existing_coverage(tmp_path):
    profile=captured_profile(tmp_path);coverage_fixture(profile)
    result=build_captured_weekly(profile)
    assert result['coverage']['days'][-1]['settlements']['count']==0
    assert result['coverage']['order_settlement']['counts']['cancelled_without_settlement']==1
    assert result['coverage']['order_settlement']['counts']['unsettled_non_cancelled']==0

@pytest.mark.parametrize('case',['missing_end','missing_page','early_asof','mismatch','missing_status'])
def test_incomplete_coverage_never_implies_zero(tmp_path,case):
    profile=captured_profile(tmp_path);coverage=coverage_fixture(profile)
    stream=coverage['streams']['settlements']
    if case=='missing_end':stream['days'].pop()
    elif case=='missing_page':stream['days'][-1]['pages'][0]['next_cursor']='missing'
    elif case=='early_asof':stream['as_of']='2026-08-08T12:00:00+07:00'
    elif case=='mismatch':coverage['scope']['site']='MY'
    else:coverage['streams']['orders']['days'][0]['pages'][0]['rows'][0].pop('order_status')
    write(Path(profile['coverage_path']),coverage)
    result=build_captured_weekly(profile)
    assert result['coverage']['status']=='unknown'
    if case!='missing_status':assert result['coverage']['days'][-1]['settlements']['count'] is None
    assert result['approval_status']=='REVIEW_ONLY'

def history_fixture(root):
    store=ProfitKnowledgeBase(root);entries=[]
    for site,shop,cost,fx in [('TH','fixture','8','0.2'),('TH','fixture','9','0.3'),('MY','fixture','8','0.2'),('TH','other-shop','8','0.2')]:
        row=settlement_row();row.update(region=site,shop_id=shop)
        costs,rates=snapshots(cost,fx)
        report=build_monthly_estimated_report([row],period_start='2026-08-01',period_end='2026-08-31',costs=costs,fx=rates,ad_rate='0.22',ad_rate_source='SYNTHETIC explicit fixture',code_version='SYNTHETIC').payload()
        entries.append(store.approve_monthly_report(report,approved_by='SYNTHETIC TEST ONLY',approved_at='2026-09-01T00:00:00Z'))
    return entries

def test_historical_index_reads_original_versions_and_isolates_scope(tmp_path):
    profile=captured_profile(tmp_path);root=tmp_path/'knowledge';entries=history_fixture(root);profile['knowledge_root']=str(root)
    before={str(p):p.read_bytes() for p in root.rglob('*.json')}
    result=build_captured_weekly(profile);found=result['history']['entries']
    assert len(found)==2
    assert {v['report_id'] for v in found}=={e.payload['report']['report_id'] for e in entries[:2]}
    assert found[0]['report']['totals']!=found[1]['report']['totals']
    assert result['approval_status']=='REVIEW_ONLY'
    assert all(v['approval_status']=='APPROVED' and v['report_id']==v['report']['report_id'] for v in found)
    assert {str(p):p.read_bytes() for p in root.rglob('*.json')}==before

@pytest.mark.parametrize('case',['tamper','path_escape','missing','scope_unproven'])
def test_unverified_history_is_not_approved(tmp_path,case):
    profile=captured_profile(tmp_path);root=tmp_path/'knowledge';entries=history_fixture(root);profile['knowledge_root']=str(root)
    if case=='tamper':
        value=json.loads(entries[0].path.read_text());value['report']['totals']['profit_cny']='999';write(entries[0].path,value)
    elif case=='path_escape':
        value=json.loads((root/'index.json').read_text());value[0]['artifact_path']='../../outside.json';write(root/'index.json',value)
    elif case=='missing':entries[0].path.unlink()
    else:
        # A modified scope cannot inherit the original approval checksum.
        value=json.loads(entries[0].path.read_text());value['report']['order_lines']=[];write(entries[0].path,value)
    result=build_captured_weekly(profile)
    assert result['history']['issues'] and result['approval_status']=='REVIEW_ONLY'

def test_asof_and_fx_revision_do_not_overwrite_report_identity(tmp_path):
    profile=captured_profile(tmp_path);coverage=coverage_fixture(profile)
    first=build_captured_weekly(profile)
    coverage['streams']['settlements']['as_of']='2026-08-11T00:00:00+07:00';write(Path(profile['coverage_path']),coverage)
    later=build_captured_weekly(profile)
    assert first['review_id']!=later['review_id']
    assert first['reports']['tiktok']['report']['report_id']==later['reports']['tiktok']['report']['report_id']
    override=build_captured_weekly({**profile,'fx_overrides':{'THB':'0.3'}})
    assert override['reports']['tiktok']['report']['report_id']!=later['reports']['tiktok']['report']['report_id']

def test_complete_no_settlement_does_not_invent_profit(tmp_path):
    profile=captured_profile(tmp_path);path=Path(profile['evidence_path']);e=json.loads(path.read_text());e['orders']=[];e['net_settlement_total_local']='0';write(path,e)
    coverage_fixture(profile);result=build_captured_weekly(profile)
    assert all(d['settlements']['count']==0 for d in result['coverage']['days'])
    assert result['coverage']['order_settlement']['counts']['unsettled_non_cancelled']==1
    assert result['reports']['tiktok']['report']['totals']['profit_cny'] is None
