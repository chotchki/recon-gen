"""X.2.spike.1 — unit tests for the HTML renderer.

Spike.1 proves the tree → HTML projection works. These tests verify:

1. Sheet title + description appear in the output.
2. Each visual becomes one ``<section>`` with title + subtitle.
3. Visual class name is exposed as ``data-visual-kind`` (the d3
   hydration hook for spike.2).
4. HTML is escaped at leaves (defensive against L2-supplied prose
   that might include angle brackets or quotes).
5. Output is a complete, well-formed HTML document.
6. ``app.resolve_auto_ids()`` runs before render — visuals built with
   ``visual_id=AUTO`` (the default) land as ``data-visual-id="v-kpi-
   s0-0"`` in the HTML, NOT ``"_AutoSentinel.AUTO"``. spike.2 keys
   hx-post URLs off ``data-visual-id``, so unresolved IDs would
   silently break the swap dispatch.

No live data, no chart libraries, no HTMX — those land in spike.2.
"""

from __future__ import annotations

import pytest

from tests._test_helpers import make_test_config
from quicksight_gen.common.html import emit_html
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import KPI


_TEST_CFG = make_test_config()


def _build_app(sheet: Sheet) -> App:
    """Wrap a Sheet in the minimal App+Analysis needed by emit_html.

    emit_html calls ``app.resolve_auto_ids()`` and validates that the
    sheet is part of ``app.analysis.sheets`` — both invariants need a
    full App context, not a raw Sheet.
    """
    app = App(name="html-test", cfg=_TEST_CFG)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="html-test-analysis",
        name="HTML Test",
    ))
    analysis.add_sheet(sheet)
    return app


def _minimal_sheet() -> Sheet:
    """Tree ``Sheet`` with one KPI — the smallest non-trivial fixture."""
    sheet = Sheet(
        sheet_id=SheetId("test-sheet"),
        name="Test",
        title="Test Sheet Title",
        description="A short description.",
    )
    sheet.visuals.append(
        KPI(
            title="Open Exceptions",
            subtitle="Count of open invariant violations.",
            visual_id=VisualId("v-test-kpi"),
        ),
    )
    return sheet


def test_emit_html_includes_sheet_title() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "Test Sheet Title" in out


def test_emit_html_includes_sheet_description() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "A short description." in out


def test_emit_html_emits_one_section_per_visual() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert out.count("<section") == 1


def test_emit_html_includes_visual_title_and_subtitle() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "Open Exceptions" in out
    assert "Count of open invariant violations." in out


def test_emit_html_carries_visual_kind_attribute() -> None:
    """X.4 + spike.2 hook: visual class name lands as a data attribute
    so a single bootstrap JS can target d3 hydration per kind without
    reflection."""
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert 'data-visual-kind="KPI"' in out


def test_emit_html_carries_visual_id_attribute() -> None:
    """Visual id lands too — needed when spike.2's hx-get fragment
    swap targets a specific visual."""
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert 'data-visual-id="v-test-kpi"' in out


def test_emit_html_resolves_auto_visual_ids() -> None:
    """Regression for the spike.1 footgun: visuals built with the
    default ``visual_id=AUTO`` must have IDs resolved before they
    land in HTML. Pre-fix this emitted ``data-visual-id=
    "_AutoSentinel.AUTO"`` because resolution only ran inside
    emit_analysis / emit_dashboard."""
    from quicksight_gen.common.tree._helpers import auto_id

    sheet = Sheet(
        sheet_id=SheetId("auto-sheet"),
        name="Auto",
        title="Auto Title",
        description="x",
    )
    # No visual_id passed — defaults to AUTO sentinel.
    sheet.visuals.append(KPI(title="K1", subtitle=None))
    sheet.visuals.append(KPI(title="K2", subtitle=None))

    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "_AutoSentinel" not in out
    # resolve_auto_ids feeds the position slug ``v-{kind}-s{sheet}-
    # {visual}`` through ``auto_id()`` (UUIDv5, M.4.4.10c) so the
    # final attribute value is the deterministic UUID, not the slug
    # itself.
    assert f'data-visual-id="{auto_id("v-kpi-s0-0")}"' in out
    assert f'data-visual-id="{auto_id("v-kpi-s0-1")}"' in out


def test_emit_html_rejects_sheet_not_in_app() -> None:
    """Sheet must belong to the App we pass — without that we couldn't
    resolve IDs against the right analysis. Catch the wrong-app
    footgun loudly."""
    sheet = _minimal_sheet()
    other_app = App(name="other", cfg=_TEST_CFG)
    other_app.set_analysis(Analysis(
        analysis_id_suffix="other-analysis",
        name="Other",
    ))
    with pytest.raises(ValueError, match="not part of App"):
        emit_html(other_app, sheet, dashboard_id="test-dashboard")


def test_emit_html_escapes_titles() -> None:
    """L2 instances supply prose; renderer must defend against
    angle brackets / ampersands at the leaf level."""
    sheet = Sheet(
        sheet_id=SheetId("xss-sheet"),
        name="x",
        title="<script>alert(1)</script>",
        description="A & B",
    )
    sheet.visuals.append(
        KPI(title="<b>bold</b>", subtitle=None, visual_id=VisualId("v-x")),
    )
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "A &amp; B" in out
    assert "<b>bold</b>" not in out
    assert "&lt;b&gt;bold&lt;/b&gt;" in out


def test_emit_html_returns_complete_document() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert out.startswith("<!DOCTYPE html>")
    assert "<html" in out
    assert "</html>" in out.strip()
    assert "<head>" in out
    assert "<body" in out  # may carry class attributes


def test_emit_html_handles_empty_sheet() -> None:
    """Edge: a sheet with zero visuals still emits a valid document
    (just title + description, no sections)."""
    sheet = Sheet(
        sheet_id=SheetId("empty"),
        name="Empty",
        title="Empty Sheet",
        description="No visuals yet.",
    )
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "Empty Sheet" in out
    assert "<section" not in out
    assert out.startswith("<!DOCTYPE html>")


def test_emit_html_handles_visual_without_subtitle() -> None:
    """Some visual kinds have ``subtitle=None``; the subtitle ``<p>``
    must be omitted when subtitle is unset (no empty paragraphs)."""
    sheet = Sheet(
        sheet_id=SheetId("no-subtitle"),
        name="x",
        title="No Subtitle",
        description="x",
    )
    sheet.visuals.append(
        KPI(title="Bare KPI", subtitle=None, visual_id=VisualId("v-bare")),
    )
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "Bare KPI" in out
    assert 'class="subtitle"' not in out
