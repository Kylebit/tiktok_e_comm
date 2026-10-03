from pathlib import Path

import pytest

from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import publication_r2_review as review


def test_r3_r2_review_root_pin_selects_registered_project(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    registry = tmp_path / "review-registry"
    registration = registry / "data" / "r2_candidate_reviews" / "3956742887"
    registration.mkdir(parents=True)
    (registration / "registration.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bridge, "REPORTS_ROOT", candidate / "reports" / "product-preparation")
    monkeypatch.setenv(review.R2_REVIEW_RUNTIME_ROOT_ENV, str(registry.resolve()))

    path = bridge._report_path("3956742887", "round1-approved-snapshot.json")

    assert path == (
        registration
        / "project"
        / "reports"
        / "product-preparation"
        / "3956742887"
        / "round1-approved-snapshot.json"
    )


def test_r3_r2_review_root_pin_rejects_relative_path(tmp_path, monkeypatch):
    monkeypatch.setenv(review.R2_REVIEW_RUNTIME_ROOT_ENV, "relative/review-registry")

    with pytest.raises(ValueError, match="R2_REVIEW_RUNTIME_ROOT_NOT_ABSOLUTE"):
        review.review_runtime_root(tmp_path)
