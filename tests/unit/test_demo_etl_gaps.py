"""BTa.8 — ETL-feed gap-plant emitter pins.

`emit_demo_etl_gap_sql` produces a small overlay of INSERTs the
Studio's Refresh Data step appends when no operator ETL hook is
configured. Verify the shape across dialects + that the planted
rows make Triage's `detect_gaps` surface the expected gap kinds.
"""

from __future__ import annotations

import duckdb

import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from recon_gen.common.l2 import load_instance
from recon_gen.common.l2.demo_etl_gaps import (
    DEMO_GAP_ID_PREFIX,
    PHANTOM_RAIL_NAME,
    PHANTOM_TEMPLATE_NAME,
    add_missing_metadata_gap_rows,
    add_phantom_rail_gap_rows,
    add_phantom_template_gap_rows,
    add_uncovered_rail_gap_rows,
    add_uncovered_template_gap_rows,
    emit_demo_etl_gap_sql,
)
from recon_gen.common.sql.dialect import Dialect


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def test_emits_three_phantom_rail_rows(writable_l2_yaml: Path) -> None:
    """Triage's `unmatched_rail` should pick these up — 3 rows is
    enough for the volume badge to render a meaningful count."""
    inst = load_instance(writable_l2_yaml)
    sql = emit_demo_etl_gap_sql(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    assert sql.count(f"'{PHANTOM_RAIL_NAME}'") == 3


def test_emits_two_phantom_template_rows(writable_l2_yaml: Path) -> None:
    inst = load_instance(writable_l2_yaml)
    sql = emit_demo_etl_gap_sql(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    assert sql.count(f"'{PHANTOM_TEMPLATE_NAME}'") == 2


def test_emits_missing_metadata_row_for_first_template_with_transfer_key(
    writable_l2_yaml: Path,
) -> None:
    """Picks the first template (sorted by name) that declares a
    transfer_key — that's the canonical required-metadata-key
    surface Triage's `_detect_missing_metadata_keys` reads from."""
    inst = load_instance(writable_l2_yaml)
    sql = emit_demo_etl_gap_sql(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    # Plant row has metadata='{}' and a real template name.
    assert "__demo_gap_missing_md_000" in sql


def test_every_demo_row_has_demo_gap_prefix_on_id(
    writable_l2_yaml: Path,
) -> None:
    """Every planted row's `id` starts with `__demo_gap_` so an
    operator browsing transactions or running cleanup can grep + drop
    them in one expression."""
    inst = load_instance(writable_l2_yaml)
    sql = emit_demo_etl_gap_sql(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    # Every INSERT statement should reference a demo-prefixed id +
    # transfer_id + the sentinel account_id (also prefixed). 3 demo-
    # prefixed strings per INSERT row.
    insert_count = sql.count("INSERT INTO testpfx_transactions")
    demo_id_count = sql.count(f"'{DEMO_GAP_ID_PREFIX}")
    assert demo_id_count == insert_count * 3


def test_emits_deterministic_for_fixed_anchor(
    writable_l2_yaml: Path,
) -> None:
    """Same anchor + instance ⇒ byte-identical SQL. Lets a future
    semantic-lock or replay test pin the demo overlay shape."""
    inst = load_instance(writable_l2_yaml)
    anchor = datetime(2026, 5, 30, 14, 0, 0)
    a = emit_demo_etl_gap_sql(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB, anchor=anchor,
    )
    b = emit_demo_etl_gap_sql(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB, anchor=anchor,
    )
    assert a == b


@pytest.mark.parametrize("dialect", [Dialect.DUCKDB, Dialect.POSTGRES, Dialect.ORACLE])
def test_emits_valid_sql_per_dialect(
    writable_l2_yaml: Path, dialect: Dialect,
) -> None:
    """Every dialect should at least produce parseable INSERT
    statements. Cross-dialect agreement on timestamp literal format
    is the only per-dialect divergence; the rest is plain SQL."""
    inst = load_instance(writable_l2_yaml)
    sql = emit_demo_etl_gap_sql(
        inst, prefix="testpfx", dialect=dialect,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    assert "INSERT INTO testpfx_transactions" in sql
    # Oracle wraps timestamps in TIMESTAMP '...' typed literal.
    if dialect is Dialect.ORACLE:
        assert "TIMESTAMP '" in sql
    elif dialect is Dialect.POSTGRES:
        # PG preserves the `T` separator per _sql_timestamp_literal.
        assert "'2026-05-30T14:00:00'" in sql
    else:
        # SQLite uses a space separator (parser-friendly).
        assert "'2026-05-30 14:00:00'" in sql


def test_emit_skips_missing_metadata_when_no_template_has_transfer_key(
    tmp_path: Path,
) -> None:
    """When the L2 declares no template with a transfer_key, the
    missing-metadata plant skips silently — phantom-rail + phantom-
    template rows still emit so Triage isn't fully empty."""
    # The spec_example fixture does have transfer_keys; this test
    # builds a minimal L2 with none to exercise the skip branch.
    # Mock the instance shape instead of loading a custom YAML — the
    # picker only reads `instance.transfer_templates[*].transfer_key`.
    from dataclasses import dataclass

    @dataclass
    class _FakeTmpl:
        name: str
        transfer_key: str | None

    @dataclass
    class _FakeInst:
        transfer_templates: tuple[_FakeTmpl, ...]
        rails: tuple[object, ...] = ()  # uncovered_rail plant reads this too

    fake = _FakeInst(transfer_templates=(
        _FakeTmpl(name="t1", transfer_key=None),
        _FakeTmpl(name="t2", transfer_key=""),
    ))
    # Cast to L2Instance — the emit function only reads
    # `.transfer_templates` so the mock satisfies its actual surface.
    from typing import cast
    from recon_gen.common.l2 import L2Instance

    sql = emit_demo_etl_gap_sql(
        cast(L2Instance, fake), prefix="testpfx", dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    # Phantom rail/template plants still land.
    assert f"'{PHANTOM_RAIL_NAME}'" in sql
    assert f"'{PHANTOM_TEMPLATE_NAME}'" in sql
    # Missing-metadata row absent.
    assert "__demo_gap_missing_md_" not in sql


def test_per_kind_plant_functions_are_independently_callable(
    writable_l2_yaml: Path,
) -> None:
    """BTa.8 — operator directive: each gap kind has its own plant
    function, parallel to `add_broken_rail_plants` /
    `boost_inv_fanout_plants`. Test that each one is callable in
    isolation + produces only its kind of rows."""
    inst = load_instance(writable_l2_yaml)
    anchor = datetime(2026, 5, 30, 14, 0, 0)
    rail_sql = add_phantom_rail_gap_rows(
        prefix="testpfx", dialect=Dialect.DUCKDB,
        anchor=anchor, count=3,
    )
    assert rail_sql.count(f"'{PHANTOM_RAIL_NAME}'") == 3
    assert PHANTOM_TEMPLATE_NAME not in rail_sql

    tmpl_sql = add_phantom_template_gap_rows(
        prefix="testpfx", dialect=Dialect.DUCKDB,
        anchor=anchor, count=2,
    )
    assert tmpl_sql.count(f"'{PHANTOM_TEMPLATE_NAME}'") == 2
    # Template plants set rail_name to the phantom-for-tmpl-gap
    # marker, NOT to legacy_card_swipe.
    assert PHANTOM_RAIL_NAME not in tmpl_sql

    md_sql = add_missing_metadata_gap_rows(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB, anchor=anchor,
    )
    assert "__demo_gap_missing_md_" in md_sql


def test_uncovered_rail_emits_delete_for_alphabetically_last_rail(
    writable_l2_yaml: Path,
) -> None:
    """BTa.8 + cold-read v3 — coverage card should show ✗ for at
    least one rail. The plant emits DELETE for the alphabetically-
    last rail (stable + demo-clear)."""
    inst = load_instance(writable_l2_yaml)
    sql = add_uncovered_rail_gap_rows(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB,
    )
    assert "DELETE FROM testpfx_transactions" in sql
    assert "WHERE rail_name =" in sql
    # The alphabetically-last rail name surfaces in the SQL.
    rail_names = sorted(str(r.name) for r in inst.rails)
    assert rail_names[-1] in sql


def test_uncovered_template_emits_delete_for_alphabetically_last_template(
    writable_l2_yaml: Path,
) -> None:
    inst = load_instance(writable_l2_yaml)
    sql = add_uncovered_template_gap_rows(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB,
    )
    assert "DELETE FROM testpfx_transactions" in sql
    assert "WHERE template_name =" in sql
    tmpl_names = sorted(str(t.name) for t in inst.transfer_templates)
    assert tmpl_names[-1] in sql


def test_uncovered_rail_returns_empty_when_no_rails() -> None:
    """Vacuous L2 (no rails) — return empty, don't emit a malformed
    DELETE."""
    from dataclasses import dataclass
    from typing import cast
    from recon_gen.common.l2 import L2Instance

    @dataclass
    class _FakeInst:
        rails: tuple[object, ...] = ()
        transfer_templates: tuple[object, ...] = ()

    sql = add_uncovered_rail_gap_rows(
        cast(L2Instance, _FakeInst()),
        prefix="p", dialect=Dialect.DUCKDB,
    )
    assert sql == ""


def test_uncovered_rail_sql_escapes_single_quotes() -> None:
    """Defensive — rail names come from operator-authored L2 yaml.
    Single quotes in a name would otherwise break the DELETE."""
    from dataclasses import dataclass
    from typing import cast
    from recon_gen.common.l2 import L2Instance

    @dataclass
    class _R:
        name: str

    @dataclass
    class _FakeInst:
        rails: tuple[_R, ...]
        transfer_templates: tuple[object, ...] = ()

    fake = _FakeInst(rails=(_R(name="rail_with_'quote"),))
    sql = add_uncovered_rail_gap_rows(
        cast(L2Instance, fake), prefix="p", dialect=Dialect.DUCKDB,
    )
    # Single quote doubled per SQL string-literal rule.
    assert "rail_with_''quote" in sql


def test_emit_composer_includes_both_insert_and_delete_plants(
    writable_l2_yaml: Path,
) -> None:
    """`emit_demo_etl_gap_sql` chains every plant kind — Triage
    INSERTs + Coverage DELETEs — so a single call plants the full
    demo failure set."""
    inst = load_instance(writable_l2_yaml)
    sql = emit_demo_etl_gap_sql(
        inst, prefix="testpfx", dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    assert "INSERT INTO testpfx_transactions" in sql
    assert "DELETE FROM testpfx_transactions" in sql


def test_per_kind_plant_count_parameterized() -> None:
    """count knob respects caller intent — a future demo-density
    knob can scale plant volume per kind independently."""
    anchor = datetime(2026, 5, 30, 14, 0, 0)
    sql_low = add_phantom_rail_gap_rows(
        prefix="p", dialect=Dialect.DUCKDB, anchor=anchor, count=1,
    )
    sql_high = add_phantom_rail_gap_rows(
        prefix="p", dialect=Dialect.DUCKDB, anchor=anchor, count=10,
    )
    assert sql_low.count("INSERT INTO p_transactions") == 1
    assert sql_high.count("INSERT INTO p_transactions") == 10


def test_gaps_actually_surface_in_triage_detect_gaps(
    writable_l2_yaml: Path,
) -> None:
    """End-to-end pin: plant the gaps into a seeded sqlite DB, run
    detect_gaps, assert the phantom rail + phantom template surface
    as gap cards. Closes the loop between this emitter and the BTa.4
    Triage page that consumes the planted rows."""
    import asyncio
    import os
    import sqlite3
    import tempfile

    from recon_gen.common.db import (
        AsyncConnectionPool, execute_script, make_connection_pool,
    )
    from recon_gen.common.l2.contract import derive_column_contracts
    from recon_gen.common.l2.triage import detect_gaps
    from tests._test_helpers import make_test_config

    inst = load_instance(writable_l2_yaml)
    prefix = writable_l2_yaml.stem
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = duckdb.connect(db_path)
    conn.execute(
        f"CREATE TABLE {prefix}_transactions ("
        "entry INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL, account_id TEXT NOT NULL, "
        "account_role TEXT, account_parent_role TEXT, "
        "account_scope TEXT NOT NULL, "
        "amount_money BIGINT NOT NULL, amount_direction TEXT NOT NULL, "
        "status TEXT NOT NULL, posting TIMESTAMP NOT NULL, "
        "transfer_id TEXT NOT NULL, transfer_parent_id TEXT, "
        "rail_name TEXT NOT NULL, "
        "template_name TEXT, origin TEXT NOT NULL, metadata TEXT)"
    )
    gap_sql = emit_demo_etl_gap_sql(
        inst, prefix=prefix, dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    cur = conn.cursor()
    execute_script(cur, gap_sql, dialect=Dialect.DUCKDB)
    conn.commit()
    conn.close()

    cfg = make_test_config(dialect=Dialect.DUCKDB, demo_database_url=db_path)
    pool: AsyncConnectionPool = asyncio.run(make_connection_pool(cfg))
    try:
        contracts = derive_column_contracts(inst)
        gaps = asyncio.run(detect_gaps(
            pool, prefix, inst, contracts, dialect=Dialect.DUCKDB,
        ))
    finally:
        asyncio.run(pool.close())
        if os.path.exists(db_path):
            os.unlink(db_path)

    kinds = {g.kind for g in gaps}
    assert "unmatched_rail" in kinds
    assert "unmatched_template" in kinds
    # Phantom rail value surfaces as observed_value of one of the gaps.
    rail_gaps = [g for g in gaps if g.kind == "unmatched_rail"]
    assert any(g.observed_value == PHANTOM_RAIL_NAME for g in rail_gaps)
