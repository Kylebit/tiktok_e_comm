"""Pure gallery completeness checks; historical image receipts remain readable.

Rules select a specific product-family pack first.  The fallback borrows only
the seven-image coverage baseline, never another family's facts or claims.
Call after canonical R2 identity/QA validation and before final-plan creation.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

DEFAULT_MINIMUM = 7
DEFAULT_ROLES = ('cover', 'product_detail', 'scene_1', 'scene_2', 'scene_3', 'size', 'instructions')
BRANDS = ('livelyhive-sea', 'homebloom-sea')
SUCCESSOR_ROLES = ('master', 'detail', 'scene_1', 'scene_2', 'scene_3', 'proportion_evidence', 'construction_evidence')
GALLERY_WO_SHA = '7c550f03b53d1f3786ce5d3bdca97f2fbaf3df270c71ff88486cf5e3770cf0f4'
REFRIGERATOR_R1 = 'sha256:1a99e2433d173548b598b16a1ab7c40fd0640eb6f9fe29ec4b90c05314f7fb67'
REFRIGERATOR_IMAGE_PLAN = 'sha256:e2670474c8020eb4fa120b07d36353b19269589b60fa55eed26b5b5b8aa9e5d1'
REFRIGERATOR_ROLES = ('refrigerator_cover_daisy', 'refrigerator_cover_feather',
    'refrigerator_cover_starry_sky', 'refrigerator_pocket_detail',
    'refrigerator_top_drape_construction', 'refrigerator_kitchen_scene',
    'refrigerator_pattern_alternatives', 'refrigerator_empty_pocket_finish')
REFRIGERATOR_POLICY = 'frozen-refrigerator-eight/2026-10-26.1'


def digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_pin(reference):
    from shared_platform.immutable_approval_files import require_local_path
    require(type(reference) is dict and set(reference) == {'path', 'sha256'}, 'GALLERY_SUCCESSOR_PIN_REQUIRED')
    path = Path(reference['path'])
    require_local_path(path, root=path.parent)
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == reference['sha256'], 'GALLERY_SUCCESSOR_PIN_CHANGED')
    return json.loads(raw)


def effective_image_plan(first, generation, *, round1=None):
    """Read only a generation-bound image-plan successor; never rewrite R1.

    Historical two-master documents have no such pointer and keep their original
    role plan. The separate final-gallery gate refuses to call them complete.
    """
    original = first.get('image_execution_plan') or first.get('image_plan') or {}
    reference = generation.get('gallery_image_plan_successor')
    if reference is None:
        return deepcopy(original)
    successor = read_pin(reference)
    if successor.get('schema_version') == 'gallery-image-plan-successor/v2':
        return _append_effective_image_plan(first, generation, successor, round1=round1)
    unsigned = dict(successor)
    supplied = unsigned.pop('successor_digest', None)
    require(supplied == digest(unsigned) and successor.get('schema_version') == 'gallery-image-plan-successor/v1'
            and successor.get('offer_id') == generation.get('offer_id') == first.get('offer_id') == '4039931504'
            and successor.get('seller_sku') == '0989', 'GALLERY_SUCCESSOR_IDENTITY_CHANGED')
    authority = read_pin(successor['authority_ref'])
    require(successor['authority_ref']['sha256'] == GALLERY_WO_SHA
            and authority.get('offer_id') == successor['offer_id']
            and authority.get('task_id') == 'TASK-20261006-001'
            and authority['gallery']['minimum_per_brand'] == 7
            and authority['gallery']['minimum_total_distinct'] == 14, 'GALLERY_SUCCESSOR_AUTHORITY_CHANGED')
    archive = read_pin(successor['predecessor_bundle_ref'])
    require(archive['WO_ref'] == successor['authority_ref'], 'GALLERY_SUCCESSOR_PREDECESSOR_CHANGED')
    refs = {Path(row['original']['path']).name: row for row in archive['files']}
    def archived(name):
        row = refs[name]
        require(row['original']['sha256'] == row['exclusive_copy']['sha256'], 'GALLERY_SUCCESSOR_PREDECESSOR_CHANGED')
        return read_pin(row['exclusive_copy'])
    old_r1 = archived('round1-approved-snapshot.json')
    old_first = archived('first-review.json')
    require(first == old_first and (round1 is None or round1 == old_r1)
            and old_r1['snapshot_digest'] == generation['round1_snapshot_digest'] == successor['round1_snapshot_digest']
            and old_r1['image_plan_digest'] == successor['predecessor_image_plan_digest']
            and digest(old_r1['fact_snapshot']) == successor['preserved_fact_snapshot_digest']
            and old_r1['canonical_targets'] == successor['target_labels'] == first['target_selection']['requested'],
            'GALLERY_SUCCESSOR_FROZEN_FACTS_OR_TARGETS_CHANGED')
    require(digest({k:v for k,v in old_r1.items() if k != 'snapshot_digest'}) == old_r1['snapshot_digest']
            and digest(first) == old_r1['first_review_digest'], 'GALLERY_SUCCESSOR_ORIGINAL_R1_CHANGED')
    plan = deepcopy(successor['image_plan'])
    require(plan.get('summary', {}).get('net_new_output_count') == 12
            and plan['summary'].get('retained_output_count') == 2
            and plan['summary'].get('total_output_count') == 14
            and plan['summary']['total_output_count'] == sum(len(brand['generated_assets']) for brand in plan['brand_plans'])
            and plan['summary']['retained_output_count'] == len(successor['retained_masters'])
            and plan['summary']['net_new_output_count'] == plan['summary']['total_output_count']-plan['summary']['retained_output_count'],
            'GALLERY_SUCCESSOR_IMAGE_COUNTS_CHANGED')
    require(plan['schema_version'] == original['schema_version'] and len(plan['brand_plans']) == 2
            and {row['id'] for row in plan['brand_plans']} == {'lh', 'hb'}, 'GALLERY_SUCCESSOR_BRANDS_CHANGED')
    old_plan = deepcopy(original); new_plan = deepcopy(plan)
    for body in (old_plan, new_plan):
        body['status'] = 'APPROVED'  # Existing canonical R1 status normalization only.
        body.pop('gallery_requirements', None)
        # These are plan output counts, not paid-call or generated-asset facts.
        body.get('summary', {}).pop('net_new_output_count', None)
        body.get('summary', {}).pop('retained_output_count', None)
        body.get('summary', {}).pop('total_output_count', None)
        for brand in body['brand_plans']:
            brand.pop('generated_assets', None)
    require(old_plan == new_plan, 'GALLERY_SUCCESSOR_NON_IMAGE_PLAN_FIELDS_CHANGED')
    previous = {row['id']: row for row in original['brand_plans']}
    for brand in plan['brand_plans']:
        require(tuple(row['role'] for row in brand['generated_assets']) == SUCCESSOR_ROLES
                and brand['generated_assets'][0] == previous[brand['id']]['generated_assets'][0],
                'GALLERY_SUCCESSOR_ROLE_OR_RETAINED_MASTER_CHANGED')
    source = successor['source_reference']
    require(source['position'] == 6 and source['sha256'] == authority['preserve']['source_sha256'],
            'GALLERY_SUCCESSOR_SOURCE_FACT_CHANGED')
    # Verify the exact source bytes without treating its file path as evidence.
    from shared_platform.immutable_approval_files import require_local_path
    source_path=Path(source['path']);require_local_path(source_path,root=source_path.parent)
    source_raw = source_path.read_bytes()
    require(hashlib.sha256(source_raw).hexdigest() == source['sha256'], 'GALLERY_SUCCESSOR_SOURCE_BYTES_CHANGED')
    substitutions = successor['role_substitutions']
    require(len(substitutions) == 2 and {(row['from'], row['to']) for row in substitutions} ==
            {('size_comparison','proportion_evidence'), ('installation','construction_evidence')}
            and all(row['evidence_ref'] == {'path': source['path'], 'sha256': source['sha256']}
                    and type(row['reason']) is str and bool(row['reason'].strip()) for row in substitutions),
            'GALLERY_SUCCESSOR_TRUTHFUL_ROLE_SUBSTITUTIONS_REQUIRED')
    old_generation = archived('brand-image-generation.json')
    old_qa = archived('automated-image-qa.json')
    require(read_pin(successor['retained_master_QA_ref']) == old_qa and old_qa['status'] == 'PASSED'
            and read_pin(successor['retained_generation_ref']) == old_generation,
            'GALLERY_SUCCESSOR_RETAINED_MASTER_QA_CHANGED')
    old_masters = {(row['brand_id'],row['role']): row for row in old_generation['assets']}
    require(len(successor['retained_masters']) == 2, 'GALLERY_SUCCESSOR_MASTER_PRESERVATION_REQUIRED')
    for row in successor['retained_masters']:
        prior = old_masters[(row['brand_id'], row['role'])]
        require(row['role'] == 'master' and all(row[key] == prior[key] for key in
            ('artifact_digest','public_url','task_id')) and row['artifact_digest'] in old_qa['artifact_digests'],
            'GALLERY_SUCCESSOR_RETAINED_MASTER_CHANGED')
    # Return an effective plan only. Do not mutate the canonical original R1 or
    # pretend its original digest covered fourteen roles.
    for brand in plan['brand_plans']:
        for row in brand['generated_assets']:
            replacement = next((item for item in substitutions if item['to'] == row['role']), None)
            if replacement is not None:
                row['role_substitution'] = {'replaces': replacement['from'], 'reason': 'SOURCE_FACT_UNKNOWN',
                    'evidence_basis': 'sha256:' + source['sha256'], 'unknown_facts_not_asserted': True}
    return plan



def _append_effective_image_plan(first, generation, successor, *, round1=None):
    """Exact authorized two-scene append; retain the complete qualified14."""
    require(successor.get('status') == 'AUTHORIZED_TECHNICAL_IMAGE_PLAN', 'GALLERY_APPEND_STATUS_REQUIRED')
    unsigned = dict(successor); supplied = unsigned.pop('successor_digest', None)
    require(supplied == digest(unsigned), 'GALLERY_APPEND_DIGEST_CHANGED')
    authority = read_pin(successor['append_authority_ref'])
    require(successor['append_authority_ref']['sha256'] == 'f07487b59ee10c98c0ba5b4e9718d248f893b5ada872e9a6b2034b4766a77cda'
            and authority['offer_id'] == '4039931504' and authority['task_id'] == 'TASK-20261006-001'
            and authority['interpreted_scope']['resulting_distinct_gallery'] == 16
            and authority['interpreted_scope']['role'] == 'scene_in_use_toilet_roll', 'GALLERY_APPEND_AUTHORITY_CHANGED')
    bundle = read_pin(successor['retained_gallery_bundle_ref'])
    require(successor['retained_gallery_bundle_ref']['sha256'] == 'a0c96cea9430af3bfe0878850974f239f6d51fd8b03e174b4adc9e080c895b13',
            'GALLERY_APPEND_RETAINED_BUNDLE_CHANGED')
    names = {'round1_snapshot','first_review','generation_result','translation_plan','translation_result','image_qa'}
    documents = {}
    for row in bundle['documents']:
        if row['role'] in names:
            require(row['original']['sha256'] == row['exclusive_copy']['sha256'], 'GALLERY_APPEND_RETAINED_DOCUMENT_CHANGED')
            documents[row['role']] = read_pin(row['exclusive_copy'])
    require(set(documents) == names and documents['first_review'] == first
            and (round1 is None or documents['round1_snapshot'] == round1), 'GALLERY_APPEND_R1_OR_FIRST_CHANGED')
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    require(validate_r2_identity(documents) == bundle['current_typed_R2_identity']
            and bundle['current_typed_R2_identity']['identity_digest'] ==
            'sha256:2f787564e9696aa34ff681694624f1a1c7a3b8fc92b1d6a91831eb7b85bebb15', 'GALLERY_APPEND_PREDECESSOR_IDENTITY_CHANGED')
    old_generation = documents['generation_result']
    require(successor['predecessor_successor_ref'] == old_generation['gallery_image_plan_successor']
            and successor['predecessor_successor_ref']['sha256'] == 'f81915cd17ee4f46b495b4cb5a3b472d62a550d4bea3d669a60afe0bd2316abe',
            'GALLERY_APPEND_ORIGINAL_SUCCESSOR_CHANGED')
    previous = read_pin(successor['predecessor_successor_ref'])
    for key in ('offer_id','seller_sku','round1_snapshot_digest','predecessor_image_plan_digest',
                'preserved_fact_snapshot_digest','target_labels','authority_ref','predecessor_bundle_ref',
                'retained_master_QA_ref','source_reference','retained_masters','role_substitutions','retained_generation_ref','budget_authority_ref'):
        require(successor.get(key) == previous.get(key), 'GALLERY_APPEND_FROZEN_FIELDS_CHANGED')
    require(generation['offer_id'] == successor['offer_id'] == first['offer_id'] == '4039931504'
            and generation['round1_snapshot_digest'] == old_generation['round1_snapshot_digest'], 'GALLERY_APPEND_GENERATION_IDENTITY_CHANGED')
    old_plan = previous['image_plan']; plan = deepcopy(successor['image_plan'])
    require(plan['summary'].get('net_new_output_count') == 2 and plan['summary'].get('retained_output_count') == 14
            and plan['summary'].get('total_output_count') == 16, 'GALLERY_APPEND_IMAGE_COUNTS_CHANGED')
    normalized = deepcopy(plan); original = deepcopy(old_plan)
    for body in (normalized, original):
        for key in ('net_new_output_count','retained_output_count','total_output_count'):body['summary'].pop(key,None)
        for brand in body['brand_plans']:brand.pop('generated_assets',None)
    require(normalized == original and len(plan['brand_plans']) == 2, 'GALLERY_APPEND_NON_IMAGE_PLAN_CHANGED')
    effective = effective_image_plan(first, old_generation, round1=documents['round1_snapshot'])
    for new_brand, old_brand, effective_brand in zip(plan['brand_plans'], old_plan['brand_plans'], effective['brand_plans']):
        rows = new_brand['generated_assets']; prior = old_brand['generated_assets']
        require(new_brand['id'] == old_brand['id'] and len(rows) == 8 and rows[:7] == prior
                and rows[-1].get('role') == 'scene_in_use_toilet_roll' and type(rows[-1].get('quantity')) is int
                and rows[-1]['quantity'] == 1 and type(rows[-1].get('brief')) is str and bool(rows[-1]['brief'].strip()),
                'GALLERY_APPEND_ORIGINAL_ROLES_OR_BRIEFS_CHANGED')
        effective_brand['generated_assets'].append(deepcopy(rows[-1]))
    keys = ('artifact_digest','public_url','task_id')
    def identities(rows):
        result = {(r['brand_id'],r['role']): tuple(r[k] for k in keys) for r in rows}
        require(len(result) == len(rows), 'GALLERY_APPEND_DUPLICATE_ROLE')
        return result
    retained = identities(old_generation['assets'])
    require(len(retained) == 14 and identities(successor['retained_assets']) == retained, 'GALLERY_APPEND_RETAINED_ASSET_CHANGED')
    current = identities(generation['assets'])
    require(len(current) == 16 and all(current.get(k) == v for k,v in retained.items())
            and set(current)-set(retained) == {('lh','scene_in_use_toilet_roll'),('hb','scene_in_use_toilet_roll')},
            'GALLERY_APPEND_CURRENT_ASSETS_CHANGED')
    effective['summary'].update(net_new_output_count=2, retained_output_count=14, total_output_count=16)
    return effective

def require(value, code):
    if not value:
        raise ValueError(code)


def family(brand):
    return {'lh': BRANDS[0], 'hb': BRANDS[1]}.get(brand, brand)


def gallery_policy(round1):
    if round1.get('snapshot_digest') == REFRIGERATOR_R1:
        require(digest({k:v for k,v in round1.items() if k != 'snapshot_digest'}) == REFRIGERATOR_R1
                and round1.get('image_plan_digest') == REFRIGERATOR_IMAGE_PLAN
                and digest(round1.get('image_plan')) == REFRIGERATOR_IMAGE_PLAN,
                'GALLERY_FROZEN_REFRIGERATOR_IDENTITY_CHANGED')
        facts=round1.get('fact_snapshot', {}).get('product_facts', {})
        require(facts.get('category_semantic') == '冰箱防尘盖布带侧袋'
                and [(r.get('source_key'),r.get('seller_sku'),r.get('approved_display_name')) for r in facts.get('skus', [])]
                == [('manual-01','2000431','灰雏菊 Daisy'),('manual-02','2000432','羽毛 Feather'),('manual-03','2000433','星空 Starry Sky')],
                'GALLERY_FROZEN_REFRIGERATOR_FACTS_CHANGED')
        return {'source': REFRIGERATOR_POLICY, 'minimum_per_brand': 8,
                'roles_by_brand': {brand:list(REFRIGERATOR_ROLES) for brand in BRANDS}}
    from shared_platform.publication_quality import select_product_family_pack
    fact_snapshot = round1.get('fact_snapshot', {})
    facts = fact_snapshot.get('product_facts', {})
    shared = fact_snapshot.get('shared_review_facts', {})
    pack = select_product_family_pack({'product': {
        'title': facts.get('title') or shared.get('title') or shared.get('product_title') or shared.get('source_title'),
        'description': facts.get('description') or shared.get('description'),
        'main_category': facts.get('main_category') or facts.get('category') or shared.get('main_category') or shared.get('category')
            or {'id': str(facts.get('category_semantic')).replace('_','-') if facts.get('category_semantic') in {'wallpaper','wall_sticker'} else '',
                'name': facts.get('category_semantic') or shared.get('category_semantic')},
    }})
    if pack is not None:
        return {'source': pack['rule_pack_version'],
                'minimum_per_brand': pack['image_policy']['minimum_images_per_brand'],
                'roles_by_brand': deepcopy(pack['brand_role_sets'])}
    return {'source': 'generic-seven/2026-10-15.1', 'minimum_per_brand': DEFAULT_MINIMUM,
            'roles_by_brand': {brand: list(DEFAULT_ROLES) for brand in BRANDS}}


def validate_gallery_plan(round1, *, effective_plan=None):
    """Check semantic coverage without inventing dimensions or installation."""
    policy = gallery_policy(round1)
    if policy['source'] == REFRIGERATOR_POLICY:
        frozen=deepcopy(effective_plan if effective_plan is not None else round1.get('image_plan'))
        frozen['status']='APPROVED'  # Existing first-review -> R1 status normalization.
        require(digest(frozen) == REFRIGERATOR_IMAGE_PLAN,
                'GALLERY_FROZEN_REFRIGERATOR_PLAN_CHANGED')
    plans = (effective_plan if effective_plan is not None else round1.get('image_plan', {})).get('brand_plans')
    require(isinstance(plans, list) and plans, 'GALLERY_FROZEN_PLAN_REQUIRED')
    result = {}
    for plan in plans:
        brand = family(plan.get('id'))
        require(brand in BRANDS and brand not in result, 'GALLERY_BRAND_IDENTITY_REQUIRED')
        assets = plan.get('generated_assets')
        require(isinstance(assets, list), 'GALLERY_ROLE_COVERAGE_REQUIRED')
        roles = [row.get('role') for row in assets]
        require(len(roles) >= policy['minimum_per_brand'] and len(roles) == len(set(roles))
                and all(type(role) is str and role for role in roles), 'GALLERY_MINIMUM_OR_ROLES_INCOMPLETE')
        require(all(type(row.get('quantity')) is int and row['quantity'] == 1 for row in assets),
                'GALLERY_ONE_DISTINCT_ARTIFACT_PER_ROLE_REQUIRED')
        expected = policy['roles_by_brand'][brand]
        # A truthful substitution belongs to the frozen image plan. It must not
        # claim that an unknown size or installation method has been verified.
        covered = []
        for row in assets:
            role = row['role']
            substitution = row.get('role_substitution')
            if substitution is not None:
                require(type(substitution) is dict and set(substitution) ==
                        {'replaces', 'reason', 'evidence_basis', 'unknown_facts_not_asserted'},
                        'GALLERY_ROLE_SUBSTITUTION_INVALID')
                require(substitution['replaces'] in {'size', 'instructions', 'size_comparison', 'installation', 'assembled_size', 'piece_layout_or_instructions'}
                        and role in {'construction_proportion', 'parts_detail', 'proportion_evidence', 'construction_evidence'}
                        and substitution['reason'] == 'SOURCE_FACT_UNKNOWN'
                        and type(substitution['evidence_basis']) is str and bool(substitution['evidence_basis'].strip())
                        and substitution['unknown_facts_not_asserted'] is True,
                        'GALLERY_UNVERIFIED_SIZE_OR_INSTALLATION')
                covered.append({'size_comparison': 'size', 'installation': 'instructions'}.get(substitution['replaces'], substitution['replaces'])
                               if policy['source'].startswith('generic-seven/') else substitution['replaces'])
            else:
                covered.append({'master': 'cover', 'detail': 'product_detail'}.get(role, role)
                               if policy['source'].startswith('generic-seven/') else role)
                if role in {'size', 'size_comparison', 'instructions', 'installation'}:
                    evidence = row.get('fact_evidence')
                    require(type(evidence) is dict and set(evidence) == {'fact_path', 'value'}
                            and type(evidence['fact_path']) is list and bool(evidence['fact_path']),
                            'GALLERY_UNVERIFIED_SIZE_OR_INSTALLATION')
                    observed = round1.get('fact_snapshot')
                    for key in evidence['fact_path']:
                        require(isinstance(observed, dict) and key in observed,
                                'GALLERY_UNVERIFIED_SIZE_OR_INSTALLATION')
                        observed = observed[key]
                    require(observed == evidence['value'] and isinstance(observed, dict)
                            and observed.get('fact_verified') is True and observed.get('status') == 'VERIFIED',
                            'GALLERY_UNVERIFIED_SIZE_OR_INSTALLATION')
        require(len(covered) == len(set(covered)) and set(expected).issubset(covered),
                'GALLERY_MEANINGFUL_ROLE_COVERAGE_REQUIRED')
        result[brand] = roles
    labels = round1.get('canonical_targets') or []
    required = {BRANDS[1] if label.startswith('tiktok:HB_') else BRANDS[0] for label in labels if label != 'miaoshou:COMMON'}
    require(required == set(result), 'GALLERY_FROZEN_BRAND_SCOPE_CHANGED')
    return {**policy, 'roles_by_brand': result}


def validate_gallery_documents(documents, route):
    """Count distinct qualified master artifacts per brand, not shop routes."""
    policy = validate_gallery_plan(documents['round1_snapshot'],
                                   effective_plan=effective_image_plan(documents['first_review'], documents['generation_result'],
                                                                     round1=documents['round1_snapshot']))
    rows = documents['generation_result'].get('assets') or []
    qa = documents['image_qa']
    require(qa.get('status') == 'PASSED', 'GALLERY_QA_REQUIRED')
    approved = set(qa.get('artifact_digests') or [])
    seen = set()
    counts = {}
    for brand, roles in policy['roles_by_brand'].items():
        assets = [row for row in rows if family(row.get('brand_id')) == brand]
        require([row.get('role') for row in assets] == roles, 'GALLERY_FROZEN_ROLE_ORDER_CHANGED')
        digests = [row.get('artifact_digest') for row in assets]
        urls = [row.get('public_url') for row in assets]
        require(len(assets) >= policy['minimum_per_brand'] and len(set(digests)) == len(assets)
                and len(set(urls)) == len(assets), 'GALLERY_DISTINCT_ARTIFACT_MINIMUM_REQUIRED')
        from shared_platform.publication_image_qa import is_completed_image_asset
        require(all(is_completed_image_asset(row) for row in assets), 'GALLERY_QUALIFIED_ARTIFACT_REQUIRED')
        require(all(type(value) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', value)
                    and value in approved for value in digests), 'GALLERY_QUALIFIED_ARTIFACT_REQUIRED')
        require(not seen.intersection(digests), 'GALLERY_CROSS_BRAND_DUPLICATE_ARTIFACT')
        seen.update(digests)
        counts[brand] = len(assets)
    for label, images in route['image_routes'].items():
        brand = BRANDS[1] if label.startswith('tiktok:HB_') else BRANDS[0]
        roles = policy['roles_by_brand'][brand]
        assets = [row for row in rows if family(row.get('brand_id')) == brand]
        locale=route.get('route_locales',{}).get(label)
        expected=[]
        for master in assets:
            localized=[row for row in documents.get('translation_result',{}).get('assets',[])
                if family(row.get('brand_id'))==brand and row.get('role')==master['role'] and row.get('locale')==locale]
            require(len(localized)<=1,'GALLERY_LOCALIZED_ROLE_AMBIGUOUS')
            selected=master
            if localized:
                selected=localized[0]
                tasks=[t for t in documents.get('translation_plan',{}).get('tasks',[])
                    if family(t.get('brand_id'))==brand and t.get('role')==master['role'] and t.get('locale')==locale]
                require(len(tasks)==1 and tasks[0].get('review_number')==master.get('review_number')
                    and tasks[0].get('source_artifact_digest')==master['artifact_digest']
                    and tasks[0].get('source_url')==master['public_url']
                    and selected.get('source_review_number')==master.get('review_number')
                    and selected.get('status')=='COMPLETED' and selected.get('artifact_digest') in approved,
                    'GALLERY_LOCALIZED_SOURCE_OR_QA_CHANGED')
            expected.append((selected.get('artifact_digest'),selected.get('public_url')))
        require([row.get('role') for row in images] == roles
                and [row.get('position') for row in images] == list(range(1, len(roles) + 1))
                and all(row.get('brand_id') == brand for row in images)
                and [(row.get('artifact_digest'), row.get('url')) for row in images] ==
                    expected,
                'GALLERY_TARGET_ROUTE_INCOMPLETE')
    require(set(route['image_routes']) == set(documents['round1_snapshot']['canonical_targets'])-{'miaoshou:COMMON'},
            'GALLERY_EXACT_TARGET_ROUTES_REQUIRED')
    retained = set()
    reference = documents['generation_result'].get('gallery_image_plan_successor')
    if reference is not None:
        sidecar = read_pin(reference)
        append = sidecar.get('schema_version') == 'gallery-image-plan-successor/v2'
        retained_rows = sidecar['retained_assets'] if append else sidecar['retained_masters']
        for master in retained_rows:
            actual = [row for row in rows if row.get('brand_id')==master['brand_id']
                      and row.get('role')==(master['role'] if append else 'master')]
            require(len(actual)==1 and all(actual[0].get(key)==master[key] for key in
                ('artifact_digest','public_url','task_id')), 'GALLERY_PASSED_MASTER_MUST_BE_RETAINED')
            retained.add(master['artifact_digest'])
    return {'status': 'PASSED', 'policy': policy, 'distinct_qualified_per_brand': counts,
            'total_distinct_qualified': len(seen), 'retained_qualified_count':len(retained),
            'new_qualified_count':len(seen-retained), 'route_count_is_not_image_count': True}
