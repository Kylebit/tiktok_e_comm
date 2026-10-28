"""Keep the test suite detached from the workstation's operations ledger.

Individual integration tests may install their own synthetic profile with
``monkeypatch``.  A plain ``pytest tests`` must never inherit the user's
stable runtime pointer or data root.
"""

import os
from pathlib import Path

import pytest

from scripts.regression_run_envelope import (
    EnvelopeError,
    configure_test_operations_environment,
)


_UNCONFIGURED_PROFILE = Path(__file__).with_name("__NO_SHARED_OPERATIONS_PROFILE__.json")
if _UNCONFIGURED_PROFILE.exists():
    raise RuntimeError("the test-only operations profile sentinel must not exist")

os.environ["ORBIT_OPERATIONS_PROFILE"] = str(_UNCONFIGURED_PROFILE)
try:
    configure_test_operations_environment(os.environ)
except EnvelopeError as exc:
    raise RuntimeError(f"invalid regression envelope environment: {exc}") from exc


@pytest.fixture(autouse=True)
def isolate_synthetic_settings_process():
    """A test case may bind once; the next case gets an empty process cache.

    Production settings remain immutable within a process. Resetting only at
    test boundaries prevents another synthetic fixture's path being inherited.
    """
    from core import config

    def clear():
        config._cache = None
        config._cache_source = None
        config._cache_source_stat = None
        config._cache_explicit_source = None

    clear()
    try:
        yield
    finally:
        clear()
