from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ai_studio_exposes_truthful_miaoshou_sync_progress_and_readback_feedback():
    html = (ROOT / "web/ai_image_studio.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")
    css = (ROOT / "web/static/ai_image_studio.css").read_text(encoding="utf-8")

    assert "syncProgress" in html
    assert "syncStepList" in html
    assert "同步与回读中" in html
    assert "审核门禁与最终顺序" in script
    assert "读取妙手当前版本" in script
    assert "写入主图与详情图" in script
    assert "回读并逐项验证" in script
    assert "setInterval(async ()" in script
    assert "content-package/miaoshou-images/commit" in script
    assert "若妙手旧规格重量不符合保存规则" in html
    assert "confirm_miaoshou_write: true" in script
    assert "error.payload?.sync" in script
    assert "未认领或发布商品" in script
    assert ".sync-progress" in css
    assert "@media (max-width:430px)" in css


def test_miaoshou_handler_returns_structured_failure_feedback_without_relaxing_confirmation():
    source = (
        ROOT / "modules/sourcing/new_product_server.py"
    ).read_text(encoding="utf-8")

    assert 'data.get("confirm_miaoshou_write") is not True' in source
    assert '"explicit Miaoshou write confirmation is required"' in source
    assert '"sync": sync' in source
    assert '"claimed": False' in source
    assert '"published": False' in source


def test_storyboard_cards_save_operator_copy_and_require_a_fresh_preflight():
    script = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")
    source = (ROOT / "modules/sourcing/new_product_server.py").read_text(encoding="utf-8")

    assert 'data-story-title=' in script
    assert 'data-story-focus=' in script
    assert 'saveStoryboardEdits' in script
    assert 'content-package/storyboard-edits' in script
    assert '需要重新预检' in script
    assert '"/api/new-product/content-package/storyboard-edits"' in source
    assert 'expected_revision=data.get("expected_revision")' in source
    assert "async function prepareGeneration()" in script
    assert "await saveStoryboardEdits();" in script


def test_miaoshou_sync_uses_checkbox_authorization_without_a_second_native_dialog():
    script = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")
    sync_source = script.split("async function syncMiaoshou()", 1)[1].split(
        "function schedulePoll()", 1
    )[0]

    assert 'if (!$("#miaoshouConfirm").checked) return;' in sync_source
    assert "if (syncInFlight) return;" in sync_source
    assert "confirm(" not in sync_source
    assert 'status: "preparing"' in sync_source
    assert 'post("content-package/miaoshou-images/commit"' in sync_source


def test_ai_studio_uses_one_generation_basis_confirmation_and_one_draft_barrier():
    html = (ROOT / "web/ai_image_studio.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")

    assert 'id="generationBasisConfirmed"' in html
    assert 'id="factApproved"' not in html
    assert 'id="scopeApproved"' not in html
    assert 'const generationBasisConfirmed = $("#generationBasisConfirmed").checked;' in script
    assert "fact_card_approved: generationBasisConfirmed" in script
    assert "planning_scope_approved: generationBasisConfirmed" in script
    assert "async function saveAllContentDrafts" in script
    assert "storyboardDraftDirty" in script
    assert "await saveAllContentDrafts({ includeFinalOrder: true })" in script


def test_verified_miaoshou_image_sync_also_finishes_final_content_approval():
    html = (ROOT / "web/ai_image_studio.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")
    sync_source = script.split("async function syncMiaoshou()", 1)[1].split(
        "function schedulePoll()", 1
    )[0]

    assert "同步图片并批准最终内容" in html
    assert "async function finalizeAiContentAfterVerifiedSync" in script
    assert 'post("content-package/finalize"' in script
    assert "await finalizeAiContentAfterVerifiedSync();" in sync_source


def test_ai_studio_exposes_isolated_locale_image_packs_without_product_center_write():
    html = (ROOT / "web/ai_image_studio.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")
    css = (ROOT / "web/static/ai_image_studio.css").read_text(encoding="utf-8")

    assert 'id="initializeLocalizedImagePacksButton"' in html
    assert 'id="localizedImagePackGrid"' in html
    assert 'id="localizedImageRouteGrid"' in html
    assert "不会修改商品发布中心" in html
    assert 'flowApi("content-package/localized-images")' in script
    assert 'post("content-package/localized-images/initialize"' in script
    assert "商品发布中心和平台均未被修改" in script
    assert "pack.status" in script
    assert "待文字识别、翻译、排版与人工批准" in script
    assert ".localized-pack-grid" in css


def test_ai_studio_supports_local_ocr_editable_translations_and_local_preview():
    html = (ROOT / "web/ai_image_studio.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/ai_image_studio.js").read_text(encoding="utf-8")

    assert 'id="scanLocalizedImageTextButton"' in html
    assert 'id="localizedImageLocaleSelect"' in html
    assert 'id="localizedTranslationGrid"' in html
    assert "本地 OCR 扫描全部英文文字" in html
    assert 'post("content-package/localized-images/scan-text"' in script
    assert 'post("content-package/localized-images/translation-draft"' in script
    assert 'post("content-package/localized-images/preview"' in script
    assert "localizedTranslationDraft" in script
    assert "刷新不会静默覆盖当前输入" in script
    assert "尚未批准或发布" in script
