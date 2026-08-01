from pathlib import Path

from shared_platform.collectbox_action import (
    IMPORTED,
    RECONCILIATION_REQUIRED,
    SUCCEEDED,
    CollectBoxActionStore,
    CollectBoxPlatformResult,
    common_collectbox_identity_digest,
)


ROOT = Path(__file__).resolve().parents[1]


def _approved_multicountry_plan():
    return {
        "plan_id": "omnichannel:multicountry-red",
        "product_id": "3846511157",
        "payload_digest": "a" * 64,
        "payload": {"product_revision": 15},
        "targets": [
            "tiktok:LH_PH",
            "tiktok:LH_MY",
            "tiktok:LH_TH",
            "tiktok:LH_VN",
            "tiktok:MX",
            "tiktok:GB",
            "shopee:PH",
            "shopee:MY",
            "shopee:TH",
            "shopee:VN",
            "ozon:RU",
        ],
        "status": "APPROVED",
        "approval": {"status": "APPROVED", "approved_by": "Kyle"},
    }


def test_collectbox_preview_exposes_each_selected_tiktok_country(tmp_path):
    """Red regression: platform aggregation must not hide five selected shops."""

    plan = _approved_multicountry_plan()
    projection = CollectBoxActionStore(tmp_path / "collectbox.db").preview(
        plan=plan,
        common_collectbox_identity_digest=common_collectbox_identity_digest(
            plan["plan_id"],
            plan["product_id"],
        ),
    )
    tiktok = next(
        row
        for row in projection["action"]["platforms"]
        if row["platform"] == "TIKTOK"
    )

    assert tiktok["targets"] == [
        {"target_label": "tiktok:LH_PH", "status": "PENDING"},
        {"target_label": "tiktok:LH_MY", "status": "PENDING"},
        {"target_label": "tiktok:LH_TH", "status": "PENDING"},
        {"target_label": "tiktok:LH_VN", "status": "PENDING"},
        {"target_label": "tiktok:MX", "status": "PENDING"},
        {"target_label": "tiktok:GB", "status": "PENDING"},
    ]


def test_collectbox_reconciliation_copy_allows_a_fresh_batch():
    """Red regression: an old batch is terminal, but a new UUID batch is allowed."""

    script = (ROOT / "web/static/product_workspace.js").read_text(
        encoding="utf-8"
    )

    assert "本批次结果待确认；可重新导入并创建新批次" in script
    assert "结果待人工确认，不能重试" not in script
    assert "重新导入 TikTok / Shopee 妙手采集箱" in script


def test_collectbox_durable_projection_preserves_exact_target_outcomes(tmp_path):
    plan = _approved_multicountry_plan()
    selected_tiktok = tuple(
        target for target in plan["targets"] if target.startswith("tiktok:")
    )

    def adapter(request):
        selected = tuple(
            target
            for target in request.approved_targets
            if target.startswith(request.platform.lower() + ":")
        )
        if request.platform == "TIKTOK":
            return CollectBoxPlatformResult(
                status=RECONCILIATION_REQUIRED,
                external_writes=(
                    "miaoshou:collectbox:claim:tiktok",
                    "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_PH",
                ),
                external_write_count=None,
                target_statuses=(
                    ("tiktok:LH_PH", RECONCILIATION_REQUIRED),
                    *((target, SUCCEEDED) for target in selected_tiktok[1:]),
                ),
                error_category="UNKNOWN",
                error_code="first_target_update_unknown",
                error_detail="first target update outcome is unknown",
            )
        return CollectBoxPlatformResult(
            status=SUCCEEDED,
            outcome=IMPORTED,
            platform_detail_id="71002",
            external_writes=("miaoshou:collectbox:claim:shopee",),
            external_write_count=1,
            target_statuses=tuple((target, SUCCEEDED) for target in selected),
        )

    projection = CollectBoxActionStore(tmp_path / "collectbox.db").start(
        plan=plan,
        common_collect_box_detail_id=plan["product_id"],
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    tiktok = next(
        row
        for row in projection["action"]["platforms"]
        if row["platform"] == "TIKTOK"
    )

    assert tiktok["targets"] == [
        {
            "target_label": "tiktok:LH_PH",
            "status": RECONCILIATION_REQUIRED,
        },
        *(
            {"target_label": target, "status": SUCCEEDED}
            for target in selected_tiktok[1:]
        ),
    ]
    assert {
        row["target_label"] for row in tiktok["targets"]
    } == set(selected_tiktok)
    assert not any(
        row["target_label"].startswith("tiktok:HB_")
        for row in tiktok["targets"]
    )
