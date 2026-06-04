# recon-gen v13.1.1 — Cold-Read Findings (upstream)

Independent cold read of v13.1.1 (published wheel) against a demo L2 instance, duckdb dialect, ~300k transactions. 6 context-isolated reviewers, screenshots only, no source/spec access. Findings below are diffed against the prior v12.0.0 cold read; reviewer personas: dashboard skeptic / reconciliation-pragmatist / exec, plus studio integrator / trainer / etl.

## Summary

The **sqlite→duckdb migration is a decisive operational win** and the read-only dashboards are trustworthy (statements tie to the penny; cross-sheet figures agree). Most v12.0.0 **UX findings are still open**, and the **`drift` training plant regressed** — it no longer plants on duckdb.

## Fixed / resolved since v12.0.0
- **Session-Start no longer hangs or corrupts.** The prior sqlite single-writer hang (which also dropped the base `daily_statement_summary` matview) is gone — Session-Start re-seeds the `_v` overlay cleanly on duckdb.
- **The slow analytical sheet that timed out at 60s on sqlite now renders instantly** on duckdb.
- **App-Info clock ordering fixed** — the "latest data" timestamp is now correctly *before* the "generated-at" stamp (was impossible-ordered). Residual: a business-day-rollup "Latest Date" one day past the source is shown uncaptioned, and a freshness rubric flags two matviews as stale — worth a caption.
- **Drift KPI consistency fixed** — the "largest parent drift" figure now reproduces verbatim across all drift renders (was two disagreeing values on different sheets).
- **Partial:** a large "net money moved" figure now carries an "expected near zero" caption + sign signal (still no numeric tolerance band); a date sentinel in the Probe form is now an explained "All time" lower bound.

## NEW regression (highest priority)
- **`drift` plant no longer plants (HIGH).** Clean vs violation training dashboards are byte-identical (md5-equal); Apply reports "2 plant(s) failed: drift, limit_breach_outbound". In v12 the `drift` plant worked. Both currency-denominated plants now fail — observed on duckdb, suggesting a duckdb-path plant bug introduced with the dialect swap. Breaks 2 of 6 plants and blocks re-verification of the drift-magnitude rendering item.

## Still open from v12.0.0 (unaddressed)
- **HIGH — Exec app surfaces no health/exception signal.** The exec-facing app is all volume/coverage; the open-exception count (hundreds) and breach/drift signals live in other apps. Needs a rolled-up program-health tile with threshold banding, drilling to the exceptions sheet.
- **HIGH — `limit_breach_outbound` plant never plants** (clean == violation, reads "0" in both).
- **HIGH — Apply status is self-contradictory** — a green "Apply done." banner stacked directly over a red "N plant(s) failed". Enabled counters also disagree (global header vs per-section).
- **MED — L2 full-flow diagram is an unreadable hairball** (~91 nodes / 108 edges, overlapping-template "blobs", ~half the edges are on-by-default self-loops). End-to-end path tracing is impossible. The L1 diagram, by contrast, is clean. Suggest an entity-focus/neighborhood mode + self-loops off by default.
- **MED — Editor entity-list pages have no search/sort/filter and are enormous** (one list ≈ 40,000px tall). Pure scroll-walls; add search/sort + collapse-by-default.
- **MED — Transfer-Templates Sankey is an illegible hairball** in its default view (admits 11 cyclic edges).
- **MED — Blank landing states read as broken** — several sheets open fully blank until a required picker is set, indistinguishable from an error. Add an empty-state prompt ("pick an account to begin").

## Minor / new
- ETL landing off-by-one copy: a "5-step checklist" banner over a "Three steps" subhead with three cards.

## Works well (keep)
- Statements are self-proving: the 5-number arithmetic ties to the penny and the drift glyph matches the computed residual exactly.
- Cross-sheet figure consistency (drift / overdraft / exceptions reproduce verbatim).
- The exceptions "detail" view is a proper reconciling-items breakout (per-check, dollar-sorted, drillable).
- Studio editor per-entity cards are self-documenting (badges, parent/child wiring, completion expressions, inline source citations); the L1 diagram is legible.
- The ETL Refresh→Triage→Probe loop is discoverable and self-explaining; Triage shows real grouped-gap value.
- 4 of 6 training plants teach cleanly (stuck_pending, chain_parent_disagreement, chain_orphan, phantom_rail).
