"""Knowledge-backed default image plan for product families without a pack."""

from __future__ import annotations

from typing import Any, Sequence


GENERIC_REQUIRED_ROLES = (
    ("cover", "生成一张主体清晰、事实准确、无无依据文字的主图。"),
    ("product_detail", "生成一张材质、纹理或结构细节图，不推断未知卖点。"),
    ("scene_1", "生成第一张真实使用场景图，商品必须是画面主体。"),
    ("scene_2", "生成第二张明显不同的真实使用场景图。"),
    ("scene_3", "生成第三张明显不同的真实使用场景图。"),
    ("size", "生成一张仅使用已核实尺寸和数量事实的尺寸图。"),
)
GENERIC_OPTIONAL_ROLE = (
    "instructions",
    "仅在使用方法已被事实证据确认时生成使用说明图。",
)


def build_generic_image_plan(
    target_labels: Sequence[str], *, include_instructions: bool = False
) -> dict[str, Any]:
    """Create one or two brand packs while preserving follow-brand routing."""

    labels = [str(value).strip() for value in target_labels if str(value).strip()]
    has_homebloom = any("HB_" in value or "HomeBloom" in value for value in labels)
    has_lively = any("LH_" in value or "LivelyHive" in value for value in labels)
    brand_ids = []
    if has_lively or not has_homebloom:
        brand_ids.append("livelyhive-sea")
    if has_homebloom:
        brand_ids.append("homebloom-sea")
    roles = list(GENERIC_REQUIRED_ROLES)
    if include_instructions:
        roles.append(GENERIC_OPTIONAL_ROLE)
    guidance = [
        {
            "platform": platform,
            "category_id": "UNRESOLVED_AUTOMATIC_MATCH",
            "category_zh": "待自动类目匹配",
            "recommendation": "先使用通用品类图片包；类目规则确认后由专用品类包覆盖。",
        }
        for platform in ("tiktok", "shopee", "ozon")
        if any(value.lower().startswith(f"{platform}:") for value in labels)
    ] or [{
        "platform": "tiktok",
        "category_id": "UNRESOLVED_AUTOMATIC_MATCH",
        "category_zh": "待自动类目匹配",
        "recommendation": "先使用通用品类图片包；类目规则确认后由专用品类包覆盖。",
    }]
    brand_plans = []
    for brand_id in brand_ids:
        home = brand_id == "homebloom-sea"
        brand_plans.append({
            "id": brand_id,
            "label": "HomeBloom 通用图片方案" if home else "LivelyHive 通用图片方案",
            "target_group": "HOMEBLOOM" if home else "LIVELYHIVE_FOLLOW_GROUP",
            "generation_mode": "NEW_SET_VIA_IMAGE_API",
            "positioning": (
                "温暖生活化家居构图，与 LivelyHive 保持明显视觉区分。"
                if home else
                "现代清晰实用构图；Shopee、Ozon、MX、GB 默认跟随本方案。"
            ),
            "reference_positions": [1],
            "generated_assets": [
                {"role": role, "quantity": 1, "brief": brief}
                for role, brief in roles
            ],
            "category_guidance": [dict(row) for row in guidance],
        })
    planned = len(roles) * len(brand_plans)
    return {
        "schema_version": "first-review-image-plan/v1",
        "status": "PROPOSED",
        "translation_plan": {
            "status": "DEFERRED_UNTIL_ALL_IMAGES_GENERATED",
            "decision_basis": "REVIEW_ALL_GENERATED_IMAGES_FIRST",
            "note": "先完成母版生成和自动 QA，再按含文字图片决定本地化范围。",
        },
        "source_actions": [],
        "generated_assets": [],
        "brand_plans": brand_plans,
        "summary": {
            "translation_positions": [],
            "localized_output_count": 0,
            "net_new_output_count": planned,
            "paid_generation_required": planned > 0,
        },
    }


__all__ = [
    "GENERIC_OPTIONAL_ROLE",
    "GENERIC_REQUIRED_ROLES",
    "build_generic_image_plan",
]
