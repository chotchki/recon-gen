# BE.7.A — pyright tests/ scope spike

**Status**: Spike complete. **Recommendation: scope down the
PLAN-as-written; the original estimate was off by 25×.**

## Headline

Pyright strict mode against the full `tests/` corpus:
- **5,201 errors** across **310 files** in 4.3s wall-clock
- vs the PLAN entry's "~50-200 violations" estimate
- **Pyright runs fast enough** (~4s); the cost is human triage of
  the 5,201 hits, not tool execution.

## By subtree

| Subtree | Errors | Files | Avg/file |
|---|---|---|---|
| `tests/e2e` | 1,931 | 51 | 37.9 |
| `tests/json` | 1,690 | 17 | **99.4** |
| `tests/unit` | 1,129 | 84 | 13.4 |
| `tests/audit` | 256 | 11 | 23.3 |
| `tests/data` | 143 | 11 | 13.0 |
| `tests/docs` | 16 | 3 | 5.3 |
| `tests/schema` | 14 | 4 | 3.5 |
| `tests/l2` | 12 | 1 | 12.0 |
| `tests/cli` | 6 | 1 | 6.0 |
| `tests/js` | 4 | 1 | 4.0 |

`tests/json` is the heaviest density (~100 errors/file across 17
files) because every json structural test walks the deep AWS
QuickSight dict-shape, which is `dict[str, Any]` at the boundary.

## By category

| Category | Count | What it is |
|---|---|---|
| **unknown_cascade** | 4,302 | reportUnknown{Member,Variable,Argument,Parameter}Type + reportMissingParameterType + reportUnknownLambdaType + reportMissingTypeArgument. Transitive: once one value is `Any`, every downstream access produces another Unknown. |
| **actionable** | 746 | reportAttributeAccessIssue + reportOptionalMemberAccess + reportArgumentType + reportOptionalSubscript + reportOptionalIterable. These are the **runtime-bug-catching** rules — the cfg.as_of_frame / list(None) / unique_inflows class that motivated BE.7 in the first place. |
| **hygiene** | 128 | reportUnusedImport / reportUnusedVariable. Easy fixes but low signal. |
| **other** | 25 | reportDeprecated (8), reportReturnType (6), reportCallIssue (4), and a few one-offs. |

## What changed since the PLAN estimate

The "~50-200" estimate predated:
- The Phase L tree migration (every tree leaf is hand-constructed
  in test fixtures; pyright sees them as `Any` because the tests
  don't annotate fixture returns).
- The X.2.o async DB / App2 server expansion (many e2e tests
  consume `Any`-typed async fetcher results).
- The Z.B/Z.C deployment-name + db-table-prefix refactor (boto3
  call results pyright knows nothing about without `boto3-stubs`).
- The 138-file e2e test corpus growth post-AT.

Net: pyright's strict mode on `tests/` is now a different size
than the spike-entry author estimated.

## Why the unknown_cascade dominates

Test code has a structural hygiene story src/ doesn't:
- **Fixture returns are typically `Any`** (`def cfg(): ...` returns
  a Config but pyright can't infer it without annotation; ~500
  such fixtures across the corpus).
- **AWS API call results are `dict[str, Any]`** unless boto3-stubs
  is configured (the json/* tests walk these dicts heavily).
- **`pytest.parametrize` decorators erase types** on the wrapped
  function's params unless explicitly annotated.

Each Unknown then propagates: `cfg.deployment_name` → Unknown
(from the fixture's Any return) → `f"{cfg.deployment_name}-x"` →
Unknown → `client.create_dashboard(DashboardId=...)` → reportUnknown
ArgumentType. One missing annotation produces 10+ cascade errors.

## What "actionable" looks like

The 746 actionable errors are the real signal — these are the
shapes that crash at runtime. Sample (from the dump):

- `reportOptionalMemberAccess` (279): test does `cfg.demo_database
  _url.split(...)` where `demo_database_url: str | None`. Same
  shape as the v11.22.4 chase's `cfg.test_generator.as_of_frame`
  vs `cfg.as_of_frame`.
- `reportArgumentType` (264): test passes `None` where a typed
  function wants `str` (or vice versa).
- `reportAttributeAccessIssue` (90): the exact shape that broke
  in v11.22.4 — calling a method that doesn't exist on the typed
  object.

These would have caught real CI failures during the v11.22.4
chase. The motivation for BE.7 stands; the scope of "land it in
one phase" doesn't.

## Three realistic paths forward

### A — Scope down to actionable rules only

Add `tests/**/*.py` to `pyright.include` but configure pyright
overrides to disable the 7 unknown_cascade rules (+ ignore
unused imports/vars). The 746 actionable + 25 other = ~770
errors become the visible surface; the 4,302 cascade noise stays
suppressed at the config layer.

- **Wall-clock**: ~4-6 hours to triage the 770 actionable hits.
  Most will fix or get `# type: ignore[reason]: WHY` per the
  src/-strict-scope pattern.
- **Trade-off**: BE.7's "make wrong unrepresentable" intent gets
  weaker — we accept that fixtures stay `Any`-typed and only
  catch the higher-confidence shapes. But we ship something
  this cycle.
- **Mirrors how src/ strict-scope expanded**: src/ also has
  unknown_cascade noise on dataclass field defaults; the file-
  by-file inclusion gates on what's manageable per-file. Same
  posture here.

### B — Tighter slice: actively-iterated files only

Add 3-5 files from the v11.22.4 BE.7 amendment to
`pyright.include`:
- `tests/e2e/test_inv_filters.py`
- `tests/e2e/test_exec_sheet_visuals.py`
- `tests/e2e/test_inv_dashboard_agreement.py`
- `tests/e2e/test_l1_filters.py`
- `tests/e2e/test_l1_account_filters.py`

These are the files where bugs from v11.22.4 surfaced as
production CI cycles. Fix the errors in these 5 (~500 total
errors); land BE.7 as a starter slice + queue the full
sweep for a separate phase.

- **Wall-clock**: ~2-3 hours for the 5 files.
- **Trade-off**: lint catches drift only on those 5 files; the
  rest of tests/ remains opt-in.

### C — Multi-phase BE.7 mirroring BE.4

Spike (done) → categorize → fan-out parallel agents per subtree
→ consolidate. ~10-20 hours wall-clock across multiple sessions.

- **Trade-off**: best ROI; biggest investment. The 5,201 number
  is also the strongest possible argument that this work was
  always going to be a phase, not a task.

## Top 15 hottest files

The first 15 files account for **2,360 errors (45% of the total)**.
A file-by-file approach would naturally start here:

| Errors | File |
|---|---|
| 484 | `tests/json/test_investigation.py` |
| 235 | `tests/unit/test_tree.py` |
| 183 | `tests/json/test_cli_json.py` |
| 183 | `tests/json/test_kitchen_app.py` |
| 178 | `tests/e2e/conftest.py` |
| 169 | `tests/json/test_l2_flow_tracing.py` |
| 158 | `tests/json/test_l1_dashboard.py` |
| 145 | `tests/e2e/test_l1_dashboard_structure.py` |
| 144 | `tests/e2e/test_inv_dashboard_structure.py` |
| 141 | `tests/e2e/test_l1_filters.py` |
| 114 | `tests/json/test_cleanup.py` |
| 114 | `tests/unit/test_runner_skeleton.py` |
| 113 | `tests/e2e/test_audit_dashboard_agreement.py` |
| 111 | `tests/e2e/test_exec_sheet_visuals.py` |
| 111 | `tests/e2e/test_l1_account_filters.py` |

## Recommendation

**Path A** (scope down to actionable). The unknown_cascade noise
is a separate hygiene story (annotate fixture returns, add
boto3-stubs to dev deps, etc.) — not the v11.22.4-motivated
"catch runtime shapes at compile time" goal. Disabling those 7
rules in `pyproject.toml::[tool.pyright]` for `tests/` only
keeps the actionable signal visible without 4,302 noise hits.

If the principal wants the full sweep, **Path C** is the right
shape — but it's a multi-session investment that this conversation
can't responsibly close.

## What the spike DIDN'T cover (intentional)

- **boto3-stubs as a fix vector.** Several hundred Unknown
  cascade hits would go away with `boto3-stubs[quicksight,sts,
  rds]` in `[dev]` extras. Worth measuring before any path
  commits.
- **Per-file `# type: ignore` strategy.** The Phase BE proper
  added `# typing-smell: ignore[<check>]: <why>`; pyright uses
  `# type: ignore[<rule>]`. Both are inline-with-WHY. The
  syntax is documented; not in scope for the spike.
- **Pre-existing src/-scope cleanups**. The src/ include list
  is curated, not full-coverage. Some src/ files are also
  un-checked. This spike doesn't propose changing src/ scope.
