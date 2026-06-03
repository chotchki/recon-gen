# pyright: reportArgumentType=false
# BF.4/F: `_ALL_INVARIANTS` is `tuple[str, ...]`; extract takes
# `L1Invariant` Literal — runtime correctness via dict lookups.
"""CB.5 stage 2 — qs_browser tier producer: rendered rows per L1 invariant.

One producer test per L1 invariant. Each producer:

1. Loads per-dialect cfg.
2. Opens the deployed L1 dashboard via `QsEmbedDriver`.
3. Walks the sheet for this invariant.
4. Writes the rendered row keys + count as a JSON artifact
   (`qs_browser/<inv>_qs_rows.json`).

The high-watermark validator in this same tier dir reads this
artifact + the db tier's / app2 tier's artifacts and asserts the
4-way agreement chain.

**Per-leg degradation, not per-test skip (X.2.j.C):** when QS is
unavailable for this dialect (SQLite cell — no QS dashboard; or
`RECON_E2E_USER_ARN` unset), the producer writes an artifact with
`qs_available: false` so the validator runs the chain WITHOUT the
QS leg (a clean 3-way: scenario ⊆ direct == App2 == PDF*). Same
shape as the pre-CB.5 monolithic test handled it (the QS leg
yielded `None` and the test ran 3-way).

Module-scoped QsEmbedDriver isn't possible — embed URLs are
single-use; the driver opens a fresh page per test. The L1 dashboard
ID lookup is per-dialect, deployed under the matching cfg's
`deployment_name`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import pytest

from tests.audit._dashboard_extract import (
    count_l1_invariant_rows,
    l1_invariant_row_keys,
    l1_invariant_rows_seen,
)
from tests._marks import IsolationScope, isolation_consumer  # noqa: E402
from tests.e2e._agreement import write_rendered_rows
from tests.e2e._agreement_helpers import (
    ALL_L1_INVARIANTS,
    FLAT_SHAPE_INVARIANTS,
    audit_window,
    load_dialect_cfg,
    today_anchor,
)

if TYPE_CHECKING:
    from mypy_boto3_quicksight.client import QuickSightClient

    from recon_gen.common.config import Config
    from recon_gen.common.sql import Dialect
    from tests.e2e._drivers import QsEmbedDriver


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
def qs_dashboard_id(
    dialect_cfg: "tuple[Config, Path, Dialect]",
) -> str:
    """L1 dashboard ID under this dialect's deployment_name prefix
    (Z.C: `<deployment_name>-l1-dashboard`)."""
    cfg, _, _ = dialect_cfg
    return cfg.prefixed("l1-dashboard")


@pytest.fixture(scope="module")
def dialect_qs_client(
    dialect_cfg: "tuple[Config, Path, Dialect]",
) -> "QuickSightClient":
    """Boto3 QuickSight client for this dialect's dashboard region.

    CB.7-followup (2026-06-02): renamed from `qs_client` to avoid
    shadowing the session-scoped `qs_client` in
    `tests/e2e/conftest.py`. The session-autouse
    `_qs_pre_warm_dashboards` does `request.getfixturevalue("qs_client")`
    and with the shadow it found this module-scoped one + raised
    `ScopeMismatch` at every test setup. The cascade was masking
    that — now exposed by the loadgroup unwind.
    """
    import boto3
    cfg, _, _ = dialect_cfg
    return boto3.client(  # pyright: ignore[reportUnknownMemberType]: boto3.client dynamic service overload
        "quicksight", region_name=cfg.aws_region,
    )


@pytest.fixture
def qs_driver(
    request: pytest.FixtureRequest,
    dialect_cfg: "tuple[Config, Path, Dialect]",
    qs_dashboard_id: str,
    dialect_qs_client: "QuickSightClient",
) -> "Iterator[QsEmbedDriver | None]":
    """Function-scoped QS driver. Yields `None` when QS is
    unavailable (dashboard not deployed, `RECON_E2E_USER_ARN` unset);
    the producer then writes the no-QS-leg sentinel artifact.
    """
    cfg, _, _ = dialect_cfg
    try:
        dialect_qs_client.describe_dashboard(
            AwsAccountId=cfg.aws_account_id,
            DashboardId=qs_dashboard_id,
        )
    except dialect_qs_client.exceptions.ResourceNotFoundException:
        yield None
        return
    from tests.e2e._drivers._lifecycle import qs_driver_or_none

    with qs_driver_or_none(
        request,
        cfg=cfg,
        account_id=cfg.aws_account_id,
        region=cfg.aws_region,
        viewport=(1600, 4000),
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


@pytest.mark.parametrize("invariant", ALL_L1_INVARIANTS)
def test_l1_invariant_qs_extract(
    qs_driver: "QsEmbedDriver | None",
    qs_dashboard_id: str,
    invariant: str,
) -> None:
    """Read QS's rendered rows for one L1 invariant; write the
    artifact the validator reads.

    Skips degrade per-leg, not per-test: when `qs_driver is None`,
    the producer writes an artifact with `qs_available: false` so
    the validator handles the missing-leg case rather than a hard
    artifact-not-found error.
    """
    if qs_driver is None:
        write_rendered_rows("qs_browser", f"{invariant}_qs_rows", [])
        write_rendered_rows("qs_browser", f"{invariant}_qs_meta", [
            {"qs_available": False, "qs_count": None},
        ])
        pytest.skip(
            f"QS unavailable for this dialect — wrote no-leg sentinel; "
            f"validator runs the chain without QS."
        )

    qs_driver.open(qs_dashboard_id)
    qs_count = count_l1_invariant_rows(qs_driver, invariant, _PERIOD)

    payload: list[dict[str, object]] = []
    if invariant in FLAT_SHAPE_INVARIANTS:
        # BO.1 fix: the pre-fix `qs_seen == qs_count` guard was a
        # virtualization-truncation check that used a SECOND fetch via
        # `l1_invariant_rows_seen`. Post-BO.1 both helpers go through
        # `driver.table_rows_full` (de-virtualized scroll-collect), so
        # the comparison is structurally vacuous AND the second fetch
        # invited a page-size-dropdown race when the re-navigation
        # mid-fetch dropped the bump. Truncation protection now lives
        # in `table_rows_full` itself; if scroll-collect saw less than
        # the page-size-bumped count, that's an implementation bug, not
        # a per-test guard.
        # B.3-followon: extending QS-side row-identity comparison to
        # overdraft / limit_breach needs a deployed dashboard to
        # confirm those tables' day-column projection. Drift is the
        # one the X.2.j.0 spike concretely verified — for it we
        # extract keys; for the other flat-shape ones the validator
        # falls back to count-only.
        if invariant == "drift":
            qs_keys = l1_invariant_row_keys(qs_driver, invariant, _PERIOD)
            for key_tuple in _serialize_keys(qs_keys):
                payload.append({"natural_key": key_tuple})

    write_rendered_rows("qs_browser", f"{invariant}_qs_rows", payload)
    write_rendered_rows("qs_browser", f"{invariant}_qs_meta", [
        {"qs_available": True, "qs_count": qs_count},
    ])
