"""API tests: validate the deployed Executives dashboard definition.

Four sheets: Getting Started + Account Coverage + Transaction Volume
Over Time + Money Moved. The Account Coverage sheet's visual-pinned
``activity_count >= 1`` filter on the Active KPI + Active bar is
load-bearing — without it both visuals would count every account
(open + active become identical).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from mypy_boto3_quicksight.client import QuickSightClient
    from mypy_boto3_quicksight.type_defs import (
        DashboardVersionDefinitionOutputTypeDef,
    )


    from recon_gen.common.tree import App
pytestmark = [pytest.mark.e2e, pytest.mark.api]


@pytest.fixture(scope="module")
def exec_dashboard_definition(
    qs_client: "QuickSightClient",
    account_id: str,
    exec_dashboard_id: str,
) -> "DashboardVersionDefinitionOutputTypeDef":
    resp = qs_client.describe_dashboard_definition(
        AwsAccountId=account_id,
        DashboardId=exec_dashboard_id,
    )
    return resp["Definition"]


def _visual_ids(sheet: dict) -> list[str]:
    out: list[str] = []
    for v in sheet.get("Visuals", []):
        for vtype in v.values():
            if isinstance(vtype, dict) and "VisualId" in vtype:
                out.append(vtype["VisualId"])
    return out


class TestSheets:
    EXPECTED_NAMES = [
        "Getting Started",
        "Account Coverage",
        "Transaction Volume Over Time",
        "Money Moved",
        "Info",  # M.4.4.5 — App Info canary, always last
    ]

    def test_has_five_sheets(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        assert len(exec_dashboard_definition["Sheets"]) == 5

    def test_sheet_order(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        names = [s["Name"] for s in exec_dashboard_definition["Sheets"]]
        assert names == self.EXPECTED_NAMES

    def test_every_sheet_has_description(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        for sheet in exec_dashboard_definition["Sheets"]:
            desc = sheet.get("Description", "")
            assert len(desc) > 20, (
                f"Sheet '{sheet['Name']}' missing description"
            )


class TestVisuals:
    EXPECTED_VISUAL_COUNTS = {
        # Getting Started is text-only (welcome + clickability legend +
        # 3 per-sheet description blocks).
        "Account Coverage": 5,            # 2 KPIs + 2 bars + table
        # v11.22.3 BH.8 follow-up — 3 KPIs (Total Transactions + Transfer
        # Legs sibling + Avg Daily Volume) + 2 bars.
        "Transaction Volume Over Time": 5,
        "Money Moved": 4,                  # 2 KPIs + daily bar + per-type bar
    }

    def test_visual_counts_per_sheet(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        for sheet in exec_dashboard_definition["Sheets"]:
            name = sheet["Name"]
            expected = self.EXPECTED_VISUAL_COUNTS.get(name)
            if expected is None:
                continue
            actual = len(sheet.get("Visuals", []))
            assert actual == expected, (
                f"Sheet '{name}' has {actual} visuals, expected {expected}"
            )

    def test_all_visual_ids_unique(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        all_ids: list[str] = []
        for sheet in exec_dashboard_definition["Sheets"]:
            all_ids.extend(_visual_ids(sheet))
        assert len(all_ids) == len(set(all_ids)), (
            f"Duplicate visual IDs: "
            f"{[vid for vid in all_ids if all_ids.count(vid) > 1]}"
        )

    def test_every_visual_has_subtitle(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        for sheet in exec_dashboard_definition["Sheets"]:
            for v in sheet.get("Visuals", []):
                for vtype in v.values():
                    if not (isinstance(vtype, dict) and "VisualId" in vtype):
                        continue
                    text = (
                        vtype.get("Subtitle", {})
                             .get("FormatText", {})
                             .get("PlainText", "")
                    )
                    assert len(text) > 10, (
                        f"Visual '{vtype['VisualId']}' missing subtitle"
                    )


class TestParameters:
    def _names(self, definition: dict) -> set[str]:
        names: set[str] = set()
        for p in definition.get("ParameterDeclarations", []):
            for decl in p.values():
                if isinstance(decl, dict) and "Name" in decl:
                    names.add(decl["Name"])
        return names

    def test_all_parameters_declared(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        exec_app: "App",
    ) -> None:
        # Executives has no parameters today — no cross-app drills
        # (L.6.7 dropped per QS URL parameter sync defect), no UI
        # parameter controls. The tree-walked set is the source of
        # truth; if a param ever gets added, this catches the
        # deploy-side miss.
        expected = {str(p.name) for p in exec_app.analysis.parameters}
        assert self._names(exec_dashboard_definition) == expected


class TestFilterGroups:
    def test_filter_group_ids(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        exec_app: "App",
    ) -> None:
        groups = exec_dashboard_definition.get("FilterGroups", [])
        deployed = {g["FilterGroupId"] for g in groups}
        expected = {
            str(fg.filter_group_id)
            for fg in exec_app.analysis.filter_groups
        }
        assert deployed == expected

    def test_active_only_filter_dropped_after_y2h(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ):
        """Y.2.h — `fg-exec-account-active-only` is gone. The
        `activity_count >= 1` narrowing now lives in the
        `exec-account-summary-active-ds` SQL (`WHERE COALESCE(
        activity_count, 0) > 0`); the Active KPI + bar source from
        that dataset directly. No visual-pinned filter needed —
        QS + App2 see one shape."""
        groups = exec_dashboard_definition.get("FilterGroups", [])
        legacy_fg_ids = [
            g["FilterGroupId"] for g in groups
            if g["FilterGroupId"] == "fg-exec-account-active-only"
        ]
        assert legacy_fg_ids == [], (
            "fg-exec-account-active-only should be gone after Y.2.h "
            "dataset split"
        )

    def test_filter_group_ids_unique(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        groups = exec_dashboard_definition.get("FilterGroups", [])
        ids = [g["FilterGroupId"] for g in groups]
        assert len(ids) == len(set(ids))


class TestDatasetDeclarations:
    def test_all_datasets_declared(
        self,
        exec_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        exec_dataset_ids: list[str],
    ) -> None:
        decls = exec_dashboard_definition["DataSetIdentifierDeclarations"]
        declared_ds_ids = {d["DataSetArn"].split("/")[-1] for d in decls}
        for ds_id in exec_dataset_ids:
            assert ds_id in declared_ds_ids, (
                f"Executives dataset {ds_id} not declared in dashboard"
            )
