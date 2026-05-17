"""Typed conditional-format wrappers for Table cells.

QuickSight's ``ConditionalFormatting`` is a free-form dict in the
model layer; the tree wraps the two patterns the apps actually use:

- ``CellAccentText`` — accent-colored text. Cues a left-click
  (``DATA_POINT_CLICK``) drill on that field.
- ``CellAccentMenu`` — accent-colored text on a tint background.
  Cues a right-click (``DATA_POINT_MENU``) drill on that field. Use
  this when the cell already carries a left-click action and a second
  right-click action also lives on the visual — the visual cue tells
  the analyst there's more than one drill.

Both wrap a typed ``Dim`` ref so the cell's ``field_id`` + column
name resolve together at emit time. Catches the bug class where the
two strings drift out of sync (the imperative
``link_text_format(field_id, column_name, color)`` shape took both
as separate strings, with no enforcement they describe the same
column).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

from recon_gen.common.tree._helpers import _AutoSentinel
from recon_gen.common.tree.calc_fields import resolve_column
from recon_gen.common.tree.fields import Dim


# QuickSight's conditional-formatting expression grammar is undocumented.
# The idiomatic always-true guard (confirmed by UI round-trip) is
# ``{col} <> "<sentinel>"`` — compare the column to a value no row ever
# holds. Literal booleans, ``1 = 1``, and self-equality are all rejected.
_SENTINEL = "__recon_never_matches__"


def _always_true(column_name: str) -> str:
    return f'{{{column_name}}} <> "{_SENTINEL}"'


def _resolved_field_id(dim: Dim) -> str:
    assert not isinstance(dim.field_id, _AutoSentinel), (
        "CellFormat target Dim's field_id wasn't resolved — "
        "App.resolve_auto_ids() must run before CellFormat.emit()."
    )
    return dim.field_id


@dataclass(frozen=True)
class CellAccentText:
    """Render a cell's text in ``color`` to cue a left-click drill on
    that field. The ``on: Dim`` ref carries both the field_id and the
    column name through to the emitted format options."""
    on: Dim
    color: str

    def emit(self) -> dict[str, Any]:
        column_name = resolve_column(self.on.column)
        return {
            "Cell": {
                "FieldId": _resolved_field_id(self.on),
                "TextFormat": {
                    "TextColor": {
                        "Solid": {
                            "Expression": _always_true(column_name),
                            "Color": self.color,
                        },
                    },
                },
            },
        }


@dataclass(frozen=True)
class CellAccentMenu:
    """Render a cell's text in ``text_color`` on a ``background_color``
    tint to cue a right-click (``DATA_POINT_MENU``) drill on that
    field. Distinguishes from ``CellAccentText``'s plain-accent
    left-click style, so the analyst can tell the two click idioms
    apart at a glance."""
    on: Dim
    text_color: str
    background_color: str

    def emit(self) -> dict[str, Any]:
        column_name = resolve_column(self.on.column)
        expr = _always_true(column_name)
        return {
            "Cell": {
                "FieldId": _resolved_field_id(self.on),
                "TextFormat": {
                    "TextColor": {
                        "Solid": {"Expression": expr, "Color": self.text_color},
                    },
                    "BackgroundColor": {
                        "Solid": {
                            "Expression": expr, "Color": self.background_color,
                        },
                    },
                },
            },
        }


CellFormat = Union[CellAccentText, CellAccentMenu]
