"""Top-level conftest — Y.2.gate.c.2 timings capture hook.

When invoked under the test layer chain runner, ``RECON_GEN_RUN_DIR`` and
``RECON_GEN_LAYER`` are set in the env (see ``runner.py::_layer_command``);
``pytest_runtest_makereport`` writes one JSONL line per test ``call`` phase
into ``$RECON_GEN_RUN_DIR/timings/<layer>.jsonl``.

When invoked directly (``pytest tests/...`` without the runner), both env
vars are unset and the hook is a no-op — direct invocation behavior is
unchanged.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Callable, Final, Generator

import pytest

from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_GEN_DEMO_DATABASE_URL_OR,
    RECON_GEN_DEMO_DATABASE_URL_PG,
    RECON_GEN_FUZZ_SEED,
    RECON_GEN_LAYER,
    RECON_GEN_RUN_DIR,
    RECON_GEN_DB_CONN_LEAK_GATE,
    RECON_GEN_TEST_L2_INSTANCE,
    dump_env_access,
)


def pytest_configure(config: Any) -> None:
    """Pin a session-stable fuzz seed + redirect runner.RUNS_DIR to a
    session tmp dir so tests don't pollute the real ``runs/``.

    **Fuzz seed pin (j.6.fix).** Without this, modules that materialize
    a fuzz seed at import time (e.g.,
    ``tests/data/test_l2_seed_contract.py::FUZZ_SEED``) compute a
    fresh ``secrets.randbits(32)`` PER WORKER PROCESS — each worker
    then collects ``[fuzz-seed-NNNNN]`` parametrize IDs with a
    different N, and pytest-xdist refuses to start with "Different
    tests were collected between gw0 and gwN". Fix: controller sets
    ``RECON_GEN_FUZZ_SEED`` once at session start; xdist passes env vars
    from controller to worker subprocesses via execnet, so workers
    inherit the same seed. Operator-pinned seeds
    (``RECON_GEN_FUZZ_SEED=12345 pytest ...``) flow through unchanged.

    **runs/ isolation (#741).** Tests that call
    ``runner.main(["up_to=..."])`` (e.g. ``test_cmd_up_to_*``)
    create real run dirs under the operator's ``runs/`` and call
    ``prune_old_runs``. Under matrix parallel fan-out
    (13 cells × ~16 xdist workers = ~200 invocations) this generated
    200+ transient run dirs and 200+ concurrent prune races; in-flight
    cells' ``_synth_l2.yaml`` files got nuked by sibling pruners.
    Fix: monkeypatch the runner's ``RUNS_DIR`` module attr to a
    session-tmp dir. All in-process ``runner.main`` calls land their
    fake runs in tmp; the operator's real ``runs/`` stays clean; prune
    races vanish (all workers prune their own session-tmp tree).
    Tests that explicitly override RUNS_DIR per-test (with
    ``monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)``) still win
    by pytest fixture-scope precedence.
    """
    if RECON_GEN_FUZZ_SEED.get_or_none() is None:
        os.environ[RECON_GEN_FUZZ_SEED.name] = str(secrets.randbits(32))

    # CB.7-followup (2026-06-02) — the historic GLOBAL loadgroup
    # auto-bump was deleted. Its rationale (pin a shared-prefix writer
    # fixture's tests to one worker so module-scope seeds didn't race)
    # was the exact thing CB.7-followup unwound when it dropped
    # cross-tier shared prefixes. Each test now self-isolates via a
    # per-(file, worker) hash suffix, so scattered module-scope fixtures
    # reseed their own private prefix — no contention, no DDL collisions.
    # Keeping the bump as a global caused the qs_browser cascade: with
    # full e2e collection + `-m browser` + loadgroup, worker session-start
    # dies in ~5s on every worker (xdist 3.8 loadgroup interacts badly
    # with marker-deselected items that carry xdist_group).
    #
    # BV.3.3 fix (2026-06-10) — loadgroup is now applied SURGICALLY at
    # the app2 layer only, via
    # ``src/recon_gen/_dev/runner.py::_layer_command`` (the app2 branch
    # appends ``--dist=loadgroup`` to the pytest command). App2 uses
    # directory discovery with no ``-m`` filter so the marker-deselection
    # hazard doesn't apply, and the BV.3.3 trainer dogfood module's
    # ``xdist_group("trainer-<dialect>")`` pin actually funnels grouped
    # tests onto one worker. qs_browser stays at default ``--dist=load``.

    # #741 — redirect runner.RUNS_DIR so in-process runner.main calls
    # land in session tmp instead of the operator's real runs/. Lazy
    # import to avoid circular-import surprises at conftest load time.
    #
    # The _dev package is excluded from the customer wheel
    # (pyproject.toml::tool.setuptools.packages.find::exclude). When
    # this conftest runs against the installed wheel (release.yml's
    # `Smoke test wheel` job), _dev is absent — and that's fine: no
    # test reachable from the wheel can call runner.main, so there's
    # no runs/ pollution to guard against. Swallow the ImportError.
    try:
        from recon_gen._dev import runner  # noqa: PLC0415 — lazy: only patch when tests are actually running
    except ImportError:
        return
    session_runs_tmp = Path(tempfile.mkdtemp(prefix="qs-gen-test-runs-"))  # typing-smell: ignore[qs-gen-prefix]: tempdir disambiguator, not an AWS resource ID
    runner.RUNS_DIR = session_runs_tmp  # type: ignore[misc]: patching module-level Final at session start; the Final mark documents intent for prod, tests legitimately rebind

    # CB.0 — register marker names so `PytestUnknownMarkWarning` doesn't
    # fire on the new typed marks. `_CB_MARK_DOCS` is defined at module
    # scope alongside `pytest_collection_modifyitems` further down.
    for _mark_name, _mark_doc in _CB_MARK_DOCS.items():
        config.addinivalue_line("markers", f"{_mark_name}: {_mark_doc}")

    # CB.17.e — derive AWS_PROFILE + RECON_GEN_TEST_L2_INSTANCE from the
    # operator cfg so bare `pytest` (no runner wrapper) gets the same env
    # the runner used to inject. Pre-existing env wins (operator overrides
    # cfg). Failure is silent — tests that need these will skip or fail
    # loudly with their own actionable messages.
    _derive_env_from_cfg()


_DEFAULT_CFG_CANDIDATES: tuple[Path, ...] = (
    Path("config.yaml"),
    Path("run/config.yaml"),
    Path("run/config.postgres.yaml"),
    Path("run/config.oracle.yaml"),
)


def _derive_env_from_cfg() -> None:
    """Cfg → env injection at session start, runner-free.

    Promotes three values from the operator cfg into process env:
    - ``RECON_GEN_CONFIG`` — the resolved cfg path (when not pre-set)
    - ``AWS_PROFILE`` — from ``cfg.auth.aws_profile``
    - ``RECON_GEN_TEST_L2_INSTANCE`` — from ``cfg.default_l2_instance``

    Pre-existing env wins (operator overrides cfg). Mirrors the runner's
    ``cmd_up_to`` env-derivation block — the runner still calls
    ``main`` which calls ``pytest`` which lands here, so this hook
    benefits both paths.
    """
    from recon_gen.common.env_keys import RECON_GEN_CONFIG  # noqa: PLC0415

    explicit = None
    try:
        explicit = RECON_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit = None

    cfg_path: Path | None = None
    if explicit is not None:
        cfg_path = Path(explicit)
    else:
        for candidate in _DEFAULT_CFG_CANDIDATES:
            if candidate.exists():
                cfg_path = candidate
                os.environ[RECON_GEN_CONFIG.name] = str(candidate)
                break

    if cfg_path is None or not cfg_path.exists():
        return

    try:
        from recon_gen.common.config import load_config  # noqa: PLC0415
        peek_cfg = load_config(str(cfg_path))
    except Exception:  # noqa: BLE001 — cfg peek is best-effort
        return

    aws_profile = getattr(getattr(peek_cfg, "auth", None), "aws_profile", None)
    if aws_profile and "AWS_PROFILE" not in os.environ:
        os.environ["AWS_PROFILE"] = aws_profile

    l2_default = getattr(peek_cfg, "default_l2_instance", None)
    if l2_default and RECON_GEN_TEST_L2_INSTANCE.get_or_none() is None:
        l2_path = (
            Path(l2_default)
            if Path(l2_default).is_absolute()
            else Path.cwd() / l2_default
        )
        if l2_path.exists():
            os.environ[RECON_GEN_TEST_L2_INSTANCE.name] = str(l2_path)


# CB.17.d — strangler-pattern env access aggregation.
#
# Every pytest sub-process writes its EnvVar access log to a unique
# JSON file in the directory named by ``RECON_GEN_ENV_LOG_DIR`` (when
# that env is set). The runner sets the env per subprocess and
# aggregates after all complete. Filename includes PID + a random
# suffix so concurrent xdist workers don't collide.
#
# When the env is unset, the hook is a no-op (so bare ``pytest`` runs
# don't drop debris everywhere).

def pytest_sessionfinish(session: Any, exitstatus: int) -> None:  # typing-smell: ignore[explicit-any]: pytest.Session is late-imported (test conftest avoids src/ pulls at module scope) — same pattern as `pytest_runtest_setup` above
    """Two end-of-session jobs:

    1. Write this pytest process's EnvVar access log to disk if the
       runner set ``RECON_GEN_ENV_LOG_DIR`` (per-subprocess opt-in).
    2. DG.1 — report any per-(module, worker) ``isolated_cfg``
       teardown failures collected during the run. If non-empty:
       print a clear summary AND raise ``session.exitstatus`` to a
       non-zero code so the run fails. Per operator lock 2026-06-13
       (DG.0): "still needs to be a failure so it doesn't get
       ignored and blow up the next run."
    """
    # 1. EnvVar access log (pre-DG.1 behavior, unchanged).
    from recon_gen.common.env_keys import RECON_GEN_ENV_LOG_DIR  # noqa: PLC0415 — lazy
    log_dir_raw = RECON_GEN_ENV_LOG_DIR.get_or_none()
    if log_dir_raw:
        log_dir = Path(log_dir_raw)
        log_dir.mkdir(parents=True, exist_ok=True)
        events = dump_env_access()
        summary: dict[str, dict[str, int]] = {}
        for name, op in events:
            bucket = summary.setdefault(
                name, {"read_hit": 0, "read_miss": 0, "write": 0},
            )
            bucket[op] = bucket.get(op, 0) + 1
        # PID + 12-char random suffix → unique per worker even under
        # heavy xdist parallelism + identical PID reuse across runs.
        fname = f"pytest-{os.getpid()}-{secrets.token_hex(6)}.json"
        payload = {"by_name": summary, "events": events, "pid": os.getpid()}
        (log_dir / fname).write_text(json.dumps(payload, indent=2, sort_keys=True))

    # 2. DG.1 — isolated_cfg teardown failure report.
    try:
        from tests.e2e._isolation import teardown_failures  # noqa: PLC0415 — lazy: tests/conftest.py runs before tests/e2e collection
    except ImportError:
        # Bare-pytest invocation that doesn't load tests/e2e/ at all.
        return
    failures = teardown_failures()
    if not failures:
        return
    # Print a clearly-marked section so operators can grep it out of
    # the failure log even when N other test failures are above it.
    print("\n\n========== DG.1 — isolated_cfg teardown failures ==========")
    print(
        f"{len(failures)} per-(module, worker) schema-drop failure(s) "
        "during this run. Without DG.2's boot sweep these would "
        "accumulate across CI runs until /dev/shm exhausts — see "
        "docs/audits/dg_0_db_hygiene_audit.md."
    )
    for i, f in enumerate(failures, 1):
        print(f"\n  [{i}/{len(failures)}] suffix={f.suffix!r} dialect={f.dialect} prefix={f.db_table_prefix!r}")
        print(f"    exc: {f.exc_repr}")
        # Indent the traceback so it's visually nested under the failure header.
        for line in f.traceback.splitlines():
            print(f"      {line}")
    print("\n========== end DG.1 teardown failures ==========\n")
    # Raise the run's exit code to a non-zero value if pytest itself
    # ran clean. If pytest already failed, leave its code in place.
    if exitstatus == 0:
        session.exitstatus = 1


# ---------------------------------------------------------------------------
# DB connection-leak detector (opt-in via RECON_GEN_DB_CONN_LEAK_GATE=1)
# ---------------------------------------------------------------------------
#
# Surfaced 2026-05-27 (originally named sqlite-leak-gate when aiosqlite was
# still in the dependency graph). `with duckdb.connect(...)` commits the
# transaction but DOES NOT close the connection — a common foot-gun that
# accumulates live Connection objects until OOM. Explains the local
# browser-tier OOM during the 13-variant sweep.
#
# This fixture snapshots the live DuckDBPyConnection count before each
# test + asserts no net growth after. Defaults OFF because (a) some
# legitimately-session-scoped DB fixtures hold connections across tests,
# (b) third-party libs may also leak; user opts in per branch / per
# release-gate run when the leak surface needs sweeping.
#
# Usage:  `RECON_GEN_DB_CONN_LEAK_GATE=1 pytest tests/...`


def _count_live_db_connections() -> int:
    """Sweep ``gc.get_objects()`` for live DuckDB Connections.

    Forces a ``gc.collect()`` first so legitimately-out-of-scope
    connections are reaped before the count.
    """
    import duckdb as _duckdb  # noqa: PLC0415
    import gc as _gc  # noqa: PLC0415

    # Count only OPEN DuckDB connections — a closed Connection object
    # can linger in pytest's traceback / fixture-result caches even
    # after the test's own `conn.close()` ran, which would false-
    # positive the gate. We probe each candidate by calling
    # `execute("SELECT 1")` and only count it if it doesn't raise.
    for _ in range(3):
        _gc.collect()
    live = 0
    for o in _gc.get_objects():
        if isinstance(o, _duckdb.DuckDBPyConnection):
            try:
                o.execute("SELECT 1")
                live += 1
            except Exception:  # noqa: BLE001 — DuckDB raises different exception classes for closed conns across versions; any failure means "closed, don't count"
                pass
    return live


_DB_CONN_LEAK_BASELINE: dict[str, int] = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: Any) -> Generator[None, None, None]:  # typing-smell: ignore[explicit-any]: pytest Item from late import
    """Stash the pre-setup DB-conn count when the leak gate is enabled.

    Pair with ``pytest_runtest_teardown`` (below) which compares after
    ALL fixture finalizers have run — fixes the autouse-fixture timing
    bug where the gate fires before per-test fixtures close their conns.
    """
    if RECON_GEN_DB_CONN_LEAK_GATE.get_or_none():
        _DB_CONN_LEAK_BASELINE[item.nodeid] = _count_live_db_connections()
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: Any) -> Generator[None, None, None]:  # typing-smell: ignore[explicit-any]: pytest Item from late import
    """Fail if the test left more DB conns than it found (gate opt-in).

    `with duckdb.connect(...)` commits the transaction but DOES NOT
    close the connection — a foot-gun that accumulates live Connection
    objects until OOM.

    Opt in via ``RECON_GEN_DB_CONN_LEAK_GATE=1`` — default OFF because
    legitimate session-scoped DB fixtures hold connections across tests
    and would false-positive without explicit baseline-shift tracking.
    """
    yield  # let all other teardown hooks + finalizers run first
    if not RECON_GEN_DB_CONN_LEAK_GATE.get_or_none():
        return
    before = _DB_CONN_LEAK_BASELINE.pop(item.nodeid, None)
    if before is None:
        return
    after = _count_live_db_connections()
    leaked = after - before
    if leaked > 0:
        raise AssertionError(
            f"db-conn-leak-gate: test {item.nodeid!r} leaked {leaked} "
            f"Connection instance(s) (before={before} → after={after}). "
            f"Likely culprit: `with duckdb.connect(...) as c:` commits "
            f"the transaction but DOES NOT close. Use the pool from "
            f"common/db.py or close connections explicitly in a "
            f"try/finally."
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Generator[None, Any, None]:
    """Y.2.gate.c.2 — write per-test timing JSONL when the runner is driving.

    Hook signature is the standard pytest wrapper form. The makereport hook
    fires three times per test (setup / call / teardown phases); we only
    record the ``call`` phase since that's the actual test execution time
    drift detection cares about.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    # Sidecar contract — swallow registry validator failures (a test
    # that monkeypatches RECON_GEN_RUN_DIR to an invalid path for its
    # own purposes must not cause the timings hook to crash the
    # worker).
    try:
        run_dir_path = RECON_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        return
    layer = RECON_GEN_LAYER.get_or_none()
    if not run_dir_path or not layer:
        return
    run_dir = str(run_dir_path)

    record = {
        "layer": layer,
        "test_id": report.nodeid,
        "duration_seconds": float(report.duration),
        "outcome": str(report.outcome),
    }
    # Per-worker file when xdist (c.6) is active — avoids append contention.
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
    suffix = f"-{worker_id}" if worker_id else ""
    # Sidecar contract (Y.2.gate.c.12 alignment): capture failures must
    # never break a passing test. A test that monkeypatches
    # RECON_GEN_RUN_DIR for its own purposes (e.g., the loader sidecar
    # tests) might point us at an unwritable path; swallow OSError
    # rather than crashing the worker.
    try:
        timings_dir = Path(run_dir) / "timings"
        timings_dir.mkdir(parents=True, exist_ok=True)
        target = timings_dir / f"{layer}{suffix}.jsonl"
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CB.0 — Typed-mark filtering (`--tier`, `--dialect`, `--l2`, `--fuzz-count`)
# ---------------------------------------------------------------------------
#
# The runner discovers tests via `pytest --tier=X --dialect=Y --l2=Z`
# instead of hand-listed file paths. Marks are declared via the typed
# wrappers in `tests/_marks.py` (pyright-checked at write time); these
# hooks plumb them into pytest's selection + composition-validation
# at collection time. See `docs/audits/cb_test_layers_update.md` for
# the full design rationale.


def pytest_addoption(parser: Any) -> None:  # typing-smell: ignore[explicit-any]: pytest.Parser from late import
    """Register CB.0's runner-facing options.

    All four default to None (= no filter); when set, the matching
    `pytest_collection_modifyitems` step deselects every test whose
    mark doesn't intersect with the supplied value. The runner sets
    these per cell; bare `pytest tests/unit/` with no CB options
    preserves the pre-CB behavior (all tests run unfiltered)."""
    group = parser.getgroup("recon-gen layered tests (CB.0)")
    group.addoption(
        "--tier", default=None,
        help="Run tests marked @tier(Tier.X) where X matches. "
             "Choices: unit | db | app2 | qs_api | qs_browser.",
    )
    group.addoption(
        "--dialect", default=None,
        help="Run tests marked @dialects(...) containing this dialect. "
             "Choices: pg | or | du.",
    )
    group.addoption(
        "--l2", dest="cb_l2", default=None,
        help="Run tests marked @l2(...) containing this L2 form. "
             "Choices: spec_example | sasquatch_pr | fuzz.",
    )
    group.addoption(
        "--fuzz-count", type=int, default=1,
        help="Number of fuzz seeds to expand `@l2(L2.FUZZ)` tests over. "
             "Local default 1; nightly bumps to 100+. CB.0 spike: "
             "stored on config but not yet wired into seed expansion.",
    )


# CB.0 — known marker names; registered via `pytest_configure` to silence
# the `PytestUnknownMarkWarning` that fires for custom marks. Listing them
# here keeps the marker authority in one place.
_CB_MARK_DOCS = {
    "tier": "Test tier (one of unit | db | app2 | qs_api | qs_browser). Required on every test.",
    "dialects": "DB dialects this test exercises (zero or more of pg | or | du).",
    "l2": "L2 forms this test exercises (zero or more of spec_example | sasquatch_pr | fuzz).",
    "needs": "Runtime deps (docker | playwright | aws_qs | oracledb_client).",
    "writes": "Test mutates DB state — opt in to per-worker isolation.",
    "inputs": "Cross-test artifact dependencies (pytest nodeids of tests whose artifacts this test reads). Collection-time-validated.",
    "serial": "Test must run with `-n 1` (no parallel workers). Carry a reason argument explaining why — usually surfaces a `@writes()`-without-isolation debt entry.",
    "isolation_scope": "Cross-tier isolation key (CB.7 refactor). Args: (scope_value, role) where role is 'producer' or 'consumer'. The `isolated_cfg` fixture uses scope_value as the prefix suffix.",
}


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
    """CB.0 — filter the collected test set by the runner's
    `--tier / --dialect / --l2` options + run composition-rule
    validation.

    Validation rules (per `docs/audits/cb_test_layers_update.md`):

    - No `@tier` mark on a test → ERROR (source of truth; can't
      dispatch).
    - `tier(unit)` + `dialects(...)` → ERROR.
    - `tier(qs_*)` without `aws_qs` in `needs` → ERROR.
    - `tier(qs_browser)` without `playwright` in `needs` → ERROR.

    Errors are surfaced as collected pytest errors — pytest's
    standard machinery puts them in the report.

    Filtering: each `--tier=X / --dialect=Y / --l2=Z` selector
    excludes items whose corresponding mark doesn't match. Selectors
    are independent (`AND`). Items missing a relevant mark when the
    selector is set are deselected (e.g., `--dialect=pg` on a unit
    test with no `@dialects` mark drops it — unit-tier tests run
    under `--tier=unit` only). CB.0 spike behavior: when NO CB
    selector is passed, no filtering happens — backwards compatible
    with the existing 3,436-test default-run.
    """
    cb_tier = config.getoption("--tier")
    cb_dialect = config.getoption("--dialect")
    cb_l2 = config.getoption("cb_l2")

    # Composition-rule validation (always on, even when no selectors).
    # CB.0 spike scope: only the hardest invariants — unmarked-tier
    # ERROR + unit/dialects mismatch ERROR. The fuller rule set (needs
    # / writes / l2 warnings) lands in CB.1 once the mark sweep is
    # underway.
    errors: list[str] = []

    # CB.5 addendum — `@inputs(*nodeids)` collection-time validation.
    # Build the collected-nodeid set once, then for every item that
    # carries an `inputs` marker, verify every referenced nodeid
    # actually exists in the collection. Parametrize-aware: a bare
    # `<file>::<func>` matches any parametrize instance of that
    # function (via prefix check); a full
    # `<file>::<func>[<param-id>]` requires an exact match. This
    # catches the renamed/moved/deleted-input-test case at collection
    # time, before the validator silently reads a stale or missing
    # artifact at runtime.
    collected_nodeids = {item.nodeid for item in items}
    # Build a parametrize-base index too — maps
    # `<file>::<func>` → True for any item whose nodeid starts with
    # that prefix followed by `[` (parametrize instance) OR equals it
    # exactly (non-parametrized). Lets `@inputs("...test_x")` resolve
    # against `...test_x[case1]` automatically.
    parametrize_bases: set[str] = set()
    for nodeid in collected_nodeids:
        # Split off any `[<param>]` tail.
        bracket = nodeid.find("[")
        base = nodeid[:bracket] if bracket != -1 else nodeid
        parametrize_bases.add(base)
    for item in items:
        inputs_marker = next(item.iter_markers("inputs"), None)
        if inputs_marker is None:
            continue
        missing: list[str] = []
        for ref in inputs_marker.args:
            # Exact match? Direct nodeid (parametrize-aware authors who
            # pinned a specific param instance).
            if ref in collected_nodeids:
                continue
            # Prefix match against parametrize bases? Bare nodeid
            # without `[...]` — matches any instance OR a
            # non-parametrized test by exact base.
            if ref in parametrize_bases:
                continue
            missing.append(ref)
        if missing:
            errors.append(
                f"{item.nodeid}: declares @inputs(...) referencing "
                f"nodeids that don't exist in this collection:\n    - "
                + "\n    - ".join(missing)
                + "\n  This usually means an input test was renamed, "
                "moved, or deleted. Update the @inputs(...) on the "
                "validator (or restore the input). If the input lives "
                "in a tier that's not currently being collected (e.g. "
                "running `--tier=qs_browser` standalone), chain via "
                "`./run_tests.sh up_to=<higher-watermark-layer>` "
                "instead."
            )
    # CB.6 — auto-apply `@tier(Tier.UNIT)` to any test that doesn't carry
    # an explicit tier AND isn't under a tier-dir (tests/e2e/{db,app2,
    # qs_api,qs_browser}/) whose own conftest auto-applies the matching
    # tier. The four tier-dirs each have a conftest that adds their tier
    # mark before this hook runs (pytest collection-modifyitems hooks
    # chain in conftest discovery order, deepest-first), so by the time
    # we get here every collected item under a tier-dir already has its
    # mark — the auto-mark below only catches the residual: tests under
    # tests/{unit,json,cli,docs,schema,l2,data}/ that didn't get an
    # explicit @tier.
    from tests._marks import Tier as _Tier, tier as _tier  # noqa: PLC0415
    _UNIT_MARK = _tier(_Tier.UNIT)
    for item in items:
        if any(m.name == "tier" for m in item.iter_markers()):
            continue
        nodeid_path = str(item.path)
        if "/tests/e2e/" in nodeid_path:
            # E2E items without a tier landed here because the test sits
            # at tests/e2e/ root (not in a tier-dir). Don't auto-mark —
            # the test author needs to either move the file into a
            # tier-dir or declare an explicit `@tier(...)`.
            continue
        item.add_marker(_UNIT_MARK)
    for item in items:
        markers = {m.name for m in item.iter_markers()}
        tier_marker = next(item.iter_markers("tier"), None)
        if tier_marker is None:
            errors.append(
                f"{item.nodeid}: missing `@tier(...)` mark. Apply one of "
                f"`@tier(Tier.UNIT | DB | APP2 | QS_API | QS_BROWSER)` "
                f"at the module/test level. Tests in tier-dirs "
                f"(tests/e2e/{{db,app2,qs_api,qs_browser}}/) get the "
                f"tier auto-applied by the dir's conftest — moving the "
                f"file there is the cleanest fix."
            )
            continue
        tier_value = (
            tier_marker.args[0] if tier_marker.args else None
        )
        if tier_value == "unit" and "dialects" in markers:
            errors.append(
                f"{item.nodeid}: `tier(Tier.UNIT)` + `dialects(...)` "
                f"is invalid (unit tier doesn't open a DB; tests that "
                f"emit + assert SQL strings don't carry a dialects "
                f"mark — they're cross-dialect by construction)."
            )
        if tier_value in ("qs_api", "qs_browser"):
            needs_marker = next(item.iter_markers("needs"), None)
            needs_values: set[str] = (
                set(needs_marker.args) if needs_marker is not None else set()
            )
            if "aws_qs" not in needs_values:
                errors.append(
                    f"{item.nodeid}: `tier({tier_value!r})` requires "
                    f"`needs(Need.AWS_QS)` so the runner can skip "
                    f"when AWS is paused."
                )
            if tier_value == "qs_browser" and "playwright" not in needs_values:
                errors.append(
                    f"{item.nodeid}: `tier(Tier.QS_BROWSER)` requires "
                    f"`needs(Need.PLAYWRIGHT)` (QS embed renders in a "
                    f"browser)."
                )
        # CB.7 (refactored 2026-06-02) — the previous "@writes() requires
        # db_cfg" rule was a workaround for the wrong abstraction.
        # Provider-marked isolation (writer fixtures request
        # `isolated_cfg` directly) made the rule vacuous. See
        # `tests/e2e/db/conftest.py::isolated_cfg`.

    if errors:
        # Surface as a single collected error rather than per-item;
        # gives the operator a clean diff of all violations.
        import pytest  # noqa: PLC0415 — lazy
        pytest.exit(
            "CB.0 mark composition violations:\n  - "
            + "\n  - ".join(errors),
            returncode=2,
        )

    # Selector-based filtering.
    if cb_tier is None and cb_dialect is None and cb_l2 is None:
        return  # backwards compat — no CB selectors = unfiltered

    kept: list[Any] = []  # typing-smell: ignore[explicit-any]: same posture as enclosing fn — pytest Item from late import
    deselected: list[Any] = []
    for item in items:
        # `--tier=X` keeps only tests with exactly that tier value.
        if cb_tier is not None:
            tier_marker = next(item.iter_markers("tier"), None)
            if (
                tier_marker is None
                or not tier_marker.args
                or tier_marker.args[0] != cb_tier
            ):
                deselected.append(item)
                continue
        # `--dialect=Y` keeps tests whose @dialects mark contains Y.
        # An empty / missing @dialects mark fails the selector (unit
        # tests with no DB → naturally filtered out under --dialect).
        if cb_dialect is not None:
            dialects_marker = next(item.iter_markers("dialects"), None)
            if (
                dialects_marker is None
                or cb_dialect not in dialects_marker.args
            ):
                deselected.append(item)
                continue
        # `--l2=Z` keeps tests whose @l2 mark contains Z. Same shape
        # as `--dialect`. L2.FUZZ expansion (via --fuzz-count) is
        # CB.1 territory; the spike preserves the bare-name match.
        if cb_l2 is not None:
            l2_marker = next(item.iter_markers("l2"), None)
            if (
                l2_marker is None
                or cb_l2 not in l2_marker.args
            ):
                deselected.append(item)
                continue
        kept.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = kept


# CB.0 marker registration is folded into the existing pytest_configure
# above — see the `_CB_MARK_DOCS` loop at the end of that hook body.


def pytest_generate_tests(metafunc: Any) -> None:  # typing-smell: ignore[explicit-any]: pytest.Metafunc from late import
    """CB.7-followup — auto-fuzz L2 parametrize.

    A test that takes the `l2_instance` fixture but doesn't pin to a
    named scenario via `@l2(L2.SP)` or `@l2(L2.SQ)` implicitly opts
    into a per-run fuzz cell. See `tests/_marks.py::L2` docstring for
    the resolution rules. Implementation pinned by the hook's
    semantics:

    - Compute the L2 set the test should run on (parametrize ids):
      - Start from `@l2` markers' explicit args.
      - If neither SP nor SQ is declared, add FUZZ (auto-fuzz).
      - If no `@l2` marker at all but signature takes `l2_instance`,
        treat as auto-fuzz (single FUZZ cell).
    - Parametrize `l2_instance` indirectly over the resolved id set.

    The `l2_instance` fixture (defined per-tier in
    `tests/e2e/conftest.py`, `tests/json/conftest.py`, etc.) must
    accept the indirect param and load the matching yaml:
    - "spec_example" → load the spec_example yaml
    - "sasquatch_pr" → load the sasquatch_pr yaml
    - "fuzz" → synthesize from RECON_GEN_FUZZ_SEED

    Currently DEFERRED to a CC-phase follow-up: most existing
    `l2_instance` fixtures (e.g. `tests/e2e/conftest.py`) read the
    runner-supplied env var directly + don't yet accept the
    indirect param. Flipping this on without the fixture update
    would break ~3000 tests. The hook below is a no-op stub until
    the fixtures land — when enabled it gates on a small allow-list
    so the auto-fuzz rolls out per tier.
    """
    if "l2_instance" not in metafunc.fixturenames:
        return
    # Resolve declared L2s from @l2 markers.
    declared: set[str] = set()
    for mark in metafunc.definition.iter_markers("l2"):
        for arg in mark.args:
            declared.add(arg)
    has_sp = "spec_example" in declared
    has_sq = "sasquatch_pr" in declared
    has_fuzz = "fuzz" in declared

    # Auto-fuzz rule: if neither SP nor SQ is pinned, treat as
    # unrestricted → add FUZZ.
    if not has_sp and not has_sq:
        declared.add("fuzz")
        has_fuzz = True

    # Single-form (most common today): if only one form would be in
    # declared, don't parametrize — the existing fixture's env-var
    # path handles it. This keeps backward compat with the runner's
    # per-cell L2 dispatch until the CC-phase fixture overhaul.
    if len(declared) <= 1:
        return
    # Multi-form: parametrize indirectly. The root `l2_instance`
    # fixture (defined below) accepts the string id as `request.param`
    # and dispatches via `_load_l2_by_name`. Per-tier overrides (e2e
    # session-scope `l2` shim, etc.) inherit this contract.
    _ = has_fuzz  # noqa: F841 — kept for the fuzz-only diagnostic branch
    metafunc.parametrize("l2_instance", sorted(declared), indirect=True)

    # CC.2.a — same shape as the L2 hook above, applied to the dialect
    # axis. Audit: docs/audits/cc_23_dialect_axis_to_markers.md.
    if "cfg_for_dialect" in metafunc.fixturenames:
        declared_dialects: set[str] = set()
        for mark in metafunc.definition.iter_markers("dialects"):
            for arg in mark.args:
                declared_dialects.add(arg)
        if len(declared_dialects) >= 2:
            metafunc.parametrize(
                "cfg_for_dialect", sorted(declared_dialects), indirect=True,
            )


# ---------------------------------------------------------------------------
# CC.1 — root `l2_instance` fixture (audit: docs/audits/cc_0_l2_fixture_unification.md)
#
# Replaces three per-tier fixtures (`tests/data/test_l2_seed_contract.py
# ::instance`, `tests/json/test_l2_flow_tracing_matrix.py::l2_instance`,
# `tests/e2e/conftest.py::l2`). The marker-driven auto-fuzz hook above
# decides whether this fires parametrized; if not, the env-var fallback
# path mirrors the runner's per-cell single-instance dispatch (kept
# during the CC roll-out; retires after CC.3 drops the cell concept).
# ---------------------------------------------------------------------------


def _load_l2_by_name(name: str) -> Any:  # typing-smell: ignore[explicit-any]: L2Instance import lives in src; lazy-imported below to keep tests/conftest.py side-effect-free
    """Resolve a marker-driven L2 name (`"spec_example"` / `"sasquatch_pr"`
    / `"fuzz"`) to a loaded `L2Instance`.

    Per the CC.0 spike: the `@l2(L2.SP, L2.SQ, L2.FUZZ)` marker's
    string values map 1:1 to the bundled yaml stems (and the `"fuzz"`
    sentinel routes to the per-run synthesized topology pinned by
    `RECON_GEN_FUZZ_SEED`).
    """
    from pathlib import Path  # noqa: PLC0415 — lazy
    from recon_gen.common.l2 import load_instance  # noqa: PLC0415

    if name == "fuzz":
        # Mirror the runner's fuzz-seed contract: the auto-fuzz hook
        # threads RECON_GEN_FUZZ_SEED so the same L2 topology fans
        # across dialect axes. `random_l2_yaml` lives in tests/l2/
        # (the runner already lazy-imports it from there; same shape).
        from tests.l2.fuzz import random_l2_yaml  # noqa: PLC0415
        seed_str = RECON_GEN_FUZZ_SEED.get_or_none()
        seed = int(seed_str) if seed_str is not None else 0
        import tempfile  # noqa: PLC0415
        tmp = Path(tempfile.mkdtemp(prefix="cc1-fuzz-")) / "fuzz.yaml"
        tmp.write_text(random_l2_yaml(seed))
        return load_instance(tmp)

    # Named scenarios — canonical yamls under tests/l2/ (matches
    # `tests/data/test_l2_seed_contract.py::L2_DIR` exactly).
    l2_dir = Path(__file__).resolve().parent / "l2"
    yaml_path = l2_dir / f"{name}.yaml"
    if not yaml_path.exists():
        raise ValueError(
            f"_load_l2_by_name: unknown L2 name {name!r} — "
            f"expected spec_example / sasquatch_pr / fuzz"
        )
    return load_instance(yaml_path)


@pytest.fixture
def l2_instance(request: Any) -> Any:  # typing-smell: ignore[explicit-any]: pytest.FixtureRequest + L2Instance lazy-imported to avoid pulling src/ into conftest module scope
    """Root `l2_instance` fixture — function-scoped, parametrize-aware.

    Two modes (Option B from CC.0 spike):

    - **Parametrized** (indirect via the auto-fuzz hook above):
      `request.param` is a string id (`"spec_example"` / `"sasquatch_pr"`
      / `"fuzz"`). Load that L2 via `_load_l2_by_name`.
    - **Not parametrized** (no `l2_instance` parametrize, just a
      fixture-injection): fall back to the runner-supplied
      `RECON_GEN_TEST_L2_INSTANCE` env var, or `default_l2_instance()`
      if unset. Matches the legacy single-cell behavior.

    Tier-specific overrides (e.g. `tests/e2e/conftest.py::l2` — the
    session-scope shim) inherit this contract — they're thin caches
    around this function.
    """
    if hasattr(request, "param") and request.param is not None:
        return _load_l2_by_name(request.param)
    # Env-var fallback (runner per-cell path; survives until CC.3).
    from recon_gen.common.l2 import default_l2_instance, load_instance  # noqa: PLC0415
    override = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return load_instance(override)
    return default_l2_instance()


# ---------------------------------------------------------------------------
# CC.2.a — root `cfg_for_dialect` fixture (audit: docs/audits/cc_23_dialect_axis_to_markers.md)
#
# Marker-driven dialect dispatch. When a test declares @dialects(Dialect.PG,
# Dialect.OR), the hook above parametrizes this fixture indirectly over those
# values; this body loads the matching `run/config.<dialect>.yaml` (legacy
# path) and returns the Config. Mirrors the CC.1 `l2_instance` shape exactly.
#
# Backward-compat with runner's current per-cell dispatch: when not
# parametrized AND the runner injects `RECON_GEN_DEMO_DATABASE_URL` with a
# dialect-scheme prefix, the existing `load_dialect_cfg` skip-logic in
# `tests/e2e/_agreement_helpers.py` keeps suppressing wrong-cell callspecs.
# CC.3 retires both paths in favor of per-dialect env URLs.
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg_for_dialect(request: Any) -> Any:  # typing-smell: ignore[explicit-any]: pytest.FixtureRequest + Config lazy-imported (Config lives in src/)
    """Resolve a `Config` pointing at the requested dialect's DB.

    Parametrized (indirect) by the `@dialects(Dialect.PG, Dialect.OR)`
    auto-marker hook above: `request.param` is the marker's string
    value (`"pg"` / `"or"` / `"du"`). Single-dialect callsites that
    just request the fixture without a marker fall back to the
    legacy per-cell cfg discovery.

    Today's body delegates to the e2e tier's `load_dialect_cfg` helper
    (skip-dedups wrong-cell callspecs during the CC roll-out). After
    CC.3 the skip-dedup becomes unreachable and the body simplifies
    to a direct cfg load keyed on per-dialect env URLs.
    """
    if hasattr(request, "param") and request.param is not None:
        # Marker values are `"pg"` / `"or"` / `"du"` (the Dialect enum
        # string values). load_dialect_cfg wants "postgres" / "oracle"
        # / "duckdb" — translate.
        dialect_short_to_long = {"pg": "postgres", "or": "oracle", "du": "duckdb"}
        dialect_name = dialect_short_to_long.get(str(request.param), str(request.param))
        from tests.e2e._agreement_helpers import load_dialect_cfg  # noqa: PLC0415
        cfg, _path, _dialect_enum = load_dialect_cfg(dialect_name)
        return cfg
    # Unparametrized: fall back to the runner-supplied cfg (the legacy
    # single-cell path). Mirrors `tests/e2e/conftest.py::cfg`'s discovery
    # order so e2e tests that switch to this fixture keep working.
    from recon_gen.common.config import load_config  # noqa: PLC0415
    from recon_gen.common.env_keys import RECON_GEN_CONFIG  # noqa: PLC0415
    try:
        explicit = RECON_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit = None
    if explicit is not None:
        return load_config(str(explicit))
    # Probe the canonical run/ paths.
    from pathlib import Path as _Path  # noqa: PLC0415
    for candidate in (
        _Path("config.yaml"),
        _Path("run/config.yaml"),
        _Path("run/config.postgres.yaml"),
        _Path("run/config.oracle.yaml"),
    ):
        if candidate.exists():
            return load_config(str(candidate))
    return load_config(None)


# ---------------------------------------------------------------------------
# CB.17.a — shared container fixtures (audit: docs/audits/cb_15_collapse_cells_design.md)
#
# Goal: ONE Postgres + ONE Oracle container per pytest run (memory-bounded,
# prefix-isolated via the existing `isolated_cfg` fixture downstream).
#
# Resolution order:
#
# 1. **Env URL set** (CI / pre-provisioned path): yield the env value
#    directly. ci.yml's "Start shared PG + Oracle" step provisions both
#    containers and exports `RECON_GEN_DEMO_DATABASE_URL_PG` /
#    `RECON_GEN_DEMO_DATABASE_URL_OR` onto the test step.
# 2. **Env unset** (local path): spin testcontainers. Under pytest-xdist
#    this means one container PER WORKER (~500MB PG + ~2GB Oracle per
#    worker). On laptops with `-n 2` that's ~5GB total — fine. For
#    single-container-per-run locally, operators set the env URLs
#    explicitly before invoking pytest (the `./run_tests.sh` wrapper does
#    this in CB.17.e).
#
# These fixtures yield *URLs* only — not Container objects — so callers
# can't accidentally call `.stop()` mid-session. The container lifetime
# is owned by the fixture's generator-finalize.
#
# CB.17.b chains the top-level `cfg` fixture to consume these URLs; for
# now the fixtures just exist and existing tests are unaffected.
# ---------------------------------------------------------------------------


def _strip_sa_url_prefix(url: str) -> str:
    """testcontainers returns SQLAlchemy-flavored URLs (``postgresql+psycopg2://``
    / ``oracle+oracledb://``). recon_gen's `connect_demo_db` wants the plain
    ``postgresql://`` / ``oracle://`` forms — libpq + python-oracledb both reject
    the SA dialect suffix.
    """
    return (
        url
        .replace("postgresql+psycopg2://", "postgresql://", 1)
        .replace("oracle+oracledb://", "oracle://", 1)
    )


def _shared_container_url(
    *,
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
    state_filename: str,
    container_name: str,
    spinup_fn: "Callable[[str], tuple[str, object]]",
    post_spinup_fn: "Callable[[str], None] | None" = None,
) -> str:
    """xdist canonical "session-scope-once" coordinator for shared
    Docker containers (CB.17.k).

    Follows the pytest-xdist docs pattern: use
    ``tmp_path_factory.getbasetemp().parent`` as the shared dir, a
    ``FileLock`` to serialize first-firing across workers, and an
    on-disk JSON state file as the rendezvous point.

    The first worker to acquire the lock calls ``spinup_fn`` (which is
    expected to be one of the runner's adopt-or-create helpers — extra
    safety so even in a no-lock race, the Docker daemon's name-
    uniqueness enforcement collapses concurrent creates onto one
    container). Subsequent workers read the URL from the state file.

    ``post_spinup_fn`` runs INSIDE the FileLock immediately after the
    URL is established and BEFORE the state_file is written. This is
    the single-shot post-init hook for cluster-level setup (extension
    installs, role grants, etc.) that needs to happen exactly once per
    pytest invocation regardless of xdist worker count. Because it runs
    under the same lock that serializes spinup, concurrent workers
    don't race the TOCTOU window between "extension missing" and
    ``CREATE EXTENSION IF NOT EXISTS`` — only the first worker enters
    this branch; followers see ``state_file.is_file()`` and short-
    circuit. ``worker_id == "master"`` (bare pytest) runs it inline.

    ``worker_id == "master"`` skips the lock dance — bare pytest with
    no xdist plugin doesn't need cross-worker coordination.

    Returns the container's connection URL. The container itself is
    persistent — operator manages lifecycle via Docker or
    ``./run_tests.sh down``.
    """
    from filelock import FileLock  # noqa: PLC0415 — lazy

    # Bare pytest (no xdist): straight create.
    if worker_id == "master":
        url, _ = spinup_fn(container_name)
        if post_spinup_fn is not None:
            post_spinup_fn(url)
        return url

    # xdist path: the parent of each worker's basetemp is the dir
    # shared across all workers (e.g. /tmp/pytest-of-<user>/pytest-<n>/).
    root_tmp_dir = tmp_path_factory.getbasetemp().parent
    state_file = root_tmp_dir / state_filename
    lock = FileLock(str(state_file) + ".lock")
    with lock:
        if state_file.is_file():
            return state_file.read_text().strip()
        url, _ = spinup_fn(container_name)
        if post_spinup_fn is not None:
            post_spinup_fn(url)
        state_file.write_text(url)
        return url


# Stable Docker container names — adopt-or-create rendezvous points
# for the xdist-shared session fixtures. Containers persist across
# `pytest` invocations (`_PersistentContainerHandle.stop()` is a
# no-op). Lifecycle is operator-owned: `docker stop` or
# `./run_tests.sh down`.
_SHARED_PG_CONTAINER_NAME: Final = "recon-gen-test-pg"
_SHARED_ORACLE_CONTAINER_NAME: Final = "recon-gen-test-oracle"
# BV.3.3 — dedicated containers for the Snapshotter unit-test files
# (``tests/unit/test_snapshotter_pg.py`` + ``test_snapshotter_oracle.py``).
# Separate from the shared db-tier containers above so schema-create /
# drop ops in the snapshotter tests don't fight the bv33 trainer
# dogfood walk or the layered db-tier matrix for the same container.
# Mid-flight Ctrl-C of an Oracle pytest re-run while the shared Oracle
# was busy was the smell that drove the split. Per
# project_local_dev_env_unconstrained — extra containers are fine.
_SHARED_SNAP_PG_CONTAINER_NAME: Final = "recon-gen-snap-test-pg"
_SHARED_SNAP_ORACLE_CONTAINER_NAME: Final = "recon-gen-snap-test-oracle"


def _install_pgcrypto_extension(url: str) -> None:
    """Install ``pgcrypto`` exactly once per PG container fixture spinup.

    ``emit_schema`` emits ``CREATE EXTENSION IF NOT EXISTS pgcrypto``
    (Phase CW.2 Lock 3 — audit provenance SHA-256). Despite the
    ``IF NOT EXISTS`` guard, concurrent xdist workers all finding
    pgcrypto missing simultaneously race the underlying
    ``INSERT INTO pg_extension`` and one (or more) raise
    ``psycopg.errors.UniqueViolation: pg_extension_name_index``. The
    guard is TOCTOU, not atomic.

    Mitigation: install pgcrypto at the CONTAINER fixture layer, inside
    the same FileLock that ``_shared_container_url`` already uses to
    serialize spinup. Only the first-firing worker enters this branch;
    followers see ``state_file.is_file()`` and adopt the URL without
    re-running the install. Idempotent against persistent containers
    that already have pgcrypto installed from a prior run.

    Replaces the per-test ``_ensure_pgcrypto_installed`` helper in
    ``tests/unit/test_snapshotter_pg.py`` (BV.3.3.f, commit bcf9fc25).
    The invariant — pgcrypto exists before any consumer's
    ``emit_schema`` runs — belongs at the fixture layer, not in
    per-test pre-amble.
    """
    import psycopg  # noqa: PLC0415 — lazy: PG-only path

    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def _install_pgcrypto_under_filelock(
    *,
    url: str,
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
    sentinel_filename: str,
) -> None:
    """Cross-worker-safe pgcrypto install for the env-URL escape hatch.

    The in-process spinup path (``_shared_container_url``) wraps the
    install in its own FileLock — only the first worker enters spinup,
    so only one install runs. The env-URL path skips spinup entirely
    (every worker hits its own ``_resolve_pg_container_url`` and finds
    the env-URL), so we need our own lock. Sentinel file lives in the
    xdist-shared dir; first worker installs + touches sentinel,
    followers see it + short-circuit.
    """
    from filelock import FileLock  # noqa: PLC0415 — lazy

    if worker_id == "master":
        # Bare pytest (no xdist): single process, no race.
        _install_pgcrypto_extension(url)
        return

    root_tmp_dir = tmp_path_factory.getbasetemp().parent
    sentinel = root_tmp_dir / sentinel_filename
    lock = FileLock(str(sentinel) + ".lock")
    with lock:
        if sentinel.is_file():
            return
        _install_pgcrypto_extension(url)
        sentinel.touch()


def _resolve_pg_container_url(
    *,
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
    container_name: str,
    state_filename: str,
) -> str:
    """Adopt-or-create the named PG container and return its URL.

    Extracted from ``pg_container_url`` (CB.17.k) so the BV.3.3
    snapshot tests can share the same xdist rendezvous machinery
    against a SEPARATE container name. The env-URL escape hatch
    (``RECON_GEN_DEMO_DATABASE_URL_PG``) is honored regardless of
    which named container the caller asks for — CI workflows pin a
    single pre-spun URL, the schema/prefix isolation is what keeps
    the two test families from colliding inside that one container.

    Installs ``pgcrypto`` once per fixture spinup via
    ``_install_pgcrypto_extension`` (see that function for the TOCTOU
    rationale). The install runs inside ``_shared_container_url``'s
    FileLock so concurrent xdist workers can't race the
    ``CREATE EXTENSION IF NOT EXISTS`` ⇒ ``INSERT INTO pg_extension``
    sequence. The env-URL escape hatch path also installs (CI workflows
    pinning a pre-spun container still need pgcrypto present); the
    first call wins, subsequent ``CREATE EXTENSION IF NOT EXISTS``
    calls find the extension present and short-circuit before any
    INSERT attempt, so no UniqueViolation.
    """
    env_url = RECON_GEN_DEMO_DATABASE_URL_PG.get_or_none()
    if env_url is not None:
        # Pre-spun-container path: guard the install with the same
        # cross-worker FileLock pattern as the in-process spinup
        # branch. Otherwise each xdist worker probes the env URL
        # independently and concurrent first-time installs race the
        # ``CREATE EXTENSION IF NOT EXISTS`` ⇒ ``INSERT INTO
        # pg_extension`` TOCTOU window (same shape as the in-process
        # branch). Sentinel file in the xdist-shared dir ⇒ first
        # worker installs, followers short-circuit.
        _install_pgcrypto_under_filelock(
            url=env_url,
            tmp_path_factory=tmp_path_factory,
            worker_id=worker_id,
            sentinel_filename=f"pgcrypto-installed.{state_filename}",
        )
        return env_url

    from recon_gen._dev.runner import (  # noqa: PLC0415 — lazy
        _get_or_start_pg_container,
        generate_db_password,
    )

    # BX.248 — fresh per-pytest-invocation password. If the container
    # already exists, the adopt path force-resets via unix-socket
    # trust-auth so the credential survives the rendezvous round-trip.
    password = generate_db_password()

    def _spinup_pg(name: str) -> tuple[str, object]:
        return _get_or_start_pg_container(name, password)

    return _shared_container_url(
        tmp_path_factory=tmp_path_factory,
        worker_id=worker_id,
        state_filename=state_filename,
        container_name=container_name,
        spinup_fn=_spinup_pg,
        post_spinup_fn=_install_pgcrypto_extension,
    )


def _resolve_oracle_container_url(
    *,
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
    container_name: str,
    state_filename: str,
) -> str:
    """Adopt-or-create the named Oracle container and return its URL.

    BV.3.3 — same parameterization shape as ``_resolve_pg_container_url``.
    Honors the ``RECON_GEN_DEMO_DATABASE_URL_OR`` env override; otherwise
    runs through ``_shared_container_url`` (xdist FileLock rendezvous +
    on-disk state file) and the runner's adopt-or-create helper.
    """
    env_url = RECON_GEN_DEMO_DATABASE_URL_OR.get_or_none()
    if env_url is not None:
        return env_url

    pytest.importorskip("testcontainers.oracle")
    from recon_gen._dev.runner import (  # noqa: PLC0415 — lazy
        _get_or_start_oracle_container,
        generate_db_password,
    )

    # BX.248 — fresh per-pytest-invocation password (token_hex satisfies
    # Oracle 19c's letter+digit+8chars rule). Adopt path force-resets
    # via in-container sysdba.
    password = generate_db_password()

    def _spinup(name: str) -> tuple[str, object]:
        raw_url, handle = _get_or_start_oracle_container(name, password)
        return _strip_sa_url_prefix(raw_url), handle

    return _shared_container_url(
        tmp_path_factory=tmp_path_factory,
        worker_id=worker_id,
        state_filename=state_filename,
        container_name=container_name,
        spinup_fn=_spinup,
    )


@pytest.fixture(scope="session")
def pg_container_url(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> Generator[str, None, None]:
    """URL for a session-shared Postgres container.

    CB.17.k — uses the xdist canonical "session-scope-once" pattern.
    All workers in a `pytest -n auto` invocation converge on a SINGLE
    container; only the first-firing worker creates it, others adopt
    via on-disk URL rendezvous.

    Pre-CB.17.k this was a per-worker fixture: with -n auto we spun
    16 PG containers per run, contending for memory and producing
    16× the cold-start wall. Now one container is shared across all
    workers and persists across runs (operator manages teardown via
    `./run_tests.sh down` or `docker stop`).

    Env URL escape hatch: `RECON_GEN_DEMO_DATABASE_URL_PG` skips both
    creation and rendezvous (CI workflows set it to point at a
    pre-spun container).
    """
    url = _resolve_pg_container_url(
        tmp_path_factory=tmp_path_factory,
        worker_id=worker_id,
        container_name=_SHARED_PG_CONTAINER_NAME,
        state_filename="pg-container-url.txt",
    )
    os.environ[RECON_GEN_DEMO_DATABASE_URL_PG.name] = url
    yield url


@pytest.fixture(scope="session")
def oracle_container_url(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> Generator[str, None, None]:
    """URL for a session-shared Oracle container.

    CB.17.k — uses the same xdist canonical "session-scope-once"
    coordinator as `pg_container_url`. Without it, every xdist worker
    spun its own Oracle 19c container; all 16 raced through ~3min cold
    starts simultaneously, exhausting system memory and timing out at
    the 900s ready-wait. With the coordinator: first-firing worker
    creates, others adopt via on-disk URL rendezvous. One container
    per pytest invocation; persists across runs (adopt-or-create
    against `recon-gen-test-oracle`).
    """
    url = _resolve_oracle_container_url(
        tmp_path_factory=tmp_path_factory,
        worker_id=worker_id,
        container_name=_SHARED_ORACLE_CONTAINER_NAME,
        state_filename="oracle-container-url.txt",
    )
    os.environ[RECON_GEN_DEMO_DATABASE_URL_OR.name] = url
    yield url


@pytest.fixture(scope="session")
def snapshotter_pg_container_url(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> Generator[str, None, None]:
    """URL for the BV.3.3-dedicated Snapshotter-test PG container.

    Distinct from ``pg_container_url`` so the snapshotter unit tests
    (``tests/unit/test_snapshotter_pg.py``) own their own schema-
    create / drop / TRUNCATE namespace without fighting the shared
    db-tier matrix or the bv33 trainer dogfood walk. Same adopt-or-
    create + FileLock + on-disk-rendezvous shape — just a different
    container name (``recon-gen-snap-test-pg``) and rendezvous file.

    Lifecycle is operator-owned (``./run_tests.sh down`` tears down
    both the shared and the snap variants).

    The ``RECON_GEN_DEMO_DATABASE_URL_PG`` env override still wins —
    callers pinning a pre-spun container point both fixtures at the
    same URL; schema isolation (the snap-test's ``snap_pg_test``
    base-prefix vs. the shared-PG tests' prefixes) keeps the two
    families apart inside that one container.
    """
    url = _resolve_pg_container_url(
        tmp_path_factory=tmp_path_factory,
        worker_id=worker_id,
        container_name=_SHARED_SNAP_PG_CONTAINER_NAME,
        state_filename="snap-pg-container-url.txt",
    )
    os.environ[RECON_GEN_DEMO_DATABASE_URL_PG.name] = url
    yield url


@pytest.fixture(scope="session")
def snapshotter_oracle_container_url(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> Generator[str, None, None]:
    """URL for the BV.3.3-dedicated Snapshotter-test Oracle container.

    Mirror of ``snapshotter_pg_container_url`` for the Oracle dialect.
    Adopt-or-create against ``recon-gen-snap-test-oracle`` so the
    snapshotter unit tests (``tests/unit/test_snapshotter_oracle.py``)
    can take / restore / drop golden-mirror schemas without colliding
    with the shared db-tier Oracle container — the golden-mirror's
    cold-start dance + DBMS_MVIEW.REFRESH calls are heavy enough that
    sharing led to mid-flight Ctrl-C abandonment of one or the other.
    """
    url = _resolve_oracle_container_url(
        tmp_path_factory=tmp_path_factory,
        worker_id=worker_id,
        container_name=_SHARED_SNAP_ORACLE_CONTAINER_NAME,
        state_filename="snap-oracle-container-url.txt",
    )
    os.environ[RECON_GEN_DEMO_DATABASE_URL_OR.name] = url
    yield url


# CB.17.b — `cfg_with_container_url` fixture (the bridge from the
# shared-container fixtures above to the existing `cfg` chain) lives in
# `tests/e2e/conftest.py` next to the `cfg` fixture it wraps.
