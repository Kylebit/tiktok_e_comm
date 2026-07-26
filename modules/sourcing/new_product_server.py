from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from core.config import ROOT

WEB_DIR = ROOT / "web"
STATIC_DIR = WEB_DIR / "static"
DEFAULT_PORT = 8766
IMAGE_CACHE_DIR = ROOT / "data" / "new_product_image_cache"


def require_explicit_confirmation(payload: dict, key: str, action: str) -> None:
    """Reject business writes unless the caller sends a literal JSON true."""
    if payload.get(key) is not True:
        raise ValueError(f"explicit {action} confirmation is required")


def _guess_type(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _guess_remote_type(url: str, header_type: str | None) -> str:
    ctype = (header_type or "").split(";")[0].strip().lower()
    if ctype.startswith("image/"):
        return ctype
    guessed = mimetypes.guess_type(urlparse(url).path)[0]
    return guessed or "image/jpeg"


def _cache_ext(content_type: str, path: str) -> str:
    ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"
    if not ext:
        suffix = Path(path).suffix.lower()
        ext = suffix if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp") else ".jpg"
    return ext


def _image_cache_path(url: str, content_type: str = "") -> Path:
    digest = hashlib.sha1(url.encode("utf-8", errors="replace")).hexdigest()
    ext = _cache_ext(content_type, urlparse(url).path)
    return IMAGE_CACHE_DIR / f"{digest}{ext}"


def _referer_for(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    # 1688 / 阿里 CDN 防盗链：用业务站 Referer，而不是 CDN 自身域名
    if "alicdn.com" in host or "1688.com" in host or host.startswith("cbu"):
        return "https://detail.1688.com/"
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return "https://www.1688.com/"


def _download_remote_image(url: str) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http/https image URLs are supported")

    cached = _image_cache_path(url)
    for existing in IMAGE_CACHE_DIR.glob(cached.stem + ".*"):
        if existing.is_file() and existing.stat().st_size > 0:
            ctype = mimetypes.guess_type(str(existing))[0] or "image/jpeg"
            return existing.read_bytes(), ctype

    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": _referer_for(url),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    data = b""
    content_type = "image/jpeg"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                content_type = _guess_remote_type(url, resp.headers.get("Content-Type"))
                data = resp.read(12 * 1024 * 1024)
            if not data:
                raise ValueError("empty image body")
            break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_err = exc
            if attempt >= 2:
                raise
            time.sleep(0.25 * (attempt + 1))
    if not data:
        raise last_err or RuntimeError("image download failed")
    fp = _image_cache_path(url, content_type)
    fp.write_bytes(data)
    return data, content_type


def _placeholder_svg(message: str = "image unavailable") -> bytes:
    safe = (
        str(message)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320">'
        '<rect width="100%" height="100%" fill="#f1f5f9"/>'
        f'<text x="50%" y="50%" text-anchor="middle" fill="#64748b" font-size="14">{safe}</text>'
        "</svg>"
    ).encode("utf-8")


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

    def _bytes(
        self,
        code: int,
        raw: bytes,
        content_type: str,
        *,
        cache_seconds: int | None = None,
    ) -> None:
        self.send_response(code)
        if content_type.startswith("text/") or content_type.startswith("application/json") or "html" in content_type:
            if "charset=" not in content_type.lower():
                content_type = f"{content_type}; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if cache_seconds is not None:
            self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
        self.end_headers()
        self.wfile.write(raw)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        ctype = _guess_type(path)
        if path.suffix.lower() in (".html", ".htm", ".js", ".css", ".json", ".svg"):
            if "charset=" not in ctype.lower():
                ctype = f"{ctype}; charset=utf-8"
        self._bytes(200, path.read_bytes(), ctype)

    def _proxy_image(self, raw_url: str) -> None:
        url = unquote((raw_url or "").strip())
        if not url:
            return self._json(400, {"ok": False, "error": "missing url"})
        try:
            data, ctype = _download_remote_image(url)
            return self._bytes(200, data, ctype, cache_seconds=86400)
        except (ValueError, urllib.error.URLError, TimeoutError, OSError) as exc:
            return self._bytes(
                200,
                _placeholder_svg(f"image unavailable: {exc}"),
                "image/svg+xml; charset=utf-8",
                cache_seconds=60,
            )

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _dispatch_preview(self, payload: dict, *, allow_precollect: bool) -> None:
        if allow_precollect and payload.get("precollect"):
            try:
                require_explicit_confirmation(
                    payload,
                    "confirm_miaoshou_precollect",
                    "Miaoshou pre-collection",
                )
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
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
            rel = path[len("/static/") :].strip("/")
            return self._file(STATIC_DIR / rel)
        if path == "/health":
            return self._json(200, {"ok": True, "service": "new_product", "port_default": DEFAULT_PORT})
        if path == "/api/exchange-rates":
            q = parse_qs(parsed.query)
            force = (q.get("refresh") or q.get("force") or ["0"])[0].strip().lower() in (
                "1",
                "true",
                "yes",
            )
            try:
                from modules.sourcing.fx_rates import get_exchange_rates

                return self._json(200, get_exchange_rates(force_refresh=force))
            except Exception as exc:
                return self._json(500, {"ok": False, "error": str(exc)})
        if path == "/api/proxy-image":
            q = parse_qs(parsed.query)
            return self._proxy_image((q.get("url") or [""])[0])
        if path == "/api/new-product/preview":
            q = parse_qs(parsed.query)
            raw = (q.get("offer_id") or q.get("url") or [""])[0]
            return self._dispatch_preview({"offer_id": raw}, allow_precollect=False)
        if path in ("/api/new-product/content-report", "/api/new-product/content-image"):
            q = parse_qs(parsed.query)
            raw = (q.get("offer_id") or [""])[0]
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                from modules.sourcing import new_product_workbench as np_mod

                file_path = np_mod.content_package_file(
                    raw,
                    artifact_id=(q.get("artifact_id") or [""])[0],
                    report=path.endswith("content-report"),
                )
                if not file_path:
                    return self._json(404, {"ok": False, "error": "content package file not found"})
                if path.endswith("content-report"):
                    raw_html = file_path.read_text(encoding="utf-8")

                    def rewrite_generated_src(match: re.Match[str]) -> str:
                        image_name = match.group(2)
                        artifact_id = Path(image_name).stem
                        return (
                            'src="/api/new-product/content-image?offer_id='
                            + urllib.parse.quote(raw)
                            + "&artifact_id="
                            + urllib.parse.quote(artifact_id)
                            + '"'
                        )

                    raw_html = re.sub(
                        r"src=([\"'])generated/([^\"']+)\1",
                        rewrite_generated_src,
                        raw_html,
                    )
                    return self._bytes(200, raw_html.encode("utf-8"), "text/html; charset=utf-8")
                return self._file(file_path)
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
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

        if path == "/api/new-product/content-package/sync":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                package = np_mod.sync_content_package(raw, collect_box_id=str(data.get("collect_box_id") or ""))
                return self._json(200, {"ok": True, "content_package": package})
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/prepare":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                result = np_mod.prepare_content_package(
                    raw,
                    collect_box_id=str(data.get("collect_box_id") or ""),
                )
                return self._json(200, {"ok": True, **result})
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/vision-proposal":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                require_explicit_confirmation(data, "confirm_ai_planning", "AI planning")
            except ValueError as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            refs = data.get("reference_urls") or []
            if not isinstance(refs, list):
                return self._json(400, {"ok": False, "error": "reference_urls must be a list"})
            storyboard_feedback = data.get("storyboard_feedback") or {}
            if not isinstance(storyboard_feedback, dict):
                return self._json(400, {"ok": False, "error": "storyboard_feedback must be an object"})
            try:
                result = np_mod.propose_content_package_with_vision(
                    raw,
                    reference_urls=refs,
                    storyboard_feedback=storyboard_feedback,
                )
                return self._json(200, {"ok": True, **result})
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/review":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                package = np_mod.save_content_package_review(raw, data.get("review") or {})
                return self._json(200, {"ok": True, "content_package": package})
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/first-image-preflight":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                result = np_mod.prepare_first_image_generation(raw)
                return self._json(200, result)
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/first-image-generate":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            if data.get("confirm_paid_generation") is not True:
                return self._json(400, {"ok": False, "error": "explicit paid generation confirmation is required"})
            try:
                result = np_mod.start_first_image_generation(raw)
                return self._json(200, result)
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/remaining-images-preflight":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                result = np_mod.prepare_remaining_image_generations(raw)
                return self._json(200, result)
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/suite-images-preflight":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            force_shot_ids = data.get("force_shot_ids") or []
            if not isinstance(force_shot_ids, list):
                return self._json(400, {"ok": False, "error": "force_shot_ids must be a list"})
            try:
                result = np_mod.prepare_suite_image_generations(
                    raw,
                    force_shot_ids=[str(value) for value in force_shot_ids],
                )
                return self._json(200, result)
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/remaining-images-generate":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            if data.get("confirm_paid_generation") is not True:
                return self._json(400, {"ok": False, "error": "explicit paid generation confirmation is required"})
            try:
                result = np_mod.start_remaining_image_generation(
                    raw,
                    retry_failed_only=bool(data.get("retry_failed_only")),
                )
                return self._json(200, result)
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/miaoshou-images/commit":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            if data.get("confirm_miaoshou_write") is not True:
                return self._json(400, {"ok": False, "error": "explicit Miaoshou write confirmation is required"})
            try:
                return self._json(200, np_mod.write_ordered_images_to_miaoshou(raw))
            except Exception as exc:
                try:
                    sync = (
                        np_mod.content_package_summary(raw).get(
                            "miaoshou_generated_images_write"
                        )
                        or {}
                    )
                except Exception:
                    sync = {}
                status = 409 if "已在进行中" in str(exc) else 400
                return self._json(
                    status,
                    {
                        "ok": False,
                        "error": str(exc),
                        "sync": sync,
                        "claimed": False,
                        "published": False,
                    },
                )

        if path == "/api/new-product/content-package/generated-image/sync":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                require_explicit_confirmation(data, "confirm_miaoshou_write", "Miaoshou write")
            except ValueError as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            try:
                return self._json(200, np_mod.sync_generated_image_to_miaoshou(
                    raw,
                    str(data.get("artifact_id") or ""),
                    str(data.get("action") or ""),
                ))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/content-package/generated-image/decision":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.save_generated_image_decision(
                    raw,
                    str(data.get("artifact_id") or ""),
                    str(data.get("action") or ""),
                ))
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
                require_explicit_confirmation(data, "confirm_miaoshou_write", "Miaoshou write")
            except ValueError as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            try:
                return self._json(200, np_mod.write_miaoshou_draft(raw))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/miaoshou-second-review/continue":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                require_explicit_confirmation(data, "confirm_tiktok_claim", "TikTok collection-box claim")
            except ValueError as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            try:
                return self._json(200, np_mod.start_claim_miaoshou_to_tiktok(raw))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/site-drafts/prepare":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                require_explicit_confirmation(data, "confirm_site_draft_write", "site-draft write")
            except ValueError as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            try:
                return self._json(200, np_mod.prepare_miaoshou_site_drafts(raw))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": str(exc)})

        if path == "/api/new-product/sku-numbering/fix":
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                require_explicit_confirmation(data, "confirm_miaoshou_write", "Miaoshou write")
            except ValueError as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
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
