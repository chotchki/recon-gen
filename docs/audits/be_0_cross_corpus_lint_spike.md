# BE.0 — Cross-corpus duplication lint spike

**Status:** spike for sign-off — BE.1+BE.2 implementation gated on the decisions below.
**Date:** 2026-05-24.
**Prompted by:** AA.A.11 (backlog), promoted to Phase BE on 2026-05-24. User-flagged as "huge structural win" — test↔src duplication is a class of bug we haven't systematically hunted.

The spike's job: measure the false-positive rate, the walk runtime, and whether the dual approach (1 + 3) actually reinforces or is redundant. Either result is signal — clean baseline pins the codebase as already-disciplined; messy result surfaces real drift.

---

## Headline results

**Approach 1 (content-based SQL fingerprint) is empirically not needed in this codebase.** 0 hits at threshold 100+ chars; the 5 hits at threshold 50 are all SQL SELECT-prefix strings (drift/overdraft/anomaly detectors) where the test legitimately asserts on a substring of the production query.

**Approach 3 (provenance — test literal == src module-level constant) is the high-leverage win.** 144 inline-literal hits across the test corpus where the literal exactly matches an `UPPER_SNAKE` constant in `src/recon_gen/`. Real drift potential — if src renames the constant, tests silently keep asserting the old value.

**Runtime:** approach 1 ≈ 0.7s total; approach 3 ≈ 0.6s total. Both prelude-friendly (well under 1s, comparable to the existing `b.15.lint.*` walks).

The original "land both, they reinforce each other" framing turns out to be wrong for THIS codebase — the two approaches catch disjoint bug classes, but only approach 3 finds bugs. The pair shape is still architecturally sound (catches content-equality + provenance drift independently); approach 1 just doesn't fire because the codebase is already disciplined on long-form SQL.

## Methodology

Two throwaway scripts at `/tmp/be_0_approach1.py` + `/tmp/be_0_approach3.py` walk the corpus via Python's `ast` module:

### Approach 1 (content-based)

1. Walk `src/recon_gen/**/*.py`, extract every `ast.Constant` string value, normalize (collapse whitespace + lowercase), build `{normalized_value: [(file, line), ...]}`.
2. Walk `tests/**/*.py`, find string literals where (a) `len(value) >= threshold` AND (b) regex matches a SQL fingerprint (`SELECT`/`FROM`/`WHERE`/`<<$p`/`INSERT INTO`/`UPDATE`/`CREATE TABLE`/etc., case-insensitive).
3. For each, check if `normalize(value)` is in the src index.
4. Report counts at thresholds {50, 100, 150, 200, 300, 500}.

### Approach 3 (provenance)

1. Walk `src/recon_gen/**/*.py`, find module-level assignments `NAME = "value"` where `NAME` is UPPER_SNAKE_CASE (or `_UPPER_SNAKE` private), `3 <= len(value) <= 200`. Build `{value: [(file, line, name), ...]}`.
2. Walk `tests/**/*.py`, find every string literal INSIDE an `ast.Assert` node.
3. For each, check if the literal value matches any src constant value exactly.
4. Report hits grouped by src constant (to see which symbols are most duplicated).

Skipped `test_typing_smells.py` itself (it legitimately contains src-derived literals in its own assertions).

## Approach 1 results

```
src/ index: 7811 unique normalized literals in 0.33s
  (15554 total literals; some duplicated across src files)

threshold=  50 chars  →     5 hits  (0.40s walk)
threshold= 100 chars  →     0 hits  (0.38s walk)
threshold= 150 chars  →     0 hits  (0.38s walk)
threshold= 200 chars  →     0 hits  (0.38s walk)
threshold= 300 chars  →     0 hits  (0.37s walk)
threshold= 500 chars  →     0 hits  (0.37s walk)
```

The 5 hits at threshold 50:

| Test | Src | Shared value |
|---|---|---|
| `test_au0_overdraft_full_spine.py:124` | `spine/overdraft.py:75` | `"SELECT account_id, business_day_start, stored_balance FROM "` |
| `test_as0_drift_full_spine.py:210` | `spine/drift.py:68` | `"SELECT account_id, business_day_start, drift FROM "` |
| `test_at0_anomaly_full_spine.py:114` | `spine/anomaly.py:78` | `"SELECT sender_account_id, recipient_account_id, window_end, z_bucket FROM "` |
| (+ 2 similar) | | |

All five are spine-detector SQL prefixes — the tests assert that the production SQL has a certain shape. Could be migrated to "test imports the prefix constant from src," but the test author probably WANTED an independent assertion (in case the src changes shape, the test fires to call out the change). Three options for these:

1. Migrate: extract the prefix as a `_SQL_PREFIX` constant in src; test imports + asserts on it.
2. Allowlist: per-line `# typing-smell: ignore[no-test-src-sql-duplication]: independent shape-check`.
3. Threshold up to 100 → these auto-pass.

**Recommendation:** threshold 100 is the right floor for approach 1. The 0-hit baseline at 100+ is the codebase's pinned state; future drift trips the lint.

## Approach 3 results

```
src/ module-level constants (UPPER_CASE only, len 3-200): 181 unique values in 0.17s
  (188 total constant assignments; some share values)

test assertions with inline literals matching a src constant:
  144 hits  (0.39s walk)
```

Top duplicated constants:

| Count | src location | Constant | Example test |
|---|---|---|---|
| 12 | `apps/l1_dashboard/app.py:261` | `_DRIFT_NAME` | `tests/unit/test_rich_text.py:296` |
| 10 | `common/cleanup.py:22` | `MANAGED_TAG_KEY` | `tests/unit/test_config_loader.py:121` |
| 6 | `common/rich_text.py:53` | `BR` | `tests/unit/test_rich_text.py:185` |
| 5 | `common/spine/_emit_helpers.py:53` | `DEFAULT_PREFIX` | `tests/unit/test_spine_az1_semantic_lock_json.py:52` |
| 5 | `apps/l1_dashboard/app.py:285` | `_OVERDRAFT_NAME` | `tests/js/test_render_barchart.py:212` |
| 5 | `apps/l2_flow_tracing/datasets.py:90` | `DS_POSTINGS` | `tests/json/test_l2_flow_tracing.py:201` |
| 5 | `apps/l1_dashboard/app.py:379` | `_TRANSACTIONS_NAME` | `tests/json/test_l1_dashboard.py:115` |
| 4 | `apps/l1_dashboard/app.py:336` | `_SUPERSESSION_AUDIT_NAME` | `tests/unit/test_handbook_invariants.py:113` |
| 3 | `common/drill.py:63` | `DRILL_RESET_SENTINEL_VALUE` | `tests/unit/test_tree_filter_specs.py:108` |
| 3 | `common/sql/app2_filters.py:45` | `_DATE_FROM_SENTINEL` | `tests/unit/test_sql_app2_filters.py:29` |
| 3 | `common/sql/app2_filters.py:46` | `_DATE_TO_SENTINEL` | `tests/unit/test_sql_app2_filters.py:30` |
| 3 | `apps/l1_dashboard/app.py:250` | `_GETTING_STARTED_NAME` | `tests/json/test_l2_flow_tracing.py:177` |
| 3 | `apps/l2_flow_tracing/app.py:135` | `_RAILS_NAME` | `tests/json/test_l2_flow_tracing.py:177` |

Sample of the 144 hits (eyeball signal-vs-noise):

- `tests/unit/test_html_filter_widgets.py:110` ← `common/html/render.py:275` (`_HTMX_SRC` = `"/static/vendor/js/htmx.min.js"`)
- `tests/unit/test_html_filter_widgets.py:111` ← `common/html/render.py:276` (`_D3_SRC` = `"/static/vendor/js/d3.min.js"`)
- `tests/unit/test_tree_filter_specs.py:108` ← `common/drill.py:63` (`DRILL_RESET_SENTINEL_VALUE` = `"__ALL__"`)
- `tests/unit/test_tree_filter_specs.py:203` ← `apps/l1_dashboard/datasets.py:954` (`P_L1_DS_BALANCE_DATE_DSP` = `"pL1DsBalanceDate"`)

## Bug-class analysis

The 144 hits split into FOUR rough categories based on the constant's role + the test's intent:

1. **Sheet/dataset names** (`_DRIFT_NAME`, `_TRANSACTIONS_NAME`, `DS_POSTINGS`, `_GETTING_STARTED_NAME`, …) — about 40 hits. Renaming the constant would silently leave tests asserting the old name; this is exactly the provenance drift class. **Strong import-the-constant case.**

2. **Sentinels / canonical values** (`DRILL_RESET_SENTINEL_VALUE` = `"__ALL__"`, `_DATE_FROM_SENTINEL`, `_DATE_TO_SENTINEL`, `DEFAULT_PREFIX` = `"spec_example"`) — about 20 hits. If the sentinel changes, the test should track. **Strong import-the-constant case.**

3. **Asset paths / canonical strings** (`_HTMX_SRC`, `_D3_SRC`, `MANAGED_TAG_KEY` = `"ManagedBy"`) — about 30 hits. These RARELY change but the same drift concern applies. **Medium import-the-constant case** (some are intentional independent assertions, e.g., "the canonical tag key is exactly this string — if it changes the cleanup story breaks").

4. **Parameter names / dataset identifiers** (`pL1DsBalanceDate`, `BR`, `_OVERDRAFT_NAME`, others) — about 54 hits. Mix of the above; some are "test asserts on the live parameter name" (drift class) and some are independent contract assertions.

## Open decisions for operator review

- **D1 — Approach 1 implementation in BE.1.** Land it with threshold 100 (the 0-hit baseline). Catches future drift if anyone copies a long SQL string from src into a test. Cheap (0.4s walk). **Recommendation: ship it as a future-drift guard even though current corpus is clean.**
- **D2 — Approach 3 implementation in BE.2.** Implement against UPPER_SNAKE module-level constants only (the spike scope). 144 current hits need to be either migrated to imports or allowlisted with WHY. Implementation cost is small; sweep cost (BE.4) is moderate. **Recommendation: ship it AND drive the sweep.**
- **D3 — Sweep strategy** (BE.4). Triage the 144 hits into:
  - **Migrate** (~60 expected, the sheet-name + sentinel + dataset-name categories) — test imports the constant from src.
  - **Allowlist with WHY** (~40 expected, the "intentional independent contract" cases like asset paths + cleanup tag key) — `# typing-smell: ignore[no-inline-production-constants]: independent contract check`.
  - **Refactor src** (~10 expected) — cases where the src constant should itself be importable from a more public seam, OR cases where the test reveals that two src files define the same value separately (consolidation opportunity).
  - The remaining 30-40 are likely a mix of the above; the actual split surfaces during the sweep.
- **D4 — Mode (prelude vs opt-in)**. Both checks together: ~1.3s. Well under the 1s/lint informal threshold, but adds up if more lints land. **Recommendation: prelude.** If we ever feel the bloat, the conftest can fold a `--skip-slow-lints` flag.
- **D5 — Allowlist syntax**. Existing `# typing-smell: ignore[<check>]: <reason>` pattern (already in the BC.1 lints). **Recommendation: same shape; no new mechanism.**
- **D6 — Scope: do we also walk `tests/e2e/` ↔ `tests/e2e/_drivers/`?** Same class of drift (test asserts on a driver-internal string that the driver itself derives). Sized similar to the main sweep. **Recommendation: defer to BE.5; decide after the BE.4 sweep tells us how much extra signal vs noise the driver-corpus adds.**
- **D7 — Approach 1's threshold = 100 isn't risk-free.** A test that copies a 50-90-char SQL prefix slips through. The 5 current hits are all in that range. We can either:
  - Migrate them (5 small edits) and lower the threshold to 50, OR
  - Leave at 100 and accept those 5 as the baseline.
  **Recommendation: lower the threshold to 50 in BE.1 after migrating the 5 hits.** Stricter floor; the migration is small + valuable.

## Recommended sequencing

1. **BE.1** ships approach 1 at threshold 100 (immediate 0-hit baseline). After BE.4 lands, lower threshold to 50.
2. **BE.2** ships approach 3 STAGED-DISABLED (same pattern as BC.1's D8 lint — registered but not enabled in `_build_checks` until BE.4's sweep clears the existing hits).
3. **BE.4** sweep — migrate ~60 hits to imports, allowlist ~40 with WHY. Enable BE.2 at the end.
4. **BE.5** scope question (driver corpus); revisit BE.1 threshold.

## What the spike DIDN'T cover (intentional)

- **Multi-file value matching.** Approach 3 just looks at module-level scalar assignments. Dataclass field defaults, function arg defaults, class attributes — all skipped. If a test inline-asserts a value that's a `@dataclass(frozen=True)` field's default, the spike misses it. **For BE.2:** expand the src scan to cover dataclass field defaults + maybe class attributes; measure the FP rate again.
- **Substring matching.** Approach 1 only flags EXACT normalized matches; a test that copies HALF a SQL query slips through. **For BE.1:** the substring/fuzzy-match version is doable but multiplies the FP rate. Stick with exact-match for v1.
- **Type-narrowed signals.** A test that asserts `"Drift" == sheet.name` and a src that declares `_DRIFT_NAME = "Drift"` — approach 3 catches it. But if the test asserts `"Drift" in some_string`, the literal is still inside an `Assert` node; approach 3 still catches it. Coverage is broad already.

## Conclusion

The spike answered the original "is this dual-approach valuable?" question with: **approach 3 is the value; approach 1 is the guard.** The codebase is empirically disciplined on long-form SQL (approach 1 = 0 hits at meaningful thresholds), but has substantial provenance drift potential (approach 3 = 144 hits, dominated by sheet-name + sentinel + dataset-identifier categories).

Both approaches are runtime-cheap (~0.6s each, prelude-friendly). The implementation cost is small; the sweep cost (BE.4) is the real ask — 144 hits to triage into migrate / allowlist / refactor.

**Recommended: ship both checks per the BE.1 + BE.2 + BE.4 sequencing above. Do not skip approach 1 even though it's empty today** — it's the future-drift guard that keeps the codebase from sliding into copy-paste-SQL territory.
