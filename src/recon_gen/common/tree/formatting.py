"""Typed conditional-format marker for drillable Table columns.

Phase DA — collapsed the prior two-type design (`CellAccentText` +
`CellAccentMenu`) into a single `Drillable(on=Dim, color=str)` marker.
The visual cue (plain accent text vs. accent text + tint background)
auto-derives from the drill triggers writing from `on.column` at QS
emit time + at App2 plan-build time — authors don't pick the cue, the
type system + renderer pick the visual from the drill set.

Why collapse: the prior design had `CellAccentText` mapped to "left-click
drill" and `CellAccentMenu` mapped to "menu drill", but authors had no
type-level safety against mismatch — every app site in `l1_dashboard`
paired `CellAccentText` with `DATA_POINT_MENU` triggers, so even the QS
side showed the wrong visual cue (per the CN.0 audit, now DA.0). Audit
also found zero CellAccentMenu uses anywhere — the type was dead. So
the right collapse is one marker meaning "this column carries at least
one drill" + a decision rule keyed on the drill triggers.

Visual decision rule (matches the QS convention):
- Column has at least one `DATA_POINT_MENU` drill writing from it →
  accent text + tint background ("accent-menu").
- Column has only `DATA_POINT_CLICK` drill(s) writing from it →
  accent text only ("accent").
- Column has NO drill writing from it → `ValueError` at
  `Table.__post_init__` (the DA.5 type gate; raises at the wiring site).

Tint hue is auto-derived from the accent color via `_tint_hex` — QS's
conditional formatting needs a resolved hex, so the App2-side
`color-mix(in srgb, accent 10%, transparent)` translates to a 10%
lighten toward white here (visually similar; not byte-identical, but
the parity contract is "tinted accent", not "same pixel value").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from recon_gen.common.tree._helpers import _AutoSentinel
from recon_gen.common.tree.calc_fields import resolve_column
from recon_gen.common.tree.fields import Dim

if TYPE_CHECKING:
    from recon_gen.common.tree.actions import Drill


# QuickSight's conditional-formatting expression grammar is undocumented.
# The idiomatic always-true guard (confirmed by UI round-trip) is
# ``{col} <> "<sentinel>"`` — compare the column to a value no row ever
# holds. Literal booleans, ``1 = 1``, and self-equality are all rejected.
_SENTINEL = "__recon_never_matches__"


def _always_true(column_name: str) -> str:
    return f'{{{column_name}}} <> "{_SENTINEL}"'


def _resolved_field_id(dim: Dim) -> str:
    assert not isinstance(dim.field_id, _AutoSentinel), (
        "Drillable target Dim's field_id wasn't resolved — "
        "App.resolve_auto_ids() must run before Drillable.emit()."
    )
    return dim.field_id


def _tint_hex(hex_color: str, *, amount: float = 0.10) -> str:
    """Mix `hex_color` toward white by `amount` (0..1). Analogue of the
    App2-side `color-mix(in srgb, <hex> N%, transparent)` — QS conditional
    formatting needs a resolved solid hex, so we lighten the accent
    instead of compositing against the page background. The visual
    result is "tinted accent"; parity with App2 is by intent, not pixel.

    `hex_color` must be a 6-digit hex with leading `#`. Returns the same
    shape.
    """
    if not (hex_color.startswith("#") and len(hex_color) == 7):
        raise ValueError(
            f"_tint_hex expected '#rrggbb', got {hex_color!r}"
        )
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    # Mix toward white by (1 - amount). amount=0.10 → 10% accent, 90% white.
    r2 = round(r * amount + 255 * (1 - amount))
    g2 = round(g * amount + 255 * (1 - amount))
    b2 = round(b * amount + 255 * (1 - amount))
    return f"#{r2:02x}{g2:02x}{b2:02x}"


@dataclass(frozen=True)
class Drillable:
    """Mark a column as drillable in a Table.

    The visual cue auto-derives from the drill triggers writing from
    `on.column`: any `DATA_POINT_MENU` drill writes from the column →
    accent text + tint background; only `DATA_POINT_CLICK` drill(s) →
    accent text only. No drills writing from the column is a wiring bug
    (the DA.5 type gate at `Table.__post_init__` raises on construction).

    `color` is the theme accent token at construction time (apps pass
    `theme.accent`). The tint background is auto-derived via `_tint_hex`
    — no separate token, no caller duty to pre-compute the tint.
    """

    on: Dim
    color: str

    def visual_kind(
        self, drills: Sequence["Drill"],
    ) -> Literal["accent", "accent-menu"]:
        """Resolve which visual cue applies given the Table's drill set.

        Walks `drills`, keeps the ones whose `writes` reference a `Dim`
        with the same column as `self.on`. If any kept drill has
        `DATA_POINT_MENU` trigger → "accent-menu"; otherwise → "accent".
        When no drill matches, returns "accent-menu" as a defensive
        default (the gate will raise; this just keeps emit() side-effect-
        free during gate-less code paths e.g. tests that don't exercise
        Table.__post_init__).
        """
        target_col = resolve_column(self.on.column)
        matching = [
            d for d in drills
            if any(
                isinstance(src, Dim)
                and resolve_column(src.column) == target_col
                for _param, src in d.writes
            )
        ]
        if not matching:
            return "accent-menu"
        if any(d.trigger == "DATA_POINT_MENU" for d in matching):
            return "accent-menu"
        return "accent"

    def emit(self, drills: Sequence["Drill"]) -> dict[str, Any]:
        """QS-side ConditionalFormatting cell entry. Picks the visual
        (accent-text-only vs. accent + tint) by walking the Table's
        drill set — callers pass `[a for a in table.actions if
        isinstance(a, Drill)]`."""
        kind = self.visual_kind(drills)
        column_name = resolve_column(self.on.column)
        expr = _always_true(column_name)
        field_id = _resolved_field_id(self.on)
        text_format: dict[str, Any] = {
            "TextColor": {
                "Solid": {"Expression": expr, "Color": self.color},
            },
        }
        if kind == "accent-menu":
            text_format["BackgroundColor"] = {
                "Solid": {
                    "Expression": expr, "Color": _tint_hex(self.color),
                },
            }
        return {
            "Cell": {
                "FieldId": field_id,
                "TextFormat": text_format,
            },
        }
