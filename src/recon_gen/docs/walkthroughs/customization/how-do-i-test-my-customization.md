# How do I run the test suite against my customized dataset SQL?

*Customization walkthrough — Developer / Product Owner. Testing.*

## The story

You've customized — swapped a dataset's SQL to read from your
warehouse view, added an `originating_branch` metadata key,
extended `rail_name` with `repo`. Each one is a small mutation to a
small surface (one SQL function, one ColumnSpec, one L2
declaration). The shipped suite covers the contract layer: do the
dataset SQL projections still emit the columns the visuals expect?
What it can't cover is YOUR SQL's semantic correctness — whether
your warehouse view returns the right NUMBERS.

This walkthrough is the testing layout: which shipped tests catch
which class of breakage, and where to add your own tests for the
customization-specific concerns the shipped suite can't reach.

## The question

"I changed a dataset's SQL. Which tests are likely to fail now, and
where do I add a test for the change I just made?"

## Where to look

The test suite runs as a layered chain — `./run_tests.sh
up_to=<layer>` walks `unit → db → app2` and
running layer N runs every layer before it. Three reference points
for a customization:

- **`tests/`** — the shipped pytest tree. The fast tiers (`tests/unit`,
  `tests/data`, `tests/json`) run with no live DB; the
  e2e tiers under `tests/e2e/` spin up containers and serve the
  self-hosted dashboards.
- **`tests/json/test_dataset_contract.py`** — the contract test.
  For every dataset, asserts the SQL projection's column shape
  matches the declared `DatasetContract`. This is the test that
  catches contract drift after a SQL swap.
- **`./run_tests.sh up_to=app2`** — the full chain: it spins the
  container, serves the self-hosted dashboards and runs the e2e
  tiers against them. Run it before declaring a customization
  production-ready.

## What you'll see in the demo

The fast tier:

```bash
./run_tests.sh up_to=unit
```

Runs the no-DB tiers (`tests/unit`, `tests/json`,
`tests/cli` and friends) — ~20s on a fresh laptop. The contract
tests alone run in well under a second. Note it does NOT run
`tests/data` (the demo-seed / semantic-lock tier, Classes 2 and 3
below), so a seed edit can pass here while the lock-drift check
never fires.

The full chain:

```bash
./run_tests.sh up_to=app2
```

Spins the container, serves the self-hosted dashboards, then runs
the browser tier against them (it runs under pytest-xdist and
dominates wall time).

For a single test, bare pytest is fine when you're iterating on one
file:

```bash
.venv/bin/pytest tests/json/test_dataset_contract.py -k overdraft -v
```

The `-k` filter matches on test ID. The contract test IDs are the
first column of each contract (e.g. `account_id` for
`OVERDRAFT_CONTRACT`). Use this to narrow to one customization at a
time during iteration. For layered work — anything above one file —
go through `./run_tests.sh`, not bare pytest; the runner sets up the
container + server fixtures the e2e tiers depend on.

## What it means

The shipped tests sort into four classes by the breakage they catch.
Class 1 lives in the fast `unit` tier, which `up_to=unit` runs.
Classes 2 and 3 live in `tests/data` — also no-DB, but the
`up_to=unit` command does NOT run them, so a seed edit needs them
invoked explicitly. The fourth is the e2e end of the chain
(`db → app2`).

### Class 1 — Unit tests (`tests/unit/`)

Fast. No database. Pure-Python assertions about the
generator's output.

- **`tests/json/test_dataset_contract.py`** — the SQL projection
  matches the declared `DatasetContract`. The MOST important test
  for a customization. Fails if your SQL swap forgot a column or
  got the order wrong.
- **`tests/json/test_<app>.py` per app** (`test_executives.py`,
  `test_investigation.py` and the rest) — the generate pipeline
  produces valid analysis + dashboard JSON, plus per-app visual +
  filter wiring assertions. Catches a broken cross-reference (a
  visual referencing a dataset that no longer exists, a filter
  referencing a column that's gone) and "the visual now references a
  column the contract dropped."
- **`tests/unit/test_theme_presets.py`** — theme preset registry
  validity. Add a test here when registering a new preset for your
  bank.

Run this class on every customization commit:

```bash
./run_tests.sh up_to=unit
```

For tight iteration on one file, `.venv/bin/pytest <file> -x` stops
on the first failure — fastest feedback.

### Class 2 — Demo data tests (`tests/data/`)

Asserts the demo seed generator's output. The semantic lock at
`tests/data/_semantic_locks/<instance>.duckdb.json` records the L1
invariant violation set at the canonical anchor (2030-01-01) and
fails on any shift in what the invariants flag — replacing the older
SHA256-of-the-bytes lock. Plus per-scenario coverage assertions
(`TestScenarioCoverage`). `up_to=unit` does NOT run this tier —
invoke it directly with `.venv/bin/pytest tests/data` after a seed
edit.

If you customized the seed generator (added a new `rail_name`
value's seed branch, planted a new exception scenario), the lock
check fails — that's the prompt to re-lock once the shift is
intentional:

```bash
recon-gen data semantic-lock --l2 run/my_institution.yaml
```

`--check` is the verify-only form CI runs (non-zero on drift, with a
diff). See CLAUDE.md "Demo Data Conventions" for the re-lock pattern.

If you customized the L2 instance to add a new `rail_name` value,
the demo seed should also plant ≥1 row of the new type so the e2e
tests have something to render. The `TestScenarioCoverage` pattern
makes this a one-line assertion.

### Class 3 — L2 schema + seed contract tests (`tests/data/test_l2_seed_contract.py`)

Asserts the per-prefix DDL emitted by
`common.l2.schema.emit_schema(l2_instance)` and the seed bytes
emitted by `common.l2.seed.emit_seed(l2_instance, scenario)`.
Catches:

- Schema migrations that don't round-trip (DROP without a matching
  CREATE, missing index).
- Per-prefix view emission that drifts from the L2 instance
  vocabulary.

Customizations that touch `common/l2/schema.py` (a new view, a new
index) are the ones that fail tests here. The fix is usually to
update the matching test expectation alongside the schema change.

### Class 4 — End-to-end (`tests/e2e/`)

The expensive end of the chain. The browser tier (`@pytest.mark.browser`)
is Playwright WebKit headless: it loads the self-hosted dashboard
server, clicks through tabs and asserts visual rendering + filter
interactions against a real container's data. Catches the two
failure modes the contract test CAN'T — "the dataset returns zero
rows for the customer's data" and "the dashboard renders but the
visual layer is broken because of a column the dataset no longer
emits."

Run the full chain once before declaring a customization
production-ready:

```bash
./run_tests.sh up_to=app2
```

To drive a single failing e2e test interactively (spin the
container, serve the dashboards, drop into pdb against the live
fixtures) reach for the triage verb instead of a hand-rolled setup:

```bash
./run_tests.sh triage tests/e2e/test_l1_filters.py::test_check_type_dropdown_exposes_options
```

See it live: https://recon-gen-spec.hotchkiss.io/ — the demos render
from this codebase, so they're a quick eyeball check that a dashboard
shape actually shows the data.

## Drilling in

A few patterns to know once the basic test layout makes sense:

### Add a unit test for a custom dataset's SQL semantics

The shipped contract test asserts column SHAPE, not column
CORRECTNESS. Your custom SQL needs its own correctness test.
Pattern:

```python
# tests/test_my_overdraft_customization.py
import os

import pytest
import psycopg2

from recon_gen.common.config import load_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.apps.l1_dashboard.datasets import build_overdraft_dataset


@pytest.mark.skipif(
    not os.environ.get("RECON_GEN_TEST_DB_URL"),
    reason="set RECON_GEN_TEST_DB_URL to a fixture-loaded warehouse",
)
def test_overdraft_returns_known_overdrawn_account():
    cfg = load_config("config.yaml")
    l2 = load_instance("run/my_institution.yaml")
    ds = build_overdraft_dataset(cfg, l2)
    sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery

    conn = psycopg2.connect(os.environ["RECON_GEN_TEST_DB_URL"])
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()

    # Your bank's known-overdrawn-yesterday account fixture
    assert any(r[0] == "fixture-overdrawn-account-1" for r in rows)
```

The pattern: extract the SQL from the built DataSet, run it against
a test warehouse with known fixture data, assert on specific row
presence. Skip the test when the fixture warehouse isn't available
(CI gates this).

### Add an e2e test for a customization-specific scenario

If your customization adds a new exception check (or extends an
existing one to fire on a new `rail_name`), add an e2e test that
verifies the visual layer surfaces it. e2e tests drive through a
`DashboardDriver` (`tests/e2e/_drivers/`), never raw Playwright —
the `l1_dashboard_driver` fixture yields `(driver, dashboard_arg)`
for the self-hosted renderer:

```python
# tests/e2e/test_repo_exception_check.py
def test_repo_transfers_appear_in_rail_filter(l1_dashboard_driver):
    # After your seed plants 'repo' transfers, the Rail
    # dropdown should advertise the new value as a pickable option.
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg)
    options = driver.filter_options("Rail")
    assert "repo" in options
```

New tests follow the existing patterns — `tests/e2e/test_l1_*.py`
is the canonical reference for the driver verbs and the fixture.

### When to add a test vs trust the contract test

The contract test catches:

- Column added / removed / renamed.
- Column type changed.
- Column order changed.

The contract test does NOT catch:

- The SQL returns wrong numbers (semantic bug).
- The SQL returns zero rows (your warehouse view is empty).
- The SQL has a typo that returns NULL where data should be (e.g.
  `JSON_VALUE(metadata, '$.cardbrand')` vs `'$.card_brand'`).
- Performance regressions (your warehouse view is now 30s instead
  of 200ms).

Add unit tests for the second category (semantic correctness), e2e
tests for the third (visual rendering) and a manual or monitoring
check for the fourth — the dashboard's direct-query latency degrades
visibly under bad SQL, but no automated test catches it before
deploy.

### CI integration

The shipped GitHub Actions workflow (`.github/workflows/ci.yml`)
runs the SAME `./run_tests.sh` chain CI and local both invoke,
including the e2e tiers — there's no "passes locally, fails on CI"
gap. The prod-publish gate lives in `release.yml`. For your fork, a
unit-tier failure (contract drift) should block a PR; an e2e failure
is your call — it may be container flakiness, not a code regression,
but the project's own policy treats a failing chain test as a merge
blocker, not a deferrable.

## Next step

Once you have a test plan in place:

1. **Run the fast tier first.** `./run_tests.sh up_to=unit`. This
   catches the most common customization breakage class (contract
   drift) in seconds.
2. **Add at least one customization-specific test per customization
   commit.** A custom dataset SQL gets a row-count or fixture-row
   assertion. A new `rail_name` gets a `TestScenarioCoverage`
   assertion in the demo data tests. A new metadata key gets a
   `JSON_EXISTS` assertion in the relevant dataset's column
   projection.
3. **Run the full chain before you ship the customization.**
   `./run_tests.sh up_to=app2`. The browser tier is the catch-all
   for "the dashboard renders" — a green run here is the last gate
   before you put the customization in front of users.

## Related walkthroughs

- [How do I swap the SQL behind a dataset?](how-do-i-swap-dataset-sql.md) —
  the contract test (Class 1) is what enforces the
  swap-without-breaking-visuals guarantee.
- [How do I self-host the dashboards?](../../reference/self-host.md) —
  serving the dashboards is part of the e2e (Class 4) loop; the
  browser tier renders the same self-hosted server you'll run for
  real.
- [How do I extend canonical values?](how-do-i-extend-canonical-values.md) —
  paired with this walkthrough's `TestScenarioCoverage`
  recommendation. Adding a new value without a coverage assertion
  means the value lands in production without ever having been
  e2e-tested.
