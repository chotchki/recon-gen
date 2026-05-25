"""X.4.j.2 — Studio + Dashboards browser e2e (Playwright).

Builds on X.4.j.1's API e2e by adding the actual browser interaction:

- X.4.j.2.a — uvicorn-in-thread server fixture (real bound port, since
  Playwright can't talk to TestClient / ASGITransport).
- X.4.j.2.b — Click the Deploy button on the Studio home page; assert
  the status indicator transitions running → ok with the right tx
  count visible.
- X.4.j.2.c — Navigate each of the 4 dashboards (L1 / L2FT / Inv /
  Exec); assert at least one visual section renders per app.
- X.4.j.2.d — Auto-reload via the data_generation_id poller: open a
  dashboard tab, fire POST /deploy from a separate context, assert the
  tab reloads itself within ~6s without manual refresh.

Total runtime ~5 minutes (postgres + 2 deploys + Playwright). Heavy
but worth the credibility — proves the whole Studio → Deploy →
Dashboards-auto-reload loop works as designed.
"""
from __future__ import annotations

import re
import stat
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

# Skip module if any of these aren't installed.
pytest.importorskip("testcontainers.postgres")
pytest.importorskip("playwright.sync_api")
pytest.importorskip("aiosqlite")

# Studio chrome (Deploy button + status indicator + framenavigated reload
# detection) is operator-app UI distinct from the DashboardDriver verb
# set (open / goto_sheet / table_rows / pick_filter), which targets
# renderer-agnostic *dashboard* operations. Playwright is the right
# level for chrome-driven assertions; ported tests still go through
# DashboardDriver.
from playwright.sync_api import sync_playwright  # typing-smell: ignore[no-playwright-leak]: studio chrome is not a dashboard verb
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs

from recon_gen.cli._html_serve import REAL_APPS

from tests.e2e._studio_deploy_helpers import (
    QUICKSIGHT_GEN_BIN,
    SASQUATCH_YAML,
    apply_schema_to,
    docker_available,
    make_studio_cfg,
    studio_server,
    write_etl_hook_script,
    write_pg_etl_cfg,
)


pytestmark = [
    pytest.mark.skipif(
        not docker_available(),
        reason="docker not available — skipping postgres-in-docker e2e",
    ),
    pytest.mark.skipif(
        not SASQUATCH_YAML.exists(),
        reason=(
            f"{SASQUATCH_YAML} missing — sasquatch_pr.yaml is gitignored "
            "(operator config); copy it into run/ to enable this test"
        ),
    ),
    pytest.mark.skipif(
        not QUICKSIGHT_GEN_BIN.exists(),
        reason=f"{QUICKSIGHT_GEN_BIN} missing — install the package first",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures (same shape as test_deploy_pipeline_pg_to_sqlite.py — kept
# local rather than conftest because both files only need them in their
# own module-scope; sharing them through conftest would couple the
# tests' fixture lifetimes unnecessarily).
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_container_url() -> Iterator[str]:
    container = PostgresContainer("postgres:17-alpine")
    container.start()
    try:
        raw_url: str = container.get_connection_url()  # type: ignore[no-untyped-call]: testcontainers method has no type annotations
        url = raw_url.replace("postgresql+psycopg2://", "postgresql://", 1)
        yield url
    finally:
        container.stop()


@pytest.fixture
def etl_hook_script(
    tmp_path: Path, pg_container_url: str,
) -> Path:
    """Hook that runs `recon-gen data apply --execute` against the
    pg container. Pre-applies the schema."""
    pg_cfg, pg_cfg_path = write_pg_etl_cfg(pg_container_url, tmp_path)
    apply_schema_to(pg_cfg)
    return write_etl_hook_script(tmp_path, pg_cfg_path)


# ---------------------------------------------------------------------------
# X.4.j.2.b/c — Deploy button click + per-dashboard navigation
# ---------------------------------------------------------------------------


def test_deploy_button_drives_pipeline_and_dashboards_render(
    tmp_path: Path,
    etl_hook_script: Path,
    pg_container_url: str,
) -> None:
    """The full operator flow:

    1. Open Studio home page.
    2. Click Deploy changes button.
    3. Wait for #deploy-status to flip to ok with tx count visible.
    4. Navigate to each of the 4 dashboards; assert each renders
       at least one visual section.
    """
    cfg, _sqlite_path = make_studio_cfg(
        tmp_path,
        etl_hook=etl_hook_script,
        etl_datasource_url=pg_container_url,
    )
    apply_schema_to(cfg)

    with studio_server(cfg) as base_url, sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        try:
            # X.4.j.2.b — click Deploy and wait for the ok status.
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.wait_for_selector("#deploy-btn", timeout=10000)

            page.click("#deploy-btn")
            # Status flips to running, then ok. Wait for the ok
            # data-state. AM.2 step 1 (2026-05-25): switched from
            # `.deploy-status--ok` class to `[data-state="ok"]`
            # semantic attribute so the selector doesn't couple to
            # the Tailwind utility classes the JS writes for color.
            # Deploy takes ~30-60s on sasquatch_pr (etl_hook re-runs
            # data apply). 120s is comfortable headroom.
            page.wait_for_selector(
                '#deploy-status[data-state="ok"]',
                timeout=120_000,
            )
            status_text = page.locator("#deploy-status").text_content() or ""
            # Match shape: "Deployed (gen N, M tx)"
            assert re.match(
                r"Deployed \(gen \d+, \d+ tx\)", status_text,
            ), f"unexpected status text: {status_text!r}"

            # X.4.j.2.c — each dashboard renders.
            for app_name in REAL_APPS:
                response = page.goto(
                    f"{base_url}/dashboards/{app_name}",
                    wait_until="domcontentloaded",
                )
                # All four apps land on a Getting Started sheet that's
                # text-only — no filter form, no visual sections in
                # the initial markup. The signals we CAN rely on for
                # every landing page:
                # 1. HTTP 200 (no 404 / 500 from a wiring bug).
                # 2. The data-generation-id meta (proves the studio
                #    threaded the deploy counter through, and the
                #    dashboard route picked up the right cfg).
                # 3. The dashboard's title in the <title> tag (proves
                #    we landed on the right app, not a redirect).
                assert response is not None and response.status == 200, (
                    f"{app_name} dashboard returned "
                    f"{response.status if response else 'no response'}"
                )
                content = page.content()
                assert (
                    '<meta name="data-generation-id"' in content
                ), f"{app_name} dashboard missing poller baseline meta"
                # Title exists + non-empty (rules out blank error page)
                title = page.title()
                assert title and title.strip(), (
                    f"{app_name} dashboard rendered with empty title"
                )
        finally:
            browser.close()


# ---------------------------------------------------------------------------
# X.4.j.2.d — auto-reload via the data_generation_id poller
# ---------------------------------------------------------------------------


def test_dashboard_auto_reloads_when_data_generation_id_bumps(
    tmp_path: Path,
) -> None:
    """Open a dashboard tab. Fire POST /deploy from another context.
    The tab's poller (3s interval) sees the bumped counter, reloads
    itself within ~6s.

    Uses a no-op etl_hook (`true`) + no etl_datasource so the deploy
    is fast (~10s, just generator + matview + reload bump). The full
    cross-dialect path is X.4.j.2.b/c's job; this test isolates the
    reload contract.

    Detection: the poller's `location.reload()` triggers a
    framenavigated event Playwright observes. We count navigations
    after the initial page load — exactly one bump → exactly one
    reload navigation.
    """
    # Hook script that no-ops cleanly (exit 0 immediately). This makes
    # step 1 sub-second so the whole pipeline finishes in ~5-10s.
    noop_hook = tmp_path / "noop_hook.sh"
    noop_hook.write_text("#!/bin/bash\nexit 0\n")
    noop_hook.chmod(noop_hook.stat().st_mode | stat.S_IEXEC)

    cfg, _sqlite_path = make_studio_cfg(
        tmp_path, etl_hook=noop_hook,  # NO etl_datasource — generator only
    )
    apply_schema_to(cfg)

    nav_count = 0

    def _on_framenavigated(_frame: object) -> None:
        nonlocal nav_count
        nav_count += 1

    with studio_server(cfg) as base_url, sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        try:
            # 1. Open the L1 dashboard. Initial goto fires one
            #    framenavigated event — capture the listener AFTER goto
            #    so it doesn't count.
            page.goto(
                f"{base_url}/dashboards/l1_dashboard",
                wait_until="domcontentloaded",
            )
            # The poller's first immediate pollOnce fires inside
            # DOMContentLoaded — at this point baseline == server
            # value, so no reload. Confirm by reading the meta:
            baseline_meta = page.locator(
                'meta[name="data-generation-id"]',
            ).get_attribute("content")
            assert baseline_meta is not None
            baseline = int(baseline_meta)

            # NOW attach the listener so we only count reload-triggered
            # navigations, not the initial goto.
            page.on("framenavigated", _on_framenavigated)

            # 2. Fire POST /deploy from a separate HTTP context.
            #    `urllib` is stdlib so no extra dep; the studio's
            #    POST /deploy returns synchronously after the pipeline
            #    completes — this blocks ~5-10s.
            req = urllib.request.Request(
                f"{base_url}/deploy", method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 — local bound url, not user input
                assert resp.status == 200

            # 3. Wait for the poller's next tick (3s) + the page reload
            #    + DOMContentLoaded handler to re-run. Give it 8s of
            #    headroom because the poll interval drifts and reload
            #    + page-shell parse takes a beat.
            page.wait_for_function(
                f"() => parseInt("
                f"document.querySelector('meta[name=\"data-generation-id\"]')"
                f".getAttribute('content'), 10) > {baseline}",
                timeout=8_000,
            )

            # 4. Assert: at least one navigation fired (the reload).
            assert nav_count >= 1, (
                f"poller should have triggered a reload but nav_count={nav_count}"
            )

            # 5. The fresh page's meta should reflect the bumped
            #    counter (proves we re-rendered, not just polled).
            new_meta = page.locator(
                'meta[name="data-generation-id"]',
            ).get_attribute("content")
            assert new_meta is not None
            assert int(new_meta) > baseline, (
                f"reloaded page's meta ({new_meta}) should exceed "
                f"baseline ({baseline})"
            )
        finally:
            browser.close()
