import json
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "config.json")
CONFIG_EXAMPLE = os.path.join(BASE_DIR, "data", "config.example.json")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = (
    "Ты — копирайтер маркетплейса Ozon. Основа работы — исходное название товара с TikTok Shop "
    "(малайский, английский или другой язык SEA). Переведи смысл на естественный русский для карточки Ozon.\n\n"
    "Стиль и структура — как у успешных карточек декора для дома на Ozon:\n"
    "- title: русский заголовок 60–100 символов (минимум 60, максимум 200). "
    "Опирайся на исходное TikTok-название: сохрани тип товара, узор/цвет/стиль, ключевые свойства, "
    "размер и материал — всё, что явно следует из оригинала. "
    "Не выдумывай детали, которых нет в названии. Без английских слов, без списков ключей.\n"
    "- description: связный текст на русском, 5–8 предложений, тоже по смыслу исходного названия. "
    "Опиши узор/цвет/стиль, свойства (самоклеящаяся, водостойкая, съёмная), "
    "где использовать, размеры, материал, комплектацию и монтаж. "
    "БЕЗ списков «—», БЕЗ разделов «Особенности:», БЕЗ хэштегов.\n"
    "- hashtags: строка из 4-6 русских хэштегов через пробел (ТОЛЬКО для поля hashtags, НЕ в description).\n"
    "- description: НИКОГДА не включай символ # и хэштеги в текст описания.\n"
    "- description: НЕ используй слова «оригинал», «original», «подлинный» и НЕ вставляй исходное название с TikTok.\n"
    "- description: НЕ пиши «по ключевым словам» — только естественный текст для покупателя.\n"
    "- color_name: основной цвет товара по-русски (одно слово или словосочетание).\n"
    "- material: материал товара — ОБЯЗАТЕЛЬНО выбери ОДНО значение из следующего списка (точная строка):\n"
    "  \"ПВХ (поливинилхлорид)\", \"Полиэстер\", \"Нетканый материал (спанбонд)\", \"Хлопок\",\n"
    "  \"Акрил\", \"Силикон\", \"Бумага\", \"Стекло\", \"Металл\", \"Дерево\", \"Резина\", \"Вискоза\".\n"
    "  Для наклеек/стикеров обычно \"ПВХ (поливинилхлорид)\"; для скатертей/чехлов обычно \"Полиэстер\".\n"
    "  НЕ придумывай значения за пределами этого списка.\n"
    "- kit: комплектация коротко (например: \"1 штука\", \"набор 10 штук\").\n"
    "- weight_g: примерный вес в граммах (число).\n"
    "- depth_mm, width_mm, height_mm: примерные габариты упаковки в мм (число).\n"
    "- len_cm, wid_cm: размеры изделия в см (строки).\n"
    "- price_cny: цена в юанях CNY, рассчитай из TikTok MYR цены по указанному курсу, округли до целых.\n"
    "- old_price_cny: зачёркнутая цена = price_cny * 1.3, округли до целых.\n"
    "- type_id: выбери ОДИН type_id из предоставленного списка категорий, наиболее точно "
    "описывающий ЧТО ЭТО ЗА ПРЕДМЕТ физически. Верни целое число.\n\n"
    "Если есть блок «Разбор исходного названия» — это автоматическая подсказка по тому же TikTok-заголовку. "
    "Используй её как вспомогательную, но главный источник — полное исходное название в начале запроса. "
    "Черновики title/description можно перефразировать естественнее, не теряя фактов из оригинала.\n\n"
    "Отвечай СТРОГО валидным JSON без markdown, со всеми перечисленными ключами."
)


def deepseek_api_key() -> str:
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env
    try:
        from core.config import get

        k = (get("ai.api_key") or "").strip()
        if k:
            return k
    except Exception:
        pass
    for path in (CONFIG_PATH, CONFIG_EXAMPLE):
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    k = (json.load(f).get("deepseek_api_key") or "").strip()
                if k and not k.startswith("YOUR_"):
                    return k
            except (json.JSONDecodeError, OSError):
                pass
    return ""


def generate_draft(
    title_ms: str,
    offer_id: str,
    price_myr: float = None,
    myr_cny_rate: float = 1.55,
    category_candidates: list = None,
    price_local: float = None,
    price_currency: str = None,
    tk_category_path: str = "",
    tk_category_leaf: str = "",
    product_type_hint: str = "",
    rule_context: dict | None = None,
) -> dict:
    price_info = ""
    if price_local is not None and price_currency:
        price_info = (
            f"\nЦена на маркетплейсе: {price_local} {price_currency} "
            f"(уже пересчитана в CNY отдельно, верни price_cny/old_price_cny как обычно)."
        )
    elif price_myr:
        price_info = (
            f"\nTikTok цена: {price_myr} MYR, курс 1 MYR = {myr_cny_rate} CNY "
            f"→ price_cny = round({price_myr} * {myr_cny_rate})"
        )

    cat_hint = ""
    if tk_category_path or tk_category_leaf:
        cat_hint = f"\nКатегория TikTok Shop: {tk_category_path or tk_category_leaf}"

    type_hint = ""
    if product_type_hint == "tablecloth":
        type_hint = (
            "\n\nТип товара: скатерть / салфетка на стол (текстиль). "
            "type_id фиксирован: 92692, category_id: 17028730. "
            "Материал: полиэстер. Заголовок и описание — про скатерть для стола, "
            "не про наклейку на стену."
        )
    elif product_type_hint == "sticker":
        type_hint = (
            "\n\nТип товара: самоклеящаяся декоративная наклейка / плёнка на стену, окно или мебель. "
            "В title и description используй «наклейка» или «плёнка», НЕ «обои». "
            "type_id для интерьерных наклеек: 91971."
        )

    cand_str = ""
    if category_candidates:
        lines = "\n".join(
            f"{c['type_id']}: {c['type_name_zh']} (группа: {c['cat_name_zh']})"
            for c in category_candidates
        )
        cand_str = f"\n\nДоступные type_id для выбора:\n{lines}"
    elif product_type_hint == "tablecloth":
        cand_str = "\n\nИспользуй type_id=92692 (скатерть)."

    rule_str = ""
    if rule_context:
        try:
            import translate as _translate  # noqa: WPS433

            rule_str = _translate.format_rule_context_for_prompt(rule_context)
        except Exception:
            pass

    user_prompt = (
        f"Исходное название TikTok Shop (главная основа для перевода):\n{title_ms}\n"
        f"offer_id (для справки, не включай в текст): {offer_id}"
        + cat_hint
        + type_hint
        + price_info
        + cand_str
        + rule_str
    )

    key = deepseek_api_key()
    if not key:
        raise RuntimeError("未配置 DeepSeek API Key（tiktok settings.json ai.api_key 或 data/config.json）")

    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
    }

    cmd = [
        "curl", "-s", "--noproxy", "*", DEEPSEEK_URL,
        "-H", "Content-Type: application/json",
        "-H", "Authorization: Bearer " + key,
        "-d", json.dumps(body),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    resp = json.loads(out)

    if "error" in resp:
        raise RuntimeError("DeepSeek API error: " + json.dumps(resp["error"], ensure_ascii=False))

    content = resp["choices"][0]["message"]["content"]
    return json.loads(content)
