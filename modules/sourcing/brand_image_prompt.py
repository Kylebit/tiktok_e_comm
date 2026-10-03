from __future__ import annotations


def build_brand_image_prompt(
    *,
    brand_label: str,
    positioning: str,
    role: str,
    brief: str,
    product_identity: str,
    product_reference_count: int | None = None,
    composition_reference_count: int = 0,
) -> str:
    normalized_identity = str(product_identity or "").casefold()
    is_wall_sticker = any(
        term in normalized_identity
        for term in ("wall sticker", "wall decal", "decorative sticker", "墙贴", "装饰贴纸")
    )
    role_aliases = {
        "cover_scene": "cover",
        "installed_detail": "floral_detail",
        "product_detail": "material_detail",
        "scene_1": "living_room_scene",
        "scene_2": "bedroom_scene",
        "scene_3": "reading_corner_scene",
        "piece_layout_or_instructions": "piece_layout",
    }
    resolved_role = role_aliases.get(role, role)
    role_rules = {
        "cover": "Create a square ecommerce cover with the exact verified product as the clear hero. Preserve its supplied construction and design. No text.",
        "pattern_detail": "Create a square close-up detail of the exact supplied design and verified material. No text.",
        "material_detail": "Create a square close-up detail of the exact supplied product material, edge construction, shape and printed design. Preserve only visible, verified texture and structure. No text.",
        "floral_detail": "Create a square close-up detail of the exact supplied floral motifs, colors, leaves and separate-piece structure. No text.",
        "botanical_detail": "Create a square close-up detail of the exact supplied botanical product. Preserve the verified flower, grass and foliage types, colors, dried texture, stem structure and bundle composition. Do not add a vase, text or unverified plant varieties.",
        "wall_scene": "Create a square modern wall application scene. The supplied wallpaper pattern must remain exact and recognizable. No text.",
        "cabinet_scene": "Create a square cabinet-front application scene using the exact supplied pattern. No text.",
        "door_scene": "Create a square door-surface application scene distinct from wall and cabinet scenes. No text.",
        "bedroom_scene": "Create a square warm bedroom decor or application scene appropriate to the exact supplied product. Keep the product fully recognizable. No text.",
        "living_room_scene": "Create a square warm living-room decor or application scene appropriate to the exact supplied product. Keep the product fully recognizable. No text.",
        "reading_corner_scene": "Create a square reading-corner decor scene, visually distinct from the living room and bedroom. Preserve the exact supplied product structure and design. No text.",
        "entryway_scene": "Create a square entryway or console-table decor scene with the exact supplied decorative product as the clear hero. Use a camera composition distinct from living-room and bedroom scenes. Do not imply that the vase or staging props are included. No text.",
        "dining_table_scene": "Create a square dining-table or sideboard decor scene with the exact supplied decorative product as the clear hero. Keep its verified structure, palette and scale recognizable. Do not imply that the vase or staging props are included. No text.",
        "photography_prop_scene": "Create a square still-life photography styling scene using the exact supplied decorative product as a photography prop. Preserve its verified structure, palette and composition. Do not add event, wedding or commercial-use claims. No text.",
        "nursery_or_vanity_scene": "Create a square warm vanity-corner or small-space wall-decor scene, visually distinct from the living room and bedroom. Preserve the exact supplied product structure and design. No text.",
        "bathroom_scene": "Create a square modern bathroom scene with the exact supplied floor mat fully visible, realistically scaled and unobstructed. No text.",
        "entrance_scene": "Create a square bright entrance or doorway scene with the exact supplied floor mat fully visible. Use a camera angle clearly distinct from the bathroom scene. No text.",
        "indoor_scene": "Create a square indoor transition-space or vanity-area scene with the exact supplied floor mat fully visible and a third distinct camera composition. No text.",
        "size_comparison": "Create a square English master size card. Show exactly two options: '44 cm x 3 m' and '44 cm x 5 m'. Do not add any other dimensions.",
        "assembled_size": "Create a square dimension card for the complete assembled wall sticker. Show only '125 cm x 80 cm', dimension lines and common unit tokens. Do not add prose or other dimensions.",
        "piece_layout": "Create a square three-piece layout card. Show exactly three separate pieces and only these dimensions: '43 cm x 56 cm', '35 cm x 36 cm', and '45 cm x 56 cm'. Do not add installation or performance claims.",
        "size_and_variants": "Create a square language-neutral size and variant card. Show exactly '50 x 80 cm' and the three supplied verified designs. Use only numbers, dimension lines and common unit tokens. Do not show SKU IDs, prose, extra sizes or extra variants.",
        "quantity_composition": "Create a square English master quantity-and-composition card. Show the verified bundle quantity once as the total and the verified component types stated in the reviewed brief. Do not invent or display numeric per-component quantities unless every component quantity is explicitly verified and their sum exactly equals the verified bundle total. Do not show SKU IDs, dimensions, care claims, a vase as included, or unverified component quantities.",
        "four_piece_layout": "Create a square language-neutral quantity layout showing exactly four separate matching pieces of the supplied product. Preserve the exact verified design and product form. Do not show dimensions, SKU IDs, extra pieces, packaging, adhesive backing, prose or unverified claims.",
        "washable_reusable_use": "Create a square practical care-and-reuse scene showing one supplied product being gently rinsed with clean water and prepared for reuse. Use this role only when washable and reusable are verified in the reviewed brief. Preserve the exact product form and design; do not add detergent, tools, text, badges, adhesive backing or unverified performance claims.",
        "installation": "Create a square English master preparation card with five concise labels only: Measure, Plan, Mark, Cut, Align. Do not depict or claim adhesive backing, peeling, waterproofing, removability, or installation ease.",
    }
    instruction = role_rules.get(resolved_role)
    if not instruction:
        raise ValueError(f"unsupported brand image role: {role}")
    if is_wall_sticker:
        instruction = {
            "cover": "Create a square ecommerce cover showing the exact wall sticker already installed in a styled real room. The installed sticker is the visual hero and occupies about 45-60% of the frame. Show enough furniture for scale, but never show a wallpaper roll, loose printed sheet, packaging, flat lay, white-background packshot, text, badge or watermark.",
            "floral_detail": "Create a square close-to-medium oblique room vignette of the exact installed wall sticker, with crisp floral detail and the exact separate-piece structure preserved, plus some furniture or architectural context for scale. It must not be an isolated white-background motif or a wallpaper roll. No text.",
            "living_room_scene": "Create a square living-room installation scene of the exact wall sticker using a deliberate three-quarter camera angle and asymmetric composition. Keep the sticker prominent, attractive and realistically scaled. No text.",
            "bedroom_scene": "Create a square bedroom installation scene of the exact wall sticker from a camera angle clearly different from the living-room image, with bed or bedside furniture providing scale. Keep the sticker prominent and fully recognizable. No text.",
            "reading_corner_scene": "Create a square reading-corner or lounge-corner installation scene of the exact wall sticker using a diagonal or corner perspective, visually distinct from living-room and bedroom views. No text.",
            "nursery_or_vanity_scene": "Create a square nursery, vanity-corner or compact-home installation scene of the exact wall sticker using a side or corner viewpoint, visually distinct from living-room and bedroom views. Use furniture for scale and no text.",
        }.get(resolved_role, instruction)
    reference_policy = ""
    if product_reference_count is not None:
        reference_policy = f" The first {int(product_reference_count)} ordered reference image(s) are the only product-identity truth."
        if composition_reference_count:
            reference_policy += (
                f" The following {int(composition_reference_count)} reference image(s) are composition-only examples: use their room staging, "
                "camera variety and product prominence, but do not copy their flower artwork, people, furniture, logos, badges, text or watermarks."
            )
    return (
        "Generate one net-new, production-ready ecommerce product image from the supplied references. "
        f"Verified product identity: {product_identity}. "
        "Preserve the exact product structure, design, palette, motif shapes, spacing and proportions visible in the supplied reference. "
        "Do not invent extra products, colors, materials, dimensions, certifications, logos, watermarks, discounts, or performance claims. "
        "Use the references only under the ordered reference policy and create a genuinely new composition."
        f"{reference_policy} Visual family: {brand_label}. Positioning: {positioning} "
        f"Task: {instruction} Reviewed brief: {brief}"
    )
