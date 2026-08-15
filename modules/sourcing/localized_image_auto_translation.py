"""Strict automatic translation for locally detected image text.

The model may translate text, but it may not change OCR region identities,
source text, numeric facts, locale coverage, or publication state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from modules.sourcing.image_suite_plan import chat_completions, message_content


MODEL = "gpt-5.4-mini-official"
PROVIDER = "toapis-chat-completions/v1"
SCHEMA_VERSION = "localized-image-auto-translation/v1"
AUTO_TRANSLATION_LOCALES = ("ms-MY", "th-TH", "vi-VN", "ru-RU", "es-MX")
_LOCALE_NAMES = {
    "ms-MY": "natural Malaysian Malay",
    "th-TH": "natural Thai",
    "vi-VN": "natural Vietnamese",
    "ru-RU": "natural Russian",
    "es-MX": "natural Mexican Spanish",
}
_SYSTEM_PROMPT = """You translate English text already printed inside ecommerce images.
Translate only the supplied source_text into every requested locale. Do not add
selling claims, facts, dimensions, warnings, brands, or punctuation that are not
present. Preserve every number, decimal, unit token, model code, and proper brand.
Keep each translation concise enough for the same image box. Return JSON only.

The response must be exactly:
{
  "schema_version": "localized-image-auto-translation/v1",
  "translations": {
    "ms-MY": [{"region_id":"...","source_text":"...","translated_text":"..."}],
    "th-TH": [...], "vi-VN": [...], "ru-RU": [...], "es-MX": [...]
  }
}
Each locale must contain every input region exactly once and in input order."""


class LocalizedImageAutoTranslationError(ValueError):
    """The automatic translation response cannot safely bind to OCR facts."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _default_model_call(
    messages: list[dict[str, Any]], *, temperature: float, max_tokens: int
) -> str:
    response = chat_completions(
        messages,
        model=MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return message_content(response)


def _json_object(raw: object) -> dict[str, Any]:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.I)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise LocalizedImageAutoTranslationError(
            "automatic translation did not return valid JSON"
        ) from error
    if not isinstance(payload, dict):
        raise LocalizedImageAutoTranslationError(
            "automatic translation did not return a JSON object"
        )
    return payload


def _source_rows(regions: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in regions:
        if not isinstance(raw, Mapping):
            raise LocalizedImageAutoTranslationError("OCR region is invalid")
        region_id = str(raw.get("region_id") or "").strip()
        source_text = str(raw.get("source_text") or "").strip()
        if (
            not re.fullmatch(r"text-[a-f0-9]{20}", region_id)
            or not source_text
            or len(source_text) > 500
        ):
            raise LocalizedImageAutoTranslationError("OCR region is invalid")
        rows.append({"region_id": region_id, "source_text": source_text})
    ids = [row["region_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise LocalizedImageAutoTranslationError("OCR region identity is ambiguous")
    return rows


def _numeric_tokens(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def _requires_translation(source_text: str) -> bool:
    letters = re.findall(r"[A-Za-z]", source_text)
    invariant_code = bool(re.fullmatch(r"[A-Z0-9_.&+\-/]{2,20}", source_text))
    return len(letters) >= 4 and not invariant_code


def _validate_target_language(locale: str, source_text: str, translated: str) -> None:
    if not _requires_translation(source_text):
        return
    # Retail terms and short phrases can legitimately be identical loanwords
    # in Malay, Vietnamese or Spanish. Those Latin-script languages cannot be
    # classified reliably without a separate model, so their hard gates remain
    # exact coverage, non-empty text and numeric preservation. Thai and Russian
    # still require their target script and therefore cannot remain English.
    if translated.casefold() == source_text.casefold() and locale in {
        "th-TH",
        "ru-RU",
    }:
        raise LocalizedImageAutoTranslationError(
            f"{locale} target language translation is unchanged"
        )
    if locale == "th-TH" and not re.search(r"[\u0e00-\u0e7f]", translated):
        raise LocalizedImageAutoTranslationError("th-TH target language is missing")
    if locale == "ru-RU" and not re.search(r"[\u0400-\u04ff]", translated):
        raise LocalizedImageAutoTranslationError("ru-RU target language is missing")


def _validated_translations(
    payload: Mapping[str, Any], source_rows: Sequence[Mapping[str, str]]
) -> dict[str, list[dict[str, str]]]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise LocalizedImageAutoTranslationError(
            "automatic translation schema is unsupported"
        )
    raw_translations = payload.get("translations")
    if not isinstance(raw_translations, Mapping) or set(raw_translations) != set(
        AUTO_TRANSLATION_LOCALES
    ):
        raise LocalizedImageAutoTranslationError(
            "automatic translation locale coverage has changed"
        )
    expected_ids = [row["region_id"] for row in source_rows]
    expected_by_id = {row["region_id"]: row["source_text"] for row in source_rows}
    result: dict[str, list[dict[str, str]]] = {}
    for locale in AUTO_TRANSLATION_LOCALES:
        raw_rows = raw_translations.get(locale)
        if not isinstance(raw_rows, list):
            raise LocalizedImageAutoTranslationError(
                f"{locale} translation region coverage is invalid"
            )
        ids = [str(row.get("region_id") or "").strip() for row in raw_rows if isinstance(row, Mapping)]
        if ids != expected_ids or len(raw_rows) != len(source_rows):
            raise LocalizedImageAutoTranslationError(
                f"{locale} translation region coverage has changed"
            )
        clean_rows: list[dict[str, str]] = []
        for raw in raw_rows:
            if not isinstance(raw, Mapping):
                raise LocalizedImageAutoTranslationError(
                    f"{locale} translation row is invalid"
                )
            region_id = str(raw.get("region_id") or "").strip()
            source_text = str(raw.get("source_text") or "").strip()
            translated = str(raw.get("translated_text") or "").strip()
            if source_text != expected_by_id.get(region_id):
                raise LocalizedImageAutoTranslationError(
                    f"{locale} source text identity has changed"
                )
            if not translated or len(translated) > 800 or "\n" in translated:
                raise LocalizedImageAutoTranslationError(
                    f"{locale} translated text is invalid"
                )
            if _numeric_tokens(translated) != _numeric_tokens(source_text):
                raise LocalizedImageAutoTranslationError(
                    f"{locale} numeric tokens have changed"
                )
            _validate_target_language(locale, source_text, translated)
            clean_rows.append(
                {
                    "region_id": region_id,
                    "source_text": source_text,
                    "translated_text": translated,
                }
            )
        result[locale] = clean_rows
    return result


def translate_image_regions(
    regions: Sequence[Mapping[str, Any]],
    *,
    model_call: Callable[..., str] = _default_model_call,
) -> dict[str, Any]:
    """Translate one image's OCR rows to all locales in exactly one model call."""

    source_rows = _source_rows(regions)
    source_digest = _digest(source_rows)
    if not source_rows:
        return {
            "schema_version": SCHEMA_VERSION,
            "translations": {locale: [] for locale in AUTO_TRANSLATION_LOCALES},
            "receipt": {
                "status": "NO_TEXT_REUSE_BASE",
                "provider": PROVIDER,
                "model": MODEL,
                "model_calls": 0,
                "source_digest": source_digest,
            },
        }
    request = {
        "schema_version": SCHEMA_VERSION,
        "locales": [
            {"locale": locale, "language": _LOCALE_NAMES[locale]}
            for locale in AUTO_TRANSLATION_LOCALES
        ],
        "regions": source_rows,
    }
    raw = model_call(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Translate this exact OCR inventory:\n"
                + json.dumps(request, ensure_ascii=False, indent=2),
            },
        ],
        temperature=0.0,
        # One image may contain 10-20 regions and every region is returned for
        # five locales. A smaller dynamic ceiling truncated otherwise valid
        # JSON on real 14-region product cards, so reserve the full bounded
        # response budget while still making exactly one request per image.
        max_tokens=8000,
    )
    translations = _validated_translations(_json_object(raw), source_rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "translations": translations,
        "receipt": {
            "status": "AUTO_TRANSLATED",
            "provider": PROVIDER,
            "model": MODEL,
            "model_calls": 1,
            "source_digest": source_digest,
            "translation_digest": _digest(translations),
        },
    }
