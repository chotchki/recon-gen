"""DD.4 — drift gate.

The test driver ``tests/e2e/_drivers/app2.py::_SESSION_COOKIE_NAME``
intentionally duplicates the cookie name string rather than importing
the production constant on every cookie peek. This gate pins the two
together so a future rename of the production constant breaks at unit
time, not via opaque e2e cookie-missing failures.
"""

from __future__ import annotations

from recon_gen.common.html.auth import SESSION_COOKIE_NAME
from tests.e2e._drivers.app2 import _SESSION_COOKIE_NAME


def test_driver_session_cookie_name_matches_production() -> None:
    assert _SESSION_COOKIE_NAME == SESSION_COOKIE_NAME, (
        "DD.4: tests/e2e/_drivers/app2.py::_SESSION_COOKIE_NAME drifted "
        f"({_SESSION_COOKIE_NAME!r}) from "
        f"recon_gen.common.html.auth.SESSION_COOKIE_NAME ({SESSION_COOKIE_NAME!r}). "
        "Update the driver constant (or both) to match."
    )
