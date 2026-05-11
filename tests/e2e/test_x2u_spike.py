"""X.2.u.1 — SPIKE: parametrized `[qs, app2]` driver fixture shape.

Proves the fixture design before X.2.u.2 ports the real structural tests
onto it. The fixture yields ``(driver, dashboard_arg)``:

- ``qs`` param — drives the *deployed* L1 dashboard (real DB data via
  the QS datasource); ``dashboard_arg`` is the deployed dashboard ID.
  Skips when ``QS_E2E_USER_ARN`` is unset or the dashboard isn't
  deployed.
- ``app2`` param — drives a *locally-spun* App2 server built from the
  *same* tree (`l1_app`), reading the *same* DB via
  ``make_live_db_fetcher_for_app`` (dialect via ``cfg.dialect``). So
  both surfaces see identical data — the "scenario → DB → output"
  pipeline, App2 as the third output. Skips when ``cfg.demo_database_url``
  is unset.

This spike test uses only verbs that already work on both drivers
(``open`` / ``sheet_names``) — no ``goto_sheet`` (the App2 impl is
id-based vs the QS impl name-based; reconciling that is X.2.u.2 work,
when the TreeValidator-style tests actually hit it).

Gated by ``QS_GEN_E2E=1`` like every other tests/e2e/ file. Delete this
file once X.2.u.2 lands the real parametrized structural tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.e2e._drivers import App2Driver
from tests.e2e._harness_html2 import make_live_db_fetcher_for_app


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.fixture(params=["qs", "app2"])
def l1_driver(
    request, cfg, region, account_id, l1_dashboard_id, l1_app,
) -> Iterator[tuple[Any, str]]:
    """``(driver, dashboard_arg)`` parametrized over the two renderers.

    QS leg: skip if no user ARN / dashboard not deployed. App2 leg: skip
    if no DB URL. Both legs back onto the same `l1_app` tree + (for
    app2) the same DB the deployed dashboard reads."""
    if request.param == "qs":
        from quicksight_gen.common.browser.helpers import get_user_arn
        from tests.e2e._drivers import QsEmbedDriver

        try:
            get_user_arn()
        except RuntimeError as exc:
            pytest.skip(str(exc))
        # Pre-flight: confirm the dashboard exists (else the embed loads
        # as an empty QS error page that times out the "wait for tabs").
        import boto3
        qs = boto3.client("quicksight", region_name=region)
        try:
            qs.describe_dashboard(
                AwsAccountId=account_id, DashboardId=l1_dashboard_id,
            )
        except qs.exceptions.ResourceNotFoundException:
            pytest.skip(
                f"L1 dashboard {l1_dashboard_id!r} not deployed in "
                f"{account_id}/{region}; deploy it first."
            )
        with QsEmbedDriver.embed(
            aws_account_id=account_id, aws_region=region,
        ) as d:
            yield d, l1_dashboard_id
    else:  # app2
        if not cfg.demo_database_url:
            pytest.skip(
                "no cfg.demo_database_url — the app2 leg reads the same DB "
                "the deployed dashboard does"
            )
        assert l1_app.analysis is not None
        fetcher = make_live_db_fetcher_for_app(tree_app=l1_app, cfg=cfg)
        primary_sheet = l1_app.analysis.sheets[0]
        with App2Driver.serving(
            tree_app=l1_app, sheet=primary_sheet,
            data_fetcher=fetcher, dashboard_id="l1",
            dashboard_title="L1 Reconciliation (live)",
        ) as d:
            yield d, "l1"


def test_l1_landing_lists_declared_sheets(
    l1_driver: tuple[Any, str], l1_app,
) -> None:
    """The dashboard's tab strip names every analysis sheet the tree
    declares — verified identically against the deployed QS dashboard
    and the locally-spun App2 server, off the same `l1_app` tree."""
    driver, dashboard_arg = l1_driver
    driver.open(dashboard_arg)
    declared = {s.name for s in l1_app.analysis.sheets}
    rendered = set(driver.sheet_names())
    missing = declared - rendered
    assert not missing, (
        f"{driver.dialect}: tab strip missing sheets {sorted(missing)} — "
        f"declared={sorted(declared)}, rendered={sorted(rendered)}"
    )
