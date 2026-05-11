"""X.2.q — dialect-aware e2e drivers.

``DashboardDriver`` is the protocol (the e2e test vocabulary, results as
plain Python); ``App2Driver`` drives the self-hosted HTMX renderer;
``QsEmbedDriver`` (X.2.q.1) will drive the embedded QuickSight iframe.
See ``base.py`` for the design.
"""

from __future__ import annotations

from tests.e2e._drivers.app2 import App2Driver
from tests.e2e._drivers.base import DashboardDriver

__all__ = ["App2Driver", "DashboardDriver"]
