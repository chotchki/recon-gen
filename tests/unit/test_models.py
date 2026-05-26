"""Unit tests for model serialization."""

import json

from recon_gen.common.models import (
    Analysis,
    AnalysisDefinition,
    BarChartAggregatedFieldWells,
    BarChartConfiguration,
    BarChartFieldWells,
    BarChartVisual,
    CategoryFilter,
    CategoryFilterConfiguration,
    CategoricalDimensionField,
    ColumnIdentifier,
    CredentialPair,
    CustomSql,
    DataSet,
    DataSetIdentifierDeclaration,
    DataSource,
    DataSourceCredentials,
    DataSourceParameters,
    DimensionField,
    Filter,
    FilterGroup,
    FilterScopeConfiguration,
    InputColumn,
    KPIConfiguration,
    KPIFieldWells,
    KPIVisual,
    MeasureField,
    NumericalAggregationFunction,
    NumericalMeasureField,
    PhysicalTable,
    PieChartAggregatedFieldWells,
    PieChartConfiguration,
    PieChartFieldWells,
    PieChartVisual,
    PostgreSqlParameters,
    SelectedSheetsFilterScopeConfiguration,
    SheetDefinition,
    SheetVisualScopingConfiguration,
    TableConfiguration,
    TableFieldWells,
    TableUnaggregatedFieldWells,
    TableVisual,
    Tag,
    Theme,
    ThemeConfiguration,
    DataColorPalette,
    UIColorPalette,
    Visual,
    VisualTitleLabelOptions,
)
from recon_gen.common.cleanup import (
    DEPLOYMENT_TAG_KEY,
    MANAGED_TAG_KEY,
    MANAGED_TAG_VALUE,
)
from recon_gen.common.config import Config
from tests._test_helpers import make_test_config
from recon_gen.common.datasource import build_datasource


class TestStripNones:
    def test_none_keys_removed(self):
        ds = DataSet(
            AwsAccountId="123",
            DataSetId="ds-1",
            Name="Test",
            PhysicalTableMap={},
            LogicalTableMap=None,
        )
        out = ds.to_aws_json()
        assert "LogicalTableMap" not in out

    def test_nested_none_keys_removed(self):
        kpi = KPIVisual(
            VisualId="kpi-1",
            Title=VisualTitleLabelOptions(FormatText={"PlainText": "X"}),
            Subtitle=None,
        )
        visual = Visual(KPIVisual=kpi)
        out = visual.to_aws_json() if hasattr(visual, "to_aws_json") else {}
        # Use the internal helper directly
        from recon_gen.common.models import _strip_nones, asdict
        out = _strip_nones(asdict(visual))
        assert "Subtitle" not in out["KPIVisual"]
        assert "BarChartVisual" not in out


class TestThemeSerialization:
    def test_roundtrip_json(self):
        theme = Theme(
            AwsAccountId="123456789012",
            ThemeId="test-theme",
            Name="Test",
            BaseThemeId="CLASSIC",
            Configuration=ThemeConfiguration(
                DataColorPalette=DataColorPalette(Colors=["#000", "#FFF"]),
                UIColorPalette=UIColorPalette(PrimaryBackground="#FFFFFF"),
            ),
        )
        raw = theme.to_json_string()
        parsed = json.loads(raw)
        assert parsed["ThemeId"] == "test-theme"
        assert parsed["Configuration"]["DataColorPalette"]["Colors"] == ["#000", "#FFF"]

    def test_required_fields_present(self):
        theme = Theme(
            AwsAccountId="123",
            ThemeId="t",
            Name="T",
            BaseThemeId="CLASSIC",
            Configuration=ThemeConfiguration(),
        )
        out = theme.to_aws_json()
        for key in ("AwsAccountId", "ThemeId", "Name", "BaseThemeId", "Configuration"):
            assert key in out


class TestDataSetSerialization:
    def test_custom_sql_structure(self):
        ds = DataSet(
            AwsAccountId="123",
            DataSetId="ds-1",
            Name="Test DS",
            PhysicalTableMap={
                "table1": PhysicalTable(
                    CustomSql=CustomSql(
                        Name="SQL",
                        DataSourceArn="arn:aws:quicksight:us-east-1:123:datasource/x",
                        SqlQuery="SELECT 1",
                        Columns=[InputColumn(Name="id", Type="INTEGER")],
                    )
                )
            },
        )
        out = ds.to_aws_json()
        sql = out["PhysicalTableMap"]["table1"]["CustomSql"]
        assert sql["SqlQuery"] == "SELECT 1"
        assert sql["Columns"][0]["Name"] == "id"
        assert sql["Columns"][0]["Type"] == "INTEGER"

    def test_import_mode_default(self):
        ds = DataSet(
            AwsAccountId="123",
            DataSetId="ds-1",
            Name="Test",
            PhysicalTableMap={},
        )
        assert ds.to_aws_json()["ImportMode"] == "DIRECT_QUERY"


class TestAnalysisSerialization:
    def test_minimal_analysis(self):
        analysis = Analysis(
            AwsAccountId="123",
            AnalysisId="a-1",
            Name="Test",
            Definition=AnalysisDefinition(
                DataSetIdentifierDeclarations=[
                    DataSetIdentifierDeclaration(Identifier="ds", DataSetArn="arn:x")
                ],
                Sheets=[SheetDefinition(SheetId="s1", Name="Sheet 1")],
            ),
        )
        out = analysis.to_aws_json()
        assert out["AnalysisId"] == "a-1"
        assert len(out["Definition"]["Sheets"]) == 1
        assert "ThemeArn" not in out  # None should be stripped


class TestVisualSerialization:
    def test_kpi_visual(self):
        from recon_gen.common.models import _strip_nones, asdict
        kpi = Visual(
            KPIVisual=KPIVisual(
                VisualId="kpi-1",
                Title=VisualTitleLabelOptions(FormatText={"PlainText": "Test KPI"}),
                ChartConfiguration=KPIConfiguration(
                    FieldWells=KPIFieldWells(
                        Values=[
                            MeasureField(
                                NumericalMeasureField=NumericalMeasureField(
                                    FieldId="f1",
                                    Column=ColumnIdentifier(
                                        DataSetIdentifier="ds",
                                        ColumnName="amount",
                                    ),
                                    AggregationFunction=NumericalAggregationFunction(
                                        SimpleNumericalAggregation="SUM"
                                    ),
                                )
                            )
                        ],
                    ),
                ),
            )
        )
        out = _strip_nones(asdict(kpi))
        assert "KPIVisual" in out
        vals = out["KPIVisual"]["ChartConfiguration"]["FieldWells"]["Values"]
        assert vals[0]["NumericalMeasureField"]["AggregationFunction"]["SimpleNumericalAggregation"] == "SUM"

    def test_bar_chart_visual(self):
        from recon_gen.common.models import _strip_nones, asdict
        bar = Visual(
            BarChartVisual=BarChartVisual(
                VisualId="bar-1",
                ChartConfiguration=BarChartConfiguration(
                    FieldWells=BarChartFieldWells(
                        BarChartAggregatedFieldWells=BarChartAggregatedFieldWells(
                            Category=[
                                DimensionField(
                                    CategoricalDimensionField=CategoricalDimensionField(
                                        FieldId="d1",
                                        Column=ColumnIdentifier(
                                            DataSetIdentifier="ds",
                                            ColumnName="merchant",
                                        ),
                                    )
                                )
                            ],
                        )
                    ),
                    Orientation="VERTICAL",
                ),
            )
        )
        out = _strip_nones(asdict(bar))
        cfg = out["BarChartVisual"]["ChartConfiguration"]
        assert cfg["Orientation"] == "VERTICAL"
        cat = cfg["FieldWells"]["BarChartAggregatedFieldWells"]["Category"][0]
        assert cat["CategoricalDimensionField"]["Column"]["ColumnName"] == "merchant"

    def test_pie_chart_visual(self):
        from recon_gen.common.models import _strip_nones, asdict
        pie = Visual(
            PieChartVisual=PieChartVisual(
                VisualId="pie-1",
                ChartConfiguration=PieChartConfiguration(
                    FieldWells=PieChartFieldWells(
                        PieChartAggregatedFieldWells=PieChartAggregatedFieldWells(
                            Category=[
                                DimensionField(
                                    CategoricalDimensionField=CategoricalDimensionField(
                                        FieldId="d1",
                                        Column=ColumnIdentifier(
                                            DataSetIdentifier="ds",
                                            ColumnName="status",
                                        ),
                                    )
                                )
                            ],
                        )
                    ),
                ),
            )
        )
        out = _strip_nones(asdict(pie))
        assert "PieChartVisual" in out

    def test_table_visual(self):
        from recon_gen.common.models import _strip_nones, asdict
        tbl = Visual(
            TableVisual=TableVisual(
                VisualId="tbl-1",
                ChartConfiguration=TableConfiguration(
                    FieldWells=TableFieldWells(
                        TableUnaggregatedFieldWells=TableUnaggregatedFieldWells(
                            Values=[
                                {
                                    "FieldId": "f1",
                                    "Column": {
                                        "DataSetIdentifier": "ds",
                                        "ColumnName": "id",
                                    },
                                }
                            ]
                        )
                    ),
                ),
            )
        )
        out = _strip_nones(asdict(tbl))
        vals = out["TableVisual"]["ChartConfiguration"]["FieldWells"]["TableUnaggregatedFieldWells"]["Values"]
        assert vals[0]["FieldId"] == "f1"

    def test_visual_union_only_one_set(self):
        from recon_gen.common.models import _strip_nones, asdict
        v = Visual(KPIVisual=KPIVisual(VisualId="kpi-1"))
        out = _strip_nones(asdict(v))
        assert len(out) == 1
        assert "KPIVisual" in out


class TestFilterSerialization:
    def test_category_filter(self):
        from recon_gen.common.models import _strip_nones, asdict
        fg = FilterGroup(
            FilterGroupId="fg-1",
            CrossDataset="SINGLE_DATASET",
            ScopeConfiguration=FilterScopeConfiguration(
                SelectedSheets=SelectedSheetsFilterScopeConfiguration(
                    SheetVisualScopingConfigurations=[
                        SheetVisualScopingConfiguration(
                            SheetId="s1", Scope="ALL_VISUALS"
                        )
                    ]
                )
            ),
            Filters=[
                Filter(
                    CategoryFilter=CategoryFilter(
                        FilterId="f1",
                        Column=ColumnIdentifier(
                            DataSetIdentifier="ds", ColumnName="status"
                        ),
                        Configuration=CategoryFilterConfiguration(
                            FilterListConfiguration={
                                "MatchOperator": "CONTAINS",
                                "SelectAllOptions": "FILTER_ALL_VALUES",
                            }
                        ),
                    )
                )
            ],
        )
        out = _strip_nones(asdict(fg))
        assert out["FilterGroupId"] == "fg-1"
        scope = out["ScopeConfiguration"]["SelectedSheets"]
        assert scope["SheetVisualScopingConfigurations"][0]["SheetId"] == "s1"
        cf = out["Filters"][0]["CategoryFilter"]
        assert cf["FilterId"] == "f1"
        assert cf["Configuration"]["FilterListConfiguration"]["MatchOperator"] == "CONTAINS"


class TestTagSerialization:
    def test_tag_in_theme(self):
        theme = Theme(
            AwsAccountId="123",
            ThemeId="t",
            Name="T",
            BaseThemeId="CLASSIC",
            Configuration=ThemeConfiguration(),
            Tags=[Tag(Key=MANAGED_TAG_KEY, Value=MANAGED_TAG_VALUE)],
        )
        out = theme.to_aws_json()
        assert out["Tags"] == [{"Key": MANAGED_TAG_KEY, "Value": MANAGED_TAG_VALUE}]

    def test_tag_in_dataset(self):
        ds = DataSet(
            AwsAccountId="123",
            DataSetId="ds-1",
            Name="Test",
            PhysicalTableMap={},
            Tags=[
                Tag(Key=MANAGED_TAG_KEY, Value=MANAGED_TAG_VALUE),
                Tag(Key="Env", Value="dev"),
            ],
        )
        out = ds.to_aws_json()
        assert len(out["Tags"]) == 2
        assert out["Tags"][0] == {"Key": MANAGED_TAG_KEY, "Value": MANAGED_TAG_VALUE}
        assert out["Tags"][1] == {"Key": "Env", "Value": "dev"}

    def test_tag_in_analysis(self):
        analysis = Analysis(
            AwsAccountId="123",
            AnalysisId="a-1",
            Name="Test",
            Definition=AnalysisDefinition(
                DataSetIdentifierDeclarations=[
                    DataSetIdentifierDeclaration(Identifier="ds", DataSetArn="arn:x")
                ],
            ),
            Tags=[Tag(Key=MANAGED_TAG_KEY, Value=MANAGED_TAG_VALUE)],
        )
        out = analysis.to_aws_json()
        assert out["Tags"] == [{"Key": MANAGED_TAG_KEY, "Value": MANAGED_TAG_VALUE}]

    def test_no_tags_stripped(self):
        ds = DataSet(
            AwsAccountId="123",
            DataSetId="ds-1",
            Name="Test",
            PhysicalTableMap={},
        )
        out = ds.to_aws_json()
        assert "Tags" not in out


class TestConfigTags:
    def test_default_emits_managed_and_deployment_tags(self):
        """Z.C — every deploy stamps both ManagedBy + Deployment so
        cleanup can scope per-deploy (not just per-account). Replaces
        the v8.4.0 two-tag scheme (ManagedBy + ResourcePrefix +
        optional L2Instance) with a single Deployment tag."""
        cfg = make_test_config()
        tags_by_key = {t.Key: t.Value for t in cfg.tags()}
        assert tags_by_key == {
            MANAGED_TAG_KEY: MANAGED_TAG_VALUE,
            DEPLOYMENT_TAG_KEY: "recon-test",
        }

    def test_extra_tags_merged(self):
        cfg = make_test_config(
            extra_tags={"Environment": "prod", "Team": "finance"},
        )
        tags = cfg.tags()
        # ManagedBy + Deployment (always emitted) + Environment + Team
        assert len(tags) == 4
        keys = [t.Key for t in tags]
        assert MANAGED_TAG_KEY in keys
        assert DEPLOYMENT_TAG_KEY in keys
        assert "Environment" in keys
        assert "Team" in keys

    def test_common_tag_always_first(self):
        cfg = make_test_config(extra_tags={"Foo": "bar"})
        tags = cfg.tags()
        assert tags[0].Key == MANAGED_TAG_KEY

    def test_deployment_tag_carries_cfg_value(self):
        """Z.C — Deployment tag value mirrors cfg.deployment_name so
        cleanup's per-deploy filter has something to match against."""
        cfg = make_test_config(deployment_name="qs-ci-12345-pg")
        tags_by_key = {t.Key: t.Value for t in cfg.tags()}
        assert tags_by_key[DEPLOYMENT_TAG_KEY] == "qs-ci-12345-pg"


class TestConfigPrefixed:
    """Z.C — cfg.prefixed() uses deployment_name as the single prefix
    segment (replaces v8.x's <resource_prefix>-<l2_instance_prefix>-...)."""

    def test_prefixed_uses_deployment_name(self):
        cfg = make_test_config(deployment_name="recon-prod")
        assert cfg.prefixed("l1-dashboard") == "recon-prod-l1-dashboard"

    def test_prefixed_lets_two_deployments_coexist(self):
        """The headline use case: same dashboard kind, different deployment."""
        cfg_a = make_test_config(deployment_name="recon-sasquatch")
        cfg_b = make_test_config(deployment_name="recon-wonkawash")
        assert cfg_a.prefixed("l1-dashboard") != cfg_b.prefixed("l1-dashboard")


# ---------------------------------------------------------------------------
# Dataset builder tests
# ---------------------------------------------------------------------------

_TEST_CFG = make_test_config(
    principal_arns=["arn:aws:quicksight:us-west-2:111122223333:user/default/admin"],
)


# ---------------------------------------------------------------------------
# DataSource model tests
# ---------------------------------------------------------------------------

class TestDataSourceSerialization:
    def test_postgresql_datasource(self):
        ds = DataSource(
            AwsAccountId="123456789012",
            DataSourceId="test-ds",
            Name="Test",
            Type="POSTGRESQL",
            DataSourceParameters=DataSourceParameters(
                PostgreSqlParameters=PostgreSqlParameters(
                    Host="localhost",
                    Port=5432,
                    Database="mydb",
                ),
            ),
            Credentials=DataSourceCredentials(
                CredentialPair=CredentialPair(
                    Username="user",
                    Password="pass",
                ),
            ),
        )
        out = ds.to_aws_json()
        assert out["Type"] == "POSTGRESQL"
        pg = out["DataSourceParameters"]["PostgreSqlParameters"]
        assert pg["Host"] == "localhost"
        assert pg["Port"] == 5432
        assert pg["Database"] == "mydb"
        creds = out["Credentials"]["CredentialPair"]
        assert creds["Username"] == "user"
        assert creds["Password"] == "pass"

    def test_none_fields_stripped(self):
        ds = DataSource(
            AwsAccountId="123",
            DataSourceId="ds",
            Name="Test",
            Type="POSTGRESQL",
            DataSourceParameters=DataSourceParameters(
                PostgreSqlParameters=PostgreSqlParameters(
                    Host="h", Port=5432, Database="db",
                ),
            ),
        )
        out = ds.to_aws_json()
        assert "Credentials" not in out
        assert "Permissions" not in out
        assert "Tags" not in out

    def test_tags_included(self):
        ds = DataSource(
            AwsAccountId="123",
            DataSourceId="ds",
            Name="Test",
            Type="POSTGRESQL",
            DataSourceParameters=DataSourceParameters(
                PostgreSqlParameters=PostgreSqlParameters(
                    Host="h", Port=5432, Database="db",
                ),
            ),
            Tags=[Tag(Key=MANAGED_TAG_KEY, Value=MANAGED_TAG_VALUE)],
        )
        out = ds.to_aws_json()
        assert out["Tags"] == [{"Key": MANAGED_TAG_KEY, "Value": MANAGED_TAG_VALUE}]


# ---------------------------------------------------------------------------
# DataSource builder tests
# ---------------------------------------------------------------------------

_DEMO_CFG = make_test_config(
    demo_database_url="postgresql://demouser:demopass@db.example.com:5432/quicksight_demo",
    principal_arns=["arn:aws:quicksight:us-west-2:111122223333:user/default/admin"],
)


class TestBuildDatasource:
    def test_parses_url(self):
        ds = build_datasource(_DEMO_CFG)
        out = ds.to_aws_json()
        pg = out["DataSourceParameters"]["PostgreSqlParameters"]
        assert pg["Host"] == "db.example.com"
        assert pg["Port"] == 5432
        assert pg["Database"] == "quicksight_demo"
        creds = out["Credentials"]["CredentialPair"]
        assert creds["Username"] == "demouser"
        assert creds["Password"] == "demopass"

    def test_type_is_postgresql(self):
        ds = build_datasource(_DEMO_CFG)
        assert ds.Type == "POSTGRESQL"

    def test_has_managed_by_tag(self):
        ds = build_datasource(_DEMO_CFG)
        tag_keys = {t.Key for t in ds.Tags}
        assert MANAGED_TAG_KEY in tag_keys

    def test_has_permissions_when_principal_set(self):
        ds = build_datasource(_DEMO_CFG)
        assert ds.Permissions is not None
        assert len(ds.Permissions) == 1

    def test_no_permissions_without_principal(self):
        cfg = make_test_config(
            demo_database_url="postgresql://u:p@h:5432/db",
        )
        ds = build_datasource(cfg)
        assert ds.Permissions is None

    def test_datasource_id_uses_prefix(self):
        ds = build_datasource(_DEMO_CFG)
        # Z.C — `<deployment_name>-demo-datasource` (was the historical
        # `qs-gen-demo-datasource` from the v8.x default resource_prefix).
        assert ds.DataSourceId == f"{_DEMO_CFG.deployment_name}-demo-datasource"

    def test_raises_without_demo_url(self):
        cfg = make_test_config()
        import pytest
        with pytest.raises(ValueError, match="demo_database_url"):
            build_datasource(cfg)


class TestBuildDatasourceOracle:
    """P.6.b — Oracle dispatch: ``Type=ORACLE`` + OracleParameters
    instead of PostgreSqlParameters. Two URL forms accepted (Easy
    Connect ``user/pass@host:port/SERVICE`` + SQLAlchemy-style
    ``oracle://user:pass@host:port/SERVICE``)."""

    def _oracle_cfg(self, url: str) -> Config:
        from recon_gen.common.sql import Dialect
        return make_test_config(
            demo_database_url=url,
            dialect=Dialect.ORACLE,
        )

    def test_easy_connect_url_parses_into_oracle_parameters(self):
        cfg = self._oracle_cfg("admin/secret@db.example.com:1521/ORCL")
        ds = build_datasource(cfg)
        assert ds.Type == "ORACLE"
        out = ds.to_aws_json()
        ora = out["DataSourceParameters"]["OracleParameters"]
        assert ora["Host"] == "db.example.com"
        assert ora["Port"] == 1521
        assert ora["Database"] == "ORCL"
        assert "PostgreSqlParameters" not in out["DataSourceParameters"]
        creds = out["Credentials"]["CredentialPair"]
        assert creds["Username"] == "admin"
        assert creds["Password"] == "secret"

    def test_sqlalchemy_style_oracle_url_parses(self):
        cfg = self._oracle_cfg(
            "oracle+oracledb://admin:secret@db.example.com:1521/?service_name=ORCL"
        )
        ds = build_datasource(cfg)
        ora = ds.to_aws_json()["DataSourceParameters"]["OracleParameters"]
        assert ora["Host"] == "db.example.com"
        assert ora["Port"] == 1521
        assert ora["Database"] == "ORCL"

    def test_easy_connect_default_port(self):
        cfg = self._oracle_cfg("admin/secret@db.example.com/ORCL")
        ds = build_datasource(cfg)
        ora = ds.to_aws_json()["DataSourceParameters"]["OracleParameters"]
        assert ora["Port"] == 1521  # Oracle's default


# ---------------------------------------------------------------------------
# Config — datasource_arn derivation
# ---------------------------------------------------------------------------

class TestConfigDatasourceArnDerivation:
    def test_derived_from_demo_url(self):
        cfg = Config(
            aws_account_id="111122223333",
            aws_region="us-west-2",
            # Z.C — required cfg fields.
            deployment_name="recon-derived",
            db_table_prefix="derived",
            demo_database_url="postgresql://u:p@h:5432/db",
        )
        assert cfg.datasource_arn == (
            "arn:aws:quicksight:us-west-2:111122223333:datasource/"
            f"{cfg.deployment_name}-demo-datasource"
        )

    def test_explicit_arn_takes_precedence(self):
        cfg = Config(
            aws_account_id="111122223333",
            aws_region="us-west-2",
            deployment_name="recon-explicit",
            db_table_prefix="explicit",
            datasource_arn="arn:aws:quicksight:us-west-2:111122223333:datasource/custom",
            demo_database_url="postgresql://u:p@h:5432/db",
        )
        assert "custom" in cfg.datasource_arn

    def test_raises_without_arn_or_demo_url(self):
        import pytest
        with pytest.raises(ValueError, match="datasource_arn"):
            Config(
                aws_account_id="123",
                aws_region="us-east-1",
                deployment_name="recon-fail-test",
                db_table_prefix="fail_test",
            )
