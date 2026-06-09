"""BT.3 — ``/studio/etl/run`` route integration tests.

Covers the GET shape (Run button + empty-state when no run has
happened) + the no-DB-pool branch (unit-test surface). Real POST
exercises against the live pipeline live in
``test_studio_deploy_route`` (existing); BT.3 narrows to the
new wire shape — disabled-generator cfg patching + 303 redirect +
last-run-state caching across requests — covered by an in-process
double of ``run_deploy_pipeline``.

The metadata-coverage helper has its own tests in
``test_l2_coverage_metadata``.
"""

from __future__ import annotations

import duckdb

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _build_app(yaml_path: Path) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


def test_etl_run_get_returns_200_with_run_button(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/etl/run")
        assert resp.status_code == 200
        body = resp.text
    assert "<title>Recon-Gen · Studio · ETL · Refresh Data</title>" in body
    assert 'id="etl-run-btn"' in body
    # Form posts back to /etl/run.
    assert '<form method="post" action="/etl/run">' in body


def test_etl_run_get_shows_no_runs_yet_when_state_empty(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run").text
    assert 'No runs yet' in body


def test_etl_run_get_shows_no_db_pool_banner_when_pool_absent(
    writable_l2_yaml: Path,
) -> None:
    """The unit-test surface omits db_pool — the coverage section
    surfaces a 'No DB pool wired' banner instead of crashing."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run").text
    assert 'No DB pool wired' in body


def test_etl_run_post_without_cfg_redirects_to_etl_landing(
    writable_l2_yaml: Path,
) -> None:
    """When make_studio_routes is built without cfg (unit surface),
    POST cannot run the pipeline; bail by 303-redirecting to /etl/
    rather than crashing."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/etl/run", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/etl/"


def test_etl_run_carries_top_nav_with_run_route_active(
    writable_l2_yaml: Path,
) -> None:
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    cfg = make_test_config()

    def fake_nav(active_href: str) -> str:
        return f'<nav data-test-active="{active_href}">NAV</nav>'

    routes = make_studio_routes(cache, top_nav_fn=fake_nav)
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    app = make_app(
        dashboards={"smoke": served},
        studio_routes=routes,
    )
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run").text
    assert 'data-test-active="/etl/run"' in body


def test_etl_run_landing_card_for_run_lights_up_after_BT_3_ships(
    writable_l2_yaml: Path,
) -> None:
    """BT.1's landing card for Run drops its 'coming in BT.3' hint
    once BT.3 has shipped (this test fires after BT.3 lands)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    assert "coming in BT.3" not in body
    # And the card still points at /etl/run.
    assert 'href="/etl/run"' in body


# -- BTa.6 — Run polish ---------------------------------------------------


def test_etl_run_coverage_carries_failures_only_toggle(
    writable_l2_yaml: Path,
) -> None:
    """BTa.6 — coverage section header ships a failures-only toggle
    so an operator with a large L2 can collapse the green rows out of
    sight + focus on what failed. Tested via a fake DeploySummary so
    the renderer takes the populated branch (without a pool we'd hit
    the empty-state / no-pool fallback and skip the toggle markup)."""
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from recon_gen.common.db import make_connection_pool  # noqa: PLC0415
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_etl_coverage_section,
    )
    from recon_gen.common.l2.deploy_pipeline import DeploySummary  # noqa: PLC0415
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    prefix = writable_l2_yaml.stem
    fd, db_path = tempfile.mkstemp(suffix=".duckdb")

    os.close(fd)

    os.unlink(db_path)
    conn = duckdb.connect(db_path)
    conn.execute(
        f"CREATE TABLE {prefix}_transactions ("
        "id TEXT PRIMARY KEY, rail_name TEXT, template_name TEXT, "
        "account_role TEXT, account_parent_role TEXT, "
        "amount_direction TEXT NOT NULL, transfer_parent_id TEXT, "
        "posting TIMESTAMP NOT NULL, metadata TEXT)"
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.DUCKDB, demo_database_url=db_path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        # Seed a DeploySummary so the renderer skips the
        # "no run + no rows ⇒ empty-state" branch and renders
        # the populated coverage chrome (cards + toggle).
        summary = DeploySummary(halted=False)
        body = asyncio.run(_render_etl_coverage_section(
            db_pool=pool, dialect=Dialect.DUCKDB,
            prefix=prefix, instance=cache.get(),
            last_summary=summary,
        ))
    finally:
        asyncio.run(pool.close())
        if os.path.exists(db_path):
            os.unlink(db_path)
    assert 'id="etl-coverage-failures-only"' in body
    assert "Show failures only" in body
    # CSS rule + JS shim both land.
    assert "data-failures-only" in body
    # Per-row status attribute is rendered so the CSS selector matches.
    assert 'data-coverage-status="missing"' in body


def test_etl_run_just_ran_query_param_triggers_flash_and_pulse(
    writable_l2_yaml: Path,
) -> None:
    """BTa.6 — `?just_ran=1` triggers a CSS flash animation + a 5s
    `document.title` pulse so an operator multi-tasking in another
    tab gets a visual nudge that the refresh finished."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run?just_ran=1").text
    # Flash CSS animation lands.
    assert "etlRunFlash" in body
    assert "animation: etlRunFlash" in body
    # Title pulse JS lands.
    assert "✓ Done · " in body
    assert "setTimeout" in body


def test_etl_run_no_just_ran_omits_flash_assets(
    writable_l2_yaml: Path,
) -> None:
    """Plain GET (no `?just_ran=1`) ⇒ flash CSS + title pulse omitted
    so the page stays quiet on a refresh / direct nav."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run").text
    assert "etlRunFlash" not in body
    assert "✓ Done · " not in body


def test_etl_run_post_redirects_with_just_ran_query_param(
    writable_l2_yaml: Path,
) -> None:
    """The POST handler now redirects with `?just_ran=1` to trigger
    the flash + pulse on the subsequent GET. The unit-test surface
    has no cfg so the POST falls through to the legacy /etl/
    bounce — test only the redirect-shape contract."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/etl/run", follow_redirects=False)
        assert resp.status_code == 303
        # Without cfg this branches to /etl/; only assert that when cfg
        # is plumbed (browser layer) the redirect carries just_ran.
        # Unit surface validates the bounce stays well-behaved.
        assert resp.headers["location"] == "/etl/"


def test_etl_run_log_renders_level_token_and_timing_delta() -> None:
    """BTa.6 — every log line carries `[level]` (info/warn/error)
    inferred from the event-name suffix, plus a `+Δms` against the
    prior event."""
    from recon_gen.common.html._studio_routes import _render_etl_run_log  # noqa: PLC0415
    from recon_gen.common.l2.deploy_pipeline import DeploySummary  # noqa: PLC0415

    summary = DeploySummary(
        halted=False,
        events=(
            {"event": "deploy:step1:start", "ts_unix": 1000.000, "cmd": ["ls"]},
            {"event": "deploy:step1:done", "ts_unix": 1000.150, "exit_code": 0},
            {"event": "deploy:step2:skip", "ts_unix": 1000.200, "reason": "n/a"},
            {"event": "deploy:halt", "ts_unix": 1000.300, "reason": "boom"},
        ),
    )
    html = _render_etl_run_log(summary)
    # First event has no prior — no delta.
    # Second event +150ms.
    assert "+150ms" in html
    # Third event +50ms (relative to step1:done, not start).
    assert "+50ms" in html
    # Levels surface per event.
    assert "data-test-log-level=\"info\"" in html
    assert "data-test-log-level=\"warn\"" in html
    assert "data-test-log-level=\"error\"" in html


def test_emit_stamps_ts_unix_when_absent() -> None:
    """BTa.6 — _emit auto-stamps `ts_unix` so every event the renderer
    sees has a wall-clock timestamp for Δms calculation."""
    import asyncio  # noqa: PLC0415

    from recon_gen.common.l2.deploy_pipeline import _emit  # noqa: PLC0415

    captured: list[dict[str, object]] = []

    async def _writer(payload: object) -> None:
        captured.append(dict(payload))  # type: ignore[arg-type]: Mapping → dict for assertion shape

    asyncio.run(_emit(_writer, {"event": "deploy:test:done"}))
    assert len(captured) == 1
    assert "ts_unix" in captured[0]
    assert isinstance(captured[0]["ts_unix"], float)


def test_emit_preserves_caller_provided_ts_unix() -> None:
    """Caller-provided ts_unix wins — useful for deterministic tests
    + replay scenarios."""
    import asyncio  # noqa: PLC0415

    from recon_gen.common.l2.deploy_pipeline import _emit  # noqa: PLC0415

    captured: list[dict[str, object]] = []

    async def _writer(payload: object) -> None:
        captured.append(dict(payload))  # type: ignore[arg-type]: Mapping → dict for assertion shape

    asyncio.run(_emit(_writer, {"event": "x", "ts_unix": 42.0}))
    assert captured[0]["ts_unix"] == 42.0


def test_metadata_coverage_denominator_matches_visible_list(
    writable_l2_yaml: Path,
) -> None:
    """BTa.6 — denominator math fix: every displayed row contributes
    its `per_key_count` to the headline denominator. The prior bug
    silently dropped 0-row templates from `total_keys` even though
    they still rendered as `<li>` entries → headline disagreed with
    the visible list.

    Test exercises the render directly with a hand-built md_map
    keyed by spec_example's actual template names so we don't have
    to fabricate a full L2Instance (TransferTemplate carries many
    required fields under pyright strict).
    """
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_metadata_coverage_card,
    )
    from recon_gen.common.l2.coverage import TemplateMetadataCoverage  # noqa: PLC0415

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    instance = cache.get()
    templates = list(instance.transfer_templates)
    if len(templates) < 2:
        import pytest as _pt  # noqa: PLC0415
        _pt.skip("spec_example has fewer than 2 transfer_templates")
    # One template with rows + all keys landed; one with 0 rows.
    md_map = {
        str(templates[0].name): TemplateMetadataCoverage(
            template_name=str(templates[0].name),
            row_count=10, per_key={"key_a": 10, "key_b": 10},
        ),
        str(templates[1].name): TemplateMetadataCoverage(
            template_name=str(templates[1].name),
            row_count=0, per_key={"key_x": 0, "key_y": 0},
        ),
    }
    html = _render_metadata_coverage_card(instance, md_map)
    # Headline denominator counts BOTH templates' key universes
    # (2 + 2 = 4); landed = 2 (only the with-rows template lands keys).
    assert ">2</strong>" in html
    assert ">4</strong>" in html
    assert "(50%)" in html
    # Per-row entries surface for both templates.
    assert str(templates[0].name) in html
    assert str(templates[1].name) in html



# -- BTa.8 cold-read v3 — empty-state when no run this session -----------


def test_coverage_renders_empty_state_when_no_run_this_session_even_with_rows(
    writable_l2_yaml: Path,
) -> None:
    """Cold-read v3 finding: pre-existing rows in the demo DB
    (from prior sessions / CLI `data apply` / planted overlays)
    must NOT render as green ✓ in Coverage when no Refresh Data
    has run this Studio session. Before this fix, operators saw
    100% green Coverage despite having never clicked Refresh
    Data — a trust killer. Now the empty-state copy renders
    regardless of `total_rows`."""
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from recon_gen.common.db import make_connection_pool  # noqa: PLC0415
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_etl_coverage_section,
    )
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    prefix = writable_l2_yaml.stem
    fd, db_path = tempfile.mkstemp(suffix=".duckdb")

    os.close(fd)

    os.unlink(db_path)
    conn = duckdb.connect(db_path)
    conn.execute(
        f"CREATE TABLE {prefix}_transactions ("
        "id TEXT PRIMARY KEY, rail_name TEXT, template_name TEXT, "
        "account_role TEXT, account_parent_role TEXT, "
        "amount_direction TEXT NOT NULL, transfer_parent_id TEXT, "
        "posting TIMESTAMP NOT NULL, metadata TEXT)"
    )
    conn.execute(
        f"INSERT INTO {prefix}_transactions VALUES "
        "('tx-1', 'some_rail', NULL, 'X', NULL, 'Credit', NULL, "
        "'2030-01-05 09:00:00', NULL)"
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.DUCKDB, demo_database_url=db_path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        body = asyncio.run(_render_etl_coverage_section(
            db_pool=pool, dialect=Dialect.DUCKDB,
            prefix=prefix, instance=cache.get(),
            last_summary=None,
        ))
    finally:
        asyncio.run(pool.close())
        if os.path.exists(db_path):
            os.unlink(db_path)
    # Empty-state takes over — no per-card list, no ✓ marks.
    assert 'id="etl-coverage-empty"' in body
    assert "No Refresh Data run this session" in body
    # Coverage cards themselves don't render.
    assert 'data-test-card=' not in body


# -- BTa.8 cold-read v3 — Refresh-Data context strip ----------------------


def test_refresh_context_strip_renders_deployment_dialect_hook() -> None:
    """BTa.8 cold-read v3 — the "What clicking Refresh Data will do"
    strip surfaces deployment_name + dialect + etl_hook so the
    operator knows what they're about to wipe + repopulate. Cold-read
    finding: the button gave zero hint about its target."""
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_refresh_context_strip,
    )
    html = _render_refresh_context_strip(
        deployment_name="prod-acme",
        dialect_label="postgres",
        etl_hook_command="./bin/load_acme.py",
        demo_gaps_planted=False,
    )
    assert "data-test-refresh-context" in html
    assert "prod-acme" in html
    assert "postgres" in html
    assert "./bin/load_acme.py" in html
    assert "What clicking" in html


def test_refresh_context_strip_flags_bundled_demo_when_no_hook() -> None:
    """When etl_hook is None, the strip says (none configured —
    bundled demo regeneration will run) + flags the demo-gap overlay
    when planting is on. Operator's question 'why am I getting fake
    bad data?' has an in-page answer."""
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_refresh_context_strip,
    )
    html = _render_refresh_context_strip(
        deployment_name="qsgen-sqlite",
        dialect_label="sqlite",
        etl_hook_command=None,
        demo_gaps_planted=True,
    )
    assert "bundled demo regeneration" in html
    assert "demo gap overlay" in html


def test_refresh_context_strip_empty_when_no_inputs() -> None:
    """Unit-test surface (no cfg) gets an empty strip — no orphan
    chrome rendered."""
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_refresh_context_strip,
    )
    assert _render_refresh_context_strip(
        deployment_name=None, dialect_label=None,
        etl_hook_command=None, demo_gaps_planted=False,
    ) == ""


def test_etl_run_page_drops_redundant_page_header(
    writable_l2_yaml: Path,
) -> None:
    """BTa.8 cold-read v3 — the redundant
    `<header><h1>Studio · ETL · Refresh Data</h1> qsgen-sqlite</header>`
    strip is gone (the sub-nav already conveys the active page +
    the new context strip carries the deployment chip)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run").text
    # The `<h1>Studio · ETL · Refresh Data</h1>` row is gone.
    assert "<h1>Studio · ETL · Refresh Data</h1>" not in body
    # But the page title in <head> stays — that's the browser tab.
    # CG.21 (2026-06-05) unified the title shape; deployment-name
    # dropped from non-home titles.
    assert "<title>Recon-Gen · Studio · ETL · Refresh Data</title>" in body


# -- BTa.9 — live tail + cancel ------------------------------------------


def test_render_live_tail_mount_carries_htmx_poll_attrs() -> None:
    """BTa.9 — the mount-point sets up the htmx poll loop. Without
    `load` + `every 1s` triggers + the `/etl/run/stream` initial
    URL, the operator stares at "Waiting for events…" indefinitely."""
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_etl_live_tail_mount,
    )
    html = _render_etl_live_tail_mount()
    assert 'hx-get="/etl/run/stream"' in html
    assert 'hx-trigger="load, every 1s"' in html
    assert 'id="etl-run-live-tail"' in html
    # When the stream returns HX-Trigger: etl-run-finished, the
    # inline script navigates to /etl/run?just_ran=1.
    assert "etl-run-finished" in html
    assert "/etl/run?just_ran=1" in html


def test_render_live_tail_fragment_renders_all_accumulated_events() -> None:
    """BTa.9 (cold-read iter) — the stream endpoint returns the FULL
    accumulated event list each poll, not just the delta. Each
    `outerHTML` swap re-renders the whole tail so history
    accumulates visually instead of getting clobbered."""
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_etl_live_tail_fragment,
    )
    html = _render_etl_live_tail_fragment(
        all_events=[
            {"event": "deploy:step1:start", "ts_unix": 1000.0, "cmd": "ls"},
            {"event": "deploy:step1:done", "ts_unix": 1000.1, "exit_code": 0},
        ],
        running=True,
    )
    assert 'data-test-live-tail-count="2"' in html
    assert 'data-test-live-tail-state="running"' in html
    # Next poll re-fetches the FULL list (no `?since=` param).
    assert 'hx-get="/etl/run/stream"' in html
    assert "?since=" not in html
    # Event lines render with their inferred levels.
    assert 'data-test-live-event-level="info"' in html
    assert "deploy:step1:start" in html
    assert "deploy:step1:done" in html


def test_render_live_tail_fragment_finished_state_stops_polling() -> None:
    """When the task is done, the fragment omits the polling htmx
    attrs + marks state=finished. The server emits HX-Trigger:
    etl-run-finished alongside the response so the page reloads."""
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_etl_live_tail_fragment,
    )
    html = _render_etl_live_tail_fragment(
        all_events=[{"event": "deploy:halt", "reason": "x", "ts_unix": 1.0}],
        running=False,
    )
    assert 'data-test-live-tail-state="finished"' in html
    # No polling trigger when finished.
    assert "every 1s" not in html
    # The halt event renders as error level.
    assert 'data-test-live-event-level="error"' in html


def test_render_live_tail_fragment_empty_initial_state() -> None:
    """No events yet (task just started) — show the 'Waiting for
    events…' placeholder, not an empty div."""
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_etl_live_tail_fragment,
    )
    html = _render_etl_live_tail_fragment(
        all_events=[], running=True,
    )
    assert "Waiting for events" in html


def test_etl_run_page_renders_cancel_button_when_is_running(
    writable_l2_yaml: Path,
) -> None:
    """When `is_running` is True the form swaps Refresh Data for a
    Cancel button + a "Pipeline running" indicator."""
    import asyncio  # noqa: PLC0415

    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_etl_run_page,
    )

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    html = asyncio.run(_render_etl_run_page(
        cache, dev_log=False,
        last_summary=None, last_run_at=None,
        db_pool=None, dialect=None,
        prefix_override=None, cfg=make_test_config(),
        top_nav_html="",
        is_running=True,
    ))
    assert 'id="etl-run-cancel-btn"' in html
    assert "✕ Cancel run" in html
    assert "data-test-running-indicator" in html
    # The static Refresh Data button is hidden while running.
    assert 'id="etl-run-btn"' not in html
    # The live-tail mount lands so events stream into it.
    assert 'id="etl-run-live-tail-wrap"' in html


def test_etl_run_stream_endpoint_returns_fragment(
    writable_l2_yaml: Path,
) -> None:
    """`GET /etl/run/stream` returns the live-tail fragment. With no
    events stored (no task launched), since=0 + state=finished."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/etl/run/stream")
        assert resp.status_code == 200
        body = resp.text
        # No active task ⇒ state=finished ⇒ HX-Trigger header fires
        # so any client polling sees the run as done.
        assert resp.headers.get("HX-Trigger") == "etl-run-finished"
    assert 'data-test-live-tail-state="finished"' in body


def test_etl_run_cancel_endpoint_303s_to_run_page(
    writable_l2_yaml: Path,
) -> None:
    """`POST /etl/run/cancel` is a no-op when no task is in flight,
    but still 303s back to /etl/run so the operator-facing form's
    submit is well-behaved."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/etl/run/cancel", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/etl/run"
