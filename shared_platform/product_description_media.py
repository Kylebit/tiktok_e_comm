"""Deterministic product-description media contracts shared by publishers.

The ordered gallery selected by the approved release plan is also the ordered
description gallery.  Keeping the builders here prevents a channel adapter
from silently downgrading a rich description to plain text.
"""

from __future__ import annotations

from html import escape
import hashlib
import json
from typing import Mapping, Sequence

from domains.product_operations.approved_publication_snapshot import (
    publication_images_for_target,
)


DESCRIPTION_MEDIA_PREFLIGHT_SCHEMA = "description-media-preflight/v1"


def validate_description_media_preflight(
    snapshot: Mapping[str, object],
    *,
    platform: str,
    target_labels: Sequence[str],
) -> dict[str, object]:
    """Fail closed before TikTok/Shopee writes if a target route is incomplete.

    This is deliberately a single-phase snapshot gate. It does not upload an
    image or introduce an additional provider stage; it proves that every
    selected target has non-empty copy and one complete, ordered, unique image
    route available to both the gallery and description builders.
    """

    normalized_platform = str(platform or "").strip().upper()
    if normalized_platform not in {"TIKTOK", "SHOPEE"}:
        raise ValueError("description-media preflight platform is unsupported")
    labels = tuple(str(label or "").strip() for label in target_labels)
    if not labels or any(not label for label in labels) or len(set(labels)) != len(labels):
        raise ValueError("description-media preflight target scope is invalid")
    product = snapshot.get("product")
    if not isinstance(product, Mapping) or not str(product.get("description") or "").strip():
        raise ValueError("description-media preflight requires approved description text")

    rows: list[dict[str, object]] = []
    for label in labels:
        if not label.casefold().startswith(normalized_platform.casefold() + ":"):
            raise ValueError("description-media preflight target platform conflicts")
        images = tuple(publication_images_for_target(snapshot, label))
        if not images or any(not image.startswith("https://") for image in images):
            raise ValueError(f"{label} description-media route is incomplete")
        if len(images) != len(set(images)):
            raise ValueError(f"{label} description-media route contains duplicates")
        route_digest = hashlib.sha256(
            json.dumps(images, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        rows.append(
            {
                "target_label": label,
                "gallery_image_count": len(images),
                "description_image_count": len(images),
                "ordered_route_digest": "sha256:" + route_digest,
            }
        )
    return {
        "schema_version": DESCRIPTION_MEDIA_PREFLIGHT_SCHEMA,
        "platform": normalized_platform,
        "targets": rows,
        "passed": True,
    }


def miaoshou_rich_description(description: str, image_urls: Sequence[str]) -> str:
    """Build the exact Miaoshou HTML description with every approved image."""

    text = str(description or "").strip()
    images = _unique_nonempty(image_urls, "description image URL")
    if not images:
        raise ValueError("description images are required")
    text_html = escape(text).replace("\r\n", "\n").replace("\r", "\n")
    text_html = text_html.replace("\n", "<br>")
    return (f"<p>{text_html}</p>" if text_html else "") + "".join(
        f'<p><img src="{escape(url, quote=True)}"></p>' for url in images
    )


def tiktok_rich_description(
    description: str,
    images: Sequence[Mapping[str, object]],
) -> str:
    """Build TikTok HTML from the platform-hosted ordered gallery.

    TikTok description images must use TikTok Shop URLs and include explicit
    dimensions.  Consequently this builder accepts official product-readback
    image objects, not pre-upload source URLs.
    """

    text = str(description or "").strip()
    if not text:
        raise ValueError("description text is required")
    normalized: list[tuple[str, int, int]] = []
    for image in images:
        if not isinstance(image, Mapping):
            raise ValueError("TikTok description image evidence is invalid")
        urls = image.get("urls") or image.get("url_list") or ()
        url = (
            str(urls[0] or "").strip()
            if isinstance(urls, Sequence) and not isinstance(urls, (str, bytes)) and urls
            else str(image.get("url") or "").strip()
        )
        try:
            width = int(image.get("width") or 0)
            height = int(image.get("height") or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("TikTok description image dimensions are invalid") from error
        if not url or width <= 0 or height <= 0:
            raise ValueError("TikTok description image evidence is incomplete")
        normalized.append((url, width, height))
    if not normalized:
        raise ValueError("TikTok description images are required")
    urls = [row[0] for row in normalized]
    if len(set(urls)) != len(urls):
        raise ValueError("TikTok description image URLs must be unique")
    text_html = escape(text).replace("\r\n", "\n").replace("\r", "\n")
    text_html = text_html.replace("\n", "<br>")
    return f"<p>{text_html}</p>" + "".join(
        (
            f'<p><img src="{escape(url, quote=True)}" '
            f'width="{width}" height="{height}"></p>'
        )
        for url, width, height in normalized
    )


def html_image_urls(description: object) -> tuple[str, ...]:
    """Extract ordered ``img`` sources from provider-returned HTML."""

    from html.parser import HTMLParser

    class _ImageParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.casefold() != "img":
                return
            src = str(dict(attrs).get("src") or "").strip()
            if src:
                self.urls.append(src)

    parser = _ImageParser()
    parser.feed(str(description or ""))
    return tuple(parser.urls)


def shopee_extended_description(
    description: str,
    image_ids: Sequence[str],
) -> dict[str, object]:
    """Build Shopee's ordered extended-description text and image fields."""

    text = str(description or "").strip()
    images = _unique_nonempty(image_ids, "description image ID")
    if not text or not images:
        raise ValueError("description text and image IDs are required")
    return {
        "extended_description": {
            "field_list": [
                {"field_type": "text", "text": text},
                *(
                    {
                        "field_type": "image",
                        "image_info": {"image_id": image_id},
                    }
                    for image_id in images
                ),
            ]
        }
    }


def shopee_description_image_ids(item: Mapping[str, object] | None) -> tuple[str, ...]:
    """Read ordered image IDs from an official Shopee extended description."""

    if not isinstance(item, Mapping):
        return ()
    description_info = item.get("description_info")
    extended = (
        description_info.get("extended_description")
        if isinstance(description_info, Mapping)
        else None
    )
    fields = extended.get("field_list") if isinstance(extended, Mapping) else None
    if not isinstance(fields, list):
        return ()
    result: list[str] = []
    for field in fields:
        if not isinstance(field, Mapping) or str(field.get("field_type") or "").lower() != "image":
            continue
        image_info = field.get("image_info")
        image_id = (
            str(image_info.get("image_id") or "").strip()
            if isinstance(image_info, Mapping)
            else ""
        )
        if image_id:
            result.append(image_id)
    return tuple(result)


def shopee_description_text(item: Mapping[str, object] | None) -> str:
    """Read canonical text from normal or extended Shopee descriptions."""

    if not isinstance(item, Mapping):
        return ""
    normal = str(item.get("description") or "").strip()
    if normal:
        return normal
    description_info = item.get("description_info")
    extended = (
        description_info.get("extended_description")
        if isinstance(description_info, Mapping)
        else None
    )
    fields = extended.get("field_list") if isinstance(extended, Mapping) else None
    if not isinstance(fields, list):
        return ""
    texts: list[str] = []
    for field in fields:
        if not isinstance(field, Mapping) or str(field.get("field_type") or "").lower() != "text":
            continue
        text = str(field.get("text") or "").strip()
        if not text:
            text_info = field.get("text_info")
            text = (
                str(text_info.get("text") or "").strip()
                if isinstance(text_info, Mapping)
                else ""
            )
        if text:
            texts.append(text)
    return "\n".join(texts)


def _unique_nonempty(values: Sequence[str], label: str) -> tuple[str, ...]:
    clean = tuple(str(value or "").strip() for value in values)
    if not clean or any(not value for value in clean):
        raise ValueError(f"{label}s are incomplete")
    if len(set(clean)) != len(clean):
        raise ValueError(f"{label}s must be unique")
    return clean
