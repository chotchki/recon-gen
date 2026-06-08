# BF.4 / BE.7.C.3 — pyright tests/ residual triage

**Status**: Survey for fan-out. 3,227 errors across 167 tests/ files (post BF.1 + BF.3 src/-scope flip).

## Headline by surface

| Surface | Errors | Files | Notes |
|---|---|---|---|
| Pre-BE.7 baseline | 5,201 | 310 | BE.7.A spike |
| Post-BE.7.C.2 | 3,250 | ~167 | Six-slice fixture+consumer fan-out (-1,951) |
| Post-BF.1+BF.3 | **3,227** | 167 | src/-side cascade collapse (-23) |

src/ is at 0 — every remaining cascade is intra-tests/. No more producer-side leverage; this is pure consumer-side annotation + suppression.

## Rule shape

```
   766  reportUnknownMemberType        ┐
   497  reportUnknownVariableType      │
   343  reportUnknownArgumentType      ├─ unknown_cascade: 2,127 / 3,227 = 65.9%
   215  reportUnknownParameterType     │
   165  reportUnknownLambdaType        │
   161  reportMissingParameterType     ┘
   336  reportArgumentType             ┐
    90  reportAttributeAccessIssue     ├─ actionable: 426 (13%)
   295  reportOptionalMemberAccess     ┐
    63  reportOptionalSubscript        ├─ Optional cascade: 414 (12.8%)
    55  reportOptionalIterable         ┘
    93  reportUnusedImport             ┐
    22  reportUnusedVariable           ├─ hygiene: 163 (5%)
    48  reportMissingTypeArgument      ┘
    32  reportTypedDictNotRequiredAccess
```

Notable shifts vs BE.7.A:
- Cascade dropped from 86% to 66% (the producer-annotation work moved errors into more actionable buckets).
- Optional cascade jumped — these are tests walking into AWS dataclass dict shapes (`SqlQuery is possibly None`, `sheets is possibly None`, etc).
- 32 `reportTypedDictNotRequiredAccess` — TypedDict fields accessed without `.get()`/`in` guards.

## Distribution

| Bucket | Files | Errors | % | Strategy |
|---|---|---|---|---|
| **>=200** | 2 | 718 | 22% | One slice each (slice 1 + slice 2) |
| **100-199** | 5 | 744 | 23% | 2-3 slices grouping by domain |
| **50-99** | 7 | 415 | 13% | 1 slice |
| **20-49** | 20 | 639 | 20% | 1 slice |
| **<20** | 133 | 711 | 22% | Sweep slice (most are 1-5 each) |

## Slice plan for BF.4 fan-out (5 parallel worktrees)

| Slice | Targets | Errors | Pattern |
|---|---|---|---|
| **A** | `tests/json/test_investigation.py` | 483 | Solo — biggest hotspot. Mix of helper-fn signatures + Optional walks. |
| **B** | `tests/unit/test_tree.py` + 3 small unit (test_l2_derived, test_dataset_contract, test_typing_smells contributors) | ~310 | Tree primitives + l2 typed walks. |
| **C** | `tests/json/test_cli_json.py` + `tests/json/test_cleanup.py` + `tests/json/test_app_info.py` + `tests/json/test_screenshot_harness.py` | ~393 | CLI / cleanup / app_info shape — Click invoker + deploy/cleanup helpers. |
| **D** | `tests/json/test_l2_flow_tracing.py` + `tests/json/test_l1_dashboard.py` + `tests/json/test_bar_chart_axis_labels.py` + `tests/json/test_table_column_headers.py` + `tests/json/test_cross_sheet_drill_date_widening.py` | ~447 | Dashboard JSON walks — AWS dataclass dict-shape Optional cascade dominant. |
| **E** | `tests/unit/test_runner_skeleton.py` + `tests/unit/test_browser_helpers.py` + `tests/unit/test_html_tree_fetcher.py` + `tests/unit/test_aging.py` + `tests/unit/test_spine_ay4c3_plant_adapter.py` + `tests/unit/test_common_db.py` | ~422 | Unit utilities — mixed shapes. |
| **F** | All remaining: `tests/e2e/*` (594) + `tests/audit/*` (153) + `tests/data/*` (50) + tail | ~1,172 | Broad sweep — many small files (~5-30 errors each), heavy hygiene + Optional. |

Total covered: 3,227 (entire residual).

## Fix-recipe library (from earlier C.2 work)

- **`Optional` cascade on AWS dataclass dict walks**: prefer `assert <field> is not None, "<context>"` before subscripting; reach for `cast(Foo, ...)` only when assertion would lie semantically.
- **Test-local helper fn with `cfg` param**: add `from recon_gen.common.config import Config` + `def helper(cfg: Config) -> ...:`. This is the #1 cascade root.
- **`pytest.param` `marks=` argument**: pyright wants `pytest.param[Any]`; use `marks=pytest.mark.<x>` directly without nesting.
- **`reportUnknownLambdaType`** in `sorted(..., key=lambda x: x[0])`: annotate the lambda — `key=lambda x: x[0]` becomes `key=lambda x: x[0]` after the surrounding type narrows.
- **`# type: ignore[<rule>]`** (mypy-style) → **`# pyright: ignore[<rule>]: <why>`**. mypy-style codes are silently ignored by pyright.
- **`reportUnusedImport`** that's "kept for tests that import via this module": use `from m import X as X` (PEP 484 explicit re-export form).
- **`reportTypedDictNotRequiredAccess`**: wrap in `.get("field")` or assert membership first.

Real bugs to surface (carry-over from BF.1): NewType-leaks, missing None-guards on dataclass attrs that callers depend on. Each slice flags these inline as it goes.

## Exit criteria (BE.7.D)

- After all 5 slices merge: residual goes from 3,227 → < 200 (target).
- Remaining errors are either `# pyright: ignore[<rule>]: <why>` with rationale, or genuine bugs filed as separate followups.
- Flip `pyright.include` to add `tests/` paths in BE.7.D; default-scope `pyright` runs against the tests/ surface; **0 unsuppressed errors at exit**.
- Re-enable `pyright tests/` in CI's static-gates step.
