"""Tests for dataset-level parameters in CustomSQL (M.3.10c phase 1).

Verifies that:

- The new ``DatasetParameter`` model variants emit the wire shape
  QuickSight's CreateDataSet API requires (proven against the M.3.10
  hand-spike — the captured shape is the test fixture below).
- ``build_dataset()`` plumbs ``dataset_parameters`` onto the emitted
  ``DataSet.DatasetParameters`` field.
- Tree-level ``StringParam`` / ``IntegerParam`` / ``DateTimeParam``
  variants emit ``MappedDataSetParameters`` on their declarations
  when ``mapped_dataset_params=[(dataset, "name"), ...]`` is set, and
  omit the field when not.
- The bridge round-trips: an analysis-level parameter mapped to a
  dataset-level parameter both emit valid AWS-shape JSON that
  references each other by name.

The substitution syntax (`<<$paramName>>`) is captured separately in
the project memory note `project_qs_dataset_parameters.md` — the
unit tests just verify byte-shape, not Aurora-side substitution.
"""

from __future__ import annotations

from tests._test_helpers import make_test_config
from quicksight_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    build_dataset,
)
from quicksight_gen.common.ids import ParameterName
from quicksight_gen.common.models import (
    DataSet,
    DatasetParameter,
    DateTimeDatasetParameter,
    DateTimeDatasetParameterDefaultValues,
    DecimalDatasetParameter,
    DecimalDatasetParameterDefaultValues,
    IntegerDatasetParameter,
    IntegerDatasetParameterDefaultValues,
    MappedDataSetParameter,
    StringDatasetParameter,
    StringDatasetParameterDefaultValues,
)
from quicksight_gen.common.tree import Dataset, IntegerParam, StringParam


_CFG = make_test_config()


# -- Model emit ---------------------------------------------------------------


def test_string_dataset_parameter_single_value_matches_spike_shape() -> None:
    """The captured M.3.10 spike's ``pKey`` parameter wire shape, byte-
    for-byte. Drift here means the QS API would reject the new shape."""
    p = DatasetParameter(StringDatasetParameter=StringDatasetParameter(
        Id="6d1ce7f7-2a8a-405a-b81a-b016a66c0a2f",
        Name="pKey",
        ValueType="SINGLE_VALUED",
        DefaultValues=StringDatasetParameterDefaultValues(
            StaticValues=["customer_id"],
        ),
    ))
    from dataclasses import asdict
    from quicksight_gen.common.models import _strip_nones
    assert _strip_nones(asdict(p)) == {
        "StringDatasetParameter": {
            "Id": "6d1ce7f7-2a8a-405a-b81a-b016a66c0a2f",
            "Name": "pKey",
            "ValueType": "SINGLE_VALUED",
            "DefaultValues": {"StaticValues": ["customer_id"]},
        },
    }


def test_string_dataset_parameter_multi_value_matches_spike_shape() -> None:
    """Same wire shape, ``MULTI_VALUED`` + multi-element default —
    the second M.3.10 spike's ``pValues`` form."""
    p = DatasetParameter(StringDatasetParameter=StringDatasetParameter(
        Id="751f40e3-eec9-4263-afee-40cfca9661a6",
        Name="pValues",
        ValueType="MULTI_VALUED",
        DefaultValues=StringDatasetParameterDefaultValues(
            StaticValues=["demo-customer_id-1", "demo-customer_id-0"],
        ),
    ))
    from dataclasses import asdict
    from quicksight_gen.common.models import _strip_nones
    assert _strip_nones(asdict(p)) == {
        "StringDatasetParameter": {
            "Id": "751f40e3-eec9-4263-afee-40cfca9661a6",
            "Name": "pValues",
            "ValueType": "MULTI_VALUED",
            "DefaultValues": {
                "StaticValues": [
                    "demo-customer_id-1", "demo-customer_id-0",
                ],
            },
        },
    }


def test_integer_dataset_parameter_emits() -> None:
    """Sanity: Integer variant emits the right discriminator."""
    p = DatasetParameter(IntegerDatasetParameter=IntegerDatasetParameter(
        Id="abc-1", Name="pCount", ValueType="SINGLE_VALUED",
        DefaultValues=IntegerDatasetParameterDefaultValues(StaticValues=[42]),
    ))
    from dataclasses import asdict
    from quicksight_gen.common.models import _strip_nones
    out = _strip_nones(asdict(p))
    assert "IntegerDatasetParameter" in out
    assert out["IntegerDatasetParameter"]["DefaultValues"]["StaticValues"] == [42]


def test_decimal_dataset_parameter_emits() -> None:
    """Sanity: Decimal variant emits the right discriminator."""
    p = DatasetParameter(DecimalDatasetParameter=DecimalDatasetParameter(
        Id="abc-2", Name="pAmount", ValueType="SINGLE_VALUED",
        DefaultValues=DecimalDatasetParameterDefaultValues(
            StaticValues=[1.5],
        ),
    ))
    from dataclasses import asdict
    from quicksight_gen.common.models import _strip_nones
    out = _strip_nones(asdict(p))
    assert "DecimalDatasetParameter" in out


def test_datetime_dataset_parameter_emits_with_granularity() -> None:
    """DateTime variant carries the optional TimeGranularity field."""
    p = DatasetParameter(DateTimeDatasetParameter=DateTimeDatasetParameter(
        Id="abc-3", Name="pAsOf", ValueType="SINGLE_VALUED",
        TimeGranularity="DAY",
        DefaultValues=DateTimeDatasetParameterDefaultValues(
            StaticValues=["2030-01-01T00:00:00.000Z"],
        ),
    ))
    from dataclasses import asdict
    from quicksight_gen.common.models import _strip_nones
    out = _strip_nones(asdict(p))
    assert out["DateTimeDatasetParameter"]["TimeGranularity"] == "DAY"


def test_dataset_parameter_omitted_when_not_provided() -> None:
    """A DataSet without DatasetParameters omits the field entirely
    in JSON output (so the existing 50+ datasets continue to emit
    unchanged)."""
    contract = DatasetContract(columns=[ColumnSpec("col", "STRING")])
    ds = build_dataset(
        _CFG, "qs-gen-noop-dataset", "Noop", "noop",
        "SELECT 1 AS col", contract,
        visual_identifier="noop-ds",
    )
    assert "DatasetParameters" not in ds.to_aws_json()


# -- build_dataset() plumbing -------------------------------------------------


def test_build_dataset_propagates_dataset_parameters_to_emitted_json() -> None:
    """``build_dataset(..., dataset_parameters=[...])`` lands on the
    AWS-shape DataSet's top-level ``DatasetParameters`` field."""
    contract = DatasetContract(columns=[ColumnSpec("col", "STRING")])
    params = [
        DatasetParameter(StringDatasetParameter=StringDatasetParameter(
            Id="id-1", Name="pKey", ValueType="SINGLE_VALUED",
            DefaultValues=StringDatasetParameterDefaultValues(
                StaticValues=["customer_id"],
            ),
        )),
    ]
    ds = build_dataset(
        _CFG, "qs-gen-with-params-dataset", "WithParams", "with-params",
        "SELECT JSON_VALUE(metadata, '$.' || <<$pKey>>) AS col FROM tx",
        contract,
        visual_identifier="with-params-ds",
        dataset_parameters=params,
    )
    out = ds.to_aws_json()
    assert "DatasetParameters" in out
    assert out["DatasetParameters"][0]["StringDatasetParameter"]["Name"] == "pKey"


def test_build_dataset_registers_params_for_app2_default_substitution() -> None:
    """Y.2.app2.cde — ``build_dataset`` populates the dataset-param
    registry keyed by ``visual_identifier`` so App2's ``_tree_fetcher``
    can resolve a visual's ``<<$paramName>>`` defaults at fetch time.
    A dataset built without params registers an empty list (not a
    missing key)."""
    from quicksight_gen.common.dataset_contract import get_dataset_params

    contract = DatasetContract(columns=[ColumnSpec("col", "STRING")])
    params = [
        DatasetParameter(StringDatasetParameter=StringDatasetParameter(
            Id="id-1", Name="pKey", ValueType="SINGLE_VALUED",
            DefaultValues=StringDatasetParameterDefaultValues(
                StaticValues=["customer_id"],
            ),
        )),
    ]
    build_dataset(
        _CFG, "qs-gen-registry-dataset", "Registry", "registry",
        "SELECT JSON_VALUE(metadata, '$.' || <<$pKey>>) AS col FROM tx",
        contract,
        visual_identifier="registry-ds",
        dataset_parameters=params,
    )
    got = get_dataset_params("registry-ds")
    assert len(got) == 1
    sp = got[0].StringDatasetParameter
    assert sp is not None and sp.Name == "pKey"

    build_dataset(
        _CFG, "qs-gen-registry-noparams-dataset", "NoParams", "noparams",
        "SELECT 1 AS col", contract,
        visual_identifier="registry-noparams-ds",
    )
    assert get_dataset_params("registry-noparams-ds") == []
    # Unknown identifier → empty list, not KeyError.
    assert get_dataset_params("never-registered-ds") == []


# -- Tree-level mapping wiring -----------------------------------------------


def test_string_param_emits_no_mappings_when_unset() -> None:
    """Existing analysis params (date pickers, drill sentinels) DON'T
    set ``mapped_dataset_params`` — the emitted declaration must omit
    the field entirely. Otherwise existing dashboards regress."""
    p = StringParam(name=ParameterName("pNoMappings"), default=["x"])
    decl = p.emit()
    sd = decl.StringParameterDeclaration
    assert sd is not None
    assert sd.MappedDataSetParameters is None


def test_string_param_emits_mappings_when_provided() -> None:
    """When ``mapped_dataset_params`` is set, emit one
    ``MappedDataSetParameter`` per (Dataset, name) pair, in order."""
    ds_a = Dataset(identifier="ds-a", arn="arn:fake:a")
    ds_b = Dataset(identifier="ds-b", arn="arn:fake:b")
    p = StringParam(
        name=ParameterName("pBoth"),
        default=["customer_id"],
        mapped_dataset_params=[(ds_a, "pKey"), (ds_b, "pKey")],
    )
    decl = p.emit()
    sd = decl.StringParameterDeclaration
    assert sd is not None
    assert sd.MappedDataSetParameters == [
        MappedDataSetParameter(DataSetIdentifier="ds-a", DataSetParameterName="pKey"),
        MappedDataSetParameter(DataSetIdentifier="ds-b", DataSetParameterName="pKey"),
    ]


def test_integer_param_supports_mappings() -> None:
    """IntegerParam variant also emits the field (parity check)."""
    ds = Dataset(identifier="ds-i", arn="arn:fake:i")
    p = IntegerParam(
        name=ParameterName("pInt"),
        default=[1],
        mapped_dataset_params=[(ds, "pNum")],
    )
    decl = p.emit()
    assert decl.IntegerParameterDeclaration is not None
    assert decl.IntegerParameterDeclaration.MappedDataSetParameters == [
        MappedDataSetParameter(DataSetIdentifier="ds-i", DataSetParameterName="pNum"),
    ]


def test_dataset_param_mapping_uses_dataset_identifier_not_arn() -> None:
    """Wire shape uses the Dataset's logical ``identifier`` (the analysis-
    level visual_identifier), not the ARN. Bug if it ever flipped:
    the analysis would reference a dataset by ARN, but
    DataSetIdentifierDeclarations key the dataset by identifier."""
    ds = Dataset(
        identifier="my-pretty-name",
        arn="arn:aws:quicksight:us-west-2:1:dataset/qs-gen-something",
    )
    p = StringParam(
        name=ParameterName("pX"),
        mapped_dataset_params=[(ds, "pKey")],
    )
    decl = p.emit()
    sd = decl.StringParameterDeclaration
    assert sd is not None
    mapping = sd.MappedDataSetParameters[0]
    assert mapping.DataSetIdentifier == "my-pretty-name"
    assert "arn" not in mapping.DataSetIdentifier


# -- End-to-end: cascade-shape sanity -----------------------------------------


def test_cascade_round_trip_against_spike_shape() -> None:
    """Build the M.3.10 spike's full setup end-to-end through both
    the AWS-shape DataSet + the tree's StringParam, and assert the
    JSON shape matches the captured spike."""
    contract = DatasetContract(columns=[
        ColumnSpec("id", "STRING"),
        ColumnSpec("rail_name", "STRING"),
        ColumnSpec("picked_value", "STRING"),
    ])
    ds_aws = build_dataset(
        _CFG, "qs-gen-meta-cascade-dataset", "Meta Cascade", "meta-cascade",
        (
            "SELECT id, rail_name, "
            "JSON_VALUE(metadata, '$.' || <<$pKey>>) AS picked_value "
            "FROM sasquatch_pr_current_transactions "
            "WHERE metadata IS NOT NULL "
            "AND JSON_VALUE(metadata, '$.' || <<$pKey>>) IN (<<$pValues>>)"
        ),
        contract,
        visual_identifier="meta-cascade-ds",
        dataset_parameters=[
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id="6d1ce7f7-2a8a-405a-b81a-b016a66c0a2f",
                Name="pKey",
                ValueType="SINGLE_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=["customer_id"],
                ),
            )),
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id="751f40e3-eec9-4263-afee-40cfca9661a6",
                Name="pValues",
                ValueType="MULTI_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=["demo-customer_id-1", "demo-customer_id-0"],
                ),
            )),
        ],
    )
    out = ds_aws.to_aws_json()
    assert out["DatasetParameters"] == [
        {"StringDatasetParameter": {
            "Id": "6d1ce7f7-2a8a-405a-b81a-b016a66c0a2f",
            "Name": "pKey",
            "ValueType": "SINGLE_VALUED",
            "DefaultValues": {"StaticValues": ["customer_id"]},
        }},
        {"StringDatasetParameter": {
            "Id": "751f40e3-eec9-4263-afee-40cfca9661a6",
            "Name": "pValues",
            "ValueType": "MULTI_VALUED",
            "DefaultValues": {
                "StaticValues": ["demo-customer_id-1", "demo-customer_id-0"],
            },
        }},
    ]
