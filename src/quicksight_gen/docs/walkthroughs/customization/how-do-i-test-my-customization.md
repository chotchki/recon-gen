# How do I run the test suite against my customized dataset SQL?

*Customization walkthrough — Developer / Product Owner. Testing.*

## The story

You've customized — swapped a dataset's SQL to read from your
warehouse view, added an `originating_branch` metadata key,
extended `rail_name` with `repo`. Each customization is a
small mutation to a small surface (one SQL function, one
ColumnSpec, one L2 declaration). The shipped test suite covers
the contract layer: do the dataset SQL projections still emit
the columns the visuals expect? But it doesn't (and can't) cover
*your* SQL's semantic correctness — whether your warehouse view
returns the right *numbers*.

This walkthrough covers the testing layout: which shipped tests
catch which classes of breakage, and where to add your own
tests for customization-specific concerns.

## The question

"I changed a dataset's SQL. Which tests are likely to fail
now, and where do I add a test for the change I just made?"

## Where to look

Three reference points:

- **`tests/`** — the shipped pytest suite. Two layers: unit /
  integration (fast, no AWS) and e2e (gated on `QS_GEN_E2E=1`,
  hits a real AWS account).
- **`tests/test_dataset_contract.py`** — the contract test.
  For every dataset, asserts the SQL projection's column
  shape matches the declared `DatasetContract`. This is the
  test that catches contract drift after a SQL swap.
- **`run_e2e.sh`** — the one-shot runner that regenerates JSON,
  re-deploys to AWS, and runs the full e2e suite. Use this
  before declaring a customization production-ready.

## What you'll see in the demo

The default test invocation:

```bash
.venv/bin/pytest
```

Runs every test under `tests/` *except* `tests/e2e/` (those
are gated). Total wall time on a fresh laptop: ~5-10 seconds
for ~200 tests. The contract tests alone run in well under a
second.

The e2e invocation:

```bash
./run_e2e.sh
```

Regenerates JSON, deploys to the AWS account in
`run/config.yaml`, then runs the e2e suite. Total wall time:
~10-15 minutes (the deploy alone is ~5 minutes; the browser
tests run with pytest-xdist at -n 4 by default).

For a single test:

```bash
.venv/bin/pytest tests/test_dataset_contract.py -k overdraft -v
```

The `-k` filter matches on test ID. The contract test IDs are
the first column of each contract (e.g., `account_id` for
`OVERDRAFT_CONTRACT`). Use this to narrow to one customization
at a time during iteration.

## What it means

The shipped tests divide into four layers, each catching a
different class of customization breakage:

### Layer 1 — Unit tests (`tests/test_*.py`)

Fast. No AWS. No database. Pure-Python assertions about the
generator's output.

- **`test_dataset_contract.py`** — the SQL projection
  matches the declared `DatasetContract`. **The single most
  important test for customization.** Fails if your SQL
  swap forgot a column or got the order wrong.
- **`test_generate.py`** — the full generate pipeline
  produces valid analysis + dashboard JSON. Catches
  cross-references that broke (a visual referencing a
  dataset that no longer exists, a filter referencing a
  column that's gone).
- **`test_<app>.py` per app** (`test_executives.py`,
  `test_investigation.py`, etc.) — per-app visual + filter
  wiring assertions. Catches "the visual now references a
  column the contract dropped."
- **`test_theme_presets.py`** — theme preset registry
  validity. Add a test here when registering a new preset
  for your bank.

Run this layer on every customization commit:

```bash
.venv/bin/pytest tests/ --ignore=tests/e2e -x
```

`-x` stops on first failure — fastest feedback when iterating.

### Layer 2 — Demo data tests (`tests/test_demo_data.py`)

Asserts the demo seed generator's output. Contains the
SHA256 hash lock that catches *any* byte-level shift in seed
output, plus per-scenario coverage assertions
(`TestScenarioCoverage`).

If you customized the demo generator (added a new
`rail_name` value's seed branch, planted a new exception
scenario), the hash test fails — that's the prompt to re-lock
the hash by pasting the new value into the assertion. See
CLAUDE.md "Demo Data Conventions" for the re-lock pattern.

If you customized the L2 instance to add a new
`rail_name` value, the demo seed should also be updated
to plant ≥1 row of the new type, so the e2e tests have
something to render. The `TestScenarioCoverage` pattern
makes this a one-line assertion.

### Layer 3 — L2 schema + seed contract tests (`tests/test_l2_seed_contract.py`)

Asserts the per-prefix DDL emitted by `common.l2.schema.emit_schema(l2_instance)`
and the seed bytes emitted by `common.l2.seed.emit_seed(l2_instance, scenario)`.
Catches:

- Schema migrations that don't round-trip (DROP without
  matching CREATE, missing index).
- Per-prefix view emission that drifts from the L2 instance
  vocabulary.

Customizations that touch `common/l2/schema.py` (a new view, a
new index) are most likely to fail tests here. The fix is
usually to update the matching test expectation alongside the
schema change.

### Layer 4 — End-to-end (`tests/e2e/*`, gated on `QS_GEN_E2E=1`)

The expensive layer. Two sub-layers:

- **API tests (`@pytest.mark.api`)** — boto3 calls against the
  deployed AWS resources. Asserts dataset row counts,
  dashboard structure, sheet inventory, drill-action wiring.
  Catches "the dataset deployed but returns zero rows for
  the customer's data" — the failure mode the contract test
  *can't* catch.
- **Browser tests (`@pytest.mark.browser`)** — Playwright
  WebKit headless. Loads the deployed dashboard, clicks
  through tabs, asserts visual rendering and filter
  interactions. Catches "the dashboard deployed but the
  visual layer is broken because of a column the dataset no
  longer emits."

Run the full e2e once before declaring a customization
production-ready:

```bash
./run_e2e.sh
```

Or skip the deploy if you're iterating on tests against an
existing deployment:

```bash
./run_e2e.sh --skip-deploy
```

## Drilling in

A few patterns to know once the basic test layout makes sense:

### Add a unit test for a custom dataset's SQL semantics

The shipped contract test asserts column *shape*, not column
*correctness*. Your custom SQL needs its own correctness
test. Pattern:

```python
# tests/test_my_overdraft_customization.py
import pytest
import psycopg2
from quicksight_gen.common.config import load_config
from quicksight_gen.apps.l1_dashboard.datasets import build_overdraft_dataset


@pytest.mark.skipif(
    not os.environ.get("QS_GEN_TEST_DB_URL"),
    reason="set QS_GEN_TEST_DB_URL to a fixture-loaded warehouse",
)
def test_overdraft_returns_known_overdrawn_account():
    cfg = load_config("config.yaml")
    ds = build_overdraft_dataset(cfg)
    sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery

    conn = psycopg2.connect(os.environ["QS_GEN_TEST_DB_URL"])
    rows = conn.cursor().execute(sql).fetchall()

    # Your bank's known-overdrawn-yesterday account fixture
    assert any(r[0] == "fixture-overdrawn-account-1" for r in rows)
```

The pattern: extract the SQL from the built DataSet, run it
against a test warehouse with known fixture data, assert on
specific row presence. Skip the test when the fixture
warehouse isn't available (CI gates this).

### Add an e2e test for a customization-specific scenario

If your customization adds a new exception check (or extends
an existing one to fire on a new `rail_name`), add an e2e
test that verifies the visual layer surfaces it. Pattern:

```python
# tests/e2e/test_repo_exception_check.py
import pytest


@pytest.mark.api
def test_repo_transfers_appear_in_transfer_type_filter(qs_client, dashboard_id):
    # After your seed includes 'repo' transfers, the Transfer Type
    # filter dropdown should auto-populate the new value.
    distinct_types = qs_client.get_dashboard_filter_values(
        dashboard_id, filter_id="filter-l1-transfer-type",
    )
    assert "repo" in distinct_types
```

The e2e suite runs against the deployed dashboard. New tests
follow the existing test patterns
(`tests/e2e/test_l1_*.py` is the canonical reference).

### When to add a test vs trust the contract test

The contract test catches:

- Column added / removed / renamed.
- Column type changed.
- Column order changed.

The contract test does *not* catch:

- The SQL returns wrong numbers (semantic bug).
- The SQL returns zero rows (your warehouse view is empty).
- The SQL has a typo that returns NULL where data should be
  (e.g., `JSON_VALUE(metadata, '$.cardbrand')` vs
  `'$.card_brand'`).
- Performance regressions (your warehouse view is now 30s
  instead of 200ms).

Add unit tests for the second category (semantic correctness),
add e2e tests for the third (visual rendering), and add a
manual / monitoring check for the fourth (the dashboard's
direct-query latency degrades visibly under bad SQL — but
no automated test catches it before deploy).

### CI integration

The shipped GitHub Actions workflow (`.github/workflows/ci.yml`)
runs the unit + integration layers (Layer 1-3) on every push.
It does *not* run e2e — that requires AWS credentials and a
real account. For your fork:

- Layer 1-3 in CI on every push (free, fast feedback).
- Layer 4 in a separate workflow, manually triggered or on a
  schedule, against a sandbox AWS account.

Keep the test gating clear: a unit-test failure should block a
PR; an e2e failure on a sandbox account should warn but not
block (the failure may be infrastructure flakiness, not a
code regression).

## Next step

Once you have a test plan in place:

1. **Run the shipped suite first.**
   `.venv/bin/pytest tests/ --ignore=tests/e2e -x`. This catches
   the most common customization breakage class (contract
   drift) in seconds.
2. **Add at least one customization-specific test per
   customization commit.** A custom dataset SQL gets a row-count
   or fixture-row assertion. A new `rail_name` gets a
   `TestScenarioCoverage` assertion in the demo data tests.
   A new metadata key gets a `JSON_EXISTS` assertion in the
   relevant dataset's column projection.
3. **Run e2e before the first production deploy.** `./run_e2e.sh`
   against a sandbox or staging AWS account. The browser tests
   are the catch-all for "the dashboard renders" — a green run
   here is the last gate before a production deploy.

## Related walkthroughs

- [How do I swap the SQL behind a dataset?](how-do-i-swap-dataset-sql.md) —
  the contract test (Layer 1) is what enforces the
  swap-without-breaking-visuals guarantee.
- [How do I run my first deploy?](how-do-i-run-my-first-deploy.md) —
  the deploy is part of the e2e (Layer 4) loop. Re-running
  `json apply --execute` between iterations also re-runs the
  contract test implicitly via the build.
- [How do I extend canonical values?](how-do-i-extend-canonical-values.md) —
  paired with this walkthrough's `TestScenarioCoverage`
  recommendation. Adding a new value without a coverage
  assertion means the value lands in production without ever
  having been e2e-tested.
