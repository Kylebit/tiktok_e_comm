"""Fail-closed loading for one explicitly pinned Ozon seller account."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class PinnedOzonCredentials:
    account_id: str
    api_key: str = field(repr=False)
    credentials_sha256: str

    def public(self) -> dict[str, str]:
        """Return only non-secret deployment identity."""

        return {
            "ozon_account_id": self.account_id,
            "ozon_credentials_sha256": self.credentials_sha256,
        }


def load_pinned_ozon_credentials(
    path: str | Path, *, expected_sha256: str, expected_account_id: str
) -> PinnedOzonCredentials:
    expected_digest = str(expected_sha256 or "").strip().lower()
    expected_account = str(expected_account_id or "").strip()
    if not _SHA256.fullmatch(expected_digest):
        raise ValueError("pinned Ozon credential digest is invalid")
    if not expected_account.isdecimal() or int(expected_account) <= 0:
        raise ValueError("pinned Ozon account identity is invalid")

    selected = Path(path).expanduser().resolve(strict=True)
    raw = selected.read_bytes()
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != expected_digest:
        raise ValueError("pinned Ozon credential digest drifted")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("pinned Ozon credential file is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("pinned Ozon credential file is invalid")
    account_id = str(value.get("client_id") or "").strip()
    api_key = str(value.get("api_key") or "").strip()
    if account_id != expected_account or not api_key:
        raise ValueError("pinned Ozon credential account is unavailable")
    return PinnedOzonCredentials(account_id, api_key, actual_digest)


def configured_pinned_ozon_credentials() -> PinnedOzonCredentials | None:
    names = (
        "ORBIT_OZON_CREDENTIALS_PATH",
        "ORBIT_OZON_CREDENTIALS_SHA256",
        "ORBIT_OZON_EXPECTED_ACCOUNT_ID",
    )
    values = tuple(str(os.environ.get(name) or "").strip() for name in names)
    if not any(values):
        return None
    if not all(values):
        raise ValueError("pinned Ozon credential configuration is incomplete")
    return load_pinned_ozon_credentials(
        values[0], expected_sha256=values[1], expected_account_id=values[2]
    )


def required_pinned_ozon_credentials() -> PinnedOzonCredentials:
    """Require the exact account pin used by marketplace publication."""

    pinned = configured_pinned_ozon_credentials()
    if pinned is None:
        raise ValueError("Ozon publication requires pinned credentials")
    return pinned


__all__ = [
    "PinnedOzonCredentials",
    "configured_pinned_ozon_credentials",
    "load_pinned_ozon_credentials",
    "required_pinned_ozon_credentials",
]
