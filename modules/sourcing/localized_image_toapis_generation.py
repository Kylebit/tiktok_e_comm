"""Durable ToAPIs reference-image generation for localized product images."""

from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.parse
from typing import Any, Mapping, Sequence

from PIL import Image

from modules.sourcing.toapis_client import ToAPIsClient, ToAPIsClientError


RENDERER = "toapis-reference-image/v1"
PROVIDER = "toapis-images/v1"
MODEL = "gpt-image-2-official"
_LOCALE_NAMES = {
    "ms-MY": "Malay for Malaysia",
    "th-TH": "Thai for Thailand",
    "vi-VN": "Vietnamese for Vietnam",
    "ru-RU": "Russian for Russia",
    "es-MX": "Spanish for Mexico",
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _png_bytes(raw: bytes) -> bytes:
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError("ToAPIs localized image is empty or too large")
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()
            output = BytesIO()
            image.convert("RGB").save(output, format="PNG", optimize=True)
            return output.getvalue()
    except Exception as error:
        raise ValueError("ToAPIs localized image is invalid") from error


def _size_for(source_bytes: bytes) -> str:
    try:
        with Image.open(BytesIO(source_bytes)) as image:
            ratio = image.width / max(1, image.height)
    except Exception as error:
        raise ValueError("approved source image is invalid") from error
    if ratio >= 1.2:
        return "3:2"
    if ratio <= 0.83:
        return "2:3"
    return "1:1"


def _download_completed_result(
    api: ToAPIsClient, completed: Mapping[str, Any], destination: Path
) -> Path:
    """Retry only the idempotent result download, never the paid generation."""
    last_error: ToAPIsClientError | None = None
    for _ in range(3):
        try:
            return api.download_result(dict(completed), destination)
        except ToAPIsClientError as error:
            last_error = error
    assert last_error is not None
    raise last_error


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
        "Preserve the same product identity, product count, composition, colors, texture, "
        "dimensions, numeric facts, and overall commercial meaning. Minor natural layout "
        "or background changes are acceptable. Do not add logos, watermarks, claims, text, "
        "or objects that are not requested. Render every replacement legibly and exactly, "
        "including numbers and units. Replacement list: "
        + _canonical(pairs)
    )


def generate_localized_reference_image(
    *,
    source_url: str,
    source_bytes: bytes,
    locale: str,
    translations: Sequence[Mapping[str, Any]],
    checkpoint_dir: str | Path,
    client: ToAPIsClient | None = None,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(str(source_url or "").strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("localized image source must be a public HTTPS URL")
    prompt = build_localized_reference_prompt(locale=locale, translations=translations)
    identity = {
        "schema_version": RENDERER,
        "source_url": parsed.geturl(),
        "source_digest": hashlib.sha256(source_bytes).hexdigest(),
        "locale": locale,
        "translations": list(translations),
        "model": MODEL,
    }
    job_digest = hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()
    business_id = f"localized-{job_digest[:40]}"
    root = Path(checkpoint_dir)
    state_path = root / f"{job_digest}.json"
    output_path = root / f"{job_digest}.png"
    state: dict[str, Any] = {}
    if state_path.is_file():
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state = loaded
    if state.get("identity_digest") not in {None, job_digest}:
        raise RuntimeError("localized image generation checkpoint identity drifted")
    api = client or ToAPIsClient()
    if state.get("status") == "FAILED":
        if state.get("task_id") or state.get("failure_kind") == "PROVIDER_FAILED":
            raise RuntimeError(
                "previous ToAPIs localized image generation failed; review before retry"
            )
        try:
            recovered = api.get_generation(business_id)
        except ToAPIsClientError as error:
            detail = str(error)
            if "task_not_exist" not in detail and "HTTP 404" not in detail:
                raise RuntimeError(
                    "cannot reconcile the previous ToAPIs submission safely"
                ) from error
            state = {}
        else:
            recovered_task_id = str(recovered.get("id") or business_id).strip()
            if not recovered_task_id:
                raise RuntimeError("recovered ToAPIs task identity is invalid")
            state = {
                **state,
                "status": "SUBMITTED",
                "task_id": recovered_task_id,
                "client_business_id": business_id,
            }
            _atomic_json(state_path, state)
    if state.get("status") == "COMPLETED" and output_path.is_file():
        image_bytes = _png_bytes(output_path.read_bytes())
        if hashlib.sha256(image_bytes).hexdigest() != state.get("output_digest"):
            raise RuntimeError("localized image generation artifact drifted")
        return {"image_bytes": image_bytes, "receipt": dict(state["receipt"])}

    task_id = str(state.get("task_id") or "")
    try:
        if not task_id:
            created = api.create_generation(
                allow_generation=True,
                model=MODEL,
                prompt=prompt,
                size=_size_for(source_bytes),
                resolution="1k",
                reference_images=[parsed.geturl()],
                client_business_id=business_id,
                n=1,
            )
            task_id = str(created.get("id") or "").strip()
            if not task_id:
                raise RuntimeError("ToAPIs generation did not return a task ID")
            state = {
                "schema_version": "localized-image-toapis-checkpoint/v1",
                "identity_digest": job_digest,
                "status": "SUBMITTED",
                "task_id": task_id,
                "client_business_id": business_id,
            }
            _atomic_json(state_path, state)
        completed = api.wait_for_generation(task_id, timeout=600, poll_interval=3)
        result_rows = ((completed.get("result") or {}).get("data") or [])
        public_url = str(
            (result_rows[0] if result_rows else {}).get("url") or ""
        ).strip()
        if not public_url.startswith("https://"):
            raise RuntimeError(
                "ToAPIs generation did not return a public HTTPS result"
            )
        with tempfile.TemporaryDirectory(prefix="localized-toapis-") as temporary:
            downloaded = _download_completed_result(
                api, completed, Path(temporary) / "result"
            )
            image_bytes = _png_bytes(downloaded.read_bytes())
        output_digest = hashlib.sha256(image_bytes).hexdigest()
        receipt = {
            "status": "COMPLETED",
            "provider": PROVIDER,
            "model": MODEL,
            "task_id": task_id,
            "client_business_id": business_id,
            "request_attempted": True,
            "outcome_unknown": False,
            "external_generation_count": 1,
            "output_digest": f"sha256:{output_digest}",
            "public_url": public_url,
        }
        _atomic_bytes(output_path, image_bytes)
        _atomic_json(
            state_path,
            {
                **state,
                "status": "COMPLETED",
                "output_digest": output_digest,
                "receipt": receipt,
            },
        )
        return {"image_bytes": image_bytes, "receipt": receipt}
    except Exception as error:
        deterministic_provider_failure = str(error).startswith(
            "ToAPIs generation failed:"
        )
        retry_same_task = bool(task_id) and not deterministic_provider_failure
        _atomic_json(
            state_path,
            {
                **state,
                "schema_version": "localized-image-toapis-checkpoint/v1",
                "identity_digest": job_digest,
                "status": "SUBMITTED" if retry_same_task else "FAILED",
                "failure_kind": (
                    None
                    if retry_same_task
                    else (
                        "PROVIDER_FAILED"
                        if deterministic_provider_failure
                        else "SUBMISSION_UNKNOWN"
                    )
                ),
                "task_id": task_id or None,
                "client_business_id": business_id,
            },
        )
        raise
