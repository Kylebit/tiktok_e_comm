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
    assert [(item.label, item.href) for item in NAVIGATION] == [
        ("商品目录", "/catalog"),
        ("商品上架", "/product-workspace"),
        ("供应链", "/supply-chain/"),
        ("利润", "/profit"),
        ("知识工具", "/knowledge"),
    ]
    assert all(item.level == "primary" for item in NAVIGATION)


def test_data_workspace_aggregates_existing_routes_without_replacement():
    data = next(workspace for workspace in WORKSPACES if workspace.key == "data")
    assert [link.href for link in data.links] == [
        "/profit",
        "/settlement",
        "/sku-profit",
        "/billing",
        "/analytics",
    ]
    assert next(workspace for workspace in WORKSPACES if workspace.key == "supply-chain").availability == "available"


def test_local_service_specs_keep_ports_and_desktop_uses_verified_session():
    modules = build_module_specs(ROOT, "python")

    assert [(module.key, module.port) for module in modules] == [
        ("os", 8765),
        ("treasury", 8766),
        ("rus", 8767),
    ]
    startup = (ROOT / "desktop/startup.py").read_text(encoding="utf-8")
    webview = (ROOT / "desktop/orbit_desktop_webview.py").read_text(encoding="utf-8")
    assert "DesktopSession(selection, state)" in startup
    assert "self.session.verify_document(url)" in webview
    assert "MODULES = [" not in startup + webview


def test_navigation_payload_and_server_route_are_shared_platform_owned():
    payload = navigation_payload()

    assert [(item["key"], item["href"], item["level"]) for item in payload["navigation"]] == [
        (item.key, item.href, "primary") for item in NAVIGATION
    ]
    assert {step["href"] for step in payload["flow"]} <= {
        item["href"] for item in payload["navigation"]
    }
    assert "历史能力清单" in payload["catalog_scope"]
    assert "一级入口以 navigation 的五项为准" in payload["catalog_scope"]
    assert {item["key"] for item in payload["workspaces"]} == {
        "product",
        "content",
        "channel",
        "supply-chain",
        "data",
    }
    assert owner_for_http_path("/api/orbit/navigation") == "shared_platform"
    assert owner_for_http_path("/release") == "shared_platform"
    assert owner_for_http_path("/api/release/dashboard") == "shared_platform"
    assert owner_for_http_path("/new-product") == "product_operations"
    assert owner_for_http_path("/profit") == "data_operations"
    assert payload["internal_tools"] == [
        {
            "label": "Release Lab",
            "href": "/internal/release",
            "description": "内部发布候选验收工具；不是日常业务入口",
        }
    ]
    server_source = (ROOT / "modules/products/server.py").read_text(encoding="utf-8")
    assert 'if path == "/api/orbit/navigation":' in server_source


def test_home_is_task_workspace_with_registered_five_core_navigation_and_unknown_states():
    html = (ROOT / "web/task_workspace.html").read_text(encoding="utf-8")
    tasks = (ROOT / "web/static/task_workspace.js").read_text(encoding="utf-8")
    shell = (ROOT / "web/static/operations_shell.js").read_text(encoding="utf-8")
    server = (ROOT / "modules/products/server.py").read_text(encoding="utf-8")

    assert "return self._file(WEB_DIR / 'task_workspace.html')" in server
    assert "<h1>任务工作台</h1>" in html
    assert 'src="/static/operations_shell.js"' in html
    assert 'src="/static/task_workspace.js"' in html
    assert "ORBIT_REGISTERED_NAVIGATION" in shell
    assert "fetch('/api/orbit/navigation'" in shell
    assert "导航配置不一致" in shell
    assert "状态未知" in tasks
    assert "任务服务连接失败，当前进度未验证。" in tasks
    assert "@media(max-width:" in (ROOT / "web/static/operations_shell.css").read_text(encoding="utf-8")


def test_orbit_report_and_inbox_routes_are_read_only_shared_platform_views():
    server_source = (ROOT / "modules/products/server.py").read_text(encoding="utf-8")

    assert 'path == "/api/orbit/report-runs"' in server_source
    assert "store.list_report_runs(limit=limit)" in server_source
    assert "store.list_inbox(" not in server_source
