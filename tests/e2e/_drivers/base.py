"""X.2.q — dialect-aware e2e driver protocol.

A ``DashboardDriver`` is the *test vocabulary* for browser e2e: every
verb is something a test does ("set a date filter", "read the Drift
table"), and the result comes back as plain Python — never a Playwright
``Locator`` / ``Page`` — so test bodies stay (almost) pure functions:

    driver.open("l1-dashboard", sheet="Drift")
    assert driver.table_rows("Drift Detail") == expected

One impl: ``App2Driver`` (the self-hosted HTMX/d3 page) — its quirks
(cell rendering, tab-switch timing, param-write settle) live *inside* the
driver, not in your test. The agreement gate compares the driver's
``table_rows()`` against the direct-DB matview recompute + the audit PDF.

(QuickSight was a second renderer through v15.x via ``QsEmbedDriver``,
with test bodies parametrized over ``[qs, app2]`` and a 4-way agreement
gate; both the driver and the parametrize were removed in Phase DW, so
the gate is now the 3-way ``scenario ⊆ direct == App2 == PDF`` check and
this protocol is unconditionally App2.)

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
    """Mirror the auto-derived ``human_name`` rule QuickSight used for
    column headers: ``account_id`` → ``"Account ID"``, ``rail_name`` →
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
    2. the title-case display label (``"Account ID"``) — the dataset
       contract's auto-derived ``human_name`` form (what QuickSight
       stamped on its table-column ``.title`` span).

    Cells outside ``columns`` are dropped. App2 renders the full dataset
    projection (not just visual-declared columns), so extra cells are
    handled cleanly — the test only
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
    """BG.1 — ground-truth direct-SQL helper for the ``DashboardDriver``.
    Runs ``sql`` against ``cfg.db.url`` (the same DB the deployed
    dashboard reads), with ``binds`` substituted via the same
    ``_sql_executor`` pipeline App2 renders through, and returns rows
    as ``{column: value}`` dicts.

    Why a shared helper: identity assertions
    (``rendered_kpi == query_db(sql, binds=…)``) compare the rendered
    value against ONE ground truth — the same ``_sql_executor`` path
    App2 renders through, so a mismatch is a real wire-shape bug, not
    two SQL paths diverging.

    ``binds`` keys map to App2's URL convention (``param_<name>`` for
    ``<<$pName>>`` placeholders — the retained QuickSight CustomSql
    form; ``date_from`` / ``date_to`` for the universal date filter;
    ``filter_<col>`` for ``IN``-list narrows). ``execute_visual_sql``
    translates ``<<$pName>>`` → ``:param_pName`` + applies
    ``dataset_parameters`` defaults for unsupplied ones.
    """
    url_params: dict[str, list[str]] = {
        key: [value] for key, value in (binds or {}).items()
    }
    rows, columns = execute_visual_sql(
        lambda: connect_demo_db(cfg),
        sql,
        url_params,
        dialect=cfg.db.dialect,
        dataset_parameters=list(dataset_parameters),
    )
    return [dict(zip(columns, row, strict=True)) for row in rows]


class DashboardDriver(Protocol):
    """The dashboard-driving test interface (see module docstring). All
    reads return plain Python; all writes block until the affected
    visuals have re-fetched."""

    #: Always ``"app2"`` now — the self-hosted HTMX renderer (the
    #: ``"qs"`` value went with QuickSight in DW).
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
        """The dashboard's sheet-tab names, in tab order. Reads the
        ``<nav>`` link text (the tree's ``Sheet.name``)."""
        ...

    def visual_titles(self) -> list[str]:
        """Titles of the visuals on the current sheet, in display order."""
        ...

    def filter_labels(self) -> list[str]:
        """Visible labels of the filter / parameter controls on the
        current sheet. Reads the ``#filter-form`` control labels (the
        tree's control ``.title``)."""
        ...

    def filter_options(self, label: str) -> list[str]:
        """The selectable values offered by the dropdown / multi-select
        filter control labelled ``label``, in display order. Sentinel
        entries (``"All"`` / ``"Select all"`` / blanks) are filtered
        out, so the result is the data-derived option universe — what a
        data-agnostic test picks from without hardcoding values. Reads
        the ``<select>``'s ``<option>`` text.

        For typeahead-backed pickers (LinkedValues sourced server-side)
        this returns the SEED PAGE only (empty query). Use
        ``typeahead_filter(label, query)`` to drive the per-keystroke
        load and verify a specific query's server-matched results."""
        ...

    def typeahead_filter(self, label: str, query: str) -> list[str]:
        """Drive a typeahead picker's per-keystroke load with ``query``
        and return the server-matched options.

        Catches WHERE-clause leaks, URL-resolution bugs, and option-
        accumulation bugs that the empty-query ``filter_options`` shape
        can't see — the picker only typed gets a different SQL path
        (ILIKE %q% vs seed-page) and only typed values exercise the
        clear-then-fetch dropdown logic.

        For static-options (non-typeahead) pickers, ``query`` is
        meaningless and this returns the same rendered options as
        ``filter_options``."""
        ...

    def wait_loaded(self, visual_title: str, *, timeout_ms: int = 15_000) -> None:
        """Block until the named visual has rendered content (a chart /
        table / number — not a spinner, not empty)."""
        ...

    def table_rows_full(
        self,
        visual_title: str,
        *,
        columns: Sequence[str] | None = None,
    ) -> list[dict[str, str]]:
        """Like ``table_rows`` but returns the FULL row set, not just the
        DOM window. App2 renders every row in the DOM, so the body
        matches ``table_rows`` exactly.

        (The de-virtualize verb survives from the QuickSight era, where
        it scrolled + accumulated past QS's virtualized window — the
        BO.1 fix, when overdraft's 119-row table broke the row-identity
        check against the ~37-row DOM window.)
        """
        ...

    def table_rows(
        self,
        visual_title: str,
        *,
        columns: Sequence[str] | None = None,
    ) -> list[dict[str, str]]:
        """Rows of a Table visual as dicts keyed by header text, in
        display order; cell values are the rendered (formatted) strings.
        App2 renders every row in the DOM, so this returns the full set.
        ``table_row_count`` gives just the post-filter total; the
        equivalent ``table_rows_full`` is retained for API symmetry.

        AA.A.995 — by default rows are keyed by the rendered header text:
        App2 stamps the raw SQL column name (``"account_id"``). Tests
        that need to look up cells by a known identity should pass
        ``columns`` — a sequence of raw SQL column names. The driver
        projects each row to JUST those cells, looking each one up by
        raw SQL name (App2's ``<th>``) or the title-case display label
        (the ``human_name`` form QuickSight stamped). Cells outside
        ``columns`` are dropped, so App2 rendering the full dataset
        projection (not just visual-declared columns) doesn't leak extra
        cells. ``KeyError`` (with the row's actual keys) if a column
        isn't findable under either form.
        """
        ...

    def table_row_count(self, visual_title: str) -> int:
        """The full (post-filter) row count of a table visual. App2
        renders every row in the DOM, so this counts them directly.
        Returns 0 for an empty table (not a sentinel)."""
        ...

    def find_row(
        self, visual_title: str, predicate: Mapping[str, str],
    ) -> dict[str, str] | None:
        """Walk the table looking for a row whose visible cells subset-
        match ``predicate`` (header → value). Return the first matching
        row as a header-keyed dict, or ``None`` if no row matches after
        walking the entire table. Early-exits on first match — the
        inverse-picker test
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

    def filter_value(self, label: str) -> str | None:
        """The currently-selected value of the single-select dropdown
        labelled ``label``, or ``None`` when nothing is picked (empty /
        cleared). Used by the DM cascade-clear test to assert the Account
        picker drops its stale value when the Role source changes.

        The Role→Account cascade-clear is an App2 affordance —
        QuickSight couldn't execute a parameterized picker dataset, so it
        never had a working cascade source (see
        ``[[project_qs_no_searchfilter_cascading]]``)."""
        ...

    def day_availability(
        self, label: str, *, open_on: str | None = None,
    ) -> dict[str, list[str]]:
        """Open the Flatpickr day picker labelled ``label`` and return
        each visible calendar day's availability markers as
        ``{iso_date: [states]}`` (``states`` ⊆ ``["transactions",
        "balance"]``). Days with no marker are omitted. ``open_on``
        (``YYYY-MM-DD``) navigates the calendar to that month before
        reading (without selecting a day) so the visible grid lands on the
        seeded data window — the picker's default month is the as_of-frame
        anchor, which can be far from a LOCKED_ANCHOR-seeded DB.

        The per-day decoration (DM.3) is added by the App2 Flatpickr
        ``onDayCreate`` callback from the server's ``day-availability``
        endpoint. (QuickSight's ``ParameterDateTimePickerControl`` had no
        per-day decoration surface — see
        ``[[project_qs_no_searchfilter_cascading]]``.)"""
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
        (``cfg.db.url`` stored on the driver at factory time),
        with ``binds`` substituted via the same ``_sql_executor``
        pipeline App2 renders through, and returns rows as
        ``{column: value}`` dicts. Delegates to the shared
        ``query_db_via_cfg`` helper so an identity assertion compares
        the rendered value against ONE ground truth — a mismatch is a
        real wire-shape bug, not "two SQL paths produced two answers."

        ``binds`` keys: ``param_<name>`` for ``<<$pName>>`` placeholders
        (the retained QuickSight CustomSql form), ``date_from`` /
        ``date_to`` for the universal date filter, ``filter_<col>`` for
        ``IN``-list narrows. Mirror the App2 URL contract.
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

    def drill_from_row(self, visual_title: str, row_index: int) -> None:
        """Indexed form of :meth:`drill_from_first_row` — fire the
        ``DATA_POINT_CLICK`` primary drill from the row at ``row_index``
        (zero-based) instead of row 0. Needed when the source table is
        ordered so that row 0's value isn't valid for the destination (e.g.
        Exception Detail sorts by amount DESC, so row 0 can be a control
        account with no drift; the test aims at the first drift-family row)."""
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

    def drill_from_row_via_menu(
        self, visual_title: str, row_index: int, menu_item: str,
    ) -> None:
        """Indexed form of :meth:`drill_from_first_row_via_menu` — fire the
        ``DATA_POINT_MENU`` drill from the row at ``row_index`` (zero-based)
        instead of row 0. Needed when the source table is ordered so that
        row 0 is a fixed kind and the test wants to drill a specific other
        row (e.g. the first row of a given ``check_type``)."""
        ...

    # -- metadata popup (CY.9) ------------------------------------------

    def open_metadata_panel(
        self, visual_title: str, row_index: int = 0,
    ) -> None:
        """Open the per-row metadata side-panel for the named Table
        visual's row at ``row_index`` (zero-based).

        Locates the visual section, clicks the ``⋯`` row-drill button
        on the indicated row, waits for the synthetic ``{} View metadata``
        ctxmenu entry, clicks it, and blocks until ``#side-panel``
        slides in (loses ``translate-x-full``).
        """
        ...

    def close_metadata_panel(self) -> None:
        """Dismiss the metadata side-panel via Escape and block until
        the drawer re-acquires ``translate-x-full``."""
        ...

    def metadata_panel_expand_all(self) -> None:
        """Click the ``[data-metadata-expand-all]`` toolbar button —
        opens every ``<details data-json-node>`` in the rendered tree."""
        ...

    def metadata_panel_collapse_all(self) -> None:
        """Click the ``[data-metadata-collapse-all]`` toolbar button —
        closes every ``<details data-json-node>`` in the rendered tree."""
        ...

    def metadata_panel_text(self) -> str:
        """Return the ``[data-metadata-raw]`` ``<textarea>`` value — the
        pretty-printed canonical JSON the Copy button reads. Tests
        assert on substrings of this for content-presence checks
        (cheaper than walking the rendered ``<details>`` tree).
        """
        ...

    def metadata_panel_open_details_count(self) -> int:
        """Count ``details[open][data-json-node]`` nodes in the rendered
        tree — the default-open depth verifier (``depth ≤ 2`` per CY.5
        operator lock; deeper levels collapsed by default).
        """
        ...

    # -- OIDC auth (DD.4) -----------------------------------------------

    def sign_in_via_oidc(self, *, email: str, password: str) -> None:
        """Drive the App2 → Dex → App2 OIDC code-flow login.

        ``GET /auth/login`` → 302 to Dex's authorize endpoint → fill
        ``input[name='login']`` (Dex local-connector field name; the
        visible label is "Email Address") and ``input[name='password']``
        on Dex's ``password.html`` → click ``#submit-login`` → click
        ``Grant Access`` on Dex's ``approval.html`` → 302 back to
        ``/auth/callback?code=...&state=...`` → 302 to ``/`` with the
        ``recon_gen_session`` JWT cookie set. Blocks until the post-login
        landing page settles (``networkidle``).

        Idempotent: if ``recon_gen_session`` is already present in the
        Playwright context's cookie jar this returns without driving the
        form (mirrors ``pick_filter`` peek-before-act).

        (QuickSight embed URLs were pre-signed at mint time —
        ``cfg.auth.aws.profile`` → STS →
        ``generate_embed_url_for_registered_user`` — so OIDC verbs never
        applied on the QS side; see
        ``[[project_qs_embed_url_presigned_no_oidc]]``.)"""
        ...

    def sign_out_via_oidc(self) -> None:
        """Drive ``GET /auth/logout`` — App2's logout route deletes the
        ``recon_gen_session`` cookie and 302s to Dex's
        ``end_session_endpoint`` (or ``/`` fallback). Blocks until
        ``networkidle``.

        Idempotent: if no ``recon_gen_session`` cookie is present this
        returns without driving the logout URL.

        (OIDC verbs never applied on the QuickSight side — its embed
        URLs were pre-signed at mint; see
        ``[[project_qs_embed_url_presigned_no_oidc]]``.)"""
        ...

    def inspect_jwt_cookie(self) -> dict[str, str] | None:
        """Return the current ``recon_gen_session`` cookie as a flat
        ``{name, value, domain, path}`` dict, or ``None`` when absent.

        The shape is intentionally string-valued (not Playwright's
        ``Cookie`` typed-dict) so tests can ``assert cookie is None`` /
        ``assert cookie["value"].startswith(\"eyJ\")`` without importing
        Playwright types — matches the no-Playwright-leak lint.

        (OIDC verbs never applied on the QuickSight side — its embed
        URLs were pre-signed at mint; see
        ``[[project_qs_embed_url_presigned_no_oidc]]``.)"""
        ...

    # -- artifacts -------------------------------------------------------

    def screenshot(self, path: str | Path | None = None) -> bytes:
        """Capture the current dashboard view as PNG bytes (also write
        to ``path`` when given). Captures the page."""
        ...

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        """Tear down — close the browser, stop any server the driver
        owns. (Driver factories are context managers, so tests rarely
        call this directly.)"""
        ...
