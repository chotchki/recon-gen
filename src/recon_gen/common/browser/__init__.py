"""Playwright-driven browser helpers + dashboard screenshot capture.

Promoted out of ``tests/e2e/`` in M.1.10 (per spike finding F7) so
production CLI code (the docs render / screenshot pipeline) can import
the browser primitives without reaching into ``tests/``.

The module pair:

- :mod:`recon_gen.common.browser.helpers` — Playwright page-driving
  primitives (page setup, sheet-tab navigation, table/control probing,
  waits, failure capture). Also re-exported here for convenience.
- :mod:`recon_gen.common.browser.screenshot` — per-sheet dashboard
  screenshot capture (App2 placeholder post-DW; see the module
  docstring).

Production callers typically only need ``webkit_page`` +
``capture_app_dashboards``. The full probe / assertion surface
(``count_table_rows``, ``read_kpi_value``, etc.) is for e2e test code;
it lives in the same module today because splitting it cleanly will be
more obvious once the App2 capture build surfaces what production
really needs.
"""

from .helpers import webkit_page
from .screenshot import capture_app_dashboards

__all__ = [
    "capture_app_dashboards",
    "webkit_page",
]
