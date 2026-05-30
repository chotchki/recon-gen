# API Reference

*Audience is the developer authoring a custom QuickSight app on the
tree primitives — the [integrator role page](../for-your-role/integrator.md)
is the upstream curated path.*

Auto-generated reference for the Python API. The pages here cover the
primitives an external author would import to build a QuickSight app
against this codebase — the `common/` package surface plus the
`common/tree/` package which is the public construction API as of
Phase L.

The intended user is someone writing a new app on the tree (read the
[customization handbook walkthrough](../walkthroughs/customization/how-do-i-author-a-new-app-on-the-tree.md)
first for the worked-example narrative).

## Three-layer model

The codebase is structured around three layers; the API surface
documented here lives entirely in L1:

- **L1 — `common/tree/` + `common/models.py` + `common/ids.py` +
  `common/dataset_contract.py`.** Persona-blind primitives. Knows
  about *dashboards* (sheets / visuals / filters / drills), nothing
  about banks / accounts / specific institutions.
- **L2 — `apps/<app>/app.py` + `apps/<app>/constants.py`.** Per-app
  tree assembly, in domain vocabulary.
- **L3 — SQL strings + `apps/<app>/demo_data.py` + per-L2
  `institution_name` / `institution_acronym` / `description` /
  `investigation_personas` (top-level fields on `L2Instance`) +
  theme presets.** Persona / customer flavor.

When extending the API itself (rare), the L1 invariant is: zero hits
when you grep `common/tree/` for any persona / institution name.

## Pages

- [Tree — App / Analysis / Dashboard / Sheet](tree-structure.md)
- [Tree — Visuals (KPI / Table / BarChart / Sankey)](tree-visuals.md)
- [Tree — Data (Dataset / Column / Dim / Measure / CalcField)](tree-data.md)
- [Tree — Filters + Controls + Parameters](tree-filters-controls.md)
- [Tree — Drill Actions](tree-actions.md)
- [Common foundations (models / ids / dataset_contract / drill / persona)](common-foundations.md)
