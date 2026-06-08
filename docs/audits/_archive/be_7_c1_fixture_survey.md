# BE.7.C.1 — fixture-site survey for the BE.7.C annotation pass

**Status:** Survey complete. Read-only inventory; no code edits. This catalog
drives BE.7.C.2's annotation fan-out.

## Summary

- **Total `@pytest.fixture`s in `tests/`**: 151
- **Already strong-annotated (no work needed):** 94
- **Already BE.7.B-annotated:** 4 (skip these)
  - `tests/e2e/conftest.py:130 qs_client -> "QuickSightClient"`
  - `tests/e2e/test_inv_dashboard_structure.py:33 inv_dashboard_definition -> "DashboardVersionDefinitionOutputTypeDef"`
  - `tests/e2e/test_l1_dashboard_structure.py:27 l1_dashboard_definition -> "DashboardVersionDefinitionOutputTypeDef"`
  - `tests/e2e/test_exec_dashboard_structure.py:27 exec_dashboard_definition -> "DashboardVersionDefinitionOutputTypeDef"`
- **Weak / un-annotated cascade producers:** 57
- **Total scoped-consumer references for the 57:** 336

### Per-subtree partitioning (for C.2 fan-out)

| Subtree | Weak fixtures | Consumer refs | Suggested fan-out slice |
|---|---|---|---|
| `tests/e2e/` | 30 | 196 | **2 slices** — split conftest.py (15 fixtures, 111 refs) + the 3 agreement test modules (15 fixtures, 85 refs) |
| `tests/unit/` | 10 | 65 | 1 slice — mostly the `planted_*_sqlite` family with one shape repeated 5 times |
| `tests/json/` | 7 | 39 | 1 slice — `kitchen_app` + `exec_app`/`exec_analysis` are tight cluster |
| `tests/audit/` | 5 | 14 | 1 slice — small |
| `tests/data/` | 4 | 19 | 1 slice — small |
| `tests/cli/` | 1 | 3 | combine with `tests/data/` |
| `tests/schema/` | 0 | — | already clean |
| `tests/docs/` | 0 | — | already clean |
| **Total** | **57** | **336** | **6 fan-out slices** |

### Reality-check vs the BE.7.B "~50 AWS-fixture sites" estimate

The spike estimate of "~50 AWS-fixture sites" was directionally right but the
shape is different than implied:

- **Only 1 raw boto3-client fixture left to annotate** (`per_dialect_qs_client`
  in `tests/e2e/test_audit_dashboard_agreement.py:312`). The cascade-producing
  AWS surface in tests/ is much smaller than the spike implied because BE.7.B
  already covered the conftest's `qs_client` (the single highest-leverage one).
- **The remaining 56 weak fixtures break down into 8 distinct categories** —
  see "By category" below. Half are `Config` / `L2Instance` / `App` returns
  (the next-biggest cascade source). The boto3 work is essentially done.
- **The 5,201 → ~3,500-4,000 BE.7.B extrapolation needs revision.** Per-file
  payoffs of 38-56% came from annotating fixtures that have 12+ consumers in
  the same file. The remaining fixtures with high consumer count are
  `cfg` (66 refs), `l2` (19), `inv_app`/`l1_app` (12 each), `qs_driver` (11),
  `l1_dashboard_driver` (15) — all in `tests/e2e/conftest.py`. Those alone
  should land another big cascade collapse. The long-tail of fixtures with
  1-3 consumers will produce diminishing returns.

### Surprises / signal worth flagging

1. **Three of BE.7.A's top-5 hottest files have ZERO `@pytest.fixture`s**:
   `tests/json/test_investigation.py` (484 errors), `tests/unit/test_tree.py`
   (235 errors), `tests/json/test_cli_json.py` (183 errors). These files build
   everything via module-level constants and per-test inline construction.
   **Fixture annotation will not directly collapse their error counts** — they
   need either (a) helper-function return-type annotations or (b) consumer-side
   `cfg: Config = _TEST_CFG` defaulting patterns to grow type-explicit
   signatures. This is a separate work stream from C.2.

2. **17 weak fixtures already have an inline `# type: ignore[no-untyped-def]`
   with a documented WHY** — these are intentionally un-annotated to avoid
   module-scope imports (driver fixtures, App2 result dicts, per-dialect
   connections). C.2 can either:
   - Use `from __future__ import annotations` + quoted-string annotations
     (`-> "QsEmbedDriver"`) to keep imports `TYPE_CHECKING`-guarded, OR
   - Leave these 17 as `type: ignore` and only annotate the 40 un-guarded
     fixtures. Recommendation: do the quoted-string conversion — it's the
     same pattern BE.7.B used for `qs_client -> "QuickSightClient"`.

3. **The `planted_*_sqlite` family (5 fixtures in tests/unit/) all have the
   same shape**: `def planted_X_sqlite() -> Iterator[object]` yielding a
   `sqlite3.Connection`. Trivial mass-fix to `Iterator[sqlite3.Connection]`.

4. **One fixture is just wrong**: `tests/unit/test_trainer_timeline.py:46
   spec_example() -> object` returns an `L2Instance` (it calls
   `load_instance(...)`). The `object` return is a leftover smell.

5. **`tests/e2e/test_inv_dashboard_agreement.py` and
   `tests/e2e/test_audit_dashboard_agreement.py` are the heaviest non-conftest
   files in `tests/e2e/`** — 6 weak fixtures each. Both build module-scoped
   isolated cfg/app trees + Playwright driver chains. C.2 should treat each
   as its own slice (they're correlated — annotating `isolated_inv_cfg` flows
   through to `isolated_inv_app` flows through to `per_l2_app2_results`).

## Top 10 highest-leverage fixtures (annotation collapses biggest cascade)

| Consumers | File:line | Fixture | Proposed annotation |
|---|---|---|---|
| 66 | `tests/e2e/conftest.py:80` | `cfg` | `Config` |
| 19 | `tests/e2e/conftest.py:191` | `l2` | `L2Instance` (quoted; lazy import) |
| 18 | `tests/json/test_kitchen_app.py:31` | `emitted` | `Analysis` (or its dict form) |
| 15 | `tests/e2e/conftest.py:584` | `l1_dashboard_driver` | `Iterator[tuple["DashboardDriver", str]]` (quoted) |
| 13 | `tests/unit/test_layer1_query.py:63` | `db` | `_FakeConn` (local class) |
| 12 | `tests/e2e/conftest.py:432` | `inv_app` | `App` |
| 12 | `tests/e2e/conftest.py:464` | `l1_app` | `App` |
| 11 | `tests/e2e/conftest.py:146` | `qs_driver` | `Iterator["QsEmbedDriver"]` (quoted) |
| 10 | `tests/json/test_executives.py:51` | `exec_analysis` | `Analysis` |
| 9 | `tests/audit/test_sql.py:172` | `patched_connect` | `Iterator[None]` |

These 10 fixtures alone account for **185 consumer references (55% of all
weak-fixture refs)**. The C.2 pass should prioritize these.

---

## By category (cross-cutting view, for choosing annotation shapes)

### 1. boto3-client fixtures (1 fixture, 1 ref)

| Fixture | File:line | Current | Proposed |
|---|---|---|---|
| `per_dialect_qs_client` | `tests/e2e/test_audit_dashboard_agreement.py:312` | (none) | `"QuickSightClient"` (same as BE.7.B's `qs_client`) |

The other `boto3.client("quicksight", ...)` callsites in tests/ are inline
(not fixtures): `tests/e2e/conftest.py:539` and
`tests/e2e/test_inv_dashboard_agreement.py:463`. Both are in fixture bodies
that yield a *driver* not the client — the local `qs` variable should get
`: QuickSightClient` for completeness but it's not a cascade producer.

### 2. AWS dict-response fixtures (0 fixtures)

BE.7.B already annotated all three `*_dashboard_definition` fixtures. No
remaining `describe_*` response fixtures exist; all other describe-response
uses are inline call patterns inside test bodies (`*_deployed_resources.py`),
which will benefit transitively when `qs_client` is already
`QuickSightClient`.

### 3. `Config`-returning fixtures (6 fixtures, ~80 refs combined)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `cfg` | `tests/e2e/conftest.py:80` | (none) | `Config` | 66 |
| `dialect_cfg` | `tests/e2e/test_audit_dashboard_agreement.py:176` | (none) | `Iterator[tuple[Config, Path, Dialect]]` | 2 |
| `per_dialect_cfg` | `tests/e2e/test_audit_dashboard_agreement.py:290` | (none) | `Config` | 8 |
| `isolated_inv_cfg` | `tests/e2e/test_inv_dashboard_agreement.py:205` | (none, `# type: ignore`) | `Iterator[Config]` | 7 |
| `db_cfg` | `tests/audit/test_pdf_matches_scenario.py:103` | (none) | `Config` | 3 |
| `cfg_with_prefix` | `tests/cli/test_db_fetcher.py:168` | (none, `# type: ignore`) | `Config` | 3 |

`cfg` alone is THE biggest cascade producer in the whole corpus.

### 4. `L2Instance`-returning fixtures (6 fixtures, ~36 refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `l2` | `tests/e2e/conftest.py:191` | (none, `# type: ignore`) | `"L2Instance"` (quoted) | 19 |
| `l2ft_l2_instance` | `tests/e2e/conftest.py:359` | (none) | `"L2Instance"` (quoted) | 2 |
| `spec_instance` | `tests/data/test_auto_scenario.py:38` | (none) | `L2Instance` | 9 |
| `sasquatch_instance` | `tests/data/test_auto_scenario.py:43` | (none) | `L2Instance` | 1 |
| `spec_instance` | `tests/data/test_seed_persona_clean.py:72` | (none) | `L2Instance` | 2 |
| `spec_example` | `tests/unit/test_trainer_timeline.py:46` | `object` | `L2Instance` | 5 |

### 5. `App` (tree)-returning fixtures (8 fixtures, ~42 refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `inv_app` | `tests/e2e/conftest.py:432` | (none) | `App` | 12 |
| `exec_app` | `tests/e2e/conftest.py:451` | (none) | `App` | 7 |
| `l1_app` | `tests/e2e/conftest.py:464` | (none) | `App` | 12 |
| `l2ft_app` | `tests/e2e/conftest.py:483` | (none) | `App` | 1 |
| `isolated_inv_app` | `tests/e2e/test_inv_dashboard_agreement.py:266` | (none, `# type: ignore`) | `App` | 1 |
| `l1_app` | `tests/audit/test_dashboard_extract.py:55` | (none) | `App` | 1 |
| `exec_app` | `tests/json/test_executives.py:43` | (none) | `App` | 2 |
| `kitchen_app` | `tests/json/test_kitchen_app.py:26` | (none) | `App` | 5 |

`App` from `recon_gen.common.tree` — already cheap to import everywhere (no
module-scope import concerns).

### 6. AWS-JSON dict / Analysis emit results (4 fixtures, ~32 refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `emitted` | `tests/json/test_cross_sheet_drill_date_widening.py:50` | `Any` | `dict[str, Any]` (raw `to_aws_json()` output, which is `dict`) | 3 |
| `emitted` | `tests/json/test_kitchen_app.py:31` | (none) | `Analysis` (returns `kitchen_app.emit_analysis()`) | 18 |
| `emitted_dashboard` | `tests/json/test_kitchen_app.py:37` | (none) | `Dashboard` | 1 |
| `exec_analysis` | `tests/json/test_executives.py:51` | (none) | `Analysis` | 10 |

Note: `emitted` in `test_cross_sheet_drill_date_widening.py` calls
`.to_aws_json()` — the AWS-JSON dict form — but `emit_analysis()` in the
others returns the `Analysis` model instance. Same name, different shapes;
the C.2 worker needs to read the body to pick.

### 7. `DashboardDriver`-returning fixtures (7 fixtures, ~38 refs)

All currently un-annotated with documented `# type: ignore[no-untyped-def]`
explaining the module-scope-import-avoidance.

| Fixture | File:line | Proposed | Consumers |
|---|---|---|---|
| `qs_driver` | `tests/e2e/conftest.py:146` | `Iterator["DashboardDriver"]` | 11 |
| `l1_dashboard_driver` | `tests/e2e/conftest.py:584` | `Iterator[tuple["DashboardDriver", str]]` | 15 |
| `inv_dashboard_driver` | `tests/e2e/conftest.py:592` | `Iterator[tuple["DashboardDriver", str]]` | 8 |
| `exec_dashboard_driver` | `tests/e2e/conftest.py:600` | `Iterator[tuple["DashboardDriver", str]]` | 4 |
| `l2ft_dashboard_driver` | `tests/e2e/conftest.py:608` | `Iterator[tuple["DashboardDriver", str]]` | 8 |
| `per_dialect_qs_driver` | `tests/e2e/test_audit_dashboard_agreement.py:380` | `Iterator["QsEmbedDriver \| None"]` | 1 |
| `qs_inv_driver` | `tests/e2e/test_inv_dashboard_agreement.py:441` | `Iterator["QsEmbedDriver \| None"]` | 1 |

Annotation strategy: quoted strings + `if TYPE_CHECKING: from tests.e2e._drivers import DashboardDriver, QsEmbedDriver`.

### 8. DB-connection-yielding fixtures (13 fixtures, ~67 refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `demo_db_conn` | `tests/data/test_l2_runtime_assertions.py:71` | `Any` | `Iterator[Any]` (DB-API connection — see `connect_demo_db` in src; it also returns `Any` with a documented WHY) | 7 |
| `smoke_conn` | `tests/e2e/test_dataset_sql_smoke.py:232` | (none) | `Iterator[Any]` | 1 |
| `smoke_conn` | `tests/e2e/test_demo_apply_row_counts.py:95` | `Any` | `Iterator[Any]` | 1 |
| `per_dialect_conn` | `tests/e2e/test_audit_dashboard_agreement.py:459` | (none, `# type: ignore`) | `Iterator[Any]` (existing comment confirms) | 1 |
| `db_conn` | `tests/e2e/test_inv_dashboard_agreement.py:482` | (none, `# type: ignore`) | `Iterator[Any]` (existing comment confirms) | 1 |
| `db` | `tests/unit/test_layer1_query.py:63` | (none) | `_FakeConn` (local class in module) | 13 |
| `planted_sqlite` | `tests/unit/test_bg2_assertion_logic.py:46` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 5 |
| `planted_drift_sqlite` | `tests/unit/test_bg3_assertion_logic.py:44` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 7 |
| `planted_inv_sqlite` | `tests/unit/test_bg4_assertion_logic.py:26` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 5 |
| `planted_exec_sqlite` | `tests/unit/test_bg5_assertion_logic.py:33` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 6 |
| `planted_l2ft_l1_sqlite` | `tests/unit/test_bg6_assertion_logic.py:23` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 7 |
| `sqlite_cfg` | `tests/unit/test_dashboard_driver_query_db.py:47` | `Iterator[object]` | `Iterator[Config]` (yields the cfg, not the conn) | 4 |
| `sqlite_factory` | `tests/unit/test_html_sql_executor.py:527` | `Iterator[Any]` | `Iterator[Callable[[], Any]]` (it's a factory; the inner conn is still `Any` because the wrapper class is intentional duck-typed) | 8 |

The 5 `planted_*_sqlite` fixtures are a copy-paste family. A single search-replace
pattern (`Iterator[object]` → `Iterator[sqlite3.Connection]` plus the same import
in 5 files) handles all of them.

### 9. Seeded-result / data dicts (4 fixtures, ~5 refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `seeded_audit` | `tests/e2e/test_audit_dashboard_agreement.py:335` | (none) | `tuple[Path, "TestScenario"]` (read body — yields `(out, scenario)` from `apply_db_seed` which returns the `TestScenario` dataclass) | 2 |
| `per_dialect_app2_results` | `tests/e2e/test_audit_dashboard_agreement.py:475` | (none, `# type: ignore`) | `Mapping[str, Mapping[str, object]]` — keys are sheet names, values are per-sheet `{"count": int, "seen": int, "keys": set}` | 1 |
| `seeded_l2_db` | `tests/e2e/test_inv_dashboard_agreement.py:307` | (none, `# type: ignore`) | `None` (side-effect only — `_ = seeded_l2_db` is the consumer pattern). Recommendation: keep `# type: ignore` since adding `-> None` would force the body to `return None` at end. | 2 |
| `per_l2_app2_results` | `tests/e2e/test_inv_dashboard_agreement.py:389` | (none, `# type: ignore`) | `Mapping[str, Mapping[str, object]]` | 1 |

The `Mapping[str, Mapping[str, object]]` form is a stop-gap; a proper TypedDict
for the `{count, seen, keys}` shape would be tighter. Worth a `BE.7.C.3` issue
if C.2 finds it surfaces actionable bugs.

### 10. ThemePreset (1 fixture, 5 refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `spec_example_theme` | `tests/unit/test_main_macros.py:27` | (none) | `ThemePreset` (or `"ThemePreset"`) | 5 |

### 11. Misc / side-effect / fixture-of-fixture (7 fixtures, ~10 refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `_refresh_matviews_once_per_session` | `tests/e2e/conftest.py:210` | (none, `# type: ignore`) | `None` (autouse, no consumers reference it by name) | 0 |
| `warm_aurora` | `tests/e2e/conftest.py:666` | (none) | `None` | 0 |
| `capture_top_queries` | `tests/e2e/conftest.py:706` | (none) | `Iterator[None]` (uses `yield`) | 0 |
| `_cfg_env` | `tests/audit/test_dashboard_extract.py:30` | (none) | `None` | 0 |
| `monkeypatch_module` | `tests/audit/test_dashboard_extract.py:47` | (none) | `Iterator[pytest.MonkeyPatch]` | 1 |
| `patched_connect` | `tests/audit/test_sql.py:172` | (none) | `Iterator[None]` | 9 |
| `_require_playwright` | `tests/json/test_screenshot_harness.py:97` | (none) | `None` | 0 |

The autouse and `_`-prefixed fixtures have 0 consumers by name — annotating
them is hygiene, not cascade collapse. Include in the pass but expect zero
direct error-count payoff per fixture.

---

## By subtree

### tests/e2e/ (30 weak fixtures, 196 consumer refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `cfg` | `tests/e2e/conftest.py:80` | (none) | `Config` | 66 |
| `qs_driver` | `tests/e2e/conftest.py:146` | (none, `# type: ignore`) | `Iterator["DashboardDriver"]` | 11 |
| `l2` | `tests/e2e/conftest.py:191` | (none, `# type: ignore`) | `"L2Instance"` | 19 |
| `_refresh_matviews_once_per_session` | `tests/e2e/conftest.py:210` | (none, `# type: ignore`) | `None` | 0 |
| `l2ft_l2_instance` | `tests/e2e/conftest.py:359` | (none) | `"L2Instance"` | 2 |
| `inv_app` | `tests/e2e/conftest.py:432` | (none) | `App` | 12 |
| `exec_app` | `tests/e2e/conftest.py:451` | (none) | `App` | 7 |
| `l1_app` | `tests/e2e/conftest.py:464` | (none) | `App` | 12 |
| `l2ft_app` | `tests/e2e/conftest.py:483` | (none) | `App` | 1 |
| `l1_dashboard_driver` | `tests/e2e/conftest.py:584` | (none, `# type: ignore`) | `Iterator[tuple["DashboardDriver", str]]` | 15 |
| `inv_dashboard_driver` | `tests/e2e/conftest.py:592` | (none, `# type: ignore`) | `Iterator[tuple["DashboardDriver", str]]` | 8 |
| `exec_dashboard_driver` | `tests/e2e/conftest.py:600` | (none, `# type: ignore`) | `Iterator[tuple["DashboardDriver", str]]` | 4 |
| `l2ft_dashboard_driver` | `tests/e2e/conftest.py:608` | (none, `# type: ignore`) | `Iterator[tuple["DashboardDriver", str]]` | 8 |
| `warm_aurora` | `tests/e2e/conftest.py:666` | (none) | `None` | 0 |
| `capture_top_queries` | `tests/e2e/conftest.py:706` | (none) | `Iterator[None]` | 0 |
| `dialect_cfg` | `tests/e2e/test_audit_dashboard_agreement.py:176` | (none) | `Iterator[tuple[Config, Path, "Dialect"]]` | 2 |
| `per_dialect_cfg` | `tests/e2e/test_audit_dashboard_agreement.py:290` | (none) | `Config` | 8 |
| `per_dialect_qs_client` | `tests/e2e/test_audit_dashboard_agreement.py:312` | (none) | `"QuickSightClient"` | 1 |
| `seeded_audit` | `tests/e2e/test_audit_dashboard_agreement.py:335` | (none) | `tuple[Path, "TestScenario"]` | 2 |
| `per_dialect_qs_driver` | `tests/e2e/test_audit_dashboard_agreement.py:380` | (none, `# type: ignore`) | `Iterator["QsEmbedDriver \| None"]` | 1 |
| `per_dialect_conn` | `tests/e2e/test_audit_dashboard_agreement.py:459` | (none, `# type: ignore`) | `Iterator[Any]` | 1 |
| `per_dialect_app2_results` | `tests/e2e/test_audit_dashboard_agreement.py:475` | (none, `# type: ignore`) | `Mapping[str, Mapping[str, object]]` | 1 |
| `smoke_conn` | `tests/e2e/test_dataset_sql_smoke.py:232` | (none) | `Iterator[Any]` | 1 |
| `smoke_conn` | `tests/e2e/test_demo_apply_row_counts.py:95` | `Any` | `Iterator[Any]` | 1 |
| `isolated_inv_cfg` | `tests/e2e/test_inv_dashboard_agreement.py:205` | (none, `# type: ignore`) | `Iterator[Config]` | 7 |
| `isolated_inv_app` | `tests/e2e/test_inv_dashboard_agreement.py:266` | (none, `# type: ignore`) | `App` | 1 |
| `seeded_l2_db` | `tests/e2e/test_inv_dashboard_agreement.py:307` | (none, `# type: ignore`) | leave as `# type: ignore` (side-effect only) | 2 |
| `per_l2_app2_results` | `tests/e2e/test_inv_dashboard_agreement.py:389` | (none, `# type: ignore`) | `Mapping[str, Mapping[str, object]]` | 1 |
| `qs_inv_driver` | `tests/e2e/test_inv_dashboard_agreement.py:441` | (none, `# type: ignore`) | `Iterator["QsEmbedDriver \| None"]` | 1 |
| `db_conn` | `tests/e2e/test_inv_dashboard_agreement.py:482` | (none, `# type: ignore`) | `Iterator[Any]` | 1 |

**Conftest-slice (15 fixtures, 165 consumers — highest leverage):**
all in `tests/e2e/conftest.py`. **Agreement-tests-slice (15 fixtures,
31 consumers):** `test_audit_dashboard_agreement.py` (7 fixtures) +
`test_inv_dashboard_agreement.py` (6 fixtures) + two `smoke_conn` modules
(2 fixtures). Recommend assigning these as 2 separate C.2 fan-out tasks
so the conftest changes don't block the agreement test work.

### tests/unit/ (10 weak fixtures, 65 consumer refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `planted_sqlite` | `tests/unit/test_bg2_assertion_logic.py:46` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 5 |
| `planted_drift_sqlite` | `tests/unit/test_bg3_assertion_logic.py:44` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 7 |
| `planted_inv_sqlite` | `tests/unit/test_bg4_assertion_logic.py:26` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 5 |
| `planted_exec_sqlite` | `tests/unit/test_bg5_assertion_logic.py:33` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 6 |
| `planted_l2ft_l1_sqlite` | `tests/unit/test_bg6_assertion_logic.py:23` | `Iterator[object]` | `Iterator[sqlite3.Connection]` | 7 |
| `sqlite_cfg` | `tests/unit/test_dashboard_driver_query_db.py:47` | `Iterator[object]` | `Iterator[Config]` | 4 |
| `sqlite_factory` | `tests/unit/test_html_sql_executor.py:527` | `Iterator[Any]` | `Iterator[Callable[[], Any]]` | 8 |
| `db` | `tests/unit/test_layer1_query.py:63` | (none) | `_FakeConn` (local class in module) | 13 |
| `spec_example_theme` | `tests/unit/test_main_macros.py:27` | (none) | `ThemePreset` | 5 |
| `spec_example` | `tests/unit/test_trainer_timeline.py:46` | `object` | `L2Instance` | 5 |

The `planted_*_sqlite` family (5 fixtures) is the single most repetitive
slice — same fix five times.

### tests/json/ (7 weak fixtures, 39 consumer refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `emitted` | `tests/json/test_cross_sheet_drill_date_widening.py:50` | `Any` | `dict[str, Any]` (`to_aws_json()` raw) | 3 |
| `exec_app` | `tests/json/test_executives.py:43` | (none) | `App` | 2 |
| `exec_analysis` | `tests/json/test_executives.py:51` | (none) | `Analysis` | 10 |
| `kitchen_app` | `tests/json/test_kitchen_app.py:26` | (none) | `App` | 5 |
| `emitted` | `tests/json/test_kitchen_app.py:31` | (none) | `Analysis` | 18 |
| `emitted_dashboard` | `tests/json/test_kitchen_app.py:37` | (none) | `Dashboard` | 1 |
| `_require_playwright` | `tests/json/test_screenshot_harness.py:97` | (none) | `None` | 0 |

### tests/audit/ (5 weak fixtures, 14 consumer refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `_cfg_env` | `tests/audit/test_dashboard_extract.py:30` | (none) | `None` | 0 |
| `monkeypatch_module` | `tests/audit/test_dashboard_extract.py:47` | (none) | `Iterator[pytest.MonkeyPatch]` | 1 |
| `l1_app` | `tests/audit/test_dashboard_extract.py:55` | (none) | `App` | 1 |
| `db_cfg` | `tests/audit/test_pdf_matches_scenario.py:103` | (none) | `Config` | 3 |
| `patched_connect` | `tests/audit/test_sql.py:172` | (none) | `Iterator[None]` | 9 |

### tests/data/ (4 weak fixtures, 19 consumer refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `spec_instance` | `tests/data/test_auto_scenario.py:38` | (none) | `L2Instance` | 9 |
| `sasquatch_instance` | `tests/data/test_auto_scenario.py:43` | (none) | `L2Instance` | 1 |
| `demo_db_conn` | `tests/data/test_l2_runtime_assertions.py:71` | `Any` | `Iterator[Any]` | 7 |
| `spec_instance` | `tests/data/test_seed_persona_clean.py:72` | (none) | `L2Instance` | 2 |

### tests/cli/ (1 weak fixture, 3 consumer refs)

| Fixture | File:line | Current | Proposed | Consumers |
|---|---|---|---|---|
| `cfg_with_prefix` | `tests/cli/test_db_fetcher.py:168` | (none, `# type: ignore`) | `Config` | 3 |

---

## Annotation pattern reminder (from BE.7.B)

For fixtures whose return type would force a heavy import at module scope,
use the lazy `TYPE_CHECKING` pattern:

```python
from __future__ import annotations  # at top of file
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recon_gen.common.tree import App
    from recon_gen.common.l2 import L2Instance
    from tests.e2e._drivers import DashboardDriver, QsEmbedDriver
    from mypy_boto3_quicksight.client import QuickSightClient

@pytest.fixture(scope="session")
def inv_app(cfg: "Config") -> "App":
    ...
```

For fixtures whose return type is already cheaply importable at module scope
(`Config`, `pytest.MonkeyPatch`, `sqlite3.Connection`, `Path`), use the
unquoted form — no need for `TYPE_CHECKING` overhead.

For the `# type: ignore[no-untyped-def]` cases: the BE.7.B pattern is to drop
the ignore once the annotation is on. The 17 fixtures currently carrying
`# type: ignore` should land as proper quoted-annotation conversions.

## Consumer-side annotations (for C.2 to remember)

BE.7.B annotated not just fixture returns but also their consumers'
parameter types. Same convention for C.2: when annotating
`@pytest.fixture def cfg() -> Config:`, every test that takes
`def test_foo(cfg)` should become `def test_foo(cfg: Config) -> None:`.
This is where the 38-56% per-file collapse came from in BE.7.B; the fixture
return type alone only fixes the fixture's own file, not the cascade through
its consumers.

The consumer annotation work scales the 57-fixture survey to ~336
consumer-test signatures that also need touching. C.2's wall-clock estimate
should account for this.

## Out of scope (separate work streams)

1. **`tests/json/test_investigation.py` (484 errors), `tests/unit/test_tree.py`
   (235 errors), `tests/json/test_cli_json.py` (183 errors)** — these files
   have ZERO local fixtures. Their cascade comes from module-level
   `_TEST_CFG`/`_INSTANCE` constants and per-test inline construction. They
   need a different annotation pass (helper-function return types +
   module-level constant typing) — not C.2.

2. **TypedDict for `per_l2_app2_results` / `per_dialect_app2_results`** — the
   `Mapping[str, Mapping[str, object]]` proposal is a stop-gap. A proper
   TypedDict for `{count: int, seen: int, keys: set[str]}` would be tighter
   and might surface lurking bugs (the kind BE.7.B found via
   `reportTypedDictNotRequiredAccess`). Queue as `BE.7.C.3` if the C.2
   results justify it.

3. **`pytest.parametrize` decorator type erasure** — BE.7.A's spike noted
   that `@pytest.parametrize` strips param types unless explicitly annotated.
   This is a separate hygiene story; fixture annotation alone won't address
   it.
