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
    from collections.abc import Iterator

    from tests.e2e._drivers import DashboardDriver


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    tier(Tier.QS_BROWSER),
    needs(Need.AWS_QS, Need.PLAYWRIGHT),
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

_DST_ANCHOR_FALLBACKS: dict[str, str] = {
    # L1 Daily Statement's first visual is the "Statement of Account"
    # KPI grid; the first Table is "Posting Ledger" (the detail
    # transaction list) which carries account_id + business_day columns
    # we can match against the drilled values.
    "l1-sheet-daily-statement": "Posting Ledger",
    # L1 Transactions sheet's first Table is "Posting Ledger" — same as
    # Daily Statement, surfaces transfer_id rows.
    "l1-sheet-transactions": "Posting Ledger",
    # L1 Drift sheet's first Table is "Leaf Account Drift" — the
    # account-day drill destination from Today's Exceptions.
    "l1-sheet-drift": "Leaf Account Drift",
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


def _read_source_row_values(
    driver: "DashboardDriver",
    visual_title: str,
    writes: list[_DrillWriteMap],
) -> dict[str, str] | None:
    """Read row 0 of ``visual_title`` and return ``{param_name:
    source_value}`` for every column in ``writes`` the row carries.

    Returns ``None`` when the source table is empty (no row to drill
    from — the calling test skips with a "test-data gap" reason rather
    than failing).
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
    if not rows:
        return None
    row0 = rows[0]
    out: dict[str, str] = {}
    for w in writes:
        cell = (
            row0.get(w.source_column)
            or row0.get(_title_case(w.source_column))
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

class _AppRef(NamedTuple):
    """One enumerated app for the parametrize set."""
    short: str  # "l1" | "l2ft" — matches the driver-fixture short slug
    builder_module: str  # importable module path with build_<X>_app


_APPS_WITH_CROSS_SHEET_DRILLS: list[_AppRef] = [
    _AppRef("l1", "recon_gen.apps.l1_dashboard.app"),
    _AppRef("l2ft", "recon_gen.apps.l2_flow_tracing.app"),
    # inv app has same-sheet-only drills; exec has none.
]


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
    app.emit_analysis()
    return app


def _enumerate_parametrize() -> "Iterator[Any]":
    """Yield one ``pytest.param`` per (app, drill) pair, with a
    legible ID for ``-k`` filtering and triage.

    ``pytest.param`` returns ``pytest.ParameterSet`` at runtime but the
    type isn't exposed publicly; ``Any`` is the pragmatic annotation.
    """
    for app_ref in _APPS_WITH_CROSS_SHEET_DRILLS:
        try:
            app = _build_app(app_ref.short)
        except Exception as exc:  # pragma: no cover — defensive
            # If app build fails at collection time, surface as a
            # collection error rather than silently dropping coverage.
            yield pytest.param(
                app_ref.short, None,
                id=f"{app_ref.short}/BUILD_FAILED",
                marks=pytest.mark.xfail(
                    reason=f"app build failed: {exc!r}", strict=True,
                ),
            )
            continue
        for site in iter_cross_sheet_drills(app):
            src_title = getattr(site.src_visual, "title", "(no-title)")
            test_id = (
                f"{app_ref.short}/{site.src_sheet.sheet_id}"
                f"/{src_title}"
                f"/{site.drill.name}"
                f"->{site.dst_sheet.sheet_id}"
            )
            yield pytest.param(app_ref.short, site, id=test_id)


_PARAMETRIZE_SET: list[Any] = list(_enumerate_parametrize())


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "app_short,site", _PARAMETRIZE_SET,
)
def test_cross_sheet_drill_lands_populated_and_narrowed(
    request: pytest.FixtureRequest,
    app_short: str,
    site: DrillSite | None,
) -> None:
    """For every cross-sheet ``Drill`` in every app: drill from row 0
    of the source visual, then assert the destination renders content
    AND its data narrowed to the drilled value.

    See the module docstring for the two-assertion contract and the
    drift→daily-statement bug this guardrail surfaces.
    """
    assert site is not None, "parametrize bug — site is None"

    # Pull the renderer-parametrized fixture by short slug. The
    # ``<app>_dashboard_driver`` fixtures are themselves parametrized
    # over ``[qs, app2]``, so this test inherits that × the drill
    # enumeration above.
    driver_fixture = f"{app_short}_dashboard_driver"
    driver_pair = request.getfixturevalue(driver_fixture)
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

    # Step 2: read row 0's source values for the drilled columns.
    writes = _resolve_drill_writes(site.drill)
    source_values: dict[str, str] = {}
    if writes:
        result = _read_source_row_values(driver, src_visual_title, writes)
        if result is None:
            pytest.skip(
                f"Source visual {src_visual_title!r} on "
                f"{src_sheet_name!r} is empty OR doesn't surface "
                f"the drilled columns "
                f"({[w.source_column for w in writes]}) in its rendered "
                f"cells — no row to drill from. This is a test-data "
                f"gap (seed needs a row exercising this drill), not a "
                f"drill-mechanics bug."
            )
        source_values = result

    # Step 3: fire the drill.
    if site.drill.trigger == "DATA_POINT_MENU":
        driver.drill_from_first_row_via_menu(
            src_visual_title, site.drill.name,
        )
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
