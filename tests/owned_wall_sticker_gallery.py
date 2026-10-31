"""Proposed inputs for the owned wall-sticker service-consumer fixtures only.

No immutable packet, role-coverage guard, QA result or provider fact is changed.
"""
from copy import deepcopy
import json


def include_homebloom_target(live):
    path = live['root'] / 'data/new_product_workbench' / (live['offer'] + '.json')
    state = json.loads(path.read_bytes())
    if 'hb_ph' not in state['review']['selected_sites']:
        state['review']['selected_sites'].append('hb_ph')
    path.write_text(json.dumps(state), encoding='utf-8')


def wall_sticker_gallery(proposal):
    value = deepcopy(proposal)
    roles = ('cover_scene', 'installed_detail', 'scene_1', 'scene_2', 'scene_3',
             'assembled_size', 'piece_layout_or_instructions')
    for brand in value['brand_plans']:
        assert len(brand['generated_assets']) == len(roles)
        for asset, role in zip(brand['generated_assets'], roles):
            asset['role'] = role
            asset['brief'] = 'Faithful owned wall decal; ' + role
            if role in {'assembled_size', 'piece_layout_or_instructions'}:
                asset['role'] = {'assembled_size': 'proportion_evidence',
                                 'piece_layout_or_instructions': 'construction_evidence'}[role]
                asset['brief'] = 'Show only source-visible construction; do not assert size or installation.'
                asset['role_substitution'] = {
                    'replaces': role, 'reason': 'SOURCE_FACT_UNKNOWN',
                    'evidence_basis': 'Owned source contains parcel logistics, not verified product dimensions or installation facts.',
                    'unknown_facts_not_asserted': True,
                }
    return value
