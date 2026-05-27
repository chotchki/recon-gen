"""Conditional-format helpers that cue which table cells are clickable.

Two visual languages, so users can tell click idioms apart at a glance:

* ``link_text_format`` — plain accent text color. Mark cells whose click
  target is the visual's own left-click (``DATA_POINT_CLICK``) drill.
* ``menu_link_text_format`` — accent text + pale tint background. Mark
  cells whose click target is a right-click (``DATA_POINT_MENU``) action,
  used when the visual already has a left-click action reserved for
  something else (same-sheet chart filter, another drill, etc.).

Background: QuickSight's conditional-formatting expression grammar is
undocumented. The idiomatic always-true guard (confirmed by UI round-trip)
is ``{col} <> "<sentinel>"`` — compare the column to a value no row ever
holds. Literal booleans, ``1 = 1``, and self-equality are all rejected.
"""

from __future__ import annotations

from typing import Any


_SENTINEL = "__recon_never_matches__"


def _always_true(column_name: str) -> str:
    return f'{{{column_name}}} <> "{_SENTINEL}"'


def link_text_format(
    field_id: str, column_name: str, color: str,
) -> dict[str, Any]:
    """Render a drill-source cell in ``color`` to cue left-click drill-down."""
    return {
        "Cell": {
            "FieldId": field_id,
            "TextFormat": {
                "TextColor": {
                    "Solid": {
                        "Expression": _always_true(column_name),
                        "Color": color,
                    },
                },
            },
        },
    }


def menu_link_text_format(
    field_id: str,
    column_name: str,
    text_color: str,
    bg_color: str,
) -> dict[str, Any]:
    """Render a drill-source cell in ``text_color`` on a ``bg_color`` tint
    to cue a right-click (DATA_POINT_MENU) drill — distinguishes from the
    plain-accent left-click style produced by ``link_text_format``."""
    expr = _always_true(column_name)
    return {
        "Cell": {
            "FieldId": field_id,
            "TextFormat": {
                "TextColor": {
                    "Solid": {"Expression": expr, "Color": text_color},
                },
                "BackgroundColor": {
                    "Solid": {"Expression": expr, "Color": bg_color},
                },
            },
        },
    }
