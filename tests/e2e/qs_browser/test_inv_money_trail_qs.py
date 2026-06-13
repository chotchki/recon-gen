"""CB.5 stage 2 — qs_browser tier producer: L2 money_trail rendered rows.

Decomposed from the retired ``test_inv_dashboard_agreement.py``'s
``test_invariant_three_way_agreement[money_trail]`` QS leg
(superseded + deleted in the CB.5 follow-up cleanup).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

import pytest

from recon_gen.common.env_keys import RECON_GEN_E2E

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "QS L2 money_trail producer needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports
from tests.audit._inv_dashboard_extract import (  # noqa: E402
    count_money_trail_rows,
    money_trail_row_keys,
    rows_seen_money_trail,
)
from tests._marks import IsolationScope, isolation_consumer  # noqa: E402
from tests.e2e._agreement import write_rendered_rows  # noqa: E402

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from tests.e2e._drivers import QsEmbedDriver


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    isolation_consumer(IsolationScope.AGREEMENT_INV),
]


_PLANTED_CHAIN_ROOT = "xfer-money-trail-0"
_ISOLATION_SUFFIX = "iagree"



@pytest.fixture(scope="module")
def inv_dashboard_id(isolated_cfg: "Config") -> str:
    return f"{isolated_cfg.aws.deployment_name}-investigation-dashboard"


@pytest.fixture
def qs_inv_driver(
    request: pytest.FixtureRequest,
    cfg: "Config",
    inv_dashboard_id: str,
) -> "Iterator[QsEmbedDriver | None]":
    import boto3

    qs = boto3.client("quicksight", region_name=cfg.aws.region)  # pyright: ignore[reportUnknownMemberType]: boto3.client dynamic service-name overload
    try:
        qs.describe_dashboard(
            AwsAccountId=cfg.aws.account_id,
            DashboardId=inv_dashboard_id,
        )
    except qs.exceptions.ResourceNotFoundException:
        yield None
        return
    from tests.e2e._drivers._lifecycle import qs_driver_or_none

    with qs_driver_or_none(
        request,
        cfg=cfg,
        account_id=cfg.aws.account_id,
        region=cfg.aws.region,
        viewport=(1600, 3000),
    ) as driver:
        yield driver


def _serialize_keys(keys: "set[tuple[object, ...]]") -> list[list[object]]:
    return sorted([list(t) for t in keys])


def test_money_trail_qs_extract(
    qs_inv_driver: "QsEmbedDriver | None",
    inv_dashboard_id: str,
) -> None:
    if qs_inv_driver is None:
        write_rendered_rows("qs_browser", "money_trail_qs_rows", [])
        write_rendered_rows("qs_browser", "money_trail_qs_meta", [
            {"qs_available": False, "qs_count": None},
        ])
        pytest.skip(
            "QS unavailable — wrote sentinel; validator runs without "
            "the QS leg"
        )

    qs_inv_driver.open(inv_dashboard_id)
    qs_count = count_money_trail_rows(qs_inv_driver, _PLANTED_CHAIN_ROOT)
    qs_seen = rows_seen_money_trail(qs_inv_driver, _PLANTED_CHAIN_ROOT)
    qs_keys = money_trail_row_keys(qs_inv_driver, _PLANTED_CHAIN_ROOT)

    assert qs_seen == qs_count, (
        f"QS money_trail truncated: {qs_seen} of {qs_count}"
    )

    payload: list[dict[str, object]] = []
    for key_tuple in _serialize_keys(qs_keys):  # type: ignore[arg-type]: money_trail_row_keys returns set[tuple[str|int,...]] which is a subtype of set[tuple[object,...]]; pyright doesn't follow through the union
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("qs_browser", "money_trail_qs_rows", payload)
    write_rendered_rows("qs_browser", "money_trail_qs_meta", [
        {"qs_available": True, "qs_count": qs_count},
    ])
