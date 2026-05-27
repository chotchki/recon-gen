# pyright: reportTypedDictNotRequiredAccess=false
"""API tests: verify the Executives dashboard/analysis/datasets exist."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from mypy_boto3_quicksight.client import QuickSightClient


pytestmark = [pytest.mark.e2e, pytest.mark.api]


class TestExecDashboardExists:
    def test_dashboard_status(self, qs_client: "QuickSightClient", account_id: str, exec_dashboard_id: str) -> None:
        resp = qs_client.describe_dashboard(
            AwsAccountId=account_id,
            DashboardId=exec_dashboard_id,
        )
        status = resp["Dashboard"]["Version"]["Status"]
        assert status == "CREATION_SUCCESSFUL", (
            f"Executives dashboard status is {status}, "
            "expected CREATION_SUCCESSFUL"
        )

    def test_dashboard_has_name(
        self, qs_client: "QuickSightClient", account_id: str, exec_dashboard_id: str,
    ) -> None:
        resp = qs_client.describe_dashboard(
            AwsAccountId=account_id,
            DashboardId=exec_dashboard_id,
        )
        assert len(resp["Dashboard"]["Name"]) > 0


class TestExecAnalysisExists:
    def test_analysis_status(self, qs_client: "QuickSightClient", account_id: str, exec_analysis_id: str) -> None:
        resp = qs_client.describe_analysis(
            AwsAccountId=account_id,
            AnalysisId=exec_analysis_id,
        )
        status = resp["Analysis"]["Status"]
        assert status == "CREATION_SUCCESSFUL", (
            f"Executives analysis status is {status}, "
            "expected CREATION_SUCCESSFUL"
        )


class TestExecDatasetsExist:
    def test_all_datasets_exist(
        self, qs_client: "QuickSightClient", account_id: str, exec_dataset_ids: list[str],
    ) -> None:
        for ds_id in exec_dataset_ids:
            resp = qs_client.describe_data_set(
                AwsAccountId=account_id,
                DataSetId=ds_id,
            )
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200, (
                f"Executives dataset {ds_id} not found"
            )

    # test_dataset_count was redundant after v8.8.0a23 made
    # `exec_dataset_ids` itself derive from `exec_app.datasets` —
    # the assertion `N == hardcoded` always drifts when the dataset
    # set changes (Y.2.h added exec-account-summary-active-ds; would
    # add 3 to the count). The remaining `test_all_datasets_exist`
    # iterates the derived IDs and calls `describe_data_set` on each,
    # which IS the meaningful check ("every tree-registered dataset
    # is actually deployed in QS"). Test removed 2026-05-11.
