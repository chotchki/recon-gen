"""DL.1 — Unit tests for ``iter_cross_sheet_drills``.

The kitchen-sink app (``tests/e2e/_kitchen_app.py``) wires every typed
L.1 primitive at least once including BOTH cross-sheet drills (bar /
table / sankey → drill_target sheet) AND one same-sheet drill (bar's
walk-the-flow drill that re-renders the showcase sheet around the
clicked category). That makes it the natural fixture for proving the
enumeration helper:

- yields exactly the 3 expected cross-sheet drills
- excludes the 1 same-sheet drill
- skips visuals without an ``actions`` attribute (kitchen-sink's KPI)
  without crashing
- all yielded ``dst_sheet`` refs resolve to actual Sheet objects on
  the analysis (no dangling target_sheet)

DL.2 will consume the enumeration helper to drive parametrized
content + picker-value assertions across the real apps (L1 Dashboard /
L2 Flow Tracing / Investigation / Executives) — this unit-tier test
guards the enumeration mechanics in isolation so DL.2's failures
point at the gate's bugs, not the walker's.
"""

from __future__ import annotations

import pytest

from recon_gen.common.tree import App, KPI, Sheet
from tests._test_helpers import make_test_config
from tests.e2e._helpers.drill_enumeration import (
    DrillSite, iter_cross_sheet_drills,
)
from tests.e2e._kitchen_app import build_kitchen_app


@pytest.fixture
def kitchen_app() -> App:
    return build_kitchen_app(make_test_config())


@pytest.fixture
def sites(kitchen_app: App) -> list[DrillSite]:
    return list(iter_cross_sheet_drills(kitchen_app))


def test_yields_three_cross_sheet_drills(sites: list[DrillSite]) -> None:
    """Kitchen-sink wires 3 cross-sheet drills: bar / table / sankey
    on Visuals Showcase, all targeting Drill Target.

    If a future commit adds a new cross-sheet drill to the kitchen-sink,
    bump this count + verify the new drill's destination is the
    intended one — same shape as the json-tier sanity check on
    Transactions drills."""
    assert len(sites) == 3, (
        f"Expected 3 cross-sheet drills on the kitchen-sink "
        f"(bar / table / sankey → drill_target); got {len(sites)}.\n"
        + "\n".join(
            f"  src={s.src_sheet.name!r} visual_title="
            f"{getattr(s.src_visual, 'title', '<no title>')!r} "
            f"drill={s.drill.name!r} -> dst={s.dst_sheet.name!r}"
            for s in sites
        )
    )


def test_cross_sheet_drill_titles_match_expected(
    sites: list[DrillSite],
) -> None:
    """Identify the 3 drills by visual title (analyst-facing, stable —
    not by auto-derived ``visual_id``)."""
    titles = {
        getattr(s.src_visual, "title", None) for s in sites
    }
    assert titles == {"Detail Table", "By Category", "Flow"}, (
        f"Expected cross-sheet drills from visuals "
        f"{{'Detail Table', 'By Category', 'Flow'}}; got {titles}"
    )


def test_same_sheet_drill_excluded(sites: list[DrillSite]) -> None:
    """The kitchen-sink's same-sheet drill on the bar chart
    ('Filter this sheet by category', target_sheet=AUTO → resolves to
    Visuals Showcase) must NOT appear — same-sheet drills are out of
    scope for the cross-sheet content + picker-value gate."""
    same_sheet_names = {
        s.drill.name for s in sites
        if s.drill.name == "Filter this sheet by category"
    }
    assert same_sheet_names == set(), (
        f"Same-sheet drill leaked into iter_cross_sheet_drills output: "
        f"{same_sheet_names}. Cross-sheet enumeration must filter out "
        f"drills whose resolved target_sheet is the source sheet."
    )
    # Also check the converse: src ≠ dst for every yielded site.
    for s in sites:
        assert s.src_sheet.sheet_id != s.dst_sheet.sheet_id, (
            f"Yielded site is same-sheet (src=={s.dst_sheet.sheet_id!r}): "
            f"drill={s.drill.name!r}"
        )


def test_skips_visuals_without_actions(
    kitchen_app: App, sites: list[DrillSite],
) -> None:
    """Kitchen-sink's showcase sheet contains a KPI ('Total Amount')
    — KPI visuals don't carry an ``actions`` attribute (per the QS
    model). The enumeration must skip these silently without crashing
    and obviously without yielding any sites from them."""
    assert kitchen_app.analysis is not None
    kpis = [
        v for sheet in kitchen_app.analysis.sheets
        for v in sheet.visuals
        if isinstance(v, KPI)
    ]
    assert kpis, (
        "Kitchen-sink unexpectedly has no KPI visuals — "
        "this test's premise (KPI = the 'no actions' case) is invalid."
    )
    # No site's src_visual is one of the KPI visuals.
    kpi_ids = {id(k) for k in kpis}
    leaked = [s for s in sites if id(s.src_visual) in kpi_ids]
    assert not leaked, (
        f"iter_cross_sheet_drills yielded a site whose source visual "
        f"is a KPI ({len(leaked)} sites). KPI has no actions field; "
        f"the walker should skip via getattr default."
    )


def test_destination_sheets_resolve_to_registered_analysis_sheets(
    kitchen_app: App, sites: list[DrillSite],
) -> None:
    """Every yielded ``dst_sheet`` is an actual ``Sheet`` instance
    registered on the analysis — no dangling refs, no AUTO sentinel
    leak. Same invariant ``_validate_drill_destinations`` asserts at
    emit time, restated at the walker level so DL.2's parametrize args
    are guaranteed-valid Sheet objects."""
    assert kitchen_app.analysis is not None
    registered = {id(s) for s in kitchen_app.analysis.sheets}
    for site in sites:
        assert isinstance(site.dst_sheet, Sheet), (
            f"dst_sheet is not a Sheet instance: {type(site.dst_sheet)!r}"
        )
        assert id(site.dst_sheet) in registered, (
            f"Drill {site.drill.name!r} targets "
            f"{site.dst_sheet.sheet_id!r} but that Sheet object isn't "
            f"registered on the analysis."
        )


def test_iter_returns_iterator_not_list(kitchen_app: App) -> None:
    """``iter_cross_sheet_drills`` is a generator — exhausting it twice
    yields the second pass empty. Consumers that need multiple passes
    must materialize via ``list(...)``. Documenting the shape so DL.2's
    parametrize doesn't accidentally double-consume."""
    it = iter_cross_sheet_drills(kitchen_app)
    first_pass = list(it)
    second_pass = list(it)
    assert first_pass, "first pass should yield drills"
    assert second_pass == [], (
        "second pass on the same iterator should be empty — "
        "iter_cross_sheet_drills is a generator, not a re-iterable"
    )
