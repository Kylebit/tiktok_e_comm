from pathlib import Path

import pytest

from modules.sourcing.new_product_server import require_explicit_confirmation
from modules.products.server import _NoRemoteImageRedirects, _validate_remote_image_url


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("key", "action"),
    [
        ("confirm_miaoshou_precollect", "Miaoshou pre-collection"),
        ("confirm_ai_planning", "AI planning"),
        ("confirm_miaoshou_write", "Miaoshou write"),
        ("confirm_tiktok_claim", "TikTok collection-box claim"),
        ("confirm_site_draft_write", "site-draft write"),
    ],
)
@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_external_business_actions_require_literal_json_true(key, action, value):
    with pytest.raises(ValueError, match="explicit"):
        require_explicit_confirmation({key: value}, key, action)
    require_explicit_confirmation({key: True}, key, action)


def test_treasury_routes_and_ui_keep_external_writes_behind_confirmations():
    server = (ROOT / "modules/sourcing/new_product_server.py").read_text(encoding="utf-8")
    html = (ROOT / "web/new_product.html").read_text(encoding="utf-8")

    for key in (
        "confirm_miaoshou_precollect",
        "confirm_ai_planning",
        "confirm_miaoshou_write",
        "confirm_tiktok_claim",
        "confirm_site_draft_write",
    ):
        assert key in server
        assert key in html
    assert "JSON.stringify({url: preview.offer_id, overseas_urls: overseasLines(), precollect: false})" in html
    assert "JSON.stringify({url: preview.offer_id, overseas_urls: overseasLines(), precollect: true})" not in html


def test_release_page_exposes_no_publish_action():
    html = (ROOT / "web/release.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/release.js").read_text(encoding="utf-8")

    assert "本页没有“发布”按钮" in html
    assert "/api/release/dashboard" in script
    assert "/api/release/weekly-preview" in script
    assert "fetch(" in script
    assert "method: \"POST\"" not in script
    assert "renderReleaseFailure" in script
    assert "renderWeeklyFailure" in script
    assert "A cute black line-art dog decal" not in (
        ROOT / "shared_platform/release_control.py"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private.jpg",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/private.jpg",
        "http://user:password@example.com/image.jpg",
        "http://example.com:8765/image.jpg",
    ],
)
def test_image_proxy_rejects_local_network_and_credential_targets(url):
    with pytest.raises(ValueError):
        _validate_remote_image_url(url)


def test_image_proxy_never_follows_redirects_to_a_second_unvalidated_host():
    handler = _NoRemoteImageRedirects()
    assert (
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {"Location": "http://127.0.0.1/private.jpg"},
            "http://127.0.0.1/private.jpg",
        )
        is None
    )
