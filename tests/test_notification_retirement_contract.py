from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_retired_notification_executables_and_catalog_entry_stay_absent() -> None:
    retired_executables = (
        "agent_comms/a2a_poc/server.py",
        "agent_comms/a2a_poc/client.py",
        "agent_comms/a2a_poc/ag_ui_mapping.py",
        "agent_comms/stage1/orchestrator.py",
        "agent_comms/stage1/bridge.py",
        "agent_comms/stage1/eigenflux_client.py",
        "agent_comms/stage1/run_stage1_demo.py",
        "agent_comms/serve_deliverables.py",
        "agent_comms/helper/helper_agent.py",
        "tools/_md_to_html_report.py",
    )
    assert not [path for path in retired_executables if (ROOT / path).exists()]

    catalog = json.loads((ROOT / "config" / "capability_catalog.json").read_text(encoding="utf-8"))
    encoded = json.dumps(catalog, ensure_ascii=False).lower()
    assert "feishu" not in encoded
    assert "lark" not in encoded
    assert "飞书" not in encoded
    assert not any(item.get("id") == "eigenflux" for item in catalog.get("tools", []))


def test_current_user_surfaces_do_not_advertise_retired_notification_stack() -> None:
    current_surfaces = (
        "README.md",
        "docs/DEPLOY.md",
        "docs/ARCHITECTURE.md",
        "shared_platform/entry_catalog.json",
    )
    current_surfaces += tuple(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in (ROOT / "web").rglob("*")
        if path.is_file() and path.suffix.lower() in {".html", ".js", ".css", ".json"}
    )
    forbidden = ("feishu", "lark", "飞书", "日报")
    findings: list[str] = []
    for relative in current_surfaces:
        text = (ROOT / relative).read_text(encoding="utf-8").lower()
        for token in forbidden:
            if token in text:
                findings.append(f"{relative}: {token}")
    assert findings == []


def test_lark_sdk_is_not_a_runtime_or_build_dependency() -> None:
    dependency_files = (
        "requirements-catalog.txt",
        "requirements-dev.txt",
        "requirements-review.lock.txt",
        "desktop/requirements-build.lock.txt",
    )
    findings = []
    for relative in dependency_files:
        text = (ROOT / relative).read_text(encoding="utf-8").lower()
        if "lark" in text or "feishu" in text:
            findings.append(relative)
    assert findings == []


def test_profit_report_surface_uses_neutral_companion_filename_for_new_data() -> None:
    source = (ROOT / "modules" / "shopee" / "profit_settlement.py").read_text(encoding="utf-8")
    assert '.summary.json' in source
    assert 'Historical data compatibility only' in source


def test_profit_report_companion_prefers_new_name_and_reads_legacy_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from modules.shopee import profit_settlement

    monkeypatch.setattr(profit_settlement, "OUTPUT_DIR", tmp_path)
    report = tmp_path / "weekly_shopee_profit_20260701_20260707.html"
    report.write_text('const DATA = {"rows": [], "rates": {}};\n', encoding="utf-8")
    legacy = report.with_suffix(".feishu.json")
    legacy.write_text('{"historical": true}\n', encoding="utf-8")
    legacy_bytes = legacy.read_bytes()
    legacy_mtime = legacy.stat().st_mtime_ns

    assert profit_settlement._report_meta(report)["json"] == legacy.name
    assert legacy.read_bytes() == legacy_bytes
    assert legacy.stat().st_mtime_ns == legacy_mtime
    assert not report.with_suffix(".summary.json").exists()

    current = report.with_suffix(".summary.json")
    current.write_text('{"current": true}\n', encoding="utf-8")
    current_bytes = current.read_bytes()
    current_mtime = current.stat().st_mtime_ns

    assert profit_settlement._report_meta(report)["json"] == current.name
    assert current.read_bytes() == current_bytes
    assert current.stat().st_mtime_ns == current_mtime
    assert legacy.read_bytes() == legacy_bytes


@pytest.mark.parametrize("module_name,region", (
    ("modules.miaoshou.mx_manual_overrides", "mx_confirm"),
    ("modules.miaoshou.uk_manual_overrides", "uk_confirm"),
))
def test_manual_override_reader_migrates_legacy_facts_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module_name: str, region: str
) -> None:
    module = __import__(module_name, fromlist=["load_overrides"])
    current = tmp_path / region / "manual_overrides.json"
    legacy = tmp_path / region / "feishu_manual_overrides.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"0001": {"weight_g": 120}}), encoding="utf-8")
    monkeypatch.setattr(module, "OVERRIDES_PATH", current)
    monkeypatch.setattr(module, "LEGACY_OVERRIDES_PATH", legacy)

    assert module.load_overrides() == {"0001": {"weight_g": 120}}
    saved = module.save_override("1", {"length_cm": 8}, note="checked")

    assert saved == {"weight_g": 120, "length_cm": 8, "note": "checked"}
    assert json.loads(current.read_text(encoding="utf-8"))["0001"] == saved
    assert json.loads(legacy.read_text(encoding="utf-8")) == {"0001": {"weight_g": 120}}


def test_historical_notification_evidence_is_explicitly_reference_only() -> None:
    legacy = ROOT / "docs" / "legacy" / "notifications"
    assert (legacy / "README.md").is_file()
    assert "reference_only" in (legacy / "README.md").read_text(encoding="utf-8").lower()
    assert not list(legacy.rglob("*.py"))
