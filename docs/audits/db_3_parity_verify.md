# DB.3 — Cold-read v5 parity verify

**Date:** 2026-06-12
**Phase:** DB.3 (cold-read parity verify; closes the visible-divergence loop opened by DB.0)
**Goal:** capture every DB.1-touched Visual side-by-side on QS embed + App2 so the operator can flip through pixel-by-pixel and confirm the parity claim. Anything that doesn't match a `PARITY_BREAKS` enhancement entry is a bug.

## Methodology

Re-uses the existing `tests/e2e/_drivers/` (`QsEmbedDriver` + `App2Driver`) via the parametrized `[qs, app2]` `*_dashboard_driver` fixtures in `tests/e2e/conftest.py`. Each test:

1. Open the host sheet via `driver.open(dashboard_id, sheet=...)`.
2. `driver.wait_loaded(visual_title)` blocks until a representative visual finishes rendering (not a spinner, not the empty-state banner).
3. `driver.screenshot(path=...)` writes the full page to `docs/audits/db_3_parity_verify/<sheet-slug>/{qs,app2}.png`.

Operator drives the capture pass:

```bash
./run_tests.sh up_to=qs_browser --variants=sp_pg_aw -k db3_parity
```

— **qs leg** auto-skips when the dashboard isn't deployed (no live analysis to embed); needs `recon-gen json apply --execute` first. The **app2 leg** always runs against the live DB.

**Not a regression gate** — these aren't pixel-diff assertions. The DA-style typed-invariant gate (DB.2) catches structural drift; this captures the visual taste / pixel-painting drift no type system can encode.

## Sheets in scope (DB.1 coverage)

10 sheets × 2 renderers = 20 captures.

### DB.1.1 — BarChart orientation + color_label

| Sheet | Renderer | Coverage |
|---|---|---|
| L1 Exceptions | `qs` + `app2` | horizontal bar "Exceptions by Check Type" |
| L1 Pending Aging | `qs` + `app2` | stacked horizontal "Stuck Pending by Age Bucket" + color_label="Rail" |
| L1 Unbundled Aging | `qs` + `app2` | stacked horizontal "Stuck Unbundled by Age Bucket" + color_label="Rail" |
| Exec Program Health | `qs` + `app2` | horizontal bars + color_label |
| Exec Money Moved | `qs` + `app2` | stacked horizontal + color_label="Transfer Type" |
| L2FT L2 Exceptions | `qs` + `app2` | horizontal bar by check_type |

### DB.1.2 — Sankey items_limit + (others)

| Sheet | Renderer | Coverage |
|---|---|---|
| Investigation Money Trail | `qs` + `app2` | Sankey with `_SANKEY_NODE_CAP` |
| Investigation Account Network | `qs` + `app2` | two directional Sankeys + Touching Edges table |
| L2FT Chains | `qs` + `app2` | Sankey with items_limit=50 |

### DB.1.3 — KPI Sparkline HIDDEN

| Sheet | Renderer | Coverage |
|---|---|---|
| L1 Drift | `qs` + `app2` | 4-up KPI row — confirm no empty sparkline placeholder reserved below each value |

## Verdict template (operator fills after capture pass)

For each captured pair, the operator confirms one of:

- ✅ **Parity** — pixel-level equivalent within rendering-engine noise (font hinting, sub-pixel anti-aliasing, color-mix vs solid-hex tint hue).
- ⚠ **Acceptable enhancement** — App2 paints something QS doesn't (or vice-versa); the divergence is in `PARITY_BREAKS` as an ENHANCEMENT.
- 🟥 **Bug** — operator flags as a real divergence the registry doesn't cover. File as DB.1.5 (or similar) follow-on.

Fill the table below after the capture pass:

| Sheet | Verdict | Notes |
|---|---|---|
| L1 Exceptions (horizontal bar) | ⏳ pending operator pass | |
| L1 Pending Aging (stacked horizontal) | ⏳ pending | |
| L1 Unbundled Aging (stacked horizontal) | ⏳ pending | |
| L1 Drift (KPI row, sparkline HIDDEN) | ⏳ pending | |
| Exec Program Health | ⏳ pending | |
| Exec Money Moved | ⏳ pending | |
| Inv Money Trail (Sankey cap) | ⏳ pending | |
| Inv Account Network (Sankeys) | ⏳ pending | |
| L2FT L2 Exceptions (horizontal bar) | ⏳ pending | |
| L2FT Chains (Sankey cap) | ⏳ pending | |

## Known divergences (expected — already in PARITY_BREAKS)

These show up in App2 but NOT QS; operator should NOT flag them as bugs:

- **Cell-click on `cell-accent-menu` cells opens the menu** (Phase DA / lock 2). QS only opens the menu on right-click + ⋯ button; App2 also opens on the highlighted cell's left-click. Documented exception to "left moves LEFT".
- **`?` handbook side panel on every sheet's title row** (parity-break `handbook_help_panel` — ENHANCEMENT).
- **`⋯` row menu trailing column** (parity-break `xlsx_export_button` companion — ENHANCEMENT).
- **`↓ XLSX` button above each Table** (parity-break `xlsx_export_button` — ENHANCEMENT).
- **Markdown rich text in TextBox** (parity-break `markdown_prose_richer_than_qs_text` — ENHANCEMENT).
- **Stuck-on-DA-color-mix tint hue** vs QS's `_tint_hex` 10%-white-mix (DA / Phase DA convention origin note). The two compose against different backgrounds (white solid vs row-stripe transparent), so a few-percent hue delta is expected.

## DB.4 unblock criteria

DB.3 is "done" when:

1. Operator runs the capture pass (`./run_tests.sh up_to=qs_browser -k db3_parity`) — produces 20 PNGs.
2. Operator fills the verdict table above. All ⏳ → ✅ or ⚠ (no 🟥).
3. Any 🟥 spawn DB.1.5+ follow-on tasks before DB.4 release cut fires.

If everything is ✅/⚠, DB.4 cuts a v13.16.x release bundling the four DB.1 wins + the DB.2 parity gate.

## Files

- `tests/e2e/test_db3_parity_snaps.py` — the 10 parametrized capture tests.
- `docs/audits/db_3_parity_verify/<sheet-slug>/{qs,app2}.png` — produced by the capture pass (do not pre-commit empty stubs).
- `src/recon_gen/common/parity/breaks.py::PARITY_BREAKS` — known ENHANCEMENT divergences referenced above.
