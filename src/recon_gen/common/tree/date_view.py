"""The date-view tree primitive (D5).

A `DateView` is the single source of truth for a *subjective view-window*
— the analyst-facing "look at the latest day," "show me the last 30
days," "today's statement." It bundles the owned `AsOfFrame` (D1; the
anchor) with the view's own definition (empty-behavior + the
required-coverage contract). Promoted from the AP.1 spike's
`BalanceDateView` (`tests/unit/test_ap1_view_primitive.py`).

Why this exists — the C1 release-blocker shape: a subjective view today
is split across THREE independently-authored encodings — the analysis-
param default (`RollingDate`), the dataset-param default (`StaticValues`
sentinel), and the App2 binding. Each is authored in a different place,
and they can disagree (that disagreement IS C1: QS reads "yesterday off
wall-clock," App2 reads "latest day," the dataset KPI summary lands at a
day with no rows, the KPI tile reads `0`). The `DateView` inverts the
derivation: ONE typed object is the source of truth; every renderer
binding *derives* from it. AR.2 wires those emissions onto this
primitive; AR.1 lands the primitive itself.

What is intentionally NOT here (deferred to AR.2):

- `DateTimeParam.default` emission for QS analysis param defaults.
- Dataset `StaticValues` emission for QS dataset param defaults.
- `ParameterDateTimePicker` control emission for the picker widget.
- App2 `date_from` / `date_to` binding emission.

AR.1 is the *authoring abstraction* — what app authors construct. AR.2
adds the wired emissions that make the derivation inversion concrete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum, auto

from recon_gen.common.as_of_frame import AsOfFrame


class EmptyBehavior(Enum):
    """How a view resolves when its anchor day has no rows.

    Captures the implicit precondition every QS view carries today
    (audit §5 residual tension): a view "knows" what to do when its
    declared anchor has no data, but that knowledge lives in
    developer-memory. Making it explicit on the View object turns the
    precondition into a property of the type.
    """

    #: If `anchor_day` has no data, fall back to the latest day with
    #: data ≤ anchor. The default for KPI-style "latest statement"
    #: views — renders something useful instead of going blank.
    LATEST_ON_EMPTY = auto()

    #: Honor `anchor_day` literally even if it has no rows. The view
    #: shows empty. Right for "as-of date is a hard precondition"
    #: surfaces where blank IS the correct answer (e.g., a regulator
    #: snapshot at an unsettled date).
    SHOW_EMPTY = auto()


@dataclass(frozen=True)
class DateView:
    """A typed view over a date range / single date, owning its own
    definition (anchor, span, empty-behavior, required-coverage).

    The renderer bindings (analysis-param default, dataset-param
    default, picker widget, App2 binding) all *derive* from this one
    object — AR.2's emission layer. ONE source of truth; the C1
    dual-default split becomes unrepresentable.

    The frame carries the anchor + span: `frame.as_of` is the
    right-edge anchor, `frame.window_days` is the span (0 ⇒ single-day
    view; >0 ⇒ rolling-N-day view). Span as a frame field is the
    AQ-rolled-out shape; the audit's open D4 (where window lives —
    L2/config vs generator constant) is still unsettled, but the
    primitive doesn't have to take a position on it.

    Authoring abstraction — not end-user config. App authors construct
    one of these per surface; the operator never picks one.
    """

    frame: AsOfFrame
    empty_behavior: EmptyBehavior = EmptyBehavior.LATEST_ON_EMPTY

    @property
    def anchor_day(self) -> date:
        """The single date the view points at — `frame.as_of`. For
        single-date views this is the only day; for range views this
        is the right edge."""
        return self.frame.as_of

    @property
    def window_start(self) -> date:
        """The look-back's lower bound — `frame.window_start`. Equal
        to `anchor_day` when the view is single-date (span=0)."""
        return self.frame.window_start

    @property
    def required_coverage(self) -> tuple[date, date]:
        """The date range this view needs data inside to be
        meaningful. For range views: `[window_start, anchor_day]`. For
        single-date views with `LATEST_ON_EMPTY`: `[date.min, anchor]`
        (any prior day will do). For single-date `SHOW_EMPTY`:
        `[anchor, anchor]` (exact-match-or-blank is the contract).

        The seed-coverage assertion in AR.3 calls
        `is_satisfied_by(available_days)` to make the plant ⟷
        query-window contract a test, not developer-memory.
        """
        if self.frame.window_days > 0:
            return (self.window_start, self.anchor_day)
        if self.empty_behavior is EmptyBehavior.LATEST_ON_EMPTY:
            return (date.min, self.anchor_day)
        return (self.anchor_day, self.anchor_day)

    def is_satisfied_by(self, available_days: list[date]) -> bool:
        """Does at least one available day fall inside
        `required_coverage`? The view's stated limit, checkable
        BEFORE render."""
        lo, hi = self.required_coverage
        return any(lo <= d <= hi for d in available_days)

    def resolve_day(self, available_days: list[date]) -> date | None:
        """Apply `empty_behavior` to pick the day this view actually
        renders.

        Returns the `anchor_day` if it has data, else (for
        `LATEST_ON_EMPTY`) the latest day ≤ anchor that does, else
        `None` (the view can't satisfy itself — under `SHOW_EMPTY`
        means "render blank for the anchor"; under `LATEST_ON_EMPTY`
        means "no data anywhere ≤ anchor"). Range views resolve to
        their right-edge day the same way.
        """
        if self.empty_behavior is EmptyBehavior.SHOW_EMPTY:
            return self.anchor_day
        if self.anchor_day in available_days:
            return self.anchor_day
        earlier = [d for d in available_days if d <= self.anchor_day]
        return max(earlier) if earlier else None
