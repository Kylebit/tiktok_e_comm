from modules.sourcing.brand_image_lingshi_generation import resolve_brand_image_model
from modules.sourcing.brand_image_prompt import build_brand_image_prompt


class FakeCatalogClient:
    def list_models(self, model_type):
        assert model_type == "image"
        return {
            "models": [
                {"name": "gpt-image-2", "available_for_this_key": False},
                {"name": "tt-image-2", "available_for_this_key": True},
            ]
        }

    def model_detail(self, model):
        assert model == "tt-image-2"
        return {
            "display_name": "GPT Image 2",
            "params": [
                {"name": "images"},
                {"name": "size", "options": [{"value": "2048x2048"}]},
                {"name": "quality", "options": [{"value": "medium"}]},
            ],
        }


def test_brand_model_resolver_uses_available_contract_compatible_alias():
    model, detail = resolve_brand_image_model(FakeCatalogClient())

    assert model == "tt-image-2"
    assert detail["display_name"] == "GPT Image 2"


def test_generic_decor_roles_compile_without_product_specific_exceptions():
    roles = {
        "botanical_detail": "verified flower, grass and foliage types",
        "entryway_scene": "entryway or console-table",
        "dining_table_scene": "dining-table or sideboard",
        "photography_prop_scene": "photography styling scene",
        "quantity_composition": "verified bundle quantity",
    }

    for role, expected in roles.items():
        prompt = build_brand_image_prompt(
            brand_label="Example Brand",
            positioning="Verified decor positioning",
            role=role,
            brief="Use only verified facts.",
            product_identity="Dried flower bouquet",
            product_reference_count=1,
        )

        assert expected in prompt
        assert "first 1 ordered reference image" in prompt
