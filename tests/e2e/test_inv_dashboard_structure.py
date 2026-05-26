"""API tests: validate the deployed Investigation dashboard definition.

Five sheets: Getting Started + four investigation surfaces (Recipient
Fanout / Volume Anomalies / Money Trail / Account Network). The
Account Network sheet's two side-by-side directional Sankeys are the
load-bearing K.4.8 invariant — both must declare distinct titles so
the layout encodes direction in geometry rather than in node position
inside one big Sankey.

Per L.1.16 internal visual IDs are auto-derived; analyst-facing titles
stay explicit. Identity assertions therefore key off `Title.PlainText`
(stable across the imperative→tree port), not `VisualId` (regenerated
positionally).
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
def inv_dashboard_definition(
    qs_client: "QuickSightClient",
    account_id: str,
    inv_dashboard_id: str,
) -> "DashboardVersionDefinitionOutputTypeDef":
    resp = qs_client.describe_dashboard_definition(
        AwsAccountId=account_id,
        DashboardId=inv_dashboard_id,
    )
    return resp["Definition"]


# L.11.1 — `inv_app` fixture promoted to session scope in conftest.py.


def _visual_ids(sheet: dict) -> list[str]:
    out: list[str] = []
    for v in sheet.get("Visuals", []):
        for vtype in v.values():
            if isinstance(vtype, dict) and "VisualId" in vtype:
                out.append(vtype["VisualId"])
    return out


def _visual_titles(sheet: dict) -> set[str]:
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


def _tree_visual_titles(inv_app: "App", sheet_name: str) -> set[str]:
    sheet = next(
        s for s in inv_app.analysis.sheets if s.name == sheet_name
    )
    return {v.title for v in sheet.visuals if getattr(v, "title", None)}


class TestSheets:
    EXPECTED_NAMES = [
        "Getting Started",
        "Recipient Fanout",
        "Volume Anomalies",
        "Money Trail",
        "Account Network",
        "Info",  # M.4.4.5 — App Info canary, always last
    ]

    def test_has_six_sheets(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        assert len(inv_dashboard_definition["Sheets"]) == 6

    def test_sheet_order(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        names = [s["Name"] for s in inv_dashboard_definition["Sheets"]]
        assert names == self.EXPECTED_NAMES

    def test_every_sheet_has_description(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        for sheet in inv_dashboard_definition["Sheets"]:
            desc = sheet.get("Description", "")
            assert len(desc) > 20, (
                f"Sheet '{sheet['Name']}' missing description"
            )


class TestVisuals:
    EXPECTED_VISUAL_COUNTS = {
        # Getting Started is text-only (welcome + roadmap text boxes).
        "Recipient Fanout": 4,       # 3 KPIs + table
        "Volume Anomalies": 3,       # KPI + distribution chart + table
        "Money Trail": 2,            # Sankey + hop-by-hop table
        "Account Network": 3,        # inbound Sankey + outbound Sankey + table
    }

    def test_visual_counts_per_sheet(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        for sheet in inv_dashboard_definition["Sheets"]:
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
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        all_ids: list[str] = []
        for sheet in inv_dashboard_definition["Sheets"]:
            all_ids.extend(_visual_ids(sheet))
        assert len(all_ids) == len(set(all_ids)), (
            f"Duplicate visual IDs: "
            f"{[vid for vid in all_ids if all_ids.count(vid) > 1]}"
        )

    def test_every_visual_has_subtitle(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        for sheet in inv_dashboard_definition["Sheets"]:
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

    def test_money_trail_has_sankey_and_table(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        inv_app: "App",
    ) -> None:
        sheet = next(
            s for s in inv_dashboard_definition["Sheets"]
            if s["Name"] == "Money Trail"
        )
        deployed = _visual_titles(sheet)
        expected = _tree_visual_titles(inv_app, "Money Trail")
        missing = expected - deployed
        assert not missing, (
            f"Money Trail missing visual titles: {sorted(missing)}"
        )

    def test_account_network_has_two_directional_sankeys_and_table(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        inv_app: "App",
    ) -> None:
        """K.4.8i invariant — direction must be encoded in geometry. A
        regression that drops one Sankey or merges them back into one
        omnidirectional view would silently put the analyst back into the
        anchor-disambiguation problem the redesign solved.

        The two distinct titles (inbound counterparties → anchor / outbound
        anchor → counterparties) are the analyst-facing direction signal.
        """
        sheet = next(
            s for s in inv_dashboard_definition["Sheets"]
            if s["Name"] == "Account Network"
        )
        deployed = _visual_titles(sheet)
        expected = _tree_visual_titles(inv_app, "Account Network")
        missing = expected - deployed
        assert not missing, (
            f"Account Network missing visual titles: {sorted(missing)}"
        )
        for direction_marker in ("Inbound", "Outbound"):
            assert any(direction_marker in t for t in deployed), (
                f"Account Network missing a {direction_marker!r}-titled "
                f"visual; rendered titles: {sorted(deployed)}"
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
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        inv_app: "App",
    ) -> None:
        # The tree's parameter set is the source of truth — deployed must
        # match exactly. K.4.3 fanout-threshold + K.4.4 anomalies-sigma +
        # K.4.5 money-trail-root + max-hops + min-amount + K.4.8 anchor +
        # min-amount = 7 today; the assert tracks adds/removes automatically.
        expected = {str(p.name) for p in inv_app.analysis.parameters}
        assert self._names(inv_dashboard_definition) == expected


class TestFilterGroups:
    def test_filter_group_ids(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        inv_app: "App",
    ) -> None:
        groups = inv_dashboard_definition.get("FilterGroups", [])
        deployed = {g["FilterGroupId"] for g in groups}
        expected = {str(fg.filter_group_id) for fg in inv_app.analysis.filter_groups}
        assert deployed == expected

    def test_filter_group_ids_unique(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
    ) -> None:
        groups = inv_dashboard_definition.get("FilterGroups", [])
        ids = [g["FilterGroupId"] for g in groups]
        assert len(ids) == len(set(ids))


class TestDatasetDeclarations:
    def test_all_datasets_declared(
        self,
        inv_dashboard_definition: "DashboardVersionDefinitionOutputTypeDef",
        inv_dataset_ids: list[str],
    ) -> None:
        decls = inv_dashboard_definition["DataSetIdentifierDeclarations"]
        declared_ds_ids = {d["DataSetArn"].split("/")[-1] for d in decls}
        for ds_id in inv_dataset_ids:
            assert ds_id in declared_ds_ids, (
                f"Investigation dataset {ds_id} not declared in dashboard"
            )
