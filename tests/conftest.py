"""Keep the test suite detached from the workstation's operations ledger.

Individual integration tests may install their own synthetic profile with
``monkeypatch``.  A plain ``pytest tests`` must never inherit the user's
stable runtime pointer or data root.
"""

import os
from pathlib import Path

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
