"""``recon-gen schema`` — per-prefix DDL for an L2 instance.

Three operations:

  apply  — emit the schema DDL (default), or ``--execute`` against the demo DB.
  clean  — emit the matching DROP statements (default), or ``--execute``.
  test   — pytest + pyright the schema-emitting library code.

The default for apply/clean is EMIT ONLY — print to stdout (or
``-o FILE``) without touching the DB. Pass ``--execute`` to actually
run the script. Safe path is the default; nothing drops a table by
accident.
"""

from __future__ import annotations

import subprocess
import sys

import click

from recon_gen.cli._helpers import (
    config_option,
    connect_and_apply,
    emit_to_target,
    execute_option,
    l2_instance_option,
    output_option,
    resolve_l2_for_demo,
)


@click.group()
def schema() -> None:
    """Per-prefix schema DDL: tables, views, materialized views."""


@schema.command("apply")
@l2_instance_option()
@config_option(required_for_dialect_only=True)
@output_option()
@execute_option()
def schema_apply(
    l2_instance_path: str | None, config: str,
    output: str | None, execute: bool,
) -> None:
    """Emit the schema DDL (or ``--execute`` to apply against the demo DB).

    Default behavior: print every CREATE statement for the L2 instance's
    per-prefix tables, views and materialized views to stdout (or to
    ``-o FILE``). Pipe it to your DB tool: ``recon-gen schema
    apply | psql ...``.

    Pass ``--execute`` to connect to the demo DB named in the config
    and actually run every CREATE.
    """
    from recon_gen.cli._helpers import build_config_populate_sql
    from recon_gen.common.l2.schema import emit_schema

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    schema_sql = emit_schema(
        instance, prefix=cfg.db.table_prefix, dialect=cfg.db.dialect,
    )
    # BC.7 + BC.12: schema apply IS the L2-deploy event. After DDL,
    # populate <prefix>_config_kv from the operator-passed L2 yaml.
    # Typed projection views (BC.12.6) project the kv into matview-
    # friendly shapes; matviews JOIN those views (not the kv directly,
    # not JSON_TABLE of CLOB — that's the ORA-32368 trap on Oracle 19c).
    # Lifecycle: deploy event re-populates kv from --l2; daily
    # `data refresh --execute` only touches matviews, not the kv.
    populate_sql = build_config_populate_sql(cfg, instance)
    full_sql = schema_sql + "\n" + populate_sql

    if execute:
        connect_and_apply(cfg, full_sql, label="schema DDL + config populate")
    else:
        emit_to_target(full_sql, output, label="schema DDL + config populate")


@schema.command("clean")
@l2_instance_option()
@config_option(required_for_dialect_only=True)
@output_option()
@execute_option()
def schema_clean(
    l2_instance_path: str | None, config: str,
    output: str | None, execute: bool,
) -> None:
    """Emit DROP statements (or ``--execute`` to drop against the demo DB).

    Default: print every DROP for the L2 instance's per-prefix matviews
    / views / tables (in dependency order) to stdout (or ``-o FILE``).

    Pass ``--execute`` to connect and actually drop them.

    Schema-only cleanup. To wipe seeded rows without dropping the
    schema, run ``data clean`` instead.
    """
    from recon_gen.common.l2.schema import emit_schema_drop_sql

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    sql = emit_schema_drop_sql(
        instance, prefix=cfg.db.table_prefix, dialect=cfg.db.dialect,
    )

    if execute:
        connect_and_apply(cfg, sql, label="schema DROP")
    else:
        emit_to_target(sql, output, label="schema DROP")


@schema.command("migrate-mark")
@l2_instance_option()
@config_option()
@click.option(
    "--source", "source_value", type=str, default="training",
    show_default=True,
    help=(
        "Value to write into metadata.source on every unstamped row. "
        "Default 'training' assumes pre-CZ rows came from the seed/"
        "training path (the common case — production-integrator etl_hook "
        "is brand-new in BS.4 and ships alongside CZ). Set "
        "--source=real on the rare DB that loaded real ETL data before "
        "CZ landed so those rows survive standalone-mode resets."
    ),
)
@execute_option()
def schema_migrate_mark(
    l2_instance_path: str | None, config: str,
    source_value: str, execute: bool,
) -> None:
    """CZ.6 — stamp ``metadata.source`` on every pre-CZ row.

    Phase CZ's standalone-mode cleanup gate (`cfg.app2.etl_hook is None` ⇒
    DELETE-only-synthetic on Trainer reset / Studio Deploy-changes)
    keys on ``JSON_VALUE(metadata, '$.source') = 'training'`` as the
    synthetic-row predicate. CZ.2 stamps new writes; CZ.6 fills in pre-
    CZ rows already sitting in the DB at upgrade time.

    Default-emit shape: prints what would be done (row counts per base
    table). Pass ``--execute`` to actually run the UPDATE and commit.

    Same auto-mark logic fires from ``data apply --execute``'s pre-
    flight check — this verb is the explicit form, primarily used when
    the operator wants ``--source=real`` (real ETL data loaded before
    CZ landed) or wants to re-run after seeding more pre-CZ rows.
    """
    from recon_gen.common.l2.migrate_mark import (
        count_unstamped_rows, stamp_unstamped_rows,
    )

    cfg, _instance = resolve_l2_for_demo(config, l2_instance_path)
    if not cfg.db.url:
        raise click.ClickException(
            "demo_database_url is required. "
            "Set it in your config YAML or via RECON_GEN_DEMO_DATABASE_URL."
        )

    from recon_gen.common.db import connect_demo_db

    click.echo(
        f"Connecting to {cfg.db.url.split('@')[-1]}...",
        err=True,
    )
    try:
        conn = connect_demo_db(cfg)
    except ImportError as e:
        raise click.ClickException(str(e)) from e
    try:
        tx_unstamped, bal_unstamped = count_unstamped_rows(
            conn, prefix=cfg.db.table_prefix, dialect=cfg.db.dialect,
        )
        click.echo(
            f"  found {tx_unstamped:,} unstamped {cfg.db.table_prefix}"
            f"_transactions rows, {bal_unstamped:,} unstamped "
            f"{cfg.db.table_prefix}_daily_balances rows",
            err=True,
        )
        if tx_unstamped == 0 and bal_unstamped == 0:
            click.echo(
                "  nothing to do — every row already carries "
                "metadata.source. Idempotent no-op.",
                err=True,
            )
            return
        if not execute:
            click.echo(
                f"  [dry-run] would stamp metadata.source="
                f"{source_value!r} on the rows above. "
                f"Pass --execute to actually run.",
                err=True,
            )
            return
        tx_updated, bal_updated = stamp_unstamped_rows(
            conn,
            prefix=cfg.db.table_prefix,
            dialect=cfg.db.dialect,
            source=source_value,
        )
        conn.commit()
        click.echo(
            f"  stamped metadata.source={source_value!r} on "
            f"{tx_updated:,} transactions + {bal_updated:,} "
            f"daily_balances rows.",
            err=True,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@schema.command("test")
@click.option(
    "--pytest-args", default="",
    help="Extra args passed verbatim to pytest (e.g. '-k drift -v').",
)
def schema_test(pytest_args: str) -> None:
    """Run the schema test suite (pytest + pyright)."""
    pytest_argv = (
        [sys.executable, "-m", "pytest", "tests/schema/", "-q"]
        + (pytest_args.split() if pytest_args else [])
    )
    pyright_argv = [
        sys.executable, "-m", "pyright",
        "src/recon_gen/common/l2/schema.py",
    ]
    failed: list[str] = []
    click.echo(f"$ {' '.join(pytest_argv)}")
    if subprocess.call(pytest_argv) != 0:
        failed.append("pytest")
    click.echo(f"$ {' '.join(pyright_argv)}")
    if subprocess.call(pyright_argv) != 0:
        failed.append("pyright")
    if failed:
        raise click.ClickException(f"schema test failed: {', '.join(failed)}")
    click.echo("schema test: OK")
