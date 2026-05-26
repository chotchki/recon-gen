"""``recon-gen schema`` — per-prefix DDL for an L2 instance.

Three operations:

  apply  — emit the schema DDL (default), or ``--execute`` against the demo DB.
  clean  — emit the matching DROP statements (default), or ``--execute``.
  test   — pytest + pyright the schema-emitting library code.

The default for apply/clean is **emit only** — print to stdout (or
``-o FILE``) without touching the DB. Pass ``--execute`` to actually
run the script. This makes the safe path the default; nothing
accidentally drops a table.
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
    per-prefix tables, views, and materialized views to stdout (or to
    ``-o FILE``). Pipe it to your DB tool: ``recon-gen schema
    apply | psql ...``.

    Pass ``--execute`` to connect to the demo DB named in the config
    and actually run every CREATE.
    """
    from recon_gen.cli._helpers import build_config_populate_sql
    from recon_gen.common.l2.schema import emit_schema

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    schema_sql = emit_schema(
        instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
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
        instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
    )

    if execute:
        connect_and_apply(cfg, sql, label="schema DROP")
    else:
        emit_to_target(sql, output, label="schema DROP")


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
