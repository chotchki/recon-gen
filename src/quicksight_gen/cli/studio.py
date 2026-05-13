"""``quicksight-gen studio`` — implementation tools (X.4 SPEC).

Mounts everything ``dashboards`` mounts (the four real apps + ``/docs``
when available) PLUS the Studio routes: the unified diagram (X.4.c),
the editor (``/l2_shape/...`` X.4.e), the data-shaping panel
(``/data/...`` X.4.h), and the orchestration endpoint (``POST /deploy``
X.4.g). All under one Starlette process; one in-memory ``L2InstanceCache``
backs every Studio read/write.

X.4.a.4 ships only the landing placeholder — the fuller surface lands
across X.4.c/d/e/g/h. The CLI shape (``studio --port ... -c ... --l2 ...``)
locks here so subsequent X.4 work doesn't reshuffle it.

Per the SPEC's CLI surface section: studio always requires ``--l2``
(the editor edits the L2 YAML; smoke / stub make no sense in Studio).
The dashboards-only options (``--app smoke``, ``--stub``) are deliberately
omitted from this command — reach for ``quicksight-gen dashboards`` for
those iteration loops.
"""

from __future__ import annotations

import click

from quicksight_gen.cli._helpers import (
    config_option,
    l2_instance_option,
    resolve_l2_for_demo,
)
from quicksight_gen.cli._html_serve import run_html_server
from quicksight_gen.common.html._studio_routes import make_studio_routes


@click.command("studio")
@config_option(required_for_dialect_only=True)
@l2_instance_option()
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind address. Use 0.0.0.0 to expose on the network.",
)
@click.option(
    "--port",
    type=int,
    default=8765,
    show_default=True,
    help="TCP port to listen on.",
)
@click.option(
    "--dev-log/--no-dev-log",
    default=False,
    show_default=True,
    help=(
        "Forward HTMX + d3 click events to stderr for live debugging. "
        "Default off so production deploys stay silent."
    ),
)
@click.option(
    "--app",
    "app_name",
    type=click.Choice(
        ["all", "l1_dashboard", "l2_flow_tracing",
         "investigation", "executives"],
    ),
    default="all",
    show_default=True,
    help=(
        "Which dashboard surface(s) Studio mounts under ``/dashboards``. "
        "``all`` (default) mounts the four real apps; pass a single "
        "name to narrow (faster startup when iterating on a single "
        "dashboard alongside Studio). Studio routes are unaffected by "
        "this knob."
    ),
)
@click.option(
    "--docs/--no-docs",
    "embed_docs",
    default=True,
    show_default=True,
    help=(
        "Build the mkdocs handbook (against the same `--l2`) on startup "
        "and serve it at `/docs` (X.2.i). Best-effort: silently skipped "
        "when mkdocs isn't installed (`pip install quicksight-gen[docs]`)."
    ),
)
def studio(  # type: ignore[no-untyped-def]: Click decorator strips the function-decorator return type
    config,
    l2_instance_path,
    host: str,
    port: int,
    dev_log: bool,
    app_name: str,
    embed_docs: bool,
) -> None:
    """Start Studio — the implementation-tools surface for the integrator,
    trainer, and ETL engineer.

    Studio is a Starlette process that mounts the Dashboards routes
    plus a Studio-side editor, unified diagram, data-shaping panel,
    and Deploy-changes orchestration. One in-memory cache of the L2
    YAML backs every Studio request; the YAML on disk stays the
    source of truth (every save is an atomic write through Studio).

    X.4.a.4 ships only the landing placeholder; the unified diagram
    (X.4.c), editor (X.4.e), and Deploy pipeline (X.4.g) land in
    sub-phases. The CLI surface is stable from this commit forward.
    """
    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    run_html_server(
        cfg=cfg,
        instance=instance,
        l2_instance_path=l2_instance_path,
        host=host, port=port, dev_log=dev_log,
        app_name=app_name,
        stub=False,            # Studio always reads the real DB.
        embed_docs=embed_docs,
        studio_routes_factory=make_studio_routes,
    )
