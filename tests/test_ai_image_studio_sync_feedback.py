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
