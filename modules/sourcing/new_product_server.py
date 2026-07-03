from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.config import ROOT

WEB_DIR = ROOT / "web"
STATIC_DIR = WEB_DIR / "static"
DEFAULT_PORT = 8766


def _guess_type(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


class NewProductHandler(BaseHTTPRequestHandler):
    server_version = "OrbitHiveNewProduct/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _bytes(self, code: int, raw: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        self._bytes(200, path.read_bytes(), _guess_type(path))

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _dispatch_preview(self, payload: dict, *, allow_precollect: bool) -> None:
        from modules.sourcing import new_product_workbench as np_mod

        raw = str(payload.get("url") or payload.get("offer_id") or "").strip()
        if not raw:
            self._json(400, {"ok": False, "error": "missing url or offer_id"})
            return
        try:
            if allow_precollect and payload.get("precollect"):
                urls = payload.get("overseas_urls") or []
                if isinstance(urls, str):
                    urls = [x.strip() for x in urls.replace("\r", "\n").split("\n") if x.strip()]
                result = np_mod.precollect_preview(
                    raw,
                    overseas_urls=list(urls),
                    source_code=str(payload.get("source_code") or ""),
                    force=bool(payload.get("force")),
                )
            else:
                result = np_mod.build_preview(raw, source_code=str(payload.get("source_code") or ""))
            self._json(200, result)
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/new-product", "/new-product.html"):
            return self._file(WEB_DIR / "new_product.html")
        if path.startswith("/static/"):
            rel = path[len("/static/"):].strip("/")
            return self._file(STATIC_DIR / rel)
        if path == "/health":
            return self._json(200, {"ok": True, "service": "new_product", "port_default": DEFAULT_PORT})
        if path == "/api/new-product/preview":
            q = parse_qs(parsed.query)
            raw = (q.get("offer_id") or q.get("url") or [""])[0]
            return self._dispatch_preview({"offer_id": raw}, allow_precollect=False)
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._body_json()

        if path == "/api/new-product/preview":
            return self._dispatch_preview(data, allow_precollect=True)

        from modules.sourcing import new_product_workbench as np_mod

        raw = str(data.get("offer_id") or data.get("url") or "").strip()

        if path == "/api/new-product/review":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.save_review(raw, data.get("review") or {}))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/image-request":
            prompt = str(data.get("prompt") or "").strip()
            if not raw or not prompt:
                return self._json(400, {"ok": False, "error": "missing offer_id or prompt"})
            try:
                return self._json(200, np_mod.add_image_request(raw, prompt, kind=str(data.get("kind") or "supplement")))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/miaoshou-draft":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.prepare_miaoshou_draft(raw))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/miaoshou-draft/commit":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.write_miaoshou_draft(raw))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/miaoshou-second-review/continue":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.start_claim_miaoshou_to_tiktok(raw))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/site-drafts/prepare":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.prepare_miaoshou_site_drafts(raw))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/sku-numbering/fix":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.ensure_common_sequential_skus(raw))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/overseas-source":
            overseas_url = str(data.get("overseas_url") or "").strip()
            if not raw or not overseas_url:
                return self._json(400, {"ok": False, "error": "missing offer_id or overseas_url"})
            try:
                return self._json(200, np_mod.add_overseas_source(raw, overseas_url, fetch=bool(data.get("fetch"))))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/overseas-sources":
            urls = data.get("overseas_urls") or []
            if isinstance(urls, str):
                urls = [x.strip() for x in urls.replace("\r", "\n").split("\n") if x.strip()]
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.save_overseas_sources(raw, list(urls), fetch=bool(data.get("fetch"))))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        self.send_error(404)


def serve(port: int = DEFAULT_PORT) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), NewProductHandler)
    print(f"New product workbench: http://127.0.0.1:{port}/")
    httpd.serve_forever()
