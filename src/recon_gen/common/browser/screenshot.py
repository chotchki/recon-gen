"""Dashboard screenshot capture for the handbook — self-hosted App2 engine.

PRE-DW this module captured *deployed QuickSight* dashboards: it minted
a pre-authenticated QS embed URL and walked the iframe sheet-by-sheet.
QuickSight was removed in Phase DW, so the embed-URL path is gone. DY.9
rebuilds capture against the self-hosted renderer — the same App2
Starlette server ``recon-gen dashboards`` runs, spun on an ephemeral
localhost port, driven by WebKit.

The flow (``capture_app_dashboards``):

1. Spin the real dashboards server IN A DAEMON THREAD on port 0. The
   connection pool is created INSIDE that thread's event loop — an
   ``AsyncConnectionPool`` binds to the loop it was opened in, and
   ``uvicorn.Server.serve()`` owns its loop, so a pool created in the
   main thread would deadlock on first acquire (the X.2.g.2.d footgun
   the CLI serve path also dances around). The ServedDashboard wiring
   (visual fetcher + options-search fetcher + day-availability +
   data-anchor) rides ``_html_serve.build_real_dashboards`` — POLICY 1:
   the screenshot server IS the production serve path, not a parallel
   reimplementation that could drift.
2. WebKit navigates ``/dashboards/<id>/sheets/<sheet_id>`` per sheet.
   App2 routing is STATELESS — a sheet switch is just a new URL (unlike
   QS's tab-teardown dance), so we navigate rather than click tabs.
3. ``page.screenshot(full_page=True)`` per sheet → ``<sheet_id>.png``.
4. ``url_params`` (the ``--date-from`` / ``--date-to`` overrides) move
   from QS's ``#p.<name>=`` embed-hash to App2's ``?param_<name>=``
   query string — App2 threads ``?param_*`` into the filter form's
   initial state.

``webkit_page`` + its failure-capture sidecar in ``helpers.py`` are the
kept primitives this build reuses. Real end-to-end coverage lives in
``tests/e2e/app2_browser/test_screenshot_capture.py`` (seeded DB → real
capture → one non-empty PNG per sheet).
"""

from __future__ import annotations

import contextlib
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator
from urllib.parse import urlencode

from recon_gen.common.tree import App, Sheet

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.theme import ThemePreset


# How long to wait for uvicorn to bind + accept in the background thread
# before giving up. A cold DuckDB open / PG pool fill is well under this;
# beyond it usually means a port-conflict or a cfg / dataset wiring error
# surfaced at pool-create time (re-raised from the dead thread).
_SERVER_STARTUP_TIMEOUT_S = 20.0


@contextlib.contextmanager
def _serve_dashboard_in_thread(
    tree_app: App,
    landing_sheet: Sheet,
    *,
    cfg: "Config",
    dashboard_id: str,  # typing-smell: ignore[bare-str-id]: raw CLI app slug (URL segment), same as ServedDashboard/html2_server
    theme: "ThemePreset | None",
) -> Generator[str, None, None]:
    """Run the real dashboards server for ``tree_app`` on an ephemeral
    port in a daemon thread; yield its ``http://127.0.0.1:<port>`` base.

    The pool + uvicorn share ONE event loop (created by ``asyncio.run``
    inside the thread) so the pool's filler task stays alive — the exact
    lifecycle ``cli/_html_serve.run_html_server`` uses, minus the
    blocking foreground ``asyncio.run``. Startup failures (port bind,
    pool create, dataset wiring) are captured off the thread and
    re-raised here so the caller sees the real cause, not a bare timeout.
    """
    import asyncio  # noqa: PLC0415 — stdlib, but kept local to the serve path

    import uvicorn  # noqa: PLC0415 — [serve]-extra only; lazy so no-[serve] installs import

    from recon_gen.cli._html_serve import build_real_dashboards  # noqa: PLC0415
    from recon_gen.common.db import make_connection_pool  # noqa: PLC0415
    from recon_gen.common.html.server import make_app  # noqa: PLC0415

    holder: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _thread_main() -> None:
        async def _serve() -> None:
            pool = await make_connection_pool(cfg, max_size=cfg.db.app2_pool_size)
            try:
                dashboards = build_real_dashboards(
                    [(dashboard_id, tree_app, landing_sheet)],
                    cfg, pool=pool, theme=theme,
                )
                asgi = make_app(
                    dashboards=dashboards,
                    dev_log=False,
                    # Offline capture must never render a stale cached
                    # fragment — every sheet reflects a fresh fetch.
                    visual_data_cache_max_age_s=0,
                    banner_text=cfg.app2.banner_text,
                )
                config = uvicorn.Config(
                    asgi, host="127.0.0.1", port=0, log_level="error",
                )
                server = uvicorn.Server(config)
                holder["server"] = server
                await server.serve()
            finally:
                await pool.close()

        try:
            asyncio.run(_serve())
        except BaseException as exc:  # noqa: BLE001 — surfaced to the main thread below
            error["exc"] = exc

    thread = threading.Thread(target=_thread_main, daemon=True)
    thread.start()
    deadline = time.monotonic() + _SERVER_STARTUP_TIMEOUT_S
    while True:
        if "exc" in error:
            raise RuntimeError(
                f"dashboards server thread for {dashboard_id!r} died during "
                f"startup"
            ) from error["exc"]
        server = holder.get("server")
        if server is not None and server.started:
            break
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"dashboards server for {dashboard_id!r} failed to start "
                f"within {_SERVER_STARTUP_TIMEOUT_S}s"
            )
        time.sleep(0.05)
    sock = server.servers[0].sockets[0]
    port = sock.getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _wait_for_sheet_render(page: Any, timeout_ms: int) -> None:
    """Best-effort wait for a sheet's visual sections to mount.

    The visual sections auto-load via ``hx-trigger="load"``; the caller's
    ``goto(wait_until="networkidle")`` already waits those requests out,
    so this is a belt-and-suspenders confirm that at least one
    ``section[data-visual-kind]`` attached before the paint settle. A
    sheet with zero visuals is theoretically possible (none ship that
    way today), so a timeout here degrades to screenshotting the chrome
    rather than failing the whole walk.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415

    with contextlib.suppress(PlaywrightTimeoutError):
        page.wait_for_selector(
            "section[data-visual-kind]", timeout=timeout_ms, state="attached",
        )


def capture_app_dashboards(
    tree_app: App,
    *,
    cfg: "Config",
    dashboard_id: str,  # typing-smell: ignore[bare-str-id]: raw CLI app slug (URL segment), same as ServedDashboard/html2_server
    theme: "ThemePreset | None" = None,
    output_dir: Path,
    viewport: tuple[int, int] = (1280, 900),
    initial_settle_ms: int = 10_000,
    per_sheet_settle_ms: int = 8_000,
    page_timeout_ms: int = 120_000,
    headless: bool = True,
    url_params: dict[str, str] | None = None,
) -> dict[Sheet, Path]:
    """Capture one full-page PNG per sheet of ``tree_app``'s dashboard.

    Spins the real App2 dashboards server for ``tree_app`` on an
    ephemeral localhost port (``dashboard_id`` is the URL slug + the
    ``build_real_dashboards`` title key), drives WebKit sheet-by-sheet,
    and writes ``<sheet_id>.png`` per sheet into ``output_dir``.

    ``tree_app`` must arrive with its datasets already registered in the
    shared SQL registry (build it via ``_html_serve.build_real_app``,
    which registers them) — the fetcher wiring reads that registry at
    server-build time and fails loudly if an entry is missing.

    ``url_params`` are bare parameter names (e.g. ``{"pL1DateStart":
    "2030-01-01"}``); each becomes an App2 ``?param_<name>=<value>`` on
    every sheet URL (unknown-to-a-sheet params are harmlessly ignored,
    same as the QS embed-hash applied globally).

    ``initial_settle_ms`` / ``per_sheet_settle_ms`` are paint-settle
    delays AFTER ``networkidle`` — the d3 hydration runs client-side once
    the HTMX swap lands, so a short settle buys a clean frame.

    Returns ``dict[Sheet, Path]`` keyed by Sheet object ref (filenames
    ``<sheet_id>.png``), matching the contract the handbook templates
    expect.
    """
    from recon_gen.common.browser.helpers import webkit_page  # noqa: PLC0415

    analysis = tree_app.analysis
    if analysis is None or not analysis.sheets:
        raise ValueError(
            f"tree_app {dashboard_id!r} has no analysis sheets to capture — "
            f"build it via _html_serve.build_real_app first."
        )
    # Internal IDs (VisualId etc.) resolve on first emit anyway, but do it
    # up front (idempotent) so every sheet renders identically regardless
    # of navigation order.
    tree_app.resolve_auto_ids()
    sheets = list(analysis.sheets)
    landing = sheets[0]

    output_dir.mkdir(parents=True, exist_ok=True)
    query = ""
    if url_params:
        query = urlencode({f"param_{name}": value for name, value in url_params.items()})

    results: dict[Sheet, Path] = {}
    with _serve_dashboard_in_thread(
        tree_app, landing, cfg=cfg, dashboard_id=dashboard_id, theme=theme,
    ) as base_url, webkit_page(headless=headless, viewport=viewport) as page:
        page.set_default_timeout(page_timeout_ms)
        for idx, sheet in enumerate(sheets):
            sheet_id = str(sheet.sheet_id)
            url = f"{base_url}/dashboards/{dashboard_id}/sheets/{sheet_id}"
            if query:
                url += f"?{query}"
            page.goto(url, timeout=page_timeout_ms, wait_until="networkidle")
            _wait_for_sheet_render(page, page_timeout_ms)
            settle = initial_settle_ms if idx == 0 else per_sheet_settle_ms
            if settle > 0:
                page.wait_for_timeout(settle)
            path = output_dir / f"{sheet_id}.png"
            page.screenshot(path=str(path), full_page=True)
            results[sheet] = path
    return results
