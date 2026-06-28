"""Dashboard screenshot capture for the handbook — App2 placeholder.

PRE-DW this module captured *deployed QuickSight* dashboards: it minted
a pre-authenticated QS embed URL (``generate_dashboard_embed_url``) and
walked the iframe sheet-by-sheet (``ScreenshotHarness`` /
``capture_deployed_app``). QuickSight was removed in Phase DW, so the
embed-URL capture path is gone.

The ``recon-gen docs screenshot`` command + its scaffolding
(``cli/docs.py``: per-app slug map, date-param resolution, output-dir
layout, DB warmup) are KEPT so the self-hosted App2 capture slots
straight in — only the renderer-specific capture engine below is a
placeholder.

App2 capture, when built (backlog — see PLAN.md DW):

1. Start the self-hosted dashboards server for ``app`` (the
   ``recon-gen dashboards`` Starlette app, bound to an ephemeral
   localhost port) instead of minting a QS embed URL.
2. ``with webkit_page(headless=headless, viewport=viewport) as page:``
   ``page.goto(f"http://127.0.0.1:<port>/dashboards/<slug>/")``.
3. Walk ``app.analysis.sheets`` — ``click_sheet_tab(page, sheet.name,
   timeout)``, settle, ``page.screenshot(path=out/f"{sheet_id}.png",
   full_page=True)`` — the same per-sheet loop the QS engine used (it
   was already renderer-agnostic once it had a URL).
4. ``url_params`` (the ``--date-from`` / ``--date-to`` overrides) move
   from QS's ``#p.<name>=`` embed-hash to App2's ``?param_<name>=``
   query string (the App2 URL-param contract — App2 threads
   ``?param_*`` into the filter form's initial state).

``webkit_page`` + the failure-capture sidecar in ``helpers.py`` are
the kept primitives this build reuses.
"""

from __future__ import annotations

from pathlib import Path

from recon_gen.common.tree import App, Sheet


def capture_app_dashboards(
    app: App,
    *,
    output_dir: Path,
    viewport: tuple[int, int] = (1280, 900),
    initial_settle_ms: int = 10_000,
    per_sheet_settle_ms: int = 8_000,
    page_timeout_ms: int = 120_000,
    headless: bool = True,
    url_params: dict[str, str] | None = None,
) -> dict[Sheet, Path]:
    """Capture one full-page PNG per sheet of ``app``'s dashboard.

    PLACEHOLDER — not yet implemented. The QuickSight embed-URL capture
    was removed in Phase DW; the self-hosted App2 capture (spin
    ``recon-gen dashboards`` → webkit localhost → per-sheet screenshot)
    is the planned replacement. The recipe lives in this module's
    docstring; the ``docs screenshot`` command + its scaffolding are
    wired and waiting for this engine.

    Returns ``dict[Sheet, Path]`` keyed by Sheet object ref (filenames
    ``{sheet_id}.png``), matching the contract the handbook templates
    already expect.
    """
    raise NotImplementedError(
        "Dashboard screenshot capture is not yet built. The QuickSight "
        "embed-URL path was removed in Phase DW (QuickSight removal); the "
        "self-hosted App2 capture (start `recon-gen dashboards`, drive "
        "webkit against the localhost URL, screenshot each sheet) is the "
        "planned replacement — see this module's docstring for the recipe "
        "and PLAN.md (DW backlog) for tracking."
    )
