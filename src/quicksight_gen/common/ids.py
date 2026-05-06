"""Typed identifier wrappers for QuickSight resource IDs.

QuickSight definitions cross-reference identifiers across many fields:
``SheetId`` flows into ``SheetVisualScopingConfigurations.SheetId`` and
``GridLayoutConfiguration``, ``VisualId`` flows into the same scoping
configuration's ``VisualIds`` list, ``FilterGroupId`` is the dict key
QuickSight uses to look up a filter, and ``ParameterName`` is the bare
string that gets templated into a CategoryFilter.

All four are plain strings at the API boundary, so a typo or — more
insidiously — a *kind* swap (passing a SheetId into a VisualIds list,
say) does not raise; QuickSight either silently widens scope or
silently produces zero rows. The ``NewType`` wrappers here let mypy
catch wrong-kind-of-string at the call site, mirroring the
``ColumnShape`` discipline ``common/drill.py`` already imposes on
parameter wiring.

The wrappers are zero-cost at runtime — ``SheetId(x)`` returns ``x``
unchanged. They are an annotation discipline only.
"""

from __future__ import annotations

from typing import NewType

SheetId = NewType("SheetId", str)
VisualId = NewType("VisualId", str)
FilterGroupId = NewType("FilterGroupId", str)
ParameterName = NewType("ParameterName", str)
# X.2.o.3 — dashboard slug used in App2 URL paths
# (``/dashboards/{dashboard_id}/...``) and the
# ``ServedDashboard`` mapping key. Distinct from QS resource ids
# (analyses + dashboards in QS land) — the App2 server's own
# routing slug. NewType so a SheetId can't be passed where a
# DashboardId is expected at the route boundary.
DashboardId = NewType("DashboardId", str)
