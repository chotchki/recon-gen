"""X.2.l — theme integration tests.

Verifies the L2 ``ThemePreset`` flows through to inline CSS
variables in the page shell, falls back to ``DEFAULT_PRESET``
when no theme is supplied, and consistently renders semantic
Tailwind tokens (``bg-accent``, ``text-primary-fg``, etc.) in
the markup.

The renderer ships hardcoded utility classes that resolve their
colors from CSS variables declared in input.css's ``@theme`` block;
the runtime ``<style>:root { ... }</style>`` block injected by
``emit_html`` / ``emit_dashboards_list`` overrides those defaults
per L2 instance via the cascade.
"""

from __future__ import annotations

import re
from dataclasses import replace

from starlette.testclient import TestClient

from tests._test_helpers import make_test_config
from recon_gen.common.html.render import (
    emit_dashboards_list,
    emit_html,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.ids import SheetId, VisualId
from recon_gen.common.theme import DEFAULT_PRESET
from recon_gen.common.tree.structure import Analysis, App, Sheet
from recon_gen.common.tree.visuals import KPI


_TEST_CFG = make_test_config()


def _build_app() -> tuple[App, Sheet]:
    app = App(name="theme-test", cfg=_TEST_CFG)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="theme-test-analysis",
        name="Theme Test",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("theme-sheet"),
        name="Theme",
        title="Theme Sheet",
        description="x",
    ))
    sheet.visuals.append(
        KPI(title="K", subtitle="t", visual_id=VisualId("v-k")),
    )
    return app, sheet


# ---------------------------------------------------------------------------
# Default-fallback contract
# ---------------------------------------------------------------------------


def test_emit_html_injects_default_preset_when_no_theme() -> None:
    """No ``theme`` arg → page shell carries the DEFAULT_PRESET
    accent + fg + danger as ``--color-*`` declarations. Mirrors
    the QS dialect's CLASSIC silent fallback (N.4.k)."""
    app, sheet = _build_app()
    out = emit_html(app, sheet, dashboard_id="x")
    assert f"--color-accent: {DEFAULT_PRESET.accent}" in out
    assert f"--color-accent-fg: {DEFAULT_PRESET.accent_fg}" in out
    assert f"--color-danger: {DEFAULT_PRESET.danger}" in out
    assert f"--color-success: {DEFAULT_PRESET.success}" in out


def test_emit_dashboards_list_injects_default_preset() -> None:
    """Same fallback applies to the ``/dashboards`` listing page."""
    out = emit_dashboards_list([("smoke", "Smoke")])
    assert f"--color-accent: {DEFAULT_PRESET.accent}" in out
    assert f"--color-primary-fg: {DEFAULT_PRESET.primary_fg}" in out


# ---------------------------------------------------------------------------
# Per-instance theme override
# ---------------------------------------------------------------------------


def test_emit_html_overrides_with_provided_theme() -> None:
    """An L2-supplied ThemePreset wins — its ``accent`` color
    appears in the injected ``--color-accent`` declaration, not
    the DEFAULT_PRESET value."""
    custom = replace(
        DEFAULT_PRESET,
        accent="#ff00aa",
        accent_fg="#000000",
        danger="#bada55",
    )
    app, sheet = _build_app()
    out = emit_html(app, sheet, dashboard_id="x", theme=custom)
    assert "--color-accent: #ff00aa" in out
    assert "--color-accent-fg: #000000" in out
    assert "--color-danger: #bada55" in out
    # DEFAULT_PRESET.accent must NOT appear (would mean fallback
    # leaked through).
    assert f"--color-accent: {DEFAULT_PRESET.accent}" not in out


def test_emit_dashboards_list_overrides_with_provided_theme() -> None:
    custom = replace(DEFAULT_PRESET, accent="#abcdef")
    out = emit_dashboards_list([("smoke", "Smoke")], theme=custom)
    assert "--color-accent: #abcdef" in out


# ---------------------------------------------------------------------------
# Style block placement (after Tailwind so cascade wins)
# ---------------------------------------------------------------------------


def test_theme_style_appears_after_tailwind_link() -> None:
    """The ``<style>:root { ... }</style>`` block must come AFTER
    the ``<link rel="stylesheet" href="/static/output.css">`` so
    its declarations override Tailwind's ``@theme`` defaults via
    the cascade. Reversed order would let the build-time defaults
    silently win."""
    app, sheet = _build_app()
    out = emit_html(app, sheet, dashboard_id="x")
    link_pos = out.index('href="/static/output.css"')
    style_pos = out.index("--color-accent")
    assert style_pos > link_pos


# ---------------------------------------------------------------------------
# Semantic-token sweep — no hardcoded Tailwind colors leak through
# ---------------------------------------------------------------------------


def test_no_hardcoded_palette_colors_in_render_output() -> None:
    """Sweep guard: the renderer's output should use only semantic
    tokens (bg-accent / text-primary-fg / etc.), never literal
    Tailwind palette values like text-blue-600 / bg-slate-50.

    Catches a regression where a future renderer bypasses the
    theme contract by hardcoding a literal color."""
    app, sheet = _build_app()
    out = emit_html(app, sheet, dashboard_id="x")
    # Tailwind palette literals look like ``-blue-NNN``,
    # ``-slate-NNN``, etc. These aren't valid in our semantic
    # token set.
    pattern = re.compile(r"\b(?:bg|text|border|fill|stroke|ring)-"
                         r"(?:blue|slate|red|green|amber|emerald|rose|"
                         r"violet|cyan|orange|indigo)-\d+")
    leaks = pattern.findall(out)
    # Inline JS (bootstrap.js) is also part of the output but went
    # through its own sweep — assertion catches both render.py and
    # bootstrap.js leaks since the rendered HTML includes both.
    assert leaks == [], (
        f"Hardcoded Tailwind palette utilities leaked into render "
        f"output: {sorted(set(leaks))}. Replace with semantic tokens "
        f"(see render.py / bootstrap.js + input.css @theme)."
    )


def test_render_uses_semantic_tokens() -> None:
    """Positive proof — semantic tokens that should appear actually
    appear. Catches the failure mode where the sweep dropped them.

    X.2.g.1.e: Refresh button removed (auto-refresh on filter change),
    so ``bg-accent`` / ``text-accent-fg`` now only render on the
    active sheet tab. Build with two sheets so the tab strip emits
    and the assertion holds."""
    app, sheet = _build_app()
    # Add a second sheet so the tab strip renders with bg-accent
    # on the active tab.
    assert app.analysis is not None
    app.analysis.add_sheet(Sheet(
        sheet_id=SheetId("theme-sheet-2"),
        name="Two", title="Two", description="x",
    ))
    out = emit_html(
        app, sheet, dashboard_id="x",
        all_sheets=tuple(app.analysis.sheets),
    )
    # Page shell uses these.
    assert "bg-surface-bg" in out
    assert "text-primary-fg" in out
    # Active sheet tab uses these (X.2.e).
    assert "bg-accent" in out
    assert "text-accent-fg" in out
    assert "border-surface-border" in out


# ---------------------------------------------------------------------------
# Server end-to-end — theme threads through ServedDashboard
# ---------------------------------------------------------------------------


def test_server_passes_per_dashboard_theme_to_emit() -> None:
    """A ServedDashboard with a custom theme renders that theme on
    GET /dashboards/{id}; the listing page picks the same theme up
    via the ``listing_theme`` aggregation."""
    app, sheet = _build_app()
    custom = replace(DEFAULT_PRESET, accent="#deadbe")
    asgi = make_app(dashboards={
        "themed": ServedDashboard(
            tree_app=app, sheet=sheet,
            title="Themed", data_fetcher=lambda _v, _p: {},
            theme=custom,
        ),
    })
    client = TestClient(asgi)
    dashboard_html = client.get("/dashboards/themed").text
    listing_html = client.get("/dashboards").text
    assert "--color-accent: #deadbe" in dashboard_html
    assert "--color-accent: #deadbe" in listing_html


def test_server_falls_back_to_default_when_no_theme() -> None:
    """ServedDashboard without an explicit theme → page shell uses
    DEFAULT_PRESET. Mirrors the silent-fallback contract (N.4.k)."""
    app, sheet = _build_app()
    asgi = make_app(dashboards={
        "untheme": ServedDashboard(
            tree_app=app, sheet=sheet,
            title="Untheme", data_fetcher=lambda _v, _p: {},
        ),
    })
    client = TestClient(asgi)
    out = client.get("/dashboards/untheme").text
    assert f"--color-accent: {DEFAULT_PRESET.accent}" in out
