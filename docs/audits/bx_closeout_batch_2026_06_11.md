# BX Closeout Batch — 2026-06-11

Sequential autonomous batch covering the remaining BX cells outside the cold-read backlog. Four agents fanned out one-at-a-time on `main`, each committing + pushing on completion.

## Cells

| Cell    | Status   | Commit     | Summary                                                                 |
|---------|----------|------------|-------------------------------------------------------------------------|
| vocab   | partial  | `2bdca38a` | Vocabulary alignment landed; residual call-sites pending in follow-ups. |
| BX.3    | shipped  | `d9631e82` | Rail list table view (session-only toggle, grouped by `source_role`).   |
| BX.16   | shipped  | `1936dabf` | Inline chain shape-preview below DSL field (reuses BX.8 mini-diagram).  |
| BX.17   | shipped  | `cf831d52` | Polish cluster: duration picker + ref-panel defaults + DSL autocomplete; new `FieldKind="duration"` + driver fix. |

## BX phase state after this batch

Three of four cells in this batch shipped clean; vocab landed partial and is tracked in its own follow-up entry in PLAN.md. What remains open in Phase BX:

- **BX.0.5a** — outstanding implementation cell, not yet picked up.
- **BX.18 cold-reads** — cold-read pass against the BX surface (parity with `bt_cold_read.md` / `bu_cold_read.md` style); scheduled but not started.

Everything else in BX is either shipped or absorbed into the partial vocab follow-up.

## Outstanding concerns

- **vocab partial** — call-site sweep is incomplete; mixed old/new vocabulary still lives in some studio surfaces. Risk is cosmetic drift, not correctness, but the longer it sits the more new code lands against the inconsistent shape.
- **BX.18 cold-reads not yet run** — until the cold-read pass fires, "BX is done" is a self-graded claim. Plan to run cold-reads against the BX surface (rail table view, chain shape-preview, duration picker) before declaring the phase closed.
- **BX.0.5a deferral** — not blocking the cells above, but it's the last implementation gap before BX can sweep to PLAN_ARCHIVE.md.
