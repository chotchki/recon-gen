"""Class-level test: cross-sheet drills into universally-date-scoped
sheets must widen the destination's date range.

v8.5.7 — bug class: a drill from a current-state sheet (Pending Aging,
Unbundled Aging, Supersession Audit — none in the universal date
filter scope) into the Transactions sheet (which IS scoped) lost any
row whose ``posting`` was older than the picker's default 7-day
window. The drill wrote ``pL1TxTransfer`` but did NOT write the date
range params, so the Transactions sheet's universal filter remained
narrow and the target row fell outside it.

Fix: the drills now also write ``pL1DateStart=1990-01-01`` and
``pL1DateEnd=2099-12-31`` via ``DrillStaticDateTime`` — wide-window
"all time" so the target row is always in scope.

This walker:

1. Builds the L1 dashboard analysis JSON.
2. Finds the Transactions sheet's ``SheetId``.
3. Walks every visual on every other sheet, finds every drill action
   whose ``NavigationOperation.LocalNavigationConfiguration.TargetSheetId``
   is the Transactions sheet, and asserts the drill's
   ``SetParametersOperation.ParameterValueConfigurations`` includes
   both ``pL1DateStart`` and ``pL1DateEnd`` writes with the wide
   static values.

Failure means a new cross-sheet drill into Transactions was added
without the date widening — re-add ``*_wide_date_writes()`` to its
``writes=`` list, or the dropdown bug returns.
"""

from __future__ import annotations

from typing import Any

import pytest

from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
from tests._test_helpers import make_test_config


_TRANSACTIONS_SHEET_ID = "l1-sheet-transactions"
_DATE_START_PARAM = "pL1DateStart"
_DATE_END_PARAM = "pL1DateEnd"
_EXPECTED_WIDE_START = "1990-01-01T00:00:00.000Z"
_EXPECTED_WIDE_END = "2099-12-31T00:00:00.000Z"

# Type alias — AWS QS JSON dicts have heterogeneous nested shapes; using
# Any matches the dynamic-dict walk these tests perform.
_JsonDict = dict[str, Any]


@pytest.fixture(scope="module")
def emitted() -> _JsonDict:
    cfg = make_test_config()
    app = build_l1_dashboard_app(cfg)
    return app.emit_analysis().to_aws_json()


def _drills_into_transactions(
    emitted: _JsonDict,
) -> list[tuple[str, str, _JsonDict]]:
    """Yield ``(source_sheet_id, source_visual_id, drill_action_dict)`` for
    every drill whose target sheet is the Transactions sheet."""
    out: list[tuple[str, str, _JsonDict]] = []
    definition: _JsonDict = emitted.get("Definition") or {}
    sheets: list[_JsonDict] = definition.get("Sheets") or []
    for sheet in sheets:
        sheet_id: str = sheet.get("SheetId", "<unknown>")
        if sheet_id == _TRANSACTIONS_SHEET_ID:
            # Skip drills that target the same sheet — only cross-sheet
            # drills INTO Transactions are the bug class.
            continue
        visuals: list[_JsonDict] = sheet.get("Visuals") or []
        for v in visuals:
            # Drill actions live under ``ChartConfiguration``-adjacent
            # ``Actions``; the wrapping VisualVisual key varies by
            # visual type, so we walk all visual subtypes uniformly.
            for visual_kind, visual_body in v.items():
                if not isinstance(visual_body, dict):
                    continue
                # visual_body narrowed to dict[str, Any] via isinstance.
                visual_body_d: _JsonDict = visual_body  # type: ignore[assignment]
                visual_id: str = visual_body_d.get("VisualId", "<unknown>")
                actions: list[_JsonDict] = visual_body_d.get("Actions") or []
                for action in actions:
                    nav = _find_target_sheet(action)
                    if nav == _TRANSACTIONS_SHEET_ID:
                        out.append((sheet_id, visual_id, action))
                _ = visual_kind
    return out


def _find_target_sheet(action: _JsonDict) -> str | None:
    """Return the action's NavigationOperation target sheet id, or None
    if the action isn't a navigation drill."""
    ops: list[_JsonDict] = action.get("ActionOperations") or []
    for op in ops:
        nav: _JsonDict = op.get("NavigationOperation") or {}
        local: _JsonDict = nav.get("LocalNavigationConfiguration") or {}
        target: str | None = local.get("TargetSheetId")
        if target:
            return target
    return None


def _written_param_values(action: _JsonDict) -> dict[str, str]:
    """Return ``{param_name: static_string_value}`` for every parameter
    write on the action that uses CustomValues (DateTimeValues or
    StringValues). SourceField writes are excluded — they don't carry a
    static value."""
    out: dict[str, str] = {}
    ops: list[_JsonDict] = action.get("ActionOperations") or []
    for op in ops:
        sp: _JsonDict = op.get("SetParametersOperation") or {}
        configs: list[_JsonDict] = sp.get("ParameterValueConfigurations") or []
        for cfg in configs:
            name: str | None = cfg.get("DestinationParameterName")
            if name is None:
                continue
            value_outer: _JsonDict = cfg.get("Value") or {}
            value_cv: _JsonDict = (
                value_outer.get("CustomValuesConfiguration") or {}
            )
            value: _JsonDict = value_cv.get("CustomValues") or {}
            for key in ("DateTimeValues", "StringValues"):
                vals: list[str] = value.get(key) or []
                if vals:
                    out[name] = vals[0]
                    break
    return out


def test_drills_into_transactions_widen_date_range(emitted: _JsonDict) -> None:
    """Every cross-sheet drill into the Transactions sheet must write
    the wide-window date-range params so the target row survives the
    destination's universal date filter."""
    drills = _drills_into_transactions(emitted)
    assert drills, (
        "No cross-sheet drills into Transactions found in the emitted "
        "L1 dashboard JSON. Either the test selector is wrong or the "
        "L1 app no longer has any drills into Transactions (in which "
        "case this test is obsolete and should be removed)."
    )

    bad: list[str] = []
    for sheet_id, visual_id, action in drills:
        writes = _written_param_values(action)
        start = writes.get(_DATE_START_PARAM)
        end = writes.get(_DATE_END_PARAM)
        if start != _EXPECTED_WIDE_START or end != _EXPECTED_WIDE_END:
            bad.append(
                f"  sheet={sheet_id!r} visual={visual_id!r} "
                f"action={action.get('Name')!r} "
                f"start={start!r} end={end!r}"
            )
    assert not bad, (
        f"Cross-sheet drills into Transactions are missing the wide "
        f"date-range writes (expected start="
        f"{_EXPECTED_WIDE_START!r}, end={_EXPECTED_WIDE_END!r}). Drills "
        f"that write only ``pL1TxTransfer`` will land on a Transactions "
        f"sheet whose universal date filter excludes the target row "
        f"when the row's posting is older than the picker's default "
        f"7-day window:\n" + "\n".join(bad)
    )


def test_drills_into_transactions_count_matches_known_sites(
    emitted: _JsonDict,
) -> None:
    """Sanity check: there should be exactly 3 cross-sheet drills into
    Transactions (Pending Aging / Unbundled Aging / Supersession Audit
    detail tables). If a new one is added, this fails — extending the
    expected count is fine, but flag it as a deliberate review point so
    the new drill's ``writes=`` is checked for the wide-date pattern."""
    drills = _drills_into_transactions(emitted)
    expected = 3
    assert len(drills) == expected, (
        f"Expected {expected} cross-sheet drills into Transactions "
        f"(Pending Aging / Unbundled Aging / Supersession Audit); "
        f"found {len(drills)}. If a new drill was added intentionally, "
        f"bump the expected count and confirm the new drill includes "
        f"``*_wide_date_writes()`` in its writes list. Found:\n"
        + "\n".join(
            f"  {sid} / {vid} / {a.get('Name')!r}"
            for sid, vid, a in drills
        )
    )
