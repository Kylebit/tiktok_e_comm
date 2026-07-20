from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.config import ROOT

WEB_DIR = ROOT / "web"
STATIC_DIR = WEB_DIR / "static"
DEFAULT_PORT = 8767


def _guess_type(path: Path) -> str:
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if ctype.startswith("text/") and "charset=" not in ctype:
        ctype += "; charset=utf-8"
    return ctype


class OrbitRusHandler(BaseHTTPRequestHandler):
    server_version = "OrbitRus/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def _bytes(self, code: int, raw: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        self._bytes(200, path.read_bytes(), _guess_type(path))

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _handle_rus_api(self, method: str) -> bool:
        path = urlparse(self.path).path
        if path.startswith("/api/ozon/"):
            subpath = path[len("/api/ozon/") :].split("?")[0]
        elif path.startswith("/api/rus/"):
            subpath = path[len("/api/rus/") :].split("?")[0]
        else:
            return False

        query = urlparse(self.path).query
        body = self._read_body() if method == "POST" else None

        try:
            if method == "GET" and subpath == "unmigrated":
                from modules.ozon.catalog_source import list_unmigrated_from_catalog

                return self._json(200, list_unmigrated_from_catalog())
            if method == "GET" and subpath.startswith("draft/"):
                from urllib.parse import unquote

                from modules.ozon.catalog_draft import build_draft

                seller_sku = unquote(subpath[len("draft/") :])
                draft = build_draft(seller_sku)
                if draft.get("error") and not draft.get("draft_title"):
                    return self._json(404, draft)
                return self._json(200, draft)
            if method == "GET" and subpath == "category_options":
                from modules.ozon.category_match import load_category_options
                from modules.ozon.migrate_attrs import BUILTIN_TYPE_PROFILES
                from modules.ozon.tk_category_map import load_map

                type_profiles = {str(k): v for k, v in BUILTIN_TYPE_PROFILES.items()}
                type_profiles.update(load_map().get("type_profiles") or {})
                return self._json(200, {"options": load_category_options(), "type_profiles": type_profiles})
            if method == "GET" and subpath == "pending_drafts":
                from modules.ozon.pending_drafts import list_pending

                return self._json(200, {"drafts": list_pending()})
            if method == "POST" and subpath == "pending_drafts":
                from modules.ozon.pending_drafts import save_pending

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                return self._json(200, {"saved": save_pending(payload)})
            if method == "POST" and subpath == "pending_drafts/delete":
                from modules.ozon.pending_drafts import delete_pending

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                ok = delete_pending(payload.get("seller_sku") or "")
                return self._json(200, {"deleted": ok})
            if method == "POST" and subpath == "dismiss":
                from modules.ozon.pending_drafts import add_dismissed

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                rec = add_dismissed(
                    payload.get("seller_sku") or "",
                    payload.get("tk_id") or "",
                    payload.get("reason") or "",
                )
                return self._json(200, {"dismissed": rec})
            if method == "GET" and subpath == "dismissed":
                from modules.ozon.pending_drafts import list_dismissed

                return self._json(200, {"dismissed": list_dismissed()})
            if method == "GET" and subpath == "settlement_summary":
                from modules.ozon.settlement import build_settlement_summary

                q = parse_qs(query or "")
                months_back = int((q.get("months") or ["3"])[0])
                weeks = q.get("weeks") or q.get("weeks_back")
                weeks_back = int(weeks[0]) if weeks else None
                only_settled = (q.get("only_settled") or ["1"])[0] in ("1", "true", "True")
                force_fx = (q.get("refresh_fx") or ["0"])[0] in ("1", "true", "True")
                return self._json(
                    200,
                    build_settlement_summary(
                        months_back,
                        only_settled,
                        weeks_back=weeks_back,
                        force_fx_refresh=force_fx,
                    ),
                )
            if method == "GET" and subpath == "profit_table":
                from modules.ozon.profit_analysis import build_profit_table
                from modules.ozon.pending_drafts import dismissed_offer_ids

                q = parse_qs(query or "")
                target_margin = float((q.get("target_margin") or ["0.05"])[0])
                excluded = dismissed_offer_ids()
                return self._json(200, build_profit_table(target_margin, excluded_offer_ids=excluded))
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})
            return True

        from modules.ozon.webapp_bridge import proxy_request

        try:
            status, data, ctype = proxy_request(method, subpath, query=query or None, body=body)
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})
            return True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if self._handle_rus_api("GET"):
            return
        if path in ("/", "/rus", "/rus.html", "/ozon", "/ozon.html"):
            return self._file(WEB_DIR / "ozon.html")
        if path.startswith("/static/"):
            rel = path[len("/static/") :].strip("/")
            return self._file(STATIC_DIR / rel)
        if path == "/health":
            return self._json(200, {"ok": True, "service": "orbit_rus", "port_default": DEFAULT_PORT})
        self.send_error(404)

    def do_POST(self) -> None:
        if self._handle_rus_api("POST"):
            return
        self.send_error(404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()


def serve(port: int = DEFAULT_PORT) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), OrbitRusHandler)
    print(f"Orbit Rus: http://127.0.0.1:{port}/")
    httpd.serve_forever()
