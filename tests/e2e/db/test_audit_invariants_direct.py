# pyright: reportArgumentType=false
# BF.4/F: `_ALL_INVARIANTS` is `tuple[str, ...]` but the matview-extract
# helpers take the narrower `L1Invariant` Literal. Runtime correctness
# is enforced by the matview-extract helpers' own dict lookups.
"""CB.5 stage 2 — DB-tier producer: direct matview SELECT for L1 audit.

One producer test per L1 invariant. Each producer:

1. Loads the per-dialect cfg + seeds the DB (`apply_db_seed` plants
   the scenario into the matviews).
2. Runs a direct `SELECT` against the L1 invariant matview — the
   *ground truth* every renderer should match.
3. Writes the result rows + count as a JSON artifact via
   `tests/e2e/_agreement.py::write_rendered_rows("db", "<inv>_rows", ...)`.
4. Asserts the producer-side lower bound (matview holds ≥ planted).

The high-watermark validator in `tests/e2e/qs_browser/` reads this
artifact via `read_rendered_rows("db", "<inv>_rows")` and compares
against the App2 / QS producer artifacts. Producer-side failure
(seed didn't reach the matview) fails THIS test directly — the
validator just gets a clean "missing artifact" with the actionable
"check the db tier's stderr" message.

Decomposed from the pre-CB.5 monolithic `test_audit_dashboard_agreement.py`'s
4-way agreement test, per the CB.5 stage 2 design.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import pytest

from recon_gen.common.db import connect_demo_db
from recon_gen.common.l2 import load_instance

from tests.audit._matview_extract import (
    MATVIEW_ANCHORED,
    count_l1_invariant_matview_rows,
    l1_invariant_matview_row_keys,
)
from tests.audit._scenario_expectations import expected_audit_counts
from tests.e2e._agreement import write_rendered_rows
from tests.e2e._agreement_helpers import (
    ALL_L1_INVARIANTS,
    FLAT_SHAPE_INVARIANTS,
    audit_window,
    l2_yaml_for_test,
    load_dialect_cfg,
    today_anchor,
)

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.intervals import DateInterval
    from recon_gen.common.l2.seed import ScenarioPlant
    from recon_gen.common.sql import Dialect


# CB.7 (refactored 2026-06-02) — `@isolation_producer` for the
# audit-agreement chain. App2 + qs_browser sibling files declare
# `@isolation_consumer(AGREEMENT_AUDIT)`; all three tiers share the
# same prefix and read each other's seeded state.
from tests._marks import IsolationScope, isolation_producer  # noqa: E402
pytestmark = [
    pytest.mark.e2e,
    isolation_producer(IsolationScope.AGREEMENT_AUDIT),
    # Sibling producers in this scope race on DROP+CREATE; pin to one
    # worker so the module-scope writer fixtures serialize.
    pytest.mark.xdist_group(IsolationScope.AGREEMENT_AUDIT.value),
]


_TODAY = today_anchor()
_PERIOD = audit_window(_TODAY)


@pytest.fixture(scope="module", params=["postgres", "oracle"])
def dialect_cfg(
    request: pytest.FixtureRequest,
) -> "tuple[Config, Path, Dialect]":
    """Per-dialect (cfg, cfg_path, dialect_enum). Skips cleanly on
    missing config / dialect mismatch / no demo_database_url."""
    return load_dialect_cfg(request.param)


@pytest.fixture(scope="module")
def dialect_isolated_cfg(
    request: pytest.FixtureRequest,
    dialect_cfg: "tuple[Config, Path, Dialect]",
    tmp_path_factory: pytest.TempPathFactory,
) -> "tuple[Config, Path, Dialect]":
    """Per-(module, worker, dialect) isolated cfg.

    CB.7 (refactored 2026-06-02) — this is the provider-marked
    isolation primitive for dialect-parametrized writer fixtures.
    The plain `isolated_cfg` fixture in `tests/e2e/db/conftest.py`
    serves the single-cfg case; this variant is its parallel for
    `dialect_cfg`-driven tests.

    Each (test module, xdist worker, dialect parametrize callspec)
    gets its OWN isolated cfg → concurrent workers and dialect cells
    don't race on schema apply.
    """
    from tests.e2e.db.conftest import _isolate_cfg, _isolated_cfg_key

    cfg, cfg_path, dialect = dialect_cfg
    # The hash already includes cfg.dialect.value (per `_isolated_cfg_key`
    # inputs lock), so no need to splice the dialect into the suffix
    # ourselves — the hash differs per dialect parametrize cell.
    suffix = _isolated_cfg_key(request, cfg)
    isolated_cfg = _isolate_cfg(
        cfg, suffix=suffix, tmp_path_factory=tmp_path_factory,
    )
    return isolated_cfg, cfg_path, dialect


@pytest.fixture(scope="module")
def seeded_db(
    dialect_isolated_cfg: "tuple[Config, Path, Dialect]",
) -> "ScenarioPlant":
    """Seed dialect-specific DB with the spec_example scenario.

    Module-scoped — one seed per (file, dialect, xdist worker).
    Requests `dialect_isolated_cfg` (provider-marked isolation): the
    seed lands in a per-worker prefix, eliminating concurrent-DROP+CREATE
    races without needing `xdist_group` pinning.
    """
    from tests.e2e._seed_helpers import apply_db_seed

    cfg, _cfg_path, dialect = dialect_isolated_cfg
    instance = load_instance(l2_yaml_for_test())
    conn = connect_demo_db(cfg)
    try:
        scenario = apply_db_seed(
            conn, instance,
            prefix=cfg.db_table_prefix,
            mode="l1_invariants",
            today=_TODAY,
            plant_window=_PERIOD,
            dialect=dialect,
            include_baseline=False,
        )
    finally:
        conn.close()
    return scenario


@pytest.fixture
def conn(dialect_isolated_cfg: "tuple[Config, Path, Dialect]") -> "Iterator[Any]":
    """Per-dialect raw DB connection. Thin wrapper over `connect_demo_db`
    because the test parametrizes over dialect and pytest doesn't let a
    file's local fixture override the canonical `db_conn` fixture (which
    takes the single `isolated_cfg`, not `dialect_isolated_cfg`)."""
    from recon_gen.common.db import connect_demo_db
    cfg, _, _ = dialect_isolated_cfg
    c = connect_demo_db(cfg)
    try:
        yield c
    finally:
        c.close()


def _serialize_keys(keys: "set[tuple[Any, ...]]") -> list[list[Any]]:
    """JSON-serialize a set of natural-key tuples → list of lists.

    `date` cells are stringified (ISO) so json.dumps doesn't crash;
    consumers re-parse them back via the same ISO shape. Sort for
    deterministic artifact bytes — diffing two runs of the same cell
    surfaces real drift, not key-order noise.
    """
    return sorted([_normalise_for_json(list(t)) for t in keys])


def _normalise_for_json(row: list[Any]) -> list[Any]:
    """Map non-JSON-native cells (date / datetime) to ISO strings."""
    from datetime import date, datetime
    out: list[Any] = []
    for cell in row:
        if isinstance(cell, datetime):
            out.append(cell.date().isoformat())
        elif isinstance(cell, date):
            out.append(cell.isoformat())
        else:
            out.append(cell)
    return out


@pytest.mark.parametrize("invariant", ALL_L1_INVARIANTS)
def test_l1_invariant_direct_extract(
    seeded_db: "ScenarioPlant",
    conn: Any,
    dialect_isolated_cfg: "tuple[Config, Path, Dialect]",
    invariant: str,
) -> None:
    """Direct matview SELECT for one L1 invariant; writes the artifact
    the validator reads.

    Producer-side assertion: the matview holds at least the planted
    count (`>= expected`). Catches "plant didn't reach the matview"
    locally rather than detaching the agreement comparison.

    `supersession` has no clean matview anchor (the dashboard's
    "Transactions Audit" + the audit PDF each query their own shape
    over the base tables) — for that one the artifact carries an
    empty rows list + `direct_count: null`. The validator handles the
    null shape by skipping the renderer⋈matview leg for supersession.
    """
    cfg, _cfg_path, dialect = dialect_isolated_cfg
    expected_obj = expected_audit_counts(seeded_db, _PERIOD)
    expected: int = getattr(expected_obj, f"{invariant}_count")
    is_flat = invariant in FLAT_SHAPE_INVARIANTS
    prefix = cfg.db_table_prefix

    direct_count: int | None = None
    direct_keys: set[tuple[Any, ...]] = set()
    if invariant in MATVIEW_ANCHORED:
        period_for_matview: DateInterval | None = (
            _PERIOD if is_flat else None
        )
        direct_count = count_l1_invariant_matview_rows(
            conn, prefix, invariant, period_for_matview, dialect,
        )
        if is_flat:
            direct_keys = l1_invariant_matview_row_keys(
                conn, prefix, invariant, _PERIOD, dialect,
            )

    # Producer-side lower bound — fail HERE rather than detaching
    # the agreement comparison.
    if direct_count is not None:
        assert direct_count >= expected, (
            f"Producer-side regression ({invariant}): scenario "
            f"planted {expected} rows but the {prefix}_{invariant} "
            f"matview holds only {direct_count} for the period. "
            f"Plant didn't reach the matview, or the matview SQL "
            f"drifted from the plant."
        )

    # Write the artifact for the validator. Shape per the CB.5
    # convention: a list of dicts; one dict per row containing the
    # natural-key tuple under `natural_key`. The `count` is the
    # row count (None when the matview has no anchor); a separate
    # `<invariant>_meta.json` entry is implicit in the rows shape.
    payload: list[dict[str, Any]] = []
    for key_tuple in _serialize_keys(direct_keys):
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("db", f"{invariant}_direct_rows", payload)

    # Also write a sidecar count + scenario expectation so the
    # validator can do count comparisons for the divergent-shape
    # invariants (which have no row-identity check).
    write_rendered_rows("db", f"{invariant}_direct_meta", [
        {
            "direct_count": direct_count,
            "expected_count": expected,
            "is_flat": is_flat,
            "anchored": invariant in MATVIEW_ANCHORED,
        },
    ])
