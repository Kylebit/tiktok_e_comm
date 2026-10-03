"""Durable Lingshi media generation for approved localized product images."""

from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence
import urllib.parse

from PIL import Image
import requests

from modules.sourcing.lingshi_client import LingshiClient, LingshiClientError
from modules.sourcing.image_generation_checkpoint import (
    ImageCheckpoint, ImageTaskFailed,
    ImageSubmissionRejected as LingshiMediaSubmissionRejected,
    atomic_bytes as _atomic_bytes, atomic_json as _atomic_json,
    execute_image_checkpoint,
)
RENDERER = "lingshi-localized-image/v1"
PROVIDER = "lingshi-media/v1"
MODEL = "gpt-image-2"
_LOCALE_NAMES = {
    "ms-MY": "Malay for Malaysia",
    "th-TH": "Thai for Thailand",
    "vi-VN": "Vietnamese for Vietnam",
    "ru-RU": "Russian for Russia",
    "es-MX": "Spanish for Mexico",
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_localized_reference_prompt(
    *, locale: str, translations: Sequence[Mapping[str, Any]]
) -> str:
    language = _LOCALE_NAMES.get(str(locale or ""))
    if not language:
        raise ValueError("unsupported localized image locale")
    pairs = []
    for row in translations:
        source = str(row.get("source_text") or "").strip()
        translated = str(row.get("translated_text") or "").strip()
        if not source or not translated:
            raise ValueError("localized image translation is incomplete")
        pairs.append({"source": source, "replacement": translated})
    if not pairs:
        raise ValueError("localized image translation is empty")
    return (
        "Edit the supplied reference product image into a localized ecommerce image. "
        f"Replace the listed English text with the exact {language} replacement text. "
        "Remove every listed source string completely before rendering its replacement; "
        "no listed English source phrase may remain anywhere in the final image unless its "
        "replacement is exactly identical. Inspect the finished image for residual source text. "
        "Preserve the same product identity, product count, composition, colors, texture, "
        "dimensions, numeric facts, and overall commercial meaning. Minor natural layout "
        "or background changes are acceptable. Do not add logos, watermarks, claims, text, "
        "or objects that are not requested. Render every replacement legibly and exactly, "
        "including numbers and units. Replacement list: "
        + _canonical(pairs)
    )


def _png_bytes(raw: bytes) -> bytes:
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError("Lingshi localized image is empty or too large")
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()
            output = BytesIO()
            image.convert("RGB").save(output, format="PNG", optimize=True)
            return output.getvalue()
    except Exception as error:
        raise ValueError("Lingshi localized image is invalid") from error


def _size_for(source_bytes: bytes) -> str:
    try:
        with Image.open(BytesIO(source_bytes)) as image:
            ratio = image.width / max(1, image.height)
    except Exception as error:
        raise ValueError("approved source image is invalid") from error
    if ratio >= 1.2:
        return "1536x1024"
    if ratio <= 0.83:
        return "1024x1536"
    return "1024x1024"


def _download_result(url: str) -> bytes:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Lingshi result URL must be public HTTPS")
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            parsed.geturl(), timeout=90, headers={"User-Agent": "OrbitHive-Lingshi-Result/1.0"}
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise LingshiClientError(
            f"Lingshi result download failed: {type(error).__name__}"
        ) from error
    return response.content


def _wait_for_task(
    client: LingshiClient,
    task_id: int,
    *,
    timeout: float = 900,
    poll_interval: float = 5,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    read_errors = 0
    while True:
        try:
            result = client.get_media_task(task_id)
            read_errors = 0
        except LingshiClientError:
            read_errors += 1
            if read_errors >= 3 or time.monotonic() >= deadline:
                raise
            sleeper(poll_interval)
            continue
        if bool(result.get("is_final")):
            if result.get("error") or str(result.get("status_group") or "") == "失败":
                raise ImageTaskFailed("Lingshi generation failed: provider reported terminal task failure")
            if not str(result.get("result_url") or "").startswith("https://"):
                raise RuntimeError("Lingshi completed task has no public HTTPS result")
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Lingshi generation timed out: {task_id}")
        sleeper(poll_interval)


def generate_localized_reference_image(
    *,
    source_url: str,
    source_bytes: bytes,
    locale: str,
    translations: Sequence[Mapping[str, Any]],
    checkpoint_dir: str | Path,
    retry_attempt: int = 0,
    model: str | None = None,
    client: LingshiClient | None = None,
    result_loader: Callable[[str], bytes] = _download_result,
    paid_context=None,
    business_identity: Mapping[str, Any] | None = None,
    rework_basis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(str(source_url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("localized image source must be a public HTTPS URL without credentials")
    prompt = build_localized_reference_prompt(locale=locale, translations=translations)
    selected_model = str(model or MODEL).strip()
    if not selected_model:
        raise ValueError("localized image model is required")
    params = {"images": [parsed.geturl()], "size": _size_for(source_bytes), "quality": "medium"}
    checkpoint = ImageCheckpoint(
        checkpoint_dir, kind="localized",
        business_identity=dict(business_identity) if business_identity is not None else {"source_url": parsed.geturl(), "locale": locale},
        request_identity={"renderer": RENDERER, "model": selected_model, "prompt": prompt,
                          "params": params, "source_digest": hashlib.sha256(source_bytes).hexdigest(),
                          "translations": list(translations)},
        model=selected_model, source_identity_complete=True,
    )
    if paid_context is not None:
        from shared_platform.publication_paid_requests import require_paid_context
        require_paid_context(paid_context)
        if (not business_identity or business_identity.get("offer_id") != paid_context.offer_id
                or business_identity.get("locale") != locale or not business_identity.get("brand_id")
                or not business_identity.get("role")):
            raise ValueError("R2 localization requires exact product, brand, role and locale identity")
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
        load_result=result_loader, normalize_image=_png_bytes,
        paid_context=paid_context, paid_purpose="image_translation",
    )
    receipt=dict(result['receipt'])
    if paid_context is not None:
        receipt['paid_request']=paid_context.receipt_binding(paid_context.image_key(checkpoint,{'attempt':receipt['retry_attempt']},'image_translation'))
    return {"image_bytes": result["image_bytes"], "receipt": receipt, "checkpoint_path":str(checkpoint.path)}
