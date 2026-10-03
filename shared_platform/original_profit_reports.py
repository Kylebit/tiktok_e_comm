"""Exact, read-only allowlist for the user's retained July report originals."""
import hashlib
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

PREFIX = '/profit-original/artifacts/'
INDEX = PREFIX + 'profit_reports_monthly/2026-07/index.html'
ASSET_ROOT = None

def serve(handler, request_path, root):
    path = unquote(urlsplit(request_path).path)
    if path in ('/profit', '/profit.html'):
        return handler._redirect(INDEX, code=302)
    if not path.startswith(PREFIX):
        return False
    key = path[len(PREFIX):]
    manifest = json.loads((root / 'config/original_profit_reports.json').read_text(encoding='utf-8'))
    if key not in manifest['files'] or ASSET_ROOT is None:
        handler.send_error(404)
        return True
    base = Path(ASSET_ROOT).resolve()
    file = base / key
    if file.is_symlink() or not file.resolve().is_relative_to(base) or not file.is_file():
        handler.send_error(404)
        return True
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['files'][key]['sha256']:
        handler.send_error(409, 'Original report checksum mismatch')
        return True
    handler.send_response(200)
    handler.send_header('Content-Type', mimetypes.guess_type(key)[0] or 'application/octet-stream')
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src https: data:; sandbox allow-scripts")
    handler.end_headers()
    handler.wfile.write(raw)
    return True
