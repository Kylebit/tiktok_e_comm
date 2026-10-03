from pathlib import Path
from io import BytesIO
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.request import urlopen
from urllib.parse import urlencode
from html.parser import HTMLParser

from domains.data_operations.profit_settlement.http_report_view import handle_report_view
from domains.data_operations.profit_settlement.render import render_profit_report_html
from shared_platform import runtime_identity
from shared_platform.original_profit_reports import INDEX
from modules.products.server import Handler as ProductHandler


ROOT = Path(__file__).resolve().parents[1]


def test_first_level_july_tiktok_links_match_frozen_report_allowlist():
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'a' and attrs.get('data-site'):
                self.links[attrs['data-site']] = attrs

    links = Links()
    links.feed((ROOT / 'web/profit_hub.html').read_text(encoding='utf-8'))
    manifest = json.loads((ROOT / 'config/original_profit_reports.json').read_text(encoding='utf-8'))['files']
    assert set(links.links) == {'MY', 'TH', 'VN', 'PH'}
    for site, attrs in links.links.items():
        expected = (f'/profit-original/artifacts/profit_reports_monthly/2026-07/'
                    f'tiktok_{site}_2026-07-01_2026-07-31.monthly-profit.html')
        assert attrs['href'] == expected
        assert attrs['target'] == '_blank'
        assert {'noopener', 'noreferrer'} <= set(attrs['rel'].split())
        assert expected.removeprefix('/profit-original/artifacts/') in manifest


def test_profit_route_uses_task_hub_and_retains_original_july_deep_link():
    source = (ROOT / "modules/products/server.py").read_text(encoding="utf-8")
    hub = (ROOT / "web/profit_hub.html").read_text(encoding="utf-8")
    assert 'if path in ("/profit", "/profit.html"):' in source
    assert 'return self._file(WEB_DIR / "profit_hub.html")' in source
    assert 'if path.startswith("/profit-original/artifacts/"):' in source
    assert f'href="{INDEX}"' in hub
    assert 'if path in ("/profit-partial-review", "/profit-partial-review.html"):' in source
    assert 'return self._file(WEB_DIR / "profit_legacy.html")' in source
    assert 'if path in ("/profit-archive", "/profit-archive.html"):' in source
    assert 'return self._file(WEB_DIR / "profit_archive.html")' in source
    assert 'if path in ("/profit-review", "/profit-review.html"):' in source
    assert 'return self._file(WEB_DIR / "profit_center.html")' in source


def test_profit_skill_history_instructions_match_task_hub_and_original_links():
    skill = (ROOT / 'domains/data_operations/skills/manage-profit-settlement/SKILL.md').read_text(encoding='utf-8')
    reference = (ROOT / 'domains/data_operations/skills/manage-profit-settlement/references/historical-report-recovery.md').read_text(encoding='utf-8')
    mirror = (ROOT / 'artifacts/skill_reviews/manage-profit-settlement.zh-CN.md').read_text(encoding='utf-8')
    for content in (skill, reference, mirror):
        assert '`/profit`' in content
        assert '`/profit-original/artifacts/profit_reports_monthly/2026-07/`' in content
        assert '`/profit-partial-review`' in content
    assert '`/profit` redirects to the checksum-pinned original July monthly report' not in skill
    assert 'Use local `/profit` to open the checksum-pinned original July monthly report' not in reference
    assert '`/profit` 直接展示旧版布局' not in mirror
    assert '未声明可核对的 `source_sha256`' in mirror


def test_partial_review_keeps_real_historical_facts_separate_from_synthetic_preview():
    page = (ROOT / "web/profit_legacy.html").read_text(encoding="utf-8")
    layout = (ROOT / "web/profit_legacy_layout.html").read_text(encoding="utf-8")
    assert "真实历史部分结算" in page
    assert "428 个商品行、409 个脱敏订单" in page
    assert "不是完整 7 月利润报表" in page
    assert 'src="/profit-historical-partial"' in page
    assert 'href="/profit-legacy-layout"' in page
    assert 'type="file"' not in page
    assert "synthetic-only" in layout
    for marker in (
        "SHOPEE monthly 订单级利润明细",
        "下单日期从",
        "Seller SKU",
        "净结算(CNY)",
        "商品总成本(CNY)",
        "广告费(CNY)",
        "本土履约费(CNY)",
        "利润率(利润/用户实付)",
        "联盟营销佣金(AMS)",
        "最新汇率(CNY/当地)",
        "筛选合计",
    ):
        assert marker in layout


def test_real_handler_serves_task_hub_with_july_link_and_partial_review_separately():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProductHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/profit")
        response = connection.getresponse()
        assert response.status == 200
        hub = response.read().decode("utf-8")
        assert 'id="profitTasks"' in hub
        assert f'href="{INDEX}"' in hub
        connection.close()
        with urlopen(base + "/profit-partial-review", timeout=5) as response:
            page = response.read().decode("utf-8")
            assert response.status == 200
            assert "真实历史部分结算" in page
            assert response.headers["Cache-Control"] == "no-store"
        with urlopen(base + "/profit-historical-partial", timeout=10) as response:
            historical = response.read().decode("utf-8")
            assert response.status == 200
            assert "HISTORICAL_ACTUAL_PARTIAL_UNVERIFIED" in historical
            assert "428 个商品行" in historical
            assert "409 个脱敏父订单" in historical
            assert "2026-07-31：226 行" in historical
            assert "结算日期从" in historical
            assert "下单时间</th>" in historical
            assert "未具备" in historical
            assert response.headers["Cache-Control"] == "no-store"
            assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        with urlopen(base + "/profit-legacy-layout", timeout=5) as response:
            layout = response.read().decode("utf-8")
            assert response.status == 200
            assert "SHOPEE monthly 订单级利润明细" in layout
            assert response.headers["Content-Security-Policy"] == (
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "img-src data:; frame-ancestors 'self'; sandbox allow-scripts"
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_archive_surface_is_local_report_only_and_exposes_data_gap():
    page = (ROOT / "web/profit_archive.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/profit_archive.js").read_text(encoding="utf-8")
    assert "旧 7 月 JSON/HTML 快照未在现有工作区找到" in page
    assert "不保存、不联网、不重算" in page
    assert 'type="file"' in page and "multiple" in page
    assert "/api/profit-center/report-view" in script
    assert "localStorage" not in script
    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script
    assert "source_snapshot_or_checksum" in script
    assert "来源快照身份缺失" in script
    assert "crypto.subtle.digest('SHA-256'" in script
    assert "内容不同，已作为新版本保留" in script
    assert "reports.set(text(report.report_id),report)" not in script


def test_archive_client_fails_closed_on_report_identity_and_size():
    script = (ROOT / "web/static/profit_archive.js").read_text(encoding="utf-8")
    for marker in (
        "MAX_BYTES=16*1024*1024",
        "平台身份无效",
        "缺少 report_id",
        "period_kind 无效",
        "报告期间无效",
        "缺少订单行或合计",
        "缺少计算口径或状态",
    ):
        assert marker in script


def test_retained_detailed_renderer_keeps_reviewed_july_interactions():
    renderer = (ROOT / "domains/data_operations/profit_settlement/render.py").read_text(encoding="utf-8")
    for marker in (
        "总成交额（用户实付）CNY",
        "下单日期从",
        "daily-order-counts",
        "order-table-top-scroll",
        "筛选合计",
        "Seller SKU",
        "最新汇率(CNY/当地)",
        "商品名称",
    ):
        assert marker in renderer
    assert 'if platform in {"SHOPEE", "TIKTOK"}' in renderer


def test_archive_report_posts_to_retained_renderer_without_storage_or_network():
    report = {
        "report_id": "shopee-profit-fixture",
        "platform": "shopee",
        "period_kind": "monthly",
        "calculation_kind": "realized_settlement_with_estimated_ads",
        "period": {"start": "2026-07-01", "end": "2026-07-31"},
        "status": "needs_review",
        "totals": {},
        "order_lines": [],
    }
    body = urlencode({"report": json.dumps(report)}).encode()

    class Headers(dict):
        def get_all(self, name):
            value = self.get(name)
            return [value] if value is not None else []

    class Handler:
        server = SimpleNamespace(server_address=("127.0.0.1", 8876))
        headers = Headers({
            "Host": "127.0.0.1:8876",
            "Origin": "http://127.0.0.1:8876",
            "Content-Length": str(len(body)),
            "Content-Type": "application/x-www-form-urlencoded",
        })
        rfile = BytesIO(body)
        wfile = BytesIO()
        response_headers = {}

        def send_response(self, code):
            self.code = code

        def send_header(self, name, value):
            self.response_headers[name] = value

        def end_headers(self):
            pass

        def _json(self, code, payload):
            self.code, self.error = code, payload

    handler = Handler()
    handle_report_view(handler)
    rendered = handler.wfile.getvalue().decode()
    assert handler.code == 200
    assert "SHOPEE monthly 订单级利润明细" in rendered
    assert "无可计算事实；金额未知" in rendered
    assert handler.response_headers["Content-Security-Policy"] == "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; sandbox allow-scripts"
    assert handler.response_headers["Cache-Control"] == "no-store"


def test_runtime_identity_tracks_archive_assets_and_detects_each_change(tmp_path):
    names = (
        "web/profit_archive.html",
        "web/static/profit_archive.js",
        "web/static/profit_archive.css",
        "web/profit_legacy.html",
        "web/profit_legacy_layout.html",
        "web/static/profit_legacy.css",
    )
    assert all(name in runtime_identity.ASSET_FILES for name in names)
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    original = runtime_identity.file_set(tmp_path, names)
    for name in names:
        path = tmp_path / name
        before = path.read_text(encoding="utf-8")
        path.write_text(before + " changed", encoding="utf-8")
        changed = runtime_identity.file_set(tmp_path, names)
        actual = {
            "service": "fixture", "contract_version": runtime_identity.CONTRACT,
            "code_root": str(tmp_path.resolve()), "commit": "a" * 40,
            "build": {"digest": "same", "missing": []}, "assets": original,
            "profile": {"profile_id": "fixture", "settings_path": "fixture"},
            "configuration_source_known": True, "configuration_source_matches": True,
            "configuration_initialized": True, "stores": {}, "missing_dependencies": [],
        }
        expected = {**actual, "assets": changed}
        assert runtime_identity.compare_identity(actual, expected) == "ASSET_MISMATCH"
        path.write_text(before, encoding="utf-8")


def test_isolated_renderer_accepts_embedded_raster_and_refuses_remote_image():
    base = {
        "report_id": "fixture", "platform": "shopee", "period_kind": "monthly",
        "calculation_kind": "fixture", "period": {"start": "2026-07-01", "end": "2026-07-31"},
        "status": "needs_review", "totals": {}, "order_lines": [],
    }
    line = {"identity": {}, "product": {"image_url": "data:image/png;base64,iVBORw0KGgo="}}
    embedded = render_profit_report_html({**base, "order_lines": [line]})
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in embedded
    from domains.data_operations.profit_settlement.http_report_view import _isolated_report
    isolated = _isolated_report({**base, "order_lines": [{"identity": {}, "product": {"image_url": "https://example.invalid/main.jpg"}}]})
    remote = render_profit_report_html(isolated)
    assert "https://example.invalid/main.jpg" not in remote
    assert "无可离线显示主图" in remote
