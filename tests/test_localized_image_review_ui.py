from pathlib import Path

from modules.sourcing import new_product_server


ROOT = Path(__file__).resolve().parents[1]


def test_localized_result_console_is_separate_from_ai_studio():
    html = (ROOT / "web" / "localized_image_review.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )
    product_server = (ROOT / "modules" / "products" / "server.py").read_text(
        encoding="utf-8"
    )

    assert "多语言图片执行结果" in html
    assert 'data-human-review-surface="none"' in html
    assert 'id="refreshReview"' in html
    assert 'id="paidGenerationConfirm"' not in html
    assert 'id="generateLocalizedImages"' not in html
    assert 'id="approveLocalizedImages"' not in html
    assert "content-package/localized-image-review" in script
    assert '"/localized-image-review"' in product_server
    assert "localized_image_review.html" in product_server


def test_result_console_does_not_write_marketplaces_or_mutate_product_center():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )
    html = (ROOT / "web" / "localized_image_review.html").read_text(encoding="utf-8")

    forbidden = ("publish-tiktok", "publish-shopee", "publish-ozon", "miaoshou-images/commit")
    assert all(value not in script for value in forbidden)
    assert "所有人工审核统一在商品发布中心完成" in html


def test_result_page_is_read_only_and_uses_one_refresh_action():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )
    html = (ROOT / "web" / "localized_image_review.html").read_text(
        encoding="utf-8"
    )

    assert 'id="refreshReview"' in html
    assert 'id="passVisibleImages"' not in html
    assert 'id="approveLocalizedImages"' not in html
    assert 'id="paidGenerationConfirm"' not in html
    assert 'id="generateLocalizedImages"' not in html
    assert 'request("/approve"' not in script
    assert 'request("/decision"' not in script
    assert 'request("/generate"' not in script
    assert "refreshReview" in script


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


def test_refresh_button_recovers_after_the_first_read():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )

    assert '$("refreshReview").disabled = busy;' in script
    assert '$("refreshReview").addEventListener("click", load);' in script


def test_lightbox_close_button_is_not_left_disabled_after_loading():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )

    assert '$("closeLightbox").disabled = false;' in script


def test_read_error_remains_visible_without_a_write_retry():
    script = (ROOT / "web" / "static" / "localized_image_review.js").read_text(
        encoding="utf-8"
    )

    assert 'status(`失败：${error.message || error}`, true);' in script
    assert "method: \"POST\"" not in script


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
