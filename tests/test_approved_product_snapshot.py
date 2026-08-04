import pytest

from shared_platform.product_snapshot import (
    project_approved_tiktok_category_decisions,
)


TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
)


def test_immutable_product_category_projects_exact_six_tiktok_decisions():
    decisions = project_approved_tiktok_category_decisions(
        {"name": "贴饰 > 墙贴", "id": "", "confidence": "approved"},
        targets=TARGETS,
    )

    assert list(decisions) == list(TARGETS)
    assert {row["category_id"] for row in decisions.values()} == {"600338"}
    assert decisions["tiktok:LH_PH"]["evidence_digest"] == (
        "01da90466a74143b065cc2a9a90fc52d51bc516316da00361181915b3047463e"
    )
    assert decisions["tiktok:GB"]["evidence_digest"] == (
        "24eb8b5d3f5dedeac07212c600140510f408e5479e9e1b80251f4e1af36a1486"
    )


@pytest.mark.parametrize(
    "category,targets",
    [
        ({"name": "unsupported"}, TARGETS),
        ({"name": "贴饰 > 墙贴"}, (*TARGETS, "tiktok:UNKNOWN")),
        ({"name": "贴饰 > 墙贴"}, (*TARGETS[:-1], TARGETS[0])),
    ],
)
def test_category_projection_fails_closed_for_unsupported_snapshot(
    category,
    targets,
):
    with pytest.raises(ValueError):
        project_approved_tiktok_category_decisions(
            category,
            targets=targets,
        )
