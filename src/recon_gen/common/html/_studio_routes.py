"""Studio route builder.

``make_studio_routes(cache)`` returns the ``Route``/``Mount`` list that
``cli.studio`` splices into ``make_app(... studio_routes=...)``. Each
returned route closes over the supplied ``L2InstanceCache`` so Studio's
read/write paths share one in-memory instance per server.

Routes (current):

- ``GET /`` — unified Studio home page (X.4.f.7). Diagram pane on
  top (iframe of ``/diagram``) + per-kind ``<details>`` sections
  below, each lazy-loaded via ``hx-get`` of the editor route's
  ``?embed=1`` fragment. HX-Trigger ``l2-cascade-reload`` fans
  out to refresh the diagram + every section after any save/delete.
- ``GET /diagram`` — the L2 topology rendered via post-processed
  graphviz SVG with rails as first-class nodes (the X.4.b dot pivot;
  spike locked 2026-05-13). Reads the per-rail Digraph builder,
  inlines its DOT source, and a small JS shim does the wasm-graphviz
  render + ``data-kind`` / ``data-id`` annotation + chrome wiring.
  Knobs: ``?engine=`` flips the layout binary (dot / neato / sfdp /
  …); ``?focus=<node_id>`` filters to that node + its
  ``_smart_focus_hops``-deep neighborhood (server-side re-render, dot
  re-lays out the smaller subgraph cleanly).
- ``Mount /studio/static`` — Studio-specific JS / CSS (the diagram
  shim + stylesheet). Sibling to the existing ``/static`` mount
  Dashboards owns; namespaced so a future renderer-replacement
  doesn't collide.
- ``Mount /studio/wasm-graphviz`` — the ``@hpcc-js/wasm-graphviz``
  module reused from ``docs/stylesheets/wasm-graphviz/``. No
  duplicated copy under ``assets/vendor/`` for the spike phase;
  the production vendoring decision lands at X.4.c.1 once the
  renderer is locked.

Severability: this module is Studio-only. ``cli.dashboards`` calls
``make_app`` with ``studio_routes=None`` and never imports this file.
"""

from __future__ import annotations

import json
import secrets
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import replace as dataclass_replace
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    # BV.3.3 snapshot — string-quoted return type on
    # `_get_or_make_snapshotter` references this; the implementation
    # imports lazily inside the closure so the module-level boot graph
    # stays lean.
    from recon_gen.common.snapshotter import Snapshotter
from urllib.parse import quote


# X.4.e cache-bust — boot-time random hex appended as `?cb=…` to every
# Studio asset URL the rendered pages emit. Stays stable for the
# lifetime of the process; restart the server to force every browser
# to refetch (no `Cmd+Shift+R` needed). Static-asset cache headers
# (Starlette's StaticFiles ETag/Last-Modified) still revalidate
# between server restarts; this just guarantees a fresh URL when the
# server itself bumps.
_BOOT_ID: str = secrets.token_hex(4)


def asset_url(path: str) -> str:
    """Versioned URL for a Studio asset.

    ``asset_url("diagram-svg.css")`` → ``/studio/static/diagram-svg.css?cb=<boot>``
    ``asset_url("/studio/wasm-graphviz/index.js")`` →
        ``/studio/wasm-graphviz/index.js?cb=<boot>`` (absolute path
    passes through unchanged except for the cb suffix).
    """
    if path.startswith("/"):
        return f"{path}?cb={_BOOT_ID}"
    return f"/studio/static/{path}?cb={_BOOT_ID}"

from datetime import date, datetime, timedelta

from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from recon_gen.common.config import Config, PlantKind, ScopeKind
from recon_gen.common.html._components import (
    kind_label_singular,
    render_summary_search_input,
)
from recon_gen.common.l2.editor import EntityKind
from recon_gen.common.html._studio_assets.tw_classes import (
    chrome_button_classes,
    compact_input_classes,
    ghost_button_classes,
    knob_wrapper_classes,
    timeline_chip_base_classes,
    timeline_day_classes,
)
from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.l2 import L2Instance
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.contract import (
    ChainEdgeContract,
    ColumnContracts,
    ColumnPredicate,
    RailContract,
    TemplateContract,
    derive_column_contracts,
)
from recon_gen.common.l2.coverage import (
    CoverageEntry,
    CoverageMap,
    TemplateMetadataCoverage,
    chain_edge_id,
    coverage_for,
    metadata_coverage_per_template,
)
from recon_gen.common.l2.deploy_pipeline import (
    DeploySummary,
    run_deploy_pipeline,
)
from recon_gen.common.l2.probe import (
    ProbeKind,
    ProbeResult,
    ProbeRow,
    evaluate_predicate,
    fetch_probe_rows,
)
from recon_gen.common.l2.triage import (
    Gap,
    detect_gaps,
)
from recon_gen.common.l2.seed import DEFAULT_BASELINE_WINDOW_DAYS
from recon_gen.common.l2.tg_cache import TestGeneratorCache
from recon_gen.common.l2.trainer_timeline import (
    PlantHit,
    compute_plant_timeline,
    hits_by_kind,
)
from recon_gen.common.l2.topology import (
    _VALID_SHOW_CATEGORIES,
    _categories_for_layer,
    _rail_id,
    _template_id,
    build_topology_graph_per_rail,
    topology_graph_for,
    visible_entities_for,
)
from recon_gen.common.l2.trainer import plants_per_node
from recon_gen.common.sql.dialect import Dialect
from recon_gen.common.html._side_panel import (
    render_side_panel_trigger,
    side_panel_routes as _side_panel_routes_imported,
)
from recon_gen.common.html._studio_training import render_training_pane
from recon_gen.common.html.render import _emit_theme_style


def studio_theme_head(instance: object) -> str:
    """X.4.f.13 — App2 Tailwind output.css link + L2 theme override block.

    Every studio HTML page links App2's compiled Tailwind sheet (which
    declares ``--color-accent`` / ``--color-surface`` / etc. with
    build-time defaults via ``input.css``'s ``@theme`` block) AND
    injects a per-L2-instance ``:root { --color-accent: ...; }``
    override so the studio inherits the active institution's brand
    palette. Post-AM, every chrome surface reads ``--color-*`` tokens
    directly via Tailwind utilities (`bg-accent`, `text-primary-fg`,
    etc.) — the ``--studio-*`` alias layer in the retired
    ``editor.css`` / ``data.css`` / ``diagram.css`` chrome rules is
    gone with those files. Only ``diagram-svg.css`` remains, and its
    SVG-only rules use literal hex values inside ``!important``
    overrides where Tailwind utilities can't reach SVG attribute
    selectors per AM.0 lock L4.

    Pass the L2Instance (``cache.get()``) — its optional ``theme``
    attribute drives the override. ``None`` falls back to
    ``DEFAULT_PRESET`` per the silent-fallback contract (N.4.k).
    """
    theme = getattr(instance, "theme", None)
    return (
        f'<link rel="stylesheet" href="/static/output.css?cb={_BOOT_ID}">\n'
        f'  {_emit_theme_style(theme)}'
    )


_STUDIO_ASSETS_DIR = Path(__file__).parent / "_studio_assets"
# wasm-graphviz vendored once under docs/stylesheets/ for the docs site
# (Phase T). For Studio, mounted at /studio/wasm-graphviz/ so the diagram
# shim can ``await import()`` it without a second 800KB copy.
_WASM_GRAPHVIZ_DIR = (
    Path(__file__).parent.parent.parent / "docs" / "stylesheets"
    / "wasm-graphviz"
)

# CZ.4 — refuse banner shown when Studio's POST /deploy fires with
# ``cfg.etl_hook is None`` (standalone-mode). The deploy pipeline would
# wipe-and-reseed; without an ETL hook the wipe is not followed by a
# real-data reload, so any unmarked row in the demo DB might be customer
# data. The message names the two correct unblock paths so the refusal
# is a redirect, not a dead-end. See PLAN Phase CZ + repo CLAUDE.md.
_CZ_STANDALONE_MODE_REFUSE_MESSAGE = (
    "Standalone mode — Deploy-changes refused.\n"
    "\n"
    "Your configuration does not declare an ETL hook (cfg.etl_hook is "
    "empty). In this mode, Deploy-changes would wipe rows we cannot "
    "prove are synthetic, which may delete real customer data.\n"
    "\n"
    "To deploy changes safely, either:\n"
    "  1. Configure cfg.etl_hook in your config.yaml so the next ETL "
    "cycle re-populates the demo DB, or\n"
    "  2. Use the Trainer 'Clear synthetic rows and re-seed' button, "
    "which removes only rows tagged metadata.source='training' and "
    "preserves unmarked rows.\n"
    "\n"
    "If you intended to reset everything (including unmarked rows), "
    "use the CLI:\n"
    "  recon-gen data apply --execute  (with your operator authority)."
)


def _duckdb_pool_subprocess_bracket(
    db_pool: AsyncConnectionPool | None,
) -> Callable[[], AbstractAsyncContextManager[None]] | None:
    """CO.x — bind ``run_deploy_pipeline``'s
    ``subprocess_lock_bracket`` callback to the pool's
    ``released_for_subprocess`` async context manager IFF this is a
    DuckDB pool.

    PG / Oracle pools support concurrent writers, so the bracket
    around ``step_1_etl_hook``'s subprocess is unnecessary on those
    dialects — we return ``None`` and the pipeline no-ops the bracket
    via ``nullcontext``. DuckDB's process-level write lock would
    otherwise block the operator's ``cfg.etl_hook`` subprocess from
    acquiring a write handle on the same ``.duckdb`` file, per the
    ``etl_duckdb_studio_concurrency`` audit.

    Duck-typed on ``released_for_subprocess`` rather than
    ``isinstance(_AsyncDuckdbPool)`` so this stays in the public
    ``common/html/`` layer without importing the private
    ``common/db.py`` pool class — only ``_AsyncDuckdbPool`` defines
    the method.
    """
    if db_pool is None or not hasattr(db_pool, "released_for_subprocess"):
        return None
    # Duck-typed across AsyncConnectionPool implementations — only
    # _AsyncDuckdbPool defines released_for_subprocess; the cast
    # collapses pyright's unknown-member fan-out at the boundary.
    return cast(
        "Callable[[], AbstractAsyncContextManager[None]]",
        db_pool.released_for_subprocess,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]: duck-typed, gated by hasattr check above
    )


def _editor_url_for_focus_node(node_id: str | None) -> str | None:
    """CF.3.m polish — bijective inverse of `_focus_node_for_entity`,
    pointing at the EDIT form for the entity.

    Per operator lock (2026-06-05): right-click "Open in editor" must
    land *directly* on the entity's `/edit` page, not on a list or a
    view page. When the node id doesn't uniquely identify an editable
    entity — synthetic `rail__bundle_N` aggregator nodes (no source-
    side entity) and `role__X` nodes (a role is shared by multiple
    accounts / templates / limit_schedules, so there's no unique
    target) — return ``None`` so the caller suppresses the affordance
    entirely rather than dumping the operator on a disambiguation
    surface. Roles + bundles intentionally deferred until we wire a
    proper "which entity uses this role?" picker, since unblocking
    them would require leaving the diagram.

    Mirror these prefix arms in the diagram.js `_editorUrlForNode` —
    test gates drift via `test_diagram_js_inverse_arms_match_python`.
    """
    if not node_id:
        return None
    if node_id.startswith("rail__bundle_"):
        # Synthetic CF.3.b aggregator node — no source-side entity.
        return None
    if node_id.startswith("rail__"):
        return f"/l2_shape/rail/{node_id[len('rail__'):]}/edit"
    if node_id.startswith("tmpl__"):
        return f"/l2_shape/transfer_template/{node_id[len('tmpl__'):]}/edit"
    if node_id.startswith("role__"):
        # Multiple accounts / templates / limit_schedules can share a
        # role; no unique edit target. Deferred until we add a
        # role-disambiguation picker; until then, suppress the link.
        return None
    return None


def _dev_log_head_snippets(dev_log: bool) -> tuple[str, str]:
    """Return ``(meta_tag, script_tag)`` to inject when ``dev_log=True``.

    Both ``""`` when off so production pages stay zero-overhead.
    The meta gates ``dev_log.js``'s installation (the script body is
    a no-op if the meta is absent — see the script's first line).
    """
    if not dev_log:
        return ("", "")
    return (
        '<meta name="dev-log">\n',
        f'<script src="{asset_url("/static/js/dev_log.js")}" defer></script>\n',
    )


_HOME_SECTIONS: tuple[tuple[str, str, str], ...] = (
    # (kind, label, accessor on L2Instance)
    ("account", "Accounts", "accounts"),
    ("account_template", "Account templates", "account_templates"),
    ("rail", "Rails", "rails"),
    ("transfer_template", "Transfer templates", "transfer_templates"),
    ("chain", "Chains", "chains"),
    ("limit_schedule", "Limit schedules", "limit_schedules"),
)

# X.4.f.12 — singleton kinds get their own home-page section format
# (no list, no +Add — just an Edit link landing on the singleton form).
_HOME_SINGLETONS: tuple[tuple[str, str, str], ...] = (
    # (kind, label, attr on L2Instance — None means "not set yet")
    ("theme", "Theme", "theme"),
    # CF.4.j cold-read P0: persona singleton removed from the home
    # surface 2026-06-05 because `BXa.1` deleted the persona form
    # routes but left the home section in place — the Edit link
    # 404'd. Re-add when the persona structured form ships.
    # AI.2.c — top-level instance settings (description +
    # institution_name + institution_acronym). The attr field is a
    # placeholder; the is_set check below branches for this kind because
    # it spans multiple fields. Phase CP removed
    # role_business_day_offsets from the singleton — offsets now live
    # per-Account / per-AccountTemplate.
    ("instance", "Instance settings", "description"),
)


def _banner(cfg: Config | None, *, embed: bool = False) -> str:
    """CU.3 — top-of-page banner driven by ``cfg.banner_text``.

    Replaces the AH.4 demo-mode banner. Returns empty string when
    ``cfg`` is None, when ``cfg.banner_text`` is None / empty, or when
    ``embed=True`` (embedded iframe surfaces suppress chrome). Demo
    installs set ``banner_text`` to a short disclaimer (e.g. "Edits
    reset on next restart"); production cfgs leave it None. Inline-
    styled so it needs no stylesheet / Tailwind-utility rebuild.
    """
    if embed or cfg is None or not cfg.banner_text:
        return ""
    return (
        '<div class="server-banner" role="status" '
        'style="background:#fff3cd;border-bottom:1px solid #ffe69c;'
        'color:#664d03;padding:0.6rem 1rem;font-size:0.9rem;text-align:center">'
        f"{escape(cfg.banner_text)} "
        '<a href="https://chotchki.github.io/recon-gen/" target="_blank" '
        'rel="noopener" style="color:#664d03;text-decoration:underline">'
        "Learn more</a>."
        "</div>"
    )


# CZ.5 — locked operator copy (REPLAN, 2026-06-09). Standalone-mode
# kicks in when ``cfg.etl_hook is None``: Trainer reset + Studio
# Deploy-changes will only DELETE rows tagged
# ``metadata.source = 'training'``; unmarked rows are presumed real
# customer data and survive. The banner makes the protection visible
# BEFORE the operator clicks anything.
STANDALONE_MODE_BANNER_TEXT = (
    "Standalone mode — your ETL hook is not configured. "
    "Reset and Deploy-changes will only remove rows tagged "
    "metadata.source='training'. Any unmarked rows are presumed to be "
    "real customer data and will be preserved. To configure an ETL "
    "hook, edit cfg.etl_hook in your config.yaml."
)

# REPLAN-locked Trainer reset button label on standalone-mode.
STANDALONE_RESET_BUTTON_LABEL = "Clear synthetic rows and re-seed"

# Tooltip for the Studio Deploy-changes button when standalone-mode
# disables it. Mirrors the banner-copy framing so the hover hint and
# the page banner stay aligned.
STANDALONE_DEPLOY_DISABLED_TOOLTIP = (
    "Standalone mode (cfg.etl_hook is None) — Deploy-changes is "
    "disabled because it would TRUNCATE + reseed the demo DB. "
    "Configure cfg.etl_hook in your config.yaml to re-enable."
)


def _standalone_mode_banner(cfg: Config | None, *, embed: bool = False) -> str:
    """CZ.5 — standalone-mode banner driven by ``cfg.etl_hook``.

    Renders when ``cfg.etl_hook is None`` (the gate signal — no ETL
    integrator wired, so Trainer-reset / Deploy-changes default to
    DELETE-only-synthetic to protect any real rows in the DB). Returns
    empty string when ``cfg`` is None, when ``cfg.etl_hook`` is
    configured, or when ``embed=True``.

    Inline-styled with a distinct color from the CU.3 demo banner
    (amber there → muted-blue here) so the two signals don't
    visually blur. The amber demo-only banner means "edits won't
    survive restart"; this banner means "automated deletes will be
    cautious".
    """
    if embed or cfg is None or cfg.etl_hook is not None:
        return ""
    return (
        '<div class="server-banner" role="status" '
        'data-test-standalone-mode-banner '
        'style="background:#dbeafe;border-bottom:1px solid #93c5fd;'
        'color:#1e3a8a;padding:0.6rem 1rem;font-size:0.9rem;text-align:center">'
        f"{escape(STANDALONE_MODE_BANNER_TEXT)}"
        "</div>"
    )


def _render_home_page(
    cache: L2InstanceCache, dev_log: bool, *, cfg: Config | None = None,
    top_nav_html: str = "",
    query_params: Mapping[str, str] | None = None,
) -> str:
    """X.4.f.7 — unified Studio home page (diagram + every entity kind).

    Composes one page with:

    - Header (matching the chrome on ``/diagram`` and ``/l2_shape/<kind>/``).
    - Diagram pane — ``<iframe src="/diagram?layer=1">`` so the
      wasm-graphviz render stays self-contained (its own document
      context; no double-load of the module script when the cascade
      forces a refresh).
    - Per-kind entity sections — ``<details>`` with lazy-loaded
      ``hx-get`` content (the editor route's ``?embed=1`` fragment).
      First section open; the rest collapsed so a 7-rail / 30-account
      L2 isn't an unbroken wall on first paint. Each section also
      links out (``↗``) to the dedicated per-kind page (deep-link
      target preserved from X.4.e — handy for sharing a URL).

    Cascade fan-out: every editor save/delete returns
    ``HX-Trigger: l2-cascade-reload``. Each section's inner ``<div>``
    declares ``hx-trigger="load, l2-cascade-reload from:body"`` so it
    refetches its fragment. The iframe is in its own document context
    and HTMX doesn't forward HX-Trigger events across that boundary;
    a small parent-page JS listener catches the same custom event and
    bumps ``iframe.src = iframe.src`` to force a reload.
    """
    instance = cache.get()
    prefix = escape(cfg.deployment_name if cfg is not None else cache.path.stem)
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)

    # CF.4.d — home URL carries kind-namespaced toolbar state. Each
    # section's hx-get URL translates those into the bare keys the
    # /l2_shape/<kind>/ endpoint expects (Q1A for the section,
    # Q1B for the home URL). Sections with active state auto-open
    # (Q6A — a collapsed section that quietly hides its search hits
    # is a footgun). Listed by kind here so the loop below can read
    # both the per-section URL fragment and the auto-open decision.
    qp: Mapping[str, str] = query_params if query_params is not None else {}
    _TOOLBAR_KEYS = ("q", "sort_column", "page_offset", "page_size")
    section_query: dict[str, str] = {}
    section_has_state: dict[str, bool] = {}
    for kind, _label, _accessor in _HOME_SECTIONS:
        parts: list[str] = []
        active = False
        for base in _TOOLBAR_KEYS:
            home_key = f"{kind}_{base}"
            val = qp.get(home_key)
            if val:
                parts.append(f"{base}={quote(val)}")
                active = True
        section_query[kind] = ("&" + "&".join(parts)) if parts else ""
        section_has_state[kind] = active

    # If no section has active state, default open is the first
    # (legacy behavior). If any section does, only those auto-open.
    #
    # CG.14 (2026-06-05) — operator lock: stay with account-only on
    # first load. Accounts are the persistent ledger; rails are the
    # verb but more numerous (a dense L2 can carry 100+) and would
    # blow up the fresh-visit scroll. Open-the-top-three was an
    # option but front-loads three accordions worth of vertical
    # space; localStorage-persisted last-open was an option but
    # would be the first piece of custom JS in the studio surface
    # (current studio is HTMX-only) and the cold-read didn't surface
    # this default as actively painful — just "surprising." Document
    # + keep, don't reshape until a returning-operator dogfood says
    # otherwise.
    any_state_active = any(section_has_state.values())

    section_blocks: list[str] = []
    for idx, (kind, label, accessor) in enumerate(_HOME_SECTIONS):
        n = len(getattr(instance, accessor))
        if any_state_active:
            open_attr = " open" if section_has_state[kind] else ""
        else:
            open_attr = " open" if idx == 0 else ""
        body_id = f"home-section-body-{kind}"
        # AM.2 step 2: section chrome migrated. `.home-section` /
        # `.home-section-add` / `.home-section-link` / `.home-section-body`
        # / `.home-section-loading` semantic classes retired in favor
        # of raw Tailwind utilities. `data-kind` stays as the JS hook
        # the home-page focus-filter listener uses.
        add_link = (
            f'<a class="ml-2 text-accent no-underline font-semibold text-sm hover:underline" '
            f'href="/l2_shape/{kind}/new" '
            # Stop the click from triggering the surrounding <details>
            # toggle. The browser still follows the href to the create page.
            f'onclick="event.stopPropagation()" '
            # `_HOME_SECTIONS` is typed `tuple[str, str, str]` for historical
            # reasons; every literal value above IS an EntityKind by
            # construction so the cast is the safe narrowing point.
            f'title="Create a new {escape(kind_label_singular(cast("EntityKind", kind)))}">+ Add</a>'
        )
        # CF.4 followup (2026-06-05): search input lives in the
        # summary so the operator can search without expanding first.
        # The body toolbar drops its search input (via
        # `header_owns_search=True`) so there's one search per kind.
        # Typing auto-opens the details (`oninput=...open=true`).
        section_url = f"/l2_shape/{kind}/?embed=1{section_query[kind]}"
        summary_search_html = render_summary_search_input(
            # `_HOME_SECTIONS` is typed `tuple[str, str, str]` for historical
            # reasons; every literal value above IS an EntityKind by
            # construction so the cast is the safe narrowing point.
            kind=cast("EntityKind", kind),
            initial_q=qp.get(f"{kind}_q", ""),
            section_url=section_url,
            body_id=body_id,
            url_prefix=kind,
        )
        # CG.9 (2026-06-05) — explicit `▸` chevron at the left edge of
        # the section summary, rotating 90° via `group-open:rotate-90`
        # when the operator opens the section. Matches the card-level
        # chevron added in CF.4.l so the open/close affordance reads
        # the same at both surface levels. Native browser marker
        # suppressed (`list-none` + the webkit attr-selector) so we
        # control the glyph + color + animation.
        section_chevron_html = (
            '<span class="inline-block transition-transform '
            'group-open:rotate-90 text-secondary-fg select-none" '
            'aria-hidden="true" data-role="section-chevron">▸</span>'
        )
        section_blocks.append(
            f'<details class="group bg-white border border-surface-border '
            f'rounded-md mb-3 overflow-hidden" '
            f'data-kind="{escape(kind)}"{open_attr}>'
            f'<summary class="cursor-pointer px-4 py-2 font-semibold '
            f'text-accent bg-surface-bg select-none hover:bg-link-tint '
            f'flex items-center gap-2 flex-wrap list-none '
            f'[&::-webkit-details-marker]:hidden">'
            f"{section_chevron_html}"
            f'<span>{escape(label)} '
            f'<span class="text-xs text-secondary-fg font-normal">({n})</span>'
            f"</span>"
            f"{summary_search_html}"
            f"{add_link}"
            f'<a class="ml-2 text-accent no-underline font-normal text-sm hover:underline" '
            f'href="/l2_shape/{kind}/" '
            f'onclick="event.stopPropagation()" '
            f'title="Open in dedicated page">↗</a>'
            f"</summary>"
            f'<div id="{body_id}" '
            f'hx-get="/l2_shape/{kind}/?embed=1{section_query[kind]}" '
            f'hx-trigger="load, l2-cascade-reload from:body" '
            f'hx-swap="innerHTML">'
            f'<p class="p-4 text-secondary-fg italic m-0">loading…</p>'
            f"</div>"
            f"</details>"
        )
    # X.4.f.12 — singleton sections at the bottom of the home page
    # (cosmetic / less-frequently-edited than the entity collections).
    # No list, no +Add — just an Edit link to the singleton form.
    for kind, label, attr in _HOME_SINGLETONS:
        if kind == "instance":
            # AI.2.c — multi-field singleton: "set" when ANY top-level
            # field is populated.
            is_set = (
                getattr(instance, "description", None) is not None
                or getattr(instance, "institution_name", None) is not None
                or getattr(instance, "institution_acronym", None) is not None
            )
        else:
            is_set = getattr(instance, attr, None) is not None
        status = "set" if is_set else "not set"
        # BF.7+BF.8 (2026-05-25): theme + persona are structured forms
        # now (per-field controls, not yaml blocks). Only `instance`
        # is still a single YAML block (two top-level scalars don't
        # warrant a decomposed form). Title + body reflect the
        # actual editor surface so the home-page prose doesn't lie.
        singleton_form_kind = (
            "structured form" if kind in ("theme", "persona")
            else "single YAML block"
        )
        singleton_link = (
            f'<a class="ml-2 text-accent no-underline font-semibold text-sm hover:underline" '
            f'href="/l2_shape/{kind}/" '
            f'onclick="event.stopPropagation()" '
            f'title="Edit {escape(label)} ({singleton_form_kind})">Edit</a>'
        )
        singleton_body = (
            f"{escape(label)} is a {singleton_form_kind} — "
            f"click <strong>Edit</strong> to view / change it."
        )
        # CG.9 — same chevron treatment on singleton sections so the
        # whole accordion reads consistently.
        singleton_chevron_html = (
            '<span class="inline-block transition-transform '
            'group-open:rotate-90 text-secondary-fg select-none mr-2" '
            'aria-hidden="true" data-role="section-chevron">▸</span>'
        )
        section_blocks.append(
            f'<details class="group bg-white border border-surface-border '
            f'rounded-md mb-3 overflow-hidden" '
            f'data-kind="{escape(kind)}">'
            f'<summary class="cursor-pointer px-4 py-2 font-semibold '
            f'text-accent bg-surface-bg select-none hover:bg-link-tint '
            f'list-none [&::-webkit-details-marker]:hidden">'
            f"{singleton_chevron_html}"
            f"{escape(label)} "
            f'<span class="text-xs text-secondary-fg font-normal">({escape(status)})</span> '
            f"{singleton_link}"
            f"</summary>"
            f"<div>"
            f'<p class="p-4 text-secondary-fg italic m-0">{singleton_body}</p>'
            f"</div>"
            f"</details>"
        )
    sections_html = "\n    ".join(section_blocks)
    demo_banner = _banner(cfg)
    standalone_banner = _standalone_mode_banner(cfg)
    # CF.4 followup (2026-06-05): the "Studio · qsgen-duckdb" +
    # `Deploy changes` strip was removed. Operator dogfood: it
    # duplicated info the top-nav already carries (which surface
    # you're on) and the deployment_name was secondary chrome.
    # Deploy is reachable via `recon-gen json apply --execute` (or a
    # future top-nav action) — no button here.

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · {prefix}</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <script>
    // X.4.e.5 — swap 4xx response bodies (validator returns 400 + the
    // re-rendered form fragment). 5xx still treated as errors.
    document.addEventListener('htmx:beforeSwap', function(evt) {{
      var status = evt.detail.xhr.status;
      if (status >= 400 && status < 500) {{
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
      }}
    }});
    // CF.3.l (2026-06-05) — the iframe-cascade-reload listener +
    // X.4.f.8 iframe-focus filter pipeline were removed when Diagram
    // got promoted to its own top-level surface. The editor's
    // cascade-reload still triggers section refetches via the
    // hx-trigger pair on each section body (load + cascade).

    // CF.3.l (2026-06-05) — the X.4.f.8 iframe-focus filter pipeline
    // + the X.4.f.8.reverse click-card-title iframe-URL mutator were
    // removed when Diagram got promoted to its own top-level surface.
    // Per-card focus is now a plain anchor in the read-card template;
    // the editor's entity-card focus-filter has no counterpart since
    // the editor surface no longer hosts the diagram.
  </script>
  {devlog_script}</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}{standalone_banner}
  <header class="px-8 py-4 border-b border-surface-border bg-white" id="home-intro">
    <h1 class="text-xl font-semibold m-0">L2 Editor</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      Each section below is a kind of building block in this
      institution's L2 shape. Expand one to browse its entries —
      search the summary or click a card title to see the detail.
      The diagram link in the top nav shows how the kinds connect.
    </p>
  </header>
  <main class="px-4 pt-4 pb-8 max-w-7xl mx-auto" id="home-entities">
    <div id="delete-confirm-banner-slot" data-test-delete-banner-slot></div>
    {sections_html}
  </main>
</body>
</html>
"""


# -- BT.1 — /studio/etl landing page ----------------------------------------


# Per BT.0.5 design mockup §5 navigation flow: 3-card index, one per
# child page + one-line description. Title + summary + "coming in BT.N"
# hint pulled from the mockup doc; the cards link to the eventual route
# targets so when BT.2/3/4 land they go live automatically — no edit
# back to this list. Order: Probe (investigate) → Run (execute) →
# Triage (find + fix) — matches the operator's natural flow per the
# mockup's narrative.
def _render_etl_sub_nav(active_href: str) -> str:
    """BTa.7 — sub-nav strip on every ETL sub-page so the operator
    can jump between Refresh Data / Triage / Probe without bouncing
    back to /etl/.

    Cold-read v2 finding: after Refresh Data fails the operator
    needs Triage to debug, but there's no in-page nav to it. Each
    sub-page now ships this strip just below the page header.

    Cold-read v3 finding: the active sub-nav entry styled
    identically to the primary action button on the same page
    (both `bg-accent text-accent-fg` rectangles) — operators were
    confused which was navigation vs action. Fix: render the active
    entry as a flat "you-are-here" label (no button chrome, accent
    underline), inactive entries as borderless text links. Keeps
    the action button visually unique on the page.
    """
    items: list[tuple[str, str]] = [
        ("↻ Refresh Data", "/etl/run"),
        ("⚠ Triage", "/etl/triage"),
        ("🔍 Probe", "/etl/probe"),
        ("← Loop overview", "/etl/"),
    ]
    blocks: list[str] = []
    for label, href in items:
        active = href == active_href
        if active:
            # "You are here" — flat, no border, accent underline.
            # NOT a button shape so the eye reads it as a marker,
            # not as an action.
            blocks.append(
                '<span class="px-1 py-1.5 text-sm font-semibold text-accent '
                'border-b-2 border-accent cursor-default" '
                f'aria-current="page" '
                f'data-test-etl-subnav-active="{escape(href)}">'
                f'{escape(label)}</span>'
            )
        else:
            blocks.append(
                '<a class="px-1 py-1.5 text-sm text-secondary-fg no-underline '
                'border-b-2 border-transparent hover:text-accent '
                'hover:border-accent transition-colors" '
                f'href="{escape(href)}" '
                f'data-test-etl-subnav="{escape(href)}">{escape(label)}</a>'
            )
    return (
        '<nav class="flex flex-wrap items-center gap-6 px-4 py-2 bg-white '
        'border-b border-surface-border" aria-label="ETL Support sub-nav" '
        'data-test-etl-subnav>'
        + "".join(blocks)
        + '</nav>'
    )


# BTa.3 — numbered loop (BTa.0 Lock 2). Tuple ordering IS the loop
# order: Refresh Data → Triage gaps → Probe & fix. The render walks
# in order + assigns 1./2./3. + a `→` arrow between cards.
_ETL_LANDING_CARDS: tuple[tuple[str, str, str | None, str], ...] = (
    (
        "Refresh Data",
        "/etl/run",
        None,
        "Execute the ETL pipeline (wipe → hook → matview refresh) and "
        "render a per-kind coverage tally so you can confirm every "
        "declared primitive landed at least one row.",
    ),
    (
        "Triage gaps",
        "/etl/triage",
        None,
        "Find + fix gaps — diff declared contracts against observed "
        "runtime; each gap renders a card with the diagnosis + a deep "
        "link to the relevant L2 editor page.",
    ),
    (
        "Probe & fix",
        "/etl/probe",
        None,
        "Investigate one L2 slice — pick a rail, template, or chain "
        "and see L2-declared column expectations side-by-side with "
        "the runtime rows that match.",
    ),
)


# BTa.3 — first-time tutorial banner content (BTa.0 Lock 2).
# 5-step checklist surfaced inline in a collapsible details block;
# dismissable via the X button. Dismissal persists in localStorage
# keyed on deployment_name so each environment carries its own state.
_TUTORIAL_STEPS: tuple[tuple[str, str], ...] = (
    (
        "Configure your ETL hook",
        "Set <code>etl_hook</code> in your <code>config.yaml</code> "
        "(or skip — the bundled demo regenerates Sasquatch data when "
        "the hook is unset). The Refresh Data run-status banner tells "
        "you which one fired last.",
    ),
    (
        "Refresh Data",
        "Click <strong>Refresh Data</strong> to run your hook, then "
        "the matview refresh. The coverage report shows every "
        "L2-declared primitive (rail / template / chain / metadata key) "
        "with the observed row count.",
    ),
    (
        "Triage the gaps",
        "If anything missed, the Triage view groups gaps by kind. "
        "Each card carries the diagnosis + a deep link to the L2 "
        "editor's create-new form, with a one-click \"Back to Triage\" "
        "breadcrumb that survives the save.",
    ),
    (
        "Probe a single slice",
        "Use Probe to investigate one specific entity — pick a rail "
        "name, set the date window (defaults to All time), and see "
        "the L2-declared contract next to the runtime rows. Faster "
        "than running the whole pipeline when you're iterating on one "
        "fixture.",
    ),
    (
        "Re-run + repeat",
        "Edit the L2, click Refresh Data again, watch the coverage "
        "tally close. The loop tightens as you go — most operators "
        "hit clean coverage on the third pass.",
    ),
)


def _render_tutorial_banner(deployment_name: str) -> str:
    """BTa.3 — dismissable "First time here?" tutorial banner.

    Renders a collapsible 5-step checklist above the numbered loop
    cards. Dismissal persists in ``localStorage`` keyed on
    ``deployment_name`` (per BTa.0 Lock 2) so the operator sees it
    once per environment + the dismissal survives navigation /
    page-refresh / browser-restart.

    Hidden by default until the JS shim checks localStorage — avoids
    a one-frame flash for returning operators. The ``data-tutorial-
    banner-key`` attribute lets the inline script (no module import,
    no fetch) toggle visibility + write the dismissal back.
    """
    storage_key = f"recon_gen.tutorial_dismissed.{deployment_name}"
    step_items = "\n      ".join(
        f'<li class="mb-2 last:mb-0">'
        f'<strong class="text-accent">{escape(title)}</strong> — {body}'
        f'</li>'
        for title, body in _TUTORIAL_STEPS
    )
    # Hidden on initial render; the inline script reveals it when the
    # localStorage flag isn't set. Using inline style="display:none"
    # rather than `hidden` attribute so the script's
    # `style.display = ''` reveals it cleanly without attribute-toggle
    # gymnastics.
    return f"""
  <aside id="etl-tutorial-banner"
         class="mx-8 mt-6 bg-accent/5 border border-accent/30 rounded-md p-4"
         style="display:none"
         data-tutorial-banner-key="{escape(storage_key)}">
    <div class="flex items-start justify-between gap-3 mb-2">
      <h2 class="text-base font-semibold text-accent m-0">
        First time here? Walk the loop ↓
      </h2>
      <button type="button"
              class="text-secondary-fg hover:text-primary-fg text-sm leading-none p-1 -m-1"
              aria-label="Dismiss tutorial banner"
              data-tutorial-dismiss>×</button>
    </div>
    <details class="text-sm" open>
      <summary class="cursor-pointer text-secondary-fg hover:text-accent mb-2">
        Show the 5-step checklist
      </summary>
      <ol class="list-decimal pl-6 m-0 mt-2">
      {step_items}
      </ol>
    </details>
  </aside>
  <script>
  (function() {{
    const banner = document.getElementById('etl-tutorial-banner');
    if (!banner) return;
    const key = banner.dataset.tutorialBannerKey;
    let dismissed = false;
    try {{ dismissed = localStorage.getItem(key) === '1'; }} catch (e) {{}}
    if (!dismissed) {{
      banner.style.display = '';
    }}
    const closeBtn = banner.querySelector('[data-tutorial-dismiss]');
    if (closeBtn) {{
      closeBtn.addEventListener('click', () => {{
        banner.style.display = 'none';
        try {{ localStorage.setItem(key, '1'); }} catch (e) {{}}
      }});
    }}
  }})();
  </script>
"""


def _render_etl_landing_page(
    cache: L2InstanceCache,
    dev_log: bool,
    *,
    cfg: Config | None = None,
    top_nav_html: str = "",
) -> str:
    """BT.1 — ``/studio/etl`` landing page.

    3-card index of the Phase BT ETL Support surfaces per the BT.0.5
    design mockup (§5 cross-page navigation flow). Mirrors the home
    page's chrome (top-nav + Studio header) so the operator's mental
    model stays consistent across `/`, `/data`, `/diagram`, `/etl/`.

    Each card links to its target sub-route. BT.2/3/4 land those
    routes — until then, a click 404s; the "coming in BT.N" hint on
    the card primes the operator that the destination isn't ready.
    """
    instance = cache.get()
    # CG.21 (2026-06-05) — deployment name lives only on the home page
    # `<title>`. Other surfaces (this ETL home + sub-pages, the diagram,
    # the data page, the editor list / form / singleton pages) drop it
    # so the canonical `Recon-Gen · Studio · <surface>` shape stays tight.
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    demo_banner = _banner(cfg)
    standalone_banner = _standalone_mode_banner(cfg)

    # BTa.3 — numbered cards with arrows between them. The tuple
    # order IS the loop order (Refresh Data → Triage → Probe), and
    # the index becomes the visible step number.
    card_blocks: list[str] = []
    for step_num, (title, href, phase, description) in enumerate(_ETL_LANDING_CARDS, start=1):
        phase_hint = (
            f"{escape(href)} · coming in {escape(phase)}"
            if phase is not None
            else escape(href)
        )
        card_blocks.append(
            '<a class="etl-landing-card group block p-5 bg-white border '
            'border-surface-border rounded-md shadow-sm hover:border-accent '
            'hover:shadow-md transition-shadow no-underline text-primary-fg" '
            f'href="{escape(href)}" data-step="{step_num}">'
            '<div class="flex items-baseline gap-3 mb-1">'
            '<span class="inline-flex items-center justify-center w-7 h-7 '
            'rounded-full bg-accent text-accent-fg text-sm font-semibold '
            'shrink-0" aria-hidden="true">'
            f'{step_num}</span>'
            f'<h2 class="text-xl font-semibold text-accent m-0">{escape(title)}</h2>'
            '</div>'
            f'<p class="text-xs text-secondary-fg font-mono m-0 mb-2 pl-10">{phase_hint}</p>'
            f'<p class="text-sm text-primary-fg m-0 pl-10">{escape(description)}</p>'
            '</a>'
        )
    # Arrow between cards (visible on lg+, where the grid is single-row).
    # On smaller screens the cards stack vertically; arrows hidden via
    # `hidden lg:flex` to avoid awkward sideways arrows on stacked cards.
    arrow_html = (
        '<div class="hidden lg:flex items-center justify-center text-accent '
        'text-3xl font-bold" aria-hidden="true">→</div>'
    )
    cards_with_arrows = f"\n    {arrow_html}\n    ".join(card_blocks)

    tutorial_banner = _render_tutorial_banner(
        cfg.deployment_name if cfg is not None else cache.path.stem,
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · ETL</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  {devlog_script}</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}{standalone_banner}
  <header class="px-8 py-4 border-b border-surface-border bg-white">
    <h1 class="text-xl font-semibold m-0">ETL Support</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      Three steps to land your customer's ETL feed cleanly. Walk them
      in order on a first pass; once you know the surface, jump
      anywhere via the numbered cards.
    </p>
  </header>
  {tutorial_banner}
  <main class="px-8 pb-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-[1fr_auto_1fr_auto_1fr] lg:items-stretch" id="etl-landing-cards">
    {cards_with_arrows}
  </main>
</body>
</html>
"""


# -- BT.2 — /studio/etl/probe page ------------------------------------------


# BTa.2 P1.1 (operator-locked): Probe default window is "All time"
# — the prior 7-day default created a trust-killer where Run-coverage
# said a rail had data but Probe (defaulted to last-7-days) said "no
# rows" because the seed-data anchor was years outside any reasonable
# rolling window. "All" sentinel is 1900-01-01 → today; the SQL
# fetcher's BETWEEN bind still works, just with a wide-enough window
# to catch any reasonable historical data. Operators can narrow via
# the date pickers.
_PROBE_DEFAULT_FROM = date(1900, 1, 1)


async def _render_etl_probe_page(
    cache: L2InstanceCache,
    dev_log: bool,
    request: Request,
    *,
    db_pool: AsyncConnectionPool | None,
    dialect: Dialect | None,
    prefix_override: str | None,
    cfg: Config | None = None,
    top_nav_html: str = "",
) -> str:
    """BT.2 — ``/etl/probe`` L2-slice probe page.

    Three-step UX:
      1. Operator picks a slice via the kind radio + name dropdown +
         date-range picker.
      2. Server fetches the L2 contract (via BT.5's derivation) for the
         picked entity + the observed transactions rows narrowed by
         the slice + window.
      3. Page renders contract (left) and observed rows (right) so the
         operator can scan for matches / gaps.

    When no name is picked (initial page load), the right panel
    shows an empty-state copy nudging the operator to pick.

    When db_pool is None (unit-test surface), the observed-rows fetch
    is skipped; the page renders the picker + a "no DB pool wired"
    banner where the rows table would be. The contract pane still
    renders so the L2-derivation surface is testable without a DB.
    """
    instance = cache.get()
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    demo_banner = _banner(cfg)
    standalone_banner = _standalone_mode_banner(cfg)
    prefix = (
        prefix_override
        if prefix_override is not None
        else (cfg.db_table_prefix if cfg is not None else cache.path.stem)
    )

    qp = request.query_params
    kind = _validate_probe_kind(qp.get("kind"))
    name = (qp.get("name") or "").strip()
    # BTa.2 P1.1 — default window = All time (1900 → today). Eliminates
    # the Run-vs-Probe disagreement the BT cold-read flagged as P1.
    today = date.today()  # typing-smell: ignore[no-datetime-now]: probe page default-window anchor — wall-clock today is the operator-facing "to" endpoint; explicit ?date_to overrides
    date_from = _parse_iso_date(qp.get("date_from")) or _PROBE_DEFAULT_FROM
    date_to = _parse_iso_date(qp.get("date_to")) or today

    contracts = derive_column_contracts(instance)
    picker_html = _render_probe_picker(
        instance, kind=kind, name=name,
        date_from=date_from, date_to=date_to,
    )

    if name == "":
        body_html = _render_probe_empty_initial()
    else:
        body_html = await _render_probe_body(
            contracts=contracts,
            kind=kind, name=name,
            date_from=date_from, date_to=date_to,
            db_pool=db_pool, prefix=prefix, dialect=dialect,
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · ETL · Probe</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  {devlog_script}</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}{standalone_banner}
  {_render_etl_sub_nav("/etl/probe")}
  <header class="px-8 py-4 border-b border-surface-border bg-white">
    <h1 class="text-xl font-semibold m-0">Probe</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      Slice the L2 to see how many rails / templates / chains the
      configured ETL needs to land. Run before staging customer data.
    </p>
  </header>
  <main>
  {picker_html}
  {body_html}
  </main>
</body>
</html>
"""


# BTa.5 — one-line slice-kind definitions (per BTa.0.5 mockup §5a).
# Surfaced inline next to each radio so operators new to the L2
# vocabulary don't have to bounce to the side-panel glossary.
_PROBE_KIND_DEFINITIONS: Mapping[str, str] = {
    "rail": (
        "one money-movement leg shape (e.g. ACH credit, internal "
        "GL move) — the lowest-level L2 primitive."
    ),
    "transfer_template": (
        "a multi-leg event template that bundles two or more rails "
        "into one logical transfer (e.g. card-purchase = auth + post)."
    ),
    "chain": (
        "a parent → child dependency between transfers (e.g. ACH "
        "settlement triggers GL clearing 1-2 days later)."
    ),
}


# BTa.5 — date quick-pick chips. Each chip carries a (label, day-window)
# tuple where window=None means "All time" → date_from = _PROBE_DEFAULT_FROM.
_PROBE_DATE_CHIPS: tuple[tuple[str, int | None], ...] = (
    ("Last 7d", 7),
    ("Last 30d", 30),
    ("Last 90d", 90),
    ("All time", None),
)


def _chip_window_dates(
    today: date, days: int | None,
) -> tuple[date, date]:
    """Resolve a chip label's day-window into a (from, to) date pair."""
    if days is None:
        return _PROBE_DEFAULT_FROM, today
    from datetime import timedelta  # noqa: PLC0415
    return today - timedelta(days=days - 1), today


def _render_probe_picker(
    instance: L2Instance, *,
    kind: ProbeKind, name: str,
    date_from: date, date_to: date,
) -> str:
    """3-radio + name dropdown + date range form. Vanilla GET-with-query-
    params so the URL is bookmarkable + operator-shareable.

    BTa.5 — operator-facing polish:
    - Per-radio one-line definition (`_PROBE_KIND_DEFINITIONS`) so first-
      time operators don't bounce to the glossary mid-task.
    - Name input is a searchable `<input list="">` + `<datalist>` (native
      browser autocomplete; no JS dependency). Single input across all
      kinds keeps the form simple — the active kind's name list seeds
      the suggestions.
    - Date quick-pick chips render below the form as anchor links that
      carry the current (kind, name) forward + swap in the chip's
      window. Server-side date math, no JS.
    """
    rail_names = sorted(str(r.name) for r in instance.rails)
    template_names = sorted(str(t.name) for t in instance.transfer_templates)
    chain_parents = sorted({str(c.parent) for c in instance.chains})

    # The active kind's universe seeds the datalist suggestions.
    if kind == "rail":
        active_names = rail_names
    elif kind == "transfer_template":
        active_names = template_names
    else:
        active_names = chain_parents
    datalist_options = "\n      ".join(
        f'<option value="{escape(n)}">' for n in active_names
    )

    def _checked(k: ProbeKind) -> str:
        return ' checked' if kind == k else ''

    def _radio_row(k: str, label: str) -> str:
        defn = _PROBE_KIND_DEFINITIONS[k]
        return (
            '<label class="flex items-start gap-2 mb-2 last:mb-0 text-sm">'
            f'<input type="radio" name="kind" value="{escape(k)}" '
            f'data-test-kind="{escape(k)}" class="mt-1"{_checked(cast(ProbeKind, k))}>'
            '<span>'
            f'<span class="font-semibold">{escape(label)}</span>'
            f'<span class="block text-xs text-secondary-fg mt-0.5">{escape(defn)}</span>'
            '</span>'
            '</label>'
        )

    radio_rows = "\n      ".join((
        _radio_row("rail", "Rail"),
        _radio_row("transfer_template", "Transfer Template"),
        _radio_row("chain", "Chain"),
    ))

    # Quick-pick chips — anchor links that carry the picker forward.
    today = date.today()  # typing-smell: ignore[no-datetime-now]: chip date math anchored to operator-facing today; no UTC subtlety
    chip_blocks: list[str] = []
    for chip_label, days in _PROBE_DATE_CHIPS:
        chip_from, chip_to = _chip_window_dates(today, days)
        chip_qs = (
            f"kind={quote(kind, safe='')}&name={quote(name, safe='')}"
            f"&date_from={chip_from.isoformat()}&date_to={chip_to.isoformat()}"
        )
        active = (
            chip_from == date_from and chip_to == date_to
        )
        chip_classes = (
            "bg-accent text-accent-fg border-accent"
            if active
            else "bg-white text-secondary-fg border-surface-border hover:border-accent"
        )
        chip_blocks.append(
            f'<a class="inline-block px-3 py-1 rounded-full border text-xs '
            f'no-underline {chip_classes}" '
            f'href="/etl/probe?{chip_qs}" '
            f'data-test-date-chip="{escape(chip_label)}">{escape(chip_label)}</a>'
        )
    chips_html = "".join(chip_blocks)

    return f"""
  <form method="get" action="/etl/probe" class="px-8 pt-6 pb-3 bg-white border-b border-surface-border">
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mb-3">
      Pick a slice of the L2 to probe. Side-by-side view shows L2-
      declared column expectations next to the runtime rows that match.
    </p>
    <fieldset class="border border-surface-border rounded-md p-3 mb-3" id="probe-kind-fieldset">
      <legend class="text-xs uppercase tracking-wide text-secondary-fg px-1">Slice kind</legend>
      {radio_rows}
    </fieldset>
    <div class="flex flex-wrap items-end gap-4 mb-3">
      <label class="block">
        <span class="block text-xs uppercase tracking-wide text-secondary-fg mb-1">Name</span>
        <input list="probe-name-suggestions" name="name" value="{escape(name)}"
               id="probe-name-input"
               class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white min-w-[20rem]"
               placeholder="Start typing to search…"
               autocomplete="off">
        <datalist id="probe-name-suggestions">
      {datalist_options}
        </datalist>
      </label>
      <label class="block">
        <span class="block text-xs uppercase tracking-wide text-secondary-fg mb-1">From</span>
        <input type="date" name="date_from" value="{date_from.isoformat()}" class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white">
      </label>
      <label class="block">
        <span class="block text-xs uppercase tracking-wide text-secondary-fg mb-1">To</span>
        <input type="date" name="date_to" value="{date_to.isoformat()}" class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white">
      </label>
      <button type="submit" class="px-3 py-1 bg-accent text-accent-fg rounded-sm border border-accent text-sm hover:opacity-85">Apply</button>
    </div>
    <div class="flex flex-wrap items-center gap-2 mb-1" id="probe-date-chips">
      <span class="text-xs uppercase tracking-wide text-secondary-fg">Quick window:</span>
      {chips_html}
    </div>
    <p class="text-xs text-secondary-fg m-0 mt-2">
      Window defaults to <strong>All time</strong> (1900-01-01 →
      today). Pick a chip or set the date inputs to narrow.
    </p>
  </form>
"""


def _render_probe_empty_initial() -> str:
    """Empty-state copy shown before the operator picks a name."""
    return """
  <section class="px-8 py-10 text-center text-secondary-fg" id="probe-empty-initial">
    <p class="text-sm m-0">
      Pick a slice above to see L2-declared expectations alongside
      observed runtime rows.
    </p>
  </section>
"""


async def _render_probe_body(
    *,
    contracts: ColumnContracts,
    kind: ProbeKind, name: str,
    date_from: date, date_to: date,
    db_pool: AsyncConnectionPool | None,
    prefix: str,
    dialect: Dialect | None,
) -> str:
    """Side-by-side: contract (left) + observed rows (right)."""
    contract_html = _render_probe_contract_panel(contracts, kind=kind, name=name)
    if db_pool is None or dialect is None:
        observed_html = (
            '<section class="p-6 text-sm text-secondary-fg">'
            '<p class="m-0"><strong>No DB pool wired.</strong> '
            'The Probe needs a connection to <code>'
            f'{escape(prefix)}_transactions</code> to read observed '
            'rows. Run Studio against the demo DB to see live data.</p>'
            '</section>'
        )
    else:
        result = await fetch_probe_rows(
            db_pool, prefix,
            kind=kind, name=name,
            date_from=date_from, date_to=date_to,
            dialect=dialect,
        )
        observed_html = _render_probe_observed_panel(
            result, contracts=contracts, kind=kind, name=name,
            date_from=date_from, date_to=date_to,
        )
    return f"""
  <section class="grid grid-cols-1 lg:grid-cols-2 gap-0 border-t border-surface-border" id="probe-body">
    <div class="border-r border-surface-border bg-white px-6 py-4" id="probe-contract-panel">
      <h2 class="text-base font-semibold m-0 mb-3">Expected (from L2)</h2>
      {contract_html}
    </div>
    <div class="bg-surface-bg px-6 py-4" id="probe-observed-panel">
      <h2 class="text-base font-semibold m-0 mb-3">Observed (window)</h2>
      {observed_html}
    </div>
  </section>
"""


def _render_probe_contract_panel(
    contracts: ColumnContracts, *, kind: ProbeKind, name: str,
) -> str:
    """Per-kind contract listing for the picked entity.

    Rail / TransferTemplate: one contract (or "not declared" message).
    Chain: zero-or-more chain-edge contracts whose parent matches the
    picked name (one parent can have multiple children → multiple
    edges).
    """
    if kind == "rail":
        rail_matches = [rc for rc in contracts.rails if str(rc.rail_name) == name]
        if not rail_matches:
            return _probe_no_such_entity("rail", name)
        return _render_rail_contract(rail_matches[0])
    if kind == "transfer_template":
        tmpl_matches = [
            tc for tc in contracts.templates if str(tc.template_name) == name
        ]
        if not tmpl_matches:
            return _probe_no_such_entity("transfer template", name)
        return _render_template_contract(tmpl_matches[0])
    # chain
    edges = [e for e in contracts.chain_edges if str(e.parent) == name]
    if not edges:
        return _probe_no_such_entity("chain parent", name)
    return _render_chain_contracts(edges)


def _probe_no_such_entity(kind_label: str, name: str) -> str:
    return (
        '<p class="text-sm text-warning m-0">'
        f'No {escape(kind_label)} named <code>{escape(name)}</code> in '
        'this L2. Pick a name from the dropdown.</p>'
    )


def _render_rail_contract(rc: RailContract) -> str:
    rows: list[str] = [
        _contract_row("rail_name", "=", str(rc.selector.equals)),
    ]
    for pred in rc.predicates:
        rows.append(_contract_row(pred.column, *_predicate_op_value(pred)))
    return _contract_table(rows) + _editor_link(rc.editor_path)


def _render_template_contract(tc: TemplateContract) -> str:
    rows: list[str] = [
        _contract_row("template_name", "=", str(tc.selector.equals)),
    ]
    for pred in tc.predicates:
        rows.append(_contract_row(pred.column, *_predicate_op_value(pred)))
    return _contract_table(rows) + _editor_link(tc.editor_path)


def _render_chain_contracts(edges: list[ChainEdgeContract]) -> str:
    """Chain parent may have N child edges; render one block per child.

    BTa.5 — surfaces a "View arrow diagram" side-panel trigger per
    parent so the operator can see the parent → child shape without
    bouncing to /diagram. Trigger renders once per parent (not per
    edge, since all edges share a parent in this call).
    """
    blocks: list[str] = []
    # All edges in this call share the same parent (caller filters by
    # parent in `_render_probe_contract_panel`). Surface the side-panel
    # trigger once at the top.
    parent = str(edges[0].parent) if edges else ""
    if parent:
        trigger = render_side_panel_trigger(
            f"/studio/side-panel/chain/{quote(parent, safe='')}",
            label="↗ View arrow diagram",
            aria_label=f"View arrow diagram for chain {parent}",
            extra_classes="text-xs mb-3",
        )
        blocks.append(
            '<p class="m-0 mb-2" data-test-chain-arrow-trigger>'
            f'{trigger}</p>'
        )
    for edge in edges:
        rows: list[str] = [
            _contract_row("parent", "=", str(edge.parent)),
            _contract_row("child", "=", str(edge.child)),
            _contract_row(
                "kind", "=",
                "Required (singleton)" if edge.is_singleton else "XOR sibling",
            ),
        ]
        if edge.fan_in:
            count = (
                str(edge.expected_parent_count)
                if edge.expected_parent_count is not None
                else "(unbounded)"
            )
            rows.append(_contract_row("fan_in", "=", f"N:1 (parents/child: {count})"))
        for pred in edge.predicates:
            rows.append(_contract_row(pred.column, *_predicate_op_value(pred)))
        blocks.append(
            _contract_table(rows) + _editor_link(edge.editor_path)
            + '<hr class="my-3 border-surface-border">'
        )
    return "".join(blocks)


def _contract_row(column: str, op: str, value: str) -> str:
    return (
        '<tr>'
        f'<td class="px-2 py-1 font-mono text-xs">{escape(column)}</td>'
        f'<td class="px-2 py-1 text-xs text-secondary-fg">{escape(op)}</td>'
        f'<td class="px-2 py-1 font-mono text-xs">{escape(value)}</td>'
        '</tr>'
    )


def _contract_table(rows: list[str]) -> str:
    return (
        '<table class="w-full mb-2 border-collapse">'
        '<thead>'
        '<tr class="text-left text-xs uppercase tracking-wide text-secondary-fg border-b border-surface-border">'
        '<th class="px-2 py-1">Column</th>'
        '<th class="px-2 py-1">Op</th>'
        '<th class="px-2 py-1">Expected</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _editor_link(path: str) -> str:
    return (
        '<p class="text-xs m-0">'
        f'<a class="text-accent hover:underline" href="{escape(path)}">→ Edit in L2</a>'
        '</p>'
    )


def _predicate_op_value(pred: ColumnPredicate) -> tuple[str, str]:
    """Display ``(operator, value)`` strings for a ColumnPredicate."""
    if pred.kind == "equals":
        return ("=", str(pred.expected))
    if pred.kind == "one_of":
        values = cast(tuple[str, ...], pred.expected)
        return ("∈", "{" + ", ".join(values) + "}")
    # not_null
    return ("≠", "NULL")


def _render_probe_observed_panel(
    result: ProbeResult, *,
    contracts: ColumnContracts,
    kind: ProbeKind, name: str,
    date_from: date, date_to: date,
) -> str:
    """Right panel: observed rows table with per-cell ✓/✗ where the
    contract predicates apply."""
    window_label = (
        f"{date_from.isoformat()} → {date_to.isoformat()}"
    )
    if result.total_count == 0:
        return _render_probe_empty_observed(window_label)

    showing = len(result.rows)
    header = (
        '<p class="text-xs text-secondary-fg m-0 mb-3">'
        f'Showing <strong>{showing}</strong> of <strong>{result.total_count:,}</strong> '
        f'rows in window {escape(window_label)}'
        '</p>'
    )

    # Resolve the predicate set applicable to this slice once; reuse
    # per-row for evaluation.
    predicates = _predicates_for_slice(contracts, kind=kind, name=name)

    body_rows: list[str] = []
    for row in result.rows:
        body_rows.append(_render_observed_row(row, predicates))
    table = (
        '<table class="w-full border-collapse text-xs">'
        '<thead>'
        '<tr class="text-left uppercase tracking-wide text-secondary-fg border-b border-surface-border">'
        '<th class="px-2 py-1">Transaction</th>'
        '<th class="px-2 py-1">Posting</th>'
        '<th class="px-2 py-1">Rail / Template</th>'
        '<th class="px-2 py-1">Role / Direction</th>'
        '<th class="px-2 py-1">Predicate fit</th>'
        '</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table>'
    )
    legend = (
        '<p class="text-xs text-secondary-fg m-0 mt-3">'
        'Predicate fit: <span class="text-success">✓</span> matches L2, '
        '<span class="text-danger">✗</span> contradicts, '
        '<span class="text-secondary-fg">—</span> no value to evaluate.'
        '</p>'
    )
    return header + table + legend


def _render_observed_row(
    row: ProbeRow, predicates: tuple[ColumnPredicate, ...],
) -> str:
    """One row in the observed-rows table."""
    pass_count = 0
    fail_count = 0
    skip_count = 0
    for pred in predicates:
        result = evaluate_predicate(pred, row)
        if result is True:
            pass_count += 1
        elif result is False:
            fail_count += 1
        else:
            skip_count += 1

    rail_or_tmpl = (
        f'{escape(row.rail_name or "—")}'
        + (f' / {escape(row.template_name)}' if row.template_name else "")
    )
    role_dir = (
        f'{escape(row.account_role or "—")} / {escape(row.amount_direction)}'
    )
    fit = (
        f'<span class="text-success">{pass_count}✓</span> '
        f'<span class="text-danger">{fail_count}✗</span> '
        f'<span class="text-secondary-fg">{skip_count}—</span>'
    )
    return (
        '<tr class="border-b border-surface-border">'
        f'<td class="px-2 py-1 font-mono">{escape(row.transaction_id)}</td>'
        f'<td class="px-2 py-1 font-mono">{escape(row.posting.isoformat())}</td>'
        f'<td class="px-2 py-1 font-mono">{rail_or_tmpl}</td>'
        f'<td class="px-2 py-1">{role_dir}</td>'
        f'<td class="px-2 py-1">{fit}</td>'
        '</tr>'
    )


def _render_probe_empty_observed(window_label: str) -> str:
    return f"""
    <div class="border border-dashed border-surface-border rounded-md p-6 text-center text-sm">
      <p class="m-0 mb-2"><strong>No rows match this slice.</strong></p>
      <p class="m-0 mb-2 text-secondary-fg">
        The L2 declares this rail / template / chain but the ETL hook
        hasn't produced any matching rows in the window {escape(window_label)}.
      </p>
      <ul class="text-left text-secondary-fg max-w-xl mx-auto list-disc list-inside m-0 mb-0">
        <li>Widen the window — backfill / historical loads may live outside today's default.</li>
        <li>Check <a href="/etl/run" class="text-accent hover:underline">Run + coverage</a> to see when the last ETL ran.</li>
        <li>If the last run was recent, this slice may be a real ETL gap. Open <a href="/etl/triage" class="text-accent hover:underline">Triage</a>.</li>
      </ul>
    </div>
"""


def _predicates_for_slice(
    contracts: ColumnContracts, *, kind: ProbeKind, name: str,
) -> tuple[ColumnPredicate, ...]:
    """Return the predicate set BT.5 derived for the picked entity.

    Rail / TransferTemplate: the matched entity's ``predicates`` tuple.
    Chain: the union across every edge of the matched parent (one
    parent may have N children; the per-row fit count aggregates).
    """
    if kind == "rail":
        for rc in contracts.rails:
            if str(rc.rail_name) == name:
                return tuple(rc.predicates)
        return ()
    if kind == "transfer_template":
        for tc in contracts.templates:
            if str(tc.template_name) == name:
                return tuple(tc.predicates)
        return ()
    # chain — union predicates across all matching edges.
    preds: list[ColumnPredicate] = []
    for edge in contracts.chain_edges:
        if str(edge.parent) == name:
            preds.extend(edge.predicates)
    return tuple(preds)


def _validate_probe_kind(value: str | None) -> ProbeKind:
    """Coerce the URL's ?kind= to a typed ProbeKind; default 'rail'."""
    if value == "transfer_template":
        return "transfer_template"
    if value == "chain":
        return "chain"
    return "rail"


def _parse_iso_date(value: str | None) -> date | None:
    """Parse YYYY-MM-DD; tolerate empty / malformed by returning None."""
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# -- BT.3 — /studio/etl/run page --------------------------------------------


async def _render_etl_run_page(
    cache: L2InstanceCache,
    dev_log: bool,
    *,
    last_summary: DeploySummary | None,
    last_run_at: datetime | None,
    db_pool: AsyncConnectionPool | None,
    dialect: Dialect | None,
    prefix_override: str | None,
    cfg: Config | None = None,
    top_nav_html: str = "",
    just_ran: bool = False,
    is_running: bool = False,
) -> str:
    """BT.3 — ``/etl/run`` ETL execution + coverage report page.

    Renders:
      1. Run-ETL form (POSTs to /etl/run).
      2. Last-run banner (status + duration + halt reason when halted).
      3. Last-run event log (when a run has happened).
      4. Coverage cards (rails / templates / chains via coverage_for;
         metadata via metadata_coverage_per_template).
      5. Empty-state when no run has happened AND no rows exist.

    BTa.9 — when ``is_running`` is True, a live-tail container
    mounts above the static log + polls `/etl/run/stream?since=N`
    for new events every second. A "Cancel" form replaces the
    "Refresh Data" button while the task is in flight.

    The "run" state is closure-scope (single Studio user, single
    process); restart clears it.
    """
    instance = cache.get()
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    demo_banner = _banner(cfg)
    standalone_banner = _standalone_mode_banner(cfg)
    prefix = (
        prefix_override
        if prefix_override is not None
        else (cfg.db_table_prefix if cfg is not None else cache.path.stem)
    )

    run_form_html = _render_etl_run_form(
        last_summary=last_summary,
        last_run_at=last_run_at,
        etl_hook_command=cfg.etl_hook if cfg is not None else None,
        deployment_name=cfg.deployment_name if cfg is not None else None,
        dialect_label=cfg.dialect.value if cfg is not None else None,
        demo_gaps_planted=cfg is not None and cfg.etl_hook is None,
        is_running=is_running,
    )
    log_html = _render_etl_run_log(last_summary)
    live_tail_html = _render_etl_live_tail_mount() if is_running else ""
    coverage_html = await _render_etl_coverage_section(
        db_pool=db_pool, dialect=dialect,
        prefix=prefix, instance=instance,
        last_summary=last_summary,
    )

    # BTa.6 — `?just_ran=1` triggers a transient flash on the
    # coverage section + a 5s tab-title pulse so an operator
    # multi-tasking in another tab gets a visual nudge that the
    # refresh finished.
    flash_styles = "" if not just_ran else """
    <style>
      @keyframes etlRunFlash {
        0% { background-color: rgba(34, 197, 94, 0.18); }
        100% { background-color: transparent; }
      }
      #etl-coverage { animation: etlRunFlash 2s ease-out; }
    </style>
"""
    flash_script = "" if not just_ran else f"""
    <script>
    (function() {{
      const original = document.title;
      document.title = '✓ Done · ' + original;
      setTimeout(() => {{ document.title = original; }}, 5000);
    }})();
    </script>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · ETL · Refresh Data</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  {flash_styles}
  {devlog_script}</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}{standalone_banner}
  {_render_etl_sub_nav("/etl/run")}
  <header class="px-8 py-4 border-b border-surface-border bg-white">
    <h1 class="text-xl font-semibold m-0">Refresh Data</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      Re-run the bundled deploy pipeline (schema + seed + matview
      refresh + dashboard regeneration) against the demo database.
    </p>
  </header>
  <main>
  {run_form_html}
  {live_tail_html}
  {log_html}
  {coverage_html}
  </main>
  {flash_script}
</body>
</html>
"""


def _render_etl_run_form(
    *,
    last_summary: DeploySummary | None,
    last_run_at: datetime | None,
    etl_hook_command: str | None = None,
    deployment_name: str | None = None,
    dialect_label: str | None = None,
    demo_gaps_planted: bool = False,
    is_running: bool = False,
) -> str:
    """Refresh-Data button + last-run status sidebar.

    BTa.2 P1.3 — last-run banner now surfaces hook attribution
    (the command that ran + bundled-demo distinction + exit code)
    so first-time operators can tell whether THEIR hook ran or
    the bundled demo placeholder. ``etl_hook_command`` is
    ``cfg.etl_hook`` (None → bundled demo hook ran).

    BTa.8 cold-read v3 — ``deployment_name`` + ``dialect_label`` +
    ``demo_gaps_planted`` feed a "What clicking Refresh Data will
    do" context strip above the button (cold-read finding: the
    button didn't tell the operator which DB they were about to
    wipe + repopulate). When all three are absent, the context
    strip is skipped (unit-test surface).
    """
    if last_summary is None:
        status_html = (
            '<p class="text-sm text-secondary-fg m-0">No runs yet.</p>'
        )
    else:
        ts = last_run_at.isoformat(timespec="seconds") if last_run_at else "—"
        hook_attr = _format_hook_attribution(etl_hook_command)
        if last_summary.halted:
            exit_code = last_summary.step1_etl_hook_exit_code
            status_html = (
                '<p class="text-sm m-0 mb-1">'
                f'<span class="text-danger font-semibold">● HALTED</span> '
                f'at {ts}</p>'
                f'<p class="text-xs text-secondary-fg m-0 mb-1">'
                f'reason: <code>{escape(last_summary.halt_reason or "—")}</code></p>'
                f'<p class="text-xs text-secondary-fg m-0 mb-1">'
                f'hook: {hook_attr}</p>'
                f'<p class="text-xs text-secondary-fg m-0">'
                f'exit code: <code>{exit_code}</code></p>'
            )
        else:
            tx_after = last_summary.step3_generator_transactions_after
            status_html = (
                '<p class="text-sm m-0 mb-1">'
                f'<span class="text-success font-semibold">● success</span> '
                f'at {ts}</p>'
                f'<p class="text-xs text-secondary-fg m-0 mb-1">'
                f'gen {last_summary.step5_data_generation_id} · '
                f'{tx_after:,} transactions</p>'
                f'<p class="text-xs text-secondary-fg m-0">'
                f'hook: {hook_attr}</p>'
            )
    context_html = _render_refresh_context_strip(
        deployment_name=deployment_name,
        dialect_label=dialect_label,
        etl_hook_command=etl_hook_command,
        demo_gaps_planted=demo_gaps_planted,
    )
    # BTa.9 — swap button per run state.
    # BTb.4 — Cancel button now ships hover-tooltip + clarifying copy
    # underneath. No modal (operator-locked: friction-heavy). Partial
    # state is intentional — aids troubleshooting AND the next refresh
    # wipes it automatically.
    if is_running:
        action_html = """
    <div class="flex flex-col gap-2">
      <form method="post" action="/etl/run/cancel">
        <button type="submit" id="etl-run-cancel-btn"
                class="px-4 py-2 bg-danger text-white rounded-sm border border-danger text-sm font-semibold hover:opacity-85"
                title="Stops pipeline immediately. Partial DB state stays until the next Refresh Data wipes it (intentional — aids troubleshooting). Subprocess hooks may keep running until they exit.">
          ✕ Cancel run
        </button>
      </form>
      <p class="text-xs text-secondary-fg max-w-md m-0"
         data-test-cancel-help>
        Stops the pipeline immediately. Partial DB state stays until
        the next Refresh Data wipes it — we don't auto-clean to help
        with troubleshooting. Subprocess hooks may keep running until
        they exit.
      </p>
    </div>
    <div class="text-sm text-secondary-fg">
      <span class="inline-block w-2 h-2 rounded-full bg-warning animate-pulse mr-2"></span>
      <span data-test-running-indicator>Pipeline running — live events below.</span>
    </div>
"""
    else:
        action_html = f"""
    <form method="post" action="/etl/run">
      <button type="submit" id="etl-run-btn" class="px-4 py-2 bg-accent text-accent-fg rounded-sm border border-accent text-sm font-semibold hover:opacity-85">
        ↻ Refresh Data
      </button>
    </form>
    <div id="etl-last-run-status">
      {status_html}
    </div>
"""
    return f"""
  {context_html}
  <section class="flex items-center gap-6 px-8 pt-6 pb-3 bg-white border-b border-surface-border">
    {action_html}
  </section>
"""


def _render_refresh_context_strip(
    *,
    deployment_name: str | None,
    dialect_label: str | None,
    etl_hook_command: str | None,
    demo_gaps_planted: bool,
) -> str:
    """BTa.8 cold-read v3 — "What clicking Refresh Data will do" strip.

    Renders a 3-fact context panel above the button: which
    deployment + dialect the wipe lands on, and which hook (or
    bundled-demo path) will run. Cold-read finding: the previous
    page header showed only the deployment_name in a small chip
    next to a redundant title; now it lives above the action
    button where the operator's eye is when they're about to click.

    Returns empty string when nothing to show (unit-test surface
    that builds the form without cfg context).
    """
    if not deployment_name and not dialect_label and not etl_hook_command:
        return ""
    facts: list[str] = []
    if deployment_name:
        facts.append(
            '<div><dt class="text-xs uppercase tracking-wide text-secondary-fg">'
            'Deployment</dt>'
            f'<dd class="m-0 font-mono text-sm">{escape(deployment_name)}</dd></div>'
        )
    if dialect_label:
        facts.append(
            '<div><dt class="text-xs uppercase tracking-wide text-secondary-fg">'
            'Dialect</dt>'
            f'<dd class="m-0 font-mono text-sm">{escape(dialect_label)}</dd></div>'
        )
    if etl_hook_command:
        facts.append(
            '<div><dt class="text-xs uppercase tracking-wide text-secondary-fg">'
            'ETL hook</dt>'
            f'<dd class="m-0 font-mono text-sm break-all">'
            f'<code>{escape(etl_hook_command)}</code></dd></div>'
        )
    else:
        gap_note = (
            ' <span class="text-warning text-xs">+ demo gap overlay '
            '(phantom rail / template / missing metadata + uncovered '
            'rail/template DELETEs)</span>'
            if demo_gaps_planted else ""
        )
        facts.append(
            '<div><dt class="text-xs uppercase tracking-wide text-secondary-fg">'
            'ETL hook</dt>'
            '<dd class="m-0 text-sm">'
            '<em class="text-secondary-fg">(none configured — bundled demo '
            f'regeneration will run){gap_note}</em></dd></div>'
        )
    return (
        '<section class="px-8 pt-4 pb-2 bg-surface-bg border-b border-surface-border" '
        'data-test-refresh-context>'
        '<p class="text-xs uppercase tracking-wide text-secondary-fg m-0 mb-2">'
        'What clicking <strong>↻ Refresh Data</strong> will do</p>'
        '<dl class="grid grid-cols-1 sm:grid-cols-3 gap-4 m-0">'
        + "".join(facts) +
        '</dl>'
        '</section>'
    )


def _render_etl_live_tail_mount() -> str:
    """BTa.9 — mount point for the live-tail HTML fragment.

    The empty wrapper div has htmx attrs that fire an immediate
    `hx-get` of `/etl/run/stream?since=0` on `load`, then continue
    polling every 1s while the run is in flight. When the stream
    endpoint detects the task finished, it emits `HX-Trigger:
    etl-run-finished` which the inline script catches + navigates
    to `/etl/run?just_ran=1` so the final summary + flash render.
    """
    return """
  <section class="px-8 pt-3 pb-3 bg-surface-bg border-b border-surface-border"
           id="etl-run-live-tail-wrap">
    <h2 class="text-base font-semibold m-0 mb-2">Live event tail</h2>
    <div id="etl-run-live-tail"
         class="bg-white border border-surface-border rounded-md p-3 max-h-72 overflow-y-auto font-mono text-xs"
         hx-get="/etl/run/stream"
         hx-trigger="load, every 1s"
         hx-swap="outerHTML">
      <p class="text-secondary-fg italic">Waiting for events…</p>
    </div>
  </section>
  <script>
  (function() {
    document.body.addEventListener('etl-run-finished', function() {
      // Brief delay so the last fragment swap completes visually.
      setTimeout(() => { window.location.href = '/etl/run?just_ran=1'; }, 250);
    });
  })();
  </script>
"""


def _render_etl_live_tail_fragment(
    *,
    all_events: list[Mapping[str, object]],
    running: bool,
) -> str:
    """BTa.9 — the live-tail polling response.

    Returns the SAME `<div id="etl-run-live-tail">` wrapper (so
    htmx's `outerHTML` swap is self-replacing) with the FULL
    accumulated event list re-rendered. Each poll's response
    replaces the wrapper entirely; delta-only responses would
    clobber the accumulating history. When the run is still in
    flight we arm the next poll; when it's done we leave the
    wrapper inert and the caller emits
    ``HX-Trigger: etl-run-finished``.
    """
    event_html = "".join(
        _format_live_event_line(event) for event in all_events
    )
    if not all_events:
        event_html = (
            '<p class="text-secondary-fg italic" data-test-live-tail-empty>'
            'Waiting for events…</p>'
        )
    poll_attrs = ""
    state_attr = "finished"
    if running:
        poll_attrs = (
            ' hx-get="/etl/run/stream"'
            ' hx-trigger="every 1s"'
            ' hx-swap="outerHTML"'
        )
        state_attr = "running"
    return (
        '<div id="etl-run-live-tail"'
        ' class="bg-white border border-surface-border rounded-md p-3 max-h-72 overflow-y-auto font-mono text-xs"'
        f' data-test-live-tail-state="{state_attr}"'
        f' data-test-live-tail-count="{len(all_events)}"'
        f'{poll_attrs}>'
        f'{event_html}'
        '</div>'
    )


def _format_live_event_line(event: Mapping[str, object]) -> str:
    """BTa.9 — one event → one log line (Δms is omitted in the live
    tail because we don't know the prior event's timestamp from a
    single fragment; the static log renders deltas at end-of-run)."""
    event_name = str(event.get("event") or event.get("kind") or "")
    if event_name.endswith(":halt") or event_name == "deploy:cancelled":
        level = "error"
        level_class = "text-danger"
    elif event_name.endswith(":skip"):
        level = "warn"
        level_class = "text-warning"
    else:
        level = "info"
        level_class = "text-secondary-fg"
    suffix_bits: list[str] = []
    for k, v in event.items():
        if k in ("event", "kind", "ts_unix"):
            continue
        suffix_bits.append(f"{escape(str(k))}={escape(str(v))}")
    suffix = " " + " ".join(suffix_bits) if suffix_bits else ""
    return (
        f'<div class="leading-relaxed" data-test-live-event-level="{level}">'
        f'<span class="{level_class} mr-2 uppercase text-[10px]">[{level}]</span>'
        f'{escape(event_name)}{suffix}'
        '</div>'
    )


def _format_hook_attribution(etl_hook_command: str | None) -> str:
    """Hook label for the last-run status banner.

    When the operator hasn't configured ``cfg.etl_hook``, the
    deploy ran the bundled demo regen — make that visible so
    "I ran ETL but my data isn't here" gets a faster diagnosis.
    """
    if not etl_hook_command:
        return "<em>(bundled demo regeneration — no operator hook configured)</em>"
    return f"<code>{escape(etl_hook_command)}</code>"


def _render_etl_run_log(last_summary: DeploySummary | None) -> str:
    """Per-step event log from the last run. Empty when no run yet.

    BTa.6 — renders per-event timing delta (Δms from prior event)
    + an inferred level token (info/warn/error). Level is derived
    from the event name suffix: ``*:halt`` ⇒ error, ``*:skip`` ⇒
    warn, anything else ⇒ info. Color-coded so the operator can
    scan for the first non-info event when triaging a failure.
    """
    if last_summary is None or not last_summary.events:
        return ""
    log_lines: list[str] = []
    prior_ts: float | None = None
    for event in last_summary.events:
        # Pull and remove the timestamp before formatting so it
        # renders as Δms instead of as a raw key=value.
        raw_ts = event.get("ts_unix")
        ts_unix: float | None = (
            float(raw_ts) if isinstance(raw_ts, (int, float)) else None
        )
        # `event` is the human label (e.g. deploy:step1:done);
        # legacy events used the `kind` key — support both.
        event_name = str(event.get("event") or event.get("kind") or "")
        # Inferred log level from the event-name suffix.
        if event_name.endswith(":halt"):
            level = "error"
            level_class = "text-danger"
        elif event_name.endswith(":skip"):
            level = "warn"
            level_class = "text-warning"
        else:
            level = "info"
            level_class = "text-secondary-fg"
        # Δms — relative to the prior event in the captured order.
        delta_html = ""
        if ts_unix is not None:
            if prior_ts is not None:
                delta_ms = int(round((ts_unix - prior_ts) * 1000))
                delta_html = (
                    f'<span class="text-secondary-fg text-[10px] mr-2">'
                    f'+{delta_ms}ms</span>'
                )
            prior_ts = ts_unix
        # Suffix: everything except `event`/`kind`/`ts_unix`.
        suffix_bits: list[str] = []
        for k, v in event.items():
            if k in ("event", "kind", "ts_unix"):
                continue
            suffix_bits.append(f"{escape(str(k))}={escape(str(v))}")
        suffix = " " + " ".join(suffix_bits) if suffix_bits else ""
        log_lines.append(
            f'<div class="font-mono text-xs leading-relaxed" '
            f'data-test-log-level="{level}">'
            f'{delta_html}'
            f'<span class="{level_class} mr-2 uppercase text-[10px]">'
            f'[{level}]</span>'
            f'{escape(event_name)}{suffix}'
            f'</div>'
        )
    return f"""
  <section class="px-8 py-3 bg-surface-bg border-b border-surface-border" id="etl-run-log">
    <h2 class="text-base font-semibold m-0 mb-2">Last-run log</h2>
    <div class="bg-white border border-surface-border rounded-md p-3 max-h-72 overflow-y-auto">
      {''.join(log_lines)}
    </div>
  </section>
"""


async def _render_etl_coverage_section(
    *,
    db_pool: AsyncConnectionPool | None,
    dialect: Dialect | None,
    prefix: str,
    instance: L2Instance,
    last_summary: DeploySummary | None,
) -> str:
    """Coverage cards section — rails / templates / chains / metadata."""
    if db_pool is None or dialect is None:
        return (
            '<section class="px-8 py-6">'
            '<p class="text-sm text-secondary-fg m-0">'
            '<strong>No DB pool wired.</strong> '
            f'Connect Studio to <code>{escape(prefix)}_transactions</code> '
            'to render coverage.</p></section>'
        )

    # BTa.8 cold-read v3 — when no Refresh Data has run THIS session,
    # don't render green ✓ marks against pre-existing data. Operators
    # were trusting stale green as a clean signal even though the
    # rows came from a prior session / CLI `data apply`. Show the
    # empty-state regardless of `total_rows`: the only meaningful
    # coverage signal is "did the operator just refresh".
    if last_summary is None:
        return """
  <section class="px-8 py-10 text-center text-secondary-fg" id="etl-coverage-empty">
    <p class="text-sm m-0 mb-2"><strong>No Refresh Data run this session.</strong></p>
    <p class="text-sm m-0 max-w-2xl mx-auto">
      Coverage only renders after a Refresh Data click in this Studio
      session — pre-existing rows from prior sessions / CLI runs aren't
      auto-trusted. Click <strong>↻ Refresh Data</strong> above to
      populate.
    </p>
  </section>
"""

    cov_map = await coverage_for(db_pool, prefix, instance, dialect=dialect)
    md_map = await metadata_coverage_per_template(
        db_pool, prefix, instance, dialect=dialect,
    )

    rails_card = _render_coverage_card_for_kind(
        "Rails",
        [(str(r.name), cov_map.by_node_id.get(_rail_id(r.name)))
         for r in instance.rails],
    )
    templates_card = _render_coverage_card_for_kind(
        "Templates",
        [(str(t.name), cov_map.by_node_id.get(_template_id(t.name)))
         for t in instance.transfer_templates],
    )
    chains_card = _render_chain_coverage_card(instance, cov_map)
    metadata_card = _render_metadata_coverage_card(instance, md_map)

    return f"""
  <section class="px-8 py-6" id="etl-coverage">
    <div class="flex items-center gap-4 mb-3">
      <h2 class="text-base font-semibold m-0">Coverage</h2>
      <label class="text-xs text-secondary-fg flex items-center gap-1 cursor-pointer">
        <input type="checkbox" id="etl-coverage-failures-only"
               data-test-failures-toggle>
        Show failures only
      </label>
    </div>
    <style>
      /* BTa.6 — when the toggle is on, hide every coverage row whose
         status is "present" (green ✓). The metadata card uses
         data-test-coverage-row-mark="text-success" for the same purpose. */
      #etl-coverage[data-failures-only="1"] li[data-coverage-status="present"],
      #etl-coverage[data-failures-only="1"] li[data-test-coverage-row-mark="text-success"] {{
        display: none;
      }}
    </style>
    <script>
    (function() {{
      const root = document.getElementById('etl-coverage');
      const toggle = document.getElementById('etl-coverage-failures-only');
      if (!root || !toggle) return;
      toggle.addEventListener('change', () => {{
        if (toggle.checked) {{
          root.setAttribute('data-failures-only', '1');
        }} else {{
          root.removeAttribute('data-failures-only');
        }}
      }});
    }})();
    </script>
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
      {rails_card}
      {templates_card}
      {chains_card}
    </div>
    {metadata_card}
    <p class="text-xs text-secondary-fg mt-4 m-0">
      Coverage report green = ETL contract satisfied.
      Not green? → <a class="text-accent hover:underline" href="/etl/triage">Open Triage</a> to see specific gaps.
    </p>
  </section>
"""


def _render_coverage_card_for_kind(
    title: str, entries: list[tuple[str, CoverageEntry | None]],
) -> str:
    """One card: title + N/M tally + per-entity ✓/✗ list.

    BTa.6 — each `<li>` ships `data-coverage-status="present|missing"`
    so the "Show failures only" toggle can hide the green rows via
    CSS attribute selector (no per-row class munging).
    """
    present_count = sum(1 for _n, e in entries if e is not None and e.present)
    total = len(entries)
    pct = (
        f" ({(present_count / total * 100):.0f}%)" if total > 0 else ""
    )
    rows: list[str] = []
    for name, entry in entries:
        is_present = entry is not None and entry.present
        mark = (
            '<span class="text-success">✓</span>' if is_present
            else '<span class="text-danger">✗</span>'
        )
        status = "present" if is_present else "missing"
        rows.append(
            f'<li class="flex justify-between gap-2 text-xs font-mono py-0.5" '
            f'data-coverage-status="{status}">'
            f'<span>{escape(name)}</span>{mark}</li>'
        )
    return f"""
      <div class="bg-white border border-surface-border rounded-md p-3" data-test-card="{escape(title.lower())}">
        <h3 class="text-sm font-semibold m-0 mb-2">{escape(title)}</h3>
        <p class="text-xs text-secondary-fg m-0 mb-2">
          <strong>{present_count}</strong> of <strong>{total}</strong> declared{pct}
        </p>
        <ul class="list-none m-0 p-0">{''.join(rows)}</ul>
      </div>
"""


def _render_chain_coverage_card(
    instance: L2Instance, cov_map: CoverageMap,
) -> str:
    """Chains card — one row per chain edge (parent → child), ✓ when
    both endpoints have data."""
    entries: list[tuple[str, CoverageEntry | None]] = []
    for chain in instance.chains:
        for child_spec in chain.children:
            edge_id = chain_edge_id(str(chain.parent), str(child_spec.name))
            entries.append((
                f"{chain.parent} → {child_spec.name}",
                cov_map.by_chain_edge_id.get(edge_id),
            ))
    return _render_coverage_card_for_kind("Chains", entries)


def _render_metadata_coverage_card(
    instance: L2Instance,
    md_map: Mapping[str, TemplateMetadataCoverage],
) -> str:
    """Metadata card: per-template required-key landing tally.

    BTa.6 — denominator now matches the displayed list. The prior
    rollup excluded 0-row templates from `total_keys` while still
    rendering them in the per-row list, producing an apparent
    denominator drift to a careful reader (cold-read trust killer:
    `headline says 3/4 but I count 5 rows`). Now every template the
    operator can see contributes its `per_key_count` to the
    denominator; 0-row templates contribute 0 to `landed_keys` so
    the math reflects the visible reality.
    """
    rows: list[str] = []
    total_keys = 0
    landed_keys = 0
    for template in instance.transfer_templates:
        cov = md_map.get(str(template.name))
        if cov is None:
            continue
        per_key_count = len(cov.per_key)
        # Every displayed template contributes its key-universe to
        # the denominator — the headline must match the visible list.
        total_keys += per_key_count
        # A key is "landed" if at least one row carries it AND the
        # template has rows. No rows → 0 landed, full denominator.
        if cov.row_count == 0:
            label = f"0/{per_key_count} keys ✗ no rows"
            mark_class = "text-danger"
            # 0 contribution to landed_keys (no rows ⇒ no keys land).
        else:
            landed = sum(
                1 for _k, count in cov.per_key.items() if count > 0
            )
            landed_keys += landed
            if landed == per_key_count:
                label = f"{landed}/{per_key_count} keys ✓"
                mark_class = "text-success"
            else:
                missing = [
                    k for k, count in cov.per_key.items() if count == 0
                ]
                label = (
                    f"{landed}/{per_key_count} keys ✗  "
                    f"missing: {', '.join(missing)}"
                )
                mark_class = "text-danger"
        rows.append(
            f'<li class="flex justify-between gap-2 text-xs font-mono py-0.5" '
            f'data-test-coverage-row-mark="{mark_class}">'
            f'<span>{escape(str(template.name))}</span>'
            f'<span class="{mark_class}">{escape(label)}</span></li>'
        )
    pct = (
        f"{(landed_keys / total_keys * 100):.0f}%"
        if total_keys > 0 else "—"
    )
    return f"""
    <div class="bg-white border border-surface-border rounded-md p-4" id="etl-coverage-metadata">
      <h3 class="text-sm font-semibold m-0 mb-2">Metadata</h3>
      <p class="text-xs text-secondary-fg m-0 mb-3">
        <strong>{landed_keys}</strong> of <strong>{total_keys}</strong>
        required metadata keys landed ({pct})
      </p>
      <ul class="list-none m-0 p-0">{''.join(rows)}</ul>
    </div>
"""


# -- BT.4 — /studio/etl/triage page ------------------------------------------


# BU.2a — labels + editor CTAs migrated to typed handbook source.
# `common.handbook.l2_triage_gaps` parses `docs/L2_Triage_Gaps.md`
# + ships `SECTION_TITLE_BY_KIND` (labels) + `EDITOR_LABEL_BY_KIND` (CTAs).
# Helpers below cache the parsed sections so render isn't paying
# parse cost per gap card.


def _gap_kind_label(kind: str) -> str:
    from recon_gen.common.handbook.l2_triage_gaps import (  # noqa: PLC0415
        SECTION_TITLE_BY_KIND,
    )
    return SECTION_TITLE_BY_KIND.get(kind, kind)


def _gap_kind_editor_label(kind: str) -> str:
    from recon_gen.common.handbook.l2_triage_gaps import (  # noqa: PLC0415
        EDITOR_LABEL_BY_KIND,
    )
    return EDITOR_LABEL_BY_KIND.get(kind, "Open editor")

# BTa.4 — per-kind visual stripe per BTa.0 Lock 3. Each kind ships
# a distinct icon SHAPE + color — accessibility-friendly (not
# color-only) and operator-recognizable across pages. Kind order
# is the canonical accordion render order (most-actionable first).
_GAP_KIND_RENDER_ORDER: tuple[str, ...] = (
    "unmatched_rail",
    "unmatched_template",
    "missing_limit_schedule",
    "missing_metadata_key",
)

_GAP_KIND_ICONS: Mapping[str, str] = {
    "unmatched_rail": "⊘",
    "unmatched_template": "⚠",
    "missing_limit_schedule": "⊠",
    "missing_metadata_key": "⊟",
}

# Tailwind border-l-4 color tokens per gap kind. Distinct enough to
# pattern-match across the page without the operator reading the
# label every time, while staying inside the brand palette.
_GAP_KIND_STRIPES: Mapping[str, str] = {
    "unmatched_rail": "border-l-4 border-l-warning",
    "unmatched_template": "border-l-4 border-l-amber-500",
    "missing_limit_schedule": "border-l-4 border-l-orange-500",
    "missing_metadata_key": "border-l-4 border-l-danger",
}


async def _render_etl_triage_page(
    cache: L2InstanceCache,
    dev_log: bool,
    *,
    db_pool: AsyncConnectionPool | None,
    dialect: Dialect | None,
    prefix_override: str | None,
    cfg: Config | None = None,
    top_nav_html: str = "",
) -> str:
    """BT.4 — ``/etl/triage`` exception triage page.

    Runs ``detect_gaps`` against the cached L2 + the demo DB; renders
    one decision card per gap with the diagnosis prose + evidence +
    deep link to the editor (link-only v1 per BT.0 lock 5).
    Empty-state when no gaps detected.
    """
    instance = cache.get()
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    demo_banner = _banner(cfg)
    standalone_banner = _standalone_mode_banner(cfg)
    prefix = (
        prefix_override
        if prefix_override is not None
        else (cfg.db_table_prefix if cfg is not None else cache.path.stem)
    )

    if db_pool is None or dialect is None:
        body_html = (
            '<section class="px-8 py-10 text-center text-secondary-fg">'
            '<p class="m-0"><strong>No DB pool wired.</strong> '
            f'Connect Studio to <code>{escape(prefix)}_transactions</code> '
            'to run the gap detector.</p></section>'
        )
    else:
        contracts = derive_column_contracts(instance)
        try:
            gaps = await detect_gaps(
                db_pool, prefix, instance, contracts, dialect=dialect,
            )
        except Exception as exc:  # noqa: BLE001 — broad: db drivers raise dialect-specific exceptions
            # BTa.4 — transient HTTP 500 retry-on-lock-busy. The gap
            # detector queries materialized views that the deploy
            # pipeline rebuilds; landing on Triage during a refresh
            # used to surface as a raw 500. Detect the "recomputing"
            # shape (lock contention / temp-table missing / etc.) and
            # render a friendly retry prompt instead of crashing.
            body_html = _render_triage_recomputing_banner(exc)
        else:
            body_html = _render_triage_body(gaps)

    # BTb.3 — when on the bundled-demo path (no operator hook), echo
    # the Run-page demo-overlay disclosure here so the operator
    # doesn't panic at 4,400 "Missing LimitSchedule" rows + decide
    # the L2 is broken. Real-hook deployments skip this banner.
    demo_plant_banner = ""
    if cfg is not None and cfg.etl_hook is None:
        demo_plant_banner = """
  <aside class="mx-8 mt-6 mb-2 bg-accent/5 border border-accent/30 rounded-md px-4 py-3 text-sm"
         data-test-triage-demo-plant-banner role="status">
    <strong class="text-accent">ⓘ Bundled-demo data.</strong>
    Some gaps below are intentional demo plants (rows tagged
    <code>__demo_gap_*</code>) so this page has content to demo.
    With a real ETL hook configured (set <code>cfg.etl_hook</code>),
    only your real gaps surface.
  </aside>
"""

    # BV.7 Surface 4 (BU.0 Lock 11.4) — dynamic plant banner. Reads
    # the trainer KV (``<v>_config_kv['trainer_applied_plants']``)
    # at request time + intersects against the L2-side registry
    # categories so the operator sees which L2 kinds are currently
    # planted. ``read_applied_state`` returns ``{}`` on any failure
    # (no v overlay yet, DB down, parse fail) — the renderer's
    # empty-state branch covers all of those cleanly. Plumbed below
    # the demo-plant disclosure so the order is:
    #   1. "bundled-demo data" disclaimer (static, cfg-driven)
    #   2. "currently planted" summary (dynamic, KV-driven)
    # → demo disclaimer answers "why are there ANY rows?", plant
    # banner answers "which of MY plants is firing right now?".
    from recon_gen.common.html._plant_banner import (  # noqa: PLC0415
        render_plant_banner,
    )
    from recon_gen.common.l2.v_overlay import (  # noqa: PLC0415
        read_applied_state,
    )

    plant_banner_html = ""
    if cfg is not None:
        applied_state = await read_applied_state(cfg)
        plant_banner_html = render_plant_banner(applied_state)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · ETL · Triage</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  {devlog_script}</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}{standalone_banner}
  {_render_etl_sub_nav("/etl/triage")}
  <header class="px-8 py-4 border-b border-surface-border bg-white">
    <h1 class="text-xl font-semibold m-0">Triage</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      ETL feed gaps the Probe + Refresh Data passes surfaced. Each
      row points at the L2 shape + the SQL fragment that would
      close the gap.
    </p>
  </header>
  {demo_plant_banner}
  {plant_banner_html}
  <main>
  {body_html}
  </main>
</body>
</html>
"""


def _render_triage_recomputing_banner(exc: BaseException) -> str:
    """BTa.4 — friendly retry prompt when the gap detector raises.

    Replaces the prior 500 spinner-of-doom: the most common cause
    during normal operator iteration is a concurrent matview refresh
    holding a lock, so present the operator with a `<meta refresh>`
    hint + manual retry button rather than a stack trace. The
    underlying error class is shown in a `<details>` block for
    diagnostics; ``repr`` is escaped before injection.
    """
    return f"""
  <section class="px-8 py-10 text-center" id="triage-recomputing"
           data-test-triage-state="recomputing">
    <p class="text-base font-semibold text-warning m-0 mb-2">
      ⏳ Triage data is recomputing.
    </p>
    <p class="text-sm text-secondary-fg max-w-2xl mx-auto m-0 mb-3">
      The gap detector hit a transient error — usually the matview
      refresh from the last <a class="text-accent hover:underline" href="/etl/run">Refresh Data</a>
      run holding a lock. Retry in a moment.
    </p>
    <p class="m-0 mb-4">
      <a class="inline-block px-4 py-2 bg-accent text-accent-fg rounded-sm border border-accent text-sm hover:opacity-85"
         href="/etl/triage">↻ Retry now</a>
    </p>
    <details class="text-xs text-secondary-fg max-w-2xl mx-auto">
      <summary class="cursor-pointer hover:text-accent">Error details</summary>
      <pre class="text-left bg-surface-bg p-3 mt-2 rounded-sm overflow-auto"><code>{escape(repr(exc))}</code></pre>
    </details>
  </section>
"""


def _render_triage_body(gaps: tuple[Gap, ...]) -> str:
    """BTa.4 — accordion-grouped triage view.

    Gaps cluster by ``gap.kind`` into 4 collapsible ``<details>`` sections
    (BTa.0 Lock 3). Each section header carries the kind label + a volume
    badge (`Unmatched rail_name • 256 rows total · 3 distinct values`); each
    card inside ships a per-kind color/icon stripe (accessible — distinct
    shape AND label, not color-only). When only one kind has gaps, that
    section renders open by default; with multiple kinds present, all
    sections collapse so the operator can scan the kind distribution at a
    glance before diving in.

    Within a section, cards sort by ``evidence.row_count`` DESC — the
    highest-volume gap is the most impactful fix.
    """
    if not gaps:
        return """
  <section class="px-8 py-10 text-center" id="triage-empty">
    <p class="text-base font-semibold text-success m-0 mb-2">● No gaps detected.</p>
    <p class="text-sm text-secondary-fg m-0 mb-1">
      Every row produced by the last ETL run matches the L2's declared contracts.
    </p>
    <p class="text-sm text-secondary-fg m-0">
      → Re-check on the next ETL run, or after editing the L2.
    </p>
  </section>
"""
    # Group by kind, preserve canonical render order.
    groups: dict[str, list[Gap]] = {}
    for gap in gaps:
        groups.setdefault(gap.kind, []).append(gap)
    # Sort each kind's gaps by row_count DESC so the highest-impact
    # card surfaces first.
    for kind_gaps in groups.values():
        kind_gaps.sort(
            key=lambda g: g.evidence.row_count, reverse=True,
        )
    default_open = len(groups) == 1
    section_blocks: list[str] = []
    for kind in _GAP_KIND_RENDER_ORDER:
        if kind not in groups:
            continue
        section_blocks.append(
            _render_triage_kind_section(
                kind, groups[kind], default_open=default_open,
            ),
        )
    # Any unknown kinds (defensive: a new GapKind shipped without
    # render-order coverage) render at the end so they're not silently
    # dropped.
    for kind, kind_gaps in groups.items():
        if kind in _GAP_KIND_RENDER_ORDER:
            continue
        section_blocks.append(
            _render_triage_kind_section(
                kind, kind_gaps, default_open=default_open,
            ),
        )
    sections_html = "\n".join(section_blocks)
    total = len(gaps)
    kind_count = len(groups)
    count_label = (
        f"{total} gap{'s' if total != 1 else ''} across "
        f"{kind_count} kind{'s' if kind_count != 1 else ''}"
    )
    return f"""
  <section class="px-8 py-4 border-b border-surface-border bg-white" id="triage-header">
    <p class="text-sm m-0"><strong>{count_label}.</strong></p>
  </section>
  <section class="px-8 py-6 flex flex-col gap-3" id="triage-gaps">
    {sections_html}
  </section>
"""


def _render_triage_kind_section(
    kind: str, kind_gaps: list[Gap], *, default_open: bool,
) -> str:
    """One collapsible accordion section for a single gap kind.

    Header carries: kind icon + label + volume badge (total rows +
    distinct count). Body renders one card per gap, sorted by row
    count DESC (caller's responsibility).
    """
    label = _gap_kind_label(kind)
    icon = _GAP_KIND_ICONS.get(kind, "•")
    total_rows = sum(g.evidence.row_count for g in kind_gaps)
    distinct = len(kind_gaps)
    badge_text = (
        f"{total_rows:,} row{'s' if total_rows != 1 else ''} total · "
        f"{distinct} distinct"
    )
    cards_html = "\n".join(_render_gap_card(g) for g in kind_gaps)
    open_attr = " open" if default_open else ""
    return f"""
    <details class="bg-white border border-surface-border rounded-md overflow-hidden"
             data-test-gap-kind-section="{escape(kind)}"{open_attr}>
      <summary class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-surface-bg list-none">
        <span class="text-2xl leading-none" aria-hidden="true">{icon}</span>
        <span class="text-base font-semibold text-primary-fg">{escape(label)}</span>
        <span class="ml-auto text-xs text-secondary-fg font-mono">{escape(badge_text)}</span>
      </summary>
      <div class="px-4 pb-4 pt-1 grid grid-cols-1 lg:grid-cols-2 gap-3">
        {cards_html}
      </div>
    </details>
"""


def _append_from_query(target: str, from_path: str) -> str:
    """Append ``?from=<path>`` (or ``&from=<path>``) for back-breadcrumbs.

    BTa.2 P1.5 — preserves an existing query string and URL-encodes
    the carried path so the editor's ``request.query_params.get("from")``
    receives the original value verbatim.
    """
    separator = "&" if "?" in target else "?"
    return f"{target}{separator}from={quote(from_path, safe='/')}"


def _render_gap_card(gap: Gap) -> str:
    """One decision card per gap.

    BTa.2 P1.4+P1.5 — appends ``?from=/etl/triage`` to the CTA so the
    L2 editor can render a sticky "← Back to Triage" breadcrumb after
    the operator lands there + survives the save-redirect for a
    one-click "save then go back" loop.

    BTa.4 — per-kind color stripe (`border-l-4 border-l-<token>`) +
    volume badge in the card title (`Unmatched rail_name • 256 rows`)
    + columnar evidence mini-table (replaces the prior JSON-ish
    `extras:` ul dump). The kind label moved up to the accordion
    section header (no more per-card kind banner — drop redundant
    chrome since the section already names the kind).
    """
    cta_label = _gap_kind_editor_label(gap.kind)
    stripe_classes = _GAP_KIND_STRIPES.get(gap.kind, "border-l-4 border-l-warning")
    link_target = _append_from_query(gap.link_target, "/etl/triage")
    # BTb.2 — prefill the editor's name field with the offending value
    # so the operator doesn't retype the phantom rail / template name.
    # Only attach for the kinds whose editor consumes a `name` field
    # (unmatched_rail / unmatched_template / missing_limit_schedule).
    # Missing_metadata_key links to an existing template's edit page
    # which doesn't take a prefill.
    if gap.observed_value and gap.kind in (
        "unmatched_rail", "unmatched_template", "missing_limit_schedule",
    ):
        link_target = (
            f"{link_target}&prefill_name="
            f"{quote(gap.observed_value, safe='')}"
        )
    # Card title: observed value (the operator-readable identifier
    # of what's broken) + volume badge.
    title_value = escape(gap.observed_value) if gap.observed_value else "—"
    row_count = gap.evidence.row_count
    volume_badge = (
        f'<span class="text-xs text-secondary-fg font-mono">'
        f'{row_count:,} row{"s" if row_count != 1 else ""}</span>'
    )
    # Evidence mini-table: dt/dd pairs replace the prior bullet list.
    # The dt is the field name (declared_rails / etc.) + dd is the
    # operator-facing value. Sample transaction id is the first row
    # when present.
    evidence_rows: list[str] = []
    if gap.evidence.sample_transaction_id:
        evidence_rows.append(
            '<div class="contents">'
            '<dt class="text-xs text-secondary-fg">sample tx</dt>'
            f'<dd class="text-xs font-mono m-0">'
            f'{escape(gap.evidence.sample_transaction_id)}</dd>'
            '</div>'
        )
    for key, value in gap.evidence.extras.items():
        evidence_rows.append(
            '<div class="contents">'
            f'<dt class="text-xs text-secondary-fg">{escape(key)}</dt>'
            f'<dd class="text-xs font-mono m-0 break-all">{escape(value)}</dd>'
            '</div>'
        )
    evidence_html = ""
    if evidence_rows:
        evidence_html = (
            '<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 m-0 mb-3 '
            'p-2 bg-surface-bg rounded-sm">'
            + "".join(evidence_rows)
            + '</dl>'
        )
    return f"""
    <article class="bg-white {stripe_classes} rounded-md p-4 shadow-sm" data-test-gap-kind="{escape(gap.kind)}">
      <div class="flex items-baseline justify-between gap-3 mb-2">
        <h3 class="text-base font-semibold text-primary-fg m-0 font-mono">{title_value}</h3>
        {volume_badge}
      </div>
      <p class="text-sm m-0 mb-3">{escape(gap.diagnosis)}</p>
      {evidence_html}
      <p class="m-0">
        <a class="inline-block px-3 py-1 bg-accent text-accent-fg rounded-sm border border-accent text-sm hover:opacity-85"
           href="{escape(link_target)}">→ {escape(cta_label)}</a>
      </p>
    </article>
"""


# CF.3.k — bounded LRU cache for the digraph build. Studio is
# single-user / single-process so a module-level dict is sufficient
# (no thread/Redis layer needed). Cache key includes the L2 instance
# identity (`id(...)` — invalidates naturally when cache.get()
# returns a fresh instance after the yaml is saved) plus every render
# axis (prefix / focus / layer / hide_singleleg). Bound at 32 entries
# to handle focus-mode browsing without unbounded growth.
_DIAGRAM_DIGRAPH_CACHE: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
_DIAGRAM_DIGRAPH_CACHE_MAX = 32


def _build_digraph_cached(
    instance: Any,
    *,
    db_table_prefix: str,
    focus_node_id: str | None,
    layer: int,
    hide_singleleg: bool,
    show: frozenset[str] | None = None,
) -> Any:
    """LRU-cached wrapper over ``build_topology_graph_per_rail``.

    Cache key uses ``id(instance)`` as the version signal: when the
    operator saves the L2 yaml, ``cache.get()`` returns a fresh
    L2Instance object → new key → cache miss → fresh render. No
    explicit invalidation needed.

    CF.3.d — `show` is part of the key. Categorical filtering changes
    the emit; each show-set deserves its own cached entry.
    """
    key: tuple[Any, ...] = (
        id(instance), db_table_prefix, focus_node_id, layer,
        hide_singleleg, show,
    )
    cached = _DIAGRAM_DIGRAPH_CACHE.get(key)
    if cached is not None:
        # Move-to-end → most-recently-used
        _DIAGRAM_DIGRAPH_CACHE.move_to_end(key)
        return cached
    digraph = build_topology_graph_per_rail(
        instance,
        db_table_prefix=db_table_prefix,
        bundle_parallel_rails=True,
        focus_node_id=focus_node_id,
        layer=layer,
        hide_singleleg=hide_singleleg,
        show=show,
    )
    _DIAGRAM_DIGRAPH_CACHE[key] = digraph
    # Bound the cache — drop oldest when over the limit.
    while len(_DIAGRAM_DIGRAPH_CACHE) > _DIAGRAM_DIGRAPH_CACHE_MAX:
        _DIAGRAM_DIGRAPH_CACHE.popitem(last=False)
    return digraph


def _render_diagram_page(
    cache: L2InstanceCache,
    dev_log: bool,
    focus_node_id: str | None = None,
    layer: int = 1,
    *,
    coverage_available: bool = False,
    embed: bool = False,
    cfg: Config | None = None,
    top_nav_html: str = "",
    hide_singleleg: bool = False,
    show: frozenset[str] | None = None,
) -> str:
    """Render the L2 topology diagram (per-rail / dot, X.4.b spike winner).

    Strategy: build the graphviz ``Digraph`` server-side via
    ``build_topology_graph_per_rail`` (rails as first-class nodes;
    bundle nodes for parallel pure-connectivity rails; templates as
    clusters around their leg-rails). Inline the DOT source into a
    ``<template>`` block; a JS shim (``diagram.js``) does the
    wasm-graphviz render + post-processes the SVG to add ``data-kind``
    / ``data-id`` attrs. The shim wires the chrome (toggle checkboxes,
    layer stepper, click-to-focus → URL navigation) by toggling CSS
    classes — no DOM mutation per interaction.

    Why server-side DOT, client-side render: the Python graphviz
    wrapper handles DOT construction (it's in the docs extra; Studio
    inherits via the same install). The wasm-graphviz binary handles
    layout + SVG emission in the browser — no system ``dot``
    dependency, same approach Phase T's docs use.

    ``focus_node_id`` (optional) filters the graph to the focused
    node + its ``_smart_focus_hops``-deep neighborhood (roles/
    templates default to 2 to cross a rail; rails/bundles default
    to 1). Server re-emits a smaller DOT; dot re-lays out the
    subgraph cleanly. Click-empty-canvas / Esc / Reset all drop
    the param to restore the full picture.
    """
    instance = cache.get()
    # Z.C — topology helpers require db_table_prefix as a keyword. Use
    # cfg.db_table_prefix when available; fall back to the deployment
    # name (or `"unbound"` sentinel) when the studio is rendering
    # topology without an attached cfg.
    db_prefix = (
        cfg.db_table_prefix if cfg is not None else "unbound"
    )
    # CF.3.k — cache the digraph build (the expensive step) keyed by
    # (instance, prefix, focus, layer, hide_singleleg). Layout time
    # is operator-locked as "not a constraint" so we can trade compute
    # freely, but operator-PERCEIVED latency benefits from caching
    # on every revisit of the same focus / layer / filter combination.
    digraph = _build_digraph_cached(
        instance,
        db_table_prefix=db_prefix,
        focus_node_id=focus_node_id,
        layer=layer,
        hide_singleleg=hide_singleleg,
        show=show,
    )
    dot_source: str = digraph.source

    # Counts for the chrome (uses the typed projection so they reflect
    # the underlying L2 shape, not the rendered subgraph).
    typed = topology_graph_for(instance, db_table_prefix=db_prefix)
    prefix = escape(cfg.deployment_name if cfg is not None else cache.path.stem)
    n_role_internal = sum(
        1 for n in typed.nodes
        if n.kind == "role" and n.scope == "internal"
    )
    n_role_external = sum(
        1 for n in typed.nodes
        if n.kind == "role" and n.scope == "external"
    )
    n_rail = sum(1 for n in typed.nodes if n.kind == "rail")
    n_template = sum(1 for n in typed.nodes if n.kind == "template")
    n_chain = sum(1 for e in typed.edges if e.kind == "chain")
    n_bundle = sum(1 for e in typed.edges if e.kind == "rail_bundle")
    n_self_loop = sum(1 for e in typed.edges if e.kind == "self_loop")
    n_control_parent = sum(1 for e in typed.edges if e.kind == "control_parent")

    # Sidecar metadata for the JS shim — graphviz doesn't surface
    # node-scope through the SVG, so we ship a small map the post-
    # processor merges in (data-scope per role node).
    role_meta: dict[str, dict[str, Any]] = {
        n.id: {"scope": n.scope, "templated": n.templated}
        for n in typed.nodes
        if n.kind == "role" and n.scope is not None
    }
    # CF.3.c — edge-kind sidecar populated server-side by
    # ``build_topology_graph_per_rail`` at each ``g.edge`` call. Keys
    # are ``"<src>-><dst>"`` strings matching what graphviz puts in
    # the SVG `<title>`; values are the typed kind
    # ("chain"/"rail_bundle"/"control_parent"). JS reads this to skip
    # the title-based ``_edgeKind`` heuristic, which was misclassifying
    # post-CF.3.f port-anchored edges.
    edge_meta_raw = getattr(digraph, "edge_meta", None)
    edge_meta: dict[str, str] = dict(edge_meta_raw) if edge_meta_raw else {}
    sidecar = json.dumps(  # typing-smell: ignore[json-indent]: inline page payload — compact saves bytes
        {"role_meta": role_meta, "edge_meta": edge_meta},
    )

    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    # X.4.c.5.b — surface pool availability to the JS shim. The chrome
    # toggle (X.4.c.5.d) reads this meta tag to decide whether to mount
    # the Coverage checkbox; absent ⇒ no toggle (graceful degrade).
    coverage_meta = (
        '<meta name="diagram-coverage-available" content="1">\n'
        if coverage_available
        else ""
    )
    # X.4.c.6 — trainer overlay is always available (pure scenario
    # walk, no DB). The meta tag mirrors the coverage shape so the JS
    # shim's gate is symmetrical.
    trainer_meta = '<meta name="diagram-trainer-available" content="1">\n'
    # Build URL fragments so layer / focus / clear-focus links
    # preserve the other params, INCLUDING ``embed=1`` (2026-05-25
    # user dogfood: clicking a layer link inside the home-page
    # iframe was dropping the embed flag, so the iframe re-rendered
    # with the standalone diagram's full studio chrome stacked
    # below the home page's own chrome → two nav bars).
    def _qs(
        *,
        layer_val: int,
        focus_val: str | None,
        hide_singleleg_val: bool | None = None,
        show_val: frozenset[str] | None = None,
    ) -> str:
        """Build a URL query string preserving navigation context.

        Optional kwargs override the corresponding request value (used
        by the "Show:" + "single-leg rails" toggle anchors); the
        layer/focus links pass nothing and inherit the current values.
        """
        bits: list[str] = [f"layer={layer_val}"]
        if focus_val:
            bits.append(f"focus={escape(focus_val)}")
        if embed:
            bits.append("embed=1")
        hs = hide_singleleg if hide_singleleg_val is None else hide_singleleg_val
        if hs:
            bits.append("hide_singleleg=1")
        # CF.3.d — preserve the show-set across navigation. When the
        # caller passes show_val, use it; otherwise inherit from the
        # current request. Encoded as `show=a,b,c` (sorted for stable
        # cache keys + URL diffability).
        active_show = show if show_val is None else show_val
        if active_show is not None and len(active_show) < 5:  # 5 = full default
            bits.append("show=" + ",".join(sorted(active_show)))
        return "?" + "&".join(bits)

    # CF.3.m — shared label utility for every checkbox row in the
    # sidebar (Show / Edge labels / Overlays). Defined before
    # `_show_anchor` since the closure references it.
    toggle_label_cls = "inline-flex items-center gap-1 cursor-pointer text-sm text-primary-fg"

    # CF.3.d — resolve the active show-set the same way topology.py
    # does, so the chrome anchors below can render the correct
    # on/off state and emit toggle URLs that flip one category at a
    # time. When the request omits `?show=...`, fall through to the
    # layer compat shim (`role` + `control_parent` at L1; +`rail` at
    # L2; +`template` +`chain` at L3).
    active_show: frozenset[str] = (
        show if show is not None else _categories_for_layer(layer)
    ) & _VALID_SHOW_CATEGORIES

    def _toggle_show_url(category: str) -> str:
        """URL flipping `category` in or out of the active show-set.

        Server re-emit (not CSS hide) — dot re-lays-out the smaller
        / larger subset cleanly, same pattern as the layer-link
        family + the single-leg hide toggle.
        """
        next_show = (
            active_show - {category}
            if category in active_show
            else active_show | {category}
        )
        return _qs(
            layer_val=layer,
            focus_val=focus_node_id,
            show_val=next_show,
        )

    def _show_anchor(category: str, label_html: str, *, aria: str) -> str:
        """Render a server-side category toggle as a checkbox.

        CF.3.m polish (2026-06-05): unified with the CSS-toggle
        checkboxes in the sidebar (Show / Edge labels / Overlays) for
        visual coherence. The checkbox carries `data-show-category` /
        `data-show-state` so contract tests + the JS shim can read
        the active set; `onchange` navigates to a flipped-show URL
        which server-re-emits + dot-relays-out. Real `<input>` gives
        keyboard handling (space toggles) for free.
        """
        on = category in active_show
        state_word = "on" if on else "off"
        url = _toggle_show_url(category)
        checked_attr = " checked" if on else ""
        return (
            f'<label class="{toggle_label_cls}" '
            f'data-show-category="{category}" '
            f'data-show-state="{state_word}" '
            f'title="Server-side re-emit; dot relays out the diagram">'
            f'<input type="checkbox"{checked_attr} '
            f'onchange="window.location.href=&#x27;{url}&#x27;" '
            f'aria-label="{aria}">'
            f'{label_html}'
            f'</label>'
        )

    # CF.3.m polish — layer pills want a stronger active/inactive
    # contrast than `chrome_button_classes()` provides. The previous
    # `btn_base + bg-accent` overlay lost the cascade fight against
    # `bg-link-tint` in some Tailwind paint orders, leaving the
    # "active" pill near-indistinguishable from inactive. Build a
    # dedicated pair instead. Active = solid dark `primary-fg`
    # fill + white text + `font-semibold` so the picked layer is
    # unmistakable across themes (DEFAULT_PRESET's mid-blue accent
    # is the typical low-contrast culprit when paired with white).
    btn_shared = (
        "block w-full px-3 py-1 rounded-sm cursor-pointer text-sm "
        "border text-center"
    )
    btn_inactive = (
        f"{btn_shared} bg-link-tint text-accent border-surface-border "
        f"hover:bg-accent hover:text-white"
    )
    btn_active = (
        f"{btn_shared} bg-primary-fg text-white border-primary-fg "
        f"font-semibold shadow-inner cursor-default "
        f"hover:bg-primary-fg hover:text-white"
    )
    layer_links = " ".join(
        f'<a class="{btn_active if n == layer else btn_inactive}" '
        f'href="{_qs(layer_val=n, focus_val=focus_node_id)}">{label}</a>'
        for n, label in (
            (1, "Roles &amp; Structure"),
            (2, "+ Rails"),
            (3, "+ Chains&nbsp;&amp;&nbsp;Templates"),
        )
    )

    # CF.3.d — server-side category toggle anchors. Each click
    # navigates with a flipped `?show=...` set, triggering a fresh
    # dot relayout of the smaller/larger subgraph. Replaces the four
    # CSS-hide checkboxes that left the layout frozen + gappy.
    rail_toggle_html = _show_anchor(
        "rail",
        f'Rails <span class="text-xs font-normal">({n_rail})</span>',
        aria="toggle rail category",
    )
    template_toggle_html = _show_anchor(
        "template",
        f'Templates <span class="text-xs font-normal">({n_template})</span>',
        aria="toggle template category",
    )
    chain_toggle_html = _show_anchor(
        "chain",
        f'Chains <span class="text-xs font-normal">({n_chain})</span>',
        aria="toggle chain category",
    )
    control_parent_toggle_html = _show_anchor(
        "control_parent",
        (
            'Control hierarchy '
            f'<span class="text-xs font-normal">({n_control_parent})</span>'
        ),
        aria="toggle control hierarchy category",
    )

    # X.4.c.5.d — Coverage toggle. Mounted only when the demo-DB pool
    # is wired (which the JS shim also gates by reading the
    # diagram-coverage-available meta). Off by default — clean diagram;
    # on overlays presence/absence tint per node.
    coverage_toggle_html = (
        f'<label class="{toggle_label_cls}">'
        '<input type="checkbox" id="toggle-coverage">'
        ' Coverage'
        '</label>'
        if coverage_available
        else ""
    )
    # X.4.c.6 — Trainer toggle. Always available (pure scenario walk).
    # Off by default; on overlays per-plant-kind badges per node.
    trainer_toggle_html = (
        f'<label class="{toggle_label_cls}">'
        '<input type="checkbox" id="toggle-trainer">'
        ' Trainer'
        '</label>'
    )
    # CF.3.h — server-side SingleLegRail hide. Server re-emit (not
    # CSS hide) so dot re-lays-out the smaller subset cleanly. Modeled
    # as a navigating anchor (URL = source of state truth) rather
    # than a checkbox + JS handler — the layer link family does the
    # same thing and gives us a consistent operator mental model.
    hide_singleleg_url = _qs(
        layer_val=layer,
        focus_val=focus_node_id,
        hide_singleleg_val=(not hide_singleleg),
    )
    # CF.3.m polish — render as a checkbox to match the rest of the
    # sidebar. ``checked`` ⇒ "single-leg rails shown" (hide-singleleg
    # is OFF). Unchecking navigates to ``?hide_singleleg=1`` so the
    # server re-emits without standalone single-leg rails.
    hide_singleleg_checked = "" if hide_singleleg else " checked"
    hide_singleleg_toggle_html = (
        f'<label class="{toggle_label_cls}" '
        f'title="Server-side re-emit; templates keep their single-leg legs">'
        f'<input type="checkbox"{hide_singleleg_checked} '
        f'onchange="window.location.href=&#x27;{hide_singleleg_url}&#x27;" '
        f'aria-label="toggle single-leg rail visibility">'
        f'Single-leg rails'
        f'</label>'
    )

    # Focus indicator + clear link. Visible only when ?focus= is set.
    # Clear preserves the current layer. Renders inside the sidebar's
    # Focus section (CF.3.m), which auto-opens when a focus is active.
    if focus_node_id is not None:
        focus_indicator = (
            f'<details open class="border-t border-surface-border">'
            f'<summary class="cursor-pointer font-semibold px-3 py-2 '
            f'hover:bg-link-tint">Focus</summary>'
            f'<div class="px-3 pb-2 flex flex-col gap-1 text-xs '
            f'text-secondary-fg">'
            f'<code class="font-mono text-primary-fg break-all">'
            f'{escape(focus_node_id)}</code>'
            f'<a class="{chrome_button_classes()} self-start mt-1" '
            f'href="{_qs(layer_val=layer, focus_val=None)}">clear focus</a>'
            f'</div></details>'
        )
    else:
        focus_indicator = ""

    # CF.3.m polish — "Open in editor" button at the sidebar foot.
    # Only render when there's a focused node whose id maps to a
    # uniquely-editable entity (rail / template). With no focus, or
    # focus on a synthetic bundle / shared role, suppress the button
    # entirely — landing on a list page is the operator's "deferred"
    # case per the 2026-06-05 lock.
    view_in_editor_url = _editor_url_for_focus_node(focus_node_id)
    if view_in_editor_url is not None:
        view_in_editor_html = (
            f'<div class="border-t border-surface-border px-3 py-2">'
            f'<a id="view-in-editor" '
            f'class="{chrome_button_classes()} block text-center" '
            f'href="{view_in_editor_url}" '
            f'title="Edit this entity in the L2 Editor">'
            f'Open focused entity in editor'
            f'</a></div>'
        )
    else:
        view_in_editor_html = ""

    # CF.3.m — collapsible floating sidebar replaces the prior
    # page-local header + two horizontal chrome rows. Operator
    # complaint: the chrome ate ~30 % of viewport before the canvas
    # even started. Sidebar floats over the canvas (canvas is not
    # padding-pushed); collapsing it gives the canvas full width
    # without a relayout.
    sidebar_section_summary_cls = (
        "cursor-pointer font-semibold px-3 py-2 select-none "
        "hover:bg-link-tint flex items-center gap-2 "
        "marker:text-secondary-fg"
    )
    sidebar_section_body_cls = "px-3 pb-2 flex flex-col gap-1"
    sidebar_html = f"""
    <details id="diagram-sidebar" open
             class="group absolute top-2 left-2 z-10 bg-white border border-surface-border
                    shadow-md rounded-md text-sm flex flex-col
                    max-h-[calc(100%-1rem)] open:w-64 not-open:w-10
                    overflow-hidden">
      <summary class="flex items-center gap-2 px-3 py-2
                      border-b border-surface-border cursor-pointer
                      list-none select-none hover:bg-link-tint
                      [&::-webkit-details-marker]:hidden"
               title="Toggle chrome sidebar">
        <!-- CF.3.m polish (2026-06-05) — children of the <details>
             use `group-open:`/`group-not-open:` to read the PARENT's
             open state (the `open:`/`not-open:` variants on a child
             element would check the child's own [open] attribute,
             which it never has — every child would silently stay
             hidden). The `group` class on the <details> wires this.
             L2 stem dropped from the header: it already appears in
             the status line directly below as
             "<stem> · N nodes · M edges". -->
        <!-- Reset zoom + Reset all live in the always-visible header
             strip so a misadjusted view can be recovered without
             re-expanding the collapsible body. Hidden when the
             sidebar itself is collapsed (chevron-only strip).
             `event.stopPropagation()` on both stops the click from
             toggling the parent <details>. -->
        <button id="reset-zoom-btn" type="button"
                class="group-not-open:hidden bg-link-tint text-accent border border-surface-border px-2 py-0.5 rounded-sm cursor-pointer text-xs hover:bg-accent hover:text-white"
                onclick="event.stopPropagation()"
                title="Reset zoom &amp; pan to fit canvas">Reset zoom</button>
        <a id="toggle-reset"
           class="group-not-open:hidden bg-link-tint text-accent border border-surface-border px-2 py-0.5 rounded-sm cursor-pointer text-xs hover:bg-accent hover:text-white"
           href="{"?embed=1" if embed else "?"}"
           onclick="event.stopPropagation()"
           title="Reset every chrome toggle to defaults">Reset all</a>
        <span class="text-2xl leading-none font-bold text-secondary-fg ml-auto"
              aria-hidden="true">
          <span class="group-not-open:hidden">«</span>
          <span class="group-open:hidden">»</span>
        </span>
      </summary>
      <div class="flex flex-col overflow-y-auto">
        <div class="px-3 py-1 text-xs text-secondary-fg border-b border-surface-border"
             id="diagram-status" data-prefix="{prefix}">loading…</div>
        {focus_indicator}
        <details open class="border-t border-surface-border">
          <summary class="{sidebar_section_summary_cls}">Layer</summary>
          <div class="{sidebar_section_body_cls}">
            {layer_links}
          </div>
        </details>
        <details open class="border-t border-surface-border">
          <summary class="{sidebar_section_summary_cls}">Show</summary>
          <div class="{sidebar_section_body_cls}">
            <label class="{toggle_label_cls}">
              <input type="checkbox" id="toggle-role-internal" checked>
              Internal roles <span class="text-xs text-secondary-fg font-normal">({n_role_internal})</span>
            </label>
            <label class="{toggle_label_cls}">
              <input type="checkbox" id="toggle-role-external" checked>
              External roles <span class="text-xs text-secondary-fg font-normal">({n_role_external})</span>
            </label>
            {rail_toggle_html}
            {template_toggle_html}
            {chain_toggle_html}
            {control_parent_toggle_html}
          </div>
        </details>
        <details class="border-t border-surface-border">
          <summary class="{sidebar_section_summary_cls}">Edge labels</summary>
          <div class="{sidebar_section_body_cls}">
            <label class="{toggle_label_cls}">
              <input type="checkbox" id="toggle-edge-label-rail_bundle" checked>
              Bundles <span class="text-xs text-secondary-fg font-normal">({n_bundle})</span>
            </label>
            <label class="{toggle_label_cls}">
              <input type="checkbox" id="toggle-edge-label-self_loop" checked>
              Self-loops <span class="text-xs text-secondary-fg font-normal">({n_self_loop})</span>
            </label>
            <label class="{toggle_label_cls}">
              <input type="checkbox" id="toggle-edge-label-chain" checked>
              Chain badges <span class="text-xs text-secondary-fg font-normal">({n_chain})</span>
            </label>
            <label class="{toggle_label_cls}">
              <input type="checkbox" id="toggle-edge-label-control_parent" checked>
              Control labels
            </label>
          </div>
        </details>
        <details class="border-t border-surface-border">
          <summary class="{sidebar_section_summary_cls}">Overlays</summary>
          <div class="{sidebar_section_body_cls}">
            {coverage_toggle_html}
            {trainer_toggle_html}
            {hide_singleleg_toggle_html}
          </div>
        </details>
        {view_in_editor_html}
      </div>
    </details>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · Diagram</title>
  {devlog_meta}{coverage_meta}{trainer_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
  {devlog_script}</head>
<body class="{"flex flex-col m-0 p-0 font-sans bg-surface-bg text-primary-fg h-screen" if embed else "flex flex-col font-sans bg-surface-bg text-primary-fg h-screen"}">
  {top_nav_html}
  {_banner(cfg, embed=embed)}{_standalone_mode_banner(cfg, embed=embed)}

  <!--
    CG.10 (2026-06-05) — the diagram page is the only nav-chrome
    surface that intentionally OMITS the trainer-style
    `<header class="px-8 py-4 border-b ... bg-white">` strip that
    `/`, `/training/`, `/etl/`, and `/l2_shape/<kind>/` carry. Two
    reasons:

    1. Vertical budget. The body uses `h-screen flex flex-col` so
       the canvas (`#diagram-target`, `flex-1 min-h-0`) fills the
       viewport. A 60-80px header strip would directly steal
       canvas height — the SVG already fights for room on dense L2s.

    2. Chrome identity. CF.3.m promoted the floating diagram
       sidebar (rendered above, gated on `not embed`) into the
       diagram's primary chrome. Adding a static header strip would
       compete with it for the operator's attention; the sidebar
       already exposes layer toggles, layout reset, view-in-editor
       — everything a page header would normally anchor.

    The top-nav above this comment already announces the surface
    (the L2 Editor / Diagram / Training... row is the operator's
    "where am I" signal). Cold-read v3 P1 flagged the inconsistency
    and offered "document the exemption" as the alternative to
    adding the strip — taking that path deliberately.
  -->
  <!-- CK.1 (2026-06-05) — visually-hidden h1 + `<main>` landmark
       so screen readers can announce the page + skip-to-content
       works. CG.10's vertical-budget exemption stands for the
       trainer-style header strip (visible h1 would steal canvas
       pixels); the .sr-only h1 doesn't paint anything visible. -->
  <h1 class="sr-only">Recon-Gen Studio — L2 diagram</h1>
  <!-- CF.3.m — canvas wrapper becomes the positioning context for
       the floating sidebar. AM.2 step 3 fix-up (2026-05-25) preserved:
       `flex` + `min-h-0` keep the SVG from overflowing to its
       intrinsic graphviz pixel size; `#diagram-target` keeps
       `flex-1 min-h-0 min-w-0` so the injected SVG fits the viewBox
       via preserveAspectRatio. -->
  <main class="relative flex flex-1 min-h-0 overflow-hidden bg-white">
    {sidebar_html if not embed else ""}
    <div id="diagram-target" class="flex-1 min-h-0 min-w-0"></div>
  </main>

  <template id="topology-dot">{escape(dot_source)}</template>
  <script id="topology-meta" type="application/json">{sidecar}</script>

  <script type="module" src="{asset_url("diagram.js")}"></script>
</body>
</html>
"""


_PLANT_LABELS: tuple[tuple[PlantKind, str], ...] = (
    ("drift", "Drift"),
    ("overdraft", "Overdraft"),
    ("limit_breach", "Limit breach"),
    ("stuck_pending", "Stuck pending"),
    ("stuck_unbundled", "Stuck unbundled"),
    ("supersession", "Supersession"),
)


def _build_state_url(tg_cache: TestGeneratorCache) -> str:
    """X.4.h.url — encode the trainer cache state as a /data URL.

    Bookmarkable + shareable: every knob mutation pushes this URL via
    ``HX-Push-Url`` so the browser bar reflects state, history works,
    and reload restores it. Default-valued knobs are omitted from the
    URL to keep it clean — `/data` with no params == "all defaults".
    """
    from urllib.parse import urlencode  # noqa: PLC0415

    tg = tg_cache.get()
    _window = tg_cache.get_window()
    window_start, window_end = _window.start, _window.end
    today = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode URL default-detection — wall-clock today defines the omit-when-default threshold; not a determinism path
    default_window_end = today
    default_window_start = today - timedelta(
        days=DEFAULT_BASELINE_WINDOW_DAYS - 1,
    )
    params: list[tuple[str, str]] = []
    if window_start != default_window_start:
        params.append(("window_start", window_start.isoformat()))
    if window_end != default_window_end:
        params.append(("window_end", window_end.isoformat()))
    if tg.end_date is not None:
        params.append(("end_date", tg.end_date.isoformat()))
    if tg.scope != "full":
        params.append(("scope", tg.scope))
    if tg.seed is not None:
        params.append(("seed", str(tg.seed)))
    if tg.plants:
        params.append(("plants", ",".join(tg.plants)))
    if not tg_cache.is_etl_hook_enabled():
        params.append(("etl_hook", "disabled"))
    if not params:
        return "/data"
    return f"/data?{urlencode(params)}"


def _apply_state_url_to_cache(
    request: Request,
    tg_cache: TestGeneratorCache,
) -> None:
    """X.4.h.url — read /data URL query params + apply to the cache.

    Called on every ``GET /data`` so a bookmarked / reloaded URL
    restores the trainer's prior knob state. Invalid values silently
    drop (same posture as the PUT routes' validation — bad input
    leaves the cache in its prior state).

    Idempotent: applying the same URL twice yields the same cache
    state. Absent params leave the cache untouched (so the natural
    default-cache-from-from_config stays).
    """
    from typing import cast as _cast  # noqa: PLC0415

    qp = request.query_params
    new_window_start: date | None = None
    new_window_end: date | None = None
    raw_ws = qp.get("window_start")
    if raw_ws:
        try:
            new_window_start = date.fromisoformat(raw_ws)
        except ValueError:
            pass
    raw_we = qp.get("window_end")
    if raw_we:
        try:
            new_window_end = date.fromisoformat(raw_we)
        except ValueError:
            pass
    if new_window_start is not None or new_window_end is not None:
        tg_cache.update_window(
            start=new_window_start
            if new_window_start is not None
            else _UNSET_LOCAL,
            end=new_window_end
            if new_window_end is not None
            else _UNSET_LOCAL,
        )

    raw_end = qp.get("end_date")
    if raw_end is not None:  # explicit empty = clear to None
        if raw_end == "":
            tg_cache.update(end_date=None)
        else:
            try:
                tg_cache.update(end_date=date.fromisoformat(raw_end))
            except ValueError:
                pass

    raw_scope = qp.get("scope")
    if raw_scope is not None:
        from recon_gen.common.config import (  # noqa: PLC0415
            ScopeKind,
        )
        from typing import get_args as _get_args  # noqa: PLC0415

        if raw_scope in _get_args(ScopeKind):
            tg_cache.update(scope=_cast(ScopeKind, raw_scope))

    raw_seed = qp.get("seed")
    if raw_seed is not None:
        if raw_seed == "":
            tg_cache.update(seed=None)
        else:
            try:
                tg_cache.update(seed=int(raw_seed))
            except ValueError:
                pass

    raw_plants = qp.get("plants")
    if raw_plants is not None:
        # Empty string → clear to () == "all kinds" per SPEC.
        from typing import get_args as _get_args2  # noqa: PLC0415

        known: set[PlantKind] = set(_get_args2(PlantKind))
        if raw_plants == "":
            tg_cache.update(plants=())
        else:
            # BF.1.S2: `if p in known` narrows `p` to `PlantKind` since
            # `known: set[PlantKind]`; previous `_cast(PlantKind, p)` was
            # redundant — pyright would now flag it.
            picked = tuple(
                p
                for p in raw_plants.split(",")
                if p in known
            )
            tg_cache.update(plants=picked)

    raw_etl_hook = qp.get("etl_hook")
    if raw_etl_hook is not None:
        # Only "disabled" / "enabled" recognized; bad values silently
        # drop (same posture as other URL params).
        if raw_etl_hook == "disabled":
            tg_cache.set_etl_hook_enabled(False)
        elif raw_etl_hook == "enabled":
            tg_cache.set_etl_hook_enabled(True)


# Sentinel that mirrors the cache's _UNSET — needed because
# update_window's signature takes ``date | object`` for each bound
# (sentinel = "leave alone"), and we want to leave one alone when
# only the other was sent.
_UNSET_LOCAL: object = object()

_SCOPE_LABELS: tuple[tuple[ScopeKind, str, str], ...] = (
    # (value, short label, hover hint pulled from deploy_pipeline docstrings)
    (
        "full",
        "full",
        "Wipe + emit baseline (90 days, all rails) + plants. The "
        "locked-seed default — byte-identical to data apply.",
    ),
    (
        "uncovered_rails",
        "uncovered rails",
        "Fill baseline only for rails the operator's external data "
        "hasn't already covered. No plants — the operator's data is "
        "the story; we just patch the gaps.",
    ),
    (
        "exceptions_only",
        "exceptions only",
        "Plants only, no baseline. Layers L1/Investigation scenarios "
        "on top of the operator's external data.",
    ),
    (
        "only_template",
        "only template",
        "Emit baseline restricted to one TransferTemplate's leg-rails "
        "closure. Useful when iterating on a single template's "
        "lifecycle. Requires the template name in the field below.",
    ),
)


def _render_plants_strip(
    selected: tuple[PlantKind, ...] | None,
) -> str:
    """X.4.h.2 — render the plant-toggle checkbox strip.

    ``selected`` is the current ``cfg.test_generator.plants``. Empty
    tuple = "all kinds" per the SPEC (matches the
    ``filter_scenario_plants(plants=None or ())`` short-circuit), so
    every checkbox renders checked when the tuple is empty.

    Wired with HTMX: each toggle ``hx-put``s the full new selection
    to ``/data/knobs/plants`` (whole-form serialization, server is
    the source of truth for the new state). The form's
    ``hx-swap="outerHTML"`` re-paints the strip from the response so
    the on-screen state always reflects what the server holds.
    """
    select_all = not selected  # None or empty tuple = "all kinds"
    items: list[str] = []
    for kind, label in _PLANT_LABELS:
        checked = "checked " if (select_all or kind in (selected or ())) else ""
        items.append(
            f'<label class="inline-flex items-center gap-1 cursor-pointer text-sm text-primary-fg">'
            f'<input type="checkbox" name="plant" value="{kind}" {checked}/>'
            f' {escape(label)}'
            f"</label>"
        )
    body = "".join(items)
    return (
        f'<form id="data-knob-plants" class="{knob_wrapper_classes()} flex-wrap" '
        f'hx-put="/data/knobs/plants" '
        f'hx-trigger="change" '
        f'hx-target="#data-knob-plants" '
        f'hx-swap="outerHTML">'
        f'<span class="font-mono text-sm text-secondary-fg">plants:</span>'
        f"{body}"
        f"</form>"
    )


def _render_etl_hook_strip(
    command: str | None,
    enabled: bool,
) -> str:
    """X.4.h.etl-toggle — render the etl-hook enable/disable strip.

    Surfaces ``cfg.etl_hook`` (the shell command). The toggle disables
    the hook for the next Deploy without erasing the cfg field — flip
    back on later to restore it. BS.4 (2026-05-29) dropped the legacy
    ``etl_datasource`` half of the pair; the etl_hook is the sole ETL
    contract now (writes directly to demo_db, no upstream copy).

    Three render states:
      - ``command is None`` ⇒ disabled toggle, "(not configured)"
        text. Operator hasn't wired one in cfg.yaml; toggle is moot.
      - ``command`` set, ``enabled=True`` ⇒ active checkbox,
        ``<code>`` showing the command (truncated with ``title=`` for
        the full text on hover).
      - ``command`` set, ``enabled=False`` ⇒ unchecked checkbox,
        ``<code>`` greyed out; deploy will skip step 1.

    Wired with HTMX: the checkbox PUTs ``enabled=on`` (HTML form
    default for checked checkboxes — absence = unchecked) to
    ``/data/knobs/etl_hook``. The route flips the cache flag and
    re-renders the strip.
    """
    common_attrs = (
        'hx-put="/data/knobs/etl_hook" '
        'hx-target="#data-knob-etl-hook" '
        'hx-swap="outerHTML" '
        'hx-trigger="change"'
    )
    code_base = "font-mono text-xs px-2 py-0.5 rounded-sm bg-surface-bg"
    if command is None:
        cmd_class = f"{code_base} text-secondary-fg italic"
        body = (
            '<input type="checkbox" disabled '
            'aria-label="etl_hook (not configured)"/>'
            f'<code class="{cmd_class}">(not configured)</code>'
        )
    else:
        checked = "checked " if enabled else ""
        cmd_class = (
            f"{code_base} text-primary-fg"
            if enabled
            else f"{code_base} text-secondary-fg line-through"
        )
        body = (
            f'<input type="checkbox" name="enabled" value="on" '
            f'{checked}'
            f'aria-label="Run etl_hook on next deploy" '
            f"{common_attrs}/>"
            f'<code class="{cmd_class}" title="{escape(command)}">'
            f"{escape(command)}</code>"
        )
    return (
        f'<form id="data-knob-etl-hook" class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">etl hook:</span>'
        f"{body}"
        f"</form>"
    )


def _render_window_strip(window_start: date, window_end: date) -> str:
    """X.4.h.3.window — render the scenario-window picker.

    Two date inputs (start / end) plus a "last 90 days" reset.
    Defines the trainer's scenario bounds — purely a UI concept
    (does NOT round-trip through the generator). The timeline panel
    renders one row per day in ``[window_start, window_end]``; the
    operator scrubs ``up_to`` (= ``tg.end_date``) within those bounds.

    Both inputs PUT to ``/data/knobs/window``; the route accepts
    ``window_start=<ISO>`` and/or ``window_end=<ISO>`` (either or
    both). The "reset" button sends ``reset=1`` to snap back to the
    default (last 90 days from today).
    """
    common_attrs = (
        'hx-put="/data/knobs/window" '
        'hx-target="#data-knob-window" '
        'hx-swap="outerHTML"'
    )
    input_cls = compact_input_classes()
    return (
        f'<form id="data-knob-window" class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">window:</span>'
        f'<input type="date" name="window_start" '
        f'value="{escape(window_start.isoformat())}" '
        f'class="{input_cls}" '
        f'aria-label="Window start date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<span class="text-secondary-fg">→</span>'
        f'<input type="date" name="window_end" '
        f'value="{escape(window_end.isoformat())}" '
        f'class="{input_cls}" '
        f'aria-label="Window end date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="{ghost_button_classes()}" '
        f'title="Reset to last 90 days from today" '
        f"{common_attrs} "
        f"hx-vals='{{\"reset\": \"1\"}}'>last 90 days</button>"
        f"</form>"
    )


def _render_up_to_strip(
    up_to: date,
    window_start: date,
    window_end: date,
) -> str:
    """X.4.h.3 — render the "up to" scrub-head day-stepper.

    UI: ``[←] [date input] [→] [snap to end]``. ``←`` / ``→`` step
    ±1 day (clamped to the window); the date input commits on change;
    "snap to end" sets up_to = window_end (the most-data position).

    The cache stores up_to as ``tg.end_date``; the renderer always
    receives a concrete date (None resolves to window_end before
    arriving here).

    Wired with HTMX: each control PUTs to ``/data/knobs/end_date``
    (kept the legacy URL — internal-only rename to "up_to" in the UI;
    the generator field stays ``end_date``). Server-side delta
    handler clamps results to ``[window_start, window_end]``.
    """
    iso = up_to.isoformat()
    common_attrs = (
        'hx-put="/data/knobs/end_date" '
        'hx-target="#data-knob-end-date" '
        'hx-swap="outerHTML"'
    )
    snap_payload = f'{{"end_date": "{escape(window_end.isoformat())}"}}'
    btn_cls = ghost_button_classes()
    input_cls = compact_input_classes()
    return (
        f'<form id="data-knob-end-date" class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">up to:</span>'
        f'<button type="button" class="{btn_cls}" '
        f'title="Step back 1 day (within window)" '
        f"{common_attrs} "
        f"hx-vals='{{\"delta\": \"-1\"}}'>←</button>"
        f'<input type="date" name="end_date" value="{escape(iso)}" '
        f'min="{escape(window_start.isoformat())}" '
        f'max="{escape(window_end.isoformat())}" '
        f'class="{input_cls}" '
        f'aria-label="Pick simulation cutoff date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="{btn_cls}" '
        f'title="Step forward 1 day (within window)" '
        f"{common_attrs} "
        f"hx-vals='{{\"delta\": \"1\"}}'>→</button>"
        f'<button type="button" class="{btn_cls}" '
        f'title="Snap to window end ({escape(window_end.isoformat())})" '
        f"{common_attrs} "
        f"hx-vals='{snap_payload}'>snap to end</button>"
        f'<span class="font-mono text-xs text-primary-fg tabular-nums" '
        f'aria-label="Current up_to">{escape(iso)}</span>'
        f"</form>"
    )


def _render_seed_strip(selected: int | None) -> str:
    """X.4.h.4 — render the random-seed input + roll/clear buttons.

    UI: ``[number input] [roll] [clear]``. The number input commits
    on change; "roll" asks the server for a fresh random uint32 and
    pins it; "clear" resets to None (the locked-default sentinel —
    generator side falls back to ``_BASELINE_BASE_SEED = 42``).

    Wired with HTMX: the input PUTs ``seed=<int>``; "roll" sends
    ``roll=1`` (server picks the random value, returns the rendered
    strip with the new value showing); "clear" sends ``seed=`` (empty
    string → clear to None — same form-encoding the date stepper uses
    for its "today" reset).

    The trailing chip surfaces the current value so the operator
    sees the cached state at a glance even when typing into the input.
    """
    val_str = str(selected) if selected is not None else ""
    pretty = str(selected) if selected is not None else "(default)"
    common_attrs = (
        'hx-put="/data/knobs/seed" '
        'hx-target="#data-knob-seed" '
        'hx-swap="outerHTML"'
    )
    btn_cls = ghost_button_classes()
    input_cls = compact_input_classes()
    roll_btn_cls = f"{btn_cls} border-accent"
    return (
        f'<form id="data-knob-seed" class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">seed:</span>'
        f'<input type="number" name="seed" value="{escape(val_str)}" '
        f'min="0" max="4294967295" '
        f'class="{input_cls} w-[12ch] tabular-nums" '
        f'aria-label="Pin a random seed (uint32)" '
        f'placeholder="(default)" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="{roll_btn_cls}" '
        f'title="Pick a fresh random seed" '
        f"{common_attrs} "
        f"hx-vals='{{\"roll\": \"1\"}}'>roll</button>"
        f'<button type="button" class="{btn_cls}" '
        f'title="Clear to default (None ⇒ locked _BASELINE_BASE_SEED)" '
        f"{common_attrs} "
        f"hx-vals='{{\"seed\": \"\"}}'>clear</button>"
        f'<span class="font-mono text-xs text-primary-fg tabular-nums" '
        f'aria-label="Current seed">{escape(pretty)}</span>'
        f"</form>"
    )


def _render_scope_strip(selected: ScopeKind) -> str:
    """X.4.h.5 — render the scope-selector radio group.

    UI: three radio buttons (one per ``ScopeKind``). The cached value
    renders pre-selected; clicking another radio PUTs ``scope=<value>``
    via HTMX. The full descriptive hover hint (lifted from the deploy
    pipeline's per-scope docstrings) sits in the ``title=`` attribute
    so the operator can hover-discover the difference between
    ``full`` / ``uncovered_rails`` / ``exceptions_only`` without
    bouncing back to the SPEC.

    Each radio's ``hx-trigger="change"`` is what the form-encoded
    ``scope`` field carries — the input's own value, no hx-vals
    needed (unlike the date-stepper deltas / seed-roll which need a
    second key in the payload).
    """
    common_attrs = (
        'hx-put="/data/knobs/scope" '
        'hx-target="#data-knob-scope" '
        'hx-swap="outerHTML" '
        'hx-trigger="change"'
    )
    items: list[str] = []
    radio_label_cls = "inline-flex items-center gap-1 cursor-pointer text-sm text-primary-fg"
    for value, short, hint in _SCOPE_LABELS:
        checked = "checked " if value == selected else ""
        items.append(
            f'<label class="{radio_label_cls}" title="{escape(hint)}">'
            f'<input type="radio" name="scope" value="{escape(value)}" '
            f'{checked}{common_attrs}/>'
            f' {escape(short)}'
            f"</label>"
        )
    body = "".join(items)
    return (
        f'<form id="data-knob-scope" class="{knob_wrapper_classes()} flex-wrap">'
        f'<span class="font-mono text-sm text-secondary-fg">scope:</span>'
        f"{body}"
        f"</form>"
    )


def _render_only_template_strip(selected: str | None) -> str:
    """X.4.i.3 — text input for ``cfg.test_generator.only_template``.

    Wires ``cfg.test_generator.scope = "only_template"`` to a concrete
    TransferTemplate name. Operator types the template name into the
    input; commit-on-change PUTs the value. Empty string clears to None
    (which the only_template scope arm rejects with a loud-fail at
    deploy time — surfacing the "you selected only_template but haven't
    picked one" footgun loudly is intentional).
    """
    val_str = selected if selected is not None else ""
    pretty = selected if selected else "(none)"
    common_attrs = (
        'hx-put="/data/knobs/only_template" '
        'hx-target="#data-knob-only-template" '
        'hx-swap="outerHTML"'
    )
    return (
        f'<form id="data-knob-only-template" '
        f'class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">only_template:</span>'
        f'<input type="text" name="only_template" '
        f'value="{escape(val_str)}" '
        f'class="{compact_input_classes()} w-64" '
        f'aria-label="TransferTemplate name to scope to" '
        f'placeholder="(none — required for scope=only_template)" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<span class="font-mono text-xs text-primary-fg" '
        f'aria-label="Current only_template">{escape(pretty)}</span>'
        f"</form>"
    )


def _render_derive_balances_strip(
    enabled: bool, roles: tuple[str, ...] | None,
) -> str:
    """X.4.i.3 — derive_balances flag + per-account-role narrowing.

    UI: checkbox + read-only chip showing the active role set. The
    role list is operator-configurable per L2 in cfg.yaml, but Studio
    today surfaces just the on/off toggle — the narrowing field stays
    yaml-driven (rarely tweaked per-deploy, and editing it in the
    panel would crowd the chrome strip). When the toggle is on, the
    chip shows the resolved role set so the trainer sees what's
    being derived; when off, the chip is empty.
    """
    checked = "checked " if enabled else ""
    if enabled:
        if roles is None:
            chip = "control accounts (default)"
        else:
            chip = ", ".join(roles)
    else:
        chip = "(disabled)"
    common_attrs = (
        'hx-put="/data/knobs/derive_balances" '
        'hx-target="#data-knob-derive-balances" '
        'hx-swap="outerHTML" '
        'hx-trigger="change"'
    )
    return (
        f'<form id="data-knob-derive-balances" '
        f'class="{knob_wrapper_classes()}">'
        f'<label class="inline-flex items-center gap-1 cursor-pointer font-mono text-sm text-primary-fg">'
        f'<input type="checkbox" name="enabled" '
        f'{checked}{common_attrs}/>'
        f' derive_balances'
        f"</label>"
        f'<span class="text-xs text-secondary-fg italic" '
        f'title="Account roles being derived; configure per-L2 via '
        f'test_generator.derive_balances_account_roles" '
        f'aria-label="Derive scope">{escape(chip)}</span>'
        f"</form>"
    )


_PLANT_KIND_ABBRV: tuple[tuple[PlantKind, str], ...] = (
    # 2-3 char abbreviation for the per-day chip — keeps the column
    # visually scannable when 6+ plant kinds land on the same day.
    ("drift", "DR"),
    ("overdraft", "OD"),
    ("limit_breach", "LB"),
    ("stuck_pending", "SP"),
    ("stuck_unbundled", "SU"),
    ("supersession", "SS"),
)
_PLANT_KIND_LABELS: Mapping[PlantKind, str] = {
    kind: label for kind, label in _PLANT_LABELS
}


def _render_timeline_section(
    instance: object,
    tg_cache: TestGeneratorCache | None,
) -> str:
    """X.4.h.6.b/c — render the vertical plant-timeline column.

    Dense-render every day in the trainer's scenario window
    (``[window_start, window_end]``, oldest→newest). Days
    ``<= up_to`` are "data" days — show chips for plants that hit
    them. Days ``> up_to`` are "future" — dimmed, no chips, but still
    clickable to advance the scrub head. The ``up_to`` row carries
    ``.timeline-day--anchor`` + auto-scrolls into view on render.

    Returns the entire ``<section id="data-timeline">`` block so the
    HTMX refresh (``hx-get="/data/timeline"`` triggered by every knob
    PUT via ``HX-Trigger: trainer-knobs-changed``) can swap it as one
    unit with ``hx-swap="outerHTML"``.

    Each row carries ``hx-put`` to ``/data/knobs/end_date`` so a click
    jumps up_to there (without touching the window). The PUT then fires
    the same ``trainer-knobs-changed`` trigger which re-renders the
    timeline (closing the loop).

    ``tg_cache=None`` ⇒ renders against TestGeneratorConfig() defaults
    + a default last-90-days window; that's the unit-test page-shell
    surface that omits Studio's knob mutation routes.
    """
    if tg_cache is not None:
        effective_tg = tg_cache.get()
        _window = tg_cache.get_window()
        window_start, window_end = _window.start, _window.end
        up_to = tg_cache.get_up_to()
    else:
        from recon_gen.common.config import (  # noqa: PLC0415
            TestGeneratorConfig,
        )
        effective_tg = TestGeneratorConfig()
        window_end = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode page-shell default — wall-clock today is the operator-friendly anchor for "last 90 days"; not a determinism path
        window_start = window_end - timedelta(
            days=DEFAULT_BASELINE_WINDOW_DAYS - 1,
        )
        up_to = window_end

    # Anchor the plant projection on window_end, NOT up_to. Plants
    # stay at fixed calendar positions while the scrub head slides
    # within the window — the trainer's mental model is "scenario is
    # fixed, I'm choosing how far through it to view". Without this,
    # `default_scenario_for(today=up_to)` would shift every plant
    # backward as up_to moves earlier, which the trainer experiences
    # as "plants move backwards when I click an earlier day" — the
    # bug the user reported.
    #
    # KNOWN MISMATCH: Deploy still anchors at tg.end_date (= up_to)
    # via deploy_pipeline.py. Until Deploy gets a separate (anchor,
    # cutoff) split, the dashboards Deploy emits will NOT match this
    # preview when up_to < window_end. Tracking as a follow-up — the
    # generator needs an "anchor at window_end, truncate emission at
    # cutoff" mode for full end-to-end alignment.
    import dataclasses as _dc  # noqa: PLC0415

    plant_projection_tg = _dc.replace(
        effective_tg, end_date=window_end,
    )
    sparse_timeline = compute_plant_timeline(instance, plant_projection_tg)  # type: ignore[arg-type]: instance shape from L2InstanceCache.get is Any-ish, but compute_plant_timeline narrows internally
    hits_by_day: dict[date, tuple[PlantHit, ...]] = {
        td.day: td.hits for td in sparse_timeline
    }

    # Dense window: window_start … window_end inclusive.
    n_days = (window_end - window_start).days + 1
    window_days: list[date] = [
        window_start + timedelta(days=i) for i in range(n_days)
    ]
    n_data_days = sum(1 for d in window_days if d <= up_to)
    n_future_days = n_days - n_data_days

    # Header: total plants across the FULL window — what's "available"
    # in the scenario, regardless of where the scrub head sits. The
    # trainer needs to know "12 plants are in this window" so they
    # know what they can scrub forward to find. Filtering by up_to
    # would shrink/grow the count as they click around, which is
    # disorienting.
    kind_counts = hits_by_kind(sparse_timeline)
    total = sum(kind_counts.values())
    n_hit_days = len(sparse_timeline)
    if effective_tg.scope == "uncovered_rails":
        kind_summary = "(scope=uncovered_rails ⇒ no plants emitted)"
    elif total == 0:
        kind_summary = "(no plants in current scenario)"
    else:
        kind_summary = " · ".join(
            f"{_PLANT_KIND_LABELS.get(k, k)} {n}"
            for k, n in kind_counts.items()
        )
    header_html = (
        f'<header class="flex flex-col gap-1 px-3 py-2 border-b border-surface-border bg-surface-bg">'
        f'<span class="text-sm font-semibold text-primary-fg">{total} '
        f'plant{"" if total == 1 else "s"} across '
        f'{n_hit_days} day{"" if n_hit_days == 1 else "s"} '
        f'<span class="text-xs text-secondary-fg font-normal">'
        f"(window: {escape(window_start.isoformat())} → "
        f"{escape(window_end.isoformat())} · "
        f"{n_data_days} day{'' if n_data_days == 1 else 's'} of data, "
        f"{n_future_days} future)"
        f"</span></span>"
        f'<span class="text-xs text-secondary-fg">{escape(kind_summary)}</span>'
        f"</header>"
    )

    rows: list[str] = []
    put_attrs = (
        'hx-put="/data/knobs/end_date" '
        'hx-target="#data-knob-end-date" '
        'hx-swap="outerHTML"'
    )
    for day in window_days:
        iso = day.isoformat()
        is_anchor = day == up_to
        is_future = day > up_to
        hits = hits_by_day.get(day, ())
        # Per-day chips: ALWAYS render at their calendar position so
        # the trainer sees the full plant set across the window —
        # answers "what can I scrub to?" without depending on where
        # the scrub head currently sits. Plants past up_to are still
        # legitimate parts of the scenario; they just haven't been
        # emitted yet at the current cutoff.
        day_kinds: dict[PlantKind, int] = {}
        for hit in hits:
            day_kinds[hit.kind] = day_kinds.get(hit.kind, 0) + 1
        chip_base = timeline_chip_base_classes()
        chip_kind_variants: dict[PlantKind, str] = {
            "drift": " bg-accent/12 text-accent border-accent/25",
            "overdraft": " bg-danger/12 text-danger border-danger/25",
            "limit_breach": " bg-danger/12 text-danger border-danger/25",
            "stuck_pending": " bg-warning/12 text-warning border-warning/25",
            "stuck_unbundled": " bg-warning/12 text-warning border-warning/25",
            "supersession": " bg-success/12 text-success border-success/25",
        }
        chip_html: list[str] = []
        for kind, abbrv in _PLANT_KIND_ABBRV:
            if kind not in day_kinds:
                continue
            n = day_kinds[kind]
            count_suffix = f" {n}" if n > 1 else ""
            title = _PLANT_KIND_LABELS.get(kind, kind)
            variant = chip_kind_variants.get(kind, "")
            chip_html.append(
                f'<span class="{chip_base}{variant}" '
                f'title="{escape(title)} ×{n}">'
                f"{escape(abbrv)}{escape(count_suffix)}"
                f"</span>"
            )
        chips = "".join(chip_html)

        cls_attr = timeline_day_classes()
        if is_future:
            cls_attr += " py-px px-2 border-transparent text-secondary-fg"
        elif not hits:
            cls_attr += " py-px px-2 border-transparent text-secondary-fg"
        if is_anchor:
            cls_attr += (
                " border-accent border-2 px-1.5 py-1.5 bg-accent/6 "
                "font-semibold relative hover:bg-accent/10"
            )
        # Anchor row gets a stable id so the JS scrollIntoView can find
        # it after every HTMX swap.
        id_attr = ' id="timeline-anchor-row"' if is_anchor else ""
        if is_anchor:
            title_text = f"up to = {iso} (current scrub head)"
        elif is_future:
            title_text = f"Click to advance up_to → {iso}"
        else:
            title_text = f"Click to rewind up_to → {iso}"
        # AM.2 step 4: data-role + data-state attributes give tests a
        # stable, styling-free hook for "is this a timeline-day row?"
        # + "is this row the anchor / future / data?"
        if is_anchor:
            day_state = "anchor"
        elif is_future:
            day_state = "future"
        elif not hits:
            day_state = "empty"
        else:
            day_state = "data"
        rows.append(
            f'<button type="button" data-role="timeline-day" '
            f'data-state="{day_state}" class="{cls_attr}"{id_attr} '
            f'title="{escape(title_text)}" '
            f"{put_attrs} "
            f"hx-vals='{{\"end_date\": \"{escape(iso)}\"}}'>"
            f'<span class="font-mono text-xs tabular-nums shrink-0">{escape(iso)}</span>'
            f'<span class="flex flex-wrap items-center gap-1">{chips}</span>'
            f"</button>"
        )
    rows_html = "".join(rows)
    body = (
        f"{header_html}"
        f'<div class="flex flex-col gap-1 p-2 overflow-y-auto max-h-[60vh]">{rows_html}</div>'
        # Scroll the anchor row into view ONLY when it's not already
        # visible. On initial /data load the anchor is at the bottom of
        # a 90-row column so we need to scroll it in. On a click swap
        # the anchor is almost always already in view (operator clicked
        # a visible row) — scrolling would jump the viewport jarringly,
        # so skip it. The check uses the row + scroll container's
        # rects; htmx executes inline <script> in swapped fragments so
        # this runs after every render.
        f'<script>(function() {{'
        f'var a = document.getElementById("timeline-anchor-row");'
        f'if (!a) return;'
        # AM.2 step 4 (2026-05-25): scroll-container detection now
        # walks to the parent .flex.flex-col wrapper via parentElement
        # since the `.timeline-rows` semantic class was retired.
        f'var c = a.parentElement;'
        f'if (!c) return;'
        f'var ar = a.getBoundingClientRect();'
        f'var cr = c.getBoundingClientRect();'
        f'if (ar.top < cr.top || ar.bottom > cr.bottom) {{'
        f'  a.scrollIntoView({{block: "center", behavior: "auto"}});'
        f'}}'
        f'}})();</script>'
    )

    return (
        f'<section class="bg-white border border-surface-border rounded-md '
        f'overflow-hidden" id="data-timeline" '
        f'aria-label="Plant timeline" '
        f'hx-get="/data/timeline" '
        f'hx-trigger="trainer-knobs-changed from:body" '
        f'hx-swap="outerHTML">'
        f"{body}"
        f"</section>"
    )


def _render_data_page(
    cache: L2InstanceCache,
    dev_log: bool,
    *,
    tg_cache: TestGeneratorCache | None = None,
    etl_hook_command: str | None = None,
    cfg: Config | None = None,
    top_nav_html: str = "",
) -> str:
    """X.4.h.1 — Studio "trainer mode" data-shaping panel shell.

    Page-shell + h.2 plant-toggle + h.3 day-stepper + h.4 seed input
    + h.5 scope selector + h.6 plant-timeline column wired through
    the ``TestGeneratorCache``. Training pane stays placeholder
    here (h.9).

    ``tg_cache`` is None for the unit-test surface that exercises the
    page shell without the full studio cfg wiring; in that mode the
    plant-toggle strip renders from the SPEC default ("all kinds")
    and the PUT route is absent (h.2's tests cover both modes).

    The same Deploy button + status span the home page surfaces are
    spliced in so the trainer can re-deploy without bouncing back to
    ``/``. Listener pattern mirrors home — defines a top-level
    ``quicksightDeploy()`` JS helper bound to the button's onclick.
    """
    instance = cache.get()
    prefix = escape(cfg.deployment_name if cfg is not None else cache.path.stem)
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    selected_plants = (
        tg_cache.get().plants if tg_cache is not None else ()
    )
    plants_strip = _render_plants_strip(selected_plants)
    if tg_cache is not None:
        _window = tg_cache.get_window()
        window_start, window_end = _window.start, _window.end
        up_to = tg_cache.get_up_to()
    else:
        # Unit-test page-shell surface — no cache wired. Materialize
        # the same defaults from_config would use so the strips render
        # something sensible (operator-friendly date pickers, not
        # blank slots that look broken).
        from datetime import timedelta as _td  # noqa: PLC0415

        window_end = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode page-shell default — wall-clock today is the operator-friendly anchor for "last 90 days"; not a determinism path
        window_start = window_end - _td(days=DEFAULT_BASELINE_WINDOW_DAYS - 1)
        up_to = window_end
    window_strip = _render_window_strip(window_start, window_end)
    end_date_strip = _render_up_to_strip(up_to, window_start, window_end)
    selected_seed = (
        tg_cache.get().seed if tg_cache is not None else None
    )
    seed_strip = _render_seed_strip(selected_seed)
    selected_scope: ScopeKind = (
        tg_cache.get().scope if tg_cache is not None else "full"
    )
    scope_strip = _render_scope_strip(selected_scope)
    selected_only_template = (
        tg_cache.get().only_template if tg_cache is not None else None
    )
    only_template_strip = _render_only_template_strip(selected_only_template)
    derive_enabled = (
        tg_cache.get().derive_balances if tg_cache is not None else False
    )
    derive_roles = (
        tg_cache.get().derive_balances_account_roles
        if tg_cache is not None else None
    )
    derive_balances_strip = _render_derive_balances_strip(
        derive_enabled, derive_roles,
    )
    etl_hook_enabled = (
        tg_cache.is_etl_hook_enabled() if tg_cache is not None else True
    )
    etl_hook_strip = _render_etl_hook_strip(
        etl_hook_command, etl_hook_enabled,
    )
    timeline_section = _render_timeline_section(instance, tg_cache)
    training_pane = render_training_pane()
    demo_banner = _banner(cfg)
    standalone_banner = _standalone_mode_banner(cfg)
    # CU.3 — Deploy button is always rendered. Demo installs configure
    # dummy AWS creds so /deploy fails noisily at the AWS-push step;
    # the visible visitor effect is the local DB rebuild.
    #
    # CZ.5 — standalone-mode (cfg.etl_hook is None) visually disables
    # the button + carries a tooltip explaining the protection so the
    # operator sees the gate BEFORE the click instead of bouncing off
    # CZ.4's server-side refuse. Couples with the standalone banner +
    # the Trainer reset label change for a single coherent signal.
    standalone_mode = cfg is not None and cfg.etl_hook is None
    if standalone_mode:
        deploy_controls = (
            '<button id="deploy-btn" '
            'data-test-deploy-standalone-disabled '
            'class="ml-auto bg-accent/40 text-accent-fg border '
            'border-accent/40 px-3 py-1 rounded-sm cursor-not-allowed '
            'text-sm opacity-60" '
            'type="button" disabled '
            f'title="{escape(STANDALONE_DEPLOY_DISABLED_TOOLTIP)}">'
            'Deploy changes</button>\n'
            '    <span id="deploy-status" class="text-xs text-secondary-fg" '
            'aria-live="polite"></span>'
        )
    else:
        deploy_controls = (
            '<button id="deploy-btn" class="ml-auto bg-accent text-accent-fg border '
            'border-accent px-3 py-1 rounded-sm cursor-pointer text-sm '
            'hover:opacity-85 disabled:opacity-60 disabled:cursor-not-allowed" '
            'type="button"\n'
            '            onclick="quicksightDeploy()">Deploy changes</button>\n'
            '    <span id="deploy-status" class="text-xs text-secondary-fg" '
            'aria-live="polite"></span>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · Data</title>
  {devlog_meta}{studio_theme_head(instance)}
  {devlog_script}</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}{standalone_banner}
  <header class="flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-white shrink-0">
    <h1>Studio · data shaping</h1>
    <span class="text-sm text-secondary-fg font-mono">{prefix}</span>
    <!-- BS.3 part 3 (2026-05-29): shared top-nav injected above; this
         header now only carries the page title + prefix + deploy. -->
    {deploy_controls}
  </header>
  <script>
    // X.4.h.1 — Deploy button mirrors the home page's. POSTs /deploy,
    // swaps the deploy-status span to reflect the result.
    function quicksightDeploy() {{
      var btn = document.getElementById('deploy-btn');
      var status = document.getElementById('deploy-status');
      btn.disabled = true;
      status.className = 'text-xs text-secondary-fg';
      status.dataset.state = 'running';
      status.textContent = 'Deploying…';
      fetch('/deploy', {{ method: 'POST' }})
        .then(function(resp) {{
          return resp.json().then(function(body) {{
            return {{ ok: resp.ok, status: resp.status, body: body }};
          }});
        }})
        .then(function(result) {{
          btn.disabled = false;
          if (result.body.halted) {{
            status.className = 'text-xs text-warning font-semibold';
            status.dataset.state = 'halted';
            status.textContent = 'Halted: ' + result.body.halt_reason;
          }} else if (result.ok) {{
            var s3 = result.body.step3_generator;
            status.className = 'text-xs text-success';
            status.dataset.state = 'ok';
            status.textContent = (
              'Deployed (gen ' + result.body.step5_data_generation_id +
              ', ' + s3.transactions_after + ' tx)'
            );
          }} else {{
            status.className = 'text-xs text-danger font-semibold';
            status.dataset.state = 'error';
            status.textContent = 'Failed: HTTP ' + result.status;
          }}
        }})
        .catch(function(err) {{
          btn.disabled = false;
          status.className = 'text-xs text-danger font-semibold';
          status.dataset.state = 'error';
          status.textContent = 'Failed: ' + (err && err.message || err);
        }});
    }}
  </script>

  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <div class="flex flex-col gap-2 px-4 py-3 border-b border-surface-border bg-surface-bg" id="data-knobs">
    {etl_hook_strip}
    {scope_strip}
    {only_template_strip}
    {derive_balances_strip}
    {window_strip}
    {end_date_strip}
    {seed_strip}
    {plants_strip}
  </div>

  <main class="grid grid-cols-1 lg:[grid-template-columns:24rem_1fr] gap-4 max-w-7xl mx-auto p-4">
    {timeline_section}
    <section id="data-training" aria-label="Training pane" class="bg-white border border-surface-border rounded-md p-4 overflow-auto">
{training_pane}
    </section>
  </main>
</body>
</html>
"""


def make_studio_routes(
    cache: L2InstanceCache,
    dev_log: bool = False,
    db_pool: AsyncConnectionPool | None = None,
    *,
    dialect: Dialect | None = None,
    prefix_override: str | None = None,
    cfg: Config | None = None,
    tg_cache: TestGeneratorCache | None = None,
    top_nav_fn: Callable[[str], str] | None = None,
) -> list[Route | Mount]:
    """Build the Studio route list bound to ``cache``.

    Spliced into ``make_app(..., studio_routes=...)`` BEFORE the
    Dashboards routes so Studio's ``GET /`` overrides the
    ``GET / → /dashboards`` redirect that ``make_app`` installs in
    Dashboards-only mode.

    Args:
        cache: The shared in-memory ``L2InstanceCache`` every Studio
            route reads from (and X.4.d.3+ writes to).
        dev_log: When True, the diagram + landing pages emit
            ``<meta name="dev-log">`` + load ``/static/js/dev_log.js``
            so client-side console errors / uncaught exceptions /
            unhandled promise rejections / HTMX events POST to
            ``/log`` (which ``make_app`` mounts when ``dev_log=True``).
            Default False so a production-style ``recon-gen
            studio`` invocation stays silent.
        db_pool: Optional ``AsyncConnectionPool`` against the demo DB.
            When set, X.4.c.5's ``GET /diagram/coverage`` route mounts
            and the diagram chrome surfaces a Coverage toggle. When
            None, coverage is silently absent — the only UX impact is
            the missing chrome toggle, no broken behavior. Studio's
            CLI always provides a pool (``cli/studio.py`` rejects
            ``--stub`` / smoke); ``None`` is the unit-test surface.
        dialect: SQL dialect; required when ``db_pool`` is set
            (the coverage fetcher's column-name case folds via
            ``column_name(...)``). When ``db_pool`` is None, this is
            ignored.
        prefix_override: Optional override for the ``<prefix>_transactions``
            schema prefix; usually omitted (defaults to ``cfg.db_table_prefix``).
            When ``cfg`` is also None and the override is omitted, the
            coverage route's prefix-resolve raises — that combination
            is only valid for the unit-test surface that doesn't mount
            the coverage route (``db_pool=None``).
        cfg: Full Config dataclass; required for the X.4.g.13
            ``POST /deploy`` route (the deploy pipeline reads
            ``cfg.etl_hook`` / ``cfg.test_generator`` plus DB
            connection knobs). None ⇒ POST /deploy is silently
            omitted (unit-test surface that doesn't exercise the
            pipeline).
    """
    def _top_nav_html(active_href: str) -> str:
        """BS.3 part 3: closure-wrap top_nav_fn so handlers stay terse.

        Returns the shared top-nav HTML for the active page, or "" when
        no factory was provided (unit-test surface / dashboards-only
        embedding paths). The renderers' default ``top_nav_html=""``
        kwarg keeps the layout valid in either case.
        """
        if top_nav_fn is None:
            return ""
        return top_nav_fn(active_href)

    async def landing(request: Request) -> HTMLResponse:
        return HTMLResponse(
            _render_home_page(
                cache, dev_log, cfg=cfg,
                top_nav_html=_top_nav_html("/"),
                # CF.4.d — pass through kind-namespaced toolbar state
                # so each section's hx-get URL carries it (Q1B).
                query_params=request.query_params,
            ),
        )

    async def etl_landing(_request: Request) -> HTMLResponse:
        # BT.1 — /etl/ landing index. 3-card pointer to BT.2/3/4 sub-pages.
        return HTMLResponse(
            _render_etl_landing_page(
                cache, dev_log, cfg=cfg,
                top_nav_html=_top_nav_html("/etl/"),
            ),
        )

    # BT.3 + BTa.9 — closure-scope "last run" state. Holds:
    #   - "summary"     : finished DeploySummary | None
    #   - "at"          : finished-at datetime | None
    #   - "task"        : in-flight asyncio.Task | None (BTa.9)
    #   - "events"      : live event list the streaming endpoint reads (BTa.9)
    #   - "started_at"  : in-flight start datetime | None (BTa.9)
    #   - "cancelled"   : True when the last run was cancelled (BTa.9)
    # Single-process / single-user Studio; restart wipes.
    _etl_run_state: dict[str, object] = {
        "summary": None, "at": None,
        "task": None, "events": [],
        "started_at": None, "cancelled": False,
    }
    # BV.4.10.d — same shape for /training/session-start +
    # /training/apply. Apply tracks pending_count so the post-run
    # status message can match the operator's pre-click expectation.
    _training_start_state: dict[str, object] = {
        "task": None, "events": [], "started_at": None,
        # BV.4.10.e (P1.2) — pending checkbox state operator had at
        # the moment they clicked Session Start. Preserved here so the
        # post-redirect landing render can re-apply the selection
        # (without it, the operator silently loses their in-flight
        # picks). Cleared after the next Apply consumes them.
        "pending_kinds": frozenset(),
    }
    _training_apply_state: dict[str, object] = {
        "task": None, "events": [], "started_at": None,
        "pending_count": 0,
    }

    async def _run_pipeline_async(
        patched_cfg: Config,
    ) -> None:
        """BTa.9 — pipeline body that runs in a detached task.

        Builds a `_tee` writer that appends each event into
        `_etl_run_state["events"]` so the stream endpoint can poll
        them as they land. Handles CancelledError + post-pipeline
        gap planting + final state transition.
        """
        import asyncio  # noqa: PLC0415
        from recon_gen.common.l2.deploy_pipeline import DeploySummary  # noqa: PLC0415

        # Reset the live-events buffer for this run.
        live_events: list[Mapping[str, object]] = []
        _etl_run_state["events"] = live_events
        _etl_run_state["summary"] = None
        _etl_run_state["at"] = None
        _etl_run_state["cancelled"] = False

        async def _tee(payload: Mapping[str, object]) -> None:
            live_events.append(dict(payload))

        # BU.1.8 — typed overlay surface. ETL_DEBUG = baseline + L1
        # plants + L2 demo-gap overlay. Replaces the BTa.8 inline
        # overlay block that used to live below; the overlay layer
        # is now applied inside run_deploy_pipeline before matview
        # refresh so the matviews see the overlay'd state cleanly.
        from recon_gen.common.l2.pipeline_overlays import (  # noqa: PLC0415
            ETL_DEBUG, LOCKED_SEED,
        )
        # Real-hook deployments don't want the L2 demo overlay —
        # their data is the source of truth. Use LOCKED_SEED
        # (baseline + L1 plants only) so the L1 dashboards still
        # have demo content but the L2 overlay stays off.
        overlays = ETL_DEBUG if patched_cfg.etl_hook is None else LOCKED_SEED
        subprocess_lock_bracket = _duckdb_pool_subprocess_bracket(db_pool)
        try:
            summary = await run_deploy_pipeline(
                patched_cfg, cache.get(), dev_log=_tee,
                overlays=overlays,
                subprocess_lock_bracket=subprocess_lock_bracket,
            )
        except asyncio.CancelledError:
            # BTa.9 — operator cancel. Don't roll back partial state
            # (operator explicit: next wipe handles it + partial state
            # aids troubleshooting). Just synthesize a halted summary.
            from datetime import datetime as _dt  # noqa: PLC0415

            await _tee({
                "event": "deploy:cancelled",
                "reason": "operator cancel",
                "ts_unix": __import__("time").time(),
            })
            _etl_run_state["summary"] = DeploySummary(
                halted=True,
                halt_reason="cancelled by operator (partial state remains; next Refresh Data wipes it)",
                events=tuple(live_events),
            )
            _etl_run_state["at"] = _dt.now()  # typing-smell: ignore[no-datetime-now]: BTa.9 cancel-stamp uses wall-clock for the operator's last-run banner
            _etl_run_state["cancelled"] = True
            _etl_run_state["task"] = None
            raise

        # BU.1.8 — the BTa.8 inline overlay block previously here
        # moved INTO run_deploy_pipeline via the L2_DEMO_GAP_OVERLAY
        # typed layer (selected via overlays=ETL_DEBUG above when
        # cfg.etl_hook is None). Matview refresh now sees the
        # overlay'd state cleanly.

        _etl_run_state["summary"] = summary
        _etl_run_state["at"] = datetime.now()  # typing-smell: ignore[no-datetime-now]: run-stamp for the operator-facing "last run at ..." banner — same wall-clock anchor as the trainer page; not a determinism path
        _etl_run_state["task"] = None

    async def etl_run(request: Request) -> HTMLResponse | RedirectResponse:
        """GET — render the run page; POST — launch background task + 303.

        POST disables ``test_generator`` per BT.0 lock 1 (pure-ETL
        runs; generator overlay stays a Training-mode opt-in) — BUT
        only when ``cfg.etl_hook`` is configured. With no hook the
        pipeline would otherwise be wipe → no-op → empty DB; the
        operator's "Refresh Data" click would dutifully wipe their
        data with nothing to repopulate. Leaving the generator on
        in the no-hook case gives the bundled-demo path actual
        rows to surface in coverage + triage + probe.

        BTa.9 — POST no longer blocks until the pipeline finishes;
        instead it spawns a detached asyncio task and 303s back to
        the GET so the operator can watch live events stream in via
        ``/etl/run/stream``. Cancel via ``POST /etl/run/cancel``.
        """
        import asyncio  # noqa: PLC0415

        if request.method == "POST":
            if cfg is None:
                # Same gate as POST /deploy — pipeline needs cfg.
                return RedirectResponse(url="/etl/", status_code=303)
            # BTa.9 — double-click guard: if a run is in flight,
            # ignore the second POST + bounce to GET so the operator
            # sees the live tail.
            existing = _etl_run_state.get("task")
            if existing is not None and isinstance(existing, asyncio.Task) and not existing.done():
                return RedirectResponse(url="/etl/run", status_code=303)
            # Bundled-demo path: no hook ⇒ keep generator enabled
            # so Refresh Data actually reloads the demo seed.
            if cfg.etl_hook is None:
                patched_cfg = cfg
            else:
                patched_cfg = dataclass_replace(
                    cfg, test_generator=dataclass_replace(
                        cfg.test_generator, enabled=False,
                    ),
                )
            _etl_run_state["started_at"] = datetime.now()  # typing-smell: ignore[no-datetime-now]: BTa.9 wall-clock anchor for "running for Ns" + elapsed-time display
            task = asyncio.create_task(_run_pipeline_async(patched_cfg))
            _etl_run_state["task"] = task
            return RedirectResponse(url="/etl/run", status_code=303)

        last_summary = cast(
            "DeploySummary | None", _etl_run_state.get("summary"),
        )
        last_run_at = cast(
            "datetime | None", _etl_run_state.get("at"),
        )
        task_obj = _etl_run_state.get("task")
        is_running = isinstance(task_obj, asyncio.Task) and not task_obj.done()
        just_ran = request.query_params.get("just_ran") == "1"
        html = await _render_etl_run_page(
            cache, dev_log,
            last_summary=last_summary, last_run_at=last_run_at,
            db_pool=db_pool, dialect=dialect,
            prefix_override=prefix_override,
            cfg=cfg,
            top_nav_html=_top_nav_html("/etl/run"),
            just_ran=just_ran,
            is_running=is_running,
        )
        return HTMLResponse(html)

    async def etl_run_stream(_request: Request) -> HTMLResponse:
        """BTa.9 — live-tail fragment endpoint.

        Returns the FULL accumulated event log on every poll, not
        just the delta since the last poll. The htmx swap is
        ``outerHTML`` on the wrapper div, so each response replaces
        the whole tail with the latest view; returning only deltas
        would clobber the accumulated history.

        When the task is still in flight the fragment carries the
        next-poll htmx attrs; when finished the response also
        emits ``HX-Trigger: etl-run-finished`` so the inline
        client script navigates to ``/etl/run?just_ran=1`` for
        the final summary + flash.
        """
        import asyncio  # noqa: PLC0415

        live_events = cast(
            "list[Mapping[str, object]]",
            _etl_run_state.get("events") or [],
        )
        task_obj = _etl_run_state.get("task")
        running = (
            isinstance(task_obj, asyncio.Task) and not task_obj.done()
        )
        fragment = _render_etl_live_tail_fragment(
            all_events=live_events,
            running=running,
        )
        headers: dict[str, str] = {}
        if not running:
            headers["HX-Trigger"] = "etl-run-finished"
        return HTMLResponse(fragment, headers=headers)

    async def etl_run_cancel(_request: Request) -> RedirectResponse:
        """BTa.9 — cancel the in-flight run, if any."""
        import asyncio  # noqa: PLC0415

        task_obj = _etl_run_state.get("task")
        if isinstance(task_obj, asyncio.Task) and not task_obj.done():
            task_obj.cancel()
        return RedirectResponse(url="/etl/run", status_code=303)

    async def etl_triage(request: Request) -> HTMLResponse:
        # BT.4 — exception triage. Renders gap cards diffing the L2's
        # declared contracts (BT.5) against the observed runtime; each
        # card carries a deep link to the relevant editor list page
        # (link-only v1 per BT.0 lock 5).
        # BV.4.2 (slice) — `?prefix=<value>` URL param routes the
        # triage page at a different prefix (typically the v overlay).
        # Defaults to the closure-captured prefix when absent.
        url_prefix = request.query_params.get("prefix")
        effective_prefix = url_prefix or prefix_override
        html = await _render_etl_triage_page(
            cache, dev_log,
            db_pool=db_pool, dialect=dialect,
            prefix_override=effective_prefix,
            cfg=cfg,
            top_nav_html=_top_nav_html("/etl/triage"),
        )
        return HTMLResponse(html)

    async def etl_probe(request: Request) -> HTMLResponse:
        # BT.2 — L2-slice probe. Reads (kind, name, date_from, date_to)
        # from query params; renders the L2-declared contract for the
        # picked entity side-by-side with observed runtime rows. When
        # no name is picked, renders the picker + empty observed pane.
        # When db_pool is absent (unit-test surface), the observed
        # pane shows a "no DB pool wired" banner; picker still works.
        html = await _render_etl_probe_page(
            cache, dev_log, request,
            db_pool=db_pool,
            dialect=dialect,
            prefix_override=prefix_override,
            cfg=cfg,
            top_nav_html=_top_nav_html("/etl/probe"),
        )
        return HTMLResponse(html)

    async def data(request: Request) -> HTMLResponse:
        # X.4.h.url — read URL query params into the cache so a
        # bookmarked / reloaded /data?... restores trainer state.
        # Absent params leave the cache alone (so a bare /data still
        # picks up wherever the operator left off in this session).
        if tg_cache is not None:
            _apply_state_url_to_cache(request, tg_cache)
        etl_hook_command = cfg.etl_hook if cfg is not None else None
        return HTMLResponse(_render_data_page(
            cache, dev_log,
            tg_cache=tg_cache,
            etl_hook_command=etl_hook_command,
            cfg=cfg,
            top_nav_html=_top_nav_html("/data"),
        ))

    async def data_timeline(_request: Request) -> HTMLResponse:
        """X.4.h.6.c — refresh just the timeline section.

        Triggered by HTMX when any knob PUT response carries
        ``HX-Trigger: trainer-knobs-changed`` (the ``hx-trigger`` on
        the timeline section listens via ``from:body``). Returns the
        full ``<section id="data-timeline">`` block; the section's
        own ``hx-swap="outerHTML"`` swaps it.
        """
        return HTMLResponse(_render_timeline_section(cache.get(), tg_cache))

    async def diagram(request: Request) -> HTMLResponse:
        focus_node_id = request.query_params.get("focus") or None
        layer_raw = request.query_params.get("layer", "1")
        try:
            layer = max(1, min(3, int(layer_raw)))
        except ValueError:
            layer = 1
        # X.4.f.8.embed-chrome — when embedded inside the home page's
        # iframe, drop the studio-header so the page doesn't carry two
        # nav bars (the home's + the diagram's).
        embed = request.query_params.get("embed") == "1"
        # CF.3.h — server-side hide of standalone SingleLegRails.
        # URL = source of state truth (operator lock). Template-resident
        # single-leg rails stay in their composite shape's port row.
        hide_singleleg = request.query_params.get("hide_singleleg") == "1"
        # CF.3.d — categorical show-set via `?show=role,rail,template`.
        # Parsed as a comma-separated list and intersected with valid
        # categories ("role" / "rail" / "template" / "chain" /
        # "control_parent"). When absent, the layer compat shim
        # derives the set; when present, it overrides layer's gating.
        show_raw = request.query_params.get("show")
        if show_raw:
            show_param: frozenset[str] | None = (
                frozenset(
                    p.strip() for p in show_raw.split(",") if p.strip()
                ) & _VALID_SHOW_CATEGORIES
            )
        else:
            show_param = None
        # BS.3 part 3 — embedded diagram (inside the home iframe) skips
        # the top nav too; the host page already carries it.
        nav_html = "" if embed else _top_nav_html("/diagram")
        return HTMLResponse(
            _render_diagram_page(
                cache, dev_log, focus_node_id, layer,
                coverage_available=db_pool is not None,
                embed=embed,
                cfg=cfg,
                top_nav_html=nav_html,
                hide_singleleg=hide_singleleg,
                show=show_param,
            ),
        )

    # BU.1 vertical slice — Trainer surface (registry-driven, see
    # common/l2/plant_registry.py + common/html/_studio_training_v2.py).
    async def training_landing(request: Request) -> HTMLResponse:
        # BV.4.0 vertical slice — new landing surface from v3.
        # BU's /training/plant/<kind> + /training/tour/<kind> routes
        # stay registered today (orphaned — not linked from v3 landing).
        # BV.4.4 made v3 the primary surface but didn't yet remove the
        # v2 routes; tracked as a backlog cleanup separate from the
        # primary-surface migration.
        import os as _os  # noqa: PLC0415

        from recon_gen.common.html._studio_training_v3 import (  # noqa: PLC0415
            render_training_v3_landing,
        )
        from recon_gen.common.l2.v_overlay import (  # noqa: PLC0415
            read_applied_state, read_failed_kinds, read_last_apply,
            read_session_metadata,
            session_metadata_l2_mtime_key,
            session_metadata_session_start_key,
        )
        instance = cache.get()
        base_prefix = cfg.db_table_prefix if cfg is not None else cache.path.stem
        v_overlay_exists = await _v_overlay_exists(
            cfg, instance, base_prefix,
        )
        # CF.1 — the Session-Start ribbon stays ?status= driven because
        # it's a transient post-action signal with no failure mode. The
        # post-Apply banner reads from kv (read_last_apply) so it
        # survives nav + Studio restart and renders honest amber on
        # partial-failure instead of an unconditional green claim.
        session_status = request.query_params.get("status") or None
        applied: dict[str, dict[str, str]] = {}
        failed: dict[str, str] = {}
        last_apply: dict[str, object] | None = None
        l2_stale = False
        if v_overlay_exists and cfg is not None:
            applied = await read_applied_state(cfg)
            failed = await read_failed_kinds(cfg)
            last_apply = await read_last_apply(cfg)
            metadata = await read_session_metadata(cfg)
            stored_mtime_str = metadata.get(session_metadata_l2_mtime_key(), "")
            if stored_mtime_str:
                try:
                    stored_mtime = float(stored_mtime_str)
                    current_mtime = _os.path.getmtime(str(cache.path))
                    # 1-second tolerance so the same-second write doesn't
                    # immediately scream stale.
                    if current_mtime - stored_mtime > 1.0:
                        l2_stale = True
                except OSError:
                    pass
            session_start_time = metadata.get(
                session_metadata_session_start_key(), "",
            )
        else:
            session_start_time = ""
        # BV.4.10.d — detect in-flight Session Start so the landing
        # renders a banner + collapsible live-tail instead of looking
        # idle while the operator waits.
        import asyncio as _asyncio  # noqa: PLC0415

        task_obj = _training_start_state.get("task")
        session_start_running = (
            isinstance(task_obj, _asyncio.Task) and not task_obj.done()
        )
        apply_task_obj = _training_apply_state.get("task")
        apply_running = (
            isinstance(apply_task_obj, _asyncio.Task)
            and not apply_task_obj.done()
        )
        apply_pending_count = (
            int(cast(int, _training_apply_state.get("pending_count") or 0))
            if apply_running else 0
        )
        # BV.4.10.e — pending_kinds carries the operator's pre-Session-
        # Start checkbox state forward across the redirect. The render
        # emits them as DOM-side check-on-load (not HTML `checked`
        # attr) so cb.defaultChecked still reflects only applied
        # state; the diff preview then correctly shows "+N new".
        pending_kinds_obj = _training_start_state.get("pending_kinds")
        pending_kinds: tuple[str, ...] = (
            tuple(sorted(cast(frozenset[str], pending_kinds_obj)))
            if isinstance(pending_kinds_obj, frozenset)
            else ()
        )
        # CZ.5.fix1 (2026-06-09) — operator cold-read of /training/ found
        # the standalone-mode banner missing. CZ.5 added the banner-emit
        # path to render_training_v3_landing (gated on `standalone_mode`)
        # AND wired it into 3 other Studio surfaces (lines 620/914/1031),
        # but missed the v3 training landing call site here.
        #
        # CZ.5.fix2 — also plumb the standard `_banner` + `_standalone_mode_banner`
        # HTML through (kwargs added in v3.py) so the chrome-level banner
        # uses the same shape as every other Studio page. The prior inline
        # rounded-box variant in v3 looked alien against the rest of Studio.
        standalone_mode = cfg is not None and cfg.etl_hook is None
        demo_banner_html = _banner(cfg)
        standalone_banner_html = _standalone_mode_banner(cfg)
        return HTMLResponse(render_training_v3_landing(
            top_nav_html=_top_nav_html("/training/"),
            theme_head=studio_theme_head(instance),
            base_prefix=base_prefix,
            v_overlay_exists=v_overlay_exists,
            session_status=session_status,
            enabled_kinds=tuple(applied.keys()),
            pending_kinds=pending_kinds,
            form_values=applied,
            failed_kinds=failed,
            last_apply=last_apply,
            l2_stale=l2_stale,
            session_start_time=session_start_time,
            session_start_running=session_start_running,
            apply_running=apply_running,
            apply_pending_count=apply_pending_count,
            standalone_mode=standalone_mode,
            demo_banner_html=demo_banner_html,
            standalone_banner_html=standalone_banner_html,
        ))

    async def training_apply_stream(
        _request: Request,
    ) -> HTMLResponse:
        """BV.4.10.d — apply live-tail fragment. Mirror of
        ``training_session_start_stream`` for the Apply task."""
        import asyncio as _asyncio  # noqa: PLC0415

        events = cast(
            "list[Mapping[str, object]]",
            _training_apply_state.get("events") or [],
        )
        task_obj = _training_apply_state.get("task")
        running = (
            isinstance(task_obj, _asyncio.Task) and not task_obj.done()
        )
        from recon_gen.common.html._studio_training_v3 import (  # noqa: PLC0415
            render_training_apply_live_tail,
        )
        fragment = render_training_apply_live_tail(
            events=events, running=running,
        )
        headers: dict[str, str] = {}
        if not running:
            headers["HX-Trigger"] = "training-apply-finished"
        return HTMLResponse(fragment, headers=headers)

    async def training_session_start_stream(
        _request: Request,
    ) -> HTMLResponse:
        """BV.4.10.d — live-tail fragment endpoint.

        Returns the FULL accumulated event log on every poll (htmx's
        `outerHTML` swap replaces the wrapper entirely; delta-only
        would clobber the history). On run completion, sends
        ``HX-Trigger: training-session-start-finished`` so the inline
        client script reloads /training/ to pick up the post-run
        state (success banner + applied/failed ledger reads)."""
        import asyncio as _asyncio  # noqa: PLC0415

        events = cast(
            "list[Mapping[str, object]]",
            _training_start_state.get("events") or [],
        )
        task_obj = _training_start_state.get("task")
        running = (
            isinstance(task_obj, _asyncio.Task) and not task_obj.done()
        )
        from recon_gen.common.html._studio_training_v3 import (  # noqa: PLC0415
            render_training_session_start_live_tail,
        )
        fragment = render_training_session_start_live_tail(
            events=events, running=running,
        )
        headers: dict[str, str] = {}
        if not running:
            headers["HX-Trigger"] = "training-session-start-finished"
        return HTMLResponse(fragment, headers=headers)

    async def training_plant(request: Request) -> HTMLResponse | RedirectResponse:
        import asyncio  # noqa: PLC0415

        from recon_gen.common.html._studio_training_v2 import (  # noqa: PLC0415
            coerce_form_to_kwargs,
            now_anchor,
            render_training_plant_page,
        )
        from recon_gen.common.l2.plant_registry import get_entry  # noqa: PLC0415
        from recon_gen.common.db import (  # noqa: PLC0415
            connect_demo_db, execute_script,
        )

        kind = request.path_params["kind"]
        entry = get_entry(kind)
        if entry is None:
            return HTMLResponse(
                f"<h1>404</h1><p>{escape(kind)} is not a plant kind.</p>",
                status_code=404,
            )
        instance = cache.get()
        theme = studio_theme_head(instance)
        top = _top_nav_html("/training/")
        plant_status: str | None = None
        form_values: dict[str, str] | None = None
        if request.method == "POST":
            if cfg is None:
                return RedirectResponse(url="/training/", status_code=303)
            form = await request.form()
            form_values = {str(k): str(v) for k, v in form.items()}
            kwargs = coerce_form_to_kwargs(entry, form_values)
            plant_cfg = cfg

            plant_instance = instance

            # BU.4 P0 — refresh matviews after plant. Without this, the
            # plant rows land in `<prefix>_transactions` but the L1 +
            # L2FT dashboards read from refreshed matviews (e.g.
            # `<prefix>_current_transactions`) which still hold the
            # pre-plant state. Tour shows "no rows" even though the
            # plant succeeded — exact bug the operator hit on
            # chain_orphan.
            from recon_gen.common.l2.schema import refresh_matviews_sql  # noqa: PLC0415

            def _do_plant() -> None:
                sql = entry.plant_function(
                    prefix=plant_cfg.db_table_prefix,
                    dialect=plant_cfg.dialect,
                    anchor=now_anchor(),
                    instance=plant_instance,
                    **kwargs,
                )
                refresh_sql = refresh_matviews_sql(
                    plant_instance,
                    prefix=plant_cfg.db_table_prefix,
                    dialect=plant_cfg.dialect,
                )
                conn = connect_demo_db(plant_cfg)
                try:
                    cur = conn.cursor()
                    try:
                        execute_script(cur, sql, dialect=plant_cfg.dialect)
                        execute_script(cur, refresh_sql, dialect=plant_cfg.dialect)
                        conn.commit()
                    finally:
                        cur.close()
                finally:
                    conn.close()

            await asyncio.to_thread(_do_plant)
            plant_status = (
                f"Planted {kind} with "
                + ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
                + " — matviews refreshed. Tour the dashboard to see it surface."
            )
        return HTMLResponse(render_training_plant_page(
            entry,
            top_nav_html=top,
            theme_head=theme,
            plant_status=plant_status,
            form_values=form_values,
        ))

    async def training_reset(_request: Request) -> RedirectResponse:
        """BU.1.6 + BU.1.8 — Trainer clean-baseline reset.

        Runs wipe + regenerate to a BASELINE-ONLY DB state — no L1
        invariant plants (drift/overdraft/etc) and no BTa.8 L2-feed
        gap overlay. The Trainer's whole pedagogical premise is
        "plant ONE thing, see ONLY it surface" so both plant layers
        have to stay off.

        BU.1.8 — typed overlay surface. ``TRAINER_CLEAN`` is the
        named flow (PipelineOverlays(layers=())) — empty layers tuple
        means no L1 invariant plants + no L2 demo-gap overlay. The
        pipeline detects the L1_INVARIANT_PLANTS layer is absent +
        force-routes step_3_generator through scope="uncovered_rails"
        (baseline-only emit_baseline_seed path). Replaces the
        BU.1.6 inline cfg-scope hack — the typed name says what
        the call does.

        The /etl/run POST passes ETL_DEBUG / LOCKED_SEED instead;
        two demos, two typed flows.
        """
        if cfg is None:
            return RedirectResponse(url="/training/", status_code=303)
        from recon_gen.common.l2.pipeline_overlays import (  # noqa: PLC0415
            TRAINER_CLEAN,
        )
        subprocess_lock_bracket = _duckdb_pool_subprocess_bracket(db_pool)
        # CZ.3 — standalone-mode (no etl_hook) → DELETE-synthetic-only
        # so real customer rows survive the Trainer reset; ETL-mode
        # (etl_hook configured) → full-TRUNCATE (next ETL cycle refills,
        # so wiping everything is safe). Matview refresh still runs on
        # both paths per CZ.3's matview_refresh_decision lock.
        synthetic_only_wipe = cfg.etl_hook is None
        await run_deploy_pipeline(
            cfg, cache.get(), dev_log=None, overlays=TRAINER_CLEAN,
            subprocess_lock_bracket=subprocess_lock_bracket,
            synthetic_only_wipe=synthetic_only_wipe,
        )
        return RedirectResponse(
            url="/training/?reset=1", status_code=303,
        )

    # ---- BV.4.0 vertical slice — v-overlay POST handlers ----

    async def training_session_start(
        request: Request,
    ) -> RedirectResponse:
        """POST /training/session-start — full lifecycle (DL.10).

        BV.4.10.d — spawns a detached asyncio task + 303s back to
        /training/. The landing page detects the in-flight task and
        renders a banner + collapsible live-tail (BTa.9 pattern).
        Double-click guard: re-POSTing while a run is in flight is
        a no-op redirect.

        BV.4.10.e (P1.2) — operator's in-flight checkbox state arrives
        as ``pending_kinds`` form fields (JS-populated from the apply
        form before submit; see ``_bvCarryPendingToSessionStart`` in
        ``_studio_training_v3._BV_LANDING_JS``). Captured into module
        state so the next landing render restores the operator's
        selection — without this, Session Start silently discards it
        on the post→redirect→render cycle (cold-read v3 P1.2)."""
        import asyncio  # noqa: PLC0415

        if cfg is None:
            return RedirectResponse(url="/training/", status_code=303)
        existing = _training_start_state.get("task")
        if isinstance(existing, asyncio.Task) and not existing.done():
            return RedirectResponse(url="/training/", status_code=303)
        # BV.4.10.e — read pending_kinds before spawning the task.
        # Starlette's request.form() reads the multipart/urlencoded
        # body; we accept all values for the ``pending_kinds`` field
        # (one hidden input per checked kind, all named identically).
        pending: frozenset[str]
        try:
            form = await request.form()
            pending = frozenset(
                str(v) for v in form.getlist("pending_kinds")
            )
        except Exception:  # noqa: BLE001
            pending = frozenset()
        _training_start_state["pending_kinds"] = pending

        from recon_gen.common.l2.v_overlay import session_start  # noqa: PLC0415

        live_events: list[Mapping[str, object]] = []
        _training_start_state["events"] = live_events
        _training_start_state["started_at"] = datetime.now()  # typing-smell: ignore[no-datetime-now]: BV.4.10.d wall-clock anchor for "running for Ns" banner

        async def _tee(payload: Mapping[str, object]) -> None:
            live_events.append(dict(payload))

        # Capture cfg into a local so the inner closure has a non-None
        # type by construction (the early-return above gates on None).
        _cfg = cfg

        async def _run_session_start() -> None:
            subprocess_lock_bracket = _duckdb_pool_subprocess_bracket(db_pool)
            try:
                await session_start(
                    _cfg, cache.get(),
                    refresh_base=True, l2_yaml_path=cache.path,
                    dev_log=_tee,
                    subprocess_lock_bracket=subprocess_lock_bracket,
                )
            finally:
                _training_start_state["task"] = None

        task = asyncio.create_task(_run_session_start())
        _training_start_state["task"] = task
        return RedirectResponse(url="/training/", status_code=303)

    async def training_reclone(_request: Request) -> RedirectResponse:
        """POST /training/reclone — BV.4.9 Force rebuild from base.

        Drops + recreates the v overlay from current base + wipes the
        Apply state ledger. Distinct from Apply, which post-DL.9 is
        incremental: Force rebuild is the "throw out v overlay
        entirely" escape hatch the operator reaches for when they
        want fresh ground regardless of the ledger's view of
        already-applied plants. Skips /etl/run; base stays as-is.

        BV.4.10.f (P3.1) — spawn as a detached task using the same
        ``_training_start_state`` slot as Session Start, so the
        landing render shows the streaming progress page instead of
        a frozen browser. On SQLite the op is sub-second so the page
        flashes through; on Postgres / Oracle it can be seconds-to-
        tens-of-seconds and the streaming page becomes essential."""
        import asyncio  # noqa: PLC0415

        if cfg is None:
            return RedirectResponse(url="/training/", status_code=303)
        existing = _training_start_state.get("task")
        if isinstance(existing, asyncio.Task) and not existing.done():
            return RedirectResponse(url="/training/", status_code=303)

        from recon_gen.common.l2.v_overlay import session_start  # noqa: PLC0415

        live_events: list[Mapping[str, object]] = []
        _training_start_state["events"] = live_events
        _training_start_state["started_at"] = datetime.now()  # typing-smell: ignore[no-datetime-now]: BV.4.10.f wall-clock anchor mirrors Session Start

        async def _tee(payload: Mapping[str, object]) -> None:
            live_events.append(dict(payload))

        _cfg = cfg

        async def _run_reclone() -> None:
            # CO.x — wire the lock bracket defensively. With
            # refresh_base=False, session_start short-circuits the
            # entire run_deploy_pipeline branch and the bracket is
            # never entered, but plumbing it keeps the call shape
            # consistent with training_session_start so a future edit
            # that changes when run_deploy_pipeline fires doesn't
            # silently regress to the lock-conflict bug.
            subprocess_lock_bracket = _duckdb_pool_subprocess_bracket(db_pool)
            try:
                await session_start(
                    _cfg, cache.get(),
                    refresh_base=False, l2_yaml_path=cache.path,
                    dev_log=_tee,
                    subprocess_lock_bracket=subprocess_lock_bracket,
                )
            finally:
                _training_start_state["task"] = None

        task = asyncio.create_task(_run_reclone())
        _training_start_state["task"] = task
        return RedirectResponse(url="/training/", status_code=303)

    async def training_cleanup(_request: Request) -> RedirectResponse:
        """POST /training/cleanup — drops the v overlay. Base prefix
        untouched."""
        if cfg is None:
            return RedirectResponse(url="/training/", status_code=303)
        from recon_gen.common.l2.v_overlay import cleanup as v_cleanup  # noqa: PLC0415

        await v_cleanup(cfg, cache.get())
        return RedirectResponse(
            url="/training/?status=Cleanup+done+%E2%80%94+v+overlay+dropped.",
            status_code=303,
        )

    async def training_apply(request: Request) -> RedirectResponse:
        """POST /training/apply — DL.9 diff-only Apply against the v
        overlay. BV.4.10.d — spawns a detached task + redirects so
        the operator sees the live-tail banner (mirrors Session
        Start's BTa.9 pattern)."""
        import asyncio  # noqa: PLC0415

        if cfg is None:
            return RedirectResponse(url="/training/", status_code=303)
        # BV.4.10.e — Apply consumes whatever the operator selected,
        # so any pending_kinds carried across a prior Session Start
        # have been applied to the form. Clear the carry-state.
        _training_start_state["pending_kinds"] = frozenset()
        from recon_gen.common.l2.plant_registry import (  # noqa: PLC0415
            PLANT_REGISTRY,
            get_entry,
        )
        from recon_gen.common.l2.v_overlay import apply_plants  # noqa: PLC0415

        form = await request.form()
        enabled = {str(v) for v in form.getlist("enabled_kinds")}
        # BV.4.4 — form fields are named `form_<kind>_<primitive>` so
        # the same primitive name (e.g. `count`) can appear on many
        # cards without collision. Parse by longest-prefix-match
        # against registry kinds.
        registry_kinds = {entry.kind for entry in PLANT_REGISTRY}
        form_kwargs_by_kind: dict[str, dict[str, object]] = {}
        for key, value in form.items():
            key_str = str(key)
            if not key_str.startswith("form_"):
                continue
            rest = key_str[len("form_"):]
            # Match the longest prefix that's a registry kind.
            matched_kind: str | None = None
            for k in registry_kinds:
                prefix = f"{k}_"
                if rest.startswith(prefix) and (
                    matched_kind is None or len(k) > len(matched_kind)
                ):
                    matched_kind = k
            if matched_kind is None:
                continue
            field_name = rest[len(matched_kind) + 1:]
            entry = get_entry(matched_kind)
            if entry is None:
                continue
            for primitive in entry.primitives:
                if primitive.name != field_name:
                    continue
                bucket = form_kwargs_by_kind.setdefault(matched_kind, {})
                if isinstance(primitive.default, int):
                    try:
                        bucket[field_name] = int(str(value))
                    except ValueError:
                        bucket[field_name] = primitive.default
                else:
                    bucket[field_name] = str(value)

        enabled_pairs: list[tuple[object, Mapping[str, object]]] = []
        for kind in sorted(enabled):
            entry = get_entry(kind)
            if entry is None:
                continue
            kwargs = form_kwargs_by_kind.get(kind, {})
            # Backfill missing primitives with defaults.
            for primitive in entry.primitives:
                if primitive.name not in kwargs:
                    kwargs[primitive.name] = primitive.default
            enabled_pairs.append((entry, kwargs))

        # BV.4.10.d — detached task + live-tail. Same shape as
        # /training/session-start. Apply on Oracle / large-PG with
        # the slow-path reclone can take a non-trivial fraction of
        # a minute; the operator deserves the same visibility as
        # Session Start.
        existing = _training_apply_state.get("task")
        if isinstance(existing, asyncio.Task) and not existing.done():
            return RedirectResponse(url="/training/", status_code=303)

        live_events: list[Mapping[str, object]] = []
        _training_apply_state["events"] = live_events
        _training_apply_state["started_at"] = datetime.now()  # typing-smell: ignore[no-datetime-now]: BV.4.10.d wall-clock anchor
        _training_apply_state["pending_count"] = len(enabled_pairs)

        async def _tee(payload: Mapping[str, object]) -> None:
            live_events.append(dict(payload))

        _cfg = cfg

        async def _run_apply() -> None:
            try:
                await apply_plants(
                    _cfg, cache.get(),
                    enabled_pairs,  # type: ignore[arg-type]: variance widening; apply_plants pulls .plant_function off first elt — relaxed typing fine at runtime
                    dev_log=_tee,
                )
            finally:
                _training_apply_state["task"] = None

        task = asyncio.create_task(_run_apply())
        _training_apply_state["task"] = task
        return RedirectResponse(url="/training/", status_code=303)

    async def _v_overlay_exists(
        cfg_arg: Config | None, instance: L2Instance, base_prefix: str,
    ) -> bool:
        """Check if `<base>_v_transactions` exists. Cheap probe — the
        v overlay's lifecycle is all-or-nothing so testing one table
        is sufficient.

        CB.17.i.1 — the probe SQL is ``WHERE 1=0``, not ``LIMIT 1``.
        Oracle has no ``LIMIT``; the query died with ORA-00933, the
        ``except`` caught it, the probe returned False, and the
        trainer's Apply button stayed disabled. ``WHERE 1=0`` is the
        portable existence-probe idiom this codebase already uses
        (see ``_column_names`` in
        ``tests/e2e/app2/test_bv33_trainer_dogfood.py``).
        """
        import asyncio as _asyncio  # noqa: PLC0415

        from recon_gen.common.db import connect_demo_db as _connect  # noqa: PLC0415

        if cfg_arg is None:
            return False

        del instance  # closure captures base_prefix; instance unused
        def _probe() -> bool:
            try:
                conn = _connect(cfg_arg)
            except Exception:  # noqa: BLE001
                return False
            try:
                cur = conn.cursor()
                try:
                    cur.execute(f"SELECT 1 FROM {base_prefix}_v_transactions WHERE 1=0")
                    return True
                except Exception:  # noqa: BLE001
                    return False
                finally:
                    cur.close()
            finally:
                conn.close()

        return await _asyncio.to_thread(_probe)

    async def training_tour(request: Request) -> HTMLResponse:
        from recon_gen.common.html._studio_training_v2 import (  # noqa: PLC0415
            render_training_tour_page,
        )
        from recon_gen.common.l2.plant_registry import get_entry  # noqa: PLC0415

        kind = request.path_params["kind"]
        entry = get_entry(kind)
        if entry is None:
            return HTMLResponse(
                f"<h1>404</h1><p>{escape(kind)} is not a plant kind.</p>",
                status_code=404,
            )
        instance = cache.get()
        return HTMLResponse(render_training_tour_page(
            entry,
            top_nav_html=_top_nav_html("/training/"),
            theme_head=studio_theme_head(instance),
        ))

    # ---- BV.3.3 snapshot — POST /training/snapshot/{take,restore,drop} ----
    #
    # Drives the per-dialect Snapshotter (recon_gen.common.snapshotter)
    # from the test harness via HTTP, preserving the X.2.q "everything
    # through driver" invariant. App2Driver gains matching verbs that
    # POST to these routes; the trainer dogfood test calls take() once
    # after Session Start, then restore() between each plant — cutting
    # the cumulative 15× Session Start walk (~30 min on Oracle) down to
    # one take + 14× restore (DuckDB ~50ms, PG ~150ms, Oracle ~2500ms).
    #
    # Snapshotter is process-local but shared across requests — same pool +
    # same v-overlay state. Lazily constructed on first hit; cached on a
    # closure-scope dict so repeated requests reuse the golden-mirror DDL
    # the factory put in place.
    _snapshotter_state: dict[str, object] = {"instance": None}

    async def _get_or_make_snapshotter() -> "Snapshotter | None":
        """Lazy-construct the per-process Snapshotter.

        Returns None when the Studio surface lacks the deps needed to
        build one (cfg / db_pool omitted, as on the bare unit-test
        L2InstanceCache surface). Callers translate None to 503.
        """
        existing = _snapshotter_state.get("instance")
        if existing is not None:
            return cast("Snapshotter", existing)
        if cfg is None or db_pool is None:
            return None
        # Import inside the closure so the module-level import graph
        # stays lean — the Studio routes file is hot at server boot,
        # the Snapshotter only matters at first /training/snapshot hit.
        from recon_gen.common.snapshotter import (  # noqa: PLC0415
            make_snapshotter,
        )
        snap = await make_snapshotter(
            cfg,
            db_pool,
            base_prefix=prefix_override or cfg.db_table_prefix,
            l2_instance=cache.get(),
        )
        _snapshotter_state["instance"] = snap
        return snap

    def _name_from(request: Request) -> str | None:
        """Read + validate the ``?name=<...>`` query param.

        Returns the trimmed name or None when missing / empty. Callers
        translate None to 400 so the operator gets an actionable error
        instead of a generic 500 deep in the Snapshotter impl.
        """
        raw = request.query_params.get("name")
        if not raw:
            return None
        trimmed = raw.strip()
        return trimmed or None

    async def training_snapshot_take(request: Request) -> Response:
        """POST /training/snapshot/take?name=<name> — capture v-overlay state.

        Returns 204 on success, 400 when ?name= missing, 503 when the
        Studio surface can't construct a Snapshotter (cfg/db_pool
        absent — bare unit-test surface), 500 when the impl raises.
        """
        name = _name_from(request)
        if name is None:
            return PlainTextResponse(
                "missing required query parameter: name",
                status_code=400,
            )
        snap = await _get_or_make_snapshotter()
        if snap is None:
            return PlainTextResponse(
                "snapshotter unavailable — cfg or db_pool missing",
                status_code=503,
            )
        try:
            await snap.take(name)
        except NotImplementedError as exc:
            # Foundation stub path — surfaces the BV.3.3 phase message
            # so the test harness gets an actionable failure rather
            # than an opaque 500. NotImplementedError stays a 501 to
            # distinguish from "unexpected impl bug".
            return PlainTextResponse(str(exc), status_code=501)
        except Exception as exc:  # noqa: BLE001 — surface as 500 with msg
            return PlainTextResponse(
                f"snapshot take failed: {exc}", status_code=500,
            )
        return Response(status_code=204)

    async def training_snapshot_restore(request: Request) -> Response:
        """POST /training/snapshot/restore?name=<name> — restore captured state.

        Returns 204 on success, 400 / 503 / 501 / 500 with the same
        semantics as ``training_snapshot_take``.
        """
        name = _name_from(request)
        if name is None:
            return PlainTextResponse(
                "missing required query parameter: name",
                status_code=400,
            )
        snap = await _get_or_make_snapshotter()
        if snap is None:
            return PlainTextResponse(
                "snapshotter unavailable — cfg or db_pool missing",
                status_code=503,
            )
        try:
            await snap.restore(name)
        except NotImplementedError as exc:
            return PlainTextResponse(str(exc), status_code=501)
        except Exception as exc:  # noqa: BLE001 — surface as 500 with msg
            return PlainTextResponse(
                f"snapshot restore failed: {exc}", status_code=500,
            )
        return Response(status_code=204)

    async def training_snapshot_drop(request: Request) -> Response:
        """POST /training/snapshot/drop?name=<name> — drop captured state.

        Returns 204 on success, 400 / 503 / 501 / 500 with the same
        semantics as ``training_snapshot_take``.
        """
        name = _name_from(request)
        if name is None:
            return PlainTextResponse(
                "missing required query parameter: name",
                status_code=400,
            )
        snap = await _get_or_make_snapshotter()
        if snap is None:
            return PlainTextResponse(
                "snapshotter unavailable — cfg or db_pool missing",
                status_code=503,
            )
        try:
            await snap.drop(name)
        except NotImplementedError as exc:
            return PlainTextResponse(str(exc), status_code=501)
        except Exception as exc:  # noqa: BLE001 — surface as 500 with msg
            return PlainTextResponse(
                f"snapshot drop failed: {exc}", status_code=500,
            )
        return Response(status_code=204)

    routes: list[Route | Mount] = [
        Route("/", landing, methods=["GET"]),
        Route("/data", data, methods=["GET"]),
        Route("/data/timeline", data_timeline, methods=["GET"]),
        Route("/diagram", diagram, methods=["GET"]),
        Route("/training/", training_landing, methods=["GET"]),
        Route(
            "/training/session-start/stream",
            training_session_start_stream, methods=["GET"],
        ),
        Route(
            "/training/apply/stream",
            training_apply_stream, methods=["GET"],
        ),
        Route(
            "/training/session-start", training_session_start,
            methods=["POST"],
        ),
        Route(
            "/training/reclone", training_reclone, methods=["POST"],
        ),
        Route(
            "/training/cleanup", training_cleanup, methods=["POST"],
        ),
        Route(
            "/training/apply", training_apply, methods=["POST"],
        ),
        Route(
            "/training/reset", training_reset, methods=["POST"],
        ),
        Route(
            "/training/plant/{kind}", training_plant,
            methods=["GET", "POST"],
        ),
        Route(
            "/training/tour/{kind}", training_tour, methods=["GET"],
        ),
        # BV.3.3 snapshot — Snapshotter HTTP surface; preserves the
        # "everything through driver" invariant (X.2.q). App2Driver
        # POSTs to these from the trainer dogfood test's per-plant
        # restore loop.
        Route(
            "/training/snapshot/take", training_snapshot_take,
            methods=["POST"],
        ),
        Route(
            "/training/snapshot/restore", training_snapshot_restore,
            methods=["POST"],
        ),
        Route(
            "/training/snapshot/drop", training_snapshot_drop,
            methods=["POST"],
        ),
        Route("/etl/", etl_landing, methods=["GET"]),
        Route("/etl/probe", etl_probe, methods=["GET"]),
        Route("/etl/run", etl_run, methods=["GET", "POST"]),
        Route("/etl/run/stream", etl_run_stream, methods=["GET"]),
        Route("/etl/run/cancel", etl_run_cancel, methods=["POST"]),
        Route("/etl/triage", etl_triage, methods=["GET"]),
        Mount(
            "/studio/static",
            app=StaticFiles(directory=str(_STUDIO_ASSETS_DIR)),
            name="studio_static",
        ),
        # BTa.1 — side-panel fragment routes (glossary + per-term).
        # BX.12-15 + BTa.5 add more fragment routes alongside these
        # as the per-page help text + entity diagrams land.
        *_side_panel_routes_imported(cache),
        Mount(
            "/studio/wasm-graphviz",
            app=StaticFiles(directory=str(_WASM_GRAPHVIZ_DIR)),
            name="studio_wasm_graphviz",
        ),
    ]

    # X.4.e + X.4.f — editor routes (list / read / edit / save / delete
    # for every entity kind). Pure scenario over the cached L2 — no
    # pool needed, always mounted alongside the diagram.
    #
    # CU.3 — editor routes always mounted (mutation safety is the
    # sandbox-exec writable allowlist, not the route table). Demo
    # installs point ``--l2`` at a tmpdir overlay so visitor writes
    # land there and vanish on the next launchd restart.
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        make_editor_routes,
    )
    routes.extend(make_editor_routes(
        cache, top_nav_fn=top_nav_fn,
    ))

    # X.4.c.6 — trainer JSON route. Always mounted (no DB needed —
    # the scenario walk is pure Python over the cached L2).
    async def trainer(_request: Request) -> JSONResponse:
        instance = cache.get()
        tm = plants_per_node(instance)
        return JSONResponse(
            {"nodes": {k: dict(v) for k, v in tm.by_node_id.items()}},
        )

    routes.append(Route("/diagram/trainer", trainer, methods=["GET"]))

    # X.4.f.8 — visible-entities map for the home page's diagram-click
    # filter. ``?focus=<node_id>`` returns the entity IDs reachable from
    # that focus subgraph; absent / unknown focus returns the full set.
    async def visible(request: Request) -> JSONResponse:
        focus = request.query_params.get("focus") or None
        instance = cache.get()
        by_kind = visible_entities_for(instance, focus)
        return JSONResponse(
            {kind: sorted(ids) for kind, ids in by_kind.items()},
        )

    routes.append(Route("/diagram/visible", visible, methods=["GET"]))

    # X.4.h.2 — plant-toggle PUT route. Mounted only when the
    # TestGeneratorCache is wired (which Studio CLI always provides;
    # the unit-test surface that omits tg_cache also omits this route,
    # which is correct — without the cache there's nothing to mutate).
    if tg_cache is not None:
        bound_tg = tg_cache

        async def put_plants(request: Request) -> HTMLResponse:
            form = await request.form()
            # The form serializes only checked checkboxes (HTML form
            # default — `unchecked` boxes don't appear in the payload),
            # so the incoming list IS the new selection. Filter to
            # known PlantKind values to ignore any junk a curl test
            # might send; bad values silently drop rather than 500.
            # BF.1.S2: `raw in known` narrows `raw` to `PlantKind` since
            # `known: set[PlantKind]`; the previous `_cast(PlantKind, raw)`
            # call is now flagged unnecessary by pyright.
            known: set[PlantKind] = {kind for kind, _ in _PLANT_LABELS}
            new_plants_set: set[PlantKind] = set()
            for raw in form.getlist("plant"):
                if isinstance(raw, str) and raw in known:
                    new_plants_set.add(raw)
            # BF.1.S2: explicit tuple[PlantKind, ...] — without the
            # annotation pyright widens the generator's element type to
            # `str` after the `if kind in new_plants_set` membership
            # narrowing collapses against the Literal-union shape.
            new_plants: tuple[PlantKind, ...] = tuple(
                kind for kind, _ in _PLANT_LABELS if kind in new_plants_set
            )
            bound_tg.update(plants=new_plants)
            return HTMLResponse(
                _render_plants_strip(new_plants),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(Route("/data/knobs/plants", put_plants, methods=["PUT"]))

        async def put_end_date(request: Request) -> HTMLResponse:
            """X.4.h.3 — apply a delta or absolute date to the up_to knob.

            "up_to" is the simulation cutoff (= ``tg.end_date`` in the
            generator's vocabulary). Always clamped to the current
            scenario window so the trainer can't accidentally scrub
            outside its bounds. Window changes don't touch up_to —
            the trainer redefines bounds, the next click re-anchors.

            Form contract:
                - ``delta=<int>`` → step relative to current up_to
                  (clamped to ``[window_start, window_end]``).
                - ``end_date=<YYYY-MM-DD>`` → set absolute (clamped).
                  Empty string snaps to ``window_end`` (the most-data
                  position).
                - Both present: ``delta`` wins (defensive).
                - Invalid date string: silently drop (cache holds prior).
            """
            form = await request.form()
            _window = bound_tg.get_window()
            window_start, window_end = _window.start, _window.end
            current = bound_tg.get_up_to()

            delta_raw = form.get("delta")
            new_up_to: date = current
            if isinstance(delta_raw, str) and delta_raw.strip():
                try:
                    delta_days = int(delta_raw)
                except ValueError:
                    delta_days = 0
                if delta_days:
                    new_up_to = current + timedelta(days=delta_days)
            else:
                date_raw = form.get("end_date")
                if isinstance(date_raw, str):
                    if date_raw == "":
                        # Snap to window_end (the canonical "most data"
                        # position). Stored as window_end explicitly so
                        # subsequent reads stay stable even if the
                        # window shifts.
                        new_up_to = window_end
                    else:
                        try:
                            new_up_to = date.fromisoformat(date_raw)
                        except ValueError:
                            new_up_to = current  # silent drop
            # Clamp to window bounds.
            if new_up_to < window_start:
                new_up_to = window_start
            elif new_up_to > window_end:
                new_up_to = window_end
            bound_tg.update(end_date=new_up_to)
            return HTMLResponse(
                _render_up_to_strip(new_up_to, window_start, window_end),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/end_date", put_end_date, methods=["PUT"]),
        )

        async def put_window(request: Request) -> HTMLResponse:
            """X.4.h.3.window — set the trainer's scenario window.

            Form contract:
                - ``reset=1`` → snap to last 90 days from today.
                - ``window_start=<YYYY-MM-DD>`` and/or
                  ``window_end=<YYYY-MM-DD>`` → set either bound;
                  the other is preserved. Invalid ISO silently drops.
                - Window-end < window-start: ``update_window`` swaps
                  them (preserves intent over rejection).

            Window changes do NOT touch up_to. If the new window
            excludes the current up_to, the renderer + the next
            put_end_date call will clamp.
            """
            form = await request.form()
            _cur_window = bound_tg.get_window()
            cur_start, cur_end = _cur_window.start, _cur_window.end

            reset_raw = form.get("reset")
            if isinstance(reset_raw, str) and reset_raw.strip():
                end = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode reset — wall-clock today is the "last 90 days" anchor; not a determinism path
                start = end - timedelta(days=DEFAULT_BASELINE_WINDOW_DAYS - 1)
                bound_tg.update_window(start=start, end=end)
            else:
                new_start: date | object = cur_start
                new_end: date | object = cur_end
                start_raw = form.get("window_start")
                end_raw = form.get("window_end")
                if isinstance(start_raw, str) and start_raw:
                    try:
                        new_start = date.fromisoformat(start_raw)
                    except ValueError:
                        pass  # silent drop
                if isinstance(end_raw, str) and end_raw:
                    try:
                        new_end = date.fromisoformat(end_raw)
                    except ValueError:
                        pass  # silent drop
                bound_tg.update_window(start=new_start, end=new_end)
            _new_window = bound_tg.get_window()
            new_window_start, new_window_end = _new_window.start, _new_window.end
            return HTMLResponse(
                _render_window_strip(new_window_start, new_window_end),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/window", put_window, methods=["PUT"]),
        )

        async def put_seed(request: Request) -> HTMLResponse:
            """X.4.h.4 — set / roll / clear the random-seed knob.

            Form contract:
                - ``roll=1`` → server picks a fresh ``random.randint(0,
                  2**32 - 1)`` and pins it. Wins over ``seed=`` when
                  both present (defensive — UI never sends both).
                - ``seed=<int>`` → set absolute value. Empty string
                  clears to None ("clear" reset).
                - Invalid int (non-digit string): silently drop —
                  same posture as the date stepper / plant toggle.
            """
            import random  # noqa: PLC0415

            form = await request.form()
            current = bound_tg.get().seed
            new_seed: int | None = current

            roll_raw = form.get("roll")
            if isinstance(roll_raw, str) and roll_raw.strip():
                # uint32 range matches RECON_GEN_FUZZ_SEED's contract
                # (CLAUDE.md: "runner rolls a fresh random uint32 per
                # invocation"). Trainer-mode UI is not a determinism
                # path, so an unseeded random call is honest here.
                new_seed = random.randint(0, 2**32 - 1)
            else:
                seed_raw = form.get("seed")
                if isinstance(seed_raw, str):
                    if seed_raw == "":
                        new_seed = None
                    else:
                        try:
                            new_seed = int(seed_raw)
                        except ValueError:
                            new_seed = current  # silent drop
            bound_tg.update(seed=new_seed)
            return HTMLResponse(
                _render_seed_strip(new_seed),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/seed", put_seed, methods=["PUT"]),
        )

        async def put_scope(request: Request) -> HTMLResponse:
            """X.4.h.5 — set the test_generator.scope knob.

            Form contract:
                - ``scope=<full|uncovered_rails|exceptions_only>`` →
                  set absolute value.
                - Unknown / missing scope: silently keep current
                  cached value — same posture as the other knobs.

            No "clear" payload — scope has no None sentinel; the
            generator's default is ``"full"``, set explicitly via
            ``TestGeneratorConfig.scope`` default.
            """
            form = await request.form()
            current = bound_tg.get().scope
            new_scope: ScopeKind = current

            scope_raw = form.get("scope")
            known: set[ScopeKind] = {value for value, _, _ in _SCOPE_LABELS}
            if isinstance(scope_raw, str) and scope_raw in known:
                # BF.1.S2: `scope_raw in known` narrows to ScopeKind via
                # the `set[ScopeKind]` membership test.
                new_scope = scope_raw
            bound_tg.update(scope=new_scope)
            return HTMLResponse(
                _render_scope_strip(new_scope),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/scope", put_scope, methods=["PUT"]),
        )

        async def put_etl_hook(request: Request) -> HTMLResponse:
            """X.4.h.etl-toggle — flip the etl_hook enable/disable knob.

            Form contract:
                - ``enabled=on`` (HTML form default for checked
                  checkboxes) → enable. Absence → disable.

            The toggle is meaningful even when ``cfg.etl_hook is None``
            (the renderer surfaces it as disabled + "(not configured)"),
            but the cache flag is still respected — Deploy ignores it
            because the cfg field is None either way.
            """
            form = await request.form()
            enabled_raw = form.get("enabled")
            new_enabled = (
                isinstance(enabled_raw, str) and enabled_raw == "on"
            )
            bound_tg.set_etl_hook_enabled(new_enabled)
            etl_hook_command = (
                cfg.etl_hook if cfg is not None else None
            )
            return HTMLResponse(
                _render_etl_hook_strip(etl_hook_command, new_enabled),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        # CU.3 — etl_hook PUT triggers the operator's shell command
        # (cfg.etl_hook). Gating on cfg presence (rather than the old
        # `--demo-mode` flag) matches the natural semantics: a PUT to
        # invoke etl_hook is meaningless without a configured hook.
        # Demo cfgs omit `etl_hook:` → route auto-skips.
        if cfg is not None and cfg.etl_hook is not None:
            routes.append(
                Route("/data/knobs/etl_hook", put_etl_hook, methods=["PUT"]),
            )

        async def put_only_template(request: Request) -> HTMLResponse:
            """X.4.i.3 — set the test_generator.only_template knob.

            Form contract:
                - ``only_template=<name>`` → set the template name.
                - ``only_template=`` (empty string) → clear to None.

            No validation against the L2's actual TransferTemplates
            here — the deploy-time `_only_template_rails` lookup
            loud-fails with the declared list when the operator typed
            a name that doesn't exist. UI is forgiving so the
            in-progress trainer can hold the cfg in an inconsistent
            state without each keystroke kicking back an error.
            """
            form = await request.form()
            raw = form.get("only_template")
            new_value: str | None = None
            if isinstance(raw, str):
                stripped = raw.strip()
                new_value = stripped if stripped else None
            bound_tg.update_only_template(new_value)
            return HTMLResponse(
                _render_only_template_strip(new_value),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route(
                "/data/knobs/only_template",
                put_only_template,
                methods=["PUT"],
            ),
        )

        async def put_derive_balances(
            request: Request,
        ) -> HTMLResponse:
            """X.4.i.3 — flip the test_generator.derive_balances flag.

            Form contract:
                - ``enabled=on`` (HTML form default for checked
                  checkboxes) → enable.
                - Absence → disable.

            The role-narrowing field
            (``derive_balances_account_roles``) stays cfg-yaml-only —
            edited per-L2, not per-deploy. Surface in the chip so the
            operator sees what's currently in scope.
            """
            form = await request.form()
            enabled_raw = form.get("enabled")
            new_enabled = (
                isinstance(enabled_raw, str) and enabled_raw == "on"
            )
            bound_tg.update_derive_balances(new_enabled)
            roles = (
                bound_tg.get().derive_balances_account_roles
            )
            return HTMLResponse(
                _render_derive_balances_strip(new_enabled, roles),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route(
                "/data/knobs/derive_balances",
                put_derive_balances,
                methods=["PUT"],
            ),
        )

    # X.4.c.5.c — coverage JSON route. Mounted only when a pool exists
    # (Studio CLI always provides one; the unit-test surface skips this).
    if db_pool is not None:
        if dialect is None:
            raise ValueError(
                "make_studio_routes: db_pool requires dialect "
                "(coverage_for needs column_name() to case-fold per dialect)."
            )
        # Capture pool + dialect by closure for the route handler.
        bound_pool = db_pool
        bound_dialect = dialect
        bound_prefix_override = prefix_override

        async def coverage(_request: Request) -> JSONResponse:
            instance = cache.get()
            # Z.C — prefix resolution order:
            # 1) explicit prefix_override (operator wires per-call)
            # 2) cfg.db_table_prefix (cfg-bound studio session)
            # 3) cache.path.stem (yaml file basename — fallback for
            #    studio sessions wired without a cfg, e.g. unit tests)
            prefix = (
                bound_prefix_override
                or (cfg.db_table_prefix if cfg is not None else None)
                or cache.path.stem
            )
            cov = await coverage_for(
                bound_pool, prefix, instance, dialect=bound_dialect,
            )
            return JSONResponse(_coverage_to_json(cov.by_node_id, cov.by_chain_edge_id))

        routes.append(Route("/diagram/coverage", coverage, methods=["GET"]))

    # X.4.g.13 — POST /deploy: orchestrate steps 1→5 of the deploy
    # pipeline against the cached L2 + the operator-supplied cfg.
    # Mounted only when cfg is wired (Studio CLI passes it; the
    # bare-cache unit-test surface omits it).
    if cfg is not None:
        bound_cfg = cfg
        # X.4.h.2 — if a TestGeneratorCache is wired, patch each deploy
        # invocation with the latest knob state. Absent cache (unit
        # surface) ⇒ deploy reads the startup-time cfg.test_generator
        # unchanged, preserving today's behavior.
        bound_tg_for_deploy = tg_cache

        async def deploy(_request: Request) -> JSONResponse:
            instance = cache.get()
            effective_cfg = (
                bound_tg_for_deploy.patched_config(bound_cfg)
                if bound_tg_for_deploy is not None
                else bound_cfg
            )
            # CZ.4 — standalone-mode gate. ``cfg.etl_hook is None`` means
            # the next ETL cycle will NOT re-populate the demo DB after
            # we wipe it, so any unmarked row in the DB might be real
            # customer data. Refuse outright (HTTP 409) — no click-through.
            # Operators who genuinely want demo-data-wipe semantics use
            # the Trainer "Clear synthetic rows and re-seed" button
            # (DELETE-synthetic-only); operators who want the full reset
            # configure ``cfg.etl_hook`` or drop to ``recon-gen data
            # apply --execute`` (the CLI escape hatch documented in CZ).
            if effective_cfg.etl_hook is None:
                return JSONResponse(
                    {
                        "halted": True,
                        "halt_reason": "standalone-mode",
                        "message": _CZ_STANDALONE_MODE_REFUSE_MESSAGE,
                    },
                    status_code=409,
                )
            subprocess_lock_bracket = _duckdb_pool_subprocess_bracket(db_pool)
            summary = await run_deploy_pipeline(
                effective_cfg, instance, dev_log=None,
                subprocess_lock_bracket=subprocess_lock_bracket,
            )
            status = 503 if summary.halted else 200
            return JSONResponse(summary.to_json(), status_code=status)

        # CU.3 — /deploy always mounted. Demo installs configure dummy
        # AWS creds so the pipeline fails noisily at the
        # boto3-orchestration step; the local DB rebuild step succeeds
        # before that and is the visible visitor effect. The
        # sandbox-exec profile's network restrictions are the actual
        # safety layer.
        routes.append(Route("/deploy", deploy, methods=["POST"]))

    return routes


def _coverage_to_json(
    by_node_id: "Mapping[str, CoverageEntry]",
    by_chain_edge_id: "Mapping[str, CoverageEntry]",
) -> dict[str, Any]:
    """Shape the JSON payload the diagram chrome consumes.

    Top-level keys ``nodes`` and ``chain_edges`` so the JS shim can
    paint nodes vs edges separately. Each value is a flat
    ``{id: {present, count}}`` map — boolean `present` keeps the JSON
    payload trivially debug-printable.
    """
    return {
        "nodes": {
            node_id: {"present": e.present, "count": e.count}
            for node_id, e in by_node_id.items()
        },
        "chain_edges": {
            edge_id: {"present": e.present, "count": e.count}
            for edge_id, e in by_chain_edge_id.items()
        },
    }
