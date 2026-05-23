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
from pathlib import Path

import click

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


# X.1.k — fixed canonical anchor for locked-SQL determinism. The plants'
# `today` and the baseline window's anchor both feed off this so the
# emit is byte-stable regardless of when `data lock` runs. AQ.3 funnel
# (2026-05-23): the canonical value now lives on `AsOfFrame.LOCKED_ANCHOR`
# (`common/as_of_frame.py`) — this name is kept as the locked-SQL
# emitter's call site so its callers don't change.
_CANONICAL_LOCK_ANCHOR = LOCKED_ANCHOR

# X.1.k — locked SQL files live under tests/data/ (one per
# (instance, dialect)). Discovered + asserted by
# ``tests/data/test_locked_seeds.py``.
_LOCKED_SEEDS_DIR = (
    Path(__file__).resolve().parents[3]
    / "tests" / "data" / "_locked_seeds"
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
        connect_and_apply(cfg, sql, label="seed data")
    else:
        emit_to_target(sql, output, label="seed SQL")


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


@data.command("lock")
@l2_instance_option()
@config_option(required_for_dialect_only=True)
@click.option(
    "--check", "check_only", is_flag=True,
    help=(
        "Exit non-zero if the locked SQL file doesn't match a fresh "
        "emit. Use in CI to guard against unreviewed seed drift."
    ),
)
def data_lock(
    l2_instance_path: str | None, config: str, check_only: bool,
) -> None:
    """Write or verify the canonical-anchor seed SQL.

    The locked file lives at
    ``tests/data/_locked_seeds/<instance>.<dialect>.sql`` and IS the
    record of what `data apply` would emit at canonical anchor
    (2030-01-01) for this (L2 instance, dialect) pair. The CLI keys
    off ``-c config.yaml`` (dialect derived from ``demo_database_url``);
    ``--l2`` picks the L2 to lock.

    Default: refresh the locked file (overwrites the on-disk content
    with a fresh emit). Pass ``--check`` to verify-only — exit non-zero
    on drift, with a unified diff to stderr showing the first ~50 lines
    that changed.

    Run once per (postgres config, oracle config) to cover both
    dialects. Run after any seed-shape-changing commit (new plant kind,
    plant emitter change, baseline generator tweak) to refresh both
    locks before pushing.
    """
    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    fresh = build_full_seed_sql(cfg, instance, anchor=_CANONICAL_LOCK_ANCHOR)

    locked_path = (
        _LOCKED_SEEDS_DIR / f"{cfg.db_table_prefix}.{cfg.dialect.value}.sql"
    )

    if check_only:
        if not locked_path.exists():
            click.echo(
                f"  [error] --check requested but lock file is missing: "
                f"{locked_path}\n  Run `data lock` (without --check) to "
                f"create it.",
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
            f"  [error] seed drifted from {locked_path.name}:\n"
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
    failed = []
    click.echo(f"$ {' '.join(pytest_argv)}")
    if subprocess.call(pytest_argv) != 0:
        failed.append("pytest")
    click.echo(f"$ {' '.join(pyright_argv)}")
    if subprocess.call(pyright_argv) != 0:
        failed.append("pyright")
    if failed:
        raise click.ClickException(f"data test failed: {', '.join(failed)}")
    click.echo("data test: OK")
