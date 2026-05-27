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

L1 coverage: Drift, Overdraft, Limit Breach, Pending Aging,
Unbundled Aging, Today's Exceptions, Transactions — every L1 sheet
with ≥2 pickers AND a Table target. Daily Statement is covered by
the pre-existing ``test_daily_statement_*`` tests via the bespoke
``find_account_day_with_data`` helper; not re-wired here. Drift
Timelines (LineChart only, no Table) and Supersession Audit (single
dropdown) don't qualify for ≥2 pickers + Table target.

Cross-app scope (why AA.A.6 v1 is L1-only):

- **L2FT** — every sheet with ≥2 pickers (Rails / Chains / Templates)
  has its dropdown values derived from CTEs or projected via SQL CASE
  (``parent_chain_name`` lives in a 3-CTE join; ``status`` /
  ``bundle_status`` are CASE-aliases on the postings query). An
  anchor query that replicates those derivations is fragile and
  duplicates the dataset SQL. Each L2FT sheet already has bespoke
  dropdown coverage (``test_l2ft_*_dropdowns.py``, X.1.g.8–10); the
  marginal AA.A.6 value here is small.
- **Investigation** — Money Trail / Account Network defaults to a
  sentinel chain-root / anchor on first paint (``_MONEY_TRAIL_ROOT_
  SENTINEL`` / empty anchor default) so the visual renders **empty
  pre-pick**, which breaks AA.A.6's ``before > 0`` precondition.
  Recipient Fanout / Volume Anomalies have <2 pickers.
- **Executives** — every sheet has only Date From + Date To (the
  universal date range). Both pickers narrow the same column with
  different bounds; the "additive narrowing across distinct columns"
  contract degenerates here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from recon_gen.apps.l1_dashboard.datasets import (
    DRIFT_CONTRACT,
    LIMIT_BREACH_CONTRACT,
    OVERDRAFT_CONTRACT,
    STUCK_PENDING_CONTRACT,
    STUCK_UNBUNDLED_CONTRACT,
    TODAYS_EXCEPTIONS_CONTRACT,
    TRANSACTIONS_CONTRACT,
    build_drift_dataset,
    build_limit_breach_dataset,
    build_overdraft_dataset,
    build_stuck_pending_dataset,
    build_stuck_unbundled_dataset,
    build_todays_exceptions_dataset,
    build_transactions_dataset,
)
from tests.e2e._picker_anchor import (
    PickerSpec,
    SheetAnchorSpec,
    apply_anchor_to_pickers,
    fetch_anchor_row,
    non_matching_dropdown_value,
    picker_value,
    visual_column_label,
)
from recon_gen.common.config import Config



if TYPE_CHECKING:
    from recon_gen.common.l2 import L2Instance
    from tests.e2e._drivers import DashboardDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


# Per-sheet picker→column maps. To extend: add a new SheetAnchorSpec
# entry — the test body below picks it up automatically.
#
# AA.A.9 (2026-05-17) — spec carries a ``dataset_builder`` reference
# (the production ``build_*_dataset`` fn deploy uses), NOT a hand-
# listed matview name + column tuple. ``fetch_anchor_row`` extracts
# the CustomSql from the builder's DataSet output, applies declared
# param defaults via ``apply_dataset_param_defaults`` (the same
# production substitutor App2 uses on initial paint), and wraps to
# anchor on one row. Net effect: anchor SQL is **provably the same
# SQL the visual loads** — no risk of test/visual drift.
#
# Picker ``column`` values must be keys in the dataset's projection
# (i.e. columns the dataset SELECTs). AA.A.10 (stretch) plans to
# derive these from the tree walk too; until then they stay
# hand-mapped (1 line per picker).
#
# ``anchor_order`` biases the pick: typically a column-name
# ASC/DESC clause that picks a recent / lowest-cust-N / highest-
# magnitude row. For sheets where the dataset's default-param SQL
# is empty (zero violations on a fresh seed; or sentinel-empty
# Money-Trail-style), the row-survival assertion is meaningful only
# when the seed plants something — confirm via ``data apply`` first.
L1_PICKER_SPECS: tuple[SheetAnchorSpec, ...] = (
    SheetAnchorSpec(
        sheet_name="Drift",
        target_visual="Leaf Account Drift",
        dataset_builder=build_drift_dataset,
        contract=DRIFT_CONTRACT,
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
        dataset_builder=build_overdraft_dataset,
        contract=OVERDRAFT_CONTRACT,
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
        dataset_builder=build_limit_breach_dataset,
        contract=LIMIT_BREACH_CONTRACT,
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
    # Pending / Unbundled Aging — current-state matviews (no date scope
    # — see ``_wire_date_range_filter``'s skip note: stuck items don't
    # narrow by the analyst's window). Both have an Account dropdown +
    # a Transfer Type dropdown + a Rail dropdown. Transfer Type AND
    # Rail both narrow ``rail_name`` (different value-source sets — see
    # ``l1_rail_universe_values`` vs ``l1_rail_values``) — the anchor
    # row's single rail_name value satisfies both pickers.
    SheetAnchorSpec(
        sheet_name="Pending Aging",
        target_visual="Stuck Pending Detail",
        dataset_builder=build_stuck_pending_dataset,
        contract=STUCK_PENDING_CONTRACT,
        # No date filter to bias against — order by account_id ASC so
        # we land on the lowest-numbered customer (MUI Autocomplete
        # first-visible-window — see Drift spec).
        anchor_order="account_id ASC, posting DESC",
        pickers=(
            PickerSpec(
                label="Account", kind="dropdown", column="account_id",
                format=lambda a: f"{a['account_name']} ({a['account_id']})",
            ),
            PickerSpec(
                label="Transfer Type", kind="dropdown", column="rail_name",
            ),
            PickerSpec(
                label="Rail", kind="dropdown", column="rail_name",
            ),
        ),
    ),
    SheetAnchorSpec(
        sheet_name="Unbundled Aging",
        target_visual="Stuck Unbundled Detail",
        dataset_builder=build_stuck_unbundled_dataset,
        contract=STUCK_UNBUNDLED_CONTRACT,
        anchor_order="account_id ASC, posting DESC",
        pickers=(
            PickerSpec(
                label="Account", kind="dropdown", column="account_id",
                format=lambda a: f"{a['account_name']} ({a['account_id']})",
            ),
            PickerSpec(
                label="Transfer Type", kind="dropdown", column="rail_name",
            ),
            PickerSpec(
                label="Rail", kind="dropdown", column="rail_name",
            ),
        ),
    ),
    # Today's Exceptions — UNION ALL across 5 per-day L1 invariant
    # views PLUS two currently-open branches (stuck_pending,
    # stuck_unbundled). The stuck-* branches surface transactions on
    # control accounts (``clearing-suspense``, ``customer-ledger``) that
    # have no daily-balance rows, so those rows appear in the
    # ``Exception Detail`` table but those account_ids are absent from
    # the Account dropdown universe (which sources from
    # ``current_daily_balances`` via ``build_l1_accounts_dataset``).
    # Without the anchor constraint, the ``account_id ASC, magnitude
    # DESC`` order lands on an alphabetically-first control account
    # (e.g. ``clearing-suspense``) and the Account picker can't find
    # it (QS: option absent; App2: TomSelect setValue no-ops, no
    # /visuals/.../data refetch fires → 15s Playwright timeout).
    #
    # AA.A.qs-triage.5 — same shape as the Transactions fix (AA.A.993):
    # constrain the anchor account to the dropdown's advertised universe.
    SheetAnchorSpec(
        sheet_name="Today's Exceptions",
        target_visual="Exception Detail",
        dataset_builder=build_todays_exceptions_dataset,
        contract=TODAYS_EXCEPTIONS_CONTRACT,
        # Sorted-by-magnitude_amount is the visual default — pick the top
        # row of the smallest cust-N for the MUI window bias (see Drift).
        anchor_order="account_id ASC, magnitude_amount DESC",
        anchor_where_template=(
            "account_id IN ("
            "SELECT account_id FROM {prefix}_current_daily_balances"
            ")"
        ),
        pickers=(
            PickerSpec(
                label="Date From", kind="date_from", column="business_day",
            ),
            PickerSpec(
                label="Date To", kind="date_to", column="business_day",
            ),
            PickerSpec(
                label="Check Type", kind="dropdown", column="check_type",
            ),
            PickerSpec(
                label="Account", kind="dropdown", column="account_id",
                format=lambda a: f"{a['account_name']} ({a['account_id']})",
            ),
            PickerSpec(
                label="Transfer Type", kind="dropdown", column="rail_name",
            ),
        ),
    ),
    # Transactions — per-leg ledger via ``<p>_current_transactions``.
    # 5 dropdowns + date range — the densest picker landscape in L1.
    # ``posting`` is a timestamp; ``posting DESC`` lands on the most
    # recent leg in the default-param SQL output.
    #
    # AA.A.993 — constrain the anchor account to those advertised by the
    # Account dropdown (which sources from current_daily_balances via
    # ``build_l1_accounts_dataset``). The transactions matview includes
    # internal control accounts (``clearing-suspense``,
    # ``customer-ledger``) that have transactions but no daily-balance
    # rows; with no constraint the anchor picks one of those and the
    # Account picker can't find it (QS: option absent from listbox;
    # App2: TomSelect setValue no-ops, no HTMX refetch fires → 15 s
    # timeout). Same fix pattern as the AA.B.5 daily-statement helper's
    # "narrow to dropdown's initial advertised universe".
    #
    # AA.A.993 — the Transfer dropdown is omitted from the picker spec
    # for now. Its universe is ``SELECT DISTINCT transfer_id FROM
    # <p>_current_transactions`` — 8k+ rows on a default-density
    # ``spec_example`` deploy. App2 caps dataset-sourced dropdown
    # options at ``_OPTIONS_CAP = 2000`` (see
    # ``common/html/_tree_fetcher.py:367``) — a placeholder until
    # typeahead / server-side search lands ("typeahead / server-side
    # search for very large universes is a follow-on" — same module's
    # comment). The anchor's ``transfer_id`` lands past the cap on most
    # seeds (deterministic transfer_id sort orders the cap to the
    # alphabetically-first bundle/inbound prefixes). Rather than game
    # the anchor to land inside the cap — which would mask the same
    # UX problem the cap signals — the spec drops the Transfer picker
    # until the underlying typeahead lands. Re-include with the rest
    # of the pickers once App2 grows typeahead support.
    SheetAnchorSpec(
        sheet_name="Transactions",
        target_visual="Posting Ledger",
        dataset_builder=build_transactions_dataset,
        contract=TRANSACTIONS_CONTRACT,
        anchor_order="account_id ASC, posting DESC",
        anchor_where_template=(
            "account_id IN ("
            "SELECT account_id FROM {prefix}_current_daily_balances"
            ")"
        ),
        pickers=(
            PickerSpec(
                label="Date From", kind="date_from", column="posting",
            ),
            PickerSpec(
                label="Date To", kind="date_to", column="posting",
            ),
            PickerSpec(
                label="Account", kind="dropdown", column="account_id",
                format=lambda a: f"{a['account_name']} ({a['account_id']})",
            ),
            PickerSpec(
                label="Status", kind="dropdown", column="status",
            ),
            PickerSpec(
                label="Origin", kind="dropdown", column="origin",
            ),
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
    l1_dashboard_driver: tuple["DashboardDriver", str], cfg: Config, l2: "L2Instance", spec: SheetAnchorSpec,
) -> None:
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
    # AA.A.l2ft-rails-inverse.2 — use table_row_count, not table_rows.
    # table_rows() returns the DOM-visible window (capped at ~50 by QS's
    # default page size), so when both pre-pick and post-pick exceed the
    # cap, the assertion compares two saturated 50s and silently passes
    # even on broken narrows. table_row_count() bumps page size + waits
    # for WS settle for the true filtered count.
    before_count = driver.table_row_count(spec.target_visual)
    assert before_count > 0, (
        f"{spec.sheet_name!r}: target visual {spec.target_visual!r} "
        f"empty BEFORE any pick. The dataset's default-param SQL "
        f"returns rows for the anchor (otherwise ``fetch_anchor_row`` "
        f"would have raised), but the visual isn't surfacing them — "
        f"matview refresh skipped? dataset → visual binding stale?"
    )

    anchor = fetch_anchor_row(cfg, l2, spec)
    apply_anchor_to_pickers(driver, spec, anchor)
    driver.wait_loaded(spec.target_visual)
    after_count = driver.table_row_count(spec.target_visual)
    driver.screenshot()

    assert 0 < after_count <= before_count, (
        f"{spec.sheet_name!r}: anchor row {dict(anchor)!r} should "
        f"survive the all-pickers-narrowed-to-anchor state. Got "
        f"{after_count} rows (was {before_count} pre-pick). One of the "
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
    l1_dashboard_driver: tuple["DashboardDriver", str], cfg: Config, l2: "L2Instance", spec: SheetAnchorSpec,
) -> None:
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

    anchor = fetch_anchor_row(cfg, l2, spec)
    apply_anchor_to_pickers(driver, spec, anchor)
    driver.wait_loaded(spec.target_visual)
    # AA.A.l2ft-rails-inverse.2 — table_row_count is the true filtered
    # row total; table_rows() returns only the DOM-visible window (~50
    # cap), which makes a `post_invert < anchor_count` assertion always
    # false when both states exceed the cap (the L2FT-Rails-inverse bug).
    anchor_count = driver.table_row_count(spec.target_visual)
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
        # AA.A.l2ft-rails-inverse.2.e — row-identity check via find_row,
        # not row-count. After the toggle, no row should still match the
        # anchor's value for the toggled column; find_row early-exits on
        # the first offender (fast-fail when the filter is broken).
        driver.pick_filter(picker.label, [non_matching])
        driver.wait_loaded(spec.target_visual)
        visual_col = visual_column_label(spec, picker.column)
        offender = driver.find_row(
            spec.target_visual, {visual_col: matching},
        )
        assert offender is None, (
            f"{spec.sheet_name!r} picker {picker.label!r}: after "
            f"toggling to non-matching value {non_matching!r}, the "
            f"result should contain NO rows with {visual_col!r}="
            f"{matching!r} (the anchor's value). Found offending "
            f"row {offender!r}. Picker is wired to nothing, WHERE "
            f"clause is too loose (LIKE/IN with wrong scope), or "
            f"binding is inverted."
        )

        # Restore to matching — anchor row count must come back.
        driver.pick_filter(picker.label, [matching])
        driver.wait_loaded(spec.target_visual)
        post_restore = driver.table_row_count(spec.target_visual)
        assert post_restore == anchor_count, (
            f"{spec.sheet_name!r} picker {picker.label!r}: restoring "
            f"matching value {matching!r} should return row count to "
            f"the anchor-narrowed count ({anchor_count}). Got "
            f"{post_restore}. Picker binding may be inverted or the "
            f"restore path didn't actually re-fetch."
        )
