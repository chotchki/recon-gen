# CH.0 — Table component replan (2026-06-05)

## Context

v13.1.1 design review Dashboards Med #2 + the audit's #5
highest-leverage systemic fix:

> Dense ledger tables are undifferentiated dumps — no zebra, no
> header-row background, money columns not right-aligned/tabular-
> nums. Standardize one table component.

The CH plan listed 4 sub-cells (CH.0 replan, CH.1 tabular-nums
rule, CH.2 zebra+sticky+header-bg, CH.3 migrate flagged tables).
CH.0 was also charged with reconciling against CF.7's "tabular-
nums everywhere" lock.

## Audit of the existing App2 table render

`common/html/assets/js/bootstrap.js::renderTable` (the App2
self-hosted dashboard renderer). Per static read at commit
`4f816789`:

| Audit ask | App2 status | Where |
|---|---|---|
| Zebra striping | **DONE** | `bg-white` / `bg-surface-bg` alternation on `<tr>` |
| Money columns right-aligned + tabular-nums | **DONE** | `tabular-nums text-right` added per-cell when `cell.col.format` is numeric |
| Sticky header | **MISSING (this cell)** | `<thead>` had no positioning |
| Header-row bg via theme token | **MISSING (this cell)** | `<th>` had only `border-b border-surface-border` |

## Audit of the QS table render

`common/models.py::TableConfiguration` +
`common/tree/visuals.py` build the QS-side table JSON. The
contract carries:

- `FormatConfiguration` (currency + percentage + numeric) — drives
  display format, not alignment.
- `ChartConfiguration=TableConfiguration(...)` — global table
  options; no per-cell-style on currency columns.

QS doesn't ship a "right-align this column + tabular-nums" knob
the same way Tailwind classes do. Achieving parity would need:

1. A typed `TableCellStyle(text_alignment="RIGHT",
   font_family="<monospace>")` adapter mapping `currency=True`
   columns → cell style overrides at emit time. Probably extends
   `TableInlineVisualOptions` or
   `TableConditionalFormatting`.
2. A header-row style override (TableRowStyle?) to set the
   sticky/highlight contract on the QS side.

This is a non-trivial typed-primitive expansion. **Recommendation:**
ship the App2 fixes now; file the QS-side as a follow-up cell.

## CH ↔ CF.7 reconciliation

CF.7's "tabular-nums everywhere" lock was already partially
delivered by App2's existing `renderTable` (tabular-nums applies
per-cell when the column format is numeric/currency, but only
inside `renderTable` — not on hand-written tables elsewhere in
Studio). CH.1 verifies the contract holds in App2; the cross-
cutting "every numeric / money table cell, regardless of
renderer" sweep belongs in CF.7's remaining work.

**Lock:** CH primitive owns zebra + sticky header + header-bg.
Tabular-nums + right-align stay where they live today (the
renderTable per-cell class), and CF.7 owns the broader sweep over
hand-written tables.

## Plan for this autonomous branch (`cg-autonomous-CH`)

- **CH.0** (this doc) — replan + audit baseline.
- **CH.1** — pin the existing App2 tabular-nums + right-align
  contract via a unit test (catches a future refactor that drops
  the per-cell class).
- **CH.2** — add sticky-header + `bg-surface-alt` header-row fill
  to App2's `renderTable`. Pin via unit test.
- **CH.3** — verify the audit-flagged dense tables (Daily
  Statement detail, Limit Breach, Drift detail, ledger tables
  across L1 / L2FT / Investigation / Exec) all flow through
  `renderTable` and inherit. Skip migration cell if all already
  inherit; flag any that don't for follow-up.

**Deferred (file as follow-ups for operator review):**

- **CH.x — QS-side TableCellStyle adapter** — typed primitive
  expansion to mirror the App2 contract in the QS emit. ~1 day
  spike; touches `common/models.py` + `common/tree/visuals.py` +
  the QS deploy probe.
- **CH.y — CF.7 cross-cutting tabular-nums sweep** — extend the
  rule to hand-written tables in render.py + studio surfaces.
  Belongs in CF.7 per the lock above.

## Risk profile

Low. All edits are CSS-class additions on existing template
strings; no logic / no data-shape changes. The `bg-surface-alt`
token already exists in `input.css`. Sticky positioning is a
single class added to `<thead>` — defined behavior in every
modern browser.

## Verification

- CSS rebuilt via `scripts/build_app2_css.py` (Tailwind compiles
  `bg-surface-alt` + `sticky top-0 z-10` because input.css
  references them in @source-scanned templates).
- Unit tests pin the markup contract end-to-end through
  `renderTable`'s rendered output (the JS unit-test stub already
  exercises the helper).
- No DB-tier impact; layered runner skip is safe.
