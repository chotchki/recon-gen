# pyright: reportArgumentType=false
# BF.4/F: `_ALL_INVARIANTS` is `tuple[str, ...]` but extract takes
# `L1Invariant` Literal. Runtime correctness enforced by dict lookups.
"""CB.5 stage 2 — App2 tier producer: rendered rows per L1 invariant.

One producer test per L1 invariant. Each producer:

1. Loads per-dialect cfg (the DB was seeded by the db tier's
   `test_audit_invariants_direct.py::seeded_db` before this tier
   fires — the runner's `unit → db → app2 → qs_browser` chain
   guarantees ordering).
2. Builds the L1 dashboard tree against the per-dialect cfg.
3. Spins App2Driver, walks the sheet for this invariant.
4. Writes the rendered row keys + count as a JSON artifact
   (`app2/<inv>_rows.json`).

The high-watermark validator (in `tests/e2e/qs_browser/`) reads this
alongside the db tier's direct-matview / PDF + qs_browser's QS
artifacts and asserts agreement.

Module-scoped App2Driver — one driver context handles all 6 invariant
parametrize cells (the L1 dashboard's sheets each render different
invariants, but the driver opens once and `goto_sheet` swaps).

Re-seed semantics: this module DOES re-seed the DB on entry, mirroring
the db-tier's seeded_db fixture. Two reasons: (a) the runner's tier
chain is `unit → db → app2`, so by the time this tier fires the DB
is already seeded, but the module-scope fixture in db/ closes its
conn at module teardown — the schema persists, the connection
doesn't, and re-seeding here is harmless (DROP+CREATE is idempotent);
(b) running app2 standalone via `pytest tests/e2e/app2/` (no runner)
needs its own seed for the App2 walk to find any rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Mapping

import pytest

from recon_gen.common.db import connect_demo_db
from recon_gen.common.l2 import load_instance

from tests.audit._dashboard_extract import (
    count_l1_invariant_rows,
    l1_invariant_row_keys,
    l1_invariant_rows_seen,
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
from tests.e2e._drivers import App2Driver

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2.seed import ScenarioPlant
    from recon_gen.common.sql import Dialect


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    pytest.mark.xdist_group("audit_dashboard_agreement_seed"),
]


_TODAY = today_anchor()
_PERIOD = audit_window(_TODAY)


@pytest.fixture(scope="module", params=["postgres", "oracle"])
def dialect_cfg(
    request: pytest.FixtureRequest,
) -> "tuple[Config, Path, Dialect]":
    return load_dialect_cfg(request.param)


@pytest.fixture(scope="module")
def seeded_db(
    dialect_cfg: "tuple[Config, Path, Dialect]",
) -> "ScenarioPlant":
    """Re-seed the DB. See module docstring for the why (cross-tier
    boundary; harmless idempotency on the schema apply path).
    """
    from tests.e2e._seed_helpers import apply_db_seed

    cfg, _cfg_path, dialect = dialect_cfg
    instance = load_instance(l2_yaml_for_test())
    conn = connect_demo_db(cfg)
    try:
        return apply_db_seed(
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
    dialect_cfg: "tuple[Config, Path, Dialect]",
    seeded_db: "ScenarioPlant",
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

    cfg, _cfg_path, _dialect = dialect_cfg
    _ = seeded_db  # ordering dep — see fixture docstring

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
    seeded_db: "ScenarioPlant",
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
    expected_obj = expected_audit_counts(seeded_db, _PERIOD)
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
