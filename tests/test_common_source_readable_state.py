"""Normal read-only stage refresh exposes retained facts, never write power."""
from modules.products import server
from test_b4b_common_stage import context
from test_r3_common_source_facts import connect, rows


def test_common_stage_refresh_reads_persisted_source_without_run_or_approval(tmp_path, monkeypatch):
    _, _, store, request = context(tmp_path, monkeypatch)
    code, draft = server._preview_r3_common_stage(request)
    assert code == 200 and not store.path.exists()
    plan = store.create_plan(draft['common']['plan']['payload'])
    with connect(store) as db:
        before = rows(db)
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 0
    code, view = server._preview_r3_common_stage(request)
    assert code == 200 and view['common']['status'] == 'TECHNICAL_CONDITIONS_UNKNOWN'
    technical = view['common']['technical_admission']
    assert technical['source_facts']['status'] == 'RETAINED_IDENTITY_VERIFIED'
    assert technical['source_facts']['origin_binding']['plan_id'] == plan['plan_id']
    assert technical['execution_authority'] is technical['final_review_available'] is False
    assert technical['status'] == 'BLOCKED'
    assert technical['authority_facts'] == {
        'account_authority': 'UNKNOWN', 'standing_policy_authority': 'UNKNOWN',
        'coverage_authority': 'UNKNOWN', 'schema_installation': 'UNKNOWN'}
    source = technical['source_facts']
    assert source['source_coverage'] == 'LOCAL_RETAINED_ONLY'
    assert source['official_provenance'] == source['budget_status'] == 'UNKNOWN'
    assert source['execution_authority'] is False
    assert source['common_plan_ids'] == source['unstarted_common_plan_ids'] == [plan['plan_id']]
    assert all(row['official_provenance'] == 'UNKNOWN' for row in source['records'])
    assert all(row['table'] == 'release_plans' for row in source['records'])
    assert source['native_local_census'] == {
        'schema_version': 'native-common-local-write-census/v1', 'status': 'UNKNOWN',
        'reason': 'LEGACY_PREPARATION_ROOT_UNKNOWN', 'confirmed_write_count': 'UNKNOWN',
        'local_observed_confirmed_writes': 0, 'local_observed_readonly_reuses': 0,
        'unresolved_attempts': [], 'unclassified_history': [],
        'source_coverage': 'LOCAL_RETAINED_ONLY', 'coverage_authority': 'UNKNOWN',
        'account_authority': 'UNKNOWN', 'schema_installation': 'UNKNOWN',
        'budget_authority': 'UNKNOWN', 'execution_authority': False}
    assert technical['blockers'] == ['COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN',
                                     'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN']
    assert view['external_writes_performed'] == []
    with connect(store) as db:
        assert rows(db) == before


def test_common_stage_preview_does_not_create_source_or_accept_caller_authority(tmp_path, monkeypatch):
    _, _, store, request = context(tmp_path, monkeypatch)
    before = set(tmp_path.rglob('*'))
    code, view = server._preview_r3_common_stage({**request,
        'technical_admission': {'status': 'READY', 'execution_authority': True},
        'source_facts': {'status': 'RETAINED_IDENTITY_VERIFIED'},
        'store': {'verified': True}})
    assert code == 200 and not store.path.exists()
    technical = view['common']['technical_admission']
    assert technical['source_facts']['status'] == 'UNKNOWN'
    assert technical['execution_authority'] is technical['final_review_available'] is False
    assert technical['status'] == 'BLOCKED' and not technical.get('receipt_digest')
    assert view['external_writes_performed'] == []
    assert set(tmp_path.rglob('*')) == before
