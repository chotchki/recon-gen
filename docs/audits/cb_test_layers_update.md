# Test Layers thoughts
Keep the up_to model, its great!

## What to keep?
- App2 Dashboard and Quicksight Parity at Release
- Database layer is truely pluggable
- Studio is duck/ora/pg
- Dashboards are qs|app2 x ora/pg

## Pain
- AWS Costs
- AWS Database start up / shutdown time
- CI Speed
- CI Stomping on each other

## Help
- We have access to nice beefy box to run stuff on!
- There are Oracle 19c docker images! (See https://hub.docker.com/r/doctorkirk/oracle-19c or even Oracle's official images)

## Open Questions: 
- How much to keep in AWS?
  - Should we keep an Oracle DB?
- When should it be run?
- How can we better define what a test needs? Annotations? (Will this require a Python upgrade?)
  - Right now I think we have huge hand maintained arrays instead of annotations on each test fixture declaring dialects 
- Idea: Could the docker dbs be exposed to AWS from the self hosted runner? 
  - It would nuke a major cost leg of AWS and we control the scaling way more.
  - Have a public ipv4 and can port forward
    - hotchkiss.io is a dns name always pointed at me
    - OR you can do what this does and ask cloudflare: https://github.com/chotchki/hotchkiss-io/blob/main/src/coordinator/ip/cloudflare_trace.rs
  - AWS published their outbound ip range here: 52.23.63.224/27 (I could lock down the port forwarding)
    - range from aws docs here: https://docs.aws.amazon.com/quick/latest/userguide/regions.html#regions-qs
  - Couldn't do it before because of github's runner limitations

## Pytest marks annotations

The marks are the single source of truth for "what tier does this
test belong to and what does it need." The runner discovers tests via
`pytest --collect-only -m "<expr>"` instead of hand-listed file
paths. The runner shrinks, drift between code + runner stops.

### Five marks, all strongly typed

```python
from tests._marks import tier, dialects, l2, needs, writes, all_dialects, all_l2s
from tests._marks import Tier, Dialect, L2, Need

@tier(Tier.APP2)
@dialects(Dialect.PG, Dialect.DU)        # OR @all_dialects()
@l2(L2.SP, L2.SQ)                        # OR @all_l2s()
@needs(Need.DOCKER, Need.PLAYWRIGHT)
@writes()                                # this test mutates DB state — opt-in
def test_studio_plant_apply(...): ...
```

- **`@tier(Tier.X)`** — exactly one of `Tier.UNIT | DB | APP2 | QS_API
  | QS_BROWSER`. Required on every test. Validated at collection time
  (`pytest_collection_modifyitems` raises if a test has zero or
  multiple tier marks). Replaces the hardcoded test-file lists in
  `_layer_command`.
- **`@dialects(*Dialect)`** — zero or more of `Dialect.PG | OR | DU`.
  Empty means "this test doesn't open a DB" (unit-tier helper SQL
  emit tests, JSON byte-shape tests, etc.). The runner fans the test
  out over the cells whose dialect appears here. CB nukes `sl` since
  sqlite is gone. `@all_dialects()` is the convenience for tests that
  apply to every dialect — equivalent to `@dialects(*Dialect)` but
  saves the per-dialect listing churn when a dialect joins or leaves.
- **`@l2(*L2)`** — zero or more of `L2.SP | SQ | FUZZ`. Empty means
  "this test doesn't load an L2 yaml" (pure helper / SQL-emit / JSON
  byte-shape tests that operate on either inline yaml fragments or
  no L2 at all). The runner fans the test out over the matched L2
  forms, producing the `sp_du_lo` / `sq_pg_lo` / `f12345_du_lo`
  variants. `@all_l2s()` is the convenience that fans over SP + SQ +
  FUZZ together. The `l2_instance: L2Instance` fixture parameter
  receives the loaded yaml — tests don't call `load_instance` directly.
  Replaces the runner's hardcoded `expand_full()` L2 list.
  - **`L2.FUZZ`** is a *family*, not a fixed yaml — it fans out N seeds
    (today: `f12345_…`, `f67890_…`). Breadth is runner-controlled via
    a `--fuzz-count=N` CLI flag (default 1 locally, higher in nightly);
    `pytest_addoption` exposes it. Tests that genuinely want
    property-style mass fuzzing (the 1–2 contract-matrix tests at the
    bottom of `tests/data/`) opt out of the runner's count and apply
    their own `@pytest.mark.parametrize("fuzz_seed", range(...))`
    inline. This keeps the common case under runner control without
    bloating the typed mark surface with per-test breadth.
- **`@needs(*Need)`** — zero or more of `Need.DOCKER | PLAYWRIGHT |
  AWS_QS | ORACLEDB_CLIENT`. The runner's `probe_dependencies`
  already knows most of these. Pre-dispatch, the runner checks each
  test's `needs` against probe state; missing deps yield a skip with
  a clear reason rather than a 30-second container-spin-up-then-fail.
- **`@writes()`** — flag (no args) declaring this test mutates DB
  state. The conftest's DB fixture branches on it: `writes + DuckDB`
  → per-worker DB (`:memory:` or file-per-worker, per #199 / #200);
  unmarked + DuckDB → `read_only=True` against the cell's shared
  seeded `.duckdb`. Tests declare their write-ness; fixtures pick
  the matching isolation. Eliminates the "works on SQLite, races on
  DuckDB" class of bug — same principle as
  [[feedback_strict_engines_surface_isolation_bugs]].

### Strong typing — the helpers module

`tests/_marks.py`:

```python
from enum import StrEnum
import pytest

class Tier(StrEnum):
    UNIT = "unit"
    DB = "db"
    APP2 = "app2"
    QS_API = "qs_api"
    QS_BROWSER = "qs_browser"

class Dialect(StrEnum):
    PG = "pg"
    OR = "or"
    DU = "du"

class L2(StrEnum):
    SP = "spec_example"
    SQ = "sasquatch_pr"
    FUZZ = "fuzz"   # parameterized by --fuzz-count=N at runner level

class Need(StrEnum):
    DOCKER = "docker"
    PLAYWRIGHT = "playwright"
    AWS_QS = "aws_qs"
    ORACLEDB_CLIENT = "oracledb_client"

def tier(t: Tier) -> pytest.MarkDecorator:
    return pytest.mark.tier(t.value)

def dialects(*ds: Dialect) -> pytest.MarkDecorator:
    return pytest.mark.dialects(*[d.value for d in ds])

def all_dialects() -> pytest.MarkDecorator:
    """Sugar for `dialects(*Dialect)` — survives Dialect additions /
    removals without per-test churn. Use for genuinely cross-dialect
    tests; reserve explicit listing when a test SHOULD pin to a
    subset (Oracle-only-quirk tests, etc.)."""
    return dialects(*Dialect)

def l2(*xs: L2) -> pytest.MarkDecorator:
    return pytest.mark.l2(*[x.value for x in xs])

def all_l2s() -> pytest.MarkDecorator:
    """Sugar for `l2(*L2)` — fans out over spec_example + sasquatch_pr
    + fuzz together. Use for tests that should run on every shape;
    reserve explicit listing when a test pins to one form (sasquatch-
    specific scenario coverage, fuzz-only contract probe, etc.)."""
    return l2(*L2)

def needs(*ns: Need) -> pytest.MarkDecorator:
    return pytest.mark.needs(*[n.value for n in ns])

def writes() -> pytest.MarkDecorator:
    return pytest.mark.writes
```

Pyright catches `tier("appp2")` (wrong type), `dialects("Pg")` (case
typo), and `l2(L2.SAS)` (wrong member name — typo bait when there are
several short L2 forms) at write time. Pytest's runtime marks stay
strings (can't change that); the author-facing surface is fully typed. Same
trick as the existing `NewType("VariantName", str)` pattern — typed
at the call site, identity at runtime.

### Runner uses `--tier=X --dialect=Y --l2=Z` custom options, not `-m`

Pytest's `-m` filter doesn't natively understand "mark with
argument" — `-m "tier(app2)"` doesn't work; it sees the mark
`tier` regardless of args. Two options:

- (a) Custom `--tier=app2 --dialect=du --l2=sp` command-line options
  via `pytest_addoption` + selection in
  `pytest_collection_modifyitems`. The same hook also reads
  `--fuzz-count=N` to expand `L2.FUZZ` tests into N parameterized
  copies (one per fuzz seed drawn from the deterministic per-commit
  pool, per [[feedback_fuzzer_as_property_testing]]).
- (b) Distinct mark names per value (`@pytest.mark.tier_app2()`) so
  `-m` works directly — but loses strong typing.

Pick (a). The runner becomes:

### Composition rules (validated at collection time)

| Rule | Why |
|---|---|
| `tier(unit)` + `dialects(...)` ⇒ ERROR | Unit tier doesn't open a DB. Tests that emit + assert SQL strings don't carry a dialects mark — they're cross-dialect by construction. |
| `tier(unit)` + `l2(...)` ⇒ WARNING | Unit-tier tests usually operate on inline yaml fragments or no L2 at all; if a unit test actually loads an L2 instance, it's worth a comment justifying why it's not in the DB tier. |
| `tier(qs_*)` without `aws_qs` in `needs` ⇒ ERROR | QS-touching tests must declare the AWS dep so the runner knows to skip when AWS is paused. |
| `tier(qs_browser)` without `playwright` in `needs` ⇒ ERROR | Symmetry; QS embed renders in a browser. |
| `dialects()` empty + tier ≠ unit ⇒ WARNING | Tier above unit usually means a DB is touched; empty dialects is probably an oversight. (PDF + dialect-agnostic e2e tests can suppress with a comment.) |
| `l2()` empty + tier ≠ unit ⇒ WARNING | Same shape as the dialects rule: a DB/app2/qs-tier test almost certainly loads SOME L2 yaml — the empty mark is probably an oversight. |
| `writes()` without an `l2_instance` fixture in the test signature ⇒ ERROR | A test that mutates DB state but doesn't bind the L2-scoped fixture chain can't get proper per-worker isolation — it'd race on the shared seeded DB. |
| No `tier` mark ⇒ ERROR | Source of truth; can't dispatch without it. |

```python
def _layer_command(layer: Tier, cell: VariantSpec, ...) -> list[str]:
    return [
        ".venv/bin/pytest",
        "tests/",  # walks the whole tree; --tier + --dialect + --l2 filter
        f"--tier={layer.value}",
        f"--dialect={cell.dialect}",
        f"--l2={cell.l2}",
        f"--fuzz-count={cell.fuzz_count}",  # 1 local, N nightly
        "-q",
        # rest of the args (xdist, cov, env)
    ]
```

Three-axis dispatch (`--tier=app2 --dialect=du --l2=sq`) is the
same call shape; the cells fan out as the cartesian product of the
matrix. Adding a new test goes into the right tier × dialect × L2
slice automatically just by carrying its marks. The runner doesn't
get touched.

### Conftest wiring

`tests/conftest.py`:

```python
from tests._marks import Tier, Dialect, Need

def pytest_addoption(parser):
    parser.addoption("--tier", action="store", default=None, choices=[t.value for t in Tier])
    parser.addoption("--dialect", action="store", default=None, choices=[d.value for d in Dialect])

def pytest_configure(config):
    config.addinivalue_line("markers", "tier(t: Tier): exactly one tier per test")
    config.addinivalue_line("markers", "dialects(*Dialect): which DBs this test runs against")
    config.addinivalue_line("markers", "needs(*Need): external deps the runner probes")
    config.addinivalue_line("markers", "writes: declares this test mutates DB state")

def pytest_collection_modifyitems(config, items):
    # 1. Validate marks (per tier-progressed `MIGRATED_TIERS` ratchet — see below).
    # 2. Filter by --tier / --dialect / --needs.
    # 3. Auto-skip tests whose `needs` aren't in probe state.
```

Plus a fixture that branches the DB connection on `@writes`:

```python
@pytest.fixture
def db(request, variant_cfg):
    is_writer = request.node.get_closest_marker("writes") is not None
    if variant_cfg.dialect is Dialect.DU:
        if is_writer:
            # Per-worker isolation (#199 / #200) — :memory: or
            # worker_id-scoped file. The test gets a writable DB.
            return open_per_worker_duckdb()
        else:
            return open_shared_readonly_duckdb(variant_cfg.demo_database_url)
    # PG / Oracle: pooled docker-DB connection
    return open_docker_db(variant_cfg)
```

Tests declare their write-ness via `@writes()`; the fixture matches
the isolation strategy. Eliminates the "works on SQLite, races on
DuckDB" class — every leaked write surfaces at the fixture-write
boundary, not as a flaky race.

### Migration story (per-tier rollout)

Don't big-bang. One tier at a time:

1. **CB.1** — define the marks + conftest validation. Run against
   the existing tree, expect "0 tests marked" → no behavior change
   yet. Lint passes because validation is loud only on inconsistency,
   not absence.
2. **CB.2** — mark the unit tier. Smallest, most tests, no dialects
   needed. Runner keeps using hardcoded paths for unit (`tests/unit/`)
   so the dual-source-of-truth temporarily coexists. Validates the
   mark schema against a real population.
3. **CB.3** — mark the app2 tier. Replace `_layer_command`'s app2
   arm with the marks expression. Validate one full chain.
4. **CB.4** — mark the db tier. Same shape as app2.
5. **CB.5** — mark the qs_api + qs_browser tiers. Now `tests/e2e/`
   stops being a tier and starts being a directory of mixed-tier
   files (which marks resolve).
6. **CB.6** — delete the hardcoded file-lists from `_layer_command`;
   the `--tier=X --dialect=Y` flags are the only path.

**Progressive lint ratchet (addresses "scream about tests WITHOUT
marks").** `tests/conftest.py` carries a `MIGRATED_TIERS:
frozenset[Tier]` constant. During CB.2, `{Tier.UNIT}`; CB.3,
`{Tier.UNIT, Tier.APP2}`; etc. Validation rule: any test under
`tests/<tier>/` whose tier is in `MIGRATED_TIERS` MUST carry a
`@tier(...)` mark; missing marks fail collection. Tests in
non-migrated tiers stay unvalidated until their tier ratchets in.
Same shape as the existing `pyright::tool.pyright.include` per-file
ratchet. Lint only tightens — no all-or-nothing big-bang.

### Tricky cases worth thinking through before CB.1

1. **Tests parametrized over dialects** —
   `@pytest.mark.parametrize("dialect", [PG, ORACLE, DUCKDB])`.
   Does the mark apply per-parameter or to the whole test? Proposal:
   the mark is a UNION (the test runs in cells matching ANY listed
   dialect), and the parametrize-id encodes the cell-side dialect
   filter via `-k`. Concretely: a test marked `dialects("pg", "or",
   "du")` running in the `pg` cell uses `-k "pg"` to select only the
   pg parametrize entry. Requires parametrize ids to encode the
   dialect code consistently (`f"{dialect_code}-{...}"`). Acceptable
   discipline.
   - Comment: See my strong types below, I agree with the general approach

2. **The 4-way agreement test** — `test_audit_dashboard_agreement.py`
   runs QS + App2 + PDF + direct-DB. It belongs in `qs_browser` (the
   heaviest leg gates the rest). The other three "agreements" become
   assertions inside that one test rather than separate tier-tagged
   tests.

3. **Tests that need *no* dialect but DO need browser** — the
   bundled-smoke-app browser tests (`test_dashboard_driver.py::test_app2_*`)
   need Playwright but no DB. `tier(app2)` + `dialects()` (empty) +
   `needs("playwright")`. Marks compose cleanly.

4. **Fuzz over dialect-axis** — fuzz tests already use scenario codes
   `f<seed>_<dialect>_<target>`. The dialect is part of the variant
   discriminator; the marks apply per-test, not per-fuzz-cell.
   `dialects("pg", "or", "du")` lets the variant matrix expand it 3
   ways naturally.

5. **Cross-tier discovery during incremental rollout** — during CB.2-5,
   some tests have marks, some don't. The runner's marks expression
   `tier(app2) and dialects(du)` skips unmarked tests cleanly
   (`-m` is exclusive). The hardcoded path lists keep running
   unmarked tests during the transition. No big-bang.

### What this DOESN'T solve

- **The hand-maintained variant matrix** — `expand_full()` still
  defines which cells exist. Marks slot tests into cells; the cell
  enumeration stays where it is. (Strong typing on the cell axes
  per `common/variant.py` is already in place — `DialectCode` Literal
  + `DIALECTS` frozenset.)
- **Test layer DAG dependencies** — the `up_to=db` implies-unit
  chain. The marks don't replace the layer ordering; they just make
  per-layer dispatch parameterized.

## Layer / Cell Model (it is great!)
1. unit (no L2 impact)
2. db (per dialect)
  - 100% local/docker based
  - (duck/pg/ora) 
3. e2e
  - what is REALLY inside this? is it the start of l2 testing?
  - hoping this is still 100% local/docker based
  - (duck/pg/ora)
4. app2 browser
  - 100% local/docker based (duck/pg/ora)
5. qs api
  - aws oracle+postges
6. qs browser
  - aws postgres only



## What runs when in CI
- On push to main
  - up to qs browser (possible since we're not fighting AWS db spins)
- On release
  - up to qs browser



## Decisions
- sqlite is gone, too slow
