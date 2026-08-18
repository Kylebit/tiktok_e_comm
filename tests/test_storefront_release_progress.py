from __future__ import annotations

from pathlib import Path

from modules.products import server as product_server


def _failed_target(
    label: str,
    *,
    error: str,
    writes: list[str],
    save_accepted: bool,
) -> dict:
    return {
        "target_label": label,
        "status": "FAILED",
        "attempts": 1,
        "external_id": "draft:shop",
        "error": error,
        "latest_failure_evidence": {
            "evidence": {
                "verified": False,
                "save_accepted": save_accepted,
                "external_writes_performed": writes,
            }
        },
    }


def test_storefront_progress_never_counts_common_or_drafts_as_published():
    draft_writes = [
        "miaoshou:tiktok_detail:create",
        "miaoshou:tiktok_shop:claim",
        "miaoshou:tiktok_detail:update",
    ]
    run = {
        "targets": [
            {
                "target_label": "miaoshou:COMMON",
                "status": "SUCCEEDED",
                "attempts": 1,
            },
            *[
                _failed_target(
                    label,
                    error=(
                        f"Miaoshou {label} draft readback did not verify: "
                        "english_variants"
                    ),
                    writes=draft_writes,
                    save_accepted=True,
                )
                for label in (
                    "tiktok:LH_PH",
                    "tiktok:LH_MY",
                    "tiktok:LH_TH",
                    "tiktok:LH_VN",
                    "tiktok:MX",
                )
            ],
            _failed_target(
                "tiktok:GB",
                error="产品数据发生变动，请重新打开弹窗编辑",
                writes=draft_writes,
                save_accepted=False,
            ),
            *[
                {
                    "target_label": label,
                    "status": "PENDING",
                    "attempts": 0,
                }
                for label in (
                    "shopee:PH",
                    "shopee:MY",
                    "shopee:TH",
                    "shopee:VN",
                    "ozon:RU",
                )
            ],
        ]
    }

    progress = product_server._store_release_progress(run)

    assert progress == {
        "schema_version": "storefront-release-progress/v1",
        "storefront_total": 11,
        "published_verified": 0,
        "submitted_waiting_verification": 0,
        "draft_waiting_verification": 5,
        "draft_version_conflict": 1,
        "not_started": 5,
        "running": 0,
        "other_blocked": 0,
    }


def test_storefront_progress_requires_verified_or_manual_terminal_success():
    run = {
        "targets": [
            {
                "target_label": "miaoshou:COMMON",
                "status": "SUCCEEDED",
                "attempts": 1,
            },
            {
                "target_label": "tiktok:LH_PH",
                "status": "SUCCEEDED",
                "attempts": 1,
            },
            {
                "target_label": "tiktok:MX",
                "status": "MANUALLY_VERIFIED",
                "attempts": 1,
            },
            {
                "target_label": "tiktok:GB",
                "status": "SUBMITTED_UNVERIFIED",
                "attempts": 1,
            },
        ]
    }

    progress = product_server._store_release_progress(run)

    assert progress["storefront_total"] == 3
    assert progress["published_verified"] == 2
    assert progress["submitted_waiting_verification"] == 1


def test_storefront_progress_does_not_call_publish_dispatched_a_draft():
    target = _failed_target(
        "tiktok:MX",
        error="publish outcome unknown",
        writes=[
            "miaoshou:tiktok_detail:update",
            "miaoshou:tiktok_publish:submission",
        ],
        save_accepted=True,
    )
    target["latest_failure_evidence"]["evidence"][
        "publish_dispatched"
    ] = True

    progress = product_server._store_release_progress(
        {"targets": [target]}
    )

    assert progress["draft_waiting_verification"] == 0
    assert progress["draft_version_conflict"] == 0
    assert progress["other_blocked"] == 1


def test_product_workspace_copy_distinguishes_draft_from_storefront_success():
    source = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "static"
        / "product_workspace.js"
    ).read_text(encoding="utf-8")

    assert "公共草稿已核验 · 不计入店铺发布" in source
    assert "妙手草稿已保存 · 尚未提交店铺" in source
    assert "妙手草稿版本冲突 · 尚未提交店铺" in source
    assert "个店铺发布完成" in source
