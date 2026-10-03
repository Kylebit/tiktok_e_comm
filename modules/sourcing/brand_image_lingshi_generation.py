"""Checkpointed dual-brand ecommerce image generation through Lingshi AI."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from modules.sourcing.brand_image_prompt import build_brand_image_prompt
from modules.sourcing.lingshi_client import LingshiClient, MAX_UPLOAD_BYTES
from modules.sourcing.image_generation_checkpoint import ImageCheckpoint, execute_image_checkpoint
from modules.sourcing.localized_image_lingshi_generation import (
    _download_result,
    _png_bytes,
    _wait_for_task,
)


RENDERER = "lingshi-brand-image/v1"
PROVIDER = "lingshi-media/v1"
MODEL = "gpt-image-2"
MODEL_CANDIDATES = ("gpt-image-2", "tt-image-2")
SIZE = "2048x2048"
QUALITY = "medium"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_data_uri(source: Path) -> tuple[str, bytes]:
    if not source.is_file():
        raise FileNotFoundError(source)
    raw = source.read_bytes()
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Lingshi brand reference must be between 1 byte and 10MB")
    mime = mimetypes.guess_type(source.name)[0] or ""
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("Lingshi brand reference type is unsupported")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}", raw


def resolve_brand_image_model(client: LingshiClient) -> tuple[str, dict[str, Any]]:
    """Resolve an available GPT Image 2 alias and verify the required edit contract."""

    catalog = client.list_models("image")
    available = {
        str(row.get("name") or ""): row
        for row in (catalog.get("models") or [])
        if isinstance(row, Mapping) and row.get("available_for_this_key") is True
    }
    for candidate in MODEL_CANDIDATES:
        if candidate not in available:
            continue
        detail = client.model_detail(candidate)
        params = {
            str(row.get("name") or ""): row
            for row in (detail.get("params") or [])
            if isinstance(row, Mapping)
        }
        if "images" not in params or "size" not in params or "quality" not in params:
            continue
        size_options = {
            str(row.get("value") or "")
            for row in (params["size"].get("options") or [])
            if isinstance(row, Mapping)
        }
        quality_options = {
            str(row.get("value") or "")
            for row in (params["quality"].get("options") or [])
            if isinstance(row, Mapping)
        }
        if SIZE in size_options and QUALITY in quality_options:
            return candidate, detail
    raise ValueError(
        "Lingshi has no available GPT Image 2 alias with images, 2048x2048 and medium quality"
    )


def generate_brand_image(
    *,
    offer_id: str,
    brand_id: str,
    brand_label: str,
    positioning: str,
    role: str,
    brief: str,
    product_identity: str,
    source_paths: Sequence[str | Path] = (),
    source_urls: Sequence[str] = (),
    source_url_digests: Mapping[str, str] | None = None,
    product_reference_count: int | None = None,
    checkpoint_dir: str | Path,
    client: LingshiClient | None = None,
    model: str | None = None,
    retry_attempt: int = 0,
    paid_context=None,
    rework_basis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute an already authorized role, binding its complete prompt and references.

    Optional URL digests are upstream-verified content identities. Without one,
    the receipt explicitly reports incomplete source content identity; a URL is
    never represented as proof that remote image bytes have stayed unchanged.
    """
    selected_model = str(model or MODEL).strip()
    if not selected_model:
        raise ValueError("Lingshi brand model is required")
    if not all(str(value or "").strip() for value in (offer_id, brand_id, role)):
        raise ValueError("brand business identity requires product, brand and role")
    provider_references: list[str] = []
    source_identities: list[dict[str, str | None]] = []
    for source_path in source_paths:
        provider_reference, source_bytes = _source_data_uri(Path(source_path))
        provider_references.append(provider_reference)
        source_identities.append({"sha256": hashlib.sha256(source_bytes).hexdigest()})
    for source_url in source_urls:
        parsed = urlparse(str(source_url or "").strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Lingshi brand reference URL must use public HTTPS without credentials")
        provider_references.append(parsed.geturl())
        supplied_digest = (source_url_digests or {}).get(parsed.geturl())
        if supplied_digest is not None:
            supplied_digest = str(supplied_digest).removeprefix("sha256:")
            if len(supplied_digest) != 64 or any(value not in "0123456789abcdef" for value in supplied_digest):
                raise ValueError("source URL content identity must be a SHA256 digest")
        source_identities.append({"url": parsed.geturl(), "sha256": supplied_digest})
    if not 1 <= len(provider_references) <= 14:
        raise ValueError("Lingshi brand generation requires 1-14 source references")
    if product_reference_count is None:
        product_reference_count = len(provider_references)
    if type(product_reference_count) is not int or not 1 <= product_reference_count <= len(provider_references):
        raise ValueError("Lingshi product reference count is invalid")
    prompt = build_brand_image_prompt(
        brand_label=brand_label, positioning=positioning, role=role, brief=brief,
        product_identity=product_identity, product_reference_count=product_reference_count,
        composition_reference_count=len(provider_references) - product_reference_count,
    )
    params = {"images": provider_references, "size": SIZE, "quality": QUALITY}
    checkpoint = ImageCheckpoint(
        checkpoint_dir, kind="brand",
        business_identity={"offer_id": str(offer_id), "brand_id": brand_id, "role": role},
        request_identity={"renderer": RENDERER, "model": selected_model, "prompt": prompt,
                          "params": params, "source_content_identities": source_identities},
        model=selected_model, source_identity_complete=all(row.get("sha256") for row in source_identities),
    )
    if paid_context is not None:
        from shared_platform.publication_paid_requests import require_paid_context
        require_paid_context(paid_context)
        if paid_context.offer_id != str(offer_id) or not checkpoint.source_identity_complete:
            raise ValueError("R2 brand generation requires exact product and complete source byte identities")
    if rework_basis is not None:
        if paid_context is None:
            raise ValueError("R2 rework requires its existing authorization and product budget")
        from modules.sourcing.image_generation_checkpoint import prepare_image_rework
        retry_attempt = prepare_image_rework(checkpoint,basis=rework_basis)
    api = client
    def get_api():
        nonlocal api
        if api is None:
            api = LingshiClient.from_config(Path(__file__).resolve().parents[2] / "config" / "lingshi.local.json")
        return api
    result = execute_image_checkpoint(
        checkpoint, retry_attempt=retry_attempt,
        submit=lambda: get_api().create_media_generation(
            model=selected_model, prompt=prompt, params=params,
            allow_paid_request=True, allow_external_upload=True,
        ),
        poll=lambda task_id: _wait_for_task(get_api(), task_id),
        load_result=_download_result, normalize_image=_png_bytes,
        paid_context=paid_context, paid_purpose="brand_image_generation",
    )
    return {**result, "checkpoint_path":str(checkpoint.path), "artifact_id": f"brand-{brand_id}-{role}-{checkpoint.request_digest[:12]}"}
