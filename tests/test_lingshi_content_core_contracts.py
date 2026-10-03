"""Offline fixtures for the retained B00-B3A content execution contracts."""

from copy import deepcopy
from io import BytesIO
import json

from PIL import Image
import pytest

from domains.content_operations.content_package_adapter import build_content_package_handoff
from modules.sourcing import brand_image_lingshi_generation as brand
from modules.sourcing import localized_image_lingshi_generation as localized
from modules.sourcing.lingshi_client import LingshiClient, LingshiClientError, build_generation_payload


def _png():
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FixtureClient:
    def __init__(self, *, submission=None, task=None):
        self.submission = submission or {"code": 200, "data": {"task_id": 42}}
        self.task = task or {"is_final": True, "result_url": "https://assets.example/result.png"}
        self.created = []
        self.queried = []

    def create_media_generation(self, **kwargs):
        self.created.append(kwargs)
        if isinstance(self.submission, Exception):
            raise self.submission
        return deepcopy(self.submission)

    def get_media_task(self, task_id):
        self.queried.append(task_id)
        if isinstance(self.task, Exception):
            raise self.task
        return deepcopy(self.task)


def _invoke(kind, tmp_path, client, monkeypatch, **changes):
    common = {"checkpoint_dir": tmp_path, "client": client, **changes}
    if kind == "brand":
        monkeypatch.setattr(brand, "_download_result", lambda _url: _png())
        return brand.generate_brand_image(
            offer_id="fixture-product", brand_id="fixture-brand", brand_label="Fixture",
            positioning="plain", role="cover", brief="verified object",
            product_identity="verified object", source_urls=["https://assets.example/source.png"],
            **common,
        )
    return localized.generate_localized_reference_image(
        source_url="https://assets.example/source.png", source_bytes=_png(), locale="th-TH",
        translations=[{"source_text": "CLEAN", "translated_text": "สะอาด"}],
        result_loader=lambda _url: _png(), **common,
    )


def _state(tmp_path):
    paths = list(tmp_path.glob("lingshi-*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", ["brand", "localized"])
def test_unknown_outcome_blocks_same_request_retry(kind, tmp_path, monkeypatch):
    client = FixtureClient(submission=LingshiClientError("fixture transport timeout"))
    with pytest.raises(LingshiClientError):
        _invoke(kind, tmp_path, client, monkeypatch)
    assert _state(tmp_path)["status"] == "SUBMISSION_UNKNOWN"
    with pytest.raises(RuntimeError, match="unknown.*reconcile"):
        _invoke(kind, tmp_path, client, monkeypatch)
    assert len(client.created) == 1
    assert client.queried == []


@pytest.mark.parametrize("kind", ["brand", "localized"])
def test_failure_after_task_creation_is_durable_and_cannot_be_retried(kind, tmp_path, monkeypatch):
    client = FixtureClient(task={"is_final": True, "error": "fixture rejection"})
    with pytest.raises(RuntimeError, match="generation failed"):
        _invoke(kind, tmp_path, client, monkeypatch)
    state = _state(tmp_path)
    assert (state["status"], state["failure_kind"], state["task_id"]) == ("FAILED", "PROVIDER_FAILED", 42)
    with pytest.raises(RuntimeError, match="review before retry"):
        _invoke(kind, tmp_path, client, monkeypatch)
    assert len(client.created) == 1


def test_localized_rejection_before_task_is_separate_from_created_task_failure(tmp_path, monkeypatch):
    client = FixtureClient(submission={"code": 422, "msg": "fixture invalid request"})
    with pytest.raises(localized.LingshiMediaSubmissionRejected):
        _invoke("localized", tmp_path, client, monkeypatch)
    state = _state(tmp_path)
    assert state["status"] == "REJECTED_BEFORE_TASK"
    assert state["failure_kind"] == "PROVIDER_REJECTED_BEFORE_TASK"
    assert state["task_id"] is None
    assert client.queried == []


def test_brand_http_error_text_requires_reconciliation_before_another_submission(tmp_path, monkeypatch):
    client = FixtureClient(submission=LingshiClientError("Lingshi HTTP 400"))
    with pytest.raises(LingshiClientError):
        _invoke("brand", tmp_path, client, monkeypatch)
    state = _state(tmp_path)
    assert (state["status"], state["failure_kind"], state["task_id"]) == ("SUBMISSION_UNKNOWN", "OUTCOME_REQUIRES_RECONCILIATION", None)
    assert client.queried == []
    with pytest.raises(RuntimeError, match="reconcile"):
        _invoke("brand", tmp_path, client, monkeypatch)
    assert len(client.created) == 1


@pytest.mark.parametrize("kind", ["brand", "localized"])
def test_submitted_task_resumes_without_another_creation(kind, tmp_path, monkeypatch):
    client = FixtureClient(task=TimeoutError("fixture poll timeout"))
    with pytest.raises(TimeoutError):
        _invoke(kind, tmp_path, client, monkeypatch)
    assert (_state(tmp_path)["status"], _state(tmp_path)["task_id"]) == ("SUBMITTED", 42)
    client.task = {"is_final": True, "result_url": "https://assets.example/result.png"}
    result = _invoke(kind, tmp_path, client, monkeypatch)
    assert result["receipt"]["task_id"] == 42
    assert len(client.created) == 1


@pytest.mark.parametrize("kind", ["brand", "localized"])
def test_explicit_model_is_kept_in_payload_receipt_and_checkpoint_identity(kind, tmp_path, monkeypatch):
    client = FixtureClient()
    first = _invoke(kind, tmp_path, client, monkeypatch, model="fixture-model-a")
    second = _invoke(kind, tmp_path, client, monkeypatch, model="fixture-model-b")
    assert [row["model"] for row in client.created] == ["fixture-model-a", "fixture-model-b"]
    assert first["receipt"]["model"] == "fixture-model-a"
    assert second["receipt"]["model"] == "fixture-model-b"
    assert first["receipt"]["client_business_id"] != second["receipt"]["client_business_id"]


def test_preview_and_payload_keep_reference_order_without_transport(monkeypatch):
    class NoTransport:
        trust_env = False
        def request(self, *args, **kwargs):
            pytest.fail("preview attempted transport")
    client = LingshiClient(api_key="fixture-key", session=NoTransport())
    references = ["https://assets.example/identity.png", "https://assets.example/composition.png"]
    payload = build_generation_payload(prompt=" verified object ", model="fixture-model", reference_images=references)
    preview = client.preview_media_generation(**payload)
    assert preview["mode"] == "preview_only_no_network"
    assert preview["payload"]["params"]["images"] == references
    assert preview["payload"]["params"]["size"] == "2048x2048"
    assert preview["external_upload_required"] is True
    assert "fixture-key" not in json.dumps(preview)


@pytest.mark.parametrize("n", [0, 2, True, 1.5, "1"])
def test_payload_rejects_non_single_integer_output(n):
    with pytest.raises(ValueError, match="one output"):
        build_generation_payload(prompt="product", model="fixture", reference_images=["https://assets.example/source.png"], n=n)


@pytest.mark.parametrize("references", [
    [], ["https://"], ["http://assets.example/source.png"],
    ["https://assets.example/source.png", "data:image/png;base64,fixture"],
    ["https://user:password@assets.example/source.png"],
    ["https://assets.example/source.png"] * 15,
])
def test_payload_rejects_invalid_reference_set_without_silently_dropping_identity(references):
    with pytest.raises(ValueError, match="HTTPS|1-14"):
        build_generation_payload(prompt="product", model="fixture", reference_images=references)


def test_payload_requires_an_explicit_nonempty_model():
    with pytest.raises(ValueError, match="model"):
        build_generation_payload(prompt="product", model=" ", reference_images=["https://assets.example/source.png"])


@pytest.mark.parametrize("decision", ["pending", "rework", "rejected", "superseded", ""])
def test_nonapproved_generated_assets_never_enter_content_package(decision):
    handoff = build_content_package_handoff(
        product_id="fixture-product", suite_plan={"suite": {"items": [{"id": "cover"}]}},
        asset_decisions={"cover_r1": {"decision": decision}},
        generation_audits={"cover_r1": {"shot_id": "cover", "download_verified": True,
            "final_response": {"result": {"data": [{"url": "https://assets.example/result.png"}]}}}},
    )
    assert handoff.content_package.image_urls == ()
    assert handoff.asset_lineage == ()
    assert handoff.content_package.approval.status == "pending"


def test_localized_prompt_binds_exact_replacements_and_residual_source_text():
    prompt = localized.build_localized_reference_prompt(
        locale="th-TH", translations=[{"source_text": "CLEAN", "translated_text": "สะอาด"}],
    )
    assert '"source":"CLEAN"' in prompt
    assert '"replacement":"สะอาด"' in prompt
    assert "no listed English source phrase may remain" in prompt
