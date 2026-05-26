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

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db
from recon_gen.common.html._sql_executor import execute_visual_sql
from recon_gen.common.models import DatasetParameter


def _title_case_header(sql_column: str) -> str:
    """Mirror the auto-derived ``human_name`` rule QS uses for column
    headers: ``account_id`` → ``"Account ID"``, ``rail_name`` →
    ``"Rail Name"``. Preserves common all-caps initialisms (ID / SQL /
    URL / API) so display labels match the dataset contract's
    ``human_name`` default."""
    _INITIALISMS = frozenset({"id", "sql", "url", "api", "css", "ip"})
    parts: list[str] = []
    for part in sql_column.split("_"):
        if part.lower() in _INITIALISMS:
            parts.append(part.upper())
        else:
            parts.append(part.capitalize())
    return " ".join(parts)


def rekey_by_columns(
    rows: list[dict[str, str]], columns: Sequence[str],
) -> list[dict[str, str]]:
    """Return each row in ``rows`` projected to just the cells for
    ``columns``, keyed by the raw SQL column names. Looks up each
    requested column in the row by trying:

    1. the raw SQL name verbatim (``"account_id"``) — what App2 stamps
       on its ``<th>``; and
    2. the title-case display label (``"Account ID"``) — what QS stamps
       on its ``sn-table-column-N .title`` span (mirrors the dataset
       contract's auto-derived ``human_name``).

    Cells outside ``columns`` are dropped. Renderers that show extra
    columns (App2 currently renders the full dataset projection, not
    just visual-declared columns) are handled cleanly — the test only
    sees what it asked for. Raises ``KeyError`` (with the row's actual
    keys) when a column isn't findable under either form, so a typo or
    a renamed column surfaces loudly."""
    out: list[dict[str, str]] = []
    for r in rows:
        projected: dict[str, str] = {}
        for sql_col in columns:
            if sql_col in r:
                projected[sql_col] = r[sql_col]
                continue
            display = _title_case_header(sql_col)
            if display in r:
                projected[sql_col] = r[display]
                continue
            raise KeyError(
                f"rekey_by_columns: {sql_col!r} not found under raw "
                f"name or {display!r}; row keys = {list(r.keys())!r}"
            )
        out.append(projected)
    return out


def query_db_via_cfg(
    cfg: Config,
    sql: str,
    *,
    binds: Mapping[str, str] | None = None,
    dataset_parameters: Sequence[DatasetParameter] = (),
) -> list[dict[str, Any]]:  # typing-smell: ignore[explicit-any]: cell values are heterogeneous per-column — coercion happens at the assert site
    """BG.1 — ground-truth direct-SQL helper shared by both
    ``DashboardDriver`` impls. Runs ``sql`` against ``cfg.demo_database_url``
    (the same DB the deployed dashboard reads), with ``binds`` substituted
    via the same ``_sql_executor`` pipeline App2 uses, and returns rows
    as ``{column: value}`` dicts.

    Why a shared helper, not per-driver impl: identity assertions
    (``rendered_kpi == query_db(sql, binds=…)``) compare against ONE
    ground truth. Differences between QS and App2 must be wire-shape
    differences, not different SQL paths to the same answer.

    ``binds`` keys map to App2's URL convention (``param_<name>`` for
    QS ``<<$pName>>`` placeholders; ``date_from`` / ``date_to`` for the
    universal date filter; ``filter_<col>`` for ``IN``-list narrows).
    ``execute_visual_sql`` translates ``<<$pName>>`` → ``:param_pName``
    + applies ``dataset_parameters`` defaults for unsupplied ones.
    """
    url_params: dict[str, list[str]] = {
        key: [value] for key, value in (binds or {}).items()
    }
    rows, columns = execute_visual_sql(
        lambda: connect_demo_db(cfg),
        sql,
        url_params,
        dialect=cfg.dialect,
        dataset_parameters=list(dataset_parameters),
    )
    return [dict(zip(columns, row, strict=True)) for row in rows]


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

    def table_rows(
        self,
        visual_title: str,
        *,
        columns: Sequence[str] | None = None,
    ) -> list[dict[str, str]]:
        """Rows of a Table visual as dicts keyed by header text, in
        display order; cell values are the rendered (formatted) strings.
        Returns the *currently-rendered* window (QS virtualizes ~10 rows;
        App2 renders all rows in DOM). When the caller needs the
        post-filter total row count for a table that may exceed the
        viewport, ``table_row_count`` does the page-size-bump + scroll-
        accumulate dance to surface the full number.

        AA.A.995 — by default rows are keyed by the rendered header text,
        which differs by renderer: QS stamps ``column.human_name``
        (``"Account ID"``); App2 stamps the raw SQL column name
        (``"account_id"``). Tests that need to look up cells by a known
        identity should pass ``columns`` — a sequence of raw SQL column
        names. The driver projects each row to JUST those cells, looking
        each one up by raw name (App2's path) or title-case display
        label (QS's path). Cells outside ``columns`` are dropped, so
        renderer differences in which columns get shown at all (App2
        renders the full dataset projection; QS shows only visual-
        declared columns) don't leak. ``KeyError`` (with the row's
        actual keys) if a column isn't findable under either form.
        """
        ...

    def table_row_count(self, visual_title: str) -> int:
        """The full (post-filter) row count of a table visual, surfacing
        the rows past the rendered window. On QS that's the page-size-
        bump + scroll-accumulate path through the ``simplePagedDisplayNav_*``
        controls (~3-5s per call vs ``len(table_rows())``'s ~0.8s, so
        prefer the latter when you only need the window or know the
        table is small). Returns 0 for an empty table (not a sentinel)."""
        ...

    def find_row(
        self, visual_title: str, predicate: Mapping[str, str],
    ) -> dict[str, str] | None:
        """Walk the table looking for a row whose visible cells subset-
        match ``predicate`` (header → value). Return the first matching
        row as a header-keyed dict, or ``None`` if no row matches after
        walking the entire table (scroll-accumulated on QS, page-walked
        on App2). Early-exits on first match — the inverse-picker test
        only needs "is there ANY offending row?", not the full set.

        The predicate's keys are column-header DISPLAY labels (the
        text the user sees as the column name) — use
        ``visual_column_label`` to resolve SQL column names if
        needed."""
        ...

    def kpi_value(self, visual_title: str) -> str | None:
        """The headline value text of a KPI visual, or ``None`` if the
        named visual isn't a KPI / has no value rendered yet."""
        ...

    def query_db(
        self,
        sql: str,
        *,
        binds: Mapping[str, str] | None = None,
        dataset_parameters: Sequence[DatasetParameter] = (),
    ) -> list[dict[str, Any]]:  # typing-smell: ignore[explicit-any]: heterogeneous cell values — same justification as ``query_db_via_cfg``
        """BG.1 — direct-SQL ground truth for honest-gate assertions.

        Runs ``sql`` against the same DB the deployed dashboard reads
        (``cfg.demo_database_url`` stored on the driver at factory time),
        with ``binds`` substituted via the same ``_sql_executor``
        pipeline App2 uses, and returns rows as ``{column: value}``
        dicts. Both impls delegate to the shared
        ``query_db_via_cfg`` helper so the QS and App2 legs of an
        identity assertion compare against ONE ground truth — wire-shape
        differences are real bugs, not "two SQL paths produced two
        answers."

        ``binds`` keys: ``param_<name>`` for QS ``<<$pName>>``
        placeholders, ``date_from`` / ``date_to`` for the universal
        date filter, ``filter_<col>`` for ``IN``-list narrows. Mirror
        the App2 URL contract.
        """
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
