"""本地 Web 控制台：页面 + REST API。"""

import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.config import ROOT
from modules.products import costs as cost_mod

WEB_DIR = ROOT / "web"
DEFAULT_PORT = 8765

_scan_lock = threading.Lock()
_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_push_lock = threading.Lock()
_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_promo_scan_lock = threading.Lock()
_promo_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_promo_push_lock = threading.Lock()
_promo_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_deact_scan_lock = threading.Lock()
_deact_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_deact_push_lock = threading.Lock()
_deact_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_analytics_sync_lock = threading.Lock()
_analytics_sync_job: dict = {
    "running": False,
    "message": "",
    "total": 0,
    "by_segment": {},
    "error": None,
}


def _run_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    mode: str = "velocity",
) -> None:
    global _scan_job
    try:
        from modules.products import titles as title_mod

        if mode == "analytics":
            _scan_job["message"] = "同步 Analytics，随后 AI 生成标题+详情..."
            n = title_mod.scan_analytics_high_interest(
                limit=limit,
                region=region,
                build_html=False,
                quiet=True,
            )
        else:
            _scan_job["message"] = "正在统计动销，随后 AI 生成标题..."
            n = title_mod.scan_low_velocity(
                days=days,
                max_units=max_units,
                limit=limit,
                region=region,
                build_html=False,
                quiet=True,
            )
        _scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _scan_job.update(running=False, message="", error=str(e))


def _start_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    mode: str = "velocity",
) -> tuple[bool, str]:
    with _scan_lock:
        if _scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(
        target=_run_scan,
        args=(days, max_units, limit, region, mode),
        daemon=True,
    )
    t.start()
    return True, "已开始扫描"


def _scan_status() -> dict:
    with _scan_lock:
        return dict(_scan_job)


def _run_push(items: list[dict]) -> None:
    global _push_job
    from modules.products import titles as title_mod

    try:
        edits = [{
            "product_id": it.get("product_id"),
            "shop_cipher": it.get("shop_cipher"),
            "new_title": it.get("new_title"),
            "new_description": it.get("new_description"),
        } for it in items]
        title_mod.save_edits(edits)
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _push_job["message"] = f"正在推送 0/{total}..."
        result = title_mod.push_approved(ids if ids else None)
        _push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _push_job.update(running=False, message="", error=str(e))


def _start_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可推送的条目"
    with _push_lock:
        if _push_job["running"]:
            return False, "已有推送任务在进行中，请稍候"
        _push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始推送"


def _push_status() -> dict:
    with _push_lock:
        return dict(_push_job)


def _run_promo_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    scope: str,
    mode: str = "velocity",
) -> None:
    global _promo_scan_job
    try:
        from modules.products import promotions as promo_mod

        if mode == "analytics":
            _promo_scan_job["message"] = "同步 Analytics A 类，生成促销建议..."
            n = promo_mod.scan_analytics_high_interest(
                limit=limit,
                region=region,
                scope=scope,
                quiet=True,
            )
        else:
            _promo_scan_job["message"] = "正在统计动销并拉取促销活动..."
            n = promo_mod.scan_low_velocity(
                days=days,
                max_units=max_units,
                limit=limit,
                region=region,
                scope=scope,
                quiet=True,
            )
        _promo_scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _promo_scan_job.update(running=False, message="", error=str(e))


def _start_promo_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    scope: str = "adjust",
    mode: str = "velocity",
) -> tuple[bool, str]:
    with _promo_scan_lock:
        if _promo_scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _promo_scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(
        target=_run_promo_scan,
        args=(days, max_units, limit, region, scope, mode),
        daemon=True,
    )
    t.start()
    return True, "已开始扫描"


def _promo_scan_status() -> dict:
    with _promo_scan_lock:
        return dict(_promo_scan_job)


def _run_promo_push(items: list[dict]) -> None:
    global _promo_push_job
    from modules.products import promotions as promo_mod

    try:
        edits = [{
            "product_id": it.get("product_id"),
            "shop_cipher": it.get("shop_cipher"),
            "new_discount": it.get("new_discount"),
            "flash_price": it.get("flash_price"),
            "promo_price": it.get("promo_price"),
            "action": it.get("action"),
        } for it in items]
        promo_mod.save_edits(edits)
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _promo_push_job["message"] = f"正在推送 0/{total}..."
        result = promo_mod.push_approved(ids if ids else None)
        _promo_push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _promo_push_job.update(running=False, message="", error=str(e))


def _start_promo_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可推送的条目"
    with _promo_push_lock:
        if _promo_push_job["running"]:
            return False, "已有推送任务在进行中，请稍候"
        _promo_push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_promo_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始推送"


def _promo_push_status() -> dict:
    with _promo_push_lock:
        return dict(_promo_push_job)


def _run_analytics_sync(region: str | None) -> None:
    global _analytics_sync_job
    try:
        from modules.products import analytics as analytics_mod

        _analytics_sync_job["message"] = "正在拉取各站 Analytics..."
        result = analytics_mod.sync_all(region=region, quiet=True)
        _analytics_sync_job.update(
            running=False,
            message=f"完成，共 {result['total']} 条",
            total=result["total"],
            by_segment=result.get("by_segment") or {},
            error=None,
        )
    except Exception as e:
        _analytics_sync_job.update(running=False, message="", error=str(e))


def _start_analytics_sync(region: str | None) -> tuple[bool, str]:
    with _analytics_sync_lock:
        if _analytics_sync_job["running"]:
            return False, "已有 Analytics 同步任务在进行中"
        _analytics_sync_job.update(
            running=True, message="启动中...", total=0, by_segment={}, error=None
        )
    t = threading.Thread(target=_run_analytics_sync, args=(region,), daemon=True)
    t.start()
    return True, "已开始同步"


def _analytics_sync_status() -> dict:
    with _analytics_sync_lock:
        return dict(_analytics_sync_job)


def _run_deact_scan(limit: int, region: str | None) -> None:
    global _deact_scan_job
    try:
        from modules.products import deactivate as deact_mod

        _deact_scan_job["message"] = "同步 Analytics 并筛选下架候选..."
        n = deact_mod.scan_candidates(region=region, limit=limit, quiet=True)
        _deact_scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _deact_scan_job.update(running=False, message="", error=str(e))


def _start_deact_scan(limit: int, region: str | None) -> tuple[bool, str]:
    with _deact_scan_lock:
        if _deact_scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _deact_scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(target=_run_deact_scan, args=(limit, region), daemon=True)
    t.start()
    return True, "已开始扫描"


def _deact_scan_status() -> dict:
    with _deact_scan_lock:
        return dict(_deact_scan_job)


def _run_deact_push(items: list[dict]) -> None:
    global _deact_push_job
    from modules.products import deactivate as deact_mod

    try:
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _deact_push_job["message"] = f"正在下架 0/{total}..."
        result = deact_mod.push_approved(ids if ids else None)
        _deact_push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _deact_push_job.update(running=False, message="", error=str(e))


def _start_deact_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可下架的条目"
    with _deact_push_lock:
        if _deact_push_job["running"]:
            return False, "已有下架任务在进行中，请稍候"
        _deact_push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_deact_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始下架"


def _deact_push_status() -> dict:
    with _deact_push_lock:
        return dict(_deact_push_job)


def _api_status() -> dict:
    from core import auth
    from modules.products import titles as title_mod
    from modules.products import promotions as promo_mod
    from modules.products import deactivate as deact_mod

    try:
        tok = auth.load_token()
        access_exp = auth.access_expires_at(tok)
        refresh_exp = auth.refresh_expires_at(tok)
        pending = len(title_mod.load_queue("pending"))
        pending_promos = len(promo_mod.load_queue("pending"))
        pending_deact = len(deact_mod.load_queue("pending"))
        return {
            "ok": True,
            "seller_name": tok.get("seller_name"),
            "access_expires": access_exp.isoformat() if access_exp else None,
            "refresh_expires": refresh_exp.isoformat() if refresh_exp else None,
            "pending_titles": pending,
            "pending_promos": pending_promos,
            "pending_deactivate": pending_deact,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path):
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if not length:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html")
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            return self._file(WEB_DIR / "static" / rel)
        if path in ("/costs", "/costs.html"):
            return self._file(WEB_DIR / "costs.html")
        if path in ("/titles", "/titles.html"):
            return self._file(WEB_DIR / "titles.html")
        if path in ("/promotions", "/promotions.html"):
            return self._file(WEB_DIR / "promotions.html")
        if path in ("/analytics", "/analytics.html"):
            return self._file(WEB_DIR / "analytics.html")
        if path in ("/deactivate", "/deactivate.html"):
            return self._file(WEB_DIR / "deactivate.html")

        if path == "/api/status":
            return self._json(200, _api_status())
        if path == "/api/titles":
            from modules.products import titles as title_mod
            items = title_mod.load_queue("pending")
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/titles/scan/status":
            return self._json(200, {"ok": True, **_scan_status()})
        if path == "/api/titles/push/status":
            return self._json(200, {"ok": True, **_push_status()})
        if path == "/api/analytics/summary":
            from modules.products import analytics as analytics_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            return self._json(200, {"ok": True, **analytics_mod.summary(region=region)})
        if path == "/api/analytics/products":
            from modules.products import analytics as analytics_mod
            q = parse_qs(urlparse(self.path).query)
            segment = (q.get("segment") or [None])[0]
            region = (q.get("region") or [None])[0]
            items = analytics_mod.load_analytics(segment=segment, region=region)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/analytics/sync/status":
            return self._json(200, {"ok": True, **_analytics_sync_status()})
        if path == "/api/deactivate":
            from modules.products import deactivate as deact_mod
            items = deact_mod.load_queue("pending")
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/deactivate/scan/status":
            return self._json(200, {"ok": True, **_deact_scan_status()})
        if path == "/api/deactivate/push/status":
            return self._json(200, {"ok": True, **_deact_push_status()})
        if path == "/api/promotions":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            act_filter = (q.get("action") or [None])[0]
            region_filter = (q.get("region") or [None])[0]
            items = promo_mod.load_queue(
                "pending", action=act_filter, region=region_filter
            )
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/promotions/activities":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            region_filter = (q.get("region") or [None])[0]
            try:
                acts = promo_mod.list_ongoing_by_shop(region=region_filter)
                return self._json(200, {"ok": True, "activities": acts})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/promotions/scan/status":
            return self._json(200, {"ok": True, **_promo_scan_status()})
        if path == "/api/promotions/push/status":
            return self._json(200, {"ok": True, **_promo_push_status()})
        if path == "/api/promotions/coupons":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            try:
                coupons = promo_mod.list_coupons(region=region)
                return self._json(200, {"ok": True, "coupons": coupons})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/promotions/coupon-drafts":
            from modules.products import promotions as promo_mod
            drafts = promo_mod.load_coupon_drafts()
            return self._json(200, {"ok": True, "drafts": drafts})
        if path == "/api/costs/export.csv":
            out = ROOT / "exports" / "sku_costs.csv"
            cost_mod.export_csv(out)
            return self._file(out)

        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self._read_json()
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "error": "invalid json"})

        if path == "/api/costs":
            try:
                saved = cost_mod.save_costs_bulk(data.get("costs") or [])
                self._json(200, {"ok": True, "saved": saved})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/titles/scan":
            ok, msg = _start_scan(
                days=int(data.get("days") or 30),
                max_units=int(data.get("max_units", 1)),
                limit=int(data.get("limit") or 30),
                region=data.get("region") or None,
                mode=data.get("mode") or "velocity",
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/analytics/sync":
            ok, msg = _start_analytics_sync(data.get("region") or None)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/deactivate/scan":
            ok, msg = _start_deact_scan(
                limit=int(data.get("limit") or 50),
                region=data.get("region") or None,
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/deactivate/push":
            items = data.get("items") or []
            ok, msg = _start_deact_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/titles/push":
            items = data.get("items") or []
            ok, msg = _start_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/promotions/scan":
            ok, msg = _start_promo_scan(
                days=int(data.get("days") or 30),
                max_units=int(data.get("max_units", 1)),
                limit=int(data.get("limit") or 30),
                region=data.get("region") or None,
                scope=data.get("scope") or "adjust",
                mode=data.get("mode") or "velocity",
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/promotions/coupons/scan":
            from modules.products import promotions as promo_mod
            try:
                n = promo_mod.scan_coupon_suggestions(
                    region=data.get("region") or None,
                    limit=int(data.get("limit") or 4),
                )
                self._json(200, {"ok": True, "count": n})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/promotions/coupon-drafts/mark":
            from modules.products import promotions as promo_mod
            draft_id = int(data.get("id") or 0)
            if draft_id:
                promo_mod.mark_coupon_draft_used(draft_id)
            self._json(200, {"ok": True})
            return

        if path == "/api/promotions/push":
            items = data.get("items") or []
            ok, msg = _start_promo_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        self.send_error(404)


def serve(port: int = DEFAULT_PORT, open_browser: bool = True, page: str = "index"):
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_DIR / "static").mkdir(parents=True, exist_ok=True)

    if not (WEB_DIR / "costs.html").is_file():
        from modules.products.build_page import build_html
        build_html()

    server = HTTPServer(("127.0.0.1", port), Handler)
    routes = {
        "index": "/",
        "costs": "/costs",
        "titles": "/titles",
        "promotions": "/promotions",
        "analytics": "/analytics",
        "deactivate": "/deactivate",
    }
    url = f"http://127.0.0.1:{port}{routes.get(page, '/')}"
    print(f"  ✅ 控制台: http://127.0.0.1:{port}/")
    print(f"  Listing 优化: http://127.0.0.1:{port}/titles")
    print(f"  Analytics: http://127.0.0.1:{port}/analytics")
    print(f"  零销下架: http://127.0.0.1:{port}/deactivate")
    print(f"  促销调价: http://127.0.0.1:{port}/promotions")
    print(f"  成本维护: http://127.0.0.1:{port}/costs")
    print("  Ctrl+C 停止")

    if open_browser:
        import webbrowser
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
        server.server_close()
