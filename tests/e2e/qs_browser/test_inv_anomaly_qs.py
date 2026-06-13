"""CB.5 stage 2 — qs_browser tier producer: L2 anomaly rendered rows.

Decomposed from the retired ``test_inv_dashboard_agreement.py``'s
``test_invariant_three_way_agreement[anomaly]`` QS leg
(superseded + deleted in the CB.5 follow-up cleanup).

Per-leg degradation: when the isolated Investigation dashboard is
NOT deployed (default — the runner deploys against the shared cfg
only), the QS leg yields None; the producer writes a sentinel
artifact + skips. The validator runs the chain WITHOUT the QS leg.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

import pytest

from recon_gen.common.env_keys import RECON_GEN_E2E

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "QS L2 anomaly producer needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports
from tests.audit._inv_dashboard_extract import (  # noqa: E402
    anomaly_row_keys,
    count_anomaly_rows,
    rows_seen_anomaly,
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


_DEFAULT_SIGMA = 2.0
_ISOLATION_SUFFIX = "iagree"



@pytest.fixture(scope="module")
def inv_dashboard_id(isolated_cfg: "Config") -> str:
    """Isolated cfg's investigation-dashboard ID. When the dashboard
    isn't deployed, qs_inv_driver yields None and the producer writes
    the no-leg sentinel."""
    return f"{isolated_cfg.deployment_name}-investigation-dashboard"


@pytest.fixture
def qs_inv_driver(
    request: pytest.FixtureRequest,
    cfg: "Config",
    inv_dashboard_id: str,
) -> "Iterator[QsEmbedDriver | None]":
    """Function-scoped QS driver aimed at the isolated dashboard.
    Yields None when unavailable; producer writes sentinel + skips.
    """
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


def test_anomaly_qs_extract(
    qs_inv_driver: "QsEmbedDriver | None",
    inv_dashboard_id: str,
) -> None:
    """Read QS's rendered rows for L2 anomaly; write the artifact."""
    if qs_inv_driver is None:
        write_rendered_rows("qs_browser", "anomaly_qs_rows", [])
        write_rendered_rows("qs_browser", "anomaly_qs_meta", [
            {"qs_available": False, "qs_count": None},
        ])
        pytest.skip(
            "QS unavailable — wrote sentinel; validator runs without "
            "the QS leg"
        )

    qs_inv_driver.open(inv_dashboard_id)
    qs_count = count_anomaly_rows(qs_inv_driver, _DEFAULT_SIGMA)
    qs_seen = rows_seen_anomaly(qs_inv_driver, _DEFAULT_SIGMA)
    qs_keys = anomaly_row_keys(qs_inv_driver, _DEFAULT_SIGMA)

    assert qs_seen == qs_count, (
        f"QS anomaly truncated: {qs_seen} of {qs_count} — "
        f"validator's row-identity would be partial"
    )

    payload: list[dict[str, object]] = []
    for key_tuple in _serialize_keys(qs_keys):  # type: ignore[arg-type]: anomaly_row_keys returns set[tuple[str|date,...]] which is a subtype of set[tuple[object,...]]; pyright doesn't follow through the union
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("qs_browser", "anomaly_qs_rows", payload)
    write_rendered_rows("qs_browser", "anomaly_qs_meta", [
        {"qs_available": True, "qs_count": qs_count},
    ])
