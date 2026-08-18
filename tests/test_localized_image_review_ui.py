from pathlib import Path

from modules.sourcing import new_product_server


ROOT = Path(__file__).resolve().parents[1]


def test_standalone_review_console_is_separate_from_ai_studio():
    html = (ROOT / "web" / "localized_image_review.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )
    product_server = (ROOT / "modules" / "products" / "server.py").read_text(
        encoding="utf-8"
    )

    assert "多语言图片审核台" in html
    assert 'id="paidGenerationConfirm"' in html
    assert 'id="generateLocalizedImages"' in html
    assert 'id="approveLocalizedImages"' in html
    assert "content-package/localized-image-review" in script
    assert '"/localized-image-review"' in product_server
    assert "localized_image_review.html" in product_server


def test_review_console_does_not_write_marketplaces_or_mutate_product_center():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )
    html = (ROOT / "web" / "localized_image_review.html").read_text(encoding="utf-8")

    forbidden = ("publish-tiktok", "publish-shopee", "publish-ozon", "miaoshou-images/commit")
    assert all(value not in script for value in forbidden)
    assert "独立审核，不修改当前发布计划" in html


def test_review_http_actions_are_registered_on_both_local_services():
    treasury = (ROOT / "modules" / "sourcing" / "new_product_server.py").read_text(
        encoding="utf-8"
    )
    orbit = (ROOT / "modules" / "products" / "server.py").read_text(
        encoding="utf-8"
    )

    for action in ("initialize", "generate", "decision", "approve"):
        endpoint = f"content-package/localized-image-review/{action}"
        assert endpoint in treasury
        assert endpoint in orbit
    assert "paid ToAPIs localized image generation" in treasury


def test_initial_project_button_recovers_after_the_first_read():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )

    assert '$("initializeReview").disabled = busy || Boolean(state?.initialized);' in script


def test_lightbox_close_button_is_not_left_disabled_after_loading():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )

    assert '$("closeLightbox").disabled = false;' in script


def test_generation_error_survives_the_final_action_rerender():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )

    assert "let failureMessage = \"\";" in script
    assert "if (failureMessage) status(failureMessage, true);" in script


def test_paid_generation_runs_as_a_background_job_and_ui_polls_progress():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )
    treasury = (ROOT / "modules" / "sourcing" / "new_product_server.py").read_text(
        encoding="utf-8"
    )

    assert "_start_localized_image_review_generation_job" in treasury
    assert "threading.Thread" in treasury
    assert "generation_job" in script
    assert "scheduleGenerationPoll" in script
    assert "clearGenerationPoll" in script


def test_background_generation_start_is_immediate_and_deduplicated(monkeypatch):
    jobs = []
    threads = []

    class FakeModule:
        @staticmethod
        def localized_image_review_summary(_raw):
            return {
                "offer_id": "offer-1",
                "review": {
                    "revision": 3,
                    "tasks": [
                        {
                            "task_id": "task-1",
                            "status": "PENDING_GENERATION",
                            "source_url": "https://assets.example/image.png",
                        }
                    ],
                },
                "generation_job": jobs[-1] if jobs else {"status": "IDLE"},
            }

        @staticmethod
        def save_localized_image_review_generation_job(_offer_id, payload):
            jobs.append(dict(payload))
            return dict(payload)

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.alive = False
            threads.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(new_product_server.threading, "Thread", FakeThread)
    new_product_server._LOCALIZED_IMAGE_REVIEW_THREADS.clear()
    try:
        first = new_product_server._start_localized_image_review_generation_job(
            FakeModule, raw="offer-1", expected_revision=3
        )
        second = new_product_server._start_localized_image_review_generation_job(
            FakeModule, raw="offer-1", expected_revision=3
        )
    finally:
        new_product_server._LOCALIZED_IMAGE_REVIEW_THREADS.clear()

    assert len(threads) == 1
    assert threads[0].kwargs["daemon"] is True
    assert first["generation_job"]["status"] == "QUEUED"
    assert second["generation_job"]["job_id"] == first["generation_job"]["job_id"]
