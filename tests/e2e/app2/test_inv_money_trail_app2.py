"""CB.5 stage 2 — App2 tier consumer: L2 money_trail rendered rows.

Reads the DB state seeded by the db-tier producer
(`tests/e2e/db/test_inv_direct.py`) and writes App2-rendered rows as
artifacts the cross-tier validator consumes.

CB.7 followup (2026-06-02): migrated from producer-marked + re-seeding
to consumer-marked + read-only. See `tests/e2e/_isolation.py::enforce_readonly`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

import pytest

from recon_gen.common.env_keys import RECON_GEN_E2E

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "App2 L2 money_trail consumer needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports
from recon_gen.common.l2 import (  # noqa: E402
    L2Instance,
    load_instance,
)
from tests.audit._inv_dashboard_extract import (  # noqa: E402
    count_money_trail_rows,
    money_trail_row_keys,
    rows_seen_money_trail,
)
from tests._marks import IsolationScope, isolation_consumer  # noqa: E402
from tests.e2e._agreement import write_rendered_rows  # noqa: E402
from tests.e2e._agreement_helpers import (  # noqa: E402
    l2_yaml_for_test,
)
from tests.e2e._drivers import App2Driver  # noqa: E402

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.tree import App


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    isolation_consumer(IsolationScope.AGREEMENT_INV),
]


_INSTANCE: L2Instance = load_instance(l2_yaml_for_test())

_MONEY_TRAIL_CHAIN_LENGTH = 3
_PLANTED_CHAIN_ROOT = "xfer-money-trail-0"


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
    isolated_cfg: "Config",
    isolated_inv_app: "App",
) -> None:
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
