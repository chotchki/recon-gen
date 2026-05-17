"""AA.A.6 — generic additive-pickers row-survival test for L1 sheets.

Pattern: for each L1 sheet with ≥2 pickers, fetch a known-good anchor
row from the underlying matview, drive every picker to that row's
values *additively*, assert the target visual still renders ≥1 row.

Catches three classes of regression in one test body:

- A picker over-narrows and zeros the table even when matching a
  known anchor row (WHERE clause is wrong column / wrong operator).
- A picker's binding goes stale (e.g. AA.E.2's silent miss — dropdown
  bound to bare ``account_id`` while WHERE expected the display-form
  concat).
- Combined filters compose wrongly (AND vs OR mixup; double-quoted
  literal; etc).

Parametrized over ``[qs, app2]`` via ``l1_dashboard_driver`` so a
parity gap = a real wiring divergence.

Spike resolution (AA.A.6 PLAN entry, locked 2026-05-17): path (1) —
DB-direct anchor query (precedent: ``_daily_statement_pick.py``). The
"intersect-advertised-options" path was rejected as fragile to seed
shape; the "split-into-bespoke" path was rejected because it loses
the generic-coverage benefit that's the whole point of AA.A.6.

v1 scope: Drift + Overdraft (structurally identical: date range +
Account + Account Role pickers + Table target). Daily Statement is
covered by the pre-existing ``test_daily_statement_*`` tests via the
bespoke ``find_account_day_with_data`` helper; not re-wired here.
Follow-on commits will extend ``L1_PICKER_SPECS`` to Limit Breach /
Today's Exceptions / Transactions / Pending Aging / Unbundled Aging
(those need per-sheet picker→column mapping; some have off-table
columns like Today's Exceptions' Check Type that the generic shape
handles via the spec's ``anchor_columns``).
"""

from __future__ import annotations

import pytest

from tests.e2e._picker_anchor import (
    PickerSpec,
    SheetAnchorSpec,
    apply_anchor_to_pickers,
    fetch_anchor_row,
    non_matching_dropdown_value,
    picker_value,
)


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


# Per-sheet picker→column maps. To extend: add a new SheetAnchorSpec
# entry following the same shape — the test body below picks it up
# automatically.
#
# Anchor column choices:
#   - Always include every column referenced by a picker's `column`
#     OR its `format` callable (e.g. account_display needs both
#     account_name + account_id).
#   - `anchor_order` biases the pick: typically `business_day_start
#     DESC` so the anchor lands on a recent day (matches what an
#     analyst sees on open). For sheets where the matview is empty
#     often (zero violations on a fresh seed), the row-survival
#     assertion is meaningful only when the seed actually plants
#     something for that sheet — confirm via ``data apply`` first.
L1_PICKER_SPECS: tuple[SheetAnchorSpec, ...] = (
    SheetAnchorSpec(
        sheet_name="Drift",
        target_visual="Leaf Account Drift",
        anchor_table="{p}_drift",
        anchor_columns=(
            "account_id", "account_name", "account_role", "business_day_start",
        ),
        # Bias toward low cust-N so the picked anchor lands in MUI
        # Autocomplete's first-visible-window (the QS Account dropdown
        # virtualizes — typed-filter doesn't reliably narrow to a
        # specific Customer N when the seed has 25+ accounts, see the
        # AA.A.6 v1 chain bs8k6zogh failure capture). Bias-not-fix —
        # deeper MUI-Autocomplete typed-filter behavior follow-on.
        anchor_order="account_id ASC, business_day_start DESC",
        pickers=(
            PickerSpec(
                label="Date From", kind="date_from",
                column="business_day_start",
            ),
            PickerSpec(
                label="Date To", kind="date_to",
                column="business_day_start",
            ),
            PickerSpec(
                label="Account", kind="dropdown", column="account_id",
                format=lambda a: f"{a['account_name']} ({a['account_id']})",
            ),
            PickerSpec(
                label="Account Role", kind="dropdown", column="account_role",
            ),
        ),
    ),
    SheetAnchorSpec(
        sheet_name="Overdraft",
        target_visual="Overdraft Violations",
        anchor_table="{p}_overdraft",
        anchor_columns=(
            "account_id", "account_name", "account_role", "business_day_start",
        ),
        # Bias toward low cust-N — see Drift spec for context.
        anchor_order="account_id ASC, business_day_start DESC",
        pickers=(
            PickerSpec(
                label="Date From", kind="date_from",
                column="business_day_start",
            ),
            PickerSpec(
                label="Date To", kind="date_to",
                column="business_day_start",
            ),
            PickerSpec(
                label="Account", kind="dropdown", column="account_id",
                format=lambda a: f"{a['account_name']} ({a['account_id']})",
            ),
            PickerSpec(
                label="Account Role", kind="dropdown", column="account_role",
            ),
        ),
    ),
    SheetAnchorSpec(
        sheet_name="Limit Breach",
        target_visual="Limit Breach Detail",
        anchor_table="{p}_limit_breach",
        anchor_columns=(
            "account_id", "account_name", "rail_name", "business_day",
        ),
        # Bias toward low cust-N — see Drift spec for context.
        anchor_order="account_id ASC, business_day DESC",
        pickers=(
            PickerSpec(
                label="Date From", kind="date_from",
                column="business_day",
            ),
            PickerSpec(
                label="Date To", kind="date_to",
                column="business_day",
            ),
            PickerSpec(
                label="Account", kind="dropdown", column="account_id",
                format=lambda a: f"{a['account_name']} ({a['account_id']})",
            ),
            # Limit Breach's "Transfer Type" picker narrows by
            # ``rail_name`` post-Z.B (transfer_type was subsumed into
            # rail). Picker label kept as "Transfer Type" for analyst
            # continuity; the WHERE clause is rail_name. See
            # ``apps/l1_dashboard/datasets.py::build_limit_breach_dataset``.
            PickerSpec(
                label="Transfer Type", kind="dropdown", column="rail_name",
            ),
        ),
    ),
)


@pytest.mark.parametrize(
    "spec", L1_PICKER_SPECS, ids=lambda s: s.sheet_name,
)
def test_l1_additive_pickers_keep_anchor_row(
    l1_dashboard_driver, cfg, spec: SheetAnchorSpec,
):
    """For each L1 sheet with ≥2 pickers: fetch a known-good anchor
    row, drive every picker to that row's values, assert the target
    table still has ≥1 row.

    Failure shapes:

    - **Anchor matview empty** → fixture raises ``RuntimeError`` from
      ``fetch_anchor_row``; the matview legitimately has zero rows for
      the sheet's violation kind. Either the seed plants nothing here
      (check ``auto_scenario.py`` + ``TestScenarioCoverage``) or the
      refresh didn't run.

    - **Pre-pick visual empty** → the target table renders zero rows
      before any pick. The matview row exists in the DB but isn't
      reaching the visual — dataset SQL bug, parameter default issue,
      or the universal date filter's default excludes the anchor's day.

    - **Post-pick visual empty** → target had rows pre-pick, anchor
      exists in the matview, but the combined-pick narrowing zeroes
      the table. The smoking gun for AA.A.6's class of regression:
      one of the picker WHERE clauses is wrong (wrong column, wrong
      operator, wrong format expectation — e.g. AA.E.2's
      ``account_id`` vs ``account_display`` miss).
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=spec.sheet_name)
    driver.wait_loaded(spec.target_visual)

    # Snapshot pre-pick so the assertion message can report whether
    # the visual is empty before or after the narrow.
    before = driver.table_rows(spec.target_visual)
    assert len(before) > 0, (
        f"{spec.sheet_name!r}: target visual {spec.target_visual!r} "
        f"empty BEFORE any pick. The matview ({spec.anchor_table}) "
        f"likely has zero rows for the default date window, or the "
        f"dataset SQL filters them out at load. Check the seed + "
        f"matview refresh state."
    )

    anchor = fetch_anchor_row(cfg, spec)
    apply_anchor_to_pickers(driver, spec, anchor)
    driver.wait_loaded(spec.target_visual)
    after = driver.table_rows(spec.target_visual)
    driver.screenshot()

    assert 0 < len(after) <= len(before), (
        f"{spec.sheet_name!r}: anchor row {dict(anchor)!r} should "
        f"survive the all-pickers-narrowed-to-anchor state. Got "
        f"{len(after)} rows (was {len(before)} pre-pick). One of the "
        f"picker WHERE clauses is wrong column / wrong operator / "
        f"wrong value format — drill into the failure capture's "
        f"network.txt to see which dataset SQL came back empty."
    )


# AA.A.7 — Inverse exclusion: each picker, when toggled to a non-matching
# value, should narrow the result below the anchor-narrowed count (the
# anchor row is excluded). Restoring to the matching value should bring
# the count back. Together AA.A.6 + AA.A.7 pin filter semantics in both
# directions: matching values keep matching rows; non-matching values
# exclude them.
#
# v1 scope: dropdown pickers only. Slider / datetime / date_range
# inversion follows the same shape but needs per-kind "generate a
# non-matching value" logic (slider: anchor ± epsilon; datetime: shift
# day; date_range: shift both bounds together). Defer until the dropdown
# shape proves out across the bundled sheets.
@pytest.mark.parametrize(
    "spec", L1_PICKER_SPECS, ids=lambda s: s.sheet_name,
)
def test_l1_dropdown_pickers_inverse_excludes_anchor(
    l1_dashboard_driver, cfg, spec: SheetAnchorSpec,
):
    """For each sheet with ≥2 pickers: after the AA.A.6 all-pickers-
    anchored state, iterate over the *dropdown* pickers. For each:

    1. Toggle to a non-matching value (any other advertised option).
    2. Assert row count strictly decreases (the anchor row is among
       those excluded; usually the count drops to 0 since the anchor
       was typically the only row that matched all N constraints).
    3. Restore the matching value.
    4. Assert row count returns to the anchor-narrowed count.

    Catches three regression classes AA.A.6 alone can't see:

    - **Picker wired to nothing** — toggle has no effect on the count
      (the WHERE clause references the wrong column or the param
      isn't bound at all).
    - **Picker WHERE too loose** — e.g. ``LIKE '%x%'`` when it should
      be ``=``; non-matching value still matches.
    - **Picker binding inverted** — toggling EXCLUDES the matching row
      instead of EXCLUDING the non-matching one; restore-after-toggle
      fails to bring the count back.

    Same ``[qs, app2]`` parametrization as AA.A.6 — parity gap = real
    wiring divergence.

    v1: dropdowns only. A picker whose advertised options have ≤1
    distinct value can't be inverted (no other option to pick) — those
    pickers are skipped with a warning so the test stays green on
    seed-dependent sparse dropdowns. Sliders / dates land as v2 once
    their inversion semantics settle.
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=spec.sheet_name)
    driver.wait_loaded(spec.target_visual)

    anchor = fetch_anchor_row(cfg, spec)
    apply_anchor_to_pickers(driver, spec, anchor)
    driver.wait_loaded(spec.target_visual)
    anchor_count = len(driver.table_rows(spec.target_visual))
    assert anchor_count > 0, (
        f"{spec.sheet_name!r}: AA.A.6 precondition failed — anchor "
        f"narrowing produced 0 rows. Inverse test can't run; fix "
        f"AA.A.6 first."
    )

    dropdown_pickers = [p for p in spec.pickers if p.kind == "dropdown"]
    assert dropdown_pickers, (
        f"{spec.sheet_name!r}: spec has no dropdown pickers — nothing "
        f"for the inverse-exclusion v1 test to exercise. Either add "
        f"dropdown(s) to the spec or remove from L1_PICKER_SPECS."
    )

    for picker in dropdown_pickers:
        matching = picker_value(picker, anchor)
        try:
            non_matching = non_matching_dropdown_value(
                driver, picker.label, matching,
            )
        except RuntimeError:
            # Seed-dependent sparse dropdown — only one option. Skip
            # this picker (not the whole test); the other pickers
            # still exercise the inverse contract.
            continue

        # Toggle to non-matching.
        driver.pick_filter(picker.label, [non_matching])
        driver.wait_loaded(spec.target_visual)
        post_invert = len(driver.table_rows(spec.target_visual))
        assert post_invert < anchor_count, (
            f"{spec.sheet_name!r} picker {picker.label!r}: toggling "
            f"to non-matching value {non_matching!r} should reduce "
            f"row count below the anchor-narrowed count "
            f"({anchor_count}). Got {post_invert}. Picker is wired "
            f"to nothing, WHERE clause is too loose (LIKE/IN with "
            f"wrong scope), or binding is inverted."
        )

        # Restore to matching — anchor row count must come back.
        driver.pick_filter(picker.label, [matching])
        driver.wait_loaded(spec.target_visual)
        post_restore = len(driver.table_rows(spec.target_visual))
        assert post_restore == anchor_count, (
            f"{spec.sheet_name!r} picker {picker.label!r}: restoring "
            f"matching value {matching!r} should return row count to "
            f"the anchor-narrowed count ({anchor_count}). Got "
            f"{post_restore}. Picker binding may be inverted or the "
            f"restore path didn't actually re-fetch."
        )
