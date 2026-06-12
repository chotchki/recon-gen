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
# CN.5 — typed reference to a handbook page under ``src/recon_gen/docs/_handbook_per_sheet/``.
# Conventional shape is ``<app>/<sheet>`` (e.g. ``l1/drift``) without
# the ``.md`` extension; the Starlette ``GET /handbook/<path>`` route
# appends ``.md`` when resolving. NewType so a Sheet's optional
# ``handbook_path`` field can't be confused with arbitrary strings at
# wiring sites.
HandbookPath = NewType("HandbookPath", str)
# BX.12 — typed reference to a GLOSSARY key in
# ``common/html/_side_panel.py``. Used by ``FieldSpec.glossary_anchor``
# to emit a per-field ``[?]`` side-panel trigger that opens to the
# matching glossary entry. NewType so a FieldSpec's optional
# ``glossary_anchor`` can't be confused with arbitrary strings at
# wiring sites; an anti-drift test pins every used anchor against the
# GLOSSARY dict so a typo here breaks at sessionstart, not at the
# first click.
GlossaryAnchor = NewType("GlossaryAnchor", str)
# BX.13 — typed reference to a SURFACES_AS key in
# ``common/html/_side_panel.py``. Where ``GlossaryAnchor`` answers
# "what does this term mean?", ``SurfaceAnchor`` answers "where does
# the value I type here end up?" — the operator-facing surfaces a
# field's value flows into (audit PDF cover, L1 dashboard header,
# QS theme.accent on the primary KPI bar, etc.). Sibling to
# ``GlossaryAnchor`` with the same anti-drift contract: every wired
# anchor MUST resolve to a ``SURFACES_AS`` entry, pinned at
# sessionstart by ``tests/unit/test_side_panel.py``.
SurfaceAnchor = NewType("SurfaceAnchor", str)
