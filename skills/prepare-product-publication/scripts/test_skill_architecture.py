from __future__ import annotations

from pathlib import Path

import yaml


SKILL_DIR = Path(__file__).resolve().parents[1]


def test_skill_package_has_required_zero_write_review_contracts():
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    decision = (SKILL_DIR / "references" / "decision-contract.md").read_text(encoding="utf-8")

    assert "publication-preparation-decision/v1" in decision
    assert "FIRST_REVIEW_READY" in skill
    assert "zero external writes" in skill.lower()
    assert "second round" in skill.lower()
    assert "Do not use OCR" in skill
    assert "Do not automatically" in skill


def test_skill_client_cannot_claim_create_site_drafts_or_publish():
    script = (SKILL_DIR / "scripts" / "prepare_product_publication.py").read_text(encoding="utf-8")

    assert "claim_miaoshou_to_tiktok" not in script
    assert "prepare_miaoshou_site_drafts" not in script
    assert "publish_approved_product" not in script
    assert "write_miaoshou_draft" not in script
    assert "prepare_miaoshou_draft" not in script


def test_openai_interface_mentions_the_skill_and_review_packet():
    payload = yaml.safe_load((SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    interface = payload["interface"]

    assert 25 <= len(interface["short_description"]) <= 64
    assert "$prepare-product-publication" in interface["default_prompt"]
    assert "first-review decision packet" in interface["default_prompt"]
