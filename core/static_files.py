"""Resolve an untrusted static URL suffix inside one public directory."""
from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote


def resolve_static_path(root: Path, encoded_relative: str) -> Path | None:
    """Return an existing in-root file, or None for every rejected path.

    Callers pass the suffix of urlparse(request_target).path without unquoting
    or stripping slashes. These handlers do not decode paths earlier, so decode
    exactly once here; percent escapes left by that pass are literal filenames.
    Windows separators, drives/ADS and trailing-dot/space aliases are rejected
    on every OS. Trusted fixed pages/reports must continue using their own paths.
    """
    if re.search(r"%(?![0-9a-fA-F]{2})", encoded_relative):
        return None
    try:
        relative = unquote(encoded_relative, errors="strict")
        if not relative or any(char in relative for char in "\\:"):
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in relative):
            return None
        parts = relative.split("/")
        if any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in parts):
            return None
        resolved_root = root.resolve(strict=True)
        candidate = (resolved_root / relative).resolve(strict=True)
        candidate.relative_to(resolved_root)
        return candidate if candidate.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None
