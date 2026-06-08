# X.2.u.1 — Spike: the parametrized `[qs, app2]` driver fixture

**Status:** done. Decisions below are locked for u.2–u.6.

## The question

X.2.q ported every browser e2e onto the `DashboardDriver` protocol, but the
QS-side tests still run QS-only (`qs_driver` fixture) and App2's `test_html2_*`
run App2-only. X.2.u wants "one test body × `[qs, app2]`". The crux: the two
legs get their dashboard *differently* —

- **`qs`** drives a **deployed** dashboard (`<resource_prefix>-<l2>-<app>-…`,
  real data via the QS datasource). The dashboard must already exist.
- **`app2`** drives a **locally-spun** uvicorn server, built from the same
  tree, reading the same DB via `make_live_db_fetcher_for_app(tree_app, cfg)`.
  Nothing pre-exists; the fixture spins it.

So a parametrized fixture can't just `yield driver` — the test also needs the
right *open argument* for each leg. Hence `yield (driver, dashboard_arg)`.

## What the spike ran

`tests/e2e/test_x2u_spike.py` — a `@pytest.fixture(params=["qs", "app2"])`
`l1_driver` yielding `(driver, dashboard_arg)`, plus one test
(`test_l1_landing_lists_declared_sheets`) using only verbs that already work
on both drivers (`open` + `sheet_names`). The `app2` leg ran against a
freshly-spun PG container (`schema apply` + `data apply --seed-density=0.2` +
`data refresh`, ~13k rows) with `QS_GEN_DEMO_DATABASE_URL` pointed at it:

```
tests/e2e/test_x2u_spike.py::test_l1_landing_lists_declared_sheets[app2] PASSED  [1.72s]
```

The `qs` leg was not run live (creds expired at spike time) — but that leg is
not novel: it's exactly `test_audit_dashboard_agreement.py::per_dialect_qs_driver`
(the `describe_dashboard`-skip + `QsEmbedDriver.embed(...)` ctx-manager
pattern), which CI exercises continuously. `test_x2u_spike.py` stays in the
tree until u.2 lands the real parametrized structural tests, then it's deleted.

## Decisions

### Fixture shape — per-app parametrized fixture in `conftest.py`

Four fixtures: `l1_dashboard_driver`, `inv_dashboard_driver`,
`exec_dashboard_driver`, `l2ft_dashboard_driver` — each
`@pytest.fixture(params=["qs", "app2"])`, each `yield (driver, dashboard_arg)`.
A test that wants both renderers depends on the one for its app; a test that
only makes sense for one renderer keeps using `qs_driver` (or grows a
per-test `pytest.skip(... if driver.dialect == ...)`).

To avoid 4× copy-paste, a module-level helper does the body; the four fixtures
are thin wrappers that pass their app's already-existing conftest fixtures:

```python
def _parametrized_dashboard_driver(request, *, cfg, region, account_id,
                                   dashboard_id, app, short):
    if request.param == "qs":
        # skip if get_user_arn() raises (no QS_E2E_USER_ARN)
        # pre-flight describe_dashboard → skip on ResourceNotFoundException
        with QsEmbedDriver.embed(aws_account_id=account_id,
                                 aws_region=region) as d:
            yield d, dashboard_id
    else:  # app2
        # skip if not cfg.demo_database_url
        fetcher = make_live_db_fetcher_for_app(tree_app=app, cfg=cfg)
        with App2Driver.serving(tree_app=app, sheet=app.analysis.sheets[0],
                                data_fetcher=fetcher, dashboard_id=short) as d:
            yield d, short

@pytest.fixture(params=["qs", "app2"])
def l1_dashboard_driver(request, cfg, region, account_id, l1_dashboard_id, l1_app):
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, region=region, account_id=account_id,
        dashboard_id=l1_dashboard_id, app=l1_app, short="l1")
# … inv / exec / l2ft analogous
```

- **(a) conftest, not opt-in per file.** The `<app>_app` / `<app>_dashboard_id`
  fixtures are already in conftest; putting the parametrized driver there means
  any structural/filter test for that app just depends on it. One place to
  maintain the skip logic.
- **(b) function-scoped, both legs.** The QS embed URL is single-use → the
  `qs` leg *must* be function-scoped. The App2 server *could* be module-scoped
  (no single-use constraint), but a parametrized fixture can't mix scopes per
  param, and `App2Driver.serving` spin-up measured ~1–2 s including the page
  load. Acceptable. If it bites later: split a module-scoped
  `<app>_app2_server` fixture out and have the function-scoped param wrap it —
  but YAGNI now.
- **(c) QS-precondition vs App2-spin.** Independent `pytest.skip()` in each
  branch: `qs` skips when no ARN / dashboard not deployed; `app2` skips when
  no `cfg.demo_database_url`. A test thus runs `[qs, app2]`, `[qs]`, `[app2]`,
  or `[]` depending on what's available — the runner's `browser` layer has
  both (deployed QS + the variant's seeded DB), a bare `pytest` dev run
  typically gets `[app2]` only (no `QS_E2E_USER_ARN`).
- **(d) shared-L2 wiring — no prefix-stamp dance.** The existing conftest
  `<app>_app` fixture (`build_<app>_app(cfg, l2_instance=_resolve_test_l2_instance())`,
  post-`emit_analysis()`) is sufficient: it registers the datasets (with the
  L2 prefix baked into the CustomSql), so `make_live_db_fetcher_for_app(tree_app=<app>_app, cfg=cfg)`
  works directly off the *unstamped* conftest `cfg` — `cfg` only supplies the
  dialect (placeholder rewriting) + the pool URL; the prefix is already in the
  SQL. (`test_html2_executives_live.py` stamps `cfg.with_l2_instance_prefix(...)`
  before `build_executives_app` — that's only needed when *also* recomputing
  dataset *IDs* / ARNs from cfg, which the conftest fixture has already done.
  The fetcher path doesn't care.) The DB the `app2` leg reads is
  `cfg.demo_database_url` = the same DB the deployed QS dashboard reads — in
  the runner's matrix that's the variant's seeded container/Aurora; this is
  the **output** slot of `scenario → DB (sqlite/oracle/postgres) → output`.

### App2Driver gaps surfaced — punch-list for u.2/u.4

The spike test deliberately avoided `goto_sheet`; reading the code confirms:

1. **`App2Driver.goto_sheet(name)` is id-based, must become name-based.**
   It currently does `self.open(self._dashboard, sheet=name)` → URL
   `/dashboards/<d>/sheets/<name>`, but App2's route segment is the
   **SheetId**, not the sheet name. The protocol contract (`base.py`) and the
   QS impl (`click_sheet_tab` matching tab text) are name-based. `TreeValidator`
   calls `driver.goto_sheet(sheet.name)` — so u.2 *must* fix this. Fix:
   `App2Driver.serving()` builds `self._sheet_id_by_name = {s.name: s.sheet_id
   for s in tree_app.analysis.sheets}`; `goto_sheet(name)` translates
   name→id. Same for the protocol's `open(dashboard, sheet=)` (QS treats
   `sheet=` as a name) — make App2's `open(sheet=)` accept the name too;
   update the `test_html2_*` / `test_dashboard_driver.py` call sites that
   currently pass SheetIds (`open("smoke", sheet="showcase")` →
   `sheet="Showcase"`, etc.) as u.5 cleanup. The `_smoke_app.py` sheets have
   distinct names vs ids ("Showcase"/"showcase", "MoneyTrail"/"money-trail"),
   so this is a real (latent) inconsistency, not a no-op rename.

2. **`set_slider` — App2 has it, QS raises `NotImplementedError`.** Parametrizing
   `test_inv_filters.py`'s slider tests surfaces *QS* as the gap. Keep those
   tests' QS leg `pytest.skip`'d (`ParameterSliderControl` DOM-drive is the
   open X.2.q follow-on) and let the App2 leg actually run.

3. **`drill_from_first_row` / `drill_from_first_row_via_menu` — App2 raises
   `NotImplementedError`.** App2's cross-sheet drills are `<a href>` clicks
   today, not row-level data-point clicks. Parametrizing the drill tests
   (`test_inv_drilldown` / `test_l1_cross_sheet_drill_date_widening`) surfaces
   this. u.4 decides: wire App2 row-drill, or keep the drill tests QS-only
   with a PLAN note that App2 row-drill is unbuilt. (Leaning: keep QS-only for
   now — App2 row-drill is a real renderer feature, not a test-harness tweak.)

### What u.2 inherits

- The four `<app>_dashboard_driver` fixtures land in `conftest.py` (above).
- `App2Driver.goto_sheet` + `open(sheet=)` become name-based (gap #1) — this
  is a prerequisite for the `TreeValidator` port; do it first in u.2.
- `test_{l1,inv,exec}_sheet_visuals.py`: swap `qs_driver` → the parametrized
  `<app>_dashboard_driver`; the test body collapses to
  `driver, arg = <fixture>; TreeValidator(<app>_app, driver).validate_structure()`
  (it already calls `driver.open(...)` inside `validate_structure`? — no,
  check: `TreeValidator.validate_structure` does `goto_sheet` per sheet but
  not the initial `open` — the test still needs `driver.open(arg)` first).
