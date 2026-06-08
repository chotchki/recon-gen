"""CB.5 stage 2 — App2 tier: L2 anomaly rendered rows.

Seeds its own isolated prefix + writes App2-rendered rows as
artifacts the cross-tier validator reads. Tiers communicate via
JSON artifacts on disk (the pre-CB.7 contract restored in the
CB.7-followup unwind 2026-06-02).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Iterator

import pytest

from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.env_keys import RECON_GEN_E2E

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "App2 L2 anomaly tier needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports
from recon_gen.common.l2 import (  # noqa: E402
    L2Instance,
    load_instance,
    refresh_matviews_sql,
)
from recon_gen.common.spine import AnomalyInvariant  # noqa: E402
from tests.audit._inv_dashboard_extract import (  # noqa: E402
    anomaly_row_keys,
    count_anomaly_rows,
    rows_seen_anomaly,
)
from tests.e2e._agreement import write_rendered_rows  # noqa: E402
from tests.e2e._agreement_helpers import (  # noqa: E402
    l2_yaml_for_test,
    today_anchor,
)
from tests.e2e._drivers import App2Driver  # noqa: E402

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.spine.anomaly import AnomalyGenerator
    from recon_gen.common.tree import App


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
]


_TODAY = today_anchor()
_INSTANCE: L2Instance = load_instance(l2_yaml_for_test())

_DEFAULT_SIGMA = 2.0
_ANOMALY_BASELINE_PAIRS = 100
_ANOMALY_BASELINE_AMOUNT = 100.0
_ANOMALY_SPIKE_MAGNITUDE = 100_000.0


def _plant_anchor_day() -> date:
    return _TODAY - timedelta(days=2)


def _build_anomaly_generator(
    cfg: "Config", anchor_day: date,
) -> "AnomalyGenerator":
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=_ANOMALY_BASELINE_PAIRS,
        baseline_amount=_ANOMALY_BASELINE_AMOUNT,
        spike_magnitude=_ANOMALY_SPIKE_MAGNITUDE,
        anchor_day=anchor_day,
        instance=_INSTANCE,
    )
    gen.prefix = cfg.db_table_prefix
    return gen


@pytest.fixture(scope="module")
def seeded_l2_db(isolated_cfg: "Config") -> None:
    """Apply schema + broad seed + spine plants + matview refresh
    against the per-(module, worker) isolated cfg."""
    from tests.e2e._seed_helpers import apply_db_seed

    conn = connect_demo_db(isolated_cfg)
    try:
        apply_db_seed(
            conn, _INSTANCE,
            prefix=isolated_cfg.db_table_prefix,
            mode="l1_plus_broad",
            today=_TODAY,
            dialect=isolated_cfg.dialect,
            include_baseline=False,
        )
        anchor = _plant_anchor_day()
        try:
            anomaly_gen = _build_anomaly_generator(isolated_cfg, anchor)
        except ValueError as exc:
            if "anomaly sender-eligible internal account" in str(exc):
                pytest.skip(
                    f"L2 lacks the role needed by _build_anomaly_generator: "
                    f"{exc} — see backlog #239 for the L2-shape / test-robustness fix"
                )
            raise
        anomaly_gen.emit(conn)
        conn.commit()
        refresh_sql = refresh_matviews_sql(
            _INSTANCE,
            prefix=isolated_cfg.db_table_prefix,
            dialect=isolated_cfg.dialect,
        )
        with conn.cursor() as cur:
            execute_script(
                cur, refresh_sql, dialect=isolated_cfg.dialect,
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def isolated_inv_app(
    isolated_cfg: "Config",
) -> "Iterator[App]":
    """Investigation App tree built against the ISOLATED cfg.

    Registry isolation (BL.0.A): `build_investigation_app` writes to
    module-level dataset registries keyed by `visual_identifier` (a
    CONSTANT). `isolated_dataset_registries` snapshots on enter,
    restores on exit — prevents this build's iagree-prefixed SQL
    from overwriting a session-scoped Investigation app's entries.
    """
    from recon_gen.apps.investigation.app import build_investigation_app
    from recon_gen.common.dataset_contract import isolated_dataset_registries

    with isolated_dataset_registries():
        app = build_investigation_app(
            isolated_cfg, l2_instance=_INSTANCE,
        )
        app.emit_analysis()
        yield app


def _serialize_keys(keys: "set[tuple[Any, ...]]") -> list[list[Any]]:
    return sorted([_normalise_row(list(t)) for t in keys])


def _normalise_row(row: list[Any]) -> list[Any]:
    from datetime import date as _date, datetime as _datetime
    out: list[Any] = []
    for cell in row:
        if isinstance(cell, _datetime):
            out.append(cell.date().isoformat())
        elif isinstance(cell, _date):
            out.append(cell.isoformat())
        else:
            out.append(cell)
    return out


def test_anomaly_app2_extract(
    seeded_l2_db: None,
    isolated_cfg: "Config",
    isolated_inv_app: "App",
) -> None:
    _ = seeded_l2_db
    """Read App2's rendered rows for L2 anomaly; write the artifact.

    Producer-side: `app2_count >= 1` (at least the planted singleton)
    and `app2_seen == app2_count` (no DOM-window truncation).
    """
    from tests.e2e._harness_html2 import make_live_db_fetchers_for_app

    assert isolated_inv_app.analysis is not None
    visual_fetcher, options_search_fetcher = make_live_db_fetchers_for_app(
        tree_app=isolated_inv_app, cfg=isolated_cfg,
    )

    with App2Driver.serving(
        cfg=isolated_cfg,
        tree_app=isolated_inv_app,
        sheet=isolated_inv_app.analysis.sheets[0],
        data_fetcher=visual_fetcher, options_search_fetcher=options_search_fetcher,
        dashboard_id="inv", dashboard_title="Investigation (live)",
    ) as driver:
        driver.open("inv")
        app2_count = count_anomaly_rows(driver, _DEFAULT_SIGMA)
        app2_seen = rows_seen_anomaly(driver, _DEFAULT_SIGMA)
        app2_keys = anomaly_row_keys(driver, _DEFAULT_SIGMA)

    assert app2_count >= 1, (
        f"Producer regression: App2 anomaly shows {app2_count}; "
        f"expected at least 1 (the planted spike singleton)"
    )
    assert app2_seen == app2_count, (
        f"App2 anomaly table truncated: {app2_seen} of {app2_count} "
        f"visible — validator's row-identity check would be partial"
    )

    payload: list[dict[str, Any]] = []
    for key_tuple in _serialize_keys(app2_keys):  # type: ignore[arg-type]: set[tuple] by construction
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("app2", "anomaly_app2_rows", payload)
    write_rendered_rows("app2", "anomaly_app2_meta", [
        {
            "app2_count": app2_count,
            "sigma_threshold": _DEFAULT_SIGMA,
        },
    ])
