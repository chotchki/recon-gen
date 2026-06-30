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
        build_cascade_map,
        make_day_availability_fetcher,
        make_options_search_fetcher,
        make_tree_db_fetcher,
    )
    from recon_gen.common.html.server import (  # noqa: PLC0415
        ServedDashboard,
    )
    # DM.2 — cascade narrowing map for the options-search fetcher. Walks
    # every served app's tree for ``ParameterDropdown`` controls carrying
    # a ``cascade_source`` (the Daily Statement Role→Account cascade) and
    # records, keyed by the dropdown's own (options dataset, column), the
    # match column + source param + sentinels. The picker datasets are
    # unparameterized (CQ.4.a — QS can't execute parameterized picker
    # datasets), so the App2 narrowing rides this map at fetch time
    # instead of a ``<<$pRole>>`` placeholder. Built from the SAME
    # real_apps the served dashboards are composed from (one fetcher
    # serves every dashboard; dataset identifiers are globally unique so
    # the map keys correctly across apps).
    cascade_map = build_cascade_map([tree_app for _name, tree_app, _sheet in real_apps])
    # CQ.2.e — single search fetcher serves both the JSON typeahead
    # endpoint (per-keystroke load) AND the HTML cascade endpoint
    # (sibling-change re-fetch). Both pass query='' for the seed
    # page; typeahead passes the user-typed string. The pre-CQ.2
    # make_options_fetcher with its silent LIMIT 2000 is gone.
    opts_search_fetcher = make_options_search_fetcher(
        cfg, pool=pool, cascade_map=cascade_map,
    )
    # DM.3 — per-(account, day) availability fetcher for the Daily
    # Statement Business Day picker (App2 only). Pool-backed; one
    # UNION-ALL query per visible calendar window (overscanned by the
    # JS so a month-flip rarely re-fires). Shared across dashboards on
    # this server (one prefix per cfg).
    day_avail_fetcher = make_day_availability_fetcher(cfg, pool=pool)
    # DK.10 — per-request data-anchor fetcher. Server route awaits it
    # once per dashboard / sheet GET and stamps the resulting
    # ``YYYY-MM-DD`` onto every ParameterDateSpec so the Flatpickr UI
    # clamps the upper bound to the latest moment the feed has data
    # for. Pool-backed; matview is one row, fetch cost is negligible.
    # Closes over ``cfg.db.table_prefix`` so all dashboards on this
    # server (one prefix per cfg) share the lookup.
    prefix = cfg.db.table_prefix

    async def _fetch_data_anchor() -> str | None:
        sql = f"SELECT data_anchor FROM {prefix}_data_anchor LIMIT 1"
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch_all(sql)  # pyright: ignore[reportUnknownMemberType]: SyncConnection Protocol; pool wraps per-dialect async wrapper around the same row-shaped result
        except Exception:  # noqa: BLE001 — best-effort; matview missing / legacy deploy / cold DB → unbounded picker (legacy behavior)
            return None
        if not rows:
            return None
        anchor = rows[0][0] if rows[0] else None
        if anchor is None:
            return None
        # Drivers return either a date or datetime; coerce to ISO date.
        from datetime import date as _date, datetime as _dt  # noqa: PLC0415
        if isinstance(anchor, _dt):
            return anchor.date().isoformat()
        if isinstance(anchor, _date):
            return anchor.isoformat()
        return None

    return {
        name: ServedDashboard(
            tree_app=tree_app,
            sheet=sheet,
            title=APP_TITLES.get(name, name.title()),
            data_fetcher=make_tree_db_fetcher(tree_app, cfg, pool=pool),
            theme=theme,
            filter_specs=(),
            options_search_fetcher=opts_search_fetcher,
            data_anchor_fetcher=_fetch_data_anchor,
            day_availability_fetcher=day_avail_fetcher,
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


def _resolve_handbook_docs_dir(
    *,
    embed_docs: bool,
    docs_site_dir: str | None,
    l2_instance_path: Path | None,
) -> tuple[Path | None, "tempfile.TemporaryDirectory[str] | None"]:
    """Pick the directory to mount at ``/docs`` for the studio / dashboards
    server. Returns ``(docs_dir, docs_tmp)``:

    - ``docs_dir is None`` → ``/docs`` stays unmounted.
    - ``docs_tmp`` is non-None only for the build-on-launch path; the
      caller MUST keep the handle alive for the server's lifetime and
      clean it up on shutdown (the tempdir holds the built site).

    Two modes:

    1. DZ.5 pre-built dir (``--docs-dir`` / ``RECON_GEN_DOCS_SITE_DIR``):
       serve it directly, NO build. Keeps the heavy mkdocs build off the
       launch critical path; it also dodges the sandboxed demo server's
       inability to run the build at all (a themed L2 makes mkdocs write a
       CSS shim into the docs source tree, a write the sandbox denies).
       The launchd demo host builds the site once in its unsandboxed
       refresh job and points the server here. CLI flag wins over env
       (mirrors the TLS precedence). An explicit override beats
       ``--no-docs`` (a positive "serve THIS" request). A configured dir
       with no ``index.html`` is operator error → loud ``UsageError``,
       never a silent skip.
    2. X.2.i build-on-launch (default): build into a tempdir against the
       same L2. Best-effort — needs the ``[docs]`` extra; a
       ``[serve]``-only install (no mkdocs) silently skips, never a hard
       fail.
    """
    from recon_gen.common.env_keys import (  # noqa: PLC0415 — lazy: only when serving
        RECON_GEN_DOCS_SITE_DIR,
    )

    docs_site_override: str | None = docs_site_dir
    if docs_site_override is None:
        env_docs = RECON_GEN_DOCS_SITE_DIR.get_or_none()
        if env_docs is not None:
            docs_site_override = str(env_docs)

    if docs_site_override is not None:
        prebuilt = Path(docs_site_override)
        if (prebuilt / "index.html").is_file():
            click.echo(f"docs: serving pre-built handbook at /docs/ ({prebuilt})")
            return prebuilt, None
        raise click.UsageError(
            f"docs-dir {prebuilt} has no index.html — build it first with "
            f"`recon-gen docs apply -o {prebuilt}` (or unset --docs-dir / "
            f"RECON_GEN_DOCS_SITE_DIR to build on launch)."
        )

    if not (
        embed_docs
        and importlib.util.find_spec("mkdocs") is not None
        and l2_instance_path is not None
    ):
        return None, None

    from recon_gen.cli.docs import build_docs_site  # noqa: PLC0415

    docs_tmp = tempfile.TemporaryDirectory(prefix="qs-html-docs-")
    # strict=False — a stray mkdocs warning shouldn't take the server
    # down; `docs apply --strict` is the place that gates on those.
    rc = build_docs_site(str(l2_instance_path), docs_tmp.name, strict=False)
    if rc == 0 and (Path(docs_tmp.name) / "index.html").is_file():
        click.echo("docs: embedded handbook at /docs/")
        return Path(docs_tmp.name), docs_tmp

    click.echo(
        "docs: mkdocs build failed — serving without /docs "
        "(run `recon-gen docs apply` to triage)"
    )
    docs_tmp.cleanup()
    return None, None


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
    docs_site_dir: str | None = None,
    studio_routes_factory: StudioRoutesFactory | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
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
    from recon_gen.common.attribution import (  # noqa: PLC0415
        resolve_attribution,
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

    # DE.4 — cfg.app2.tls fallback. Precedence: CLI flag → env var →
    # cfg.app2.tls.{cert_path,key_path}. The env read goes through the
    # `env_keys` registry (`RECON_GEN_TLS_CERT/KEY` with `must_be_file`
    # validator + access-log + deprecation channel) — NOT through the
    # click `envvar=` bypass, which had no typed validator (post-v14
    # audit fix #267: a typo `RECON_GEN_TLS_CRT` silently fell through
    # to None → uvicorn booted HTTP when operator intended HTTPS).
    # Half-set in cfg yaml is impossible (the loader raises); half-set
    # across cfg + CLI is operator error but the pairing check below
    # still catches it.
    from recon_gen.common.env_keys import (  # noqa: PLC0415 — lazy: only used when serving
        RECON_GEN_TLS_CERT,
        RECON_GEN_TLS_KEY,
    )
    if tls_cert is None:
        env_cert = RECON_GEN_TLS_CERT.get_or_none()
        if env_cert is not None:
            tls_cert = str(env_cert)
    if tls_key is None:
        env_key = RECON_GEN_TLS_KEY.get_or_none()
        if env_key is not None:
            tls_key = str(env_key)
    if tls_cert is None and tls_key is None:
        tls_cfg = cfg.app2.tls
        if tls_cfg is not None:
            tls_cert = tls_cfg.cert_path
            tls_key = tls_cfg.key_path

    # DC.1 — TLS pairing constraint. Both or neither. Half-set TLS is
    # operator error (typo in the cfg / env), not a graceful HTTP
    # fallback — the operator's intent was HTTPS, so fail loudly.
    if bool(tls_cert) ^ bool(tls_key):
        raise click.UsageError(
            "--tls-cert and --tls-key must be set together (got "
            f"cert={tls_cert!r} key={tls_key!r}). Set both to enable "
            "HTTPS, or omit both for HTTP."
        )
    tls_enabled = bool(tls_cert and tls_key)

    theme = resolve_l2_theme(instance)
    if theme is not None:
        click.echo(f"theme: L2-driven ({theme.theme_name})")

    docs_dir, docs_tmp = _resolve_handbook_docs_dir(
        embed_docs=embed_docs,
        docs_site_dir=docs_site_dir,
        l2_instance_path=l2_instance_path,
    )

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
            f"{cfg.aws.deployment_name!s} from {cache.path}"
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
                    f"data: DB-backed ({cfg.db.dialect.value}) → "
                    f"{cfg.db.table_prefix}"
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
                cfg, max_size=cfg.db.app2_pool_size,
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
                f"data: DB-backed ({cfg.db.dialect.value}) → {len(real_apps)} "
                f"app(s) [{', '.join(n for n, _, _ in real_apps)}] "
                f"(prefix={cfg.db.table_prefix})"
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
                banner_text=cfg.app2.banner_text,
                # DZ.12 — the footer credit resolves from the L2 instance's
                # ``attribution:`` block (None ⇒ baked default credit;
                # enabled=false ⇒ no footer). Mirrors banner_text: the
                # caller resolves, make_app just threads it to the shell.
                attribution=resolve_attribution(instance.attribution),
                # DD.3 — passing cfg threads through to make_app's auth
                # middleware short-circuit. When cfg.auth.oidc + .session
                # are both set, JwtCookieMiddleware + the /auth/* routes
                # fire; absent ⇒ HTTP local-dev passthrough.
                cfg=cfg,
            )
            scheme = "https" if tls_enabled else "http"
            click.echo(f"server: {scheme}://{host}:{port}/")
            if studio_routes is not None:
                click.echo(f"  → {scheme}://{host}:{port}/ — Studio")
            if len(dashboards) > 1:
                click.echo(
                    f"  → {scheme}://{host}:{port}/dashboards lists "
                    f"{len(dashboards)} dashboards"
                )
            if docs_dir is not None:
                click.echo(f"  → {scheme}://{host}:{port}/docs/ — embedded handbook")
            if dev_log:
                click.echo("dev-log: on (events forwarded to stderr)")
            if tls_enabled:
                click.echo(f"tls: cert={tls_cert} key={tls_key}")
            uv_kwargs: dict[str, Any] = dict(
                host=host, port=port, log_level="info",
            )
            if tls_enabled:
                uv_kwargs["ssl_certfile"] = tls_cert
                uv_kwargs["ssl_keyfile"] = tls_key
            uv_config = uvicorn.Config(asgi_app, **uv_kwargs)
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
