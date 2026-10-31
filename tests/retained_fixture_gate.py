"""Explicit historical acceptance inputs are separate from synthetic software tests.

No historic checkout, live database or diagnostic is guessed from this machine.
An explicitly selected replay must supply its whole environment; partial setup
is a failure, not a skip. All original replay assertions remain unchanged.
"""
import os
import pytest


def require_retained_inputs(*names):
    present = [name for name in names if os.environ.get(name)]
    if not present:
        pytest.skip('Historical retained-data replay not selected; explicit inputs required: ' + ','.join(names))
    missing = sorted(set(names) - set(present))
    if missing:
        pytest.fail('Selected historical replay has incomplete explicit inputs: ' + ','.join(missing))
