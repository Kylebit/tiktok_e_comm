#!/usr/bin/env python3
"""Typed DuoPlus OpenAPI client, with offline previews and durable app installation.

Migrated from the preserved user Skill. No personal configuration or registry fallback.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib import error, parse, request
from modules.tools.provider_http import open_request


DEFAULT_BASE_URL = "https://openapi.duoplus.cn"
ALLOWED_HOSTS = {"openapi.duoplus.cn", "openapi.duoplus.net"}
SECRET_KEYS = {
    "adb_password",
    "address",
    "android_id",
    "api_key",
    "authorization",
    "bssid",
    "duoplus-api-key",
    "gaid",
    "gsf_id",
    "iccid",
    "imei",
    "mac",
    "msin",
    "msisdn",
    "password",
    "secret",
    "serialno",
    "token",
}


class DuoPlusError(RuntimeError):
    """A user-actionable DuoPlus client error."""


def _validated_base_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = parse.urlparse(candidate)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)):
        raise DuoPlusError(
            "DUOPLUS_BASE_URL must be https://openapi.duoplus.cn "
            "or https://openapi.duoplus.net"
        )
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise DuoPlusError("DUOPLUS_BASE_URL must not contain a path, query, or fragment")
    return candidate


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            result[key] = "***REDACTED***" if normalized in SECRET_KEYS else _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _load_api_key() -> str:
    """Load the key without printing it or placing it in command arguments."""
    process_value = os.environ.get("DUOPLUS_API_KEY", "").strip()
    if process_value:
        return process_value
    return ""


class DuoPlusClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        lang: str = "zh",
        timeout: float = 20.0,
    ) -> None:
        if not api_key.strip():
            raise DuoPlusError(
                "DUOPLUS_API_KEY is not set. Set it locally; do not paste it into chat."
            )
        self._api_key = api_key.strip()
        self.base_url = _validated_base_url(base_url)
        self.lang = lang
        self.timeout = timeout
        self._last_request_at: Optional[float] = None

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            remaining = 1.0 - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)

    def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if path not in {"/api/v1/cloudPhone/list", "/api/v1/cloudPhone/status", "/api/v1/cloudPhone/info",
                        "/api/v1/app/list", "/api/v1/app/installedList", "/api/v1/app/install"}:
            raise DuoPlusError("Refusing an unexpected API path")
        self._throttle()
        url = self.base_url + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Lang": self.lang,
                "DuoPlus-API-Key": self._api_key,
                "User-Agent": "Orbit-Portable-DuoPlus/1.0",
            },
        )
        try:
            with open_request(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise DuoPlusError(f"DuoPlus HTTP {exc.code}; preserve operation state before retry") from None
        except error.URLError as exc:
            raise DuoPlusError("DuoPlus transport failed; outcome may be unknown") from None
        finally:
            self._last_request_at = time.monotonic()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DuoPlusError("DuoPlus returned non-JSON data") from exc
        if not isinstance(result, dict):
            raise DuoPlusError("DuoPlus returned an unexpected JSON shape")
        if result.get("code") != 200:
            raise DuoPlusError("DuoPlus API returned a non-success code")
        return _redact(result)

    def devices(self, page: int = 1, pagesize: int = 20) -> Dict[str, Any]:
        if page < 1:
            raise DuoPlusError("page must be at least 1")
        if not 1 <= pagesize <= 100:
            raise DuoPlusError("pagesize must be between 1 and 100")
        return self.post(
            "/api/v1/cloudPhone/list",
            {"page": page, "pagesize": pagesize},
        )

    def status(self, image_ids: Iterable[str]) -> Dict[str, Any]:
        ids = [item.strip() for item in image_ids if item.strip()]
        if not ids:
            raise DuoPlusError("At least one device ID is required")
        if len(ids) > 100:
            raise DuoPlusError("Refusing to query more than 100 device IDs at once")
        return self.post("/api/v1/cloudPhone/status", {"image_ids": ids})

    def info(self, image_id: str) -> Dict[str, Any]:
        device_id = image_id.strip()
        if not device_id:
            raise DuoPlusError("A device ID is required")
        return self.post("/api/v1/cloudPhone/info", {"image_id": device_id})

    def apps(self, page: int = 1, pagesize: int = 100) -> Dict[str, Any]:
        if page < 1:
            raise DuoPlusError("page must be at least 1")
        if not 1 <= pagesize <= 100:
            raise DuoPlusError("pagesize must be between 1 and 100")
        return self.post("/api/v1/app/list", {"page": page, "pagesize": pagesize})

    def installed_apps(self, image_id: str) -> Dict[str, Any]:
        device_id = image_id.strip()
        if not device_id:
            raise DuoPlusError("A device ID is required")
        return self.post("/api/v1/app/installedList", {"image_id": device_id})

    def install_app(
        self, image_ids: Iterable[str], app_id: str, app_version_id: str = ""
    ) -> Dict[str, Any]:
        ids = [item.strip() for item in image_ids if item.strip()]
        if not ids or len(ids) > 20:
            raise DuoPlusError("install-app requires between 1 and 20 device IDs")
        application_id = app_id.strip()
        if not application_id:
            raise DuoPlusError("An app ID is required")
        payload: Dict[str, Any] = {"image_ids": ids, "app_id": application_id}
        if app_version_id.strip():
            payload["app_version_id"] = app_version_id.strip()
        return {"method": "POST", "url": self.base_url + "/api/v1/app/install",
                "payload": payload, "network_call_performed": False,
                "next_action": "BIND_APP_VERSION_PACKAGE_AND_EXISTING_AUTHORIZATION"}


def install_scope(*, tenant_id: str, profile_digest: str, origin: str,
                  image_ids: list[str], app_id: str, app_version_id: str, package: str) -> dict:
    """Freeze provider identifiers; no installed-list response can prove a version."""
    import re
    if (not re.fullmatch(r'[a-f0-9]{64}', profile_digest)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tenant_id)):
        raise DuoPlusError('tenant/profile identity is invalid')
    ids = sorted(set(image_ids))
    if (not 1 <= len(ids) <= 20 or len(ids) != len(image_ids)
            or any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in ids)
            or not app_id.strip() or not app_version_id.strip()
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+', package)):
        raise DuoPlusError('exact devices, app ID, explicit version ID and package are required')
    return {'tenant_id': tenant_id, 'profile_digest': profile_digest,
            'origin': _validated_base_url(origin), 'action': 'install-app',
            'image_ids': ids, 'app_id': app_id.strip(), 'app_version_id': app_version_id.strip(), 'package': package}


def execute_install(client: DuoPlusClient, *, artifact_root: Path, scope: dict,
                    authorization: dict, reconcile_only: bool = False) -> dict:
    """Consume existing scope authorization, submit once and read back packages.

    The caller attests the instruction reference. This function verifies bindings,
    not the human's identity. Unknown submissions are never replayed; installedList
    proves package presence only, so it cannot settle an unknown version/install.
    """
    import re
    from modules.sourcing.image_generation_checkpoint import atomic_json, business_lock, digest
    from shared_platform.capability_runtime import checked_path
    expected = install_scope(**{key: scope[key] for key in
        ('tenant_id', 'profile_digest', 'origin', 'image_ids', 'app_id', 'app_version_id', 'package')})
    if expected != scope or client.base_url != scope['origin']:
        raise DuoPlusError('installation scope/provider differs')
    if (authorization.get('scope') != scope or not authorization.get('authorization_id')
            or not re.fullmatch(r'(?:audit|fixture)://[A-Za-z0-9_./:-]{1,220}', str(authorization.get('instruction_ref', '')))):
        raise DuoPlusError('existing authorization must bind the complete installation scope and instruction reference')
    directory = checked_path(Path(artifact_root), 'duoplus/' + scope['profile_digest'])
    lock_digest = digest({'tenant': scope['tenant_id'], 'provider': scope['origin']})
    def check_files():
        for name in ('installs.json', 'installs.json.tmp', f'.lingshi-{lock_digest[:24]}.lock'):
            checked_path(directory, name)
    check_files()
    path = checked_path(directory, 'installs.json')
    key = digest(scope)
    with business_lock(directory, lock_digest):
        check_files()
        state = {'schema': 'duoplus-install-ledger/v1', 'profile_digest': scope['profile_digest'], 'requests': {}}
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding='utf-8'))
                unsigned = {k: v for k, v in state.items() if k != 'digest'}
                if (state['digest'] != digest(unsigned) or state['schema'] != 'duoplus-install-ledger/v1'
                        or state['profile_digest'] != scope['profile_digest']): raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise DuoPlusError('installation ledger is damaged; preserve it and reconcile') from None
        def save():
            check_files()
            state['digest'] = digest({k: v for k, v in state.items() if k != 'digest'})
            atomic_json(path, state)
        row = state['requests'].get(key)
        if row and row['scope'] != scope: raise DuoPlusError('installation request digest collision')
        for peer_key, peer in state['requests'].items():
            if (peer_key != key and set(peer['scope']['image_ids']) & set(scope['image_ids'])
                    and peer['status'] in {'SUBMITTING', 'UNKNOWN', 'ACKNOWLEDGED'}):
                raise DuoPlusError('a device has an unresolved installation; reconcile its original scope')
        new_submission = row is None
        if row is None:
            if reconcile_only: raise DuoPlusError('no original installation to reconcile')
            found = None
            for page in range(1, 21):
                response = client.apps(page=page, pagesize=100)
                data = response.get('data', {})
                for app in data.get('list', []):
                    if app.get('id') == scope['app_id']:
                        found = app
                if found or page >= data.get('total_page', 1): break
            if (not found or found.get('pkg') != scope['package']
                    or scope['app_version_id'] not in {v.get('id') for v in found.get('version_list', [])}):
                raise DuoPlusError('official app list does not bind the exact app/version/package')
            row = {'scope': scope, 'authorization_digest': digest(authorization),
                   'instruction_ref': authorization['instruction_ref'], 'status': 'SUBMITTING',
                   'request_attempted': True, 'install_request_count': 1, 'readbacks': []}
            state['requests'][key] = row
            save()  # Durable before the potentially mutating call, including process death.
            try:
                response = client.post('/api/v1/app/install', {k: scope[k] for k in ('image_ids', 'app_id', 'app_version_id')})
                if response.get('code') != 200: raise DuoPlusError('installation response was not acknowledged')
                row.update(status='ACKNOWLEDGED', provider_response_digest=digest(response))
                save()
            except Exception:
                row['status'] = 'UNKNOWN'; save()
                raise DuoPlusError('installation outcome unknown; use reconcile-install, never repeat install') from None
        readbacks = []
        for device in scope['image_ids']:
            response = client.installed_apps(device)
            packages = response.get('data', {}).get('list')
            if not isinstance(packages, list) or any(not isinstance(v, str) for v in packages):
                raise DuoPlusError('installed-apps response does not match the documented package-list contract')
            readbacks.append({'image_id': device, 'package': scope['package'],
                              'package_present': scope['package'] in packages,
                              'version_verified': False, 'response_digest': digest(response)})
        row['readbacks'] = readbacks
        if row['status'] in {'ACKNOWLEDGED', 'PACKAGE_PRESENT_VERSION_UNVERIFIED'}:
            row['status'] = 'PACKAGE_PRESENT_VERSION_UNVERIFIED' if all(r['package_present'] for r in readbacks) else 'ACKNOWLEDGED'
        elif row['status'] == 'SUBMITTING': row['status'] = 'UNKNOWN'
        save()
        return {**row, 'new_install_request_count': int(new_submission),
                'next_action': 'VERIFY_VERSION_WITH_SEPARATE_OFFICIAL_EVIDENCE', 'ledger_path': str(path)}
