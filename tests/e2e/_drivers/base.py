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
    ``DashboardDriver`` impls. Runs ``sql`` against ``cfg.db.url``
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
        dialect=cfg.db.dialect,
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
        ``<option>`` text.)

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
        """Like ``table_rows`` but **de-virtualizes** — for renderers that
        virtualize (QS), scroll through the table and accumulate every
        row. Returns the FULL set, not just the DOM window.

        Use for row-identity checks (the audit-agreement validator's
        QS-side row keying). For App2 the body matches ``table_rows``
        exactly (App2 renders all rows in DOM). Added in the BO.1 fix
        when overdraft's 119-row table broke the row-identity check
        against the 37-row DOM window.
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
        Returns the *currently-rendered* window (QS virtualizes ~10 rows;
        App2 renders all rows in DOM). When the caller needs the
        post-filter total row count for a table that may exceed the
        viewport, ``table_row_count`` does the page-size-bump + scroll-
        accumulate dance to surface the full number. For the FULL row
        set on QS (de-virtualized), use ``table_rows_full``.

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

    def filter_value(self, label: str) -> str | None:
        """The currently-selected value of the single-select dropdown
        labelled ``label``, or ``None`` when nothing is picked (empty /
        cleared). Used by the DM cascade-clear test to assert the Account
        picker drops its stale value when the Role source changes.

        App2-only — the Role→Account cascade-clear is an App2 affordance
        (the QS-side cascade source is gated off via ``app2_only`` because
        QS can't execute a parameterized picker dataset; see
        ``[[project_qs_no_searchfilter_cascading]]``). ``QsEmbedDriver``
        raises ``NotImplementedError``."""
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

        App2-only (DM.3) — the per-day decoration is added by the App2
        Flatpickr ``onDayCreate`` callback from the server's
        ``day-availability`` endpoint. QuickSight's
        ``ParameterDateTimePickerControl`` has no per-day decoration
        surface (see ``[[project_qs_no_searchfilter_cascading]]``), so
        ``QsEmbedDriver`` raises ``NotImplementedError``."""
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

    # -- metadata popup (CY.9 — App2-only per operator lock 7) -----------

    def open_metadata_panel(
        self, visual_title: str, row_index: int = 0,
    ) -> None:
        """Open the per-row metadata side-panel for the named Table
        visual's row at ``row_index`` (zero-based).

        Locates the visual section, clicks the ``⋯`` row-drill button
        on the indicated row, waits for the synthetic ``{} View metadata``
        ctxmenu entry, clicks it, and blocks until ``#side-panel``
        slides in (loses ``translate-x-full``).

        App2-only — ``QsEmbedDriver`` raises ``NotImplementedError`` per
        CY.5 operator lock 7 (metadata popup is an App2 affordance; QS
        path is unaffected because its dataset never projects the
        ``metadata`` column).
        """
        ...

    def close_metadata_panel(self) -> None:
        """Dismiss the metadata side-panel via Escape and block until
        the drawer re-acquires ``translate-x-full``. App2-only."""
        ...

    def metadata_panel_expand_all(self) -> None:
        """Click the ``[data-metadata-expand-all]`` toolbar button —
        opens every ``<details data-json-node>`` in the rendered tree.
        App2-only."""
        ...

    def metadata_panel_collapse_all(self) -> None:
        """Click the ``[data-metadata-collapse-all]`` toolbar button —
        closes every ``<details data-json-node>`` in the rendered tree.
        App2-only."""
        ...

    def metadata_panel_text(self) -> str:
        """Return the ``[data-metadata-raw]`` ``<textarea>`` value — the
        pretty-printed canonical JSON the Copy button reads. Tests
        assert on substrings of this for content-presence checks
        (cheaper than walking the rendered ``<details>`` tree). App2-only.
        """
        ...

    def metadata_panel_open_details_count(self) -> int:
        """Count ``details[open][data-json-node]`` nodes in the rendered
        tree — the default-open depth verifier (``depth ≤ 2`` per CY.5
        operator lock; deeper levels collapsed by default). App2-only.
        """
        ...

    # -- OIDC auth (DD.4 — App2-only; QS embed is pre-signed at mint) ----

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

        App2-only. ``QsEmbedDriver`` raises ``NotImplementedError`` per
        ``[[project_qs_embed_url_presigned_no_oidc]]`` — QS embed URLs
        are pre-signed at mint time (``cfg.auth.aws.profile`` → STS →
        ``generate_embed_url_for_registered_user``) so OIDC verbs never
        apply on the QS side."""
        ...

    def sign_out_via_oidc(self) -> None:
        """Drive ``GET /auth/logout`` — App2's logout route deletes the
        ``recon_gen_session`` cookie and 302s to Dex's
        ``end_session_endpoint`` (or ``/`` fallback). Blocks until
        ``networkidle``.

        Idempotent: if no ``recon_gen_session`` cookie is present this
        returns without driving the logout URL.

        App2-only. ``QsEmbedDriver`` raises ``NotImplementedError`` per
        ``[[project_qs_embed_url_presigned_no_oidc]]``."""
        ...

    def inspect_jwt_cookie(self) -> dict[str, str] | None:
        """Return the current ``recon_gen_session`` cookie as a flat
        ``{name, value, domain, path}`` dict, or ``None`` when absent.

        The shape is intentionally string-valued (not Playwright's
        ``Cookie`` typed-dict) so tests can ``assert cookie is None`` /
        ``assert cookie["value"].startswith(\"eyJ\")`` without importing
        Playwright types — matches the no-Playwright-leak lint.

        App2-only. ``QsEmbedDriver`` raises ``NotImplementedError`` per
        ``[[project_qs_embed_url_presigned_no_oidc]]``."""
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
