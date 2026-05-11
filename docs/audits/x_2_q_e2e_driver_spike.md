# X.2.q.0 — Dialect-aware e2e driver layer (spike)

**Status:** spike complete; the protocol shape + both driver
implementations are locked. X.2.q.2–q.5 build on this.

## Problem

Two parallel browser-e2e suites with disjoint helper vocabularies:

- **QS suite** (`tests/e2e/test_l1_*.py` / `test_inv_*.py` /
  `test_exec_*.py`) — drives the embedded QuickSight iframe via
  `data-automation-id` selectors, with the QS quirks baked into every
  test body: cell virtualization (~10 DOM rows regardless of page
  size), racy tab switches, the page-size-bump-for-true-row-count
  trick, the `ParameterDropDownControl` grey-bar click.
- **App 2 suite** (`tests/e2e/test_html2_*.py`) — drives the local
  HTMX/d3 page via its own `#filter-form` / `data-widget` /
  `section[data-visual-kind]` selectors.

Same *intent* — "set a date filter", "read the Drift table", "pick a
status" — two implementations, drifting apart, and nothing forces the
two renderers to agree on a result.

## Solution shape

A `DashboardDriver` protocol whose **verbs are the e2e test
vocabulary** and whose **reads return plain Python (dicts / lists /
strs / bytes), never a Playwright `Locator` / `Page`** — so test
bodies are (almost) pure functions:

```python
driver.open("qs-gen-postgres-sasquatch_pr-l1-dashboard", sheet="Drift")
assert driver.table_rows("Drift Detail") == expected
```

Two implementations — `QsEmbedDriver` (QS quirks sealed inside) and
`App2Driver` (the HTMX page) — selected by a `driver` fixture
`@pytest.mark.parametrize`'d over `[qs, app2]`; renderer-specific
checks `pytest.skip` the irrelevant *param*, not the *verb*. This is
the foundation `X.2.j`'s 4-way agreement gate is built on (the `qs`
and `app2` drivers' `table_rows()`, plus the audit PDF's numbers, must
match), and `X.2.l.4.d` rides on it.

## What landed

- **`tests/e2e/_drivers/base.py`** — `DashboardDriver` Protocol.
  Verbs: `dialect` (`"qs"` / `"app2"`), `open(dashboard, sheet=None)`,
  `goto_sheet(name)`, `visual_titles() -> list[str]`,
  `wait_loaded(visual_title, *, timeout_ms=15_000)`,
  `table_rows(visual_title) -> list[dict[str, str]]` (keyed by header
  text), `kpi_value(visual_title) -> str | None`,
  `pick_filter(label, values)`, `set_date_range(from_, to)`,
  `set_slider(label, lo, hi)`, `clear_filters()`, `cross_link(label)`,
  `screenshot(path=None) -> bytes`, `close()`.
- **`tests/e2e/_drivers/app2.py`** — `App2Driver` (`dialect = "app2"`).
  `App2Driver.smoke()` is a `@contextmanager` classmethod that owns the
  `html2_server` (the bundled smoke app + the deterministic stub
  fetcher — no DB, no AWS) + a `webkit_page`. Implements `open` /
  `goto_sheet`-stub / `visual_titles` / `wait_loaded` / `table_rows` /
  `kpi_value` / `screenshot`; the write verbs raise
  `NotImplementedError("X.2.q.2")`. App 2's DOM is deliberately simple
  — `section[data-visual-kind]` blocks with an `<h2>` title and a
  `.visual-data` swap target, plain `<table class="table-data">` (no
  virtualization) — so every read is a direct DOM query.
- **`tests/e2e/_drivers/qs.py`** — `QsEmbedDriver` (`dialect = "qs"`).
  `QsEmbedDriver.embed(*, aws_account_id, aws_region, user_arn=None)`
  is a `@contextmanager` classmethod that owns a `webkit_page`;
  `open(dashboard_id, sheet=None)` mints a **fresh** embed URL signed
  for the dashboard's region (region match matters — see
  `generate_dashboard_embed_url`) on each call, so the driver is
  re-usable across dashboards within one `with` block. A thin facade
  over `common/browser/helpers.py`'s primitives: `generate_dashboard_embed_url`,
  `webkit_page`, `wait_for_dashboard_loaded`, `click_sheet_tab`,
  `get_visual_titles`, `wait_for_visual_titles_present`,
  `scroll_visual_into_view`, `read_kpi_value`. `_settle_visuals()`
  (best-effort wait for ≥1 titled visual; swallows the timeout — a
  text-only sheet like `Getting Started` legitimately has none) runs
  after `open`/`goto_sheet`. `table_rows` and the write verbs raise
  `NotImplementedError("X.2.q.2")`.
- **`tests/e2e/test_dashboard_driver.py`** — the spike's ported tests:
  - App 2 leg: 3 pure-assertion tests on the smoke `Showcase` sheet
    (table rows incl. pagination + sort defaults; KPI renders a value;
    every renderer's visual is listed) via `App2Driver.smoke()`.
  - QS leg: 2 tests against the deployed L1 dashboard (the `Drift`
    sheet lists visuals + `wait_loaded` runs clean; `open` + a
    full-page screenshot returns valid PNG bytes) via
    `QsEmbedDriver.embed()` — the `qs_driver` fixture `pytest.skip`s if
    `QS_E2E_USER_ARN` is unset.
  - All 5 green: `QS_GEN_E2E=1 QS_E2E_USER_ARN=… QS_GEN_TEST_L2_INSTANCE=tests/l2/sasquatch_pr.yaml QS_GEN_CONFIG=run/config.postgres.yaml .venv/bin/pytest tests/e2e/test_dashboard_driver.py` against `qs-gen-postgres-sasquatch_pr-l1-dashboard` (us-east-1, Aurora `database-2`).

## Decisions locked

- **Location:** `tests/e2e/_drivers/` — it's test infrastructure.
  Promotable to `common/browser/` later if non-test code needs it
  (e.g. a CLI screenshot tool); no reason to live there yet.
- **`table_rows` keys by header text** (`list[dict[str, str]]`), not
  positional. Stable across column reorders; the natural shape for the
  4-way agreement diff.
- **`wait_loaded(visual_title, *, timeout_ms=15_000)`** — per-visual,
  keyword-only timeout with a sane default.
- **"This verb isn't meaningful here" → `NotImplementedError` from the
  driver, not `pytest.skip`.** A skip belongs in the *test* (skip the
  `[qs]` / `[app2]` param); the driver raising makes "you called a verb
  this renderer can't do" a loud bug, not a silent pass.
- **`screenshot` returns PNG bytes** and optionally writes to a path —
  useful both as a failure artifact and for doc/eyeball captures.
- **Factories are `@contextmanager` classmethods** (`App2Driver.smoke()`,
  `QsEmbedDriver.embed()`) that own the browser (and, for App 2, the
  server) lifecycle. `close()` is a no-op — the `with` block tears
  down. A future `App2Driver.against(url)` / `QsEmbedDriver` against a
  pre-built page can be added without touching the protocol.

## Follow-on: X.2.q.2 — App2Driver write verbs (done)

`App2Driver` now implements all the write verbs + `goto_sheet`:

- **`pick_filter` / `set_date_range` / `set_slider`** — set the
  underlying `#filter-form` element's value + dispatch a bubbling
  `change`. That's the same HTMX wire shape the Tom Select / Flatpickr
  / noUiSlider widgets emit when a user drives them; widget-chrome
  fidelity (does the chip render? does the calendar open?) is the
  `tests/js` unit harness's concern, not a driver expressing test
  intent.
- **`clear_filters`** — re-loads the bare sheet URL. App 2's filter
  state lives entirely in the URL query string, so that's the cleanest
  "reset everything" — and it re-inits the widgets fresh, not just the
  underlying controls.
- **`goto_sheet`** — App 2 routing is stateless, so a sheet switch is
  just `open(self._dashboard, sheet=name)`.
- **`cross_link`** — clicks the `<a>` with that text, waits
  `networkidle`, re-derives `_dashboard`/`_sheet` from the landed URL.
- **`_wait_for_refetch`** — every write verb runs its mutation inside
  this: block on the first `/visuals/.../data` response, then
  `networkidle` (the remaining visuals + the synchronous bootstrap.js
  re-hydrate on each swap). So a write verb returns only once the DOM
  reflects the new state.

Plumbing: `filter_specs` now threads through `html2_server` →
`ServedDashboard` so `App2Driver.smoke()` surfaces the smoke app's
`SMOKE_FILTER_SPECS` (the smoke tree has no parameter-control nodes, so
the server's auto-derive yields nothing without this). Tests:
`test_dashboard_driver.py` grew 4 App2 write-verb tests — `pick_filter`
moves the stub KPI 47→74; `clear_filters` restores it; `set_date_range`
+ `set_slider` survive the re-fetch; `goto_sheet` hops sheets. 9 tests
in the file total, all green.

## Follow-on: X.2.q.2 — `QsEmbedDriver.table_rows` (done)

`read_table_rows_dom` in `common/browser/helpers.py` reads a QS Table
visual's DOM-visible window as header-keyed dicts: column headers from
the `[data-automation-id="sn-table-column-N"]` divs (their `.title`
span — the visible header text); body cells from `sn-table-cell-{row}-{col}`;
**zipped by position** — the Nth header (left-to-right) pairs with the
Nth cell (smallest `col` first) in each row. The position-zip matters
because the header and body column-index origins differ — the screenshot
showed "Transfer ID"'s header div is `sn-table-column-2` while its body
cells start at `sn-table-cell-r-0` — so matching on the numeric index
would be off-by-N. `QsEmbedDriver.table_rows` scrolls the visual into
view, then calls it. Verified live against the deployed
`qs-gen-postgres-sasquatch_pr-l1-dashboard` (Info → "Matview Status" →
12 rows keyed `{View Name, Row Count, Latest Date}`, values aligned).

## Carried forward (not done yet)

- **`QsEmbedDriver.table_rows` — full table.** Today it returns the
  DOM-visible window (~10 rows; QS virtualizes). The whole-table path
  (bump page size to 10000 + scroll-accumulate, like `count_table_total_rows`
  does for counts) is what `X.2.j`'s 4-way agreement diff needs. → **X.2.q.3 / X.2.j.**
- **`cross_link` real-app assertion** — it's implemented but only
  smoke-tested against the smoke app (no obvious stable cross-link
  target there); a "click the drill, land on the right sheet, the
  anchor changed" assertion lands with X.2.q.3.
- **Fold in / supersede `tests/e2e/_harness_html2.py`** — the legacy
  `test_html2_*` tests still call `html2_server` directly. → **X.2.q.3.**
- **Parametrized `[qs, app2]` fixture on a *real* app.** The spike's
  smoke-app tests are App2-only (no QS smoke deployment); the QS-leg
  tests are QS-only (the smoke app isn't deployed to QS). The single
  test body × two renderers shape lands when `App2Driver` can serve a
  real app (L1) against a live DB and `QsEmbedDriver` points at the
  deployed counterpart. → **X.2.q.3.**
- **AST lint: no Playwright past the driver layer** — `tests/e2e/**`
  (and any `DashboardDriver` caller) may not `import playwright` /
  reference `Page` / `Locator` / `sync_playwright`; allowlist is
  `_drivers/*.py` + `common/browser/helpers.py` + `common/browser/screenshot.py`
  + `tests/js/**`. Same shape as the `b.15` `boto3.client`-outside-wrappers
  rule. → **X.2.q.5.**
