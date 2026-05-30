"""Shared internals for ``recon-gen dashboards`` + ``... studio``.

Both Click commands ride the same Starlette app (descendant of
``common/html/server.py``) and need the same DB-fetcher / dashboard-tree
/ pool / uvicorn dance. This module owns that body; the two CLI files
are thin Click wrappers calling ``run_html_server(...)``.

Per the SPEC (severability contract): ``dashboards`` MUST keep working
when Studio routes are absent. ``studio_routes_factory=None`` is the
Dashboards-only path; passing a non-None factory mounts Studio on the
same Starlette app.
"""

from __future__ import annotations

import asyncio
import importlib.util
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from recon_gen.common.l2.cache import L2InstanceCache

# Starlette + uvicorn + the heavy server/_smoke_app modules are
# ``[serve]``-extra-only. Importing them at module top breaks any
# install-flavor that doesn't ship ``[serve]`` (Pages job, the
# ``docs-portable-install`` job, the release.yml smoke wheel test —
# all install ``[docs]`` only and run ``recon-gen --help`` /
# ``docs apply``, which only need the CLI shell to import). Original
# ``cli/serve.py`` deferred them inside the command body; restoring
# that pattern here keeps the no-``[serve]`` install paths working.
# See ``run_html_server`` for the lazy imports.
if TYPE_CHECKING:
    from starlette.routing import Mount, Route

    from recon_gen.common.html.server import ServedDashboard


# The four real apps. Dashboards + Studio both serve them; Studio
# additionally edits the L2 they're built from. ``smoke`` is the
# dashboards-only DB-free fixture (the trainer / spike target);
# Studio's CLI deliberately omits it (Studio's whole point is to edit
# a real L2, and smoke doesn't have one).
REAL_APPS: tuple[str, ...] = (
    "l1_dashboard", "l2_flow_tracing", "investigation", "executives",
)
APP_TITLES: dict[str, str] = {
    "l1_dashboard": "L1 Dashboard",
    "l2_flow_tracing": "L2 Flow Tracing",
    "investigation": "Investigation",
    "executives": "Executives",
    "smoke": "Smoke",
}


def build_real_app(app_name: str, cfg: Any, instance: Any) -> tuple[Any, Any]:  # type: ignore[no-untyped-def]: cfg/l2 untyped pending CLI-wide sweep
    """Register ``app_name``'s datasets + build its tree.

    Returns ``(tree_app, first_sheet)``. ``build_*_datasets(...)``
    populates the shared SQL registry (per-app-prefixed IDs, so the
    four apps don't collide) which ``make_tree_db_fetcher`` reads at
    construction time — a missing entry fails loudly here, not inside
    a hot HTMX swap.
    """
    if app_name == "executives":
        from recon_gen.apps.executives.app import (  # noqa: PLC0415
            build_executives_app,
        )
        from recon_gen.apps.executives.datasets import (  # noqa: PLC0415
            build_all_datasets as _build_datasets,
        )
        # Executives' build_all_datasets doesn't take l2_instance; the
        # per-app builder does, via the kwarg below.
        _build_datasets(cfg)
        tree_app = build_executives_app(cfg, l2_instance=instance)
    elif app_name == "investigation":
        from recon_gen.apps.investigation.app import (  # noqa: PLC0415
            build_investigation_app,
        )
        from recon_gen.apps.investigation.datasets import (  # noqa: PLC0415
            build_all_datasets as _build_datasets,
        )
        _build_datasets(cfg, instance)
        tree_app = build_investigation_app(cfg, l2_instance=instance)
    elif app_name == "l2_flow_tracing":
        from recon_gen.apps.l2_flow_tracing.app import (  # noqa: PLC0415
            build_l2_flow_tracing_app,
        )
        from recon_gen.apps.l2_flow_tracing.datasets import (  # noqa: PLC0415
            build_all_l2_flow_tracing_datasets as _build_datasets,
        )
        _build_datasets(cfg, instance)
        tree_app = build_l2_flow_tracing_app(cfg, l2_instance=instance)
    elif app_name == "l1_dashboard":
        from recon_gen.apps.l1_dashboard.app import (  # noqa: PLC0415
            build_l1_dashboard_app,
        )
        from recon_gen.apps.l1_dashboard.datasets import (  # noqa: PLC0415
            build_all_l1_dashboard_datasets as _build_datasets,
        )
        _build_datasets(cfg, instance)
        tree_app = build_l1_dashboard_app(cfg, l2_instance=instance)
    else:  # pragma: no cover — click.Choice gates this
        raise click.UsageError(f"Unknown dashboard app: {app_name!r}")
    if tree_app.analysis is None or not tree_app.analysis.sheets:
        raise click.UsageError(
            f"{app_name} app has no analysis sheets — bug in builder."
        )
    return tree_app, tree_app.analysis.sheets[0]


def build_real_dashboards(
    real_apps: list[tuple[str, Any, Any]],
    cfg: Any,  # WHY: cfg is Config but importing it at module top pulls a heavy graph
    *,
    pool: Any,  # WHY: AsyncConnectionPool; Any keeps the no-[serve] install importable
    theme: Any = None,  # WHY: ThemePreset | None (heavy import)
) -> dict[str, ServedDashboard]:
    """Compose the ``{name: ServedDashboard}`` map for the real apps.

    Wires BOTH per-app fetchers: the visual ``data_fetcher`` AND the
    ``options_fetcher`` that resolves dataset-backed (LinkedValues)
    parameter-control options from their companion option-source
    datasets — the Daily Statement account/role picker, Money Trail /
    Account Network / Recipient Fanout pickers, etc. One options fetcher
    serves every app (it keys off the dataset registry by identifier at
    fetch time).

    Extracted from ``_serve`` so a unit test can assert the wiring: the
    CLI serve path silently dropped ``options_fetcher`` (empty pickers →
    the correct parameterized query never received a value → a
    permanently blank sheet) while the e2e harness wired it, and nothing
    guarded the parity between the two serve paths.
    """
    from recon_gen.common.html._tree_fetcher import (  # noqa: PLC0415
        make_options_fetcher,
        make_tree_db_fetcher,
    )
    from recon_gen.common.html.server import (  # noqa: PLC0415
        ServedDashboard,
    )
    opts_fetcher = make_options_fetcher(cfg, pool=pool)
    return {
        name: ServedDashboard(
            tree_app=tree_app,
            sheet=sheet,
            title=APP_TITLES.get(name, name.title()),
            data_fetcher=make_tree_db_fetcher(tree_app, cfg, pool=pool),
            theme=theme,
            filter_specs=(),
            options_fetcher=opts_fetcher,
        )
        for name, tree_app, sheet in real_apps
    }


# Studio-routes factory contract: a callable that takes the cache, a
# dev-log flag, and the demo-DB pool (None = no pool, e.g. unit tests
# or stub-mode dashboards) and returns a list of routes.
# ``cli.dashboards`` passes ``None``; ``cli.studio`` passes
# ``make_studio_routes``. The seam keeps ``_html_serve`` ignorant of
# Studio internals.
#
# PEP 695 ``type`` statement defers evaluation — Route/Mount only get
# resolved when a type-checker walks the alias, never at module load.
# That keeps the no-``[serve]`` install paths importable.
#
# X.4.c.5.b: the pool is the third positional arg so X.4.c.5.c's
# ``GET /diagram/coverage`` route can mount and the chrome toggle
# (X.4.c.5.d) can light up.
type StudioRoutesFactory = Callable[
    ...,  # noqa: PLE0307: BS.3 part 3 — accept kwargs (top_nav_fn) without losing the positional-3 contract; concrete shape enforced by make_studio_routes signature itself
    list[Route | Mount],
]


def run_html_server(
    *,
    cfg: Any,  # type: ignore[no-untyped-def]: cfg untyped pending CLI-wide sweep
    instance: Any,  # type: ignore[no-untyped-def]: l2 untyped pending CLI-wide sweep
    l2_instance_path: Path | None,
    host: str,
    port: int,
    dev_log: bool,
    app_name: str,
    stub: bool,
    embed_docs: bool,
    studio_routes_factory: StudioRoutesFactory | None = None,
) -> None:
    """Boot the Starlette + uvicorn HTML server (dashboards or studio).

    ``studio_routes_factory=None`` is the Dashboards-only mount;
    a non-None factory builds an ``L2InstanceCache`` from
    ``l2_instance_path`` and splices its routes into ``make_app``.

    The `--stub` / `--app smoke` paths are dashboards-only — Studio
    callers must pass ``stub=False`` and ``app_name != "smoke"``.
    """
    # Lazy imports — see module-level comment about [serve]-extra
    # gating. These only fire when the command actually runs, never
    # at CLI shell import time.
    import uvicorn  # noqa: PLC0415

    from recon_gen.common.html._smoke_app import (  # noqa: PLC0415
        SMOKE_FILTER_SPECS,
        build_smoke_app,
        stub_money_trail_fetcher,
    )
    from recon_gen.common.html.server import (  # noqa: PLC0415
        ServedDashboard,
        make_app,
    )
    from recon_gen.common.theme import (  # noqa: PLC0415
        resolve_l2_theme,
    )

    if stub and app_name != "smoke":
        raise click.UsageError(
            f"--stub only applies to --app smoke (the DB-free fixture); "
            f"--app {app_name} needs a real database."
        )

    theme = resolve_l2_theme(instance)
    if theme is not None:
        click.echo(f"theme: L2-driven ({theme.theme_name})")

    # X.2.i — build the mkdocs handbook into a tempdir (against the same
    # L2) and embed it at /docs. Best-effort: needs the [docs] extra; a
    # [serve]-only install (no mkdocs) silently skips, never a hard fail.
    docs_dir: Path | None = None
    docs_tmp: tempfile.TemporaryDirectory[str] | None = None
    if (
        embed_docs
        and importlib.util.find_spec("mkdocs") is not None
        and l2_instance_path is not None
    ):
        from recon_gen.cli.docs import build_docs_site  # noqa: PLC0415

        docs_tmp = tempfile.TemporaryDirectory(prefix="qs-html-docs-")
        # strict=False — a stray mkdocs warning shouldn't take the server
        # down; `docs apply --strict` is the place that gates on those.
        rc = build_docs_site(str(l2_instance_path), docs_tmp.name, strict=False)
        if rc == 0 and (Path(docs_tmp.name) / "index.html").is_file():
            docs_dir = Path(docs_tmp.name)
            click.echo("docs: embedded handbook at /docs/")
        else:
            click.echo(
                "docs: mkdocs build failed — serving without /docs "
                "(run `recon-gen docs apply` to triage)"
            )
            docs_tmp.cleanup()
            docs_tmp = None

    # Build the real apps' trees here (sync) — ``build_*_datasets``
    # populates the shared SQL registry (per-app-prefixed IDs → no
    # collisions) that ``make_tree_db_fetcher`` reads in ``_serve``, so
    # a missing entry fails loudly now, not inside a hot HTMX swap.
    # smoke_tree/sheet only consumed when app_name == "smoke"; declared
    # outside the if so pyright can see them as bound in the inner _serve
    # closure (pyright can't carry the app_name == "smoke" narrowing
    # across the nested function boundary).
    smoke_tree: Any = None
    smoke_sheet: Any = None
    if app_name == "smoke":
        smoke_tree, smoke_sheet = build_smoke_app(cfg)
        real_apps: list[tuple[str, Any, Any]] = []
    else:
        names = list(REAL_APPS) if app_name == "all" else [app_name]
        real_apps = [
            (name, *build_real_app(name, cfg, instance)) for name in names
        ]

    # Studio: build the in-memory L2 cache here (no event loop needed);
    # routes are built INSIDE ``_serve()`` so the factory can take the
    # demo-DB pool (X.4.c.5.b — coverage fetcher needs the pool).
    cache: L2InstanceCache | None = None
    if studio_routes_factory is not None:
        if l2_instance_path is None:  # pragma: no cover — Studio CLI requires --l2
            raise click.UsageError(
                "studio requires an L2 instance (--l2)."
            )
        cache = L2InstanceCache.from_path(l2_instance_path)
        click.echo(
            f"studio: cached L2 instance for deployment "
            f"{cfg.deployment_name!s} from {cache.path}"
        )

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
            from recon_gen.common.html._db_fetcher import (  # noqa: PLC0415
                make_db_fetcher,
            )
            if stub:
                fetcher = stub_money_trail_fetcher
                click.echo("data: stub fetcher (deterministic)")
            else:
                fetcher = make_db_fetcher(cfg, instance)
                click.echo(
                    f"data: DB-backed ({cfg.dialect.value}) → "
                    f"{cfg.db_table_prefix}"
                    f"_inv_money_trail_edges"
                )
            dashboards["smoke"] = ServedDashboard(
                tree_app=smoke_tree, sheet=smoke_sheet,
                title=APP_TITLES["smoke"], data_fetcher=fetcher,
                theme=theme, filter_specs=SMOKE_FILTER_SPECS,
            )
        else:
            from recon_gen.common.db import (  # noqa: PLC0415
                make_connection_pool,
            )
            pool = await make_connection_pool(
                cfg, max_size=cfg.app2_db_pool_size,
            )
            # X.2.u.4.b — build via the shared helper so the data fetcher
            # AND the dataset-backed-control options fetcher are both
            # wired (the latter was dropped here pre-fix => empty pickers
            # => blank Daily Statement / Money Trail / Account Network /
            # Recipient Fanout). Guarded by test_html_serve_options_fetcher.
            dashboards.update(
                build_real_dashboards(real_apps, cfg, pool=pool, theme=theme),
            )
            click.echo(
                f"data: DB-backed ({cfg.dialect.value}) → {len(real_apps)} "
                f"app(s) [{', '.join(n for n, _, _ in real_apps)}] "
                f"(prefix={cfg.db_table_prefix})"
            )
        # X.4.c.5.b — build studio_routes here, after the pool exists,
        # so the diagram chrome can light up the Coverage toggle. None
        # pool ⇒ chrome silently omits the toggle (graceful degrade).
        #
        # BS.3 part 3 (2026-05-29): build the top-nav closure here too —
        # this scope knows the dashboards list + docs presence, which
        # the Studio routes themselves shouldn't have to learn. The
        # closure produces the shared <nav> HTML keyed off active_href;
        # Studio pages inject it before their page-local headers so
        # operators can hop to dashboards/docs from inside Studio.
        studio_routes: list[Route | Mount] | None = None
        if studio_routes_factory is not None and cache is not None:
            from recon_gen.common.html.render import (  # noqa: PLC0415
                build_top_nav_entries,
                emit_top_nav,
            )
            nav_entries = build_top_nav_entries(
                [(dash_id, served.title) for dash_id, served in dashboards.items()],
                studio_enabled=True,  # studio_routes spliced ⇒ enabled by construction
                docs_url="/docs/" if docs_dir is not None else None,
            )

            def _studio_top_nav(active_href: str) -> str:
                return emit_top_nav(entries=nav_entries, active_href=active_href)
            studio_routes = studio_routes_factory(
                cache, dev_log, pool, top_nav_fn=_studio_top_nav,
            )
        try:
            asgi_app = make_app(
                dashboards=dashboards, dev_log=dev_log, docs_dir=docs_dir,
                studio_routes=studio_routes,
            )
            click.echo(f"server: http://{host}:{port}/")
            if studio_routes is not None:
                click.echo(f"  → http://{host}:{port}/ — Studio")
            if len(dashboards) > 1:
                click.echo(
                    f"  → http://{host}:{port}/dashboards lists "
                    f"{len(dashboards)} dashboards"
                )
            if docs_dir is not None:
                click.echo(f"  → http://{host}:{port}/docs/ — embedded handbook")
            if dev_log:
                click.echo("dev-log: on (events forwarded to stderr)")
            uv_config = uvicorn.Config(
                asgi_app, host=host, port=port, log_level="info",
            )
            server = uvicorn.Server(uv_config)
            await server.serve()
        finally:
            if pool is not None:
                await pool.close()

    try:
        asyncio.run(_serve())
    finally:
        if docs_tmp is not None:
            docs_tmp.cleanup()
