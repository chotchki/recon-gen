# BF.0 — pyright src/ scope spike

**Status**: Spike complete. **Phase BF is much more tractable than BE.7.A surfaced for tests/**: 1,193 errors across 161 files (vs 5,201 across 310 tests/ files).

## Headline

| Surface | Errors | Files | Per-file avg |
|---|---|---|---|
| `tests/` strict (BE.7.A) | 5,201 | 310 | 16.8 |
| `src/recon_gen/` strict (BF.0) | **1,193** | 161 | 7.4 |

The src/ surface is materially smaller. Most src/ files were already in strict scope (91 of 161, the curated include list); the residual 70 are mostly small or moderately-complex modules.

## By module

| Module | Errors | % of total | Notes |
|---|---|---|---|
| `recon_gen/cli` | 700 | 58.7% | Click CLI handlers — heavy on dict-shaped kwargs, untyped Click decorators, `os.environ` access |
| `recon_gen/common` | 411 | 34.5% | Mixed: handbook vocab walks, theme presets, sheet defs, persona ops, audit pdf helpers |
| `apps/l2_flow_tracing` | 28 | 2.3% | Mostly app.py + datasets.py |
| `apps/l1_dashboard` | 20 | 1.7% | Same |
| `apps/investigation` | 17 | 1.4% | Same |
| `apps/executives` | 5 | 0.4% | Same |
| `recon_gen/main.py` | 12 | 1.0% | The `python -m recon_gen` entry |

## Surprise

**The apps/ layer (where BE.7.C.2's lurking-bug investigation implicated the cascade) has only ~70 total errors** (4-28 per app). The bulk of un-typed src/ is cli/ + common/.

But COUNT ≠ LEVERAGE. The apps/ types propagate broadly through tests/ — every `build_l1_dashboard_app(cfg) -> App` consumed by 5+ test files becomes a cascade source if untyped. Cli/ is mostly invoked from `recon-gen` shell entry, not directly from tests/. So:

- **apps/ work**: low intrinsic count, **high cascade-collapse leverage for tests/**.
- **cli/ work**: high intrinsic count, **low cascade-collapse leverage for tests/**.
- **common/ work**: mixed (some helpers used in tests, some not).

## By rule

```
   379  reportUnknownMemberType        ┐
   323  reportUnknownArgumentType      │
   179  reportUnknownVariableType      ├─ unknown_cascade: 1,024 / 1,193 = 86%
    88  reportUnknownParameterType     │
    55  reportMissingParameterType     ┘
    50  reportMissingTypeArgument
    36  reportUnusedImport             ┐
    24  reportUnusedFunction           ├─ hygiene: 65
     5  reportUnusedVariable           ┘
    20  reportArgumentType             ┐
     6  reportAttributeAccessIssue     ├─ actionable: 33
     7  reportUnnecessaryIsInstance    ┘
```

The shape mirrors tests/: dominantly unknown_cascade (86%) with a tail of actionable + hygiene. Same cascade-collapse strategy applies: annotate producer-side returns + class attrs + module-level constants.

## Recommended sequencing for BF.1 fan-out

Prioritized by cascade-collapse leverage on tests/, not by raw error count:

| Phase B agent | Slice | Errors | Why this priority |
|---|---|---|---|
| **1 — apps/** | All 4 apps + common/sheets/app_info.py | ~80 | **Highest tests/-cascade leverage**: app builder functions called by ~80 test files. Cheap to land. |
| **2 — common/ (test-adjacent)** | common/handbook/, common/persona.py, common/clickability.py, common/aging.py, common/rich_text.py, common/sql/, common/theme.py, common/cleanup.py, common/drill.py, common/datasource.py, common/probe.py, common/provenance.py, common/deploy.py, common/sheets/ | ~250 | Moderate cascade leverage — these utility modules are imported by many tests + apps. |
| **3 — common/pdf + audit-chrome** | common/pdf/audit_chrome.py + signing.py | ~80 | Audit PDF generation. Lower tests/ leverage but bounded scope. |
| **4 — cli/ (defer-first option)** | All cli/ handlers | 700 | **Lowest tests/-cascade leverage** — cli is invoked from shell, not tests. Could be deferred to BF-followon. |

If we want this phase to ship quickly + cascade-collapse tests/ ASAP, **Phase 1 (apps/) alone** delivers the bulk of the BE.7.C.3 benefit. Phase 2 is the polish. Phase 4 (cli/) is independent value but doesn't pay back to BE.7.

## Recommendation

**Run BF.1 in 2 slices first** — apps/ + common/(test-adjacent). That's ~330 errors (~28% of the full BF surface) targeting the modules that actually feed tests/ cascade. Re-measure tests/ via BE.7.A's spike script after BF.1 lands; expect a substantial tests/-side cascade collapse for free.

Then decide whether BF.4 (cli/) is worth pursuing in this phase or punted as a follow-on. The cli/ work doesn't unblock BE.7.C.3 — it's its own independent typing hygiene chunk.

## What the spike DIDN'T cover (intentional)

- **Producer-side annotation strategy per module.** BF.1 agents need to look at each app's `App.emit_analysis()` factory + module-level `_NAME` / `_TITLE` constants + the `App` dataclass attrs to figure out what types to add. The spike just counted hits.
- **Real bug surfacing.** Like BE.7.B/C.2, BF.1 will probably surface real bugs (NewType-leaks, missing None-guards, etc.) when src/ becomes strict-typed. The spike output's 20 `reportArgumentType` + 6 `reportAttributeAccessIssue` are the candidates.
- **3rd-party stub gaps.** Some cli/ Click cascade may be `Click` decorator types pyright doesn't have stubs for — those would need `# type: ignore[<rule>]: WHY` per the BE.7 pattern.
