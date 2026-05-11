"""X.2.q — dialect-aware e2e driver protocol.

A ``DashboardDriver`` is the *test vocabulary* for browser e2e: every
verb is something a test does ("set a date filter", "read the Drift
table"), and the result comes back as plain Python — never a Playwright
``Locator`` / ``Page`` — so test bodies stay (almost) pure functions:

    driver.open("l1-dashboard", sheet="Drift")
    assert driver.table_rows("Drift Detail") == expected

Two impls: ``QsEmbedDriver`` (the embedded QuickSight iframe — the QS
quirks: cell virtualization, racy tab switches, the page-size-bump for
true row counts, the ``ParameterDropDownControl`` grey-bar click — live
*inside* the driver, not in your test) and ``App2Driver`` (the
self-hosted HTMX/d3 page). e2e tests ``@pytest.mark.parametrize`` over
``[qs, app2]`` via a ``driver`` fixture, so one body verifies both
renderers; QS-only or App2-only checks just ``pytest.skip`` the
irrelevant param. ``X.2.j``'s 4-way agreement gate compares the ``qs``
and ``app2`` drivers' ``table_rows()`` against each other (and against
the audit PDF's numbers).

Playwright must not leak past the driver layer — ``tests/e2e/**`` (and
any caller of this protocol) talks ``DashboardDriver``, not ``Page`` /
``Locator``. (X.2.q.5 lands the AST lint that enforces it.)
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class DashboardDriver(Protocol):
    """The cross-renderer dashboard-driving interface (see module
    docstring). All reads return plain Python; all writes block until
    the affected visuals have re-fetched."""

    #: ``"qs"`` for the embedded QuickSight dashboard, ``"app2"`` for the
    #: self-hosted HTMX renderer. Tests use this to ``pytest.skip``
    #: dialect-specific checks.
    dialect: str

    # -- navigation ------------------------------------------------------

    def open(self, dashboard: str, sheet: str | None = None) -> None:
        """Navigate to ``dashboard`` (and ``sheet`` if it's multi-sheet)
        and block until the page settles + its visuals have loaded.
        Idempotent — re-``open`` resets all filter state."""
        ...

    def goto_sheet(self, name: str) -> None:
        """Switch to the sheet tab named ``name`` and block until its
        visuals have (re)loaded."""
        ...

    # -- reads -----------------------------------------------------------

    def visual_titles(self) -> list[str]:
        """Titles of the visuals on the current sheet, in display order."""
        ...

    def wait_loaded(self, visual_title: str, *, timeout_ms: int = 15_000) -> None:
        """Block until the named visual has rendered content (a chart /
        table / number — not a spinner, not empty)."""
        ...

    def table_rows(self, visual_title: str) -> list[dict[str, str]]:
        """Rows of a Table visual as dicts keyed by header text, in
        display order; cell values are the rendered (formatted) strings.
        The driver handles renderer-specific quirks — QS row
        virtualization, scroll-accumulation — transparently, so the
        caller gets the full page-of-rows the renderer is showing."""
        ...

    def kpi_value(self, visual_title: str) -> str | None:
        """The headline value text of a KPI visual, or ``None`` if the
        named visual isn't a KPI / has no value rendered yet."""
        ...

    # -- writes ----------------------------------------------------------

    def pick_filter(self, label: str, values: Sequence[str]) -> None:
        """Set the filter control labelled ``label`` to ``values`` (one
        for single-select, many for multi-select) and block until the
        affected visuals re-fetch. Empty ``values`` clears it."""
        ...

    def set_date_range(self, from_: str | None, to: str | None) -> None:
        """Set the universal date range (ISO ``YYYY-MM-DD`` strings;
        ``None`` on a side leaves that bound open) and block until
        re-fetch."""
        ...

    def set_slider(
        self, label: str, lo: float | None, hi: float | None,
    ) -> None:
        """Set the numeric-range slider labelled ``label`` (``None`` on a
        side leaves it at the bound) and block until re-fetch."""
        ...

    def clear_filters(self) -> None:
        """Reset every filter on the page to its default and block until
        re-fetch."""
        ...

    def cross_link(self, label: str) -> None:
        """Click the cross-sheet / cross-app drill link labelled
        ``label``, follow the navigation, and block until the
        destination settles."""
        ...

    # -- artifacts -------------------------------------------------------

    def screenshot(self, path: str | Path | None = None) -> bytes:
        """Capture the current dashboard view as PNG bytes (also write
        to ``path`` when given). QS captures the embedded iframe content;
        App2 captures the page."""
        ...

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        """Tear down — close the browser, stop any server the driver
        owns. (Driver factories are context managers, so tests rarely
        call this directly.)"""
        ...
