"""X.2.spike.1 — unit tests for the HTML renderer.

Spike.1 proves the tree → HTML projection works. These tests verify:

1. Sheet title + description appear in the output.
2. Each visual becomes one ``<section>`` with title + subtitle.
3. Visual class name is exposed as ``data-visual-kind`` (the d3
   hydration hook for spike.2).
4. HTML is escaped at leaves (defensive against L2-supplied prose
   that might include angle brackets or quotes).
5. Output is a complete, well-formed HTML document.

No live data, no chart libraries, no HTMX — those land in spike.2.
"""

from __future__ import annotations

import pytest

from quicksight_gen.common.html import emit_html
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Sheet
from quicksight_gen.common.tree.visuals import KPI


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
    out = emit_html(_minimal_sheet())
    assert "Test Sheet Title" in out


def test_emit_html_includes_sheet_description() -> None:
    out = emit_html(_minimal_sheet())
    assert "A short description." in out


def test_emit_html_emits_one_section_per_visual() -> None:
    out = emit_html(_minimal_sheet())
    assert out.count("<section") == 1


def test_emit_html_includes_visual_title_and_subtitle() -> None:
    out = emit_html(_minimal_sheet())
    assert "Open Exceptions" in out
    assert "Count of open invariant violations." in out


def test_emit_html_carries_visual_kind_attribute() -> None:
    """X.4 + spike.2 hook: visual class name lands as a data attribute
    so a single bootstrap JS can target d3 hydration per kind without
    reflection."""
    out = emit_html(_minimal_sheet())
    assert 'data-visual-kind="KPI"' in out


def test_emit_html_carries_visual_id_attribute() -> None:
    """Visual id lands too — needed when spike.2's hx-get fragment
    swap targets a specific visual."""
    out = emit_html(_minimal_sheet())
    assert 'data-visual-id="v-test-kpi"' in out


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
    out = emit_html(sheet)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "A &amp; B" in out
    assert "<b>bold</b>" not in out
    assert "&lt;b&gt;bold&lt;/b&gt;" in out


def test_emit_html_returns_complete_document() -> None:
    out = emit_html(_minimal_sheet())
    assert out.startswith("<!DOCTYPE html>")
    assert "<html" in out
    assert "</html>" in out.strip()
    assert "<head>" in out
    assert "<body>" in out


def test_emit_html_handles_empty_sheet() -> None:
    """Edge: a sheet with zero visuals still emits a valid document
    (just title + description, no sections)."""
    sheet = Sheet(
        sheet_id=SheetId("empty"),
        name="Empty",
        title="Empty Sheet",
        description="No visuals yet.",
    )
    out = emit_html(sheet)
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
    out = emit_html(sheet)
    assert "Bare KPI" in out
    assert 'class="subtitle"' not in out
