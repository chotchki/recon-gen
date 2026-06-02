# pyright: reportArgumentType=false
# BF.4/F: `_ALL_INVARIANTS` is `tuple[str, ...]` but extract takes
# `L1Invariant` Literal. Runtime correctness enforced by dict lookups.
"""CB.5 stage 2 — App2 tier consumer: rendered rows per L1 invariant.

Reads DB state seeded by the db-tier producer
(`tests/e2e/db/test_audit_direct.py`) and writes App2-rendered rows
as artifacts the cross-tier validator consumes.

CB.7 followup (2026-06-02): migrated from producer-marked + re-seeding
to consumer-marked + read-only. The `isolation_consumer` marker drives
PG's `SET default_transaction_read_only = on` via the
`enforce_readonly` helper applied in the file's local `conn` fixture
(since this file overrides the canonical `db_conn` for the
dialect-parametrize axis). Any DROP/CREATE/INSERT from this file
raises `ReadOnlySqlTransaction` at the offending line.

The producer ran in the db tier (`unit → db → app2 → qs_browser`
chain). The ScenarioPlant needed for `expected_audit_counts` is
reconstructed deterministically via `default_scenario_for` — pure
function, no DB writes.

Module-scoped App2Driver — one driver context handles all 6 invariant
parametrize cells (the L1 dashboard's sheets each render different
invariants, but the driver opens once and `goto_sheet` swaps).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping

import pytest

from recon_gen.common.db import connect_demo_db
from recon_gen.common.l2 import load_instance
from recon_gen.common.l2.auto_scenario import default_scenario_for

from tests.audit._dashboard_extract import (
    count_l1_invariant_rows,
    l1_invariant_row_keys,
    l1_invariant_rows_seen,
)
from tests.audit._scenario_expectations import expected_audit_counts
from tests._marks import IsolationScope, isolation_consumer  # noqa: E402
from tests.e2e._agreement import write_rendered_rows
from tests.e2e._agreement_helpers import (
    ALL_L1_INVARIANTS,
    FLAT_SHAPE_INVARIANTS,
    audit_window,
    l2_yaml_for_test,
    load_dialect_cfg,
    today_anchor,
)
from tests.e2e._drivers import App2Driver
from tests.e2e._isolation import enforce_readonly

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2.seed import ScenarioPlant
    from recon_gen.common.sql import Dialect


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    isolation_consumer(IsolationScope.AGREEMENT_AUDIT),
]


_TODAY = today_anchor()
_PERIOD = audit_window(_TODAY)


@pytest.fixture(scope="module", params=["postgres", "oracle"])
def dialect_cfg(
    request: pytest.FixtureRequest,
) -> "tuple[Config, Path, Dialect]":
    return load_dialect_cfg(request.param)


@pytest.fixture(scope="module")
def dialect_isolated_cfg(
    request: pytest.FixtureRequest,
    dialect_cfg: "tuple[Config, Path, Dialect]",
    tmp_path_factory: pytest.TempPathFactory,
) -> "tuple[Config, Path, Dialect]":
    """Per-(module, worker, dialect) isolated cfg — scope-pinned to
    `AGREEMENT_AUDIT`.

    CB.7 followup — mirrors the producer's `dialect_isolated_cfg` in
    `tests/e2e/db/test_audit_direct.py`. Both fixtures call
    `_resolve_isolation_suffix` which picks up the module's
    `@isolation_consumer(IsolationScope.AGREEMENT_AUDIT)` marker and
    returns the scope value as suffix → producer + consumer read/write
    the same `<base>_x_aa` prefix.
    """
    from tests.e2e._isolation import _isolate_cfg, _resolve_isolation_suffix

    cfg, cfg_path, dialect = dialect_cfg
    suffix, _is_scope_pinned = _resolve_isolation_suffix(request, cfg)
    isolated_cfg = _isolate_cfg(
        cfg, suffix=suffix, tmp_path_factory=tmp_path_factory,
    )
    return isolated_cfg, cfg_path, dialect


@pytest.fixture(scope="module")
def scenario(
    dialect_isolated_cfg: "tuple[Config, Path, Dialect]",
) -> "ScenarioPlant":
    """Reconstruct the producer's ScenarioPlant deterministically.

    `default_scenario_for` is pure (no DB writes); same inputs the
    producer fed `apply_db_seed`. Used by `expected_audit_counts` to
    compute the planted-row count this consumer asserts against.
    """
    _ = dialect_isolated_cfg  # ordering only; scenario construction is pure
    instance = load_instance(l2_yaml_for_test())
    report = default_scenario_for(instance, today=_TODAY, mode="l1_invariants")
    return report.scenario


@pytest.fixture(scope="module")
def conn(
    dialect_isolated_cfg: "tuple[Config, Path, Dialect]",
) -> "Iterator[Any]":
    """Per-dialect read-only DB connection against the scope-pinned
    isolated cfg. CB.7 followup: applies `enforce_readonly` so the
    consumer marker is enforced by PG itself — any attempted write
    raises at the offending line."""
    cfg, _cfg_path, dialect = dialect_isolated_cfg
    c = connect_demo_db(cfg)
    try:
        enforce_readonly(c, dialect)
        yield c
    finally:
        c.close()


def _serialize_keys(keys: "set[tuple[object, ...]]") -> list[list[object]]:
    """Sort + JSON-ize tuples of natural-key cells; date → ISO."""
    return sorted([_normalise_row(list(t)) for t in keys])


def _normalise_row(row: list[object]) -> list[object]:
    from datetime import date, datetime
    out: list[object] = []
    for cell in row:
        if isinstance(cell, datetime):
            out.append(cell.date().isoformat())
        elif isinstance(cell, date):
            out.append(cell.isoformat())
        else:
            out.append(cell)
    return out


@pytest.fixture(scope="module")
def app2_results(
    dialect_isolated_cfg: "tuple[Config, Path, Dialect]",
    conn: Any,
) -> "Mapping[str, Mapping[str, object]]":
    """One App2 walk; collect all 6 invariants' (count, seen, keys).

    Module-scoped — the driver spin + L1 dashboard tree build is the
    expensive setup. Each parametrize cell reads from this dict; no
    per-test driver respin.

    Tear-down before returning isn't load-bearing here (CB.5 stage 2
    decomposition removed the QS-vs-App2 same-process Playwright
    conflict — they live in separate tier processes now), but the
    one-driver-per-module shape minimizes Playwright cost.
    """
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    from recon_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets,
    )
    from tests.e2e._harness_html2 import make_live_db_fetchers_for_app

    cfg, _cfg_path, _dialect = dialect_isolated_cfg
    _ = conn  # ordering dep — the read-only conn forces producer-ran ordering

    instance = load_instance(l2_yaml_for_test())
    build_all_l1_dashboard_datasets(cfg, instance)
    tree_app = build_l1_dashboard_app(cfg, l2_instance=instance)
    if tree_app.analysis is None:
        tree_app.emit_analysis()
    visual_fetcher, options_fetcher = make_live_db_fetchers_for_app(
        tree_app=tree_app, cfg=cfg,
    )
    results: dict[str, dict[str, object]] = {}
    assert tree_app.analysis is not None
    with App2Driver.serving(
        cfg=cfg,
        tree_app=tree_app, sheet=tree_app.analysis.sheets[0],
        data_fetcher=visual_fetcher, options_fetcher=options_fetcher,
        dashboard_id="l1", dashboard_title="L1 Dashboard",
    ) as driver:
        driver.open("l1")
        for inv in ALL_L1_INVARIANTS:
            entry: dict[str, object] = {
                "count": count_l1_invariant_rows(driver, inv, _PERIOD),
            }
            if inv in FLAT_SHAPE_INVARIANTS:
                entry["seen"] = l1_invariant_rows_seen(driver, inv, _PERIOD)
                entry["keys"] = l1_invariant_row_keys(driver, inv, _PERIOD)
            results[inv] = entry
    return results


@pytest.mark.parametrize("invariant", ALL_L1_INVARIANTS)
def test_l1_invariant_app2_extract(
    scenario: "ScenarioPlant",
    app2_results: "Mapping[str, Mapping[str, object]]",
    invariant: str,
) -> None:
    """Read App2's rendered rows for one L1 invariant; write the
    artifact the validator reads.

    Producer-side: `app2_count >= expected`. For flat-shape, also
    verifies `app2_seen == app2_count` (DOM window holds the full
    set) before serializing the keys — a partial set would silently
    mismatch the agreement validator's row-identity check.
    """
    expected_obj = expected_audit_counts(scenario, _PERIOD)
    expected: int = getattr(expected_obj, f"{invariant}_count")
    entry = app2_results[invariant]
    app2_count = int(entry["count"])  # type: ignore[call-overload]: dict value is `object`; int by fixture construction

    assert app2_count >= expected, (
        f"Producer-side regression ({invariant}): scenario planted "
        f"{expected} rows but App2 shows only {app2_count}."
    )

    payload: list[dict[str, object]] = []
    if invariant in FLAT_SHAPE_INVARIANTS:
        app2_seen = int(entry["seen"])  # type: ignore[call-overload]: same as above
        # If the DOM window truncated, the validator's row-identity
        # comparison would be partial. Fail HERE not there.
        assert app2_seen == app2_count, (
            f"App2 table window truncated ({invariant}): "
            f"{app2_seen} of {app2_count} rows visible. The validator "
            f"would compare partial row sets."
        )
        keys_set = entry["keys"]
        # `keys` is a set[tuple[...]] by construction in app2_results.
        for key_tuple in _serialize_keys(keys_set):  # type: ignore[arg-type]: set[tuple] by construction
            payload.append({"natural_key": key_tuple})

    write_rendered_rows("app2", f"{invariant}_app2_rows", payload)
    write_rendered_rows("app2", f"{invariant}_app2_meta", [
        {"app2_count": app2_count, "expected_count": expected},
    ])
