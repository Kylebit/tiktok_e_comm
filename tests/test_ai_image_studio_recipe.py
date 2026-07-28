from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web/ai_image_studio.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")


def test_wall_decal_recipe_exposes_only_supported_operator_counts():
    for shot_type in ("scene", "selling_point", "size_card"):
        assert f'data-recipe-type="{shot_type}"' in HTML

    assert 'data-recipe-type="white_bg"' not in HTML
    assert 'data-recipe-type="macro_detail"' not in HTML
    assert 'id="sizeDimensions"' in HTML
    assert 'id="sizeConfirmed"' in HTML
    assert "白底主图和微距细节不允许由 AI 生成" in HTML


def test_empty_saved_recipe_falls_back_to_selected_storyboard_counts():
    assert "function deriveRecipeCounts(" in SCRIPT
    assert "content.suite_customization?.type_counts" in SCRIPT
    assert "content.suite?.items || []" in SCRIPT
    assert "item.selected !== false" in SCRIPT
    assert "derivedCounts[item.type] += 1" in SCRIPT
    assert "hasSavedCounts ? savedCounts : derivedCounts" in SCRIPT


def test_recipe_is_persisted_and_validated_before_ai_planning():
    assert "suite_customization: collectRecipeFromDom()" in SCRIPT
    assert "请至少选择一张场景图、卖点图或尺寸图" in SCRIPT
    assert "尺寸图需要填写来源已确认的尺寸，并勾选人工确认" in SCRIPT

    planning_function = SCRIPT.split("async function requestAiPlan()", 1)[1].split(
        "async function prepareGeneration()", 1
    )[0]
    assert planning_function.index("validateRecipe()") < planning_function.index("confirm(")
    assert planning_function.index("saveContentReview({ quiet: true })") < planning_function.index(
        'post("content-package/vision-proposal"'
    )
    assert "confirm_ai_planning: true" in planning_function


def test_saved_identity_references_are_restored_from_summary_contract():
    assert "content_package?.source_snapshot || {}" in SCRIPT
    assert "sourceSnapshot.identity_reference_urls" in SCRIPT
    assert "sourceSnapshot.primary_identity_image" in SCRIPT
    assert 'post("content-package/review"' in SCRIPT
    assert "expected_revision: preview?.revision" in SCRIPT
    assert "planningErrorMessage(error)" in SCRIPT


def test_missing_local_package_blocks_ai_with_actionable_local_feedback():
    planning_function = SCRIPT.split("async function requestAiPlan()", 1)[1].split(
        "async function prepareGeneration()", 1
    )[0]
    assert "!preview?.content_package?.package_found" in planning_function
    assert 'action: "prepare-package"' in planning_function
    assert "本次没有调用 AI，也没有产生生图费用" in planning_function
    assert "runPlanningProgressAction" in SCRIPT
    assert 'action === "prepare-package"' in SCRIPT
    assert "await preparePackage()" in SCRIPT


def test_proxy_tls_disconnect_is_explained_without_automatic_retry():
    assert "UNEXPECTED_EOF_WHILE_READING" in SCRIPT
    assert "本次配方和身份参考已经保存在本地" in SCRIPT
    assert "没有创建图片生成任务" in SCRIPT
    assert "系统不会自动重试" in SCRIPT


def test_source_remove_is_persisted_immediately_and_rolls_back_on_failure():
    source_render = SCRIPT.split("function renderSources()", 1)[1].split(
        "function renderGenerated()", 1
    )[0]
    assert 'button.addEventListener("click", async (event)' in source_render
    assert "await saveSourceReview({" in source_render
    assert "已删除并保存到本地" in source_render
    assert "row.action = previousAction" in source_render
    assert "if (sourceReviewSubmitting) return" in source_render


def test_completed_paid_batch_cannot_be_started_again_from_the_ui():
    assert (
        '["completed_waiting_human_review", "completed_with_errors"].includes(generation.status)'
        in SCRIPT
    )
    assert "!total || running || completed" in SCRIPT


def test_content_strategy_selector_defaults_to_ai_and_persists_explicit_choice():
    assert 'name="contentStrategy"' in HTML
    assert 'value="source_only"' in HTML
    assert 'value="ai_assisted" checked' in HTML
    assert "content_strategy: currentContentStrategy()" in SCRIPT
    assert '|| "ai_assisted"' in SCRIPT


def test_source_only_disables_paid_ai_actions_and_excludes_generated_images():
    assert 'id="sourceOnlyGenerationNotice"' in HTML
    assert "const generatedItems = (sourceOnlyActive() ? [] : generatedCurrentRows())" in SCRIPT
    assert '$("#generationRecipe").hidden = sourceOnly' in SCRIPT
    assert '$("#storyboardGrid").hidden = sourceOnly' in SCRIPT
    assert '$("#generated").hidden = sourceOnly' in SCRIPT
    assert '$(".approval-strip").hidden = sourceOnly' in SCRIPT
    assert 'if (sourceOnlyActive()) return;' in SCRIPT
    for message in (
        "AI 分镜已禁用",
        "生图预检已禁用",
        "付费生图已禁用",
    ):
        assert message in SCRIPT


def test_source_only_is_a_two_step_select_order_and_atomic_save_flow():
    assert '["选择来源图", sourceTotal > 0' in SCRIPT
    assert '["排序并保存", sourceOnlySaved ? "done" : "current"]' in SCRIPT
    assert 'sourceOnly ? "保存来源图选择与顺序" : "保存最终顺序"' in SCRIPT
    assert 'post("content-package/source-only/review"' in SCRIPT
    assert "expected_revision: preview?.revision" in SCRIPT
    assert "image_actions: review.image_actions || []" in SCRIPT
    assert "image_order: review.image_order || []" in SCRIPT
    assert "video_action: review.video_action || \"none\"" in SCRIPT
    assert "仅保存本地，尚未写入妙手" in SCRIPT


def test_legacy_source_video_review_is_preserved_in_ai_studio():
    assert 'id="videoReviewPanel"' in HTML
    assert 'id="videoAction"' in HTML
    assert '<option value="keep">' in HTML
    assert '<option value="remove">' in HTML
    assert 'video_action: videoUrl ? ($("#videoAction")?.value || "remove") : "none"' in SCRIPT
    assert "preview?.review?.video_action || sourceVideo.action || \"keep\"" in SCRIPT
    assert '<video controls preload="none">' in SCRIPT
    assert 'target="_blank" rel="noopener">在新标签页打开来源视频' in SCRIPT


def test_source_images_default_to_keep_and_use_a_direct_remove_control():
    assert 'function sourceAction(row)' in SCRIPT
    assert 'row?.action === "remove" ? "remove" : "keep"' in SCRIPT
    assert 'class="source-remove"' in SCRIPT
    assert 'aria-label="删除来源图 ${index + 1}"' in SCRIPT
    assert 'type="hidden" value="keep"' in SCRIPT
    assert 'row.action = "remove"' in SCRIPT
    assert "identityReference.checked = false" in SCRIPT
    assert "identityPrimary.checked = false" in SCRIPT
    assert "来源图默认保留" in HTML


def test_release_center_and_studio_can_remain_open_in_parallel_tabs():
    assert (
        'id="productCenterLink" href="/product-workspace" target="_blank" rel="noopener"'
        in HTML
    )
    assert "url.searchParams.set(\"offer_id\", offerId)" in SCRIPT
    assert (
        "`/product-workspace?offer_id=${encodeURIComponent(preview.offer_id)}`"
        in SCRIPT
    )
