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
from recon_gen.common.models import (
    DateTimeDatasetParameterDefaultValues,
    DateTimeDefaultValues,
)


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

    The frame carries the anchor + window: `frame.as_of` is the
    right-edge anchor, `frame.window` is the typed `DateInterval` the
    view queries against (single-day when `frame.window.days == 1`;
    rolling N-day when `frame.window.days > 1`). BD.1 replaced the v1
    `frame.window_days: int` field with `frame.window: DateInterval`
    so the "span > 0 means rolling" convention is now a closed-closed
    days count, not a separate scalar.

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
        if self.frame.window.days > 1:
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

    # ---- Renderer emissions (AR.2) ----------------------------------------
    #
    # ONE source — `anchor_day` — drives every renderer binding. The three
    # emissions below return the SAME concrete day; QS's analysis default
    # ⋈ MappedDataSetParameters bridge drops the same value into the
    # dataset param, and App2 reads the same day from the dataset default.
    # The C1 dual-default split becomes unrepresentable: there's nothing
    # to keep in sync because there's only one source.

    def _anchor_iso(self) -> str:
        """QS-shape datetime literal — `YYYY-MM-DDT00:00:00` (no offset).
        Matches the existing dataset StaticValues serialization."""
        return f"{self.anchor_day.isoformat()}T00:00:00"

    def emit_qs_analysis_default(self) -> DateTimeDefaultValues:
        """Alias for `emit_qs_analysis_default_end()` — kept for the
        single-date case where the "default" is unambiguously the
        anchor (the AR.2 balance-date wiring's caller name)."""
        return self.emit_qs_analysis_default_end()

    def emit_qs_analysis_default_end(self) -> DateTimeDefaultValues:
        """The QS analysis-param default for the END of a range view (or
        the single-date case) — a `StaticValues` literal day, NOT a
        `RollingDate` expression. Strict-collapse: the anchor is the
        owned `as_of`, baked at deploy."""
        return DateTimeDefaultValues(StaticValues=[self._anchor_iso()])

    def emit_qs_analysis_default_start(self) -> DateTimeDefaultValues:
        """The QS analysis-param default for the START of a range view —
        `window_start` as `StaticValues`. AR.4 wires this onto the L1
        universal-range start param + the Exec 30-day start param,
        replacing the per-app `RollingDate(addDateTime(-N, ...))`
        expressions."""
        start_iso = f"{self.window_start.isoformat()}T00:00:00"
        return DateTimeDefaultValues(StaticValues=[start_iso])

    def emit_qs_dataset_default(self) -> DateTimeDatasetParameterDefaultValues:
        """The QS dataset-param default — the SAME literal day as the
        analysis default. App2 reads this directly; QS receives it via
        `MappedDataSetParameters` from the analysis side. Both renderers
        land on the same day."""
        return DateTimeDatasetParameterDefaultValues(
            StaticValues=[self._anchor_iso()],
        )

    def emit_app2_date_to(self) -> str:
        """The App2 `:date_to` bind value (or single-date filter value)
        — the anchor day as `YYYY-MM-DD`. For range views this is the
        right edge; for single-date views this is the day."""
        return self.anchor_day.isoformat()

    def emit_app2_date_from(self) -> str:
        """The App2 `:date_from` bind value — the look-back lower bound
        (`window_start`). Equals `emit_app2_date_to()` for single-date
        views (span=0)."""
        return self.window_start.isoformat()
