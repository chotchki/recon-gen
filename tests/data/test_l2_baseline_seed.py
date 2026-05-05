"""Tests for ``emit_baseline_seed`` (Phase R).

R.2.a — skeleton-level tests. Pin the public entry point's signature +
the deterministic helpers (RNG sub-stream layout, business-day calendar)
so R.2.b–e can fill in the body without accidentally regressing the API.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from quicksight_gen.common.l2.auto_scenario import default_scenario_for
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.seed import (
    _BASELINE_BASE_SEED,
    _business_days_in_window,
    _seed_for_rail,
    emit_baseline_seed,
    emit_full_seed,
)


_SPEC_EXAMPLE = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
_SASQUATCH_PR = Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"
_ANCHOR = date(2026, 4, 30)


class TestSeedForRail:
    """The per-Rail RNG sub-stream layout (R.1.f §4)."""

    def test_seed_for_rail_is_deterministic(self) -> None:
        assert _seed_for_rail("CustomerInboundACH") == _seed_for_rail(
            "CustomerInboundACH",
        )

    def test_seed_for_rail_isolates_rails(self) -> None:
        a = _seed_for_rail("CustomerInboundACH")
        b = _seed_for_rail("CustomerOutboundACH")
        assert a != b, (
            "Per-Rail RNG seeds must differ so renaming one Rail can't "
            "perturb another's emitted bytes."
        )

    def test_seed_for_rail_xors_against_base(self) -> None:
        # Empty-name edge case lands on BASE_SEED itself (crc32("") = 0).
        assert _seed_for_rail("") == _BASELINE_BASE_SEED


class TestBusinessDaysCalendar:
    """The 90-day business-day calendar (R.1.f §3)."""

    def test_window_excludes_weekends(self) -> None:
        days = _business_days_in_window(_ANCHOR, 90)
        assert all(d.weekday() < 5 for d in days), (
            "Business-day calendar must drop Sat/Sun."
        )

    def test_window_is_sorted_ascending(self) -> None:
        days = _business_days_in_window(_ANCHOR, 90)
        assert days == sorted(days)

    def test_window_anchor_is_inclusive(self) -> None:
        # 2026-04-30 is a Thursday — should be in the window.
        days = _business_days_in_window(_ANCHOR, 90)
        assert _ANCHOR in days

    def test_window_count_in_expected_range(self) -> None:
        # 90 days spans ~13 weeks → ~65 weekdays. Holidays-package may
        # shave 2-4 more if installed; either way, well above 50.
        days = _business_days_in_window(_ANCHOR, 90)
        assert 50 <= len(days) <= 66


class TestEmitBaselineSeedSkeleton:
    """R.2.a: the skeleton emits a valid header + empty INSERT bodies."""

    @pytest.mark.parametrize("yaml_path", [_SPEC_EXAMPLE, _SASQUATCH_PR])
    def test_emit_returns_string(self, yaml_path: Path) -> None:
        instance = load_instance(yaml_path)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_header_carries_instance_prefix(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        assert "L2 instance: spec_example" in sql

    def test_header_carries_anchor(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        assert _ANCHOR.isoformat() in sql

    def test_header_reports_rail_and_chain_counts(self) -> None:
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        assert f"Rails declared: {len(instance.rails)}" in sql
        assert f"Chains declared: {len(instance.chains)}" in sql

    def test_window_days_default_is_90(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        assert "90-day rolling window" in sql

    def test_window_days_override(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR, window_days=30)
        assert "30-day rolling window" in sql

    def test_no_remaining_stub_markers(self) -> None:
        # R.2.a-e all complete — no "in progress" markers should remain.
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        assert "in progress" not in sql, (
            "R.2.a-e all complete; no stub markers should remain in output"
        )

    def test_emit_is_deterministic_for_fixed_anchor(self) -> None:
        instance = load_instance(_SASQUATCH_PR)
        a = emit_baseline_seed(instance, anchor=_ANCHOR)
        b = emit_baseline_seed(instance, anchor=_ANCHOR)
        assert a == b


class TestBaselineLegLoop:
    """R.2.b — per-Rail leg loop coverage + volume + classification."""

    def test_spec_example_emits_thousands_of_legs(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        n = sql.count("INSERT INTO spec_example_transactions")
        # spec_example has 4 non-aggregating rails over 65 business days.
        # Per R.1.f §1 the heuristic targets 5k-10k legs total; we may
        # land lower because spec_example doesn't have the broad rail
        # set sasquatch_pr does, but should clear ~100.
        assert n >= 100, (
            f"R.2.b should emit ≥100 baseline legs for spec_example; got {n}"
        )

    def test_sasquatch_pr_lands_in_target_band(self) -> None:
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        n = sql.count("INSERT INTO sasquatch_pr_transactions")
        # R.1.f §1: total expected ~50k-80k legs over 90 days for
        # sasquatch_pr. R.2.b skips aggregating rails so a lower bound
        # of 30k is reasonable; aggregating rails will lift this to
        # the spec target in R.2.c.
        assert 30_000 <= n <= 100_000, (
            f"R.2.b should emit 30k-100k baseline legs for sasquatch_pr; "
            f"got {n}"
        )

    def test_aggregating_rails_emit_baseline_legs(self) -> None:
        # R.2.c implements aggregating rails. Every aggregating rail with
        # eligible accounts should emit at least one EOD/EOM parent leg
        # over the window.
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        missing: list[str] = []
        for rail in instance.rails:
            if not rail.aggregating:
                continue
            if f"'{rail.name}'" not in sql:
                missing.append(str(rail.name))
        assert not missing, (
            f"Aggregating rails with no baseline legs (R.2.c should emit "
            f"per-period parent legs): {missing}"
        )

    def test_non_aggregating_rails_all_emit_legs(self) -> None:
        # Every non-aggregating rail with eligible accounts should emit
        # at least some firings.
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        missing: list[str] = []
        for rail in instance.rails:
            if rail.aggregating:
                continue
            if f"'{rail.name}'" not in sql:
                missing.append(str(rail.name))
        assert not missing, (
            f"Non-aggregating rails with no baseline legs (R.2.b should "
            f"cover every rail with eligible accounts): {missing}"
        )

    def test_monthly_eom_rails_fire_only_at_month_end(self) -> None:
        # R.2.c — monthly_eom rails fire only on the last business day
        # of each month. Over a 90-day window that's ~3 firings, much
        # fewer than a daily_eod rail's ~65.
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        monthly_count = sql.count("'CustomerFeeMonthlySettlement'")
        daily_count = sql.count("'ACHOriginationDailySweep'")
        assert monthly_count <= 5, (
            f"monthly_eom rail should fire ~3 times in 90d; got {monthly_count}"
        )
        assert daily_count >= 30, (
            f"daily_eod rail should fire ~65 times in 90d; got {daily_count}"
        )

    def test_bundle_id_stamped_on_bundled_children(self) -> None:
        # R.2.c — children of bundled activities get bundle_id stamped
        # at emit time. The bundle_id format is
        # ``tr-base-bundle-<agg_rail_slug>-<seq:04d>``.
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        # ACHOriginationDailySweep bundles CustomerOutboundACH; both
        # the parent legs (transfer_id) and child legs (bundle_id) carry
        # ``bundle-achoriginationdailysweep`` in their identifier.
        assert "bundle-achoriginationdailysweep" in sql, (
            "Aggregating rail bundle ids should appear in emitted SQL "
            "(both as parent transfer_id and child bundle_id)."
        )

    def test_chain_firings_emit_with_transfer_parent_id(self) -> None:
        # R.2.d — every Chain entry whose parent is a Rail (not just a
        # TransferTemplate) and which fires at the configured completion
        # rate should emit child legs with transfer_parent_id set.
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        # Chain children carry the "tr-base-chain-" prefix in their
        # transfer_id; the count should be substantial across all
        # Rail-parented chain entries.
        assert sql.count("tr-base-chain-") >= 100, (
            "R.2.d should emit chain child firings; found "
            f"{sql.count('tr-base-chain-')}"
        )

    def test_required_chains_higher_completion_than_optional(self) -> None:
        # R.2.d — Required chains complete ~95% of parent firings;
        # Optional ~50%. ACHOriginationDailySweep -> ConcentrationToFRBSweep
        # is Required; CustomerInboundACH -> CustomerInboundACHReturn*
        # are Optional. Required chain should hit close to its parent's
        # firing count (~63 over 90d). Optional chains hit fewer because
        # they share an xor_group and complete probabilistically.
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)

        required_chain_children = sql.count(
            "tr-base-chain-concentrationtofrbsweep",
        )
        # Parent fires ~65 times in 90d (daily_eod aggregating); 95%
        # completion → ~62 children expected; ≥40 is comfortable.
        assert required_chain_children >= 40, (
            f"Required chain expected ≥40 child firings; got "
            f"{required_chain_children}"
        )

    def test_chain_child_amounts_lognormal_not_constant(self) -> None:
        # R.2.d chain-child amount sampler should produce varied amounts
        # via the child rail's lognormal — not a constant value.
        import re
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        # Pull the money column from chain child rows.
        chain_amounts = re.findall(
            r"tx-base-chain-[a-z-]+-\d+',[^,]+,[^,]+,[^,]+,[^,]+,[^,]+,\s*"
            r"(-?\d+\.\d+),",
            sql,
        )
        assert len(set(chain_amounts)) >= 5, (
            "Chain child amounts should be sampled via lognormal — "
            f"saw only {len(set(chain_amounts))} distinct values"
        )

    def test_daily_balances_emitted_per_account_per_active_day(self) -> None:
        # R.2.e — every (account_id, business_day) the leg loop touched
        # must produce exactly one daily_balances row. Sasquatch_pr's
        # 25 template-instance accounts active across 65 business days
        # yields ~1,500-3,000 daily balance rows after the picker's
        # uneven account selection.
        instance = load_instance(_SASQUATCH_PR)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        n = sql.count("INSERT INTO sasquatch_pr_daily_balances")
        assert 1_000 <= n <= 5_000, (
            f"R.2.e should emit 1k-5k daily_balances rows for sasquatch_pr; "
            f"got {n}"
        )

    def test_daily_balances_sorted_for_determinism(self) -> None:
        # R.2.e iteration is sorted by (account_id, day). Two runs at
        # the same anchor must produce byte-identical output.
        instance = load_instance(_SPEC_EXAMPLE)
        a = emit_baseline_seed(instance, anchor=_ANCHOR)
        b = emit_baseline_seed(instance, anchor=_ANCHOR)
        assert a == b, "R.2.e daily_balances must be deterministic"

    def test_balances_state_machine_updates(self) -> None:
        pass

class TestEmitFullSeed:
    """R.3.a — emit_full_seed concatenates baseline + plants."""

    def test_full_seed_includes_baseline_markers(self) -> None:
        instance = load_instance(_SASQUATCH_PR)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        sql = emit_full_seed(instance, scenario, anchor=_ANCHOR)
        # Baseline header + per-rail tx prefix.
        assert "Phase R healthy baseline seed" in sql
        assert "tr-base-" in sql

    def test_full_seed_includes_plant_markers(self) -> None:
        instance = load_instance(_SASQUATCH_PR)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        sql = emit_full_seed(instance, scenario, anchor=_ANCHOR)
        # Plant SQL header from emit_seed.
        assert "demo seed" in sql

    def test_full_seed_volume_baseline_plus_plants(self) -> None:
        # Full seed has more rows than baseline alone.
        instance = load_instance(_SASQUATCH_PR)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        baseline_only = emit_baseline_seed(instance, anchor=_ANCHOR)
        full = emit_full_seed(instance, scenario, anchor=_ANCHOR)
        baseline_n = baseline_only.count("INSERT INTO sasquatch_pr_transactions")
        full_n = full.count("INSERT INTO sasquatch_pr_transactions")
        assert full_n > baseline_n, (
            f"emit_full_seed should add plant rows on top of baseline: "
            f"baseline={baseline_n}, full={full_n}"
        )

    def test_full_seed_deterministic(self) -> None:
        instance = load_instance(_SASQUATCH_PR)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        a = emit_full_seed(instance, scenario, anchor=_ANCHOR)
        b = emit_full_seed(instance, scenario, anchor=_ANCHOR)
        assert a == b

    def test_densified_scenario_multiplies_plants(self) -> None:
        # R.3.b — densify_scenario multiplies per-kind plant counts by
        # the configured factor.
        from quicksight_gen.common.l2.auto_scenario import (
            densify_scenario,
        )
        instance = load_instance(_SASQUATCH_PR)
        base = default_scenario_for(instance, today=_ANCHOR).scenario
        dense = densify_scenario(base, factor=5)
        assert len(dense.drift_plants) == 5 * len(base.drift_plants)
        assert len(dense.overdraft_plants) == 5 * len(base.overdraft_plants)
        assert (
            len(dense.stuck_pending_plants)
            == 5 * len(base.stuck_pending_plants)
        )
        # Inv fanout + transfer-template plants do NOT replicate.
        assert dense.inv_fanout_plants == base.inv_fanout_plants
        assert dense.transfer_template_plants == base.transfer_template_plants

    def test_densify_factor_one_is_identity(self) -> None:
        from quicksight_gen.common.l2.auto_scenario import densify_scenario
        instance = load_instance(_SASQUATCH_PR)
        base = default_scenario_for(instance, today=_ANCHOR).scenario
        result = densify_scenario(base, factor=1)
        assert result is base

    def test_broken_rail_adds_stuck_pending_plants(self) -> None:
        # R.3.c — add_broken_rail_plants stacks N plants on one
        # specifically-picked rail.
        from quicksight_gen.common.l2.auto_scenario import (
            add_broken_rail_plants,
        )
        instance = load_instance(_SASQUATCH_PR)
        base = default_scenario_for(instance, today=_ANCHOR).scenario
        broken = add_broken_rail_plants(base, instance, broken_count=15)
        added = (
            len(broken.stuck_pending_plants)
            - len(base.stuck_pending_plants)
        )
        assert added == 15

    def test_boost_inv_fanout_multiplies_amount(self) -> None:
        # R.3.d — boost_inv_fanout_plants scales the per-transfer amount
        # so the fanout cluster stands out against the baseline customer
        # ACH median (~$665).
        from decimal import Decimal
        from quicksight_gen.common.l2.auto_scenario import (
            boost_inv_fanout_plants,
        )
        instance = load_instance(_SASQUATCH_PR)
        base = default_scenario_for(instance, today=_ANCHOR).scenario
        if not base.inv_fanout_plants:
            return  # No InvFanoutPlant in this instance — skip.
        boosted = boost_inv_fanout_plants(base, amount_multiplier=5)
        assert (
            boosted.inv_fanout_plants[0].amount_per_transfer
            == base.inv_fanout_plants[0].amount_per_transfer * 5
        )
        # Other plant kinds untouched.
        assert boosted.drift_plants == base.drift_plants
        assert boosted.stuck_pending_plants == base.stuck_pending_plants

    def test_boost_inv_fanout_multiplier_one_is_noop(self) -> None:
        from quicksight_gen.common.l2.auto_scenario import (
            boost_inv_fanout_plants,
        )
        instance = load_instance(_SASQUATCH_PR)
        base = default_scenario_for(instance, today=_ANCHOR).scenario
        result = boost_inv_fanout_plants(base, amount_multiplier=1)
        assert result is base

    def test_broken_rail_count_zero_is_noop(self) -> None:
        from quicksight_gen.common.l2.auto_scenario import (
            add_broken_rail_plants,
        )
        instance = load_instance(_SASQUATCH_PR)
        base = default_scenario_for(instance, today=_ANCHOR).scenario
        result = add_broken_rail_plants(base, instance, broken_count=0)
        assert result is base

    def test_full_seed_hash_lock_sasquatch_pr(self) -> None:
        # R.5.a — pin SHA256 of the full demo-apply pipeline output
        # (baseline + densify factor=5 + broken_count=15 +
        # boost_amount_multiplier=5) against a canonical 2030-01-01
        # anchor. Update the constant when changes are intentional.
        import hashlib
        from quicksight_gen.common.l2.auto_scenario import (
            add_broken_rail_plants,
            boost_inv_fanout_plants,
            densify_scenario,
        )
        canonical_anchor = date(2030, 1, 1)
        instance = load_instance(_SASQUATCH_PR)
        base = default_scenario_for(instance, today=canonical_anchor).scenario
        dense = densify_scenario(base, factor=5)
        broken = add_broken_rail_plants(dense, instance, broken_count=15)
        final = boost_inv_fanout_plants(broken, amount_multiplier=5)
        sql = emit_full_seed(instance, final, anchor=canonical_anchor)
        actual = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        expected = (
            # v8.6.21 — re-locked after X.1.i added FailedTransactionPlant
            # rows to the auto-scenario (open-set status enum work).
            "0b9e57b081ca9f21ea8196dc59cb833b7e8c56b60895756bab92ce31240541bd"
        )
        assert actual == expected, (
            f"emit_full_seed hash drifted for sasquatch_pr — re-lock by "
            f"pasting the new value into this test:\n  actual={actual}"
        )

    def test_full_seed_hash_lock_spec_example(self) -> None:
        # R.5.a — same as above for spec_example.
        import hashlib
        from quicksight_gen.common.l2.auto_scenario import (
            add_broken_rail_plants,
            boost_inv_fanout_plants,
            densify_scenario,
        )
        canonical_anchor = date(2030, 1, 1)
        instance = load_instance(_SPEC_EXAMPLE)
        base = default_scenario_for(instance, today=canonical_anchor).scenario
        dense = densify_scenario(base, factor=5)
        broken = add_broken_rail_plants(dense, instance, broken_count=15)
        final = boost_inv_fanout_plants(broken, amount_multiplier=5)
        sql = emit_full_seed(instance, final, anchor=canonical_anchor)
        actual = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        expected = (
            # v8.6.21 — re-locked after X.1.i FailedTransactionPlant
            # additions (see sasquatch_pr test above).
            "d7bfaadf98d888c2891be0e36b71fc096ad7ee26c0c4a6884587ecd67e584d4a"
        )
        assert actual == expected, (
            f"emit_full_seed hash drifted for spec_example — re-lock by "
            f"pasting the new value into this test:\n  actual={actual}"
        )

    def test_baseline_and_plant_id_namespaces_dont_collide(self) -> None:
        # Plants use tr-drift-*/tr-overdraft-*/etc. ids; baseline uses
        # tr-base-*/tr-base-bundle-*/tr-base-chain-*. None should overlap.
        import re
        instance = load_instance(_SASQUATCH_PR)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        sql = emit_full_seed(instance, scenario, anchor=_ANCHOR)
        baseline_ids = set(re.findall(r"'(tr-base[a-z0-9-]*)',", sql))
        plant_ids = set(re.findall(
            r"'(tr-(?:drift|overdraft|breach|stuck|tt|inv|rail|"
            r"supersession)[a-z0-9-]*)',",
            sql,
        ))
        assert not baseline_ids & plant_ids, (
            f"baseline + plant transfer_id namespaces overlap: "
            f"{baseline_ids & plant_ids}"
        )
        # After a deterministic run, state.balances should differ from
        # the initial state for every account that received legs. Run
        # the emit + replay the balance computation by re-running with
        # the same anchor and verifying determinism above does the heavy
        # lifting; here we just smoke-test that some accounts have
        # non-zero EOD balances by counting distinct account_ids in SQL.
        import re
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_baseline_seed(instance, anchor=_ANCHOR)
        # Account_id is the second value in each row.
        account_ids = set(re.findall(
            r"VALUES\n  \('[^']+', '([^']+)',", sql,
        ))
        assert len(account_ids) >= 5, (
            f"Expected ≥5 distinct account_ids touched by R.2.b leg loop; "
            f"got {len(account_ids)}: {account_ids}"
        )
