"""CB.5 stage 2 — App2 tier: L2 money_trail rendered rows.

Seeds its own isolated prefix + writes App2-rendered rows as
artifacts the cross-tier validator reads. Tiers communicate via
JSON artifacts on disk (the pre-CB.7 contract restored 2026-06-02).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Iterator

import pytest

from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.env_keys import RECON_GEN_E2E

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "App2 L2 money_trail tier needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports
from recon_gen.common.l2 import (  # noqa: E402
    L2Instance,
    load_instance,
    refresh_matviews_sql,
)
from recon_gen.common.spine import MoneyTrailInvariant  # noqa: E402
from tests.audit._inv_dashboard_extract import (  # noqa: E402
    count_money_trail_rows,
    money_trail_row_keys,
    rows_seen_money_trail,
)
from tests.e2e._agreement import write_rendered_rows  # noqa: E402
from tests.e2e._agreement_helpers import (  # noqa: E402
    l2_yaml_for_test,
    today_anchor,
)
from tests.e2e._drivers import App2Driver  # noqa: E402

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.spine.money_trail import MoneyTrailGenerator
    from recon_gen.common.tree import App


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
]


_TODAY = today_anchor()
_INSTANCE: L2Instance = load_instance(l2_yaml_for_test())

_MONEY_TRAIL_CHAIN_LENGTH = 3
_MONEY_TRAIL_AMOUNT = 100.0
_PLANTED_CHAIN_ROOT = "xfer-money-trail-0"


def _plant_anchor_day() -> date:
    return _TODAY - timedelta(days=2)


def _build_money_trail_generator(
    cfg: "Config", anchor_day: date,
) -> "MoneyTrailGenerator":
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger",
        chain_length=_MONEY_TRAIL_CHAIN_LENGTH,
        amount=_MONEY_TRAIL_AMOUNT,
        anchor_day=anchor_day,
        instance=_INSTANCE,
    )
    gen.prefix = cfg.db_table_prefix
    return gen


@pytest.fixture(scope="module")
def seeded_l2_db(isolated_cfg: "Config") -> None:
    """Apply schema + broad seed + money_trail plants + matview refresh
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
        mt_gen = _build_money_trail_generator(isolated_cfg, anchor)
        mt_gen.emit(conn)
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
    from recon_gen.apps.investigation.app import build_investigation_app
    from recon_gen.common.dataset_contract import isolated_dataset_registries

    with isolated_dataset_registries():
        app = build_investigation_app(
            isolated_cfg, l2_instance=_INSTANCE,
        )
        app.emit_analysis()
        yield app


def _serialize_keys(keys: "set[tuple[Any, ...]]") -> list[list[Any]]:
    return sorted([list(t) for t in keys])


def test_money_trail_app2_extract(
    seeded_l2_db: None,
    isolated_cfg: "Config",
    isolated_inv_app: "App",
) -> None:
    _ = seeded_l2_db
    """Read App2's rendered rows for L2 money_trail at the planted
    root; write the artifact.

    Producer-side: `app2_count == chain_length` (every edge of the
    planted chain visible) and `app2_seen == app2_count`.
    """
    from tests.e2e._harness_html2 import make_live_db_fetchers_for_app

    assert isolated_inv_app.analysis is not None
    visual_fetcher, options_fetcher = make_live_db_fetchers_for_app(
        tree_app=isolated_inv_app, cfg=isolated_cfg,
    )

    with App2Driver.serving(
        cfg=isolated_cfg,
        tree_app=isolated_inv_app,
        sheet=isolated_inv_app.analysis.sheets[0],
        data_fetcher=visual_fetcher, options_fetcher=options_fetcher,
        dashboard_id="inv", dashboard_title="Investigation (live)",
    ) as driver:
        driver.open("inv")
        app2_count = count_money_trail_rows(driver, _PLANTED_CHAIN_ROOT)
        app2_seen = rows_seen_money_trail(driver, _PLANTED_CHAIN_ROOT)
        app2_keys = money_trail_row_keys(driver, _PLANTED_CHAIN_ROOT)

    assert app2_count >= _MONEY_TRAIL_CHAIN_LENGTH, (
        f"Producer regression: App2 money_trail shows {app2_count} "
        f"edges; planted at least {_MONEY_TRAIL_CHAIN_LENGTH}"
    )
    assert app2_seen == app2_count, (
        f"App2 money_trail truncated: {app2_seen} of {app2_count} "
        f"visible — validator row-identity would be partial"
    )

    payload: list[dict[str, Any]] = []
    for key_tuple in _serialize_keys(app2_keys):  # type: ignore[arg-type]: money_trail_row_keys returns set[tuple[str|int,...]] which is a subtype of set[tuple[Any,...]]; pyright doesn't follow through the union
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("app2", "money_trail_app2_rows", payload)
    write_rendered_rows("app2", "money_trail_app2_meta", [
        {
            "app2_count": app2_count,
            "root_transfer_id": _PLANTED_CHAIN_ROOT,
        },
    ])
