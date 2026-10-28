import pytest

from modules.sourcing.brand_image_prompt import build_brand_image_prompt


@pytest.mark.parametrize(
    "role, distinctive_subject",
    [
        ("refrigerator_cover_daisy", "Daisy"),
        ("refrigerator_cover_feather", "Feather"),
        ("refrigerator_cover_starry_sky", "Starry Sky"),
        ("refrigerator_pocket_detail", "pocket"),
        ("refrigerator_top_drape_construction", "top-to-side"),
        ("refrigerator_kitchen_scene", "kitchen"),
        ("refrigerator_pattern_alternatives", "not a three-piece set"),
        ("refrigerator_empty_pocket_finish", "empty pocket"),
    ],
)
def test_refrigerator_cover_gallery_roles_accept_factual_pattern_references(
    role, distinctive_subject
):
    prompt = build_brand_image_prompt(
        brand_label="Example Brand",
        positioning="A clear kitchen composition",
        role=role,
        brief="Use the selected reference pattern and its visible pocket construction.",
        product_identity="Fabric refrigerator-top cover with hanging side pockets",
        product_reference_count=3,
    )

    assert distinctive_subject in prompt
    assert "intrinsic printed lettering" in prompt
    assert "No added text" in prompt
    assert "first 3 ordered reference image" in prompt
    assert "30 cm" not in prompt
    assert "125 cm" not in prompt
    assert "43 cm" not in prompt
    assert "Measure, Plan, Mark, Cut, Align" not in prompt
