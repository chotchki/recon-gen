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

    def sheet_names(self) -> list[str]:
        """The dashboard's sheet-tab names, in tab order. (QS reads the
        ``[role="tab"]`` strip; App2 the ``<nav>`` link text — both are
        the tree's ``Sheet.name``, so the two renderers agree.)"""
        ...

    def visual_titles(self) -> list[str]:
        """Titles of the visuals on the current sheet, in display order."""
        ...

    def filter_labels(self) -> list[str]:
        """Visible labels of the filter / parameter controls on the
        current sheet. (QS reads the ``sheet_control_name`` strip; App2
        the ``#filter-form`` control labels — both are the tree's
        control ``.title``.)"""
        ...

    def filter_options(self, label: str) -> list[str]:
        """The selectable values offered by the dropdown / multi-select
        filter control labelled ``label``, in display order. Sentinel
        entries (``"All"`` / ``"Select all"`` / blanks) are filtered
        out, so the result is the data-derived option universe — what a
        data-agnostic test picks from without hardcoding values. (QS
        opens the ``ParameterDropDownControl`` popover and reads the
        ``[role="option"]`` labels; App2 reads the ``<select>``'s
        ``<option>`` text.)"""
        ...

    def wait_loaded(self, visual_title: str, *, timeout_ms: int = 15_000) -> None:
        """Block until the named visual has rendered content (a chart /
        table / number — not a spinner, not empty)."""
        ...

    def table_rows(self, visual_title: str) -> list[dict[str, str]]:
        """Rows of a Table visual as dicts keyed by header text, in
        display order; cell values are the rendered (formatted) strings.
        Returns the *currently-rendered* window (QS virtualizes ~10 rows;
        App2 renders all rows in DOM). When the caller needs the
        post-filter total row count for a table that may exceed the
        viewport, ``table_row_count`` does the page-size-bump + scroll-
        accumulate dance to surface the full number."""
        ...

    def table_row_count(self, visual_title: str) -> int:
        """The full (post-filter) row count of a table visual, surfacing
        the rows past the rendered window. On QS that's the page-size-
        bump + scroll-accumulate path through the ``simplePagedDisplayNav_*``
        controls (~3-5s per call vs ``len(table_rows())``'s ~0.8s, so
        prefer the latter when you only need the window or know the
        table is small). Returns 0 for an empty table (not a sentinel)."""
        ...

    def read_all_table_rows(
        self, visual_title: str,
    ) -> list[dict[str, str]]:
        """Every row of a table visual as a list of header-keyed dicts,
        including rows below the fold. On QS that's a scroll-accumulate
        dance through the visual's inner grid container so virtualized
        rows get mounted and read. On App2 it's just every rendered row
        (the server-side pagination already returns all matching rows
        in one fetch).

        Use case: identity-checks where you need to verify the presence
        or absence of a specific row in the FULL filtered result, not
        just the visible window. ``table_rows()`` returns the window;
        this returns the universe."""
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

    def set_date(self, label: str, iso: str | None) -> None:
        """Set the single-value DateTime picker control labelled ``label``
        to ``iso`` (``YYYY-MM-DD``) and block until re-fetch. ``None``
        is a no-op.

        Distinct from ``set_date_range`` (two-bound universal date
        pickers on data-bearing sheets). Used for the per-sheet
        single-day equality pickers — currently only L1's Daily
        Statement (Business Day picker, bound to a
        ``TimeEqualityFilter``).

        Renderers that don't render the widget (App2 today: skips
        ``add_parameter_datetime_picker`` during filter-spec
        derivation — see ``_tree_filter_specs.py``) implement this as
        a no-op. Tests can call it unconditionally; the date narrowing
        only matters on the renderer that actually applies it.
        """
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

    def drill_from_first_row(self, visual_title: str) -> None:
        """Left-click the first data row of the named table visual to
        fire its ``DATA_POINT_CLICK`` drill — typically writes a
        parameter that re-renders the *same* sheet. Block until the
        re-fetch lands. (Cross-sheet / right-click drills go through
        ``drill_from_first_row_via_menu``.)"""
        ...

    def drill_from_first_row_via_menu(
        self, visual_title: str, menu_item: str,
    ) -> None:
        """Right-click the first data row of the named table visual,
        then click the context-menu entry whose visible text is
        ``menu_item`` — fires a ``DATA_POINT_MENU`` drill (which can
        navigate to a different sheet *or* write parameters in place).
        After the click the caller typically ``wait_loaded``\\s on the
        destination's expected visual to lock in the new sheet."""
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
