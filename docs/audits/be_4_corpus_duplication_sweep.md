# BE.4 — corpus duplication sweep plan + decision table

**Status**: Phase A complete (this doc). Phases B + C pending.

## Phase A — survey

Ran the BE.2 lint against the production tests/ corpus (excluding
`tests/unit/_fixtures/` per the lint's normal scope filter; excluding
`tests/unit/test_typing_smells.py` per the BE.0 spike's note). Total
**144 hits**, matching the BE.0 spike's measurement.

### By tests/ subtree (Phase B partitioning)

| Subtree | Hits | Phase B agent |
|---|---|---|
| `tests/json` | 77 | **Agent 1** |
| `tests/unit` | 52 | **Agent 2** |
| `tests/audit` + `tests/js` + `tests/e2e` + `tests/docs` | 15 | **Agent 3** (small-tail) |

3-way split keeps the slices balanced (77 / 52 / 15) and each
agent's edits stay within a disjoint subtree, so worktree
isolation is sufficient — no cross-agent edit conflicts.

### By unique src constant

101 unique src constants. Top 10 by duplication count:

| Count | Constant | Src location |
|---|---|---|
| 18 | `MANAGED_TAG_KEY` | `common/cleanup.py:22` |
| 13 | `_DRIFT_NAME` | `apps/l1_dashboard/app.py:261` |
| 9 | `_GETTING_STARTED_NAME` | `apps/l1_dashboard/app.py:250` |
| 6 | `_OVERDRAFT_NAME` | `apps/l1_dashboard/app.py:285` |
| 6 | `_TRANSFER_TEMPLATES_NAME` | `apps/l2_flow_tracing/app.py:158` |
| 6 | `BR` | `common/rich_text.py:53` |
| 5 | `DEFAULT_PREFIX` | `common/spine/_emit_helpers.py:53` |
| 5 | `_TODAYS_EXCEPTIONS_NAME` | `apps/l1_dashboard/app.py:349` |
| 5 | `DS_POSTINGS` | `apps/l2_flow_tracing/datasets.py:90` |
| 4 | `_TRANSACTIONS_NAME` | `apps/l1_dashboard/app.py:379` |

Full raw per-hit table preserved at the temporary survey dump (not
checked in — regenerable from `/tmp/be_4_phase_a_survey.py` against
HEAD). The cuts below operate on **categories**, not individual hits,
because the 144 hits collapse into ~6 patterns that each take a
deterministic action.

## Decision rules — categories

Phase B agents apply these rules per-hit. Default to migration; only
deviate when the rule says allowlist.

### CATEGORY 1 — Sheet names + titles (private `_SHEET_NAME` / `_SHEET_TITLE` pattern)

Constants: `_GETTING_STARTED_NAME`, `_DRIFT_NAME`, `_DRIFT_TITLE`,
`_DRIFT_TIMELINES_NAME`, `_DRIFT_TIMELINES_TITLE`, `_OVERDRAFT_NAME`,
`_OVERDRAFT_TITLE`, `_LIMIT_BREACH_NAME`, `_LIMIT_BREACH_TITLE`,
`_PENDING_AGING_NAME`, `_PENDING_AGING_TITLE`, `_UNBUNDLED_AGING_TITLE`,
`_SUPERSESSION_AUDIT_NAME`, `_SUPERSESSION_AUDIT_TITLE`,
`_TODAYS_EXCEPTIONS_NAME`, `_TRANSACTIONS_NAME`, `_TRANSACTIONS_TITLE`,
`_DAILY_STATEMENT_NAME`, `_DAILY_STATEMENT_TITLE`,
`_GETTING_STARTED_TITLE` (×2 modules), `_TRANSFER_TEMPLATES_NAME`,
`_RAILS_NAME`, `APP_INFO_SHEET_NAME`.

**~70 hits.** **Action: 🟢 MIGRATE** via direct private-name import.

```python
# Before
assert sheet_names[2] == "Drift"

# After
from recon_gen.apps.l1_dashboard.app import _DRIFT_NAME
assert sheet_names[2] == _DRIFT_NAME
```

Python's underscore-prefix is convention, not enforcement; tests
importing `_DRIFT_NAME` is a legitimate use of a privacy-by-
convention name. Importing keeps the test loud-fails on rename.

**No src refactor needed** for this category — the constants are
already named, just imported under their existing `_NAME` names.

### CATEGORY 2 — Dataset / parameter / conditional-formatting IDs (public `DS_*` / `P_*` / `CF_*`)

Constants: `DS_POSTINGS`, `DS_META_VALUES`, `DS_CHAIN_INSTANCES`,
`DS_TT_INSTANCES`, `DS_TT_LEGS`, `DS_UNIFIED_L2_EXCEPTIONS`,
`DS_APP_INFO_LIVENESS`, `DS_APP_INFO_MATVIEWS`,
`P_L1_DS_BALANCE_DATE_DSP`, `CF_INV_ANETWORK_*`.

**~20 hits.** **Action: 🟢 MIGRATE** via direct public import.

Already public-named; import path is unambiguous. The Investigation
`CF_INV_ANETWORK_*` constants live in `apps/investigation/constants.py`
(not the app module) — import path is explicit there.

### CATEGORY 3 — Cleanup tags (`MANAGED_TAG_KEY`, `DEPLOYMENT_TAG_KEY`, `MANAGED_TAG_VALUE`)

Constants: `MANAGED_TAG_KEY` (18 hits), `DEPLOYMENT_TAG_KEY` (4),
`MANAGED_TAG_VALUE` (1).

**~23 hits.** **Action: 🟢 MIGRATE**.

Import from `recon_gen.common.cleanup`. Public, already importable.

### CATEGORY 4 — Sentinels (`_DATE_FROM_SENTINEL`, `_DATE_TO_SENTINEL`, `_DRILL_RESET_SENTINEL`, `DEFAULT_PREFIX`, `_SASQUATCH_PERSONA_ACRONYM`)

**~14 hits.** **Action: 🟢 MIGRATE** via direct import (private or public
as the symbol's name dictates).

Sentinels are exactly the drift-class the lint exists to catch: a
sentinel value that diverges between prod + tests silently breaks
the sentinel's whole point.

### CATEGORY 5 — Theme constants (`_WHITE`, `_DARK_BLUE`, `_BUNDLE_EDGE_COLOR`)

**~5 hits.** **Action: 🟢 MIGRATE** via direct import.

Theme values are exactly the kind of constant that should never be
asserted with an inline hex code — a theme rename / palette shift
should fail tests loudly.

### CATEGORY 6 — Asset paths + rich-text constants (`_HTMX_SRC`, `_D3_SRC`, `_D3_SANKEY_SRC`, `BR`)

**~12 hits.** **Action: judgment per-callsite**.

Default 🟢 MIGRATE when the test is asserting "the rendered HTML
contains this asset URL" — the asset URL changing should fail tests
loudly so we know to update the CDN reference / SRI hash.

🟡 ALLOWLIST when the test is asserting the *format* of the rendered
HTML (e.g. "every visual that includes a `<br>` has a non-empty
preceding text") — in that case the literal is illustrative, not
the spec under test. Phase B agents flag these cases in their
per-subtree review docs for the principal to confirm.

## Phase B — execution

3 parallel agents, one per subtree slice. Each runs in its own
worktree (`isolation: "worktree"`) so concurrent edits don't
conflict. Per-agent contract:

1. Read this doc; for each hit in the agent's subtree, apply the
   category rule above.
2. For **default-migrate** hits: add the import; replace the inline
   literal with the imported name; run `pytest <touched-file> -q`
   to confirm no regression.
3. For **judgment-required** hits (CATEGORY 6's allowlist cases or
   anything that doesn't fit a clean import): write the proposed
   action + reasoning to `docs/audits/be_4_phase_b_<subtree>_review.md`
   for the principal to confirm before committing the allowlist.
4. Commit on the worktree branch; surface the branch name + the
   review doc path back to the principal.

Src module reads only — no agent edits `src/recon_gen/**` during
Phase B (defers to Phase C if any constant turns out to need
promotion).

## Phase C — consolidation + enable

After all 3 Phase B agents land:

1. Principal reviews each agent's `be_4_phase_b_<subtree>_review.md`
   to confirm allowlist proposals.
2. Apply any src-side promotions (likely 0 — the leading-underscore
   private-import strategy avoids most src refactors).
3. Drop the `# ` comment from BE.2's registration in
   `_build_checks()` so the lint enforces 0 hits going forward.
4. Run the full unit suite; assert 0 typing-smell hits.
5. Append a "what shipped" section to this doc.
6. Single squash commit on the BE branch + merge to main.

## Sequencing notes

- BE.3 (prelude vs opt-in mode) — spike measured combined ~1.3s, well
  under the prelude budget. Recommendation locked: prelude.
- BE.5 (driver-corpus extension) — defer until BE.4 closes so we can
  measure the real signal/noise ratio from the dashboard-corpus sweep
  before extending scope.
- BE.6 (tag) — no version bump unless BE.4's sweep reveals a real
  production bug the migration uncovers. Usually this kind of lint
  doesn't shift the release tag.
