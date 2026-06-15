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
from recon_gen.common.tree import App
from tests._test_helpers import make_test_config
from tests.e2e._helpers.drill_enumeration import iter_cross_sheet_drills


_TRANSACTIONS_SHEET_ID = "l1-sheet-transactions"
_DATE_START_PARAM = "pL1DateStart"
_DATE_END_PARAM = "pL1DateEnd"
_EXPECTED_WIDE_START = "1990-01-01T00:00:00.000Z"
_EXPECTED_WIDE_END = "2099-12-31T00:00:00.000Z"

# Type alias — AWS QS JSON dicts have heterogeneous nested shapes; using
# Any matches the dynamic-dict walk these tests perform.
_JsonDict = dict[str, Any]


@pytest.fixture(scope="module")
def kitchen_l1_app() -> App:
    """Build the L1 Dashboard app, with auto-IDs resolved.

    Shared between the tree walker (which needs the resolved tree to
    iterate drills) and the JSON fixture (which needs the same App
    emitted to dict). Built once per module to keep the test cheap.
    """
    cfg = make_test_config()
    app = build_l1_dashboard_app(cfg)
    # Idempotent — the iter_cross_sheet_drills helper also calls this,
    # but materializing IDs here pins them BEFORE emit_analysis() runs
    # so the JSON's action_id / visual_id values match what the tree
    # walker observes (no auto-ID drift between the two reads).
    app.resolve_auto_ids()
    return app


@pytest.fixture(scope="module")
def emitted(kitchen_l1_app: App) -> _JsonDict:
    return kitchen_l1_app.emit_analysis().to_aws_json()


def _drills_into_transactions(
    app: App, emitted: _JsonDict,
) -> list[tuple[str, str, _JsonDict]]:
    """Return ``(source_sheet_id, source_visual_id, drill_action_dict)``
    for every cross-sheet drill whose target is the Transactions sheet.

    Migrated to ``iter_cross_sheet_drills(app)`` (DL.1) — the tree
    walker identifies the drills (by ``src_sheet`` / ``src_visual`` /
    ``drill.action_id``), then this helper looks up the corresponding
    action dict in the emitted JSON via those same IDs. Replaces the
    pre-DL.1 hand-rolled dict walk; the public test's assertion shape
    is unchanged.
    """
    out: list[tuple[str, str, _JsonDict]] = []
    # JSON-side index for action lookup: (sheet_id, visual_id, action_name)
    # → action dict. (Name is used rather than action_id because the
    # Drill emit's ``CustomActionName`` carries the human-readable name
    # and the action_id is captured in the same dict — we key on the
    # composite that uniquely identifies a drill in the emitted JSON.)
    by_key: dict[tuple[str, str, str], _JsonDict] = {}
    definition: _JsonDict = emitted.get("Definition") or {}
    sheets: list[_JsonDict] = definition.get("Sheets") or []
    for sheet in sheets:
        sid: str = sheet.get("SheetId", "<unknown>")
        visuals: list[_JsonDict] = sheet.get("Visuals") or []
        for v in visuals:
            for _, visual_body in v.items():
                if not isinstance(visual_body, dict):
                    continue
                visual_body_d: _JsonDict = visual_body  # type: ignore[assignment]: third-party stub or test scaffolding cascade
                vid: str = visual_body_d.get("VisualId", "<unknown>")
                actions_list: list[_JsonDict] = (
                    visual_body_d.get("Actions") or []
                )
                for action in actions_list:
                    name: str = action.get("Name") or "<unknown>"
                    by_key[(sid, vid, name)] = action

    for site in iter_cross_sheet_drills(app):
        if site.dst_sheet.sheet_id != _TRANSACTIONS_SHEET_ID:
            continue
        sid = str(site.src_sheet.sheet_id)
        # After resolve_auto_ids, visual_id is the resolved VisualId.
        vid_raw = getattr(site.src_visual, "visual_id", None)
        vid = str(vid_raw) if vid_raw is not None else "<unknown>"
        name = site.drill.name
        action_dict = by_key.get((sid, vid, name))
        assert action_dict is not None, (
            f"Tree walker yielded drill (sheet={sid!r} visual={vid!r} "
            f"name={name!r}) but no matching action found in the "
            f"emitted JSON — visual-id / action-name resolution drift."
        )
        out.append((sid, vid, action_dict))
    return out


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


def test_drills_into_transactions_widen_date_range(
    kitchen_l1_app: App, emitted: _JsonDict,
) -> None:
    """Every cross-sheet drill into the Transactions sheet must write
    the wide-window date-range params so the target row survives the
    destination's universal date filter."""
    drills = _drills_into_transactions(kitchen_l1_app, emitted)
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
    kitchen_l1_app: App, emitted: _JsonDict,
) -> None:
    """Sanity check: there should be exactly 5 cross-sheet drills into
    Transactions (Pending Aging / Unbundled Aging / Supersession Audit
    Transactions Audit table / Daily Statement / Supersession Audit
    Transactions Audit row drill — Phase DA wired the last one). If a
    new one is added, this fails — extending the expected count is fine,
    but flag it as a deliberate review point so the new drill's
    ``writes=`` is checked for the wide-date pattern."""
    drills = _drills_into_transactions(kitchen_l1_app, emitted)
    expected = 4
    assert len(drills) == expected, (
        f"Expected {expected} cross-sheet drills into Transactions; "
        f"found {len(drills)}. If a new drill was added intentionally, "
        f"bump the expected count and confirm the new drill includes "
        f"``*_wide_date_writes()`` in its writes list. Found:\n"
        + "\n".join(
            f"  {sid} / {vid} / {a.get('Name')!r}"
            for sid, vid, a in drills
        )
    )
