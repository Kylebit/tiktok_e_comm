"""按产品类型（profile）组装 Ozon import 属性。"""

from __future__ import annotations

from modules.ozon.listing_text import (
    polish_ozon_description,
    polish_ozon_title,
    tablecloth_hashtags,
)
from modules.ozon.tk_category_map import profile_for_type_id


def _attr(id_: int, val: str, dict_id: int = 0) -> dict:
    return {"complex_id": 0, "id": id_, "values": [{"dictionary_value_id": dict_id, "value": str(val)}]}


# type_id → profile（可被 tk_category_ozon_map.json 的 type_profiles 覆盖/扩展）
BUILTIN_TYPE_PROFILES: dict[int, str] = {
    91971: "sticker",
    115946973: "frame",
    92692: "tablecloth",
}


def resolve_profile(type_id: int, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    mapped = profile_for_type_id(type_id)
    if mapped != "generic":
        return mapped
    return BUILTIN_TYPE_PROFILES.get(int(type_id), "generic")


def build_import_attributes(p: dict) -> list[dict]:
    """
    p 需含 offer_id, title, description, hashtags, color_name, color_dict_id,
    material, material_dict_id, kit, weight, len_cm, wid_cm, type_id,
    migrate_profile (optional)
    """
    offer_id = str(p["offer_id"])
    type_id = int(p.get("type_id") or 0)
    profile = resolve_profile(type_id, p.get("migrate_profile"))

    hashtags = p.get("hashtags") or ""
    kit = p.get("kit") or ("1 шт" if profile == "tablecloth" else "1 штука")
    description = polish_ozon_description(p.get("description") or "")
    title = polish_ozon_title(
        p.get("title") or "",
        len_cm=str(p.get("len_cm") or ""),
        wid_cm=str(p.get("wid_cm") or ""),
        migrate_profile=str(p.get("migrate_profile") or ""),
    )
    material = p.get("material") or "ПВХ (поливинилхлорид)"
    material_dict = int(p.get("material_dict_id") or 61996)
    if profile == "tablecloth":
        material = p.get("material") or "Полиэстер"
        material_dict = int(p.get("material_dict_id") or 62040)

    attrs: list[dict] = [
        _attr(85, "Нет бренда", 126745801),
        _attr(4191, description),
        _attr(10096, p["color_name"], int(p["color_dict_id"])),
        _attr(6383, material, material_dict),
        _attr(4389, "Китай", 90296),
        _attr(4384, kit),
        _attr(4180, title),
        _attr(4497, str(p["weight"])),
        _attr(9024, offer_id),
    ]

    if profile == "sticker":
        attrs.extend([
            _attr(8229, "Наклейка интерьерная", 91971),
            _attr(6384, "Декоративная", 29116),
            _attr(9048, offer_id + "_sticker"),
            _attr(23171, hashtags or "#наклейканастену #декордлядома"),
            _attr(8415, str(p.get("len_cm") or "")),
            _attr(8416, str(p.get("wid_cm") or "")),
        ])
    elif profile == "frame":
        clip_val = kit if "клип" in kit.lower() or "шт" in kit.lower() else kit
        attrs.extend([
            _attr(9048, "#" + offer_id),
            _attr(23171, hashtags or "#фоторамка #декордлядома #интерьер"),
            _attr(10097, clip_val),
        ])
    elif profile == "textile":
        attrs.extend([
            _attr(9048, offer_id),
            _attr(23171, hashtags or "#декордлядома #текстиль"),
        ])
    elif profile == "tablecloth":
        len_cm = str(p.get("len_cm") or "").strip()
        wid_cm = str(p.get("wid_cm") or "").strip()
        size_label = f"{len_cm}x{wid_cm}" if len_cm and wid_cm else (len_cm or wid_cm)
        attrs.extend([
            _attr(9048, "#" + offer_id),
            _attr(23171, hashtags or tablecloth_hashtags()),
        ])
        if size_label:
            attrs.append(_attr(6765, size_label))
        if len_cm:
            attrs.append(_attr(8415, len_cm))
        if wid_cm:
            attrs.append(_attr(8416, wid_cm))
    else:
        attrs.extend([
            _attr(9048, offer_id),
            _attr(23171, hashtags or "#декордлядома"),
        ])

    return attrs
