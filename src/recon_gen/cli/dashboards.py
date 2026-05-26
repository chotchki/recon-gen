"""``recon-gen dashboards`` — self-hosted HTMX/d3 dashboard server.

Replaces the X.2-era ``serve app2 apply`` (deleted X.4.a.5; clean cut,
no deprecation alias since the operator was the only one who ever saw
the old verb). Same shape, same options, same default behavior — read
the configured DB, mount the four real apps, optionally embed the
mkdocs handbook at ``/docs``.

Severability contract (``SPEC_studio.md``): ``dashboards`` runs cleanly
without Studio. It calls ``run_html_server(... studio_routes_factory=
None)`` so no L2 cache is built, no Studio routes are mounted, and the
``GET / → /dashboards`` redirect stays in place. ``cli.studio`` is the
bigger surface that adds the Studio mount on top.
"""

from __future__ import annotations

from pathlib import Path

import click

from recon_gen.cli._helpers import (
    config_option,
    l2_instance_option,
    resolve_l2_for_demo,
)
from recon_gen.cli._html_serve import run_html_server


@click.command("dashboards")
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
    "--stub/--no-stub",
    default=False,
    show_default=True,
    help=(
        "Use the deterministic stub fetcher instead of querying the "
        "configured DB. Useful for iterating on the JS / page shell "
        "without a populated database."
    ),
)
@click.option(
    "--app",
    "app_name",
    type=click.Choice(
        ["all", "smoke", "l1_dashboard", "l2_flow_tracing",
         "investigation", "executives"],
    ),
    default="all",
    show_default=True,
    help=(
        "Which dashboard surface(s) to serve. ``all`` (default) builds "
        "the four real apps into one server — `/dashboards` lists them "
        "and you switch between them in-process, same as `json apply` "
        "with no `--app`. Pass a single app name to narrow to one "
        "(faster startup when iterating). ``smoke`` is the DB-free "
        "spike fixture (the only one that works without a configured "
        "database / and the only one `--stub` applies to)."
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
        "when mkdocs isn't installed (`pip install recon-gen[docs]`). "
        "`--no-docs` skips the build for a faster startup. The standalone "
        "`docs apply` / `docs serve` / `docs export` CLI is unaffected "
        "either way."
    ),
)
def dashboards(
    config: str,
    l2_instance_path: str | None,
    host: str,
    port: int,
    dev_log: bool,
    stub: bool,
    app_name: str,
    embed_docs: bool,
) -> None:
    """Start the self-hosted HTMX/d3 dashboard server.

    With no ``--app`` (the default ``all``), builds the four real apps
    (``l1_dashboard`` / ``l2_flow_tracing`` / ``investigation`` /
    ``executives``) into one server — ``/dashboards`` lists them and you
    switch between them in-process; same "no-arg = all" shape as ``json
    apply``. ``--app <one>`` narrows to a single app (faster startup
    when iterating). ``--app smoke`` is the DB-free spike fixture (the
    only one ``--stub`` applies to). Config + L2 instance are loaded the
    same way the json / data / audit groups do; visual data comes from
    the configured DB; one shared connection pool serves every app. The
    mkdocs handbook (same ``--l2``) is built on startup and embedded at
    ``/docs`` when the ``[docs]`` extra is installed — ``--no-docs``
    skips it.

    Studio (``recon-gen studio``) mounts everything ``dashboards``
    mounts plus the editor + diagram + data-shaping surface; reach for
    that command when the integrator / trainer / ETL-engineer loops
    matter. ``dashboards`` is the lean read-only mount.
    """
    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    l2_path = Path(l2_instance_path) if l2_instance_path is not None else None
    run_html_server(
        cfg=cfg,
        instance=instance,
        l2_instance_path=l2_path,
        host=host, port=port, dev_log=dev_log,
        app_name=app_name, stub=stub, embed_docs=embed_docs,
        studio_routes_factory=None,  # Dashboards-only: no Studio mount.
    )
