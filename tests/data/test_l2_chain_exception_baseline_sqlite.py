"""AJ — chain-related exception checks must surface only intended /
genuine violations on a healthy demo baseline.

Two exception surfaces read chain semantics off the seeded data:

- ``<prefix>_multi_xor_violation`` (L1 matview) — a multi-children XOR
  chain parent firing that fired zero or ≥2 of its declared XOR
  siblings.
- ``<prefix>_chain_orphans`` (L2FT dataset) — a Required chain whose
  parent fired but no matched child did.

Both run against ``<prefix>_(current_)transactions`` — i.e. **real
customer ETL data in production**, where a chain-parent firing with no
child genuinely IS a violation. So the fix for the bugs below lives in
the demo SEED (stop manufacturing the firings), never in the dataset /
matview SQL (which must stay production-honest — there are no
``tr-*`` prefixes or "scaffolding" in real data).

Bugs these tests pin (RED until the AJ.2/AJ.3/AJ.4 fixes land):

- **Gap H residual (AJ.3)** — plant helpers that target a DIFFERENT
  invariant (``tr-rail`` rail-conformance, ``tr-tt`` template, ``tr-xor``
  XOR-variant, ``tr-inbreach`` limit-breach, …) fire a chain-parent
  rail/template WITHOUT its chain child, so the firing false-positives
  as a ``multi_xor_violation`` 'missed' (and a chain orphan). Fix:
  route those firings through ``_baseline_xor_child_pick`` so they're
  chain-complete. Only the intended ``tr-mxor-*`` plants should remain.
- **Gap I (AJ.4)** — ``chain_orphans`` does a naive ``parent − child``
  subtraction, so a ``fan_in`` (N:1) chain reads as a pile of orphans
  on healthy batching.

The structural guard (PASSES today) locks in the AG.1/AG.2 composition:
baseline (``tr-base-*``) chain-parent firings always get exactly one
XOR/fan-in child, so they never appear in ``multi_xor_violation``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import recon_gen.apps.l2_flow_tracing.datasets as ds_mod
from recon_gen.common.db import execute_script, _register_sqlite_aggregates
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.seed import emit_full_seed
from recon_gen.common.sql import Dialect
from recon_gen.cli._helpers import build_full_seed_sql

from tests._test_helpers import make_test_config

_L2_DIR = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _L2_DIR / "spec_example.yaml"
_SASQUATCH_PR = _L2_DIR / "sasquatch_pr.yaml"
_ANCHOR = date(2026, 4, 30)

_FIXTURES = [
    pytest.param(_SPEC_EXAMPLE, "spec_example", id="spec_example"),
    pytest.param(_SASQUATCH_PR, "sasquatch_pr", id="sasquatch_pr"),
]


def _seed_refresh(
    yaml_path: Path, prefix: str,
) -> tuple[L2Instance, sqlite3.Cursor]:
    """Apply schema + full seed + matview refresh for ``yaml_path`` against
    a fresh in-memory SQLite; return the instance + an open cursor."""
    inst = load_instance(yaml_path)
    scenario = default_scenario_for(inst, today=_ANCHOR).scenario
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(inst, prefix=prefix, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    execute_script(
        cur,
        emit_full_seed(
            inst, scenario, prefix=prefix, anchor=_ANCHOR,
            dialect=Dialect.SQLITE,
        ),
        dialect=Dialect.SQLITE,
    )
    execute_script(
        cur, refresh_matviews_sql(inst, prefix=prefix, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    return inst, cur


# -- Structural guard: AG.1/AG.2 composition (PASSES) -----------------------


@pytest.mark.parametrize("yaml_path,prefix", _FIXTURES)
def test_baseline_chain_parent_firings_are_chain_complete(
    yaml_path: Path, prefix: str,
) -> None:
    """Every baseline (``tr-base-*``) multi-children-chain parent firing
    gets exactly one XOR/fan-in child — so none surface in
    ``multi_xor_violation``.

    Locks in the AG.1 (Gap B template-firing synthesis) + AG.2 (Gap C
    per-firing XOR child-pick) composition. If a future change breaks
    it, baseline ``tr-base-*`` rows reappear here.
    """
    _inst, cur = _seed_refresh(yaml_path, prefix)
    baseline_violations = cur.execute(
        f"SELECT parent_transfer_id, disagreement_kind "
        f"FROM {prefix}_multi_xor_violation "
        f"WHERE parent_transfer_id LIKE 'tr-base-%'"
    ).fetchall()
    assert not baseline_violations, (
        f"{len(baseline_violations)} baseline (tr-base-*) chain-parent "
        f"firings surfaced as multi_xor violations — the AG.1/AG.2 "
        f"composition regressed (baseline firings must each get exactly "
        f"one XOR/fan-in child): {baseline_violations[:5]}"
    )


# -- Gap H residual (AJ.3): only intended tr-mxor-* plants (RED) -------------


@pytest.mark.parametrize("yaml_path,prefix", _FIXTURES)
def test_multi_xor_violation_holds_only_intended_plants(
    yaml_path: Path, prefix: str,
) -> None:
    """The ONLY rows ``multi_xor_violation`` produces on a healthy demo
    baseline are the intended MultiXor plants (``tr-mxor-missed`` /
    ``tr-mxor-overlap``).

    AJ.3 (Gap H residual) RED: any other transfer_id prefix means a plant
    that targets a *different* invariant fired a chain-parent leg without
    its chain child, false-positiving here:

    - ``tr-inbreach-*`` — limit-breach plant on a chain-parent rail
    - ``tr-xor-*``      — XOR-variant plant on a chain-parent template
    - ``tr-rail-*`` / ``tr-tt-*`` — broad-mode rail/template plants

    The fix routes those firings through ``_baseline_xor_child_pick`` so
    the planted firing is chain-complete and only trips the invariant it
    actually targets. (Prefix matching is fine HERE — this is a demo-seed
    test; the matview SQL itself stays prefix-blind for production.)
    """
    _inst, cur = _seed_refresh(yaml_path, prefix)
    incidental = cur.execute(
        f"SELECT parent_transfer_id, parent_rail_or_template_name, "
        f"       disagreement_kind "
        f"FROM {prefix}_multi_xor_violation "
        f"WHERE parent_transfer_id NOT LIKE 'tr-mxor-%' "
        f"ORDER BY parent_transfer_id"
    ).fetchall()
    assert not incidental, (
        f"multi_xor_violation contains {len(incidental)} row(s) from plant "
        f"scaffolding that fired a chain-parent leg without its child "
        f"(Gap H residual — should be routed through "
        f"_baseline_xor_child_pick so the firing is chain-complete): "
        f"{incidental}"
    )


def _seed_refresh_densified(
    yaml_path: Path, prefix: str,
) -> tuple[L2Instance, sqlite3.Cursor]:
    """Like ``_seed_refresh`` but emits the DENSIFIED seed (the
    ``data apply`` path: ×5 densify + broken-rail + inv-fanout plants) via
    ``build_full_seed_sql`` — the full plant set where broad-mode coverage
    fires the chain-parent rails that trip the AJ.6 residual."""
    inst = load_instance(yaml_path)
    cfg = make_test_config(dialect=Dialect.SQLITE, db_table_prefix=prefix)
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(inst, prefix=prefix, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    execute_script(
        cur, build_full_seed_sql(cfg, inst, anchor=_ANCHOR),
        dialect=Dialect.SQLITE,
    )
    execute_script(
        cur, refresh_matviews_sql(inst, prefix=prefix, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    return inst, cur


@pytest.mark.parametrize("yaml_path,prefix", _FIXTURES)
def test_broad_rail_coverage_firings_are_chain_complete(
    yaml_path: Path, prefix: str,
) -> None:
    """AJ.6 (Gap H broad-rail residual): on the DENSIFIED seed, no
    broad-mode RAIL coverage firing (``tr-rail-*``) of a multi-XOR chain
    parent stays childless in ``multi_xor_violation``.

    The broad rail picker SKIPS multi-XOR chains (``auto_scenario.
    _build_broad_rail_firings`` — "the seed.py XOR picker chooses one
    sibling per parent firing"), so a coverage firing of such a parent has
    no child unless ``_emit_plant_chain_completion`` fills it. The chain
    parent may be the rail itself (``BulkAccrualSettlement``) OR the
    firing's template (``DisbursementCycle``) — the rail emitter completes
    via both (``via_template_name``).

    RED before AJ.6 (6 ``tr-rail`` rows on spec_example densified), GREEN
    after. Densified because the residual only surfaces in the full plant
    set. NOTE: the intentional ``tr-tt-*`` firing-1/2 demo (overlap/missed
    for the tt-instances explorer) and the dedicated ``tr-mxor-*`` plants
    are NOT residuals — this guards only the unintentional ``tr-rail-*``.
    """
    _inst, cur = _seed_refresh_densified(yaml_path, prefix)
    rail_residuals = cur.execute(
        f"SELECT parent_transfer_id, parent_rail_or_template_name, "
        f"       disagreement_kind "
        f"FROM {prefix}_multi_xor_violation "
        f"WHERE parent_transfer_id LIKE 'tr-rail-%' "
        f"ORDER BY parent_transfer_id"
    ).fetchall()
    assert not rail_residuals, (
        f"{len(rail_residuals)} broad-rail coverage firing(s) of a "
        f"multi-XOR chain parent are childless in multi_xor_violation "
        f"(AJ.6 — _emit_plant_chain_completion should complete them via "
        f"rail.name OR template_name): {rail_residuals}"
    )


# -- Gap J (AL): template-level firings_typical_per_period (RED) -------------


def test_multi_leg_template_e8_fires_as_coupled_unit() -> None:
    """AL (Gap J): a multi-1-leg-rail `expected_net=0` template that
    declares `firings_typical_per_period` but is NOT a chain parent must
    fire as a coupled UNIT — both legs sharing one Transfer per firing.

    spec_example's `CardLoadCycle` (`CardLoadCardholderCredit` +
    `CardLoadSweepDebit`) is the broken case. Pre-AL.3 the legs fire only
    via the per-rail loop — each with its own `transfer_id`, never paired
    — so the template's E8 band is ignored and the flow can't be scaled
    as a unit. RED: zero Transfers carry both legs. AL.3 (template-level
    E8 → unit-firing for any E8-declaring template + per-rail loop skips
    its legs) pairs them → GREEN. spec_example-only (the fixture lives
    there); non-densified is enough (the unit-firing is a baseline path).
    """
    _inst, cur = _seed_refresh(_SPEC_EXAMPLE, "spec_example")
    paired = cur.execute(
        "SELECT transfer_id FROM spec_example_transactions "
        "WHERE rail_name IN ('CardLoadCardholderCredit', 'CardLoadSweepDebit') "
        "GROUP BY transfer_id HAVING COUNT(DISTINCT rail_name) = 2"
    ).fetchall()
    assert paired, (
        "CardLoadCycle's two SingleLegRail legs never share a Transfer — "
        "template-level firings_typical_per_period didn't drive a coupled "
        "unit-firing (Gap J / AL.3); the legs only fired standalone via the "
        "per-rail loop."
    )


def test_chain_parent_template_legs_keep_independent_counts() -> None:
    """AL.6 (Gap J follow-up, v11.9.3): a chain-parent template WITHOUT a
    template-level `firings_typical_per_period` has INDEPENDENT legs — they
    must keep their distinct per-leg rail-E8 volumes, NOT collapse to one
    shared per-firing count.

    spec_example's `DisbursementCycle` is a multi-XOR chain parent with no
    template-E8; its two leg_rails carry deliberately different rail-E8
    bands — `DisbursementAccrual` [4,6] vs `DisbursementFee` [1,1]. The
    v11.9.2 AL leg-skip keyed off chain-parenthood, so BOTH legs were
    skipped from the per-rail loop and fired ONLY as the 1/business-day unit
    firing → equal counts (collapsed). v11.9.3 gates the skip on template-E8
    alone, so the legs fire independently at their own bands →
    DisbursementAccrual fires several times more than DisbursementFee. RED
    (counts ~equal) → GREEN. Complement of
    test_multi_leg_template_e8_fires_as_coupled_unit (the E8 template that
    SHOULD couple). spec_example-only; non-densified is enough (the per-rail
    loop is a baseline path).
    """
    _inst, cur = _seed_refresh(_SPEC_EXAMPLE, "spec_example")
    accrual = cur.execute(
        "SELECT COUNT(*) FROM spec_example_transactions "
        "WHERE rail_name = 'DisbursementAccrual'"
    ).fetchone()[0]
    fee = cur.execute(
        "SELECT COUNT(*) FROM spec_example_transactions "
        "WHERE rail_name = 'DisbursementFee'"
    ).fetchone()[0]
    assert accrual > fee * 2, (
        f"DisbursementCycle's two leg_rails collapsed to a shared per-firing "
        f"count (DisbursementAccrual={accrual}, DisbursementFee={fee}) — the "
        f"chain-parent unit-firing leg-skip swallowed their independent "
        f"per-rail volumes (Gap J follow-up / v11.9.3). With the skip gated "
        f"on template-E8 only, the [4,6] accrual leg must fire well above the "
        f"[1,1] fee leg."
    )


# -- Gap I (AJ.4): fan_in chains are not orphans (RED) -----------------------


@pytest.mark.parametrize("yaml_path,prefix", _FIXTURES)
def test_cascade_and_opening_legs_tagged_balance_maintenance(
    yaml_path: Path, prefix: str,
) -> None:
    """AJ.4b — cascade-credit + opening-balance scaffolding legs carry the
    dedicated ``InternalBalanceMaintenance`` rail, NOT a money-movement
    rail.

    These legs are demo balance-maintenance scaffolding (they net to zero
    / fund starting balances so the cumulative-balance walk stays
    positive); they aren't genuine firings. Tagging them with a real rail
    made every firing-count analysis count them (the 2615-row
    ``ACHOriginationDailySweep`` false-orphan flood in chain_orphans). The
    fix tags them with a label-only rail that's not a chain parent. Both
    fixtures declare ``InternalBalanceMaintenance``; this guards the seed
    against regressing to a money-movement label.
    """
    _inst, cur = _seed_refresh(yaml_path, prefix)
    mislabeled = cur.execute(
        f"SELECT DISTINCT rail_name FROM {prefix}_current_transactions "
        f"WHERE (transfer_id LIKE 'tr-base-cascade-%' "
        f"       OR transfer_id LIKE 'tr-base-opening-%') "
        f"  AND rail_name <> 'InternalBalanceMaintenance'"
    ).fetchall()
    assert not mislabeled, (
        f"cascade/opening scaffolding legs are tagged with money-movement "
        f"rail(s) instead of InternalBalanceMaintenance — they will inflate "
        f"chain_orphans / multi_xor / rail firing counts: {mislabeled}"
    )


def test_chain_orphans_fan_in_aware_not_naive_subtraction() -> None:
    """A declared ``fan_in`` (N:1) chain is healthy when N parents
    converge on far fewer shared child Transfers — ``chain_orphans`` must
    NOT read the ``(N parents − few batches)`` difference as orphans.

    AJ.4 (Gap I): pre-fix the dataset did a naive
    ``GREATEST(parent_firing_count - child_firing_count, 0)`` and marked
    single-child chains (including fan_in) ``Required``, so a fan_in
    chain's orphan_count == the full N:1 difference. The fix makes the
    dataset fan_in-aware via ``<prefix>_transfer_parents``: orphan_count
    is the count of parent firings NOT assigned to any batch (the genuine
    "cycle closed but never batched"), which the gap doc explicitly still
    surfaces — so the assertion is that the fan_in count drops BELOW the
    naive N:1 difference, not that it's zero. (production-correct: fan_in
    is a real structural property of the L2.)

    NOTE: chain_orphans carries other, separate noise this test does not
    gate — a Required chain seeded N:1 by fixture mismodeling
    (ACHOriginationDailySweep→ConcentrationToFRBSweep) + opening-balance
    rows attributed to a chain-parent rail. Those are tracked apart from
    Gap I (declared-fan_in awareness).
    """
    inst, cur = _seed_refresh(_SASQUATCH_PR, "sasquatch_pr")
    cfg = replace(
        make_test_config(), db_table_prefix="sasquatch_pr",
        dialect=Dialect.SQLITE,
    )
    aws_ds = ds_mod.build_exc_chain_orphans_dataset(cfg, inst)
    custom_sql = list(aws_ds.PhysicalTableMap.values())[0].CustomSql
    assert custom_sql is not None
    sql = custom_sql.SqlQuery
    rows = cur.execute(sql).fetchall()

    fan_in_parents = {
        str(c.parent)
        for c in inst.chains
        for ch in c.children
        if ch.fan_in
    }
    # cols: parent_name, child_name, parent_firing_count,
    #       child_firing_count, orphan_count
    fan_in_rows = [r for r in rows if r[0] in fan_in_parents]
    for parent, child, parent_n, child_n, orphan_n in fan_in_rows:
        naive_diff = parent_n - child_n
        assert orphan_n < naive_diff, (
            f"fan_in chain {parent}->{child}: orphan_count={orphan_n} must be "
            f"BELOW the naive parent-child difference {naive_diff} "
            f"(={parent_n}-{child_n}). chain_orphans is still doing the naive "
            f"N:1 subtraction instead of counting unbatched parents (Gap I)."
        )
