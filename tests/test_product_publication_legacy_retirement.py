from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_JS = ROOT / "web" / "static" / "product_workspace.js"
RETIREMENT_DOC = ROOT / "docs" / "release_v2" / "LEGACY_PUBLICATION_RETIREMENT.md"


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(",
        source,
    )
    assert match is not None, f"missing function {name}"
    body_start = source.index("{", match.end())
    depth = 0
    for index in range(body_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[body_start : index + 1]
    raise AssertionError(f"unterminated function {name}")


def test_product_workspace_has_no_legacy_publication_write_caller() -> None:
    """The browser must never POST the retired publish-all endpoint."""

    source = WORKSPACE_JS.read_text(encoding="utf-8")
    exact_legacy_route = re.compile(
        r"(?P<quote>['\"])/api/product-workspace/publish(?P=quote)"
    )

    assert exact_legacy_route.search(source) is None
    assert "resumeExactZeroWriteFailures" not in source


def test_three_platform_buttons_only_enter_the_report_runner_contract() -> None:
    """Explicit platform actions cannot re-enter the old job/status machine."""

    source = WORKSPACE_JS.read_text(encoding="utf-8")
    expected = {
        "runTiktokReleaseAction": "/api/product-workspace/publish-tiktok",
        "runShopeeGlobalReleaseAction": (
            "/api/product-workspace/publish-shopee-global"
        ),
        "runOzonReleaseAction": "/api/product-workspace/publish-ozon",
    }
    forbidden = (
        "oneClickExecution",
        "/api/product-workspace/publish-status",
        "payload.job",
        "confirm_publish",
        "SUCCEEDED_MANUAL_REVIEW",
    )

    for function_name, endpoint in expected.items():
        body = _function_body(source, function_name)
        assert "publishPlatformBatch" in body
        if function_name != "runTiktokReleaseAction":
            assert endpoint in body
        for token in forbidden:
            assert token not in body

    publisher = _function_body(source, "publishPlatformBatch")
    assert 'payload.schema_version !== "product-publication-start/v1"' in publisher
    assert "/api/product-workspace/publication-report?" not in publisher
    assert "payload.job" not in publisher
    assert "payload.accepted" not in publisher


def test_superseded_release_specs_are_explicitly_deprecated() -> None:
    """Historical V2 design material cannot be mistaken for implementation authority."""

    retirement = RETIREMENT_DOC.read_text(encoding="utf-8")
    assert "2026-09-01" in retirement
    assert "`POST /api/product-workspace/publish`" in retirement
    assert "禁止新增调用者" in retirement
    assert "publish-approved-product" in retirement

    superseded = (
        ROOT / "docs" / "CONVERGENCE_STAGE0_BASELINE.md",
        ROOT / "docs" / "release_v2" / "README.md",
        ROOT / "docs" / "release_v2" / "ARCHITECTURE.md",
        ROOT / "docs" / "release_v2" / "DETAILED_DESIGN.md",
        ROOT / "docs" / "release_v2" / "VISUAL_TEST_PLAN.md",
    )
    for path in superseded:
        prefix = path.read_text(encoding="utf-8")[:600]
        assert "DEPRECATED" in prefix, path
        assert "LEGACY_PUBLICATION_RETIREMENT.md" in prefix, path
