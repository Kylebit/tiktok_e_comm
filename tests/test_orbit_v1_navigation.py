from pathlib import Path

from shared_platform.orbit_registry import (
    NAVIGATION,
    WORKSPACES,
    build_module_specs,
    navigation_payload,
)
from shared_platform.registry import owner_for_http_path


ROOT = Path(__file__).resolve().parents[1]


def test_orbit_primary_information_architecture_is_domain_first():
    primary = [item.label for item in NAVIGATION if item.level == "primary"]
    secondary = [item.label for item in NAVIGATION if item.level == "secondary"]

    assert primary == ["总览", "商品运营", "内容运营", "渠道运营", "供应链运营", "数据运营"]
    assert secondary == ["待我审批", "任务与通知", "审计记录", "系统与服务"]


def test_data_workspace_aggregates_existing_routes_without_replacement():
    data = next(workspace for workspace in WORKSPACES if workspace.key == "data")
    assert [link.href for link in data.links] == [
        "/settlement",
        "/sku-profit",
        "/billing",
        "/analytics",
    ]
    assert next(workspace for workspace in WORKSPACES if workspace.key == "supply-chain").availability == "planned"


def test_local_service_specs_have_one_shared_definition_and_keep_ports():
    modules = build_module_specs(ROOT, "python")

    assert [(module.key, module.port) for module in modules] == [
        ("os", 8765),
        ("treasury", 8766),
        ("rus", 8767),
    ]
    for relative in ("desktop/orbit_desktop_webview.py", "desktop/orbit_desktop_app.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "class ModuleSpec" not in source
        assert "MODULES = [" not in source
        assert "build_module_specs(ROOT, PYTHON)" in source


def test_navigation_payload_and_server_route_are_shared_platform_owned():
    payload = navigation_payload()

    assert len(payload["navigation"]) == 10
    assert {item["key"] for item in payload["workspaces"]} == {
        "product",
        "content",
        "channel",
        "supply-chain",
        "data",
    }
    assert owner_for_http_path("/api/orbit/navigation") == "shared_platform"
    server_source = (ROOT / "modules/products/server.py").read_text(encoding="utf-8")
    assert 'if path == "/api/orbit/navigation":' in server_source


def test_home_html_uses_registry_and_explicit_unknown_states():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    css = (ROOT / "web/static/orbit.css").read_text(encoding="utf-8")

    assert "fetch('/api/orbit/navigation')" in html
    assert "V1 不推测任务结果" in html
    assert "V1 不伪造库存、仓库或补货状态" in html
    assert "statusPayload.pending_mx" in html
    assert "fetch('/api/orbit/inbox?limit=20')" in html
    assert "'/api/orbit/report-runs?limit=50'" in html
    assert "不会把“没有记录”显示成任务成功" in html
    assert "@media (max-width: 720px)" in css


def test_orbit_report_and_inbox_routes_are_read_only_shared_platform_views():
    server_source = (ROOT / "modules/products/server.py").read_text(encoding="utf-8")

    assert 'path in ("/api/orbit/report-runs", "/api/orbit/inbox")' in server_source
    assert "store.list_report_runs(limit=limit)" in server_source
    assert "store.list_inbox(status=status, limit=limit)" in server_source
