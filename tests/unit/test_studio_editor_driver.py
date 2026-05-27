# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportAttributeAccessIssue=false
# BF.4/F: dataclass-introspection helpers traffic in `object` to handle every
# L2 entity uniformly. The Unknown family + AttributeAccess on .parent / etc.
# come from is_dataclass-narrowed + getattr lookups that pyright can't follow.
"""AI.3 + AI.4 + AI.6 — Studio editor dogfood harness (HTTP transport).

Drives the Studio editor over a Starlette ``TestClient`` (no browser,
no real server) to recreate every fixture in the test-input corpus
from an empty L2 in dependency order, then asserts the saved YAML
loads back structurally equal to the reference. This IS the dogfood
acceptance gate.

Per Lock 3 amendment (2026-05-21), this is the HTTP transport that
covers `spec_example` + `sasquatch_pr` + the fuzz-sampled bulk. The
sibling Playwright transport (AI.2.d.2) drives ONE full pass on
deterministic `spec_example` for real form-render+submit fidelity
when it lands; the L2 structural equivalence asserted here is
identical-shape for both transports — only the wire shape differs.

Parametrization (AI.3 + AI.6):
- `spec_example` — deterministic baseline
- `sasquatch_pr` — richer real-deploy fixture
- 5 fuzz-sampled L2s (default; override via
  ``QS_GEN_AI_FUZZ_SAMPLE_N``) — each from a deterministic seed in a
  per-commit-stable pool. Seeds materialize to tmp yaml files on the
  fly via ``random_l2_yaml(seed)``.

Live in the fast unit layer because the Starlette TestClient path
needs no docker / no real server; gives every push the round-trip
fidelity guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.env_keys import RECON_GEN_AI_FUZZ_SAMPLE_N
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from tests.e2e._drivers.studio_editor import (
    StudioHttpEditorDriver,
    build_editor_app,
)
from tests.l2.fuzz import random_l2_yaml

TestClient = pytest.importorskip("starlette.testclient").TestClient

_FIXTURES = Path(__file__).resolve().parent.parent / "l2"

# AI.6 — deterministic per-commit seed pool. The pool itself is fixed
# (same 5 seeds every run unless overridden); a future opt-in nightly
# could derive the pool from the commit SHA via
# ``feedback_fuzzer_as_property_testing``'s reproducibility contract.
_DEFAULT_FUZZ_SEEDS: tuple[int, ...] = (
    11, 12345, 100, 1000047054, 1075682443,
)


def _fuzz_seeds_for_run() -> tuple[int, ...]:
    """Return the seed pool for this run.

    ``RECON_GEN_AI_FUZZ_SAMPLE_N=N`` (legacy ``QS_GEN_AI_FUZZ_SAMPLE_N``)
    truncates / extends the default pool. N=0 disables fuzz
    parametrization (the corpus collapses to the 2 named fixtures).
    N>len(default) extends deterministically by multiplying the last
    default seed (simple stretch — nightly runs that want 100+ should
    pin a larger explicit pool here).
    """
    n = RECON_GEN_AI_FUZZ_SAMPLE_N.get_or_none()
    if n is None:
        return _DEFAULT_FUZZ_SEEDS
    if n <= 0:
        return ()
    if n <= len(_DEFAULT_FUZZ_SEEDS):
        return _DEFAULT_FUZZ_SEEDS[:n]
    # Stretch deterministically — multiply the last seed by k.
    extra = tuple(
        _DEFAULT_FUZZ_SEEDS[-1] * (k + 2)
        for k in range(n - len(_DEFAULT_FUZZ_SEEDS))
    )
    return _DEFAULT_FUZZ_SEEDS + extra


def _empty_l2() -> L2Instance:
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def _rebuild_via_http(reference_path: Path, dest: Path) -> L2Instance:
    """Drive the HTTP editor driver to rebuild the reference L2 at dest."""
    reference = load_instance(reference_path)
    cache = L2InstanceCache(dest, _empty_l2())
    app = build_editor_app(cache)
    with TestClient(app) as client:
        driver = StudioHttpEditorDriver(client, dest)
        driver.create_l2(reference)
        driver.save_l2_to_path(dest)
    return load_instance(dest)


def _materialize_fuzz_yaml(seed: int, tmp_path: Path) -> Path:
    """Generate the deterministic fuzz L2 for ``seed`` + write it to
    ``tmp_path`` so the rebuild helper can read it back like any
    other fixture. ``random_l2_yaml`` is byte-stable per seed."""
    yaml_text = random_l2_yaml(seed)
    dest = tmp_path / f"fuzz_{seed:010d}_reference.yaml"
    dest.write_text(yaml_text)
    return dest


def _by_identifier(entities: tuple[object, ...], key: str) -> list[object]:
    """Sort a collection by the named identifier field for order-
    insensitive structural comparison. The dogfood's claim is the
    editor preserves the L2 entities + their fields; collection
    tuple ORDER isn't semantically meaningful for L2 validation,
    so we compare as identifier-sorted lists.

    BB.3 driver creates reconciler entities (TTs / aggregating
    Rails) at their first-occupant rail's position rather than
    their yaml-declared position, producing a different tuple order
    in the rebuilt instance — semantically equivalent, structurally
    differently-ordered.

    Description fields are normalized (trailing whitespace stripped)
    so yaml-block-style newline drift doesn't fail the struct
    comparison — formatting differences aren't structural.
    """
    import dataclasses as _dc
    normed: list[object] = []
    for e in entities:
        if not _dc.is_dataclass(e):
            normed.append(e)
            continue
        desc = getattr(e, "description", None)
        if isinstance(desc, str):
            stripped = desc.rstrip()
            if stripped != desc:
                e = _dc.replace(e, description=stripped)  # pyright: ignore[reportArgumentType, reportUnknownArgumentType]: is_dataclass narrows to DataclassInstance|type at runtime; pyright loses through getattr
        normed.append(e)
    return sorted(normed, key=lambda x: str(getattr(x, key)))


def _normalize_descriptions(entities: tuple[object, ...]) -> list[object]:
    """Apply description-trailing-whitespace normalization (same as
    _by_identifier) for collections compared as dicts."""
    import dataclasses as _dc
    out: list[object] = []
    for e in entities:
        if _dc.is_dataclass(e):
            desc = getattr(e, "description", None)
            if isinstance(desc, str):
                stripped = desc.rstrip()
                if stripped != desc:
                    e = _dc.replace(e, description=stripped)  # pyright: ignore[reportArgumentType, reportUnknownArgumentType]: see _by_identifier: third-party stub or test scaffolding cascade
        out.append(e)
    return out


def _assert_l2_structurally_equal(
    rebuilt: L2Instance, reference: L2Instance,
) -> None:
    """AI.4 — the dogfood structural-equivalence assertion.

    Compares parsed `L2Instance` dataclasses (NOT byte-equal YAML
    files) per Lock 1: tuple-order differences in entity collections
    (BB.3 reconcilers land at first-occupant rail position rather
    than yaml-declared position; aggregator-rail-as-reconciler shifts
    similarly) AND description trailing-whitespace differences (yaml
    block-style emit drift) are non-structural and get normalized
    via `_by_identifier` / `_normalize_descriptions`.
    """
    assert _by_identifier(rebuilt.account_templates, "role") == _by_identifier(
        reference.account_templates, "role",
    )
    assert _by_identifier(rebuilt.accounts, "id") == _by_identifier(
        reference.accounts, "id",
    )
    assert _by_identifier(rebuilt.rails, "name") == _by_identifier(
        reference.rails, "name",
    )
    assert _by_identifier(rebuilt.transfer_templates, "name") == _by_identifier(
        reference.transfer_templates, "name",
    )
    # Chains have no single identifier; compare as parent-keyed dicts.
    rebuilt_chains_by_parent = {
        str(c.parent): c for c in _normalize_descriptions(rebuilt.chains)
    }
    reference_chains_by_parent = {
        str(c.parent): c for c in _normalize_descriptions(reference.chains)
    }
    assert rebuilt_chains_by_parent == reference_chains_by_parent
    # LimitSchedules have no single identifier; use the composite key.
    def _ls_key(ls: object) -> str:
        return f"{getattr(ls, 'parent_role')!s}::{getattr(ls, 'rail')!s}"
    rebuilt_ls = {
        _ls_key(ls): ls
        for ls in _normalize_descriptions(rebuilt.limit_schedules)
    }
    reference_ls = {
        _ls_key(ls): ls
        for ls in _normalize_descriptions(reference.limit_schedules)
    }
    assert rebuilt_ls == reference_ls
    assert rebuilt.role_business_day_offsets == (
        reference.role_business_day_offsets
    )
    assert rebuilt.description == reference.description


@pytest.mark.parametrize(
    "fixture_name",
    [
        pytest.param(DEFAULT_PREFIX, id="spec_example"),  # typing-smell: ignore[no-inline-production-constants]: pytest param id= is a display label for test-discovery output, not a contract value; mirrors DEFAULT_PREFIX by convention
        pytest.param("sasquatch_pr", id="sasquatch_pr"),
    ],
)
def test_http_driver_rebuilds_spec_example_structurally(
    tmp_path: Path, fixture_name: str,
) -> None:
    """The editor, driven verb-by-verb in dependency order, recreates
    every fixture's entity + both top-level fields with zero
    structural drift — the dogfood's core claim, HTTP transport.

    BB.4 — parametrized over spec_example (deterministic baseline)
    and sasquatch_pr (richer real-deploy fixture: ~30 rails, fan_in
    chains, XOR groups, aggregating rails, persona)."""
    reference = load_instance(_FIXTURES / f"{fixture_name}.yaml")
    dest = tmp_path / f"dogfood_{fixture_name}.yaml"
    rebuilt = _rebuild_via_http(_FIXTURES / f"{fixture_name}.yaml", dest)
    _assert_l2_structurally_equal(rebuilt, reference)


@pytest.mark.parametrize(
    "seed",
    [
        pytest.param(s, id=f"fuzz_{s:010d}")
        for s in _fuzz_seeds_for_run()
    ],
)
def test_http_driver_rebuilds_fuzz_l2_structurally(
    tmp_path: Path, seed: int,
) -> None:
    """AI.6 — the same dogfood claim parametrized over the fuzz axis.
    ``random_l2_yaml(seed)`` produces a deterministic valid L2 per
    seed; the dogfood claim must hold for every seed in the pool, not
    just the two hand-authored fixtures. Failure ⇒ the editor has a
    blind spot on some L2 shape only the fuzzer exercises.

    Failed seeds reproduce via `pytest -k fuzz_<NNNNN>`; the seed
    pool itself is fixed (per-commit-stable). Nightly opt-in cranks
    the pool size via ``QS_GEN_AI_FUZZ_SAMPLE_N``."""
    reference_path = _materialize_fuzz_yaml(seed, tmp_path)
    reference = load_instance(reference_path)
    dest = tmp_path / f"dogfood_fuzz_{seed:010d}.yaml"
    rebuilt = _rebuild_via_http(reference_path, dest)
    _assert_l2_structurally_equal(rebuilt, reference)


# =============================================================================
# AI.5 — Dashboard data equivalence (SQLite-only, matview-row level).
# =============================================================================
#
# Per Lock 2 (2026-05-19): "Per-sheet, per-visual: visual titles + table
# row content + KPI numeric values. Skip DOM byte-equality + screenshot
# diffs." AI.5's data-equivalence claim is that the dogfood'd L2
# produces dashboards that show the same NUMBERS as the reference L2.
# The dashboards consume matview rows (every visual's SQL projects /
# filters / groups OVER a matview). The proof shape: build a fresh
# SQLite per L2, run schema → seed → refresh, then assert every
# matview's row content matches byte-for-byte between reference and
# dogfood. Dataset SQL is downstream-pure (a function of the L2),
# so matview-row equality + L2 structural equality (AI.4) together
# imply dashboard data equality — the BB.0 catalog's reasoning shape.
#
# Why this is meaningfully stronger than AI.4: AI.4 normalizes tuple
# order + description whitespace as non-structural. AI.5 runs the FULL
# generator pipeline (schema emit, seed, matview SQL) against both L2s
# and asserts the OUTPUT is byte-equal — any hidden source of
# non-determinism downstream of the dataclass shape (field-order
# semantics in matview SQL emit, seed plant landing dates dependent on
# tuple position, etc.) gets caught here.


def _build_l2_sqlite(reference_path: Path) -> tuple[object, str]:
    """Build an in-memory SQLite with schema + plants-only seed +
    matview refresh for the L2 at ``reference_path``. Returns
    ``(conn, prefix)``.

    Plants-only seed (skips the 90-day baseline) keeps the
    per-variant runtime sub-second while still exercising every
    matview's SELECT shape — every L1 + Investigation matview reads
    its source rows from the planted scenario.
    """
    import json as _json
    import sqlite3
    from datetime import datetime as _datetime

    import yaml as _yaml
    from recon_gen.common.as_of_frame import LOCKED_ANCHOR
    from recon_gen.common.db import _register_sqlite_aggregates, execute_script
    from recon_gen.common.l2.auto_scenario import default_scenario_for
    from recon_gen.common.l2.config_table import emit_config_populate_sql
    from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
    from recon_gen.common.l2.seed import emit_seed
    from recon_gen.common.l2.serializer import serialize_l2
    from recon_gen.common.sql import Dialect

    instance = load_instance(reference_path)
    prefix = reference_path.stem.replace("-", "_").replace(".", "_")
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)

    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=prefix, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()

    # Populate <prefix>_config_kv (BC.7 + BC.12; matview JOINs depend
    # on it for limit cap + max_pending_age + direction).
    l2_yaml_text = serialize_l2(instance)
    l2_dict = _yaml.safe_load(l2_yaml_text)
    populate_sql = emit_config_populate_sql(
        prefix=prefix,
        cfg_json="{}",
        l2_json=_json.dumps(l2_dict, default=str, separators=(",", ":")),
        as_of=_datetime(
            LOCKED_ANCHOR.year, LOCKED_ANCHOR.month, LOCKED_ANCHOR.day,
            12, 0, 0,
        ),
        dialect=Dialect.SQLITE,
    )
    cur = conn.cursor()
    execute_script(cur, populate_sql, dialect=Dialect.SQLITE)
    conn.commit()

    # Plants-only seed. Default scenario is deterministic per L2 +
    # anchor; the spine generator produces the same SQL string given
    # the same L2 + anchor.
    report = default_scenario_for(instance, today=LOCKED_ANCHOR)
    seed_sql = emit_seed(
        instance, report.scenario,
        prefix=prefix, dialect=Dialect.SQLITE,
    )
    if seed_sql:
        cur = conn.cursor()
        execute_script(cur, seed_sql, dialect=Dialect.SQLITE)
        conn.commit()

    # Refresh every matview against the planted scenario.
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(
            instance, prefix=prefix, dialect=Dialect.SQLITE,
        ),
        dialect=Dialect.SQLITE,
    )
    conn.commit()

    return conn, prefix


# Matviews the dashboards read from (subset of refresh_matviews_sql's
# list; excludes the `current_*` views which are 1:1 projections of
# the base tables — base tables compared separately if needed).
_DASHBOARD_MATVIEWS = (
    "computed_subledger_balance",
    "computed_ledger_balance",
    "drift",
    "ledger_drift",
    "overdraft",
    "expected_eod_balance_breach",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "chain_parent_disagreement",
    "xor_group_violation",
    "transfer_parents",
    "fan_in_disagreement",
    "multi_xor_violation",
    "daily_statement_summary",
    "todays_exceptions",
    "inv_pair_rolling_anomalies",
    "inv_money_trail_edges",
)


def _collect_matview_rows(
    conn: object, prefix: str,
) -> dict[str, frozenset[tuple[object, ...]]]:
    """SELECT * from every dashboard-consumed matview; return a
    suffix-keyed dict of row-set fingerprints. ``frozenset`` makes
    row-ORDER non-structural (matview row order isn't a stable
    property — the dashboard sorts/filters downstream)."""
    out: dict[str, frozenset[tuple[object, ...]]] = {}
    for suffix in _DASHBOARD_MATVIEWS:
        table = f"{prefix}_{suffix}"
        try:
            rows = conn.execute(  # type: ignore[attr-defined]: sqlite3.Connection — `object` annotation kept for the driver-side opaque-conn handoff pattern
                f"SELECT * FROM {table}",
            ).fetchall()
        except Exception:
            # Matview doesn't exist for this L2 (e.g., chain_parent_
            # disagreement when no chains are declared). Treat as
            # empty so the comparison still asserts shape-equal.
            rows = []
        out[suffix] = frozenset(tuple(r) for r in rows)
    return out


def _assert_matview_rows_equal(
    ref_rows: dict[str, frozenset[tuple[object, ...]]],
    dog_rows: dict[str, frozenset[tuple[object, ...]]],
    variant: str,
) -> None:
    """AI.5 — per-matview row-set equality. Reports the first
    matview that drifts so the failure points at a specific table."""
    assert set(ref_rows.keys()) == set(dog_rows.keys()), (
        f"[{variant}] matview key set differs: "
        f"ref={sorted(ref_rows.keys())} dog={sorted(dog_rows.keys())}"
    )
    for matview, ref_set in ref_rows.items():
        dog_set = dog_rows[matview]
        missing_in_dog = ref_set - dog_set
        extra_in_dog = dog_set - ref_set
        assert not missing_in_dog and not extra_in_dog, (
            f"[{variant}] matview {matview!r}: dogfood drift.\n"
            f"  rows missing in dogfood ({len(missing_in_dog)}): "
            f"{sorted(repr(r) for r in list(missing_in_dog)[:3])}\n"
            f"  rows extra in dogfood ({len(extra_in_dog)}): "
            f"{sorted(repr(r) for r in list(extra_in_dog)[:3])}\n"
            f"  ref total: {len(ref_set)}, dog total: {len(dog_set)}"
        )


@pytest.mark.parametrize(
    "fixture_name",
    [
        pytest.param(DEFAULT_PREFIX, id="spec_example"),  # typing-smell: ignore[no-inline-production-constants]: pytest param id= is a display label for test-discovery output, not a contract value; mirrors DEFAULT_PREFIX by convention
        pytest.param("sasquatch_pr", id="sasquatch_pr"),
    ],
)
def test_dogfood_matview_rows_match_reference(
    tmp_path: Path, fixture_name: str,
) -> None:
    """AI.5 — the dogfood'd L2 produces byte-identical matview rows
    to the reference L2 when run through the same schema → seed →
    refresh pipeline. This is the user-facing acceptance gate:
    operators see the same dashboard numbers regardless of whether
    the L2 was hand-authored or rebuilt through the Studio editor.

    Stronger than AI.4 (which normalizes tuple order +
    description-whitespace as non-structural): AI.5 runs the FULL
    generator pipeline against both L2s and asserts the OUTPUT
    matches byte-for-byte."""
    reference_path = _FIXTURES / f"{fixture_name}.yaml"
    dogfood_path = tmp_path / f"dogfood_{fixture_name}.yaml"
    _rebuild_via_http(reference_path, dogfood_path)

    ref_conn, ref_prefix = _build_l2_sqlite(reference_path)
    try:
        ref_rows = _collect_matview_rows(ref_conn, ref_prefix)
    finally:
        ref_conn.close()  # type: ignore[attr-defined]: sqlite3.Connection — `object` annotation kept for the driver-side opaque-conn handoff pattern

    dog_conn, dog_prefix = _build_l2_sqlite(dogfood_path)
    try:
        dog_rows = _collect_matview_rows(dog_conn, dog_prefix)
    finally:
        dog_conn.close()  # type: ignore[attr-defined]: sqlite3.Connection — `object` annotation kept for the driver-side opaque-conn handoff pattern

    _assert_matview_rows_equal(ref_rows, dog_rows, variant=fixture_name)


@pytest.mark.parametrize(
    "seed",
    [
        pytest.param(s, id=f"fuzz_{s:010d}")
        for s in _fuzz_seeds_for_run()
    ],
)
def test_dogfood_matview_rows_match_reference_fuzz(
    tmp_path: Path, seed: int,
) -> None:
    """AI.5 + AI.6 — dashboard-data-equivalence claim parametrized
    over the fuzz axis. Same shape as the named-fixture variant
    above; fails loudly on the first seed whose dogfood'd L2
    produces drifted matview rows."""
    reference_path = _materialize_fuzz_yaml(seed, tmp_path)
    dogfood_path = tmp_path / f"dogfood_fuzz_{seed:010d}.yaml"
    _rebuild_via_http(reference_path, dogfood_path)

    ref_conn, ref_prefix = _build_l2_sqlite(reference_path)
    try:
        ref_rows = _collect_matview_rows(ref_conn, ref_prefix)
    finally:
        ref_conn.close()  # type: ignore[attr-defined]: sqlite3.Connection — `object` annotation kept for the driver-side opaque-conn handoff pattern

    dog_conn, dog_prefix = _build_l2_sqlite(dogfood_path)
    try:
        dog_rows = _collect_matview_rows(dog_conn, dog_prefix)
    finally:
        dog_conn.close()  # type: ignore[attr-defined]: sqlite3.Connection — `object` annotation kept for the driver-side opaque-conn handoff pattern

    _assert_matview_rows_equal(
        ref_rows, dog_rows, variant=f"fuzz_{seed:010d}",
    )
