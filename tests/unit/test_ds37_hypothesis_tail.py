"""DS.3.7 — the Hypothesis tail: sampled coverage BEYOND the domains.

The enumeration gate (DS.3.5) is exhaustive over boundary-derived
grids; its escape class is values off the grid OR cardinalities above
the domain bounds. This module samples exactly that tail:

- int64-scale MAGNITUDES (the enumerated grids use small cents; here
  amounts draw from deep BIGINT range),
- fan_in CARDINALITIES above the enumerated counts,
- the TOPOLOGY axis via ``random_l2_yaml(seed)`` (a schema emitted
  from an arbitrary valid L2 must apply + refresh cleanly, tripwire
  silent, on an empty feed).

Every property asserts engine == residual on the drawn state through
the real emitter — the same claim as the gate, sampled where the gate
cannot enumerate. The profile is DERANDOMIZED with the example
database disabled (tests/conftest.py): a failure here reproduces
bit-identically on any machine.
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import duckdb
from hypothesis import given, settings
from hypothesis import strategies as st

from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.money import Cents
from recon_gen.common.spine.residuals import drift_residual
from recon_gen.common.sql import Dialect
from tests.enumeration.domains import fan_in as fan_in_domain
from tests.enumeration.domains._base import SPEC_EXAMPLE, profile_for
from tests.enumeration.harness import CellBuilder, EnumerationDB, artifacts_for
from tests.l2.fuzz import random_l2_yaml

SPEC = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
PREFIX = "spec_example"
DAY = dt.date(2030, 1, 1)
NOON = dt.datetime.combine(DAY, dt.time(12, 0))

# One artifacts build for the module — each example gets a fresh DB
# against the same emitted schema (the isolated-cell path the
# packed-vs-isolated lemma measured at roughly a tenth of a second).
_ARTIFACTS = artifacts_for(SPEC, prefix=PREFIX)

# Deep signed-BIGINT-cents range, with headroom so multi-leg sums stay
# inside int64 (the LAW ranges over all of ℤ; the storage column is
# int64, and sums overflowing the column are a feed-contract violation
# upstream of every law).
_AMOUNT = st.integers(min_value=-(2**58), max_value=2**58)


def _drift_engine(db: EnumerationDB, account_id: str) -> dict[str, int]:
    rows = db.fetchall(
        f"SELECT account_id, drift FROM {db.prefix}_drift "
        f"WHERE account_id = '{account_id}'",
    )
    return {str(r[0]): int(str(r[1])) for r in rows}


@settings(max_examples=20)
@given(
    amounts=st.lists(_AMOUNT, min_size=1, max_size=4),
    stored_delta=_AMOUNT,
)
def test_drift_agrees_at_int64_magnitudes(
    amounts: list[int], stored_delta: int,
) -> None:
    """Engine drift == residual drift for arbitrary deep-int64 legs
    and an arbitrarily-wrong stored claim — the value axis the
    enumerated small-cents grid cannot reach."""
    cell = CellBuilder()
    for i, amount in enumerate(amounts):
        cell.leg(
            id=f"hy{i:02d}", account="hy-acct", amount=amount,
            status=POSTED_STATUS, posting=NOON, transfer=f"hy-t{i:02d}",
        )
    stored = sum(amounts) + stored_delta
    cell.balance(account="hy-acct", day=DAY, money=stored)
    law = drift_residual(cell.state(), "hy-acct", DAY)
    assert law is not None
    tx_rows, bal_rows = cell.rows()
    db = EnumerationDB(_ARTIFACTS)
    try:
        db.insert(tx_rows, bal_rows)
        db.refresh()
        engine = _drift_engine(db, "hy-acct")
        if law == Cents(0):
            assert engine == {}, engine
        else:
            assert engine == {"hy-acct": law.value}, (engine, law)
    finally:
        db.close()


# The engine's expected_parent_count comes from the L2 CONFIG
# (v_config_chain_children), not from the cell — on spec_example
# artifacts it is fixed. The residual must be computed against the
# SAME resolved value or the two sides diverge by construction.
_FAN_IN_EXPECTED = profile_for(SPEC_EXAMPLE).fan_in_expected[
    (fan_in_domain.SPEC_CHAIN_PARENT, fan_in_domain.SPEC_CHILD_TEMPLATE)
]
assert _FAN_IN_EXPECTED is not None


@settings(max_examples=15)
@given(
    parent_count=st.integers(min_value=4, max_value=40),
    pattern=st.sampled_from(
        ("all_posted", "all_pending", "all_zq9x", "first_failed"),
    ),
    anchor=st.booleans(),
)
def test_fan_in_agrees_above_the_enumerated_counts(
    parent_count: int, pattern: str, anchor: bool,
) -> None:
    """Engine fan_in == residual for contributor counts far above the
    enumerated boundary sets. The cell's expected map is already
    residual-derived at the construction site (the harness contract),
    so engine == expected IS the law claim."""
    cell = fan_in_domain._build_cell(  # pyright: ignore[reportPrivateUsage]: reuse the domain's residual-deriving cell site so the tail samples the SAME construction the gate enumerates
        "hyf000", parent_count=parent_count, pattern=pattern,
        anchor=anchor, template=fan_in_domain.SPEC_CHILD_TEMPLATE,
        rail=fan_in_domain._SPEC_CHILD_RAIL,  # pyright: ignore[reportPrivateUsage]: same rail constant the domain's own cells stamp
        chain_parent=fan_in_domain.SPEC_CHAIN_PARENT,
        expected_count=_FAN_IN_EXPECTED,
    )
    db = EnumerationDB(_ARTIFACTS)
    try:
        db.insert(cell.tx_rows, cell.bal_rows)
        db.refresh()
        engine = fan_in_domain.read_engine(db)
        assert engine == dict(cell.expected["fan_in"]), (
            engine, cell.expected["fan_in"],
        )
    finally:
        db.close()


@settings(max_examples=5)
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_random_topology_schema_applies_and_refreshes_clean(seed: int) -> None:
    """The topology axis: an arbitrary VALID L2 (constructive fuzzer)
    emits a schema that applies, refreshes tripwire-silent on an empty
    feed and reports zero drift — the instance-parametricity claim,
    sampled."""
    yaml_text = random_l2_yaml(seed)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", prefix="ds37-topo-", delete=False,
    ) as handle:
        handle.write(yaml_text)
        scratch = Path(handle.name)
    try:
        instance = load_instance(scratch)
        conn = duckdb.connect()
        prefix = "topo"
        execute_script(
            conn, emit_schema(instance, prefix=prefix, dialect=Dialect.DUCKDB),
            dialect=Dialect.DUCKDB,
        )
        execute_script(
            conn,
            refresh_matviews_sql(instance, prefix=prefix, dialect=Dialect.DUCKDB),
            dialect=Dialect.DUCKDB,
        )
        drift_rows = conn.execute(
            f"SELECT COUNT(*) FROM {prefix}_drift",
        ).fetchone()
        assert drift_rows is not None and int(drift_rows[0]) == 0
        conn.close()
    finally:
        scratch.unlink(missing_ok=True)
