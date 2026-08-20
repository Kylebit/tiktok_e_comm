from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_product_center_is_the_only_human_review_surface() -> None:
    product = _read("web/product_workspace.html")
    product_script = _read("web/static/product_workspace.js")
    studio = _read("web/ai_image_studio.html")
    localized = _read("web/localized_image_review.html")

    assert 'data-human-review-surface="product-center"' in product
    assert 'id="embeddedImageReview"' in product
    assert 'id="localizedImageResults"' in product
    assert "content-package/localized-image-review" in product_script
    assert "只审核商品信息、图片方案、目标店铺与售价" in product

    assert 'data-human-review-surface="none"' in studio
    assert "所有人工审核只在商品发布中心完成" in studio
    assert "来源图审核" not in studio
    assert "保存来源图决定" not in studio
    assert "保存图片决定" not in studio
    assert "保存最终顺序" not in studio
    assert "同步图片并批准最终内容" not in studio

    assert 'data-human-review-surface="none"' in localized
    assert "多语言图片执行结果" in localized
    assert "审核台" not in localized
    assert "批准" not in localized


def test_product_center_has_no_page_level_approval_buttons() -> None:
    product = _read("web/product_workspace.html")

    assert "保存图片选择" in product
    assert "保存图片审核" not in product
    assert "保存并确认商品事实" not in product
    assert 'id="approval" class="approval-section operator-clutter"' in product
    assert 'id="releasePlan" class="release-plan-section operator-clutter"' in product


def test_conversation_approval_remains_the_skill_authority() -> None:
    first_round = _read("skills/prepare-product-publication/SKILL.md")
    second_round = _read("skills/prepare-product-images/SKILL.md")
    publication = _read("skills/publish-approved-product/SKILL.md")

    assert "explicit approval in the conversation is the only human approval" in first_round
    assert "explicit approval in the conversation as the only human approval" in second_round
    assert "Page buttons are never approval authorities" in publication


def test_bilingual_publication_skill_guide_is_read_only_and_tracks_all_three_skills() -> None:
    guide = ROOT / "docs" / "PRODUCT_PUBLICATION_SKILLS_EN_ZH.md"
    text = guide.read_text(encoding="utf-8")

    assert text.startswith("# 商品发布 Skills 中英对照版")
    assert "非执行权威" in text
    assert "英文 `SKILL.md` 是唯一执行权威" in text
    assert "## English source (verbatim)" in text
    assert "## 中文完整翻译" in text
    for skill_name in (
        "prepare-product-publication",
        "prepare-product-images",
        "publish-approved-product",
    ):
        assert f"skills/{skill_name}/SKILL.md" in text
        assert f"skill-translations/{skill_name}.zh-CN.md" in text

    assert not (ROOT / "docs" / "PRODUCT_PUBLICATION_SKILLS_ZH.md").exists()

    check = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_product_publication_skills_bilingual.py"),
            "--check",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
