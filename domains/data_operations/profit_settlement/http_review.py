"""Narrow local HTTP bridge to the captured report consumer."""
import json
import socket
import time


def _reject_unread_request(handler, status, code):
    """Send the rejection before bounded draining prevents an unread-body RST."""
    handler.close_connection = True
    handler._json(status, {'status': 'check_failed', 'error': {'code': code}})
    handler.wfile.flush()
    connection = handler.connection
    previous_timeout = connection.gettimeout()
    try:
        connection.shutdown(socket.SHUT_WR)
        # Never wait for an untrusted declared length or process rejected data.
        deadline = time.monotonic() + 0.1
        remaining = 64 * 1024 + 1
        while remaining and (timeout := deadline - time.monotonic()) > 0:
            connection.settimeout(timeout)
            chunk = handler.rfile.read1(min(remaining, 8192))
            if not chunk:
                break
            remaining -= len(chunk)
    except OSError:
        pass
    finally:
        connection.settimeout(previous_timeout)


def handle_captured_review(handler, *, method: str, body_limit: int):
    port = int(handler.server.server_address[1])
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    if handler.headers.get('Host') not in hosts:
        return _reject_unread_request(handler, 403, 'invalid_local_host')
    origin = handler.headers.get('Origin')
    if origin and origin not in {'http://' + host for host in hosts}:
        return _reject_unread_request(handler, 403, 'cross_origin_rejected')
    if method == 'GET':
        return handler._json(200, {'schema_version': 'profit-captured-capability/v1', 'status': 'input_required',
                                  'mode': 'captured_review', 'action': 'POST selected profit-captured-profile/v1',
                                  'network_reads_performed': [], 'external_writes_performed': []})
    if 'application/json' not in (handler.headers.get('Content-Type') or '').lower():
        return _reject_unread_request(handler, 415, 'json_required')
    try:
        lengths = handler.headers.get_all('Content-Length') or []
        length = int(lengths[0]) if len(lengths) == 1 else -1
    except ValueError:
        length = -1
    if length < 0 or handler.headers.get('Transfer-Encoding'):
        return _reject_unread_request(handler, 400, 'invalid_content_length')
    if length > body_limit:
        return _reject_unread_request(handler, 413, 'body_too_large')
    try:
        data = handler._read_json()
    except (UnicodeDecodeError, json.JSONDecodeError):
        return handler._json(400, {'status': 'check_failed', 'error': {'code': 'invalid_json'}})
    if not isinstance(data, dict) or not isinstance(data.get('profile'), dict):
        return handler._json(400, {'status': 'check_failed', 'error': {'code': 'captured_profile_required'}})
    from .captured_consumer import build_captured_weekly
    if data.get('mode') == 'sku':
        from .captured_sku import build_captured_sku
        payload = build_captured_sku(data['profile'], data.get('sku_identity'))
    else:
        payload = build_captured_weekly(data['profile'])
    return handler._json(400 if payload['status'] == 'check_failed' else 200, payload)
