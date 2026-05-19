"""``recon-gen studio`` — implementation tools (X.4 SPEC).

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
omitted from this command — reach for ``recon-gen dashboards`` for
those iteration loops.
"""

from __future__ import annotations

import click

from recon_gen.cli._helpers import (
    config_option,
    l2_instance_option,
    resolve_l2_for_demo,
)
from recon_gen.cli._html_serve import run_html_server

# ``_studio_routes`` is starlette-backed; defer to keep the CLI shell
# importable on a no-``[serve]`` install (Pages, docs-portable smoke,
# release wheel test). The factory is only needed when ``studio`` runs.


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
        "when mkdocs isn't installed (`pip install recon-gen[docs]`)."
    ),
)
@click.option(
    "--demo-mode/--no-demo-mode",
    default=False,
    show_default=True,
    help=(
        "AE.2.b lockdown for public-demo hosting (Phase AE Mac mini). "
        "When set: (1) L2 yaml mutation endpoints (POST/PUT/DELETE on "
        "/l2_shape/*) are not mounted; (2) `POST /deploy` (AWS deploy) "
        "is not mounted; (3) `PUT /data/knobs/etl_hook` (shell exec) "
        "is not mounted; (4) trainer knob state (`.studio-state.yaml`) "
        "writes to a per-process tmpdir wiped on restart instead of "
        "persisting next to cfg.yaml. Diagram + L2 read views + "
        "data-shaping knobs (plants/window/seed/scope/etc.) continue "
        "to work — this is a mutation-perimeter cut, not a feature "
        "blackout. Defense in depth: sandbox-exec profile under "
        "`deploy/sandbox/` also denies file-write on L2 yaml + cfg.yaml "
        "regardless of this flag."
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
    demo_mode: bool,
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
    import functools  # noqa: PLC0415

    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        make_studio_routes,
    )
    from recon_gen.common.l2.tg_cache import (  # noqa: PLC0415
        TestGeneratorCache,
    )

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    # X.4.h.2 — instantiate the data-shaping panel's knob cache here
    # (cfg is in scope; the factory just gets a kwarg). Initial state =
    # cfg.test_generator snapshot merged with sidefile overrides
    # (X.4.h.7 — `<cfg.parent>/.studio-state.yaml` survives Studio
    # restarts without polluting the operator-authored cfg.yaml).
    #
    # AE.2.b — in `--demo-mode`, redirect the sidefile to a per-process
    # tmpdir so trainer-knob mutations don't persist next to cfg.yaml
    # (which is read-only under the sandbox-exec profile anyway). The
    # tmpdir is wiped on restart, matching the nightly-refresh contract.
    from pathlib import Path as _Path  # noqa: PLC0415
    if demo_mode:
        import tempfile  # noqa: PLC0415
        _demo_state_dir = _Path(
            tempfile.mkdtemp(prefix="recon-demo-studio-state-"),  # typing-smell: ignore[recon-prefix]: tmpdir name, not a deployment prefix — never reaches cfg.prefixed() flow
        )
        tg_cache = TestGeneratorCache(
            cfg.test_generator,
            state_path=_demo_state_dir / ".studio-state.yaml",
        )
    else:
        tg_cache = TestGeneratorCache.from_cfg_with_state(cfg, _Path(config))
    # Bind dialect + prefix at the CLI layer (X.4.c.5.c — coverage
    # fetcher needs both, but threading them through ``_html_serve``
    # would couple Studio internals to a Studio-ignorant module).
    studio_factory = functools.partial(
        make_studio_routes,
        dialect=cfg.dialect,
        prefix_override=cfg.db_table_prefix,
        cfg=cfg,
        tg_cache=tg_cache,
        demo_mode=demo_mode,
    )
    run_html_server(
        cfg=cfg,
        instance=instance,
        l2_instance_path=l2_instance_path,
        host=host, port=port, dev_log=dev_log,
        app_name=app_name,
        stub=False,            # Studio always reads the real DB.
        embed_docs=embed_docs,
        studio_routes_factory=studio_factory,
    )
