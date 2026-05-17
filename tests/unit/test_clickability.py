"""Unit tests for ``common/clickability.py`` — conditional-format
helpers that cue clickable cells in QS tables.

The two visual languages (``link_text_format`` plain accent text vs
``menu_link_text_format`` accent text + tint background) are how
analysts tell left-click drills apart from right-click menu drills
at a glance — see ``CLAUDE.md`` "Drill direction convention". A
regression that swaps the two styles or breaks the always-true
expression guard would silently miscue the clickability of every
drill-source table cell across all four apps.
"""

from __future__ import annotations

from recon_gen.common.clickability import (
    link_text_format,
    menu_link_text_format,
)


_FIELD_ID = "v-table-tx-account-id"
_COLUMN = "account_id"
_ACCENT = "#2E5090"
_TINT = "#E8F1EB"


class TestLinkTextFormat:
    def test_emits_cell_textcolor_only(self) -> None:
        out = link_text_format(_FIELD_ID, _COLUMN, _ACCENT)
        cell = out["Cell"]
        assert cell["FieldId"] == _FIELD_ID
        assert "TextColor" in cell["TextFormat"]
        # Plain-text style: NO background color override (that would
        # collapse it into the menu_link_text_format visual language).
        assert "BackgroundColor" not in cell["TextFormat"]

    def test_text_color_value_round_trips(self) -> None:
        out = link_text_format(_FIELD_ID, _COLUMN, _ACCENT)
        solid = out["Cell"]["TextFormat"]["TextColor"]["Solid"]
        assert solid["Color"] == _ACCENT

    def test_always_true_expression_uses_sentinel_compare(self) -> None:
        """The always-true guard must use the documented
        ``{col} <> "<sentinel>"`` form. Self-equality, ``1 = 1``, and
        bare booleans are all rejected by the QS parser; the project
        memory captures this as a confirmed quirk."""
        out = link_text_format(_FIELD_ID, _COLUMN, _ACCENT)
        expr = out["Cell"]["TextFormat"]["TextColor"]["Solid"]["Expression"]
        assert expr.startswith(f'{{{_COLUMN}}} <> "')
        assert expr.endswith('"')
        # Sentinel string itself is opaque from the caller's POV but
        # must not be a value any real row could carry.
        assert "recon_never_matches" in expr


class TestMenuLinkTextFormat:
    def test_emits_both_textcolor_and_backgroundcolor(self) -> None:
        out = menu_link_text_format(_FIELD_ID, _COLUMN, _ACCENT, _TINT)
        cell = out["Cell"]
        assert cell["FieldId"] == _FIELD_ID
        # Menu style: BOTH text + background distinguish from the plain
        # link_text_format. Asserts the visual language stays separable.
        assert "TextColor" in cell["TextFormat"]
        assert "BackgroundColor" in cell["TextFormat"]

    def test_text_and_background_colors_round_trip(self) -> None:
        out = menu_link_text_format(_FIELD_ID, _COLUMN, _ACCENT, _TINT)
        text_solid = out["Cell"]["TextFormat"]["TextColor"]["Solid"]
        bg_solid = out["Cell"]["TextFormat"]["BackgroundColor"]["Solid"]
        assert text_solid["Color"] == _ACCENT
        assert bg_solid["Color"] == _TINT

    def test_both_colors_share_one_always_true_expression(self) -> None:
        """Text + background expressions must be byte-identical so the
        QS optimizer doesn't render half-applied formatting (only one
        of the two would fire if they differed)."""
        out = menu_link_text_format(_FIELD_ID, _COLUMN, _ACCENT, _TINT)
        text_expr = out["Cell"]["TextFormat"]["TextColor"]["Solid"]["Expression"]
        bg_expr = out["Cell"]["TextFormat"]["BackgroundColor"]["Solid"]["Expression"]
        assert text_expr == bg_expr
