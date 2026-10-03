"""Original producer lineage; all product/media/HTTP fixtures are owned locally.

This tests observation ownership, not native mutation admission or budget authority.
No frozen R1/R2 digest is transplanted from an independent fixture.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from modules.products import server
from modules.sourcing import new_product_workbench as workbench
from shared_platform import operations_publication_common as common
from shared_platform import publication_rounds, publication_r3_image_bridge as bridge
from shared_platform import round1_workspace, release_control, workbench_publication_native as native
from shared_platform.workbench_engine import WorkbenchEngine
from owned_native_parent_profile import owned_profile
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import complete_source, public_settings, request
from test_round1_initial_options import choice
import test_publication_paid_entry as media


def _image_proposal():
    # Only an agent-proposed image plan is reused before preparation. No fixture
    # approval, immutable snapshot, source digest or R2 report is reused.
    plan = deepcopy(media.round1()['image_plan'])
    plan.update(schema_version='first-review-image-plan/v1', status='PROPOSED',
                source_actions=[], generated_assets=[],
                summary={'translation_positions': [], 'localized_output_count': 0,
                         'net_new_output_count': 14, 'paid_generation_required': True})
    plan['translation_plan'].update(decision_basis='REVIEW_ALL_GENERATED_IMAGES_FIRST',
                                   note='Owned fake media only; defer until masters exist.')
    for brand in plan['brand_plans']:
        brand.update(target_group='SEA', generation_mode='NEW_SET_VIA_IMAGE_API',
                     category_guidance=[{'platform': 'tiktok', 'category_id': 'fixture-home',
                        'category_zh': '家居装饰', 'recommendation': 'Owned source product roles'}])
    return plan


def _native_common_chain(live, monkeypatch, *, active_release_probe=None):
    from modules.miaoshou import client as miaoshou
    monkeypatch.setattr(miaoshou, 'post_open',
        lambda *a, **k: pytest.fail('Native source observation must not call Miaoshou'))
    complete_source(live)
    root, offer = live['root'], live['offer']
    directory = publication_rounds.report_dir(offer)
    proposal = media.read(directory/'first-review.json')
    proposal['image_execution_plan'] = _image_proposal()
    media.write(directory/'first-review.json', proposal)
    # The old live fixture keeps only a three-field image proposal at this
    # filename. R3 requires the complete actual prepared packet. Materialize the
    # real producer output BEFORE immutable prepare/freeze, not by rewriting a
    # frozen first_review_digest. Request marketplaces only: COMMON is the
    # technical stage, not one of the later release destinations.
    dashboard = release_control.build_release_dashboard(offer_id=offer)
    scope = {'offer_id': offer, 'product_center_revision': 7,
             'requested_targets': [t for t in dashboard['publication_scope']['selected_labels']
                                   if t != 'miaoshou:COMMON'], 'source_region': 'MY'}
    code, context = live['call']('context',
        {**scope, 'requested_targets': json.dumps(scope['requested_targets'])}, 'GET')
    assert code == 200, context
    options_request = {**scope, 'schema_version': 'round1-category-options-request/v1',
        'request_id': 'native-common-options-' + offer, 'context_digest': context['context_digest'],
        'account_identity_digest': context['source_account']['account_identity_digest']}
    code, options = live['call']('options', options_request)
    assert code == 200 and options['status'] == 'SUCCEEDED', options
    selected = choice(options_request, options)
    selected['request_id'] = 'native-common-capture-' + offer
    code, capture = live['call']('capture', selected)
    assert code == 200 and capture['status'] == 'SUCCEEDED', capture
    preparation_request = {**scope, 'context_digest': context['context_digest'],
        'account_identity_digest': context['source_account']['account_identity_digest'],
        'observer_reference': capture['observer_reference'], 'request_id': 'native-common-prepare-' + offer}
    # Proposed copy comes from the fixed producer input BEFORE any immutable
    # preparation, not from a frozen-review overlay or a replaced packet.
    from test_round1_sidecar_convergence import candidate
    assert 'tiktok:LH_PH' in scope['requested_targets']
    candidate_plan = candidate('tiktok:LH_PH')
    copy = candidate_plan['target_candidates'][0]['copy']
    copy['language'] = 'en'
    copy['title'] = dashboard['product']['title']
    copy['variants'][0]['seller_sku'] = dashboard['product']['seller_sku_candidate']
    round1_workspace._module(server)._safe_candidate_plan(candidate_plan, scope['requested_targets'])
    candidate_path = directory/'first-review-candidate-plan.json'
    candidate_bytes = (json.dumps(candidate_plan, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
    candidate_path.write_bytes(candidate_bytes)
    actual_packet, _, _ = round1_workspace._packet(server, preparation_request, bound=True)
    assert actual_packet['status'] == 'FIRST_REVIEW_READY', actual_packet
    media.write(directory/'first-review.json', actual_packet)
    code, packet = live['call']('prepare', preparation_request)
    assert code == 200 and packet['status'] == 'PREPARED', packet
    assert packet['packet'] == actual_packet
    profile = owned_profile(root)
    targets = sorted(packet['packet']['target_selection']['requested'])
    engine = WorkbenchEngine(profile.data_root/'tasks.db',
        {'code_version': profile.version, 'environment': profile.environment,
         'manifest_digest': profile.manifest_digest})
    engine.register_executor('native-source-worker', ['publication'], engine.release)
    task = engine.create({'template': 'publication', 'source_key': 'native-source-'+offer,
                         'scope': {'offer_id': offer, 'shops': targets}})
    token = engine.claim(task['task_id'], 'native-source-worker', ttl=300)['lease_token']
    engine.record_checkpoint(task['task_id'], token,
        {'native_preparation': {'prepared_reference': packet['prepared_reference']}})
    frozen_result = round1_workspace.auto_freeze(server, request(packet, offer))
    assert frozen_result['status'] == 'FROZEN' and frozen_result['human_approval'] is False
    frozen = native.read_frozen(engine.get(task['task_id']), profile)
    source_bytes = (directory/'round1-approved-snapshot.json').read_bytes()
    preparation = round1_workspace.read_preparation(offer, frozen['prepared_reference'])
    assert preparation['reference'] == packet['prepared_reference']
    candidate_source = preparation['candidate_input']
    assert candidate_source['present'] is True
    assert candidate_source['raw_sha256'] == hashlib.sha256(candidate_bytes).hexdigest()
    assert candidate_source['byte_length'] == len(candidate_bytes)
    import base64
    assert base64.b64decode(candidate_source['raw_base64'], validate=True) == candidate_bytes
    assert candidate_path.read_bytes() == candidate_bytes
    # Reuse only the existing closed media transport. Immediately restore the
    # real state reader which Workflow otherwise replaces for its own fixture.
    actual_load_state = workbench.load_state
    monkeypatch.setattr(media, 'OFFER', offer)
    workflow = media.Workflow(root, monkeypatch, initialize=False)
    monkeypatch.setattr(workbench, 'load_state', actual_load_state)
    media.write(root/'config/product_publication_autopilot_policy.json', media.policy(100))
    media.write(directory/'brand-image-reuse-plan.json', {
        'schema_version': 'brand-image-reuse-plan/v1', 'offer_id': offer, 'status': 'APPROVED',
        'items': [{'brand_id': brand['id'], 'role': role['role'], 'decision': 'GENERATE'}
                  for brand in workflow.round1['image_plan']['brand_plans']
                  for role in brand['generated_assets']]})
    assert workflow.masters()['completed_brand_image_count'] == 14
    assert workflow.master_qa()['status'] == 'PASSED'
    generation = media.read(directory/'brand-image-generation.json')
    plan = workflow.entry.build_approved_brand_translation_plan(offer,
        generation=generation, selections={1: ['ms-MY', 'es-MX'], 2: ['ms-MY', 'es-MX'],
                                          3: ['ms-MY', 'es-MX']},
        approved_by='orbit-product-publication-default-v1')
    media.write(directory/'brand-image-translation-plan.json', plan)
    workflow.entry.run(workflow.args(execute_paid=True))
    assert workflow.master_qa()['status'] == 'PASSED'
    documents = bridge.load_r2_documents(offer)
    identity = bridge.validate_r2_identity(documents)
    assert identity['round1_snapshot_digest'] == frozen['snapshot_digest']
    assert (directory/'round1-approved-snapshot.json').read_bytes() == source_bytes
    # The original auto-frozen owner is consumed through the same trusted
    # service context as startup. Construction does not start an actor/helper
    # or grant market execution; these tests retain their observation scope.
    from types import SimpleNamespace
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.native_windows_actor import NativeActorServiceConfig
    service = NativeSoleFinalService(live['store'],
        NativeActorServiceConfig(root/'native-source-reader-owner','Kyle'),
        operations=SimpleNamespace(engine=engine,profile=profile),
        new_decision_execution_enabled=False)
    monkeypatch.setattr(server,'_NATIVE_FINAL_SERVICE',service)
    assert service._operations.engine is engine and service._operations.profile is profile
    assert native.load_service_r2_documents(offer,service._operations)==documents
    assert native.read_frozen(engine.get(task['task_id']),profile)==frozen
    code, preview = server._preview_r3_common_stage({
        'offer_id': offer, 'release_stage': 'R3_COMMON',
        'publication_targets': ['miaoshou:COMMON']})
    assert code == 200 and preview['ok'] is True, preview
    stage = preview['common']['plan']['payload']['r3_stage_binding']
    assert stage['round1_snapshot_digest'] == frozen['snapshot_digest']
    assert stage['r2_identity'] == identity
    from shared_platform.internal_catalog_sku import internal_sku
    sku = internal_sku(preview['common']['plan']['payload']['seller_sku'])
    assert sku == frozen['internal_sku']
    engine.bind_scope(task['task_id'], token,
        {**task['scope'], 'skus': [sku]})
    engine.complete_step(task['task_id'], token, expected_step='facts',
                         checkpoint={'native_r1': frozen})
    engine.complete_step(task['task_id'], token, expected_step='images',
                         checkpoint={'native_r2': identity})
    original = engine.get(task['task_id'])
    assert native.read_frozen(original, profile) == frozen
    if active_release_probe is not None:
        assert callable(active_release_probe)
        active_release_probe(engine, original, token, profile)
        assert candidate_path.read_bytes() == candidate_bytes
        return engine, engine.get(task['task_id']), profile, frozen
    assert common.run(engine, original, token, profile, server_module=server) is False
    waiting = engine.get(task['task_id'])
    assert waiting['execution_state'] == 'waiting_domain'
    assert waiting['checkpoint']['common_technical_admission']['status'] == 'BLOCKED'
    assert preview['external_writes_performed'] == []
    assert candidate_path.read_bytes() == candidate_bytes
    return engine, waiting, profile, frozen


def test_common_observation_rejects_fresh_durable_copy_of_original_owner(live, monkeypatch):
    engine, original, profile, frozen = _native_common_chain(live, monkeypatch)
    original_common = common._read(server, original)['common']
    second = engine.create({'template': 'publication', 'source_key': 'copied-native-source',
                            'scope': original['scope']})
    # Adversarial copy into this OWNED fixture DB, including a later durable
    # event. Original owner remains the earlier actual checkpoint_saved row.
    with engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET checkpoint_json=?,steps_json=?, '
                     'action_json=?,state=? WHERE task_id=?',
                     (json.dumps(original['checkpoint']), json.dumps(original['steps']),
                      json.dumps(original['pending_observation']), 'waiting_domain', second['task_id']))
        engine._event(conn, second['task_id'], 'checkpoint_saved',
                      {'checkpoint': {'native_preparation':
                          {'prepared_reference': frozen['prepared_reference']}}})
        engine._event(conn, second['task_id'], 'domain_observation_pending',
                      original['pending_observation'])
    copied = engine.get(second['task_id'])
    # Existing real R1 guard proves this is the wrong owner, not malformed setup.
    with pytest.raises(native.NativeR1ReconciliationRequired,
                       match='NATIVE_R1_ORIGINAL_TASK_OWNER_CONFLICT'):
        native.read_frozen(copied, profile)
    current_common = original_common  # Original real pre-copy display, never a fresh authority claim.
    display_binding = common._binding(copied, current_common)
    with pytest.raises(native.NativeR1ReconciliationRequired,
                       match='NATIVE_R1_ORIGINAL_TASK_OWNER_CONFLICT'):
        common._verified_binding(copied, current_common, profile)
    before = live['store'].path.read_bytes()
    reread = common._read(server, copied)
    assert reread['error'] == 'NATIVE_R2_ORIGINAL_OWNER_AMBIGUOUS'
    assert reread['stage'] == 'RECONCILIATION_REQUIRED' and 'common' not in reread
    assert reread['external_writes_performed'] == []
    assert live['store'].path.read_bytes() == before
    assert display_binding == common._binding(original, current_common)
    directory = publication_rounds.report_dir(frozen['offer_id'])
    media.write(live['root']/'native-common-source-chain-observation.json', {
        'scope': 'owned fixture; source ownership only, no business authority',
        'original_task_id': original['task_id'], 'copied_task_id': copied['task_id'],
        'prepared_reference': frozen['prepared_reference'],
        'round1_snapshot_digest': frozen['snapshot_digest'],
        'round1_raw_sha256': hashlib.sha256(
            (directory/'round1-approved-snapshot.json').read_bytes()).hexdigest(),
        'r2_identity': bridge.validate_r2_identity(bridge.load_r2_documents(frozen['offer_id'])),
        'common_display_binding_alias_observed': display_binding,
        'original_actual_observation_binding': original['pending_observation']['receipt_binding'],
        'native_original_owner_rejection': 'NATIVE_R1_ORIGINAL_TASK_OWNER_CONFLICT',
        'common_write_admission': original['checkpoint']['common_technical_admission']})
    # The real consumer preserves the earlier ambiguous-source reconciliation.
    assert common.observe(engine, copied, profile, server_module=server) is False
    held = engine.get(copied['task_id'])
    assert held['execution_state'] == 'reconciliation_required'
    assert held['checkpoint'] == copied['checkpoint']
    with engine.transaction() as conn:
        assert engine._row(conn, copied['task_id'])['external_started'] == 0
    assert live['store'].path.read_bytes() == before


def test_legacy_missing_preparation_never_upgrades_common_write_authority(tmp_path, monkeypatch):
    from test_operations_publication_common import setup, claim
    engine, task_id, profile, provider, _, _ = setup(tmp_path, monkeypatch,
        synthetic_technical_authority=False)
    assert common.run(engine, engine.get(task_id), claim(engine, task_id),
                      profile, server_module=server) is False
    waiting = engine.get(task_id)
    diagnostic = waiting['checkpoint']['common_technical_admission']
    assert diagnostic['status'] == 'BLOCKED' and diagnostic['blockers']
    assert not diagnostic.get('receipt_digest')
    assert provider.mutations == 0
    assert waiting['execution_state'] == 'waiting_domain'
