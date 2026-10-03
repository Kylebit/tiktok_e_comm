from pathlib import Path
import hashlib
from scripts import build_product_publication_skills_bilingual


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_product_center_is_the_only_human_review_surface() -> None:
    product = _read("web/product_workspace.html")
    product_script = _read("web/static/product_workspace.js")
    studio = _read("web/ai_image_studio.html")
    assert not (ROOT / "web/localized_image_review.html").exists()

    assert 'data-human-review-surface="product-center"' in product
    assert 'id="embeddedImageReview"' in product
    assert 'id="localizedImageResults"' in product
    assert 'id="originalPublicationReview"' in product
    assert "content-package/localized-image-review" in product_script
    assert "只审核商品信息、图片方案、目标店铺与售价" in product
    assert "页面不会创建额外批准" in product

    assert 'data-human-review-surface="none"' in studio
    assert "所有人工审核只在商品发布中心完成" in studio
    assert "来源图审核" not in studio
    assert "保存来源图决定" not in studio
    assert "保存图片决定" not in studio
    assert "保存最终顺序" not in studio
    assert "同步图片并批准最终内容" not in studio



def test_product_center_has_no_page_level_approval_buttons() -> None:
    product = _read("web/product_workspace.html")

    assert "保存图片选择" in product
    assert "保存图片审核" not in product
    assert "保存并确认商品事实" not in product
    assert 'id="approval" class="approval-section operator-clutter"' in product
    assert 'id="releasePlan" class="release-plan-section"' in product
    assert 'id="releasePlanApprovalForm" class="release-plan-form operator-clutter"' in product


def test_only_final_marketplace_publication_needs_human_review() -> None:
    first_round = _read("skills/prepare-product-publication/SKILL.md")
    second_round = _read("skills/prepare-product-images/SKILL.md")
    publication = _read("skills/publish-approved-product/SKILL.md")

    assert "human_approval=false" in first_round
    assert "The sole\nnormal human gate is `FINAL_MARKETPLACE_PUBLISH`" in first_round
    assert "Continue into R2 without asking for an intermediate approval" in first_round
    assert "an autopilot snapshot with `human_approval=false` is the normal input" in second_round
    assert "an explicit `--approved-by Kyle` is blocked" in second_round
    assert "no independent conversation-receipt binding" in second_round
    assert "cannot authorize new R2 paid execution without independently verifiable provenance" in second_round
    assert "These plans do not grant final marketplace approval" in second_round
    assert "without an intermediate approval prompt" in second_round
    assert "Only the later digest-bound `FINAL_MARKETPLACE_PUBLISH` receipt does" in second_round
    assert "Kyle reviews the complete\nfrozen candidate for every selected target on the Product Publication frontend" in publication
    assert "its durable receipt is the sole human approval" in publication
    assert "No separate\nconversation confirmation is required" in publication
    assert "A bare\nbrowser click or caller-supplied" in publication
    assert "Treat business execution as HOLD" in publication
    assert "This is the only normal human approval gate" in publication
    assert "never ask for the same approval again" in publication


def test_bilingual_publication_skill_guide_is_read_only_and_stale_mirrors_are_marked() -> None:
    guide = ROOT / "docs" / "PRODUCT_PUBLICATION_SKILLS_EN_ZH.md"
    text = guide.read_text(encoding="utf-8")
    assert text == build_product_publication_skills_bilingual.build()

    assert text.startswith("# 商品发布 Skills 中英对照版")
    assert "非执行权威" in text
    assert "英文 `SKILL.md` 是唯一执行权威" in text
    assert "## English source (verbatim)" in text
    assert "## 中文参考译文" in text
    for skill_name in (
        "prepare-product-publication",
        "prepare-product-images",
        "publish-approved-product",
    ):
        assert f"skills/{skill_name}/SKILL.md" in text
        assert f"skill-translations/{skill_name}.zh-CN.md" in text

        source = ROOT / "skills" / skill_name / "SKILL.md"
        translation = ROOT / "docs" / "skill-translations" / f"{skill_name}.zh-CN.md"
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        translated = translation.read_text(encoding="utf-8")
        if skill_name == "publish-approved-product":
            assert "经服务端验证的本机前端回执可独立构成唯一终审" in translated
            assert "当前正式业务仍为 HOLD" in translated
            assert "经服务端验证的本机前端回执可独立构成唯一终审" in text
        if f"<!-- source_sha256: {source_digest} -->" not in translated:
            assert "历史译本" in translated
            assert "当前英文 `SKILL.md`" in translated
            assert f"`{skill_name}` 中文译文已过期" in text
        else:
            assert f"`{skill_name}` 中文译文已同步" in text

    assert not (ROOT / "docs" / "PRODUCT_PUBLICATION_SKILLS_ZH.md").exists()
