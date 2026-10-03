"""Sourcing package exports.

Keep package import lightweight so route handlers can import submodules
without pulling in optional scraper implementations that may be unavailable
in the current environment.
"""

from __future__ import annotations

from importlib import import_module

__all__ = ["Drission1688", "Playwright1688", "Scraper1688"]
_OPTIONAL_EXPORTS = {
    "Drission1688": ".drission_1688",
    "Playwright1688": ".playwright_1688",
    "Scraper1688": ".scrape_1688",
}


def __getattr__(name):
    module = _OPTIONAL_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        value = getattr(import_module(module, __name__), name)
    except Exception:  # optional runtime dependency, same public fallback
        value = None
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
