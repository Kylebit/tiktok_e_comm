"""R3-supported truthful substitutions reach the actual R2 prompt producer."""
import pytest
from modules.sourcing.brand_image_prompt import build_brand_image_prompt

@pytest.mark.parametrize('role',['proportion_evidence','construction_proportion','construction_evidence','parts_detail'])
def test_truthful_role_has_source_grounded_prompt_without_historical_sizes(role):
    prompt=build_brand_image_prompt(brand_label='LivelyHive',positioning='Faithful product',role=role,
        brief='Only visible construction; measurements and mounting remain unknown.',
        product_identity='New arbitrary household organiser',product_reference_count=3)
    assert 'only product-identity truth' in prompt
    assert 'No text' in prompt
    assert all(value not in prompt for value in ('44 cm','125 cm','50 x 80','Measure, Plan'))
    assert any(value in prompt for value in ('unknown','Do not invent'))

def test_unknown_role_still_rejected():
    with pytest.raises(ValueError,match='unsupported brand image role'):
        build_brand_image_prompt(brand_label='x',positioning='x',role='invent_new_claim',brief='x',product_identity='x')
