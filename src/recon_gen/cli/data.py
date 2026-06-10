"""``recon-gen data`` — per-prefix demo seed data.

Five operations:

  apply    — emit the seed SQL (default), or ``--execute`` against the demo DB.
  refresh  — emit the REFRESH MATERIALIZED VIEW SQL, or ``--execute``.
  clean    — emit TRUNCATE statements, or ``--execute`` to wipe the rows.
  lock     — write or verify the canonical-anchor seed SQL at
             ``tests/data/_locked_seeds/<instance>.<dialect>.sql``.
  test     — pytest the seed pipeline (locked-SQL byte check).

Same emit-vs-execute pattern as the schema group — default is
print the script, ``--execute`` actually runs it.
"""

from __future__ import annotations

import difflib
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2.primitives import L2Instance
    from recon_gen.common.sql import Dialect as _DialectT

from recon_gen.common.as_of_frame import LOCKED_ANCHOR
from recon_gen.cli._helpers import (
    build_full_seed_sql,
    config_option,
    connect_and_apply,
    emit_to_target,
    execute_option,
    l2_instance_option,
    output_option,
    resolve_l2_for_demo,
)


# X.1.k — fixed canonical anchor for locked-SQL determinism lives on
# `AsOfFrame.LOCKED_ANCHOR` (`common/as_of_frame.py`). BD.6 dropped the
# `_CANONICAL_LOCK_ANCHOR = LOCKED_ANCHOR` alias kept here for "caller
# compat" — the alias was the AQ.3 funnel's transition shim, no longer
# needed (data_lock command retired in AZ.5; data_semantic_lock imports
# LOCKED_ANCHOR directly).

# AZ.5 — byte-locked seed dir + `data lock` CLI retired in favor
# of semantic locks (per-violation-set JSON). _LOCKED_SEEDS_DIR
# constant + data_lock command removed in the same commit;
# `data_semantic_lock` imports `LOCKED_ANCHOR` directly.
_SEMANTIC_LOCKS_DIR = (
    Path(__file__).resolve().parents[3]
    / "tests" / "data" / "_semantic_locks"
)


@click.group()
def data() -> None:
    """Per-prefix seed data: 90-day baseline + plant overlays."""


@data.command("apply")
@l2_instance_option()
@config_option(required_for_dialect_only=True)
@output_option()
@execute_option()
@click.option(
    "--seed-density",
    type=float,
    default=1.0,
    show_default=True,
    metavar="<float>",
    help=(
        "Y.2.gate.c.13.1 — scalar multiplier on plant density "
        "(densify factor / broken-rail count / fanout multiplier). "
        "1.0 = byte-identical to pre-c.13 behavior; 2.0 = double the "
        "plants; 0.5 = halve. Operator opt-in for heavier nightly "
        "scenarios; default keeps locked SQL files valid."
    ),
)
def data_apply(
    l2_instance_path: str | None, config: str,
    output: str | None, execute: bool, seed_density: float,
) -> None:
    """Emit the demo seed SQL (or ``--execute`` to insert against the demo DB).

    The composition: 90-day baseline → densify per-kind plants ×5 →
    add 15 broken-rail stuck_pending plants → boost inv_fanout amounts
    ×5 → emit_full_seed. ``--seed-density=N`` scales the three knobs.

    Default: print every INSERT to stdout (or to ``-o FILE``). Pass
    ``--execute`` to connect + insert.

    Assumes the schema is already applied (``schema apply --execute``
    or a prior schema). After ``data apply --execute`` you'll likely
    want ``data refresh --execute`` so the matviews see the new rows.
    """
    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    sql = build_full_seed_sql(cfg, instance, density=seed_density)

    if execute:
        _cz6_pre_flight_migrate_mark(cfg)
        connect_and_apply(cfg, sql, label="seed data")
    else:
        emit_to_target(sql, output, label="seed SQL")


def _cz6_pre_flight_migrate_mark(cfg: "Config") -> None:
    """CZ.6 — pre-flight check on ``data apply --execute``.

    Counts rows in both base tables where ``metadata.source`` is unset
    (pre-CZ rows). Three outcomes:

      * No unstamped rows → silent no-op (the common steady-state path).
      * Unstamped rows + ``cfg.etl_hook is None`` (standalone mode) →
        auto-mark with ``source='training'`` and continue. Locked-
        decision rationale: pre-CZ DBs in standalone mode were
        demonstrably running through the training/seed path (etl_hook
        is new in BS.4 + CZ; absent ⇒ no real-data integrator existed).
      * Unstamped rows + ``cfg.etl_hook is not None`` (ETL mode) → log
        a one-line hint and continue without auto-marking. The
        integrator may have loaded real rows before CZ landed; the
        explicit ``recon-gen schema migrate-mark`` verb lets them
        choose ``--source=real`` for real rows or
        ``--source=training`` for synthetic ones.

    Idempotent: re-running against an already-stamped DB is a no-op
    (counts return 0). Catalog errors (base tables missing on a virgin
    DB) silently swallow — the seed apply will create the rows
    correctly stamped by CZ.2.
    """
    # The base tables may not exist yet (operator running ``schema
    # apply`` + ``data apply`` for the first time on a fresh DB).
    # Probe defensively: any error from the count path means there's
    # nothing to migrate.
    from recon_gen.common.db import connect_demo_db
    from recon_gen.common.l2.migrate_mark import (
        count_unstamped_rows, stamp_unstamped_rows,
    )
    if not cfg.demo_database_url:
        return
    try:
        conn = connect_demo_db(cfg)
    except ImportError:
        # Driver not installed — let the main apply path surface the
        # error consistently.
        return
    try:
        try:
            tx_unstamped, bal_unstamped = count_unstamped_rows(
                conn, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
            )
        except Exception:  # noqa: BLE001 — dialect-specific catalog errors are not a shared exception base
            # Base tables likely don't exist on this DB yet; nothing
            # to migrate.
            return
        total = tx_unstamped + bal_unstamped
        if total == 0:
            return
        if cfg.etl_hook is not None:
            click.echo(
                f"  [cz6] found {total:,} pre-CZ row(s) "
                f"({tx_unstamped:,} transactions, "
                f"{bal_unstamped:,} daily_balances) lacking "
                f"metadata.source; NOT auto-marking because etl_hook "
                f"is configured. If those rows are real, leave them. "
                f"If synthetic, run `recon-gen schema migrate-mark "
                f"--execute` to stamp them.",
                err=True,
            )
            return
        click.echo(
            f"  [cz6] migrating {total:,} pre-CZ row(s) "
            f"({tx_unstamped:,} transactions, "
            f"{bal_unstamped:,} daily_balances): "
            f"stamping metadata.source='training' "
            f"(cfg.etl_hook is None ⇒ standalone-mode default).",
            err=True,
        )
        try:
            stamp_unstamped_rows(
                conn,
                prefix=cfg.db_table_prefix,
                dialect=cfg.dialect,
                source="training",
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


@data.command("refresh")
@l2_instance_option()
@config_option(required_for_dialect_only=True)
@output_option()
@execute_option()
def data_refresh(
    l2_instance_path: str | None, config: str,
    output: str | None, execute: bool,
) -> None:
    """Emit the REFRESH MATERIALIZED VIEW SQL (or ``--execute`` to refresh).

    Default: print every ``REFRESH MATERIALIZED VIEW`` (in dependency
    order: leaves → helpers → invariants → rollups) to stdout (or to
    ``-o FILE``). Pass ``--execute`` to run against the demo DB.

    Run after every ETL load that mutates ``<prefix>_transactions``
    or ``<prefix>_daily_balances`` — the L1 invariant matviews +
    Investigation matviews don't auto-refresh.
    """
    from recon_gen.common.l2.schema import refresh_matviews_sql

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    sql = refresh_matviews_sql(
        instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
    )

    if execute:
        connect_and_apply(cfg, sql, label="matview refresh")
    else:
        emit_to_target(sql, output, label="refresh SQL")


@data.command("clean")
@l2_instance_option()
@config_option(required_for_dialect_only=True)
@output_option()
@execute_option()
def data_clean(
    l2_instance_path: str | None, config: str,
    output: str | None, execute: bool,
) -> None:
    """Emit TRUNCATE statements (or ``--execute`` to wipe seeded rows).

    Default: print TRUNCATEs for ``<prefix>_transactions`` and
    ``<prefix>_daily_balances`` to stdout (or to ``-o FILE``). The
    schema stays — only the rows go.

    Pass ``--execute`` to actually run them.

    To wipe schema + rows together, run ``data clean --execute``
    followed by ``schema clean --execute``.
    """
    from recon_gen.common.l2.seed import emit_truncate_sql

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    sql = emit_truncate_sql(
        instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
    )

    if execute:
        connect_and_apply(cfg, sql, label="data TRUNCATE")
    else:
        emit_to_target(sql, output, label="data TRUNCATE")


def _build_fresh_semantic_lock(
    instance: "L2Instance", anchor: "date", *, prefix: str,
    dialect: "_DialectT | None" = None,
) -> str:
    """AZ.1 / CA.6 — build a fresh semantic lock JSON for the given
    (instance, anchor) against an in-process DB (defaults to DuckDB
    post-CA.6; SQLite arm kept until CA.8 deletes it). Mirrors what
    `test_locked_seeds.py` does for byte locks, but compose + detect
    run live against a real conn rather than emitting SQL text.

    The VIOLATION set is dialect-invariant (BZ.4 + CA.0 audit
    confirmed byte-equivalence across all matview engines), so the
    on-disk lock files differ ONLY in the `scenario_fingerprint.dialect`
    field. Per-dialect files exist for symmetry with the runner's
    dialect-tagged tracking; CA.9 may collapse them once SQLite is
    fully gone.
    """
    from recon_gen.common.sql import Dialect as _Dialect  # noqa: PLC0415
    if dialect is None:
        dialect = _Dialect.DUCKDB

    from recon_gen.common.db import (
        execute_script,
    )
    from recon_gen.common.l2.config_table import replace_config
    from recon_gen.common.l2.schema import (
        emit_schema,
        refresh_matviews_sql,
    )
    from recon_gen.common.l2.seed import emit_baseline_seed
    from recon_gen.common.spine import (
        ALL_INVARIANTS,
        ScenarioContext,
        lock_to_json,
        scenario_to_generators,
        semantic_lock,
    )
    from recon_gen.common.sql import Dialect

    # Typing: SQLite + DuckDB connection objects don't share a base
    # class, but every call site in this helper uses methods that BOTH
    # support (execute / cursor / close + replace_config's bind shape).
    # Widen to Any so the body type-checks under both branches.
    from typing import Any as _Any  # noqa: PLC0415
    conn: _Any
    if dialect is Dialect.DUCKDB:
        import duckdb
        conn = duckdb.connect(":memory:")
    else:
        raise ValueError(
            f"_build_fresh_semantic_lock: in-process build supports "
            f"DuckDB only; got {dialect!r}. PG / Oracle locks need the "
            f"deployed-DB path (AZ.1.b extension)."
        )
    try:
        cur = conn.cursor()
        execute_script(
            cur,
            emit_schema(instance, prefix=prefix, dialect=dialect),
            dialect=dialect,
        )
        # Seed config row (matview reads as_of + BS.5 chain children
        # from here). Pre-BS.5 this passed l2_json="{}" because no matview
        # body read chain data through the kv projection — they baked
        # `instance.chains` as SQL literals. BS.5 (2026-05-29) moved that
        # walk into `<prefix>_v_config_chain_children`, so the kv table
        # MUST carry the actual L2 yaml or `_fan_in_disagreement` +
        # `_multi_xor_violation` matviews silently produce zero rows.
        from datetime import datetime as _datetime
        import json as _json

        import yaml as _yaml

        from recon_gen.common.l2.serializer import serialize_l2 as _serialize_l2
        l2_dict = _yaml.safe_load(_serialize_l2(instance))
        # Compact-JSON (no indent) — matches the production
        # emit_config_populate_sql shape; the kv walker parses both.
        replace_config(
            conn, prefix=prefix,
            cfg_json="{}",
            l2_json=_json.dumps(l2_dict, separators=(",", ":")),
            as_of=_datetime(anchor.year, anchor.month, anchor.day, 12, 0, 0),
        )
        # AO.L.gate — emit the 90-day baseline BEFORE plants so the lock
        # detects baseline-derived violations too. Pre-gate, the lock
        # builder skipped the baseline emit and only stamped plants;
        # AO.L stayed latent through Phase AY/AZ because baseline-only
        # firings (e.g., ConcentrationMaster direct postings) never
        # tripped the lock-anchored detector. Now the lock JSON encodes
        # the union of (baseline + plant) violations — a regression to
        # any L1 matview SQL that produces spurious baseline violations
        # trips the gate loudly. (Post-AO.L the baseline produces zero
        # spurious violations; lock files reflect that.)
        baseline_sql = emit_baseline_seed(
            instance, prefix=prefix,
            window_days=90, anchor=anchor, dialect=dialect,
        )
        # DuckDB accepts multi-statement scripts via conn.execute().
        conn.execute(baseline_sql)
        # Compose the production seed via the spine pipeline.
        from recon_gen.cli._helpers import build_default_scenario  # pyright: ignore[reportUnknownVariableType]  # WHY: helper has pending untyped-def waiver
        scenario = build_default_scenario(instance, anchor=anchor)  # pyright: ignore[reportUnknownVariableType]: same helper-untyped waiver propagates to the call
        # BD.3 — semantic-lock detector frame: window matches the
        # 90-day baseline above (emit_baseline_seed window_days=90),
        # ending on `anchor`. Plants validate against this window via
        # SingleDayPlant.at_offset_from_end (per-plant days_ago must
        # fit inside [anchor-90, anchor]).
        from recon_gen.common.as_of_frame import AsOfFrame
        from recon_gen.common.intervals import DateInterval
        lock_frame = AsOfFrame(
            as_of=anchor,
            window=DateInterval.trailing_days_ending_today(anchor, days=91),
        )
        generators = scenario_to_generators(
            scenario, instance, frame=lock_frame, prefix=prefix,
        )
        ctx = ScenarioContext(
            scenario_id=f"semantic-lock-{prefix}",
            prefix=prefix,
            dialect=dialect,
        )
        # Live emit (not dry_run) — the matview detector needs real rows.
        for gen in generators:
            gen.emit(conn, scenario_id=ctx.scenario_id)  # type: ignore[call-arg]: ViolationGenerator Protocol structural narrowing to ClaimedAccountsGenerator's scenario_id kwarg not inferred
        # Refresh matviews so detect() reads up-to-date violations.
        cur2 = conn.cursor()
        execute_script(
            cur2,
            refresh_matviews_sql(instance, prefix=prefix, dialect=dialect),
            dialect=dialect,
        )
        # Each Invariant defaults `prefix="spec_example"`; pass the
        # right prefix when the instance differs.
        invariants = [
            inv_class(prefix=prefix)  # type: ignore[call-arg]: Invariant Protocol doesn't expose prefix in its signature but every concrete subclass takes one
            for inv_class in ALL_INVARIANTS
        ]
        lock = semantic_lock(conn, invariants)
        return lock_to_json(
            lock,
            instance=prefix,
            dialect=dialect,
            canonical_anchor=anchor,
        )
    finally:
        conn.close()


@data.command("semantic-lock")
@l2_instance_option()
@click.option(
    "--check", "check_only", is_flag=True,
    help=(
        "Exit non-zero if the on-disk semantic lock doesn't match a "
        "fresh emit. Use in CI to guard against unreviewed violation "
        "set drift."
    ),
)
def data_semantic_lock(
    l2_instance_path: str | None, check_only: bool,
) -> None:
    """AZ.1 — write or verify the canonical-anchor semantic lock.

    Mirrors `data lock` but gates on the VIOLATION SET (per AZ.0
    design) rather than SQL bytes. The locked file lives at
    ``tests/data/_semantic_locks/<instance>.sqlite.json`` and is
    the record of what `semantic_lock(conn, ALL_INVARIANTS)`
    returns post-emit at canonical anchor (2030-01-01).

    Default: refresh the lock file (overwrites with a fresh emit).
    Pass ``--check`` to verify-only — exit non-zero on drift, with
    a unified diff to stderr showing the first ~50 changed lines.

    Phase AZ scope: SQLite-only initial. The matview SQL differs
    per dialect so PG / Oracle locks need real deployed DBs (the
    deploy_pipeline path); AZ.1.b extension lands those if needed
    before AZ.4's CI gate swap.
    """
    # Resolve the L2 instance. We don't need a full demo cfg here —
    # the lock is per (instance, dialect=sqlite) at canonical anchor.
    from recon_gen.common.l2.loader import load_instance
    if l2_instance_path is None:
        raise click.ClickException(
            "`data semantic-lock` requires --l2 <yaml> to pick the "
            "L2 instance to lock. Did you mean `recon-gen data "
            "semantic-lock --l2 tests/l2/spec_example.yaml`?"
        )
    yaml_path = Path(l2_instance_path)
    if not yaml_path.exists():
        raise click.ClickException(f"L2 yaml not found: {yaml_path}")
    instance = load_instance(yaml_path)
    instance_name = yaml_path.stem

    from recon_gen.common.sql import Dialect as _Dialect  # noqa: PLC0415
    # CA.6 — default lock dialect moved from SQLite to DuckDB. The
    # SQLite arm stays alive until CA.8 deletes Dialect.DUCKDB; both
    # files are kept on disk so the CI gate can be flipped without
    # losing the recoverable point. Violation set is dialect-invariant
    # (BZ.4 + CA.0 audit), only `scenario_fingerprint.dialect` differs.
    fresh = _build_fresh_semantic_lock(
        instance, LOCKED_ANCHOR, prefix=instance_name,
        dialect=_Dialect.DUCKDB,
    )
    locked_path = (
        _SEMANTIC_LOCKS_DIR / f"{instance_name}.duckdb.json"
    )

    if check_only:
        if not locked_path.exists():
            click.echo(
                f"  [error] --check requested but semantic lock is "
                f"missing: {locked_path}\n  Run `data semantic-lock` "
                f"(without --check) to create it.",
                err=True,
            )
            raise SystemExit(1)
        on_disk = locked_path.read_text()
        if fresh == on_disk:
            click.echo(
                f"  [ok] {locked_path.name} matches fresh emit", err=True,
            )
            return
        diff = list(difflib.unified_diff(
            on_disk.splitlines(keepends=True),
            fresh.splitlines(keepends=True),
            fromfile=f"locked/{locked_path.name}",
            tofile=f"fresh/{locked_path.name}",
            n=2,
        ))
        click.echo(
            f"  [error] semantic lock drifted from {locked_path.name}:\n"
            f"  Showing first 50 diff lines (run without --check to "
            f"refresh):\n",
            err=True,
        )
        for line in diff[:50]:
            click.echo(line.rstrip("\n"), err=True)
        if len(diff) > 50:
            click.echo(
                f"  ... ({len(diff) - 50} more diff lines truncated)",
                err=True,
            )
        raise SystemExit(1)

    locked_path.parent.mkdir(parents=True, exist_ok=True)
    locked_path.write_text(fresh)
    click.echo(
        f"  [lock] wrote {locked_path} ({len(fresh):,} bytes)", err=True,
    )


@data.command("etl-example")
@click.option(
    "-o", "--output",
    type=click.Path(), default="demo/etl-examples.sql",
    show_default=True,
    help="Output path for the ETL examples SQL file.",
)
def data_etl_example(output: str) -> None:
    """Emit canonical INSERT-pattern examples for ETL authors.

    Output is exemplary, not executable against the real demo seed —
    every pattern uses fixed sentinel IDs (xxx-EXAMPLE-001) so the
    statements are self-contained. Each block carries a ``-- WHY:``
    header naming the business invariant and a ``-- Consumed by:``
    header naming the dashboard view that reads the resulting rows.

    See docs/handbook/etl.md for the walkthroughs that reference this
    output.
    """
    from recon_gen.common.etl_examples import (
        generate_etl_examples_sql,
    )

    sql = generate_etl_examples_sql()
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sql)
    click.echo(f"Wrote ETL examples to {out}")


@data.command("test")
@click.option(
    "--pytest-args", default="",
    help="Extra args passed verbatim to pytest (e.g. '-k hash_lock').",
)
def data_test(pytest_args: str) -> None:
    """Run the data test suite (pytest + pyright on the seed pipeline)."""
    pytest_argv = (
        [sys.executable, "-m", "pytest", "tests/data/", "-q"]
        + (pytest_args.split() if pytest_args else [])
    )
    pyright_argv = [
        sys.executable, "-m", "pyright",
        "src/recon_gen/common/l2/seed.py",
    ]
    failed: list[str] = []
    click.echo(f"$ {' '.join(pytest_argv)}")
    if subprocess.call(pytest_argv) != 0:
        failed.append("pytest")
    click.echo(f"$ {' '.join(pyright_argv)}")
    if subprocess.call(pyright_argv) != 0:
        failed.append("pyright")
    if failed:
        raise click.ClickException(f"data test failed: {', '.join(failed)}")
    click.echo("data test: OK")
