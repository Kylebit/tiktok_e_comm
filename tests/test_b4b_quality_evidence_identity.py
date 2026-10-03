"""Real compiler regressions for family selection and approved fact provenance."""
from copy import deepcopy
import json
import pytest
from domains.product_operations import build_approved_publication_snapshot
from shared_platform.publication_autopilot import compile_release_candidate
from shared_platform.publication_quality import evaluate_publication_quality,select_product_family_pack
from shared_platform.publication_rounds import canonical_digest
from shared_platform.publication_quality_evidence import load_publication_quality_evidence, evaluate_publication_quality_evidence
from test_approved_publication_snapshot import _approved_plan,_rebind
from test_b4b_release_compiler import policy,INCIDENTS


def claim_snapshot(*,category_id='wall-sticker',title='Waterproof removable wall sticker'):
    plan=_approved_plan()
    facts=plan['payload']['product_facts']
    facts['category']={'id':category_id,'name':'Unclassified household item'}
    facts['title']=title
    facts['description']='Waterproof and removable decoration.'
    return build_approved_publication_snapshot(_rebind(plan)).payload()


def bound_round1(value,title):
    round1={'schema_version':'round1-approved-snapshot/v1','status':'APPROVED',
        'approved_by':'Kyle','offer_id':value['offer_id'],'canonical_targets':[row['target_label'] for row in value['publication_targets']],
        'fact_snapshot':{'product_facts':{'title':title}}}
    round1['snapshot_digest']=canonical_digest(round1)
    return {'round1_snapshot':round1,'publication_bridge':{'offer_id':value['offer_id'],
        'release_handoff':{'plan_id':value['plan_id'],'snapshot_digest':value['snapshot_digest'],
            'round1_snapshot_digest':round1['snapshot_digest']}}}


def compile_quality(value,evidence=None):
    return compile_release_candidate(value,policy=policy(),incident_registry=INCIDENTS,durable_evidence=evidence)


@pytest.mark.parametrize('title',['Waterproof wall sticker','防水墙贴'])
def test_unknown_category_uses_wall_sticker_semantics_and_cannot_skip_its_rules(title):
    candidate=compile_quality(claim_snapshot(category_id='unknown-household',title=title))
    assert candidate['automated_quality_gate']['product_family']=='wall_sticker'
    assert candidate['status']=='BLOCKED'
    assert any(row['code']=='UNVERIFIED_MARKETING_CLAIM' for row in candidate['blockers'])


def test_explicit_known_family_has_priority_over_conflicting_title_terms():
    value=claim_snapshot(category_id='wall-sticker',title='Wallpaper roll comparison')
    assert evaluate_publication_quality(value)['product_family']=='wall_sticker'


def test_roll_exclusion_resolves_mixed_semantics_but_explicit_family_wins():
    value=claim_snapshot(category_id='unknown-household',title='Wallpaper roll and wall sticker')
    assert evaluate_publication_quality(value)['product_family']=='wallpaper'
    value=claim_snapshot(category_id='wallpaper',title='Wall sticker comparison')
    assert evaluate_publication_quality(value)['product_family']=='wallpaper'


def test_unknown_nonmatching_product_is_not_guessed_to_be_wallpaper():
    value=claim_snapshot(category_id='unknown-household',title='Kitchen utensil')
    value['product']['description']='Plain kitchen utensil.'
    assert select_product_family_pack(value) is None
    assert evaluate_publication_quality(value)['status']=='NOT_APPLICABLE'


@pytest.mark.parametrize('drift',['foreign_offer','bad_digest','negative_claim','wrong_release'])
def test_round1_claims_cannot_be_lent_from_foreign_stale_or_negative_evidence(drift):
    value=claim_snapshot()
    evidence=bound_round1(value,'防水 可移除 墙贴')
    row=evidence['round1_snapshot']
    if drift=='foreign_offer':
        row['offer_id']='9999999';row.pop('snapshot_digest');row['snapshot_digest']=canonical_digest(row)
        evidence['publication_bridge']['release_handoff']['round1_snapshot_digest']=row['snapshot_digest']
    elif drift=='bad_digest':
        row['fact_snapshot']['product_facts']['title']='防水 可移除 改动过的墙贴'
    elif drift=='wrong_release':
        evidence['publication_bridge']['release_handoff']['snapshot_digest']='sha256:'+'f'*64
    else:
        evidence=bound_round1(value,'不防水，不可移除，非自粘墙贴')
    candidate=compile_quality(value,evidence)
    assert any(error['code']=='UNVERIFIED_MARKETING_CLAIM' for error in candidate['automated_quality_gate']['errors'])
    assert not any(check['code']=='MARKETING_CLAIM_VERIFIED' and check['status']=='PASSED'
        for check in candidate['automated_quality_gate']['checks'])


def test_exact_positive_round1_fact_remains_usable_without_new_approval():
    value=claim_snapshot();before=deepcopy(value)
    candidate=compile_quality(value,bound_round1(value,'防水 可移除 墙贴'))
    claims=[check for check in candidate['automated_quality_gate']['checks'] if check['code']=='MARKETING_CLAIM_VERIFIED']
    assert len(claims)==2 and all(check['status']=='PASSED' for check in claims)
    assert value==before


@pytest.mark.parametrize('drift', ['foreign_offer', 'bad_digest', 'malformed'])
def test_real_loader_rejects_invalid_round1_sidecar(tmp_path, drift):
    value=claim_snapshot(); root=tmp_path/value['offer_id']; root.mkdir()
    for name in ('workflow-handoff','dual-brand-publication-handoff','brand-image-translation-plan',
                 'brand-image-translation','automated-image-qa','platform-preflight'):
        (root/f'{name}.json').write_text('{"fixture":true}',encoding='utf-8')
    row=bound_round1(value,'防水 墙贴')['round1_snapshot']
    if drift=='foreign_offer':
        row['offer_id']='9999999';row.pop('snapshot_digest');row['snapshot_digest']=canonical_digest(row)
    elif drift=='bad_digest':
        row['fact_snapshot']['product_facts']['title']='改动'
    encoded='{' if drift=='malformed' else json.dumps(row)
    (root/'round1-approved-snapshot.json').write_text(encoded,encoding='utf-8')
    with pytest.raises(ValueError, match='round1'):
        load_publication_quality_evidence(value['offer_id'],reports_root=tmp_path)


def test_direct_evaluator_cannot_use_foreign_round1_roles():
    value=claim_snapshot();evidence=bound_round1(value,'防水 墙贴')
    evidence.update({key:{} for key in ('workflow_handoff','translation_plan','translation_result','image_qa','platform_preflight')})
    row=evidence['round1_snapshot'];row['offer_id']='9999999'
    row.pop('snapshot_digest');row['snapshot_digest']=canonical_digest(row)
    evidence['publication_bridge']['release_handoff']['round1_snapshot_digest']=row['snapshot_digest']
    result=evaluate_publication_quality_evidence(value,evidence,pack={})
    assert any(item['code']=='ROUND1_EVIDENCE_IDENTITY_CONFLICT' for item in result['errors'])


@pytest.mark.parametrize('title,claim', [('防水性待核实','waterproof'),('防水性能未验证','waterproof'),('是否可移除待确认','removable')])
def test_unverified_or_questioned_title_is_not_a_positive_fact(title,claim):
    value=claim_snapshot()
    candidate=compile_quality(value,bound_round1(value,title))
    check=next(row for row in candidate['automated_quality_gate']['checks']
               if row['code']=='MARKETING_CLAIM_VERIFIED' and row['claim']==claim)
    assert check['status']=='FAILED'


def test_conflicting_family_rules_return_reviewable_blocker(monkeypatch):
    import shared_platform.publication_quality as quality
    original=quality.load_product_family_pack
    def overlapping(name):
        pack=original(name)
        pack['match']['semantic_terms']=['ambiguous fixture']
        pack['match']['exclude_terms']=[]
        return pack
    monkeypatch.setattr(quality,'load_product_family_pack',overlapping)
    value=claim_snapshot(category_id='unknown-household',title='Ambiguous fixture')
    assert quality.evaluate_publication_quality(value)['status']=='FAILED'
    candidate=compile_quality(value)
    assert candidate['status']=='BLOCKED'
    assert any(row['code']=='QUALITY_RULE_SELECTION_REQUIRED' for row in candidate['blockers'])


def test_existing_structured_approved_fact_has_priority_over_title_projection():
    plan=_approved_plan();facts=plan['payload']['product_facts']
    facts['title']='Self-adhesive wall sticker';facts['description']='Decorative wall sticker.'
    approved={'value':True,'fact_verified':True,'evidence_level':'user_decision','approved_by':'Kyle',
              'approved_at':'2026-09-05T10:00:00+08:00','source':'conversation_approval'}
    facts['verified_product_claims']={'self_adhesive':approved}
    value=build_approved_publication_snapshot(_rebind(plan)).payload()
    candidate=compile_quality(value,bound_round1(value,'是否自粘待确认'))
    assert next(row for row in candidate['automated_quality_gate']['checks']
                if row['code']=='MARKETING_CLAIM_VERIFIED' and row['claim']=='self_adhesive')['status']=='PASSED'
    assert value['product']['verified_claims']['self_adhesive']==approved
