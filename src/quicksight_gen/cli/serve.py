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


# The four real apps App2 serves (everything except the DB-free
# ``smoke`` fixture). ``serve app2 apply`` with no ``--app`` builds all
# four into one server — same "no-arg = all" shape as ``json apply``;
# ``--app <one>`` narrows to a single app for iteration.
_REAL_APPS: tuple[str, ...] = (
    "l1_dashboard", "l2_flow_tracing", "investigation", "executives",
)
_APP_TITLES: dict[str, str] = {
    "l1_dashboard": "L1 Dashboard",
    "l2_flow_tracing": "L2 Flow Tracing",
    "investigation": "Investigation",
    "executives": "Executives",
    "smoke": "Smoke",
}


def _build_real_app(app_name: str, cfg, instance):  # type: ignore[no-untyped-def]: cfg/l2 untyped pending CLI-wide sweep
    """Register ``app_name``'s datasets + build its tree.

    Returns ``(tree_app, first_sheet)``. ``build_*_datasets(...)``
    populates the shared SQL registry (per-app-prefixed IDs, so the
    four apps don't collide) which ``make_tree_db_fetcher`` reads at
    construction time — a missing entry fails loudly here, not inside
    a hot HTMX swap.
    """
    if app_name == "executives":
        from quicksight_gen.apps.executives.app import (  # noqa: PLC0415
            build_executives_app,
        )
        from quicksight_gen.apps.executives.datasets import (  # noqa: PLC0415
            build_all_datasets as _build_datasets,
        )
        # Executives' build_all_datasets doesn't take l2_instance; the
        # per-app builder does, via the kwarg below.
        _build_datasets(cfg)
        tree_app = build_executives_app(cfg, l2_instance=instance)
    elif app_name == "investigation":
        from quicksight_gen.apps.investigation.app import (  # noqa: PLC0415
            build_investigation_app,
        )
        from quicksight_gen.apps.investigation.datasets import (  # noqa: PLC0415
            build_all_datasets as _build_datasets,
        )
        _build_datasets(cfg, instance)
        tree_app = build_investigation_app(cfg, l2_instance=instance)
    elif app_name == "l2_flow_tracing":
        from quicksight_gen.apps.l2_flow_tracing.app import (  # noqa: PLC0415
            build_l2_flow_tracing_app,
        )
        from quicksight_gen.apps.l2_flow_tracing.datasets import (  # noqa: PLC0415
            build_all_l2_flow_tracing_datasets as _build_datasets,
        )
        _build_datasets(cfg, instance)
        tree_app = build_l2_flow_tracing_app(cfg, l2_instance=instance)
    elif app_name == "l1_dashboard":
        from quicksight_gen.apps.l1_dashboard.app import (  # noqa: PLC0415
            build_l1_dashboard_app,
        )
        from quicksight_gen.apps.l1_dashboard.datasets import (  # noqa: PLC0415
            build_all_l1_dashboard_datasets as _build_datasets,
        )
        _build_datasets(cfg, instance)
        tree_app = build_l1_dashboard_app(cfg, l2_instance=instance)
    else:  # pragma: no cover — click.Choice gates this
        raise click.UsageError(f"Unknown App2 app: {app_name!r}")
    if tree_app.analysis is None or not tree_app.analysis.sheets:
        raise click.UsageError(
            f"{app_name} app has no analysis sheets — bug in builder."
        )
    return tree_app, tree_app.analysis.sheets[0]


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
        "Which App2 surface(s) to serve. ``all`` (default) builds the "
        "four real apps into one server — `/dashboards` lists them and "
        "you switch between them in-process, same as `json apply` with "
        "no `--app`. Pass a single app name to narrow to one (faster "
        "startup when iterating). ``smoke`` is the DB-free spike fixture "
        "(the only one that works without a configured database / and "
        "the only one `--stub` applies to)."
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
        "when mkdocs isn't installed (`pip install quicksight-gen[docs]`). "
        "`--no-docs` skips the build for a faster startup. The standalone "
        "`docs apply` / `docs serve` / `docs export` CLI is unaffected "
        "either way."
    ),
)
def app2_apply(  # type: ignore[no-untyped-def]: Click decorator strips the function-decorator return type
    config,
    l2_instance_path,
    host: str,
    port: int,
    dev_log: bool,
    stub: bool,
    app_name: str,
    embed_docs: bool,
) -> None:
    """Start the App2 HTMX/d3 dashboard server.

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
    skips it; the standalone ``docs apply`` / ``serve`` / ``export`` CLI
    is unaffected (X.2.i — additive).
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

    if stub and app_name != "smoke":
        raise click.UsageError(
            f"--stub only applies to --app smoke (the DB-free fixture); "
            f"--app {app_name} needs a real database."
        )

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    theme = resolve_l2_theme(instance)
    if theme is not None:
        click.echo(f"theme: L2-driven ({theme.theme_name})")

    # X.2.i — build the mkdocs handbook into a tempdir (against the same
    # L2) and embed it at /docs. Best-effort: needs the [docs] extra; a
    # [serve]-only install (no mkdocs) silently skips, never a hard fail.
    # The tempdir lives for the server's lifetime — cleaned up in the
    # finally around asyncio.run below.
    import importlib.util  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    docs_dir: Path | None = None
    docs_tmp: tempfile.TemporaryDirectory[str] | None = None
    if embed_docs and importlib.util.find_spec("mkdocs") is not None:
        from quicksight_gen.cli.docs import build_docs_site  # noqa: PLC0415

        docs_tmp = tempfile.TemporaryDirectory(prefix="qs-app2-docs-")
        # strict=False — a stray mkdocs warning shouldn't take the server
        # down; `docs apply --strict` is the place that gates on those.
        rc = build_docs_site(l2_instance_path, docs_tmp.name, strict=False)
        if rc == 0 and (Path(docs_tmp.name) / "index.html").is_file():
            docs_dir = Path(docs_tmp.name)
            click.echo("docs: embedded handbook at /docs/")
        else:
            click.echo(
                "docs: mkdocs build failed — serving without /docs "
                "(run `quicksight-gen docs apply` to triage)"
            )
            docs_tmp.cleanup()
            docs_tmp = None

    # Build the real apps' trees here (sync) — ``build_*_datasets``
    # populates the shared SQL registry (per-app-prefixed IDs → no
    # collisions) that ``make_tree_db_fetcher`` reads in ``_serve``, so
    # a missing entry fails loudly now, not inside a hot HTMX swap.
    if app_name == "smoke":
        smoke_tree, smoke_sheet = build_smoke_app(cfg)
        real_apps: list[tuple[str, object, object]] = []
    else:
        names = list(_REAL_APPS) if app_name == "all" else [app_name]
        real_apps = [
            (name, *_build_real_app(name, cfg, instance)) for name in names
        ]

    async def _serve() -> None:
        # X.2.g.2.d — keep the DB pool + uvicorn in ONE event loop.
        # ``asyncio.run(make_connection_pool(...))`` then ``uvicorn.run()``
        # opens the pool in loop A and starts loop B; the pool's filler
        # task is bound to A and dies when B uses it. Building the pool
        # inside the loop that runs ``Server.serve()`` keeps the filler
        # alive. One shared pool serves every app (same database).
        pool = None
        dashboards: dict[str, ServedDashboard] = {}
        if app_name == "smoke":
            if stub:
                fetcher = stub_money_trail_fetcher
                click.echo("data: stub fetcher (deterministic)")
            else:
                fetcher = make_db_fetcher(cfg, instance)
                click.echo(
                    f"data: DB-backed ({cfg.dialect.value}) → "
                    f"{cfg.l2_instance_prefix or instance.instance}"
                    f"_inv_money_trail_edges"
                )
            dashboards["smoke"] = ServedDashboard(
                tree_app=smoke_tree, sheet=smoke_sheet,
                title=_APP_TITLES["smoke"], data_fetcher=fetcher,
                theme=theme, filter_specs=SMOKE_FILTER_SPECS,
            )
        else:
            from quicksight_gen.common.db import (  # noqa: PLC0415
                make_connection_pool,
            )
            from quicksight_gen.common.html._tree_fetcher import (  # noqa: PLC0415
                make_tree_db_fetcher,
            )
            pool = await make_connection_pool(
                cfg, max_size=cfg.app2_db_pool_size,
            )
            for name, tree_app, sheet in real_apps:
                dashboards[name] = ServedDashboard(
                    tree_app=tree_app, sheet=sheet,
                    title=_APP_TITLES.get(name, name.title()),
                    data_fetcher=make_tree_db_fetcher(tree_app, cfg, pool=pool),
                    theme=theme, filter_specs=(),
                )
            click.echo(
                f"data: DB-backed ({cfg.dialect.value}) → {len(real_apps)} "
                f"app(s) [{', '.join(n for n, _, _ in real_apps)}] "
                f"(prefix={cfg.l2_instance_prefix or instance.instance})"
            )
        try:
            asgi_app = make_app(
                dashboards=dashboards, dev_log=dev_log, docs_dir=docs_dir,
            )
            click.echo(f"App2 server: http://{host}:{port}/")
            if len(dashboards) > 1:
                click.echo(
                    f"  → http://{host}:{port}/dashboards lists "
                    f"{len(dashboards)} dashboards"
                )
            if docs_dir is not None:
                click.echo(f"  → http://{host}:{port}/docs/ — embedded handbook")
            if dev_log:
                click.echo("dev-log: on (events forwarded to stderr)")
            config = uvicorn.Config(
                asgi_app, host=host, port=port, log_level="info",
            )
            server = uvicorn.Server(config)
            await server.serve()
        finally:
            if pool is not None:
                await pool.close()

    import asyncio  # noqa: PLC0415

    try:
        asyncio.run(_serve())
    finally:
        if docs_tmp is not None:
            docs_tmp.cleanup()
