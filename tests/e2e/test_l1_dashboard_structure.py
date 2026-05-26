"""API tests: validate the deployed L1 dashboard definition.

M.2c.3. EVERY assertion derives from the `l1_app` tree per the
no-hardcoded-data rule — sheet names, visual titles, parameter names,
filter-group IDs all walk the tree as the source of truth. If the
deployed definition diverges from the tree the assertion fires;
nothing here knows about specific Sasquatch values.
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
def l1_dashboard_definition(
    qs_client: "QuickSightClient",
    account_id: str,
    l1_dashboard_id: str,
) -> "DashboardVersionDefinitionOutputTypeDef":
    resp = qs_client.describe_dashboard_definition(
        AwsAccountId=account_id,
        DashboardId=l1_dashboard_id,
    )
    return resp["Definition"]


def _visual_titles(sheet: dict) -> set[str]:
    """Pull the analyst-facing titles off every visual on a sheet."""
    out: set[str] = set()
    for v in sheet.get("Visuals", []):
        for vtype in v.values():
            if not isinstance(vtype, dict) or "VisualId" not in vtype:
                continue
            text = (
                vtype.get("Title", {})
                     .get("FormatText", {})
                     .get("PlainText", "")
            )
            if text:
                out.add(text)
    return out


def _tree_visual_titles(l1_app: "App", sheet_name: str) -> set[str]:
    sheet = next(
        s for s in l1_app.analysis.sheets if s.name == sheet_name
    )
    return {v.title for v in sheet.visuals if getattr(v, "title", None)}


# -- Sheets ------------------------------------------------------------------


class TestSheets:
    def test_sheet_count_matches_tree(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        l1_app: "App",
    ) -> None:
        deployed = len(l1_dashboard_definition["Sheets"])
        expected = len(l1_app.analysis.sheets)
        assert deployed == expected

    def test_sheet_order_matches_tree(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        l1_app: "App",
    ) -> None:
        deployed = [s["Name"] for s in l1_dashboard_definition["Sheets"]]
        expected = [s.name for s in l1_app.analysis.sheets]
        assert deployed == expected

    def test_every_sheet_has_description(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        for sheet in l1_dashboard_definition["Sheets"]:
            desc = sheet.get("Description", "")
            assert len(desc) > 20, (
                f"Sheet {sheet['Name']!r} missing description"
            )


# -- Visuals -----------------------------------------------------------------


class TestVisuals:
    def test_visual_titles_match_tree(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        l1_app: "App",
    ) -> None:
        """For every sheet on the deployed dashboard, the visual titles
        must include every title declared on the corresponding sheet in
        the tree. (Deployed may carry extra titles from the model
        layer; the invariant is tree-titles ⊆ deployed-titles.)"""
        missing_per_sheet: dict[str, set[str]] = {}
        for sheet in l1_dashboard_definition["Sheets"]:
            name = sheet["Name"]
            deployed = _visual_titles(sheet)
            expected = _tree_visual_titles(l1_app, name)
            missing = expected - deployed
            if missing:
                missing_per_sheet[name] = missing
        assert not missing_per_sheet, (
            f"Sheets missing tree-declared visual titles: "
            f"{missing_per_sheet}"
        )

    def test_visual_count_matches_tree(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        l1_app: "App",
    ) -> None:
        for sheet in l1_dashboard_definition["Sheets"]:
            name = sheet["Name"]
            tree_sheet = next(
                s for s in l1_app.analysis.sheets if s.name == name
            )
            deployed_count = len(sheet.get("Visuals", []))
            expected_count = len(tree_sheet.visuals)
            assert deployed_count == expected_count, (
                f"Sheet {name!r} has {deployed_count} visuals deployed, "
                f"tree expects {expected_count}"
            )

    def test_every_visual_has_subtitle(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        for sheet in l1_dashboard_definition["Sheets"]:
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
                        f"Visual {vtype['VisualId']!r} missing subtitle"
                    )


# -- Parameters --------------------------------------------------------------


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
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        l1_app: "App",
    ) -> None:
        """Tree's parameter set is the source of truth — deployed must
        match exactly. M.2b.1 added P_L1_DATE_START + P_L1_DATE_END;
        if M.2b.4+ adds parameters they show up here automatically."""
        deployed = self._names(l1_dashboard_definition)
        expected = {str(p.name) for p in l1_app.analysis.parameters}
        assert deployed == expected


# -- Filter groups -----------------------------------------------------------


class TestFilterGroups:
    def test_filter_group_ids_match_tree(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        l1_app: "App",
    ) -> None:
        groups = l1_dashboard_definition.get("FilterGroups", [])
        deployed = {g["FilterGroupId"] for g in groups}
        expected = {
            str(fg.filter_group_id) for fg in l1_app.analysis.filter_groups
        }
        assert deployed == expected

    def test_filter_group_ids_unique(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        groups = l1_dashboard_definition.get("FilterGroups", [])
        ids = [g["FilterGroupId"] for g in groups]
        assert len(ids) == len(set(ids))


# -- Dataset declarations ----------------------------------------------------


class TestDatasetDeclarations:
    def test_all_datasets_declared(
        self,
        l1_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        l1_dataset_ids: list[str],
    ) -> None:
        """Every L1 dataset id (derived from resource_prefix) must
        appear in the dashboard's DataSetIdentifierDeclarations."""
        decls = l1_dashboard_definition["DataSetIdentifierDeclarations"]
        declared_ds_ids = {
            d["DataSetArn"].split("/")[-1] for d in decls
        }
        for ds_id in l1_dataset_ids:
            assert ds_id in declared_ds_ids, (
                f"L1 dataset {ds_id} not declared in dashboard"
            )
