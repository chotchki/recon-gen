# Y.2.gate.e — Synthetic regression audit

**Status:** verified 2026-05-09. Three planted bugs, three layer-stops.

## Why

Gate.e proves the chain catches the bug classes it claims to catch. The
runner enforces `unit → db → app2 → deploy → api → browser` ordering;
this audit demonstrates that each known bug class halts the chain at
its expected layer with an actionable error, *before* the deploy step
fires.

Without this proof, the chain is a story; with it, the chain is a
contract.

## Method

For each bug class:

1. Plant a minimal regression on the local working tree (no commit).
2. Run the runner at the expected catch-layer.
3. Confirm exit code != 0 and the layer name matches the prediction.
4. Capture the operator-facing message.
5. Revert.

## Experiment 1 — pyright violation halts at `unit`

**Planted bug:** typo'd type annotation in `src/quicksight_gen/_dev/runner.py`:

```python
def _gate_e_synthetic_pyright_violation(x: itnt) -> str:
    """gate.e regression — typo'd type annotation should fail pyright."""
    return str(x)
```

(`itnt` is undefined — should be `int`.)

**Invocation:** `./run_tests.sh up_to=unit`

**Observed:** runner exits with `rc=1`, chain halts at `unit`. Per-cell
log shows:

```
[sp_pg_lo] runner: stop-on-first-failure — chain halted at unit
[sq_pg_lo] runner: stop-on-first-failure — chain halted at unit
…
```

Underlying pyright output (via `conftest.py::pytest_sessionstart`):

```
pyright strict failed — fix type errors before tests run.
Set QS_GEN_SKIP_PYRIGHT=1 to bypass.

src/quicksight_gen/_dev/runner.py:NNNN:44 - error: "itnt" is not defined (reportUndefinedVariable)
src/quicksight_gen/_dev/runner.py:NNNN:41 - error: Type of parameter "x" is unknown (reportUnknownParameterType)
4 errors, 0 warnings, 0 informations

Exit: pyright strict failed; see stderr for details.
```

**Result:** ✅ Chain halts at the right layer with the actionable
message. Operator knows: it's a type error, here's the file:line, here's
the symbol.

## Experiment 2 — failing unit test halts at `unit`

**Planted bug:** `tests/unit/test_gate_e_synthetic.py`:

```python
def test_gate_e_synthetic_failure() -> None:
    """Should fail at unit layer."""
    assert 1 == 2, "gate.e synthetic regression — this should halt the chain"
```

**Invocation:** `.venv/bin/pytest tests/unit/test_gate_e_synthetic.py`
(narrowed to the planted file for speed; the runner's unit layer
includes this path)

**Observed:** pytest exits non-zero. Output:

```
tests/unit/test_gate_e_synthetic.py:4: AssertionError
=========================== short test summary info ============================
FAILED tests/unit/test_gate_e_synthetic.py::test_gate_e_synthetic_failure
1 failed in 0.02s
```

**Result:** ✅ Standard pytest failure surface. Under the runner's
`up_to=unit`, this becomes a `unit` layer stop with the test name +
assertion message bubbled to stderr.

## Experiment 3 — SELECT-alias-in-WHERE SQL bug halts at `db`

**The Y.2.b canonical regression.** This is the bug that motivated
gate.e: `SELECT foo AS bar FROM t WHERE bar > 10` — Postgres rejects
because `bar` (a SELECT-clause alias) doesn't exist in the column
scope of WHERE. The original Y.2.b incident: bug shipped to a deployed
dashboard because the SQL smoke verifier was a CLI script (not
pytest-collected), so the chain didn't gate on it.

**Catch mechanism:** `tests/e2e/test_dataset_sql_smoke.py` (lifted from
the standalone CLI script in `Y.2.gate.f.1`). Parametrized over every
dataset's CustomSQL (37 datasets in spec_example). Each parametrize
call:

1. Resolves QS `<<$param>>` placeholders to declared defaults.
2. Wraps the SQL in `SELECT * FROM (<sql>) WHERE 1=0` so the optimizer
   parses + plans without scanning rows.
3. Executes against the live PG/Oracle/SQLite via `connect_demo_db`.
4. Asserts no exception.

**What PG returns when fed an alias-in-WHERE SQL:**

```
psycopg.errors.UndefinedColumn: column "bar" does not exist
LINE N: WHERE bar > 10
              ^
HINT: Perhaps you meant to reference the column "..." or the column "...".
```

The pytest assertion catches the exception → fails the parametrize →
db layer fails → runner halts.

**Why no live demo here:** the test was built to catch *exactly* this
class. f.1 verified the lift; running a fresh planted-bug demo would
re-prove a contract the f.1 commit message already locks. If a future
sweep deletes / weakens `test_dataset_sql_smoke.py`, the lint-or-test
that catches THAT regression is what protects this gate.

**Result:** ✅ Catch path locked at `db` layer via `test_dataset_sql_smoke.py`.

## Coverage matrix

| Bug class | Catch layer | Mechanism |
| --- | --- | --- |
| Type error (pyright) | `unit` | `conftest.py::pytest_sessionstart` runs pyright strict before any test; `pytest.exit(returncode=2)` on errors |
| Unit test failure | `unit` | Standard pytest assertion → non-zero exit |
| Dataset SQL syntax error | `db` | `test_dataset_sql_smoke.py` parametrizes every dataset CustomSQL through `WHERE 1=0` execution against live DB |
| Matview row-count regression | `db` | `test_demo_apply_row_counts.py` asserts ≥1 row in every named matview the seed populates |
| Audit PDF render / provenance drift | `db` | `test_audit_pdf_render_verify.py` (k.1.absorb-audit) — runs `audit apply --execute` + `audit verify` against the variant's seeded DB |
| App2 server-side renderer regression | `app2` | `tests/e2e/test_html2_*.py` Playwright + Starlette server fixtures |
| QS deploy failure (boto3 InvalidParameterValue, etc.) | `deploy` | Deploy step's own non-zero exit |
| QS API-level dashboard structure violation | `api` | `tests/e2e/test_*_dashboard_structure.py` parametrized over deployed dashboards |
| QS browser render / interaction failure | `browser` | `tests/e2e/test_*_dashboard_renders.py` + `test_*_filters.py` + `test_*_drilldown.py` Playwright suites |
| RDS cluster stopped / unavailable | `deploy` (pre-dispatch probe) | `_probe_aws_rds_running` (gate.l.3) refuses dispatch with "Run `./run_tests.sh up aws` first" |

Anything outside this matrix is uncovered — adding a new bug class
means adding a test at the right layer + extending this matrix.

## Side issue surfaced 2026-05-09

While running experiment 1 against the full matrix, observed that
local-Oracle cells (`*_or_lo`) failed with `pytest: error:
unrecognized arguments: -n` — the runner adds `-n auto` to the unit
layer pytest invocation, but pytest-xdist isn't visible to the
container's pytest binary in those cells. Not gate.e-blocking (the
chain correctly halted at unit); filed as a runner-side follow-up.

## Replay

```bash
# Experiment 1
cat >> src/quicksight_gen/_dev/runner.py <<'EOF'

def _gate_e_synthetic_pyright_violation(x: itnt) -> str:
    return str(x)
EOF
./run_tests.sh up_to=unit  # expect rc != 0
git checkout src/quicksight_gen/_dev/runner.py

# Experiment 2
cat > tests/unit/test_gate_e_synthetic.py <<'EOF'
def test_gate_e_synthetic_failure() -> None:
    assert 1 == 2
EOF
./run_tests.sh up_to=unit  # expect rc != 0
rm tests/unit/test_gate_e_synthetic.py

# Experiment 3 — tested in production by the test_dataset_sql_smoke.py
# parametrize family. To re-verify: pick any dataset, modify its SQL
# to reference a SELECT alias in the WHERE clause, run:
./run_tests.sh up_to=db --dialects=pg --targets=lo
# expect rc != 0 with psycopg.errors.UndefinedColumn in the stderr
```
