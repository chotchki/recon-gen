"""X.2.m — error handling tests.

Verifies the Starlette exception handlers in ``server.make_app``
return themed 5xx + 404 pages (not framework defaults), that
``dev_log`` gates traceback exposure, and that ``emit_error_page``
itself uses semantic theme tokens.

Mirrors the ``test_html_theme_integration`` shape:

- TestClient against a wired ServedDashboard.
- Semantic-token assertions (no hardcoded ``-blue-NNN`` /
  ``-slate-NNN`` palette utilities).
- ``--color-danger`` declaration appears on every error page so the
  per-instance theme cascades through.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from starlette.testclient import TestClient

from tests._test_helpers import make_test_config
from quicksight_gen.common.html.render import emit_error_page
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.theme import DEFAULT_PRESET
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import KPI


_DASHBOARD_ID = "x2m-test"
_SHEET_ID = "test-sheet"
_VISUAL_ID = "v-test"


def _build_app() -> tuple[App, Sheet]:
    cfg = make_test_config()
    app = App(name="x2m-test", cfg=cfg)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="x2m-test-analysis",
        name="X2m Test",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId(_SHEET_ID),
        name="X2m",
        title="X2m Sheet",
        description="x",
    ))
    sheet.visuals.append(
        KPI(title="K", subtitle=None, visual_id=VisualId(_VISUAL_ID)),
    )
    return app, sheet


def _make_client(
    fetcher: Any = None, *,
    dev_log: bool = False,
    theme: Any = None,
) -> TestClient:
    tree_app, sheet = _build_app()
    asgi = make_app(
        dashboards={
            _DASHBOARD_ID: ServedDashboard(
                tree_app=tree_app,
                sheet=sheet,
                title="X2m Test",
                data_fetcher=fetcher or (lambda _v, _p: {}),
                theme=theme,
            ),
        },
        dev_log=dev_log,
    )
    # raise_server_exceptions=False so Starlette runs the 500 handler
    # instead of letting the test client re-raise the original
    # exception (which is what happens in production at the ASGI
    # boundary).
    return TestClient(asgi, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# emit_error_page contract
# ---------------------------------------------------------------------------


def test_emit_error_page_includes_status_code_and_headline() -> None:
    """The page surfaces the HTTP code + headline so an operator
    glancing at a screenshot can confirm what went wrong."""
    out = emit_error_page(
        status_code=500,
        headline="Something went wrong",
        subtitle="Try again later.",
    )
    assert "Something went wrong" in out
    assert "HTTP 500" in out
    assert "Try again later." in out


def test_emit_error_page_includes_back_link() -> None:
    """Every error page sends the user back to ``/dashboards`` so a
    bookmark for a deleted dashboard can recover without typing."""
    out = emit_error_page(
        status_code=404,
        headline="Not found",
        subtitle="x",
    )
    assert 'href="/dashboards"' in out
    assert "Back to dashboards" in out


def test_emit_error_page_hides_traceback_when_none() -> None:
    """Production callers pass ``traceback_text=None`` so internals
    don't leak. Confirm the ``<details>`` block is absent in that
    mode."""
    out = emit_error_page(
        status_code=500,
        headline="x", subtitle="y",
    )
    assert "<details" not in out
    assert "Traceback" not in out


def test_emit_error_page_includes_traceback_when_provided() -> None:
    """Dev mode wraps the traceback in a collapsible ``<details>``
    so the developer can read it without it dominating the page."""
    out = emit_error_page(
        status_code=500,
        headline="x", subtitle="y",
        traceback_text="ZeroDivisionError: division by zero\n  at line 42",
    )
    assert "<details" in out
    assert "Traceback" in out
    assert "ZeroDivisionError" in out
    assert "line 42" in out


def test_emit_error_page_escapes_traceback_html() -> None:
    """Traceback strings can contain ``<`` / ``>`` characters
    (e.g. inside ``__repr__`` output). Escape them so the page
    renders the text instead of injecting markup."""
    out = emit_error_page(
        status_code=500,
        headline="x", subtitle="y",
        traceback_text="oops <script>alert('x')</script>",
    )
    # The literal ``<script>alert`` string must NOT appear unescaped.
    assert "<script>alert" not in out
    assert "&lt;script&gt;alert" in out


def test_emit_error_page_uses_default_theme_when_none() -> None:
    """No theme arg → DEFAULT_PRESET fallback. Mirrors the
    silent-fallback contract in emit_html / emit_dashboards_list."""
    out = emit_error_page(
        status_code=500, headline="x", subtitle="y",
    )
    assert f"--color-accent: {DEFAULT_PRESET.accent}" in out
    assert f"--color-danger: {DEFAULT_PRESET.danger}" in out


def test_emit_error_page_overrides_with_provided_theme() -> None:
    """Per-instance theme threads through — the L2 customer's brand
    color appears on the error page, not the default."""
    custom = replace(DEFAULT_PRESET, accent="#deadbe", danger="#bada55")
    out = emit_error_page(
        status_code=500, headline="x", subtitle="y",
        theme=custom,
    )
    assert "--color-accent: #deadbe" in out
    assert "--color-danger: #bada55" in out


def test_emit_error_page_uses_semantic_tokens() -> None:
    """Sweep guard — no literal Tailwind palette utilities. Same
    rule as emit_html. If a future copy edit drops in
    ``text-red-600``, the regex catches it."""
    out = emit_error_page(
        status_code=500, headline="x", subtitle="y",
        traceback_text="trace",
    )
    pattern = re.compile(r"\b(?:bg|text|border|fill|stroke|ring)-"
                         r"(?:blue|slate|red|green|amber|emerald|rose|"
                         r"violet|cyan|orange|indigo)-\d+")
    leaks = pattern.findall(out)
    assert leaks == [], (
        f"Hardcoded Tailwind palette utilities leaked into error "
        f"page: {sorted(set(leaks))}. Use semantic tokens "
        f"(text-danger, bg-accent, etc.)."
    )
    # Positive proof — semantic tokens we expect on the error shell.
    assert "text-danger" in out
    assert "bg-accent" in out
    assert "text-accent-fg" in out


# ---------------------------------------------------------------------------
# 404 handler — themed not-found page
# ---------------------------------------------------------------------------


def test_unknown_dashboard_returns_themed_404() -> None:
    """Stale bookmark for a deleted dashboard renders the themed
    404 page, not Starlette's default ``Not Found`` plain text."""
    client = _make_client()
    resp = client.get("/dashboards/nonexistent")
    assert resp.status_code == 404
    body = resp.text
    assert "Not found" in body
    assert "HTTP 404" in body
    assert 'href="/dashboards"' in body
    # Confirm it's the themed shell, not a Starlette default.
    assert "--color-danger" in body
    assert "/static/output.css" in body


def test_unknown_route_returns_themed_404() -> None:
    """A path that matches no route at all (typo, garbage URL) also
    lands on the themed 404 — the handler is registered against the
    HTTP code, so Starlette's no-match 404s pick it up too."""
    client = _make_client()
    resp = client.get("/this-is-not-a-route")
    assert resp.status_code == 404
    assert "Not found" in resp.text


def test_404_carries_per_dashboard_theme() -> None:
    """When the wired dashboard has a custom theme, the 404 page
    inherits it via the listing-theme aggregation. Mirrors the
    listing page's theme-picking convention."""
    custom = replace(DEFAULT_PRESET, accent="#deadbe")
    client = _make_client(theme=custom)
    resp = client.get("/dashboards/nonexistent")
    assert "--color-accent: #deadbe" in resp.text


def test_stale_sheet_id_returns_themed_404() -> None:
    """Confirms the visual_data 404 path also goes through the
    themed handler — wrong sheet in the URL."""
    client = _make_client()
    bad_path = (
        f"/dashboards/{_DASHBOARD_ID}/sheets/wrong-sheet"
        f"/visuals/{_VISUAL_ID}/data"
    )
    resp = client.get(bad_path)
    assert resp.status_code == 404
    # The visual_data path returns themed HTML (same handler as the
    # dashboard 404). Caller can still see the body if they're
    # debugging in a browser.
    assert "Not found" in resp.text


# ---------------------------------------------------------------------------
# 500 handler — themed server-error page
# ---------------------------------------------------------------------------


def _boom_fetcher(_visual_id: str, _params: dict[str, str]) -> Any:
    raise ValueError("simulated DB outage — table does not exist")


def test_uncaught_exception_returns_themed_500() -> None:
    """A fetcher that raises mid-request → 500 with the themed
    error page. Without the handler the user would see Starlette's
    default ``Internal Server Error`` plain-text body."""
    client = _make_client(_boom_fetcher)
    resp = client.get(
        f"/dashboards/{_DASHBOARD_ID}/sheets/{_SHEET_ID}"
        f"/visuals/{_VISUAL_ID}/data",
    )
    assert resp.status_code == 500
    body = resp.text
    assert "Something went wrong" in body
    assert "HTTP 500" in body
    assert 'href="/dashboards"' in body
    assert "--color-danger" in body


def test_500_hides_traceback_in_production() -> None:
    """Production mode (``dev_log=False``, the default) MUST NOT
    leak the traceback. The exception type name + message must not
    appear in the response body."""
    client = _make_client(_boom_fetcher, dev_log=False)
    resp = client.get(
        f"/dashboards/{_DASHBOARD_ID}/sheets/{_SHEET_ID}"
        f"/visuals/{_VISUAL_ID}/data",
    )
    body = resp.text
    assert "ValueError" not in body
    assert "simulated DB outage" not in body
    assert "<details" not in body
    assert "Traceback" not in body


def test_500_includes_traceback_in_dev_mode() -> None:
    """``dev_log=True`` carries the traceback inside ``<details>`` so
    the developer can read it without leaving the browser."""
    client = _make_client(_boom_fetcher, dev_log=True)
    resp = client.get(
        f"/dashboards/{_DASHBOARD_ID}/sheets/{_SHEET_ID}"
        f"/visuals/{_VISUAL_ID}/data",
    )
    body = resp.text
    assert "<details" in body
    assert "ValueError" in body
    assert "simulated DB outage" in body


def test_500_carries_per_dashboard_theme() -> None:
    """Same theme-inheritance contract as the 404 page."""
    custom = replace(DEFAULT_PRESET, accent="#cafe00")
    client = _make_client(_boom_fetcher, theme=custom)
    resp = client.get(
        f"/dashboards/{_DASHBOARD_ID}/sheets/{_SHEET_ID}"
        f"/visuals/{_VISUAL_ID}/data",
    )
    assert "--color-accent: #cafe00" in resp.text
