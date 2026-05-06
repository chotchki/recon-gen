"""``quicksight-gen serve`` — self-hosted dashboards (App 2).

The serve group ships HTMX/d3 dashboards as a third dialect alongside
QuickSight JSON (``json``) and the audit PDF (``audit``). Each
sub-app hangs off a sub-group:

  app2 apply — start the App2 (HTMX dashboard) server.

X.4 will add ``app1`` (the YAML editor) under the same group.

App2 is a *server*, not a static artifact, so there is no ``--execute``
flag here — starting the server IS the operation, mirroring the
``docs serve`` shape (the ``apply`` verb is kept for surface symmetry
with the other artifact groups).

By default ``apply`` wires the real DB-backed fetcher (X.2.a.4) which
hits the configured Postgres / Oracle / SQLite. Pass ``--stub`` to
swap in the deterministic stub from ``_smoke_app.py`` — useful when
iterating on the JS / page shell without a populated database.
"""

from __future__ import annotations

import click

from quicksight_gen.cli._helpers import (
    config_option,
    l2_instance_option,
    resolve_l2_for_demo,
)


@click.group()
def serve() -> None:
    """Self-hosted dashboard servers (App2 = HTMX/d3 renderer)."""


@serve.group("app2")
def app2() -> None:
    """App2 — self-hosted HTMX/d3 dashboards."""


@app2.command("apply")
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
def app2_apply(  # type: ignore[no-untyped-def]
    config,
    l2_instance_path,
    host: str,
    port: int,
    dev_log: bool,
    stub: bool,
) -> None:
    """Start the App2 HTMX/d3 dashboard server.

    Loads the config + L2 instance the same way the json / data /
    audit groups do, builds the App2 tree, and runs uvicorn. The
    visual data comes from the configured DB by default; pass
    ``--stub`` to swap in the deterministic stub fetcher (useful
    when there's no populated database, or when iterating on the
    JS / page shell only).
    """
    # Imported lazily so the CLI module imports cheaply (uvicorn
    # pulls a lot of asyncio + httptools bootstrap into memory) and
    # so a `--help` invocation works without `serve` extras
    # installed.
    import uvicorn  # noqa: PLC0415

    from quicksight_gen.common.html._db_fetcher import (  # noqa: PLC0415
        make_db_fetcher,
    )
    from quicksight_gen.common.html._smoke_app import (  # noqa: PLC0415
        SMOKE_FILTER_SPECS,
        build_smoke_app,
        stub_money_trail_fetcher,
    )
    from quicksight_gen.common.html.server import (  # noqa: PLC0415
        ServedDashboard,
        make_app,
    )
    from quicksight_gen.common.theme import (  # noqa: PLC0415
        resolve_l2_theme,
    )

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    tree_app, sheet = build_smoke_app(cfg)
    if stub:
        fetcher = stub_money_trail_fetcher
        click.echo("data: stub fetcher (deterministic)")
    else:
        fetcher = make_db_fetcher(cfg, instance)
        click.echo(
            f"data: DB-backed ({cfg.dialect.value}) → "
            f"{cfg.l2_instance_prefix or instance.instance}_inv_money_trail_edges"
        )
    theme = resolve_l2_theme(instance)
    if theme is not None:
        click.echo(f"theme: L2-driven ({theme.theme_name})")
    asgi_app = make_app(
        dashboards={
            "smoke": ServedDashboard(
                tree_app=tree_app,
                sheet=sheet,
                title="Smoke",
                data_fetcher=fetcher,
                theme=theme,
                filter_specs=SMOKE_FILTER_SPECS,
            ),
        },
        dev_log=dev_log,
    )
    click.echo(f"App2 server: http://{host}:{port}/")
    if dev_log:
        click.echo("dev-log: on (events forwarded to stderr)")
    uvicorn.run(asgi_app, host=host, port=port, log_level="info")
