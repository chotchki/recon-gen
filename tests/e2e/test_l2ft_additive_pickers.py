"""AA.A.9.b — L2FT extension of the AA.A.6/7 picker-survival tests.

Same shape as ``test_l1_additive_pickers.py`` — for each L2FT sheet
with ≥2 pickers (Rails / Chains / Transfer Templates), fetch a known-
good anchor row from the dataset's CustomSql, drive every picker to
that row's values *additively*, assert the target table still renders
≥1 row; then exercise each dropdown's inverse-exclusion contract.

**Unblocked by AA.A.9.a.** L1's original docstring warned L2FT
dropdowns were fragile to anchor against because their values are
CTE-derived (``parent_chain_name`` joins 3 CTEs) or CASE-projected
(``status`` / ``bundle_status`` are CASE-aliases on the postings
query). AA.A.9.a's refactor obviates that fragility — the spec carries
a ``dataset_builder`` reference, and ``fetch_anchor_row`` extracts the
exact CustomSql the visual loads and queries THAT. The CTE / CASE
derivations are applied by the SQL engine before the projection lands;
the anchor and the visual both see the projected column.

L2FT coverage:

- **Rails** (target: Transactions table, dataset: ``build_postings_dataset``)
  — Date From / Date To narrow ``posting``; Rail / Status / Bundle
  dropdowns narrow ``rail_name`` / ``status`` / ``bundle_status``.
  Metadata Key / Value are text-field inputs (not dropdowns) so they
  fall outside the v1 dropdown-only inverse-exclusion contract; the
  AA.A.6 narrowing test doesn't exercise them either since they default
  to a sentinel that matches all rows.
- **Chains** (target: Chain Instances table, dataset:
  ``build_chain_instances_dataset``) — Date From / Date To narrow
  ``parent_posting``; Chain / Completion dropdowns narrow
  ``parent_chain_name`` / ``completion_status``. Same metadata
  text-field exclusion.
- **Transfer Templates** (target: Template Instances table, dataset:
  ``build_tt_instances_dataset``) — Date From / Date To narrow
  ``posting``; Template / Completion dropdowns narrow ``template_name``
  / ``completion_status``. Same metadata text-field exclusion. The
  sheet also has a Sankey above the table, but the table is the
  row-count target the test asserts on (sankey row counts don't
  translate; AA.A.6's contract is table-row-count-based).

L2 Exceptions sheet is out of scope: its filter set is single-column
(only the unified-exceptions Kind dropdown), so the AA.A.6
"additive narrowing across distinct columns" contract degenerates.

Parametrized over ``[qs, app2]`` via ``l2ft_dashboard_driver`` so a
parity gap = real wiring divergence.
"""

from __future__ import annotations

import pytest

from recon_gen.apps.l2_flow_tracing.datasets import (
    build_chain_instances_dataset,
    build_postings_dataset,
    build_tt_instances_dataset,
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


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def _anchor_or_skip(cfg, l2, spec: SheetAnchorSpec):  # type: ignore[no-untyped-def]: cfg/l2 typed at the helper call site
    """Fetch ``spec``'s anchor row, or ``pytest.skip`` if the dataset is
    empty.

    L1 anchored on matviews seeded by every ``auto_scenario`` invocation
    so the L1 test docstring documents "anchor matview empty → real
    bug." L2FT is different: the chain_instances dataset depends on the
    L2 instance declaring a chain AND the seed actually firing the
    chain's parent template. ``spec_example`` declares a chain
    (``ExternalReconciliationCycle``) but the auto_scenario plants the
    OTHER template (``MerchantSettlementCycle``) — so chain_instances is
    legitimately empty on the default L2. That's a seed/scenario design
    choice, NOT a Chains-sheet bug; skipping here keeps the test honest
    across L2 instances rather than encoding "expected empty" into the
    spec catalog and re-introducing the test/visual drift AA.A.9.a
    closed.

    When the dataset has rows the test runs normally. When it doesn't,
    pytest records a clear skip reason rather than a confusing pre-pick
    `before > 0` assertion failure.
    """
    try:
        return fetch_anchor_row(cfg, l2, spec)
    except RuntimeError as exc:
        pytest.skip(f"{spec.sheet_name!r}: {exc}")


# AA.A.9.b — L2FT per-sheet picker→column maps. Anchor SQL is the
# same SQL the visual loads (via AA.A.9.a's ``dataset_builder``
# extraction), so CTE / CASE derivations are honored automatically.
#
# Picker ``column`` values must be keys in the dataset's projection
# (the columns the dataset's CustomSql SELECTs). See each builder's
# docstring for the projection list.
#
# ``anchor_order`` biases the pick toward a stable row — for L2FT
# the dropdown options come from the SEED's L2 declaration (rail
# names / chain parents / template names), not from MUI Autocomplete
# windows over a 25+ entity list, so the L1-style "low cust-N bias"
# isn't needed. ``posting`` / ``parent_posting`` DESC picks a recent
# row that's likely on the first page of the table.
L2FT_PICKER_SPECS: tuple[SheetAnchorSpec, ...] = (
    SheetAnchorSpec(
        sheet_name="Rails",
        target_visual="Transactions",
        dataset_builder=build_postings_dataset,
        anchor_order="posting DESC, id ASC",
        pickers=(
            PickerSpec(
                label="Date From", kind="date_from", column="posting",
            ),
            PickerSpec(
                label="Date To", kind="date_to", column="posting",
            ),
            PickerSpec(
                label="Rail", kind="dropdown", column="rail_name",
            ),
            PickerSpec(
                label="Status", kind="dropdown", column="status",
            ),
            PickerSpec(
                label="Bundle", kind="dropdown", column="bundle_status",
            ),
        ),
    ),
    SheetAnchorSpec(
        sheet_name="Chains",
        target_visual="Chain Instances",
        dataset_builder=build_chain_instances_dataset,
        anchor_order="parent_posting DESC, parent_transfer_id ASC",
        pickers=(
            PickerSpec(
                label="Date From", kind="date_from", column="parent_posting",
            ),
            PickerSpec(
                label="Date To", kind="date_to", column="parent_posting",
            ),
            PickerSpec(
                label="Chain", kind="dropdown", column="parent_chain_name",
            ),
            PickerSpec(
                label="Completion", kind="dropdown",
                column="completion_status",
            ),
        ),
    ),
    SheetAnchorSpec(
        sheet_name="Transfer Templates",
        target_visual="Template Instances",
        dataset_builder=build_tt_instances_dataset,
        anchor_order="posting DESC, transfer_id ASC",
        pickers=(
            PickerSpec(
                label="Date From", kind="date_from", column="posting",
            ),
            PickerSpec(
                label="Date To", kind="date_to", column="posting",
            ),
            PickerSpec(
                label="Template", kind="dropdown", column="template_name",
            ),
            PickerSpec(
                label="Completion", kind="dropdown",
                column="completion_status",
            ),
        ),
    ),
)


@pytest.mark.parametrize(
    "spec", L2FT_PICKER_SPECS, ids=lambda s: s.sheet_name,
)
def test_l2ft_additive_pickers_keep_anchor_row(
    l2ft_dashboard_driver, cfg, l2, spec: SheetAnchorSpec,
):
    """For each L2FT sheet with ≥2 pickers: fetch a known-good anchor
    row, drive every picker to that row's values, assert the target
    table still has ≥1 row. Mirror of AA.A.6's L1 contract; see
    ``test_l1_additive_pickers_keep_anchor_row`` docstring for the
    failure-shape catalog."""
    # Check dataset upfront so a seed-empty sheet skips before the
    # browser open + the pre-pick `before > 0` assertion can mis-diagnose.
    anchor = _anchor_or_skip(cfg, l2, spec)

    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet=spec.sheet_name)
    driver.wait_loaded(spec.target_visual)

    # AA.A.l2ft-rails-inverse.2 — use table_row_count, not table_rows.
    # table_rows() returns the DOM-visible window (capped at ~50 by QS's
    # default page size). When both pre-pick and post-pick exceed the
    # cap, comparing two saturated 50s silently passes broken narrows.
    # See L1 additive test + AA.A.l2ft-rails-inverse PLAN entry.
    before_count = driver.table_row_count(spec.target_visual)
    assert before_count > 0, (
        f"{spec.sheet_name!r}: target visual {spec.target_visual!r} "
        f"empty BEFORE any pick. The dataset's default-param SQL "
        f"returns rows for the anchor (the dataset SQL was checked "
        f"before the browser opened), but the visual isn't surfacing "
        f"them — matview refresh skipped? dataset → visual binding "
        f"stale?"
    )

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


@pytest.mark.parametrize(
    "spec", L2FT_PICKER_SPECS, ids=lambda s: s.sheet_name,
)
def test_l2ft_dropdown_pickers_inverse_excludes_anchor(
    l2ft_dashboard_driver, cfg, l2, spec: SheetAnchorSpec,
):
    """For each L2FT sheet with ≥2 pickers: after the AA.A.6 all-
    pickers-anchored state, iterate over the dropdown pickers and
    verify each excludes the anchor when toggled to a non-matching
    value, then restores when set back. Mirror of AA.A.7's L1
    contract; see ``test_l1_dropdown_pickers_inverse_excludes_anchor``
    docstring for the regression-class catalog."""
    # See ``_anchor_or_skip`` rationale — skip seed-empty sheets cleanly.
    anchor = _anchor_or_skip(cfg, l2, spec)

    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet=spec.sheet_name)
    driver.wait_loaded(spec.target_visual)

    apply_anchor_to_pickers(driver, spec, anchor)
    driver.wait_loaded(spec.target_visual)
    # AA.A.l2ft-rails-inverse.2 — table_row_count, not len(table_rows()).
    # The original code measured the DOM-visible window (50 cap), which
    # made `post_invert < anchor_count` always false when both states
    # exceeded the cap — the L2FT-Rails-inverse bug that surfaced in
    # v11.0.1's verification chain. See L1 inverse test for the same fix.
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
        f"dropdown(s) to the spec or remove from L2FT_PICKER_SPECS."
    )

    for picker in dropdown_pickers:
        matching = picker_value(picker, anchor)
        try:
            non_matching = non_matching_dropdown_value(
                driver, picker.label, matching,
            )
        except RuntimeError:
            # Seed-dependent sparse dropdown — only one option advertised.
            # Skip this picker (not the whole test); the other pickers
            # still exercise the inverse contract.
            continue

        # AA.A.l2ft-rails-inverse.2.d — row-identity check, not row-count.
        # Old `post_invert < anchor_count` baked in the wrong premise
        # that non-matching values yield fewer rows; refuted on L2FT
        # Rails where Inbound (174 rows) > Outbound anchor (88 rows) on
        # the anchor's day, so a count-narrow assertion can never pass.
        # The real contract: after the toggle, NO row in the result
        # should still match the anchor's value for the toggled column.
        # See L1 inverse test for the same fix shape.
        driver.pick_filter(picker.label, [non_matching])
        driver.wait_loaded(spec.target_visual)
        post_invert_rows = driver.read_all_table_rows(spec.target_visual)
        visual_col = visual_column_label(spec, cfg, l2, picker.column)
        offenders = [
            r for r in post_invert_rows if r.get(visual_col) == matching
        ]
        assert not offenders, (
            f"{spec.sheet_name!r} picker {picker.label!r}: after "
            f"toggling to non-matching value {non_matching!r}, the "
            f"result should contain NO rows with {visual_col!r}="
            f"{matching!r} (the anchor's value). Found "
            f"{len(offenders)} offending row(s) out of "
            f"{len(post_invert_rows)} total; first 3: "
            f"{offenders[:3]!r}. Picker is wired to nothing, WHERE "
            f"clause is too loose (LIKE/IN with wrong scope), or "
            f"binding is inverted."
        )

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
