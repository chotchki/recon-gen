"""API tests: verify the L1 dashboard / analysis / datasets exist.

M.2c.2. Mirrors `test_inv_deployed_resources.py`. No data assertions —
this layer only checks AWS resources are present + healthy. Resource
counts derive from the `l1_app` tree (no hardcoded 5).
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.api]


class TestL1DashboardExists:
    def test_dashboard_status(self, qs_client, account_id: str, l1_dashboard_id: str) -> None:
        resp = qs_client.describe_dashboard(
            AwsAccountId=account_id,
            DashboardId=l1_dashboard_id,
        )
        status = resp["Dashboard"]["Version"]["Status"]
        assert status == "CREATION_SUCCESSFUL", (
            f"L1 dashboard status is {status}, "
            "expected CREATION_SUCCESSFUL"
        )

    def test_dashboard_has_name(
        self, qs_client, account_id: str, l1_dashboard_id: str,
    ) -> None:
        resp = qs_client.describe_dashboard(
            AwsAccountId=account_id,
            DashboardId=l1_dashboard_id,
        )
        assert len(resp["Dashboard"]["Name"]) > 0


class TestL1AnalysisExists:
    def test_analysis_status(self, qs_client, account_id: str, l1_analysis_id: str) -> None:
        resp = qs_client.describe_analysis(
            AwsAccountId=account_id,
            AnalysisId=l1_analysis_id,
        )
        status = resp["Analysis"]["Status"]
        assert status == "CREATION_SUCCESSFUL", (
            f"L1 analysis status is {status}, "
            "expected CREATION_SUCCESSFUL"
        )


class TestL1DatasetsExist:
    def test_all_datasets_exist(
        self, qs_client, account_id: str, l1_dataset_ids: list[str],
    ) -> None:
        for ds_id in l1_dataset_ids:
            resp = qs_client.describe_data_set(
                AwsAccountId=account_id,
                DataSetId=ds_id,
            )
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200, (
                f"L1 dataset {ds_id} not found"
            )

    # test_dataset_count_matches_tree was redundant after v8.8.0a23
    # made `l1_dataset_ids` itself derive from `l1_app.datasets` —
    # the assertion `N == N` would always be true. The remaining
    # `test_all_datasets_exist` above iterates the derived IDs and
    # calls `describe_data_set` on each, which IS the meaningful
    # check ("every tree-registered dataset is actually deployed in
    # QS"). Test removed 2026-05-11 as a fixture-drift cleanup.
