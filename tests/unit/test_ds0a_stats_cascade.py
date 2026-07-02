"""DS.0a — stats cascade with the refresh dependency order.

The pre-DS.0a refresh script emitted every REFRESH first and every
stats statement at the end, so each matview's FIRST refresh planned
against unanalyzed upstreams and the correlated subqueries went
quadratic (Oracle full-domain 679s -> 7.63s under the cascade; PG
computed_subledger 49.7s -> 0.6s — docs/audits/ds_0_spike_evidence/).

These tests pin the cascade as a STRUCTURAL property of the emitted
script, per dialect: base-table stats before any refresh, and every
matview's stats immediately after its own refresh. No statement
counts are asserted anywhere — a count is an uncheckable claim the
moment a matview is added; the property is what matters.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.schema import refresh_matviews_sql
from recon_gen.common.sql import Dialect

SPEC = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
PREFIX = "spec_example"


def _statements(script: str) -> list[str]:
    """One statement per non-empty line (both emitters are line-based)."""
    return [line.strip() for line in script.splitlines() if line.strip()]


def _stats_target(stmt: str, dialect: Dialect) -> str | None:
    """The table a stats statement gathers for, or None."""
    if dialect is Dialect.POSTGRES:
        m = re.fullmatch(r"ANALYZE (\w+);", stmt)
    else:
        m = re.fullmatch(
            r"BEGIN DBMS_STATS\.GATHER_TABLE_STATS\(USER, '(\w+)'\); END;", stmt,
        )
    return m.group(1) if m else None


def _refresh_target(stmt: str, dialect: Dialect) -> str | None:
    """The matview a refresh statement rebuilds, or None."""
    if dialect is Dialect.POSTGRES:
        m = re.fullmatch(r"REFRESH MATERIALIZED VIEW (?:CONCURRENTLY )?(\w+);", stmt)
    else:
        m = re.fullmatch(
            r"BEGIN DBMS_MVIEW\.REFRESH\('(\w+)', method => 'C'\); END;", stmt,
        )
    return m.group(1) if m else None


@pytest.fixture(scope="module")
def instance() -> L2Instance:
    return load_instance(SPEC)


@pytest.mark.parametrize("dialect", [Dialect.POSTGRES, Dialect.ORACLE])
class TestStatsCascade:
    def test_base_table_stats_precede_every_refresh(self, instance: L2Instance, dialect: Dialect) -> None:
        """Cascade roots: a bulk load leaves the base tables unanalyzed,
        and the Current* refreshes plan against them — their stats must
        come before ANY refresh statement."""
        stmts = _statements(refresh_matviews_sql(instance, prefix=PREFIX, dialect=dialect))
        first_refresh = next(
            i for i, s in enumerate(stmts) if _refresh_target(s, dialect)
        )
        base_stats_positions = [
            i for i, s in enumerate(stmts)
            if _stats_target(s, dialect) in (f"{PREFIX}_transactions", f"{PREFIX}_daily_balances")
        ]
        assert base_stats_positions, "base-table stats statements missing entirely"
        assert max(base_stats_positions) < first_refresh, (
            "base-table stats must precede the first REFRESH — a dependent "
            "planning against an unanalyzed base table is the DS.0a bug"
        )

    def test_every_matview_stats_lands_immediately_after_its_refresh(self, instance: L2Instance, dialect: Dialect) -> None:
        """The cascade property itself: REFRESH X is immediately followed
        by stats-of-X, so no later (dependent) refresh can plan against
        an unanalyzed X."""
        stmts = _statements(refresh_matviews_sql(instance, prefix=PREFIX, dialect=dialect))
        refreshed = [(i, _refresh_target(s, dialect)) for i, s in enumerate(stmts)]
        refreshed = [(i, n) for i, n in refreshed if n]
        assert refreshed, "no refresh statements found — parser or emitter broke"
        for i, name in refreshed:
            assert i + 1 < len(stmts) and _stats_target(stmts[i + 1], dialect) == name, (
                f"stats for {name} must be the statement immediately after its "
                f"refresh; found: {stmts[i + 1] if i + 1 < len(stmts) else '<end>'}"
            )

    def test_no_matview_stats_before_its_own_refresh(self, instance: L2Instance, dialect: Dialect) -> None:
        """Stats on a stale matview would bless the WRONG row counts —
        the cascade orders stats strictly after materialization."""
        stmts = _statements(refresh_matviews_sql(instance, prefix=PREFIX, dialect=dialect))
        refresh_pos = {
            n: i for i, s in enumerate(stmts) if (n := _refresh_target(s, dialect))
        }
        for i, s in enumerate(stmts):
            target = _stats_target(s, dialect)
            if target in refresh_pos:
                assert i > refresh_pos[target], (
                    f"stats for {target} at statement {i} precede its refresh "
                    f"at {refresh_pos[target]}"
                )


def test_duckdb_refresh_stays_table_based(instance: L2Instance) -> None:
    """DuckDB matviews are CREATE TABLE AS SELECT — stats ride the CTAS,
    no cascade statements expected, and the script must keep rebuilding
    tables rather than emitting REFRESH statements."""
    script = refresh_matviews_sql(instance, prefix=PREFIX, dialect=Dialect.DUCKDB)
    code_lines = [
        line for line in script.splitlines() if line.strip() and not line.lstrip().startswith("--")
    ]
    assert not any(line.lstrip().startswith("REFRESH MATERIALIZED VIEW") for line in code_lines)
    assert any("CREATE" in line for line in code_lines)
    assert any("DROP" in line for line in code_lines)
