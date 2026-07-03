"""DL.2 — Parametrized cross-sheet drill content + picker-value guardrail.

Walks every cross-sheet ``Drill`` in every app's tree (via
``iter_cross_sheet_drills``) and, per parametrize call:

1. Opens the source sheet.
2. Reads the first row's value for each drill-written column from the
   source visual's table cells (the ground-truth values the drill is
   about to write into the destination's parameter store).
3. Fires the drill via ``drill_from_first_row`` (DATA_POINT_CLICK) or
   ``drill_from_first_row_via_menu`` (DATA_POINT_MENU).
4. Waits for the destination's anchor visual to load and asserts the
   destination renders content (``len(table_rows(anchor)) > 0``).
5. Asserts the destination's data narrowed to the drilled value — for
   each ``(param, source_value)`` that maps to a destination column on
   the anchor table, verify every visible row's cell matches.

Anchor visual selection per ``dst_sheet`` — (a) first registered table
visual on the sheet (cheapest, works for most apps); (b) hand-curated
fallback in ``_DST_ANCHOR_FALLBACKS`` for sheets where the first table
isn't a meaningful drill destination signal. Most cases hit (a). KPI
visuals are skipped (they don't expose ``table_rows``).

The drift→daily-statement bug surfaces here as a per-parametrize
failure (Daily Statement is the dst; the drift drill writes raw
``account_id`` but Daily Statement's picker is bound to
``account_display`` so destination narrows to 0 rows — content fails).
DL.3 triages + fixes.

Tier: ``QS_BROWSER`` — both renderers in one body, no QS-only or
App2-only skips at the body level (driver-verb gaps surface as
``NotImplementedError`` with the POLICY 2 structured triple).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from recon_gen.common.tree import App, Drill
from recon_gen.common.drill import (
    DrillResetSentinel,
    DrillSourceField,
    DrillStaticDateTime,
)
from recon_gen.common.tree.visuals import Table

from tests._marks import Need, Tier, needs, tier
from tests.e2e._helpers.drill_enumeration import (
    DrillSite,
    iter_cross_sheet_drills,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.e2e._drivers import DashboardDriver

    # A per-source-sheet required-filter preselect: drives the pick(s)
    # that populate the source visual, returns False on a genuine data gap.
    _PreselectFn = Callable[[DashboardDriver], bool]


pytestmark = [
    pytest.mark.e2e,
    tier(Tier.APP2),
    needs(Need.PLAYWRIGHT),
]


# ---------------------------------------------------------------------------
# Per-sheet destination-anchor fallbacks
# ---------------------------------------------------------------------------
#
# Strategy (a): default to the first Table on the destination sheet
# (the cheapest content+row-data probe). When (a) lands on a Table whose
# rows are too narrow to expose the drilled column (e.g. a tiny summary
# Table), (b) names an alternate Table title to read instead. Keyed by
# destination sheet's ``sheet_id`` (the str value, not the SheetId
# wrapper).

# The `_spine_plant` rail tags the zero-amount DL.3.1 drill-scaffolding
# marker tx. Those markers aggregate every ViolationGenerator's marker
# leg, so on the L2 Violation Detail table (ORDER BY count DESC) the
# `_spine_plant` 'Unmatched Rail Name' row sorts to row 0 — it's the ONLY
# undeclared rail in the seed and carries the highest posting count. It's
# meaningless scaffolding, not a real user-facing rail, so the row-0 walk
# should skip PAST it to a genuine rail. DY.7.1 retired the blanket
# `_PLANT_ERROR_SENTINEL`-in-any-cell skip (which was over-broad — the
# marker even round-trips to Rails) in favor of the destination-aware row
# selector below; the constant now just names the marker for exclusion.
_PLANT_ERROR_SENTINEL = "_spine_plant"

# Some source tables hang a destination-specific drill on a column whose
# VALID values depend on the row's ``check_type`` — so a blind row-0 drill
# can read a row whose value lands the destination EMPTY. Select the first
# row whose ``check_type`` is valid for THIS drill's destination. Keyed by
# ``(src_visual_title, dst_sheet_id)`` → (accepted ``check_type`` labels,
# exclude the `_spine_plant` scaffolding marker?). NOTE the label CASE
# differs by source: L2FT 'L2 Violation Detail' CASTs Title-Case
# ('Chain Orphans'); L1 'Exception Detail' projects snake_case
# ('ledger_drift').
_L2_VIOLATION_DETAIL_TITLE = "L2 Violation Detail"
_ROW_SELECT_BY_SRC_DST: dict[tuple[str, str], tuple[frozenset[str], bool]] = {
    # L2FT L2 Violation Detail hangs BOTH the Rails + Chains drill on one
    # entity_a; row 0 (count DESC) is the _spine_plant 'Unmatched Rail Name'
    # marker, valid only for Rails → the Chains drill lands empty.
    ("L2 Violation Detail", "l2ft-sheet-rails"): (frozenset({"Unmatched Rail Name"}), True),
    ("L2 Violation Detail", "l2ft-sheet-chains"): (frozenset({"Chain Orphans"}), False),
    # L1 Exception Detail → Drift. The 'Narrow Drift' drill's anchor is
    # 'Parent Account Drift', so target ledger_drift rows (a parent/control
    # account WITH parent-drift). Row 0 by amount-DESC can be a control
    # account carrying an overdraft/cadence exception but NO drift (e.g.
    # spec's North Pool) → the leaf/parent Drift matview has no row for it →
    # empty destination.
    ("Exception Detail", "l1-sheet-drift"): (frozenset({"ledger_drift"}), False),
    # L1 Exception Detail → Daily Statement. The 'View Daily Statement for
    # this account-day' drill writes account_display + business_day, so it
    # needs a PER-ACCOUNT-DAY check row (transfer-keyed checks — chain / XOR
    # / fan-in — carry a transfer_id + NULL business_day). Exception Detail
    # sorts by amount DESC; those checks have a NULL amount, and PG sorts
    # NULLs FIRST (DuckDB sorts them LAST), so on PG row 0 is a transfer-keyed
    # check with an empty business_day and the drill read skips. Select the
    # first money/balance check (which has business_day) — the same
    # highest-amount row DuckDB's NULLS-LAST order lands at row 0.
    ("Exception Detail", "l1-sheet-daily-statement"): (
        frozenset({
            "drift", "ledger_drift", "overdraft", "limit_breach",
            "expected_eod_balance_breach", "balance_cadence_gap",
            "stuck_pending", "stuck_unbundled",
        }),
        False,
    ),
}

_DST_ANCHOR_FALLBACKS: dict[str, str] = {
    # L1 Daily Statement's first visual is the "Statement of Account"
    # KPI grid; the first Table is "Posting Ledger" (the detail
    # transaction list) which carries account_id + business_day columns
    # we can match against the drilled values.
    "l1-sheet-daily-statement": "Posting Ledger",
    # L1 Transactions sheet's first Table is "Posting Ledger" — same as
    # Daily Statement, surfaces transfer_id rows.
    "l1-sheet-transactions": "Posting Ledger",
    # L1 Drift sheet has two Tables: "Leaf Account Drift" (first) and
    # "Parent Account Drift". The cross-sheet drill source — Exception
    # Detail on l1-sheet-exceptions — sorts by amount DESC, so row 0 is
    # deterministically a CONTROL/PARENT account (the largest dollar
    # magnitudes belong to ledger-aggregating parents, not leaf
    # accounts). Pin the anchor to Parent Account Drift so the test
    # checks the visual where the drilled account_id actually appears.
    # Leaf Account Drift never shows control accounts (it filters
    # account_class=leaf), so the prior "Leaf" pin reliably saw 0
    # matching rows — the test was wrong about which anchor to read,
    # not the DL.3.5 drift_summary matview wiring (operator-confirmed
    # via screenshot: Parent Account Drift renders 8 rows for this row 0).
    "l1-sheet-drift": "Parent Account Drift",
    # L2FT Rails: "Transactions" is the rail-narrowed feed (the
    # existing L2FT cross-sheet drill test uses this anchor).
    "l2ft-sheet-rails": "Transactions",
    # L2FT Chains: "Chain Instances" is the chain-narrowed feed
    # (same existing test pattern).
    "l2ft-sheet-chains": "Chain Instances",
}


def _pick_anchor_visual_title(site: DrillSite) -> str | None:
    """Return the title of a Table visual on ``site.dst_sheet`` whose
    rows we can read for content + picker-narrowed assertions.

    Tries the ``_DST_ANCHOR_FALLBACKS`` mapping first, then falls back
    to the first ``Table`` on the destination sheet. Returns ``None``
    when no Table is found (caller should skip with a clear reason —
    this is a test-data gap to fix in ``_DST_ANCHOR_FALLBACKS``, not a
    code bug to defer).
    """
    sheet_id = str(site.dst_sheet.sheet_id)
    pinned = _DST_ANCHOR_FALLBACKS.get(sheet_id)
    if pinned is not None:
        for v in site.dst_sheet.visuals:
            if isinstance(v, Table) and v.title == pinned:
                return v.title
    for v in site.dst_sheet.visuals:
        if isinstance(v, Table):
            return v.title
    return None


# ---------------------------------------------------------------------------
# Per-source required-filter preselect (pick-first source sheets)
# ---------------------------------------------------------------------------
#
# Some source sheets are EMPTY on cold open by design — a required filter
# defaults to a no-match sentinel until the analyst picks (e.g. L1 Daily
# Statement's pL1DsAccount = '__l1_no_account_selected__'). The guardrail
# opens the sheet with no selection, so row 0 doesn't exist and the read
# skips as a false "data gap". A preselect callable keyed by
# ``src_sheet_id`` drives the required pick(s) so the source populates
# before row 0 is read. Returns True on success, False when the sheet has
# no pickable value (a genuine data gap — caller skips).


def _preselect_daily_statement(driver: "DashboardDriver") -> bool:
    """Put L1 Daily Statement into a populated state before the row-0 read.

    'Posted Money Records' is empty on cold open: ``pL1DsAccount`` defaults
    to the ``__l1_no_account_selected__`` sentinel (a pick-an-account-first
    sheet). The Account universe is available without a Role pick, and the
    Business Day picker defaults to the as_of anchor day — which is data-
    bearing for any account active on that day — so picking an Account is
    enough to fill the table (verified: picking the first account surfaces
    ~29 posting rows with the default Business Day untouched).

    Iterate the account list (capped) rather than trusting index 0 alone:
    on a sparse instance the first alphabetical account may have no posting
    on the default day. Return True at the first account whose pick
    populates 'Posted Money Records'; False when none do (genuine data gap
    → caller skips).
    """
    accounts = driver.filter_options("Account")
    for account in accounts[:_DS_PRESELECT_MAX_TRIES]:
        driver.pick_filter("Account", [account])
        driver.wait_loaded("Posted Money Records")
        if driver.table_row_count("Posted Money Records") > 0:
            return True
    return False


# Cap the Daily Statement account probe — the first data-bearing account
# is alphabetically early on both instances; the cap bounds the pathological
# "every account sparse on the default day" case to a fast skip.
_DS_PRESELECT_MAX_TRIES = 8


# Keyed by ``src_sheet_id`` (str). Only pick-first source sheets need an
# entry; every other source populates on cold open (row 0 is readable
# straight after ``open`` + ``wait_loaded``).
_SRC_REQUIRED_PICKS: dict[str, "_PreselectFn"] = {
    "l1-sheet-daily-statement": _preselect_daily_statement,
}


# ---------------------------------------------------------------------------
# Drill source column resolution
# ---------------------------------------------------------------------------

class _DrillWriteMap(NamedTuple):
    """One drill write's source-column → destination-param mapping."""
    param_name: str
    source_column: str  # raw SQL column name the source visual surfaces


def _resolve_drill_writes(drill: Drill) -> list[_DrillWriteMap]:
    """Return the data-carrying writes — ``(param_name, source_column)``
    pairs the test can read row 0 of from the source visual.

    Drops:

    - ``DrillStaticDateTime`` (no source row — static literal like
      universal-date widening to 1990-01-01)
    - ``DrillResetSentinel`` (no source row — clears a sentinel param
      to its match-all default)
    - Bare ``DrillSourceField`` (carries no column name; the source is
      an internal QS calc-field-id reference, not a row cell)

    Keeps ``Dim`` / ``Measure`` writes (the only kind that maps back to
    a column on the source table that the drilling row 0 will surface).
    """
    out: list[_DrillWriteMap] = []
    for param, source in drill.writes:
        if isinstance(source, (DrillResetSentinel, DrillStaticDateTime)):
            continue
        if isinstance(source, DrillSourceField):
            continue
        # Dim/Measure are the only remaining source kinds that map to a
        # source-row cell; both expose ``.column``. Use ``getattr``-based
        # introspection rather than another ``isinstance`` so the
        # exhaustiveness above stays the single ground-truth and we
        # don't trip pyright's "narrowing already exhaustive" warning.
        col = getattr(source, "column", None)
        if col is None:
            continue
        col_name = getattr(col, "name", None)
        if col_name is None:
            # CalcField-backed Dim/Measure — no source column to
            # match against. Skip; the drill still fires, but we
            # can't tie the source row's cell back to it.
            continue
        out.append(_DrillWriteMap(
            param_name=str(param.name),
            source_column=str(col_name),
        ))
    return out


def _select_source_row_index(
    driver: "DashboardDriver",
    site: DrillSite,
    src_visual_title: str,
) -> int | None:
    """Pick the source row index to drill from.

    Default: row 0. For a source whose destination-specific drill's valid
    values depend on the row's ``check_type`` (see ``_ROW_SELECT_BY_SRC_DST``
    — L2FT 'L2 Violation Detail' → Rails/Chains, L1 'Exception Detail' →
    Drift), pick the first row whose ``check_type`` is valid for THIS drill's
    destination instead of the blind sort-order row 0.

    Returns ``None`` when no row matches (the source genuinely lacks a
    drillable row for this destination — caller skips as a data gap).
    """
    dst_id = str(site.dst_sheet.sheet_id)
    spec = _ROW_SELECT_BY_SRC_DST.get((src_visual_title, dst_id))
    if spec is None:
        return 0
    accepted, exclude_plant = spec
    # entity_a is an L2FT-only column (used only for the plant-marker
    # exclusion); the L1 Exception Detail source has no such column, so read
    # it only when the spec actually excludes the plant marker.
    columns = ["check_type", "entity_a"] if exclude_plant else ["check_type"]
    rows = driver.table_rows(src_visual_title, columns=columns)
    for idx, row in enumerate(rows):
        ct = row.get("check_type") or row.get("Check Type")
        if ct not in accepted:
            continue
        if exclude_plant:
            ea = row.get("entity_a") or row.get("Entity A") or ""
            if _PLANT_ERROR_SENTINEL in ea:
                continue
        return idx
    return None


def _read_source_row_values(
    driver: "DashboardDriver",
    visual_title: str,
    writes: list[_DrillWriteMap],
    row_index: int = 0,
) -> dict[str, str] | None:
    """Read ``row_index`` of ``visual_title`` and return ``{param_name:
    source_value}`` for every column in ``writes`` the row carries.

    Returns ``None`` when the source table has no row at ``row_index``
    (empty table or the selector aimed past the end — the calling test
    skips with a "test-data gap" reason rather than failing).
    """
    columns = [w.source_column for w in writes]
    try:
        rows = driver.table_rows(visual_title, columns=columns)
    except KeyError:
        # The source visual doesn't surface one of the drilled columns
        # in its rendered cells — fall back to reading without column
        # projection. This catches the case where the drill writes a
        # column that's in the dataset but not rendered (e.g. a hidden
        # id column on an aggregated Table). Caller decides whether
        # this is a skip or a fail.
        return None
    if row_index >= len(rows):
        return None
    row = rows[row_index]
    out: dict[str, str] = {}
    for w in writes:
        cell = (
            row.get(w.source_column)
            or row.get(_title_case(w.source_column))
        )
        if cell is None:
            return None
        out[w.param_name] = cell
    return out


def _title_case(sql_column: str) -> str:
    """Mirror QS's auto-derived column header title-casing. Duplicate of
    ``base._title_case_header`` kept inline so this test file doesn't
    reach into ``_drivers`` internals (the no-playwright-leak lint
    allows cross-module imports inside ``tests/e2e/**`` but we keep
    the surface narrow)."""
    _INITIALISMS = frozenset({"id", "sql", "url", "api", "css", "ip"})
    parts: list[str] = []
    for part in sql_column.split("_"):
        if part.lower() in _INITIALISMS:
            parts.append(part.upper())
        else:
            parts.append(part.capitalize())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Parametrize generation
# ---------------------------------------------------------------------------

# L1 + L2FT carry cross-sheet drills; ``inv`` has same-sheet-only
# drills; ``exec`` has none. Each app gets its own test function below
# so the renderer-parametrized ``<app>_dashboard_driver`` fixture
# resolves correctly (see DL.2 fix notes above ``_enumerate_app_sites``).


def _build_app(short: str) -> App:
    """Build a fresh App tree for parametrize-time enumeration.

    Uses a test-helper Config + the default L2 instance. Doesn't share
    session state with the ``l1_app`` / ``l2ft_app`` conftest fixtures —
    those are only available inside test bodies; parametrize fires at
    collection time. The drill-enumeration walk only reads tree shape
    (Sheet IDs, Visual titles, Drill writes), not dataset SQL, so
    config-time differences between the parametrize-time build and the
    fixture-time deploy don't matter.
    """
    from recon_gen.common.l2 import default_l2_instance
    from tests._test_helpers import make_test_config

    if short == "l1":
        from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
        from recon_gen.apps.l1_dashboard.datasets import (
            build_all_l1_dashboard_datasets,
        )
        builder = build_l1_dashboard_app
        dataset_builder = build_all_l1_dashboard_datasets
    elif short == "l2ft":
        from recon_gen.apps.l2_flow_tracing.app import (
            build_l2_flow_tracing_app,
        )
        from recon_gen.apps.l2_flow_tracing.datasets import (
            build_all_l2_flow_tracing_datasets,
        )
        builder = build_l2_flow_tracing_app
        dataset_builder = build_all_l2_flow_tracing_datasets
    else:
        raise ValueError(f"unknown app short slug: {short!r}")

    inst = default_l2_instance()
    cfg = make_test_config()
    # Register the dataset contracts (some app builders look them up
    # at tree-build time via the module-level contract registry).
    dataset_builder(cfg, inst)
    app = builder(cfg, l2_instance=inst)
    app.validate()
    return app


def _enumerate_app_sites(short: str) -> "Iterator[Any]":
    """Yield one ``pytest.param`` per drill site for one app, with a
    legible ID for ``-k`` filtering and triage.

    ``pytest.param`` returns ``pytest.ParameterSet`` at runtime but the
    type isn't exposed publicly; ``Any`` is the pragmatic annotation.

    Split per-app (vs. the prior one-set-for-all-apps) so each test
    function can declare its own renderer-parametrized
    ``<app>_dashboard_driver`` fixture directly in its signature —
    pytest can't disambiguate which renderer-variant to instantiate
    when a parametrized fixture is pulled via
    ``request.getfixturevalue`` (the call returns a single value, but
    the fixture has two configurations), which broke every qs_browser
    cell at collection on the prior shape.
    """
    try:
        app = _build_app(short)
    except Exception as exc:  # pragma: no cover — defensive
        # If app build fails at collection time, surface as a
        # collection error rather than silently dropping coverage.
        yield pytest.param(
            None,
            id=f"{short}/BUILD_FAILED",
            marks=pytest.mark.xfail(
                reason=f"app build failed: {exc!r}", strict=True,
            ),
        )
        return
    for site in iter_cross_sheet_drills(app):
        src_title = getattr(site.src_visual, "title", "(no-title)")
        test_id = (
            f"{site.src_sheet.sheet_id}"
            f"/{src_title}"
            f"/{site.drill.name}"
            f"->{site.dst_sheet.sheet_id}"
        )
        yield pytest.param(site, id=test_id)


_L1_PARAMETRIZE_SET: list[Any] = list(_enumerate_app_sites("l1"))
_L2FT_PARAMETRIZE_SET: list[Any] = list(_enumerate_app_sites("l2ft"))


# ---------------------------------------------------------------------------
# Test body — shared between both per-app test functions
# ---------------------------------------------------------------------------


def _run_cross_sheet_drill_guardrail(
    driver_pair: tuple["DashboardDriver", str],
    site: DrillSite | None,
) -> None:
    """Shared body: drill from row 0 of ``site.src_visual``, assert the
    destination renders content AND its data narrowed to the drilled
    value.

    Per-app test functions wrap this so each can declare its own
    ``<app>_dashboard_driver`` fixture directly (the
    ``[app2]``-parametrized fixture can't be resolved through
    ``request.getfixturevalue`` — pytest needs the parametrize wired
    into the test's own fixture closure).
    """
    assert site is not None, "parametrize bug — site is None"

    driver, dashboard_arg = driver_pair

    src_sheet_name = site.src_sheet.name
    src_visual_title_raw = getattr(site.src_visual, "title", None)
    if not isinstance(src_visual_title_raw, str):
        pytest.fail(
            f"DL.2: source visual on {site.src_sheet.sheet_id} has no "
            f"title attribute — the parametrize layer should have "
            f"filtered this. Drill: {site.drill.name!r}."
        )
    src_visual_title: str = src_visual_title_raw
    dst_sheet_name = site.dst_sheet.name

    anchor_title = _pick_anchor_visual_title(site)
    if anchor_title is None:
        pytest.fail(
            f"DL.2 anchor-visual gap: destination sheet "
            f"{site.dst_sheet.sheet_id!r} has no Table visual the "
            f"guardrail can read. Add an entry to "
            f"_DST_ANCHOR_FALLBACKS pointing at a representative "
            f"Table on this sheet, or build a Table-shaped read "
            f"verb on KPI/Sankey/Bar visuals."
        )

    # Step 1: navigate + wait for the source visual.
    driver.open(dashboard_arg, sheet=src_sheet_name)
    driver.wait_loaded(src_visual_title)

    # Step 1b: pick-first source sheets are empty on cold open (a required
    # filter defaults to a no-match sentinel). Drive the required pick(s)
    # so the source populates before row 0 is read. See _SRC_REQUIRED_PICKS.
    preselect = _SRC_REQUIRED_PICKS.get(str(site.src_sheet.sheet_id))
    if preselect is not None:
        assert preselect(driver), (
            f"Source sheet {src_sheet_name!r} needs a required-filter "
            f"preselect to populate, but no pickable value exists on the "
            f"seed (e.g. no account with an active business day). The seed "
            f"MUST exercise every drill site — a missing pickable value is "
            f"a seed regression to fix seed-side (production-honest "
            f"invariants), not a skip."
        )
        driver.wait_loaded(src_visual_title)

    # Step 2: choose the row to drill (row 0 unless the source hangs
    # multiple destination-specific drills on one column — see
    # _select_source_row_index) and read its drilled-column values.
    row_index = _select_source_row_index(driver, site, src_visual_title)
    assert row_index is not None, (
        f"Source visual {src_visual_title!r} on {src_sheet_name!r} has no "
        f"row valid for the {dst_sheet_name!r} drill ({site.drill.name!r}) "
        f"— e.g. no rail-keyed / chain-orphan row to drill for this "
        f"destination. The seed MUST provide one: a missing drillable row "
        f"is a seed regression to fix seed-side, not a skip."
    )
    writes = _resolve_drill_writes(site.drill)
    source_values: dict[str, str] = {}
    if writes:
        result = _read_source_row_values(
            driver, src_visual_title, writes, row_index=row_index,
        )
        assert result is not None, (
            f"Source visual {src_visual_title!r} on {src_sheet_name!r} is "
            f"empty OR doesn't surface the drilled columns "
            f"({[w.source_column for w in writes]}) in its rendered cells "
            f"at row {row_index} — no row to drill from. The seed MUST "
            f"surface a drillable row here: a missing one is a seed "
            f"regression to fix seed-side, not a skip."
        )
        source_values = result

    # Step 3: fire the drill from the selected row.
    if site.drill.trigger == "DATA_POINT_MENU":
        if row_index == 0:
            driver.drill_from_first_row_via_menu(
                src_visual_title, site.drill.name,
            )
        else:
            driver.drill_from_row_via_menu(
                src_visual_title, row_index, site.drill.name,
            )
    else:
        if row_index != 0:
            driver.drill_from_row(src_visual_title, row_index)
        else:
            driver.drill_from_first_row(src_visual_title)

    # Step 4: wait for the destination's anchor visual to render +
    # assert content.
    driver.wait_loaded(anchor_title)
    dst_columns = [w.source_column for w in writes] if writes else None
    try:
        post_rows = driver.table_rows(anchor_title, columns=dst_columns)
    except KeyError:
        # The destination Table doesn't surface every drilled column
        # in its rendered headers. Fall back to reading without column
        # projection so the content assertion still fires; skip the
        # narrow assertion below.
        post_rows = driver.table_rows(anchor_title)
        dst_columns = None

    if len(post_rows) == 0:
        driver.screenshot()
        pytest.fail(
            f"Drill {site.drill.name!r} from "
            f"{src_visual_title!r} on {src_sheet_name!r} landed on "
            f"{dst_sheet_name!r} (anchor visual {anchor_title!r}) "
            f"but the destination table is empty. "
            f"Source row values: {source_values!r}. "
            f"This is either the user-reported drift→daily-statement "
            f"bug class (drill writes one shape, destination picker "
            f"expects another — narrow to 0 rows) or a destination "
            f"data-availability gap. Check the destination's filter "
            f"control's SourceParameterName matches the drill's "
            f"written param_name AND the dataset's WHERE clause "
            f"value-shape matches the source column's shape "
            f"(account_id vs account_display, etc.)."
        )

    # Step 5: picker-narrowed-to-value assertion. For each drilled
    # write whose source column appears in the destination's rendered
    # rows, every visible row must carry the drilled value.
    if not source_values or dst_columns is None:
        return  # nothing more to check — content gate is the floor

    mismatches: list[tuple[str, str, str, str]] = []  # (col, expected, got, row_idx)
    for w in writes:
        expected = source_values.get(w.param_name)
        if expected is None:
            continue
        for idx, row in enumerate(post_rows):
            cell = (
                row.get(w.source_column)
                or row.get(_title_case(w.source_column))
                or ""
            )
            if cell and cell != expected:
                mismatches.append(
                    (w.source_column, expected, cell, str(idx)),
                )
    if mismatches:
        driver.screenshot()
        # Compact head — 5 mismatches max
        head = mismatches[:5]
        pytest.fail(
            f"Drill {site.drill.name!r} from {src_visual_title!r} "
            f"on {src_sheet_name!r} → {dst_sheet_name!r} "
            f"(anchor {anchor_title!r}) rendered content but the "
            f"destination's data is NOT narrowed to the drilled "
            f"value(s). Source row values: {source_values!r}. "
            f"First {len(head)} mismatch(es): {head}. "
            f"Total mismatches: {len(mismatches)} of {len(post_rows)} "
            f"row × {len(writes)} write checks. "
            f"This is the BS.3-class picker-bypass bug — the drill "
            f"wrote a param the destination's dataset SQL doesn't "
            f"bind, or the param-shape mismatches the WHERE-clause "
            f"value-shape (e.g. drill wrote account_id but the "
            f"WHERE clause matches account_display). Fix lives in "
            f"the source app's Drill construction or the destination's "
            f"control / dataset wiring."
        )


# ---------------------------------------------------------------------------
# Per-app test functions
# ---------------------------------------------------------------------------
#
# Split per app so each function can declare its own
# ``<app>_dashboard_driver`` fixture directly — the prior single-test
# shape pulled the renderer-parametrized fixture via
# ``request.getfixturevalue``, which fails at collection ("requested
# fixture has no parameter defined for test") because pytest can't
# pick which ``[app2]`` configuration to instantiate when the
# fixture isn't in the test's own closure. Splitting also mirrors the
# canonical pattern in ``test_l2ft_cross_sheet_drill.py`` /
# ``test_l1_cross_sheet_drill_date_widening.py``.


@pytest.mark.parametrize("site", _L1_PARAMETRIZE_SET)
def test_l1_cross_sheet_drill_lands_populated_and_narrowed(
    l1_dashboard_driver: tuple["DashboardDriver", str],
    site: DrillSite | None,
) -> None:
    """L1 leg of the cross-sheet drill content + picker-value guardrail.

    See the module docstring for the two-assertion contract and the
    drift→daily-statement bug this guardrail surfaces.
    """
    _run_cross_sheet_drill_guardrail(l1_dashboard_driver, site)


@pytest.mark.parametrize("site", _L2FT_PARAMETRIZE_SET)
def test_l2ft_cross_sheet_drill_lands_populated_and_narrowed(
    l2ft_dashboard_driver: tuple["DashboardDriver", str],
    site: DrillSite | None,
) -> None:
    """L2FT leg of the cross-sheet drill content + picker-value guardrail.

    See the module docstring for the two-assertion contract.
    """
    _run_cross_sheet_drill_guardrail(l2ft_dashboard_driver, site)
