"""Internal helpers shared across tree submodules.

Title / subtitle label builders + the action lists for QuickSight
Analysis / Dashboard ResourcePermissions. Lifted from the per-app
analysis modules so visual / control / structural nodes can reach
them without re-importing across submodules.

Plus shared ``Literal`` type aliases that more than one submodule
references (e.g. ``TimeGranularity``, used by both filters and
parameters). Pyright strict on ``common/tree/`` (L.1.20) catches
out-of-set values at the wiring site; no runtime guard needed.

Plus the ``AUTO`` sentinel — distinguishes "truly optional, may stay
unset at deploy" (``T | None``) from "must be filled in by
``App.resolve_auto_ids()`` before emit" (``T | AutoResolved``). What
used to be a single ``T | None`` slot for both cases now type-encodes
the difference: pyright narrows ``T | AutoResolved`` to ``T`` after
``assert not isinstance(x, _AutoSentinel)``, and a typo'd
``visual_id=None`` (where AUTO was meant) gets a red squiggle at the
wiring site.
"""

from __future__ import annotations

import enum
import uuid
from typing import Final, Literal

from quicksight_gen.common.models import (
    VisualSubtitleLabelOptions,
    VisualTitleLabelOptions,
)


# Project-pinned namespace for deterministic UUID v5s on tree-position
# slugs. QS UI defaults VisualId / FilterGroupId / etc. to UUID-shape
# strings; positional slugs (`v-kpi-s11-0`) appear to break the editor
# even though Create succeeds. UUID v5 keeps determinism (same slug →
# same UUID) so unit tests can compute expected values via the same
# helper.
_AUTO_ID_NAMESPACE: Final = uuid.uuid5(
    uuid.NAMESPACE_DNS, "quicksight-gen.tree.auto-id",
)


def auto_id(slug: str) -> str:
    """Deterministic UUID v5 from a tree-position slug.

    Same input → same UUID across runs (test stability) AND across
    machines. Output matches QS's UUID format so the editor accepts
    it. (M.4.4.10c)
    """
    return str(uuid.uuid5(_AUTO_ID_NAMESPACE, slug))


# ---------------------------------------------------------------------------
# AUTO sentinel — "this field will be filled in by App.resolve_auto_ids()"
# ---------------------------------------------------------------------------

class _AutoSentinel(enum.Enum):
    """Singleton sentinel — see ``AUTO`` below.

    Internal enum so pyright can narrow ``T | AutoResolved`` cleanly
    via ``isinstance`` / ``is AUTO`` checks. Single member; the enum
    machinery only matters for type narrowing.
    """
    AUTO = "auto"

    def __repr__(self) -> str:
        return "AUTO"


# Public sentinel value. ``KPI.visual_id: VisualId | AutoResolved = AUTO``
# means "App.resolve_auto_ids fills me in"; emit() asserts the resolver
# ran (``assert not isinstance(self.visual_id, _AutoSentinel)``) which
# narrows the type to ``VisualId``.
AUTO: Final = _AutoSentinel.AUTO

# Type alias — the resolved-later half of the union. Reads cleaner at
# field declarations than the bare ``Literal[_AutoSentinel.AUTO]``.
AutoResolved = Literal[_AutoSentinel.AUTO]


# ---------------------------------------------------------------------------
# Shared Literal aliases
# ---------------------------------------------------------------------------

# QuickSight TimeGranularity — accepted on TimeRangeFilter, DateTimeParam,
# and a handful of date-binned visual config fields. Listed in the API
# docs for ColumnHierarchy / ParameterControlDateTimePicker / etc.; this
# codebase only uses "DAY" today, but the typed alias future-proofs the
# wrapper without locking us in.
TimeGranularity = Literal[
    "YEAR", "QUARTER", "MONTH", "WEEK",
    "DAY", "HOUR", "MINUTE", "SECOND", "MILLISECOND",
]


# Mirrors GridLayoutElement.ElementType in models.py — kept here so the
# LayoutNode protocol in structure.py and the typed Visual / TextBox
# element_type properties in visuals.py / text_boxes.py can both refer
# to it without a circular import. Two values used in practice today
# ("VISUAL" + "TEXT_BOX"); the rest reflect QS's API surface so the
# tree primitives can grow into them.
GridLayoutElementType = Literal[
    "VISUAL", "FILTER_CONTROL", "PARAMETER_CONTROL", "TEXT_BOX", "IMAGE",
]


def title_label(text: str) -> VisualTitleLabelOptions:
    return VisualTitleLabelOptions(
        Visibility="VISIBLE", FormatText={"PlainText": text},
    )


def subtitle_label(text: str) -> VisualSubtitleLabelOptions:
    return VisualSubtitleLabelOptions(
        Visibility="VISIBLE", FormatText={"PlainText": text},
    )


# ResourcePermission action lists — match the per-app
# `_ANALYSIS_ACTIONS` / `_DASHBOARD_ACTIONS` lists used by the
# existing imperative builders.
ANALYSIS_ACTIONS = [
    "quicksight:DescribeAnalysis",
    "quicksight:DescribeAnalysisPermissions",
    "quicksight:UpdateAnalysis",
    "quicksight:UpdateAnalysisPermissions",
    "quicksight:DeleteAnalysis",
    "quicksight:QueryAnalysis",
    "quicksight:RestoreAnalysis",
]

DASHBOARD_ACTIONS = [
    "quicksight:DescribeDashboard",
    "quicksight:ListDashboardVersions",
    "quicksight:UpdateDashboardPermissions",
    "quicksight:QueryDashboard",
    "quicksight:UpdateDashboard",
    "quicksight:DeleteDashboard",
    "quicksight:DescribeDashboardPermissions",
    "quicksight:UpdateDashboardPublishedVersion",
    "quicksight:UpdateDashboardLinks",
]
