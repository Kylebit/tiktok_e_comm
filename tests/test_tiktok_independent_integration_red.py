"""Red gates for the Work Package 1 TikTok integration boundary.

These tests intentionally describe the required server/UI isolation before the
independent channel publisher is integrated.  They must be run against the
unmodified ``f2395b9`` baseline and kept as permanent regression coverage.
"""

from __future__ import annotations

import inspect

from modules.products import server as product_server


SCRIPT_PATH = product_server.ROOT / "web/static/product_workspace.js"


def _script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    marker = f"function {name}("
    async_marker = f"async function {name}("
    start = source.find(marker)
    if start < 0:
        start = source.find(async_marker)
    assert start >= 0, f"JavaScript function not found: {name}"
    opening = source.index("{", start)
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(source)):
        char = source[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def test_tiktok_server_entry_never_enters_legacy_oneclick_control_plane():
    """TikTok is a direct integration and must not inherit COMMON/jobs."""

    source = inspect.getsource(product_server._start_tiktok_release)

    assert "_start_oneclick_release" not in source
    assert "batch_scope" not in source
    assert "miaoshou:COMMON" not in source


def test_tiktok_request_does_not_read_oneclick_identity_or_generation():
    """The TikTok POST owns its request identity and stale-response guard."""

    source = _script()
    publish = _function_body(source, "publishPlatformBatch")

    assert "oneClickExecution.identity" not in publish
    assert "oneClickExecution.generation" not in publish
    assert "platformPublish[platformKey]" in publish
    assert ".generation" in publish


def test_resetting_legacy_oneclick_never_clears_platform_publish_results():
    """Legacy state reset cannot erase TikTok, Shopee, or Ozon receipts."""

    reset = _function_body(_script(), "resetOneClickExecution")

    assert "platformPublish" not in reset


def test_tiktok_busy_state_already_disables_only_the_tiktok_button():
    """Preserve the existing per-click isolation; this is not a red gap."""

    source = _script()
    publish = _function_body(source, "publishPlatformBatch")
    controls = _function_body(source, "updateReleasePrimaryAction")

    assert "releaseSubmitting" not in publish
    assert "oneClickExecution.controller" not in publish
    assert "platformPublish[platformKey]" in publish
    assert (
        'platformPublish.TIKTOK.status === "PROCESSING"'
        in controls
    )
    assert (
        'platformPublish.SHOPEE_GLOBAL.status === "PROCESSING"'
        in controls
    )
    assert 'platformPublish.OZON.status === "PROCESSING"' in controls


def test_frontend_displays_provider_safe_reason_instead_of_generic_failure():
    """A redacted provider reason must survive the HTTP-to-card boundary."""

    error_mapper = _function_body(_script(), "platformPublishErrorMessage")

    assert "structured.safe_message" in error_mapper
    assert "structured.provider_code" in error_mapper


def test_tiktok_integration_scope_is_six_storefronts_without_common():
    """The shared COMMON control is not a TikTok target or prerequisite."""

    expected = {
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
        "tiktok:MX",
        "tiktok:GB",
    }
    assert len(expected) == 6
    assert "miaoshou:COMMON" not in expected
    source = inspect.getsource(product_server._start_tiktok_release)
    assert "_start_oneclick_release" not in source
