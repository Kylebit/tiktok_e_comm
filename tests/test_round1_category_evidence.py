"""Evidence identity and actual R1 preparation/freeze boundaries (offline fixtures)."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest
from shared_platform import publication_rounds as rounds
from shared_platform import round1_category_evidence as evidence
from shared_platform.publication_stock_policy import default_publication_stock_policy

ROOT = Path(__file__).resolve().parents[1]


def prepare_module():
    spec = importlib.util.spec_from_file_location('r1_prepare_fixture', ROOT / 'skills/prepare-product-publication/scripts/prepare_product_publication.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def review():
    return {'schema': 'publication-preparation-decision/v1', 'offer_id': '12345',
            'product_center_revision': 7, 'status': 'FIRST_REVIEW_READY',
            'publication_stock_policy': default_publication_stock_policy(),
            'product_facts': {'title': 'Fixture product', 'category_semantic': 'Fixture category'},
            'target_selection': {'requested': ['shopee:MY', 'shopee:PH']},
            'targets': [{'target': 'shopee:MY'}, {'target': 'shopee:PH'}],
            'image_execution_plan': {'schema_version': 'first-review-image-plan/v1', 'status': 'PROPOSED'}}


def test_missing_category_cannot_claim_first_review_ready():
    module = prepare_module()
    preview = {'ok': True, 'product': {'revision': 7, 'title': 'Fixture product'},
               'publication_scope': {'selected_labels': ['shopee:MY']}}
    packet = module.prepare_offer(offer_id='12345', requested_targets=['shopee:MY'], preview_builder=lambda _: preview)
    assert packet['status'] == 'DECISION_REQUIRED'


@pytest.mark.parametrize('legacy', [
    {'status': 'CONFIRMED', 'authority': 'SHOPEE_OFFICIAL'},
    {'status': 'CONFIRMED', 'authority': 'SHOPEE_OFFICIAL', 'offer_id': '99999', 'missing_required_attributes': None},
], ids=['missing-fields', 'wrong-offer-null-attributes'])
def test_unbound_sidecar_cannot_pass_freeze_gate(tmp_path, legacy):
    (tmp_path / 'shopee-category-review.json').write_text(json.dumps(legacy), encoding='utf-8')
    with pytest.raises(rounds.PublicationRoundContractError):
        rounds.validate_round1_reviewable(review(), report_directory=tmp_path)


def fixture_observation(packet):
    return dict(schema_version=evidence.OBSERVATION_SCHEMA, offer_id=packet['offer_id'],
                product_center_revision=packet['product_center_revision'],
                requested_targets=evidence.shopee_targets(packet), source_region='MY',
                observation_scope=evidence.SCOPE, category_input_digest=evidence.input_digest(packet),
                observer_reference='category-observation:fixture1', observed_at='2026-09-07T00:00:00+00:00',
                authority='shopee_official_category_get',
                category=dict(id=2, name='Fixture leaf', path=[dict(id=1, name='Root'), dict(id=2, name='Fixture leaf')],
                              path_complete=True, is_leaf=True, publishable=True),
                attribute_tree=[dict(attribute_id=11, is_mandatory=True, value_ids=[22])],
                selected_attributes=[dict(attribute_id=11, value_ids=[22])],
                attributes_complete=True, required_attribute_count=1, missing_required_attributes=[],
                regional_publishability='NOT_VERIFIED', business_write_count=0, auth_writes=0, official_read_count=1)


def fixture_binding():
    packet = review()
    packet['category_review_context'] = dict(source_region='MY', observation_scope=evidence.SCOPE)
    observation = fixture_observation(packet)
    receipt = evidence.build_receipt(observation)
    packet['category_evidence_binding'] = dict(status='BOUND', receipt=receipt)
    for row in packet['targets']:
        row['category'] = dict(status='EVIDENCE_BOUND', id=2, name='Fixture leaf', receipt_digest=receipt['receipt_digest'])
    return packet, receipt, observation


def test_source_cannot_be_created_by_self_consistent_json(tmp_path):
    packet, receipt, observation = fixture_binding()
    with pytest.raises(evidence.CategoryEvidenceError, match='SOURCE_UNVERIFIED'):
        evidence.validate_receipt(packet, receipt)
    with pytest.raises(rounds.PublicationRoundContractError, match='SOURCE_UNVERIFIED'):
        rounds.validate_round1_reviewable(packet, report_directory=tmp_path)
    for resolver in [lambda _: None, lambda _: {**observation, 'source_region': 'PH'}]:
        with pytest.raises(evidence.CategoryEvidenceError, match='CATEGORY_SOURCE_REFERENCE_MISMATCH'):
            evidence.validate_receipt(packet, receipt, observation_resolver=resolver)
    def unavailable(_):
        raise RuntimeError('fixture private resolver detail')
    with pytest.raises(evidence.CategoryEvidenceError, match='^SOURCE_UNVERIFIED$'):
        evidence.validate_receipt(packet, receipt, observation_resolver=unavailable)


@pytest.mark.parametrize('field,value', [
    ('offer_id', '99999'), ('product_center_revision', 8), ('product_center_revision', True),
    ('requested_targets', ['shopee:PH', 'shopee:MY']), ('requested_targets', ['shopee:MY']),
    ('source_region', 'PH'), ('observation_scope', 'REGIONAL'), ('category_input_digest', 'sha256:wrong'),
], ids=['offer', 'revision', 'bool-revision', 'order', 'target-subset', 'region', 'scope', 'input-digest'])
def test_identity_group_rejects_even_rehashed_observation(field, value):
    packet, _, observation = fixture_binding()
    observation[field] = value
    with pytest.raises(evidence.CategoryEvidenceError):
        evidence.validate_receipt(packet, evidence.build_receipt(observation), observation_resolver=lambda _: observation)


@pytest.mark.parametrize('field,value', [('title', 'Changed product'), ('category_semantic', 'Changed category')])
def test_input_facts_drift_group(field, value):
    packet, receipt, observation = fixture_binding()
    packet['product_facts'][field] = value
    with pytest.raises(evidence.CategoryEvidenceError, match='CATEGORY_CONTEXT_MISMATCH'):
        evidence.validate_receipt(packet, receipt, observation_resolver=lambda _: observation)


@pytest.mark.parametrize('field,value', [
    ('id', True), ('name', ''), ('path', []), ('path', [dict(id=1, name='Wrong tail')]),
    ('path_complete', False), ('is_leaf', False), ('publishable', None),
])
def test_leaf_path_group(field, value):
    packet, _, observation = fixture_binding()
    observation['category'][field] = value
    with pytest.raises(evidence.CategoryEvidenceError, match='CATEGORY_LEAF_INVALID'):
        evidence.validate_receipt(packet, evidence.build_receipt(observation), observation_resolver=lambda _: observation)


@pytest.mark.parametrize('field,value', [
    ('missing_required_attributes', None), ('missing_required_attributes', ['11']),
    ('attributes_complete', None), ('required_attribute_count', True), ('required_attribute_count', 0),
    ('attribute_tree', None), ('selected_attributes', []),
    ('selected_attributes', [dict(attribute_id=11, value_ids=[999])]),
    ('selected_attributes', [dict(attribute_id=11, value_ids=[True])]),
    ('attribute_tree', [dict(attribute_id=11, is_mandatory=None, value_ids=[22])]),
])
def test_attribute_group(field, value):
    packet, _, observation = fixture_binding()
    observation[field] = value
    with pytest.raises(evidence.CategoryEvidenceError, match='CATEGORY_ATTRIBUTES_INVALID'):
        evidence.validate_receipt(packet, evidence.build_receipt(observation), observation_resolver=lambda _: observation)


@pytest.mark.parametrize('field,value', [
    ('authority', 'SHOPEE_OFFICIAL'), ('business_write_count', 1), ('business_write_count', False),
    ('auth_writes', False), ('official_read_count', 0), ('regional_publishability', 'READY'),
    ('observed_at', '2026-09-07T00:00:00'), ('observer_reference', 'C:/arbitrary.json'),
])
def test_observation_metadata_group(field, value):
    packet, _, observation = fixture_binding()
    observation[field] = value
    with pytest.raises(evidence.CategoryEvidenceError):
        evidence.validate_receipt(packet, evidence.build_receipt(observation), observation_resolver=lambda _: observation)


@pytest.mark.parametrize('mutation', ['receipt-digest', 'observation-digest', 'extra-key', 'legacy'])
def test_receipt_envelope_group(mutation):
    packet, receipt, observation = fixture_binding()
    if mutation == 'receipt-digest':
        receipt['category']['name'] = 'Tampered'
    elif mutation == 'observation-digest':
        receipt['observation_digest'] = 'sha256:wrong'
        receipt['receipt_digest'] = evidence.digest({k: v for k, v in receipt.items() if k != 'receipt_digest'})
    elif mutation == 'extra-key':
        receipt['trusted'] = True
    else:
        receipt = {'schema_version': 'shopee-category-official-review/v1'}
    with pytest.raises(evidence.CategoryEvidenceError):
        evidence.validate_receipt(packet, receipt, observation_resolver=lambda _: observation)


@pytest.mark.parametrize('raw', ['null', '{"x":1,"x":2}', '{"x":NaN}', '{broken', '[]'])
def test_json_reference_group(tmp_path, raw):
    path = tmp_path / 'reference.json'; path.write_text(raw, encoding='utf-8')
    with pytest.raises(evidence.CategoryEvidenceError):
        evidence.read_receipt(path)


def test_bounded_reference(tmp_path):
    path = tmp_path / 'reference.json'; path.write_bytes(b' ' * (evidence.LIMIT + 1))
    with pytest.raises(evidence.CategoryEvidenceError, match='CATEGORY_RECEIPT_TOO_LARGE'):
        evidence.read_receipt(path)


@pytest.mark.parametrize('targets', [None, 'shopee:MY', ['shopee:SG'], ['shopee:MY', 'shopee_my'], [None]])
def test_target_shape_group(targets):
    with pytest.raises(evidence.CategoryEvidenceError, match='CATEGORY_TARGETS_INVALID'):
        evidence.shopee_targets({'target_selection': {'requested': targets}})


@pytest.mark.parametrize('mutation', ['id', 'name', 'digest', 'missing-row', 'order'])
def test_freeze_projection_group(tmp_path, mutation):
    packet, _, observation = fixture_binding()
    if mutation == 'missing-row':
        packet['targets'].pop()
    elif mutation == 'order':
        packet['targets'].reverse()
    else:
        key = 'receipt_digest' if mutation == 'digest' else mutation
        packet['targets'][0]['category'][key] = 'wrong'
    with pytest.raises(rounds.PublicationRoundContractError, match='CATEGORY_TARGET_PROJECTION_MISMATCH'):
        rounds.validate_round1_reviewable(packet, report_directory=tmp_path, category_observation_resolver=lambda _: observation)


def state():
    return {'_revision': 8, 'review': {'selected_sites': []},
            'product_approval': {'status': 'approved', 'approved_by': 'Kyle', 'approval_id': 'fixture', 'input_fingerprint': 'fixture-fingerprint'}}


def test_freeze_binds_reviewed_object_without_sidecar_reread(tmp_path, monkeypatch):
    packet, receipt, observation = fixture_binding()
    (tmp_path / 'shopee-category-review.json').write_text('{replaced file', encoding='utf-8')
    monkeypatch.setattr(rounds, '_read_json', lambda _: pytest.fail('freeze must not read mutable sidecar'))
    calls = []
    def resolver(reference):
        calls.append(reference)
        return deepcopy(observation)
    snapshot = rounds.build_round1_snapshot(first_review=packet, state=state(), approved_by='Kyle',
        approved_at='2026-09-07T00:00:00+00:00', report_directory=tmp_path, category_observation_resolver=resolver)
    assert calls == ['category-observation:fixture1']
    assert snapshot['fact_snapshot']['category_evidence_binding']['receipt'] == receipt
    assert snapshot['first_review_digest'] == rounds.canonical_digest(packet)
    receipt['category']['name'] = 'Later mutation'
    assert snapshot['fact_snapshot']['category_evidence_binding']['receipt']['category']['name'] == 'Fixture leaf'


def preparation_fixture(module):
    preview = {'ok': True, 'revision': 7, 'review': {'title': 'Fixture product', 'category': 'Fixture category',
               'selected_sites': ['shopee:MY', 'shopee:PH']}}
    preview['review'].update(cost_cny=8,weight_kg=0.2,package_cm=[20,20,3])
    preview['pricing']={'sea':[{'id':'shopee_my','list_price':20,'currency':'MYR'},
                               {'id':'shopee_ph','list_price':200,'currency':'PHP'}]}
    plan = dict(schema_version='first-review-image-plan/v1', status='PROPOSED', source_actions=[], generated_assets=[],
                summary=dict(translation_positions=[], localized_output_count=0, net_new_output_count=0, paid_generation_required=False))
    kwargs = dict(offer_id='12345', requested_targets=['shopee:MY', 'shopee:PH'], category_source_region='MY',
                  preview_builder=lambda _: deepcopy(preview), image_execution_plan=plan)
    packet = module.prepare_offer(**kwargs)
    observation = fixture_observation(packet)
    return kwargs, observation, preview


def test_actual_prepare_to_freeze_with_test_only_resolver(tmp_path):
    module = prepare_module(); kwargs, observation, _ = preparation_fixture(module)
    receipt = evidence.build_receipt(observation)
    blocked = module.prepare_offer(**kwargs, category_receipt=receipt)
    assert blocked['category_evidence_binding']['status'] == 'SOURCE_UNVERIFIED'
    packet = module.prepare_offer(**kwargs, category_receipt=receipt, category_observation_resolver=lambda _: observation)
    assert packet['status'] == 'FIRST_REVIEW_READY'
    snapshot = rounds.build_round1_snapshot(first_review=packet, state=state(), approved_by='Kyle', report_directory=tmp_path,
                                           category_observation_resolver=lambda _: observation)
    assert snapshot['fact_snapshot']['category_evidence_binding']['receipt'] == receipt
    assert packet['external_write_count'] == snapshot['external_write_count'] == 0


def test_actual_cli_file_never_supplies_trusted_resolver(tmp_path, monkeypatch, capsys):
    module = prepare_module(); _, observation, preview = preparation_fixture(module)
    path = tmp_path / 'category.json'; path.write_text(json.dumps(evidence.build_receipt(observation)), encoding='utf-8')
    output = tmp_path / 'first-review.json'
    monkeypatch.setattr(module, '_default_preview_builder', lambda: lambda _: deepcopy(preview))
    assert module.main(['--offer-id', '12345', '--targets', 'shopee:MY,shopee:PH', '--category-source-region', 'MY',
                        '--category-review', str(path), '--output', str(output)]) == 0
    packet = json.loads(output.read_text(encoding='utf-8'))
    assert packet['status'] == 'DECISION_REQUIRED'
    assert packet['category_evidence_binding']['code'] == 'SOURCE_UNVERIFIED'
    assert json.loads(capsys.readouterr().out) == packet


def test_existing_frozen_shopee_snapshot_loads_without_reapproval(tmp_path, monkeypatch):
    # Literal legacy format fixture: represents already frozen bytes, not a new approval.
    legacy = dict(schema_version=rounds.ROUND1_SCHEMA, status='APPROVED', offer_id='12345',
                  canonical_targets=['shopee:MY'], workbench_tiktok_sites=[], fact_snapshot={},
                  product_approval_id='fixture', product_approval_fingerprint='fixture-fingerprint')
    legacy['snapshot_digest'] = rounds.canonical_digest(legacy)
    path = tmp_path / '12345' / 'round1-approved-snapshot.json'; path.parent.mkdir()
    original = (json.dumps(legacy, indent=2) + '\n').encode(); path.write_bytes(original)
    monkeypatch.setattr(rounds, 'REPORTS_ROOT', tmp_path)
    assert rounds.load_round1_snapshot('12345') == legacy
    assert rounds.validate_round2_input('12345', state()) == legacy
    assert path.read_bytes() == original


def test_unknown_request_counts_remain_unknown_under_test_resolver():
    packet, _, observation = fixture_binding()
    observation['official_read_count'] = observation['auth_writes'] = None
    receipt = evidence.build_receipt(observation)
    result = evidence.validate_receipt(packet, receipt, observation_resolver=lambda _: observation)
    assert result['official_read_count'] is result['auth_writes'] is None


@pytest.mark.parametrize('revision', [None, True, '7', -1])
def test_prepare_does_not_coerce_missing_or_malformed_revision(revision):
    module = prepare_module(); kwargs, observation, preview = preparation_fixture(module)
    preview['revision'] = revision
    kwargs['preview_builder'] = lambda _: preview
    packet = module.prepare_offer(**kwargs, category_receipt=evidence.build_receipt(observation),
                                  category_observation_resolver=lambda _: observation)
    assert packet['status'] == 'DECISION_REQUIRED'
    assert packet['product_center_revision'] == revision


def test_non_shopee_reference_path_is_not_read(tmp_path):
    module = prepare_module()
    assert module._read_category_reference(tmp_path / 'absent.json', '12345', ['tiktok:LH_MY']) is None


@pytest.mark.parametrize('fixture', ['b4b_r2_actual', 'b4b_common_actual'])
def test_repository_frozen_bytes_still_consumed_by_r2(tmp_path, monkeypatch, fixture):
    source = ROOT / 'tests' / 'fixtures' / fixture / 'round1-approved-snapshot.json'
    original = source.read_bytes(); snapshot = json.loads(original)
    path = tmp_path / snapshot['offer_id'] / 'round1-approved-snapshot.json'
    path.parent.mkdir(); path.write_bytes(original)
    monkeypatch.setattr(rounds, 'REPORTS_ROOT', tmp_path)
    prior_state = {'product_approval': {'status': 'approved', 'approval_id': snapshot['product_approval_id'],
                   'input_fingerprint': snapshot['product_approval_fingerprint']},
                   'review': {'selected_sites': snapshot['workbench_tiktok_sites']}}
    assert rounds.validate_round2_input(snapshot['offer_id'], prior_state) == snapshot
    assert path.read_bytes() == source.read_bytes() == original
