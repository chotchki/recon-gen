"""Class-level test: cross-sheet drills into universally-date-scoped
sheets must widen the destination's date range.

v8.5.7 — bug class: a drill from a current-state sheet (Pending Aging,
Unbundled Aging, Supersession Audit — none in the universal date
filter scope) into the Transactions sheet (which IS scoped) lost any
row whose ``posting`` was older than the picker's default 7-day
window. The drill wrote ``pL1TxTransferId`` but did NOT write the date
range params, so the Transactions sheet's universal filter remained
narrow and the target row fell outside it.

Fix: the drills now also write ``pL1DateStart=1990-01-01`` and
``pL1DateEnd=2099-12-31`` via ``DrillStaticDateTime`` — wide-window
"all time" so the target row is always in scope.

This walker:

1. Builds the L1 dashboard tree (auto-IDs resolved).
2. Walks every cross-sheet drill via ``iter_cross_sheet_drills(app)``,
   keeping those whose ``dst_sheet`` is the Transactions sheet.
3. Reads each drill's ``writes`` directly off the tree and asserts both
   ``pL1DateStart`` and ``pL1DateEnd`` are written as ``DrillStaticDateTime``
   with the wide static values — no emit, no JSON parse.

Failure means a new cross-sheet drill into Transactions was added
without the date widening — re-add ``*_wide_date_writes()`` to its
``writes=`` list, or the dropdown bug returns.
"""

from __future__ import annotations

import pytest

from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
from recon_gen.common.tree import App, Drill, DrillStaticDateTime
from tests._test_helpers import make_test_config
from tests.e2e._helpers.drill_enumeration import iter_cross_sheet_drills


_TRANSACTIONS_SHEET_ID = "l1-sheet-transactions"
_DATE_START_PARAM = "pL1DateStart"
_DATE_END_PARAM = "pL1DateEnd"
_EXPECTED_WIDE_START = "1990-01-01T00:00:00.000Z"
_EXPECTED_WIDE_END = "2099-12-31T00:00:00.000Z"

@pytest.fixture(scope="module")
def kitchen_l1_app() -> App:
    """Build the L1 Dashboard app, with auto-IDs resolved.

    ``iter_cross_sheet_drills`` also calls ``resolve_auto_ids()``, but
    pinning the IDs here materializes drill ``target_sheet`` + ``visual_id``
    so the tree walker reads a fully-resolved tree. Built once per module.
    """
    cfg = make_test_config()
    app = build_l1_dashboard_app(cfg)
    app.resolve_auto_ids()
    return app


def _drills_into_transactions(app: App) -> list[tuple[str, str, Drill]]:
    """Return ``(source_sheet_id, source_visual_id, drill)`` for every
    cross-sheet drill whose target is the Transactions sheet.

    Walks the tree via ``iter_cross_sheet_drills(app)`` — no emit. The
    ``Drill`` object carries its own ``writes``, so the date-widening
    assertion reads the tree directly rather than re-parsing the
    serialized ``SetParametersOperation``.
    """
    out: list[tuple[str, str, Drill]] = []
    for site in iter_cross_sheet_drills(app):
        if site.dst_sheet.sheet_id != _TRANSACTIONS_SHEET_ID:
            continue
        sid = str(site.src_sheet.sheet_id)
        # After resolve_auto_ids, visual_id is the resolved VisualId.
        vid_raw = getattr(site.src_visual, "visual_id", None)
        vid = str(vid_raw) if vid_raw is not None else "<unknown>"
        out.append((sid, vid, site.drill))
    return out


def _written_param_values(drill: Drill) -> dict[str, str]:
    """Return ``{param_name: static_iso_datetime}`` for every
    ``DrillStaticDateTime`` write on the drill — the tree-native source of
    what the emit serializes as ``SetParametersOperation`` DateTimeValues.
    Dim / Measure SourceField writes carry no static literal and are
    excluded (the old emit-walk likewise skipped non-CustomValues writes)."""
    out: dict[str, str] = {}
    for param, source in drill.writes:
        if isinstance(source, DrillStaticDateTime):
            out[str(param.name)] = source.value
    return out


def test_drills_into_transactions_widen_date_range(
    kitchen_l1_app: App,
) -> None:
    """Every cross-sheet drill into the Transactions sheet must write
    the wide-window date-range params so the target row survives the
    destination's universal date filter."""
    drills = _drills_into_transactions(kitchen_l1_app)
    assert drills, (
        "No cross-sheet drills into Transactions found in the L1 "
        "dashboard tree. Either the test selector is wrong or the "
        "L1 app no longer has any drills into Transactions (in which "
        "case this test is obsolete and should be removed)."
    )

    bad: list[str] = []
    for sheet_id, visual_id, drill in drills:
        writes = _written_param_values(drill)
        start = writes.get(_DATE_START_PARAM)
        end = writes.get(_DATE_END_PARAM)
        if start != _EXPECTED_WIDE_START or end != _EXPECTED_WIDE_END:
            bad.append(
                f"  sheet={sheet_id!r} visual={visual_id!r} "
                f"action={drill.name!r} "
                f"start={start!r} end={end!r}"
            )
    assert not bad, (
        f"Cross-sheet drills into Transactions are missing the wide "
        f"date-range writes (expected start="
        f"{_EXPECTED_WIDE_START!r}, end={_EXPECTED_WIDE_END!r}). Drills "
        f"that write only ``pL1TxTransferId`` will land on a Transactions "
        f"sheet whose universal date filter excludes the target row "
        f"when the row's posting is older than the picker's default "
        f"7-day window:\n" + "\n".join(bad)
    )


def test_drills_into_transactions_count_matches_known_sites(
    kitchen_l1_app: App,
) -> None:
    """Sanity check: there should be exactly 5 cross-sheet drills into
    Transactions (Pending Aging / Unbundled Aging / Supersession Audit
    Transactions Audit table / Daily Statement / Supersession Audit
    Transactions Audit row drill — Phase DA wired the last one). If a
    new one is added, this fails — extending the expected count is fine,
    but flag it as a deliberate review point so the new drill's
    ``writes=`` is checked for the wide-date pattern."""
    drills = _drills_into_transactions(kitchen_l1_app)
    expected = 4
    assert len(drills) == expected, (
        f"Expected {expected} cross-sheet drills into Transactions; "
        f"found {len(drills)}. If a new drill was added intentionally, "
        f"bump the expected count and confirm the new drill includes "
        f"``*_wide_date_writes()`` in its writes list. Found:\n"
        + "\n".join(
            f"  {sid} / {vid} / {d.name!r}"
            for sid, vid, d in drills
        )
    )
