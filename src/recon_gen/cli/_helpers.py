"""Shared CLI helpers (load config, resolve L2, emit-vs-apply primitives).

The four artifact groups (`schema` / `data` / `json` / `docs`) reuse a
small set of primitives:

  ``resolve_l2_for_demo``  — load YAML + stamp prefix on cfg
  ``build_full_seed_sql``  — densify + broken + boost + emit_full_seed
  ``emit_to_target``       — write SQL to file or stdout
  ``connect_and_apply``    — open demo DB, execute, commit/rollback
  ``write_json``           — write a generated dataset/analysis/dashboard JSON

Every artifact module imports from here so the apply/emit/clean/test
implementations are thin wrappers around the production library
(``common/l2/``, ``common/datasource.py``, ``common/theme.py``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from recon_gen.common.config import load_config

if TYPE_CHECKING:
    from recon_gen.common.config import Config, PlantKind
    from recon_gen.common.l2.primitives import L2Instance
    from recon_gen.common.l2.seed import ScenarioPlant


__all__ = [
    "APPS",
    "build_config_populate_sql",
    "build_full_seed_sql",
    "connect_and_apply",
    "emit_to_target",
    "load_config",
    "prune_stale_files",
    "resolve_l2_for_demo",
    "write_json",
]


APPS: tuple[str, ...] = (
    "investigation",
    "executives",
    "l1-dashboard",
    "l2-flow-tracing",
)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    click.echo(f"  wrote {path}")


def prune_stale_files(directory: Path, *, keep: set[str]) -> None:
    """Delete any ``*.json`` in ``directory`` whose name isn't in ``keep``.

    Prevents orphan files from a prior emit — datasets that were dropped
    or renamed — from being re-deployed on the next ``json apply`` run.
    """
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        if path.name not in keep:
            path.unlink()
            click.echo(f"  pruned stale {path}")


def resolve_l2_for_demo(
    config_path: str, l2_instance_path: str | None,
) -> tuple[Config, L2Instance]:
    """Load config + L2 instance.

    Returns ``(cfg, instance)``. Mirrors the prelude every ``apply``
    operation needs: load YAML, resolve to either the bundled
    spec_example or the integrator's own L2. Z.C — cfg already carries
    ``cfg.deployment_name`` (QS resource-ID prefix) +
    ``cfg.db_table_prefix`` (DB-table prefix); no auto-stamping needed.
    """
    from recon_gen.common.l2 import default_l2_instance

    cfg = load_config(config_path)
    if l2_instance_path is not None:
        from recon_gen.common.l2 import load_instance
        instance = load_instance(Path(l2_instance_path))
    else:
        instance = default_l2_instance()
    return cfg, instance


_DEFAULT_DENSIFY_FACTOR = 5
_DEFAULT_BROKEN_COUNT = 15
_DEFAULT_FANOUT_MULTIPLIER = 5


def build_default_scenario(
    instance: L2Instance,
    *,
    anchor: date | None = None,
    density: float = 1.0,
    plants: tuple[PlantKind, ...] | None = None,
) -> ScenarioPlant:
    """Build the densified+broken+boosted default scenario.

    Shared by ``build_full_seed_sql`` (X.4.g.8 scope:full) and
    ``deploy_pipeline.step_3_generator`` for the X.4.g.9
    scope:exceptions_only mode. ``l1_plus_broad`` mode covers BOTH
    L1 SHOULD-violation plants (drift / overdraft / etc.) AND broad
    L2-shape plants (per-rail RailFiringPlant + per-template
    TransferTemplatePlant) so the L2 Flow Tracing dashboard's Rails /
    Chains / Transfer Templates sheets render non-empty.

    Density (Y.2.gate.c.13.1) is a scalar multiplier on the three
    plant-density knobs (densify factor, broken-rail count, fanout
    amount multiplier). ``density=1.0`` (default) is byte-identical
    to the pre-c.13 behavior — locked SQL files stay valid.
    ``density=2.0`` doubles; ``density=0.5`` halves. Multiplications
    use ``int(...)`` so values stay deterministic.

    ``plants`` (X.4.h.0.a) — optional subset of ``PlantKind`` strings
    selecting which L1 SHOULD-violation kinds to keep. ``None`` or
    empty ⇒ all 6 kinds (today's behavior, byte-identical to the
    locked seeds). Non-empty ⇒ only the named kinds; the others are
    zeroed out at the very end of the pipeline (after densify / broken
    / boost so the in-flight seed numbers don't shift between the
    "all" and "subset" paths). L2-shape fixtures (rail firings,
    template plants, fanout) always pass through.

    Returned scenario is ready to feed either ``emit_full_seed`` (for
    baseline + plants) or ``emit_seed`` (plants only).
    """
    from recon_gen.common.l2.auto_scenario import (
        add_broken_rail_plants,
        boost_inv_fanout_plants,
        default_scenario_for,
        densify_scenario,
        filter_scenario_plants,
    )

    base = default_scenario_for(
        instance, mode="l1_plus_broad", today=anchor,
    ).scenario
    dense = densify_scenario(
        base, factor=int(_DEFAULT_DENSIFY_FACTOR * density),
    )
    broken = add_broken_rail_plants(
        dense, instance, broken_count=int(_DEFAULT_BROKEN_COUNT * density),
    )
    boosted = boost_inv_fanout_plants(
        broken, amount_multiplier=int(_DEFAULT_FANOUT_MULTIPLIER * density),
    )
    return filter_scenario_plants(boosted, plants)


def build_full_seed_sql(
    cfg: Config,
    instance: L2Instance,
    *,
    anchor: date | None = None,
    density: float = 1.0,
    plants: tuple[PlantKind, ...] | None = None,
    base_seed: int | None = None,
) -> str:
    """Compose the demo seed pipeline (90-day baseline + plant overlays).

    ``anchor`` pins the calendar date used by both ``default_scenario_for``
    (plants' ``today``) and ``emit_full_seed`` (baseline window end).
    Default ``None`` defers to the underlying functions, which in turn
    pick today from the wall clock. ``data lock`` passes a canonical
    ``date(2030, 1, 1)`` so the locked SQL is deterministic across
    machines and run dates.

    ``base_seed`` (X.4.h.0.b) — root RNG seed for the baseline emitter.
    ``None`` (default) preserves byte-identity with the locked seeds
    (uses ``_BASELINE_BASE_SEED = 42``). Studio's data-shaping panel
    writes ``cfg.test_generator.seed`` here when the trainer scrubs
    to a different layout.

    See ``build_default_scenario`` for the scenario construction +
    density + plants-filter semantics.

    BC.7 + BC.12 note (revised 2026-05-24): the ``<prefix>_config``
    row populate does NOT live here — it lives in ``schema apply``
    per the three-event lifecycle (schema apply = deploy event = L2
    changes; data apply = transaction data; data refresh = matview
    refresh). ``build_config_populate_sql`` is still exposed below
    for test fixtures and any caller that wants the same shape
    inline.
    """
    from recon_gen.common.l2.seed import emit_full_seed

    final = build_default_scenario(
        instance, anchor=anchor, density=density, plants=plants,
    )
    return emit_full_seed(
        instance, final, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
        anchor=anchor, base_seed=base_seed,
    )


def build_config_populate_sql(
    cfg: Config, instance: L2Instance, *, anchor: date | None = None,
) -> str:
    """BC.7.1+2 — emit the ``<prefix>_config`` row populate SQL.

    Single seam shared by ``schema apply`` (production deploy event
    per the BC.12 three-event lifecycle) and any test fixture that
    wants the same shape. Routes the L2 through
    ``serialize_l2(instance)`` so the BC.8 ``_seconds`` derived
    fields land on rails for the stuck_pending / stuck_unbundled
    matviews. yaml → dict → JSON keeps the embedded-payload
    compact-JSON shape.

    The anchor wallclock seam mirrors ``cli/audit/__init__.py``:
    when the caller passes an explicit ``anchor`` we use it (locked
    SQL paths); otherwise we route through ``AsOfFrame.live().as_of``
    so every wall-clock read funnels through the AQ.3 seam.
    """
    import json
    from datetime import datetime

    import yaml as _yaml
    from recon_gen.common.as_of_frame import AsOfFrame
    from recon_gen.common.l2.config_table import emit_config_populate_sql
    from recon_gen.common.l2.serializer import serialize_l2

    l2_yaml_text = serialize_l2(instance)
    l2_dict = _yaml.safe_load(l2_yaml_text)
    # Compact separators — JSON is embedded as a SQL literal, not a
    # human-diffed file; the matview JSON_TABLE parser is whitespace-
    # tolerant either way. (json-indent typing-smell: compact form is
    # the deliberate choice for the embedded-payload case.)
    l2_json = json.dumps(l2_dict, default=str, separators=(",", ":"))
    # cfg_json carries an empty object today — the matviews only read
    # l2_yaml at present, and serializing the full Config dataclass
    # pulls in non-JSON-safe types (Dialect enum / Decimal / path).
    # If a matview ever needs cfg fields, expand here.
    cfg_json = "{}"
    if anchor is not None:
        as_of_dt = datetime(anchor.year, anchor.month, anchor.day, 12, 0, 0)
    else:
        live = AsOfFrame.live().as_of
        as_of_dt = datetime(live.year, live.month, live.day, 12, 0, 0)
    return emit_config_populate_sql(
        prefix=cfg.db_table_prefix,
        cfg_json=cfg_json,
        l2_json=l2_json,
        as_of=as_of_dt,
        dialect=cfg.dialect,
    )


def emit_to_target(
    sql: str, output: str | None, *, label: str,
) -> None:
    """Write SQL to ``output`` if given, else stdout.

    Default-emit shape: passing nothing prints the script to stdout
    so the integrator can pipe it (``| psql ...``) or read it. Pass
    ``-o FILE`` to write to a file instead. The destructive
    "actually run this against the DB" path is gated separately by
    ``--execute`` — see the apply/clean commands.
    """
    if output is None:
        click.echo(sql, nl=False)
        return
    Path(output).write_text(sql, encoding="utf-8")
    line_count = sql.count("\n")
    size_kb = len(sql.encode("utf-8")) // 1024
    click.echo(
        f"Wrote {label} to {output} ({line_count} lines, {size_kb} KB)",
        err=True,
    )


def connect_and_apply(
    cfg: Config, sql: str, *, label: str,
) -> None:
    """Open the demo DB connection, run ``sql``, commit; rollback on error.

    Cursor lifecycle: psycopg2 + oracledb both support
    ``with conn.cursor() as cur`` (PEP 249's cursor context manager
    protocol — close-on-exit semantics). sqlite3.Cursor doesn't
    implement ``__enter__`` / ``__exit__`` (only sqlite3.Connection
    does), so the SQLite arm acquires the cursor, runs the script,
    and explicitly closes in a finally block. Same observable
    behavior; different syntax driven by the underlying driver's
    PEP 249 conformance level.
    """
    from recon_gen.common.db import connect_demo_db, execute_script
    from recon_gen.common.sql import Dialect

    if not cfg.demo_database_url:
        raise click.ClickException(
            "demo_database_url is required. "
            "Set it in your config YAML or via RECON_GEN_DEMO_DATABASE_URL."
        )

    click.echo(f"Connecting to {cfg.demo_database_url.split('@')[-1]}...")
    try:
        conn = connect_demo_db(cfg)
    except ImportError as e:
        raise click.ClickException(str(e)) from e
    try:
        click.echo(f"  Applying {label}...")
        if cfg.dialect is Dialect.SQLITE:
            # sqlite3.Cursor lacks __enter__ / __exit__ — manage
            # close() explicitly. Same observable behavior as the
            # `with conn.cursor() as cur` block on PG / Oracle.
            cur = conn.cursor()
            try:
                execute_script(cur, sql, dialect=cfg.dialect)
            finally:
                cur.close()
        else:
            with conn.cursor() as cur:
                execute_script(cur, sql, dialect=cfg.dialect)
        conn.commit()
        click.echo(f"  {label.capitalize()} applied.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Common click options shared across artifact subcommands.

def l2_instance_option() -> Callable[..., Any]:
    """``--l2 PATH`` — defaults to bundled spec_example."""
    return click.option(
        "--l2", "l2_instance_path",
        type=click.Path(exists=True, dir_okay=False), default=None,
        help="Path to L2 instance YAML. Default: bundled spec_example.",
    )


def config_option(*, required_for_dialect_only: bool = False) -> Callable[..., Any]:
    """``--config / -c PATH`` — config.yaml.

    Pass ``required_for_dialect_only=True`` for emit-only commands that
    only need the dialect setting (no DB connection).
    """
    help_text = (
        "Path to configuration file (used for the dialect setting only)."
        if required_for_dialect_only
        else "Path to configuration file (DB connection + dialect)."
    )
    return click.option(
        "--config", "-c",
        type=click.Path(exists=True), default="config.yaml",
        help=help_text,
    )


def output_option(*, default: str | None = None) -> Callable[..., Any]:
    """``-o FILE`` — output redirect.

    For schema/data: omit ``-o`` to emit to stdout. Pass ``-o FILE`` to
    write to a file instead. ``--execute`` (separate decorator) is
    what actually runs the script against the DB.

    For json/docs: pass ``default="out"`` (or ``"site"``) so the
    emit always goes to a directory; the directory IS the artifact.
    """
    if default is None:
        return click.option(
            "-o", "--output", "output",
            type=click.Path(), default=None,
            help="Write the script to FILE instead of stdout.",
        )
    return click.option(
        "-o", "--output", "output",
        type=click.Path(), default=default,
        help=f"Output directory (default: {default}/).",
    )


def execute_option() -> Callable[..., Any]:
    """``--execute`` — actually do the destructive thing.

    Without this flag, apply/clean commands emit the script they would
    have run. With it, they connect to the demo DB / AWS and execute.
    Forces the integrator to opt in to side effects, which means the
    safe default (just emit) can never accidentally drop a table or
    redeploy a dashboard.
    """
    return click.option(
        "--execute", "execute", is_flag=True, default=False,
        help=(
            "Actually run the script (connect to the DB / AWS and "
            "execute). Without this flag, the script is emitted to "
            "stdout (or to -o FILE) without any side effects."
        ),
    )
