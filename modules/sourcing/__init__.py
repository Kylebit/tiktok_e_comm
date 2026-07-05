"""Sourcing package exports.

Keep package import lightweight so route handlers can import submodules
without pulling in optional scraper implementations that may be unavailable
in the current environment.
"""

from __future__ import annotations

try:
    from .drission_1688 import Drission1688
except Exception:  # pragma: no cover - optional runtime dependency
    Drission1688 = None

try:
    from .playwright_1688 import Playwright1688
except Exception:  # pragma: no cover - optional runtime dependency
    Playwright1688 = None

try:
    from .scrape_1688 import Scraper1688
except Exception:  # pragma: no cover - optional runtime dependency
    Scraper1688 = None

__all__ = ["Drission1688", "Playwright1688", "Scraper1688"]
