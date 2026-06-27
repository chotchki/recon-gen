"""Tests for dataset-level parameters threaded through ``build_dataset``.

The ``<<$paramName>>`` substitution mechanism is renderer-agnostic:
QuickSight substituted the literal at fetch time, and App2's executor
resolves the same placeholder's default from the dataset-parameter
registry (``get_dataset_params``, keyed by ``visual_identifier``). These
tests pin the renderer-agnostic half of that mechanism:

- ``build_dataset(dataset_parameters=[...])`` lands the params on the
  returned dataset's ``DatasetParameters`` field AND registers them for
  App2 default substitution; a dataset built without params carries none,
  so the existing 50+ datasets stay param-free.
- AK.1 — each ``DataSetParameter.Id`` is a deterministic, dataset-scoped
  UUIDv5 derived from ``(dataset_id, name)`` by ``_assign_dataset_param_ids``,
  so two datasets sharing a param name (``pKey`` across several L2FT
  datasets) never collide.

DW.1 — the QS-API wire-shape tests that lived here retired with the
QuickSight emitter. Two families went: the byte-for-byte
``DataSetParameter`` dict the CreateDataSet API required (Id / ValueType /
discriminator-key serialization), and the ``MappedDataSetParameters``
analysis→dataset bridge. Both had no post-QS home — App2 reads the
model's attributes via the registry (never the serialized wire dict) and
checks ``mapped_dataset_params`` only for presence (never the mapping's
target). See the migration record for the per-test rationale.

The substitution syntax is captured in the project memory note
``project_qs_dataset_parameters.md``.
"""

from __future__ import annotations

from tests._test_helpers import make_test_config
from recon_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    build_dataset,
)
from recon_gen.common.models import (
    DatasetParameter,
    StringDatasetParameter,
    StringDatasetParameterDefaultValues,
)
from recon_gen.common.tree._helpers import auto_id


_CFG = make_test_config()


# -- build_dataset() plumbing -------------------------------------------------


def test_dataset_parameter_omitted_when_not_provided() -> None:
    """A DataSet built without ``dataset_parameters`` carries none — the
    ``DatasetParameters`` field stays ``None`` and the App2 registry
    resolves to ``[]`` — so the existing 50+ datasets stay param-free."""
    from recon_gen.common.dataset_contract import get_dataset_params

    contract = DatasetContract(columns=[ColumnSpec("col", "STRING")])
    ds = build_dataset(
        _CFG, "qs-gen-noop-dataset", "Noop", "noop",
        "SELECT 1 AS col", contract,
        visual_identifier="noop-ds",
    )
    assert ds.DatasetParameters is None
    assert get_dataset_params("noop-ds") == []


def test_build_dataset_propagates_dataset_parameters() -> None:
    """``build_dataset(..., dataset_parameters=[...])`` lands the params
    on the returned dataset's ``DatasetParameters`` field."""
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
    assert ds.DatasetParameters is not None
    sp = ds.DatasetParameters[0].StringDatasetParameter
    assert sp is not None
    assert sp.Name == "pKey"


def test_build_dataset_registers_params_for_app2_default_substitution() -> None:
    """Y.2.app2.cde — ``build_dataset`` populates the dataset-param
    registry keyed by ``visual_identifier`` so App2's ``_tree_fetcher``
    can resolve a visual's ``<<$paramName>>`` defaults at fetch time.
    A dataset built without params registers an empty list (not a
    missing key)."""
    from recon_gen.common.dataset_contract import get_dataset_params

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


# -- AK.1 dataset-parameter Id derivation ------------------------------------


def test_cascade_build_assigns_deterministic_param_ids() -> None:
    """Build the M.3.10 spike's full cascade setup through ``build_dataset``
    and assert the params land on the dataset with build_dataset-derived
    Ids. AK.1 — construction sites no longer hand-pick Ids; ``build_dataset``
    stamps each a deterministic dataset-scoped UUIDv5 (``auto_id`` over
    ``dataset_id`` + name). The Name / ValueType / default shape is
    unchanged; the Ids are derived (the assertion catches a regression in
    ``_assign_dataset_param_ids`` — the params go in Id-less)."""
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
                Name="pKey",
                ValueType="SINGLE_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=["customer_id"],
                ),
            )),
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Name="pValues",
                ValueType="MULTI_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=["demo-customer_id-1", "demo-customer_id-0"],
                ),
            )),
        ],
    )
    params = ds_aws.DatasetParameters
    assert params is not None
    pkey, pvalues = (p.StringDatasetParameter for p in params)
    assert pkey is not None and pvalues is not None

    # AK.1 — build_dataset assigns each param a deterministic, dataset-
    # scoped UUID (auto_id over dataset_id + name); the shape is unchanged.
    assert pkey.Id == auto_id("qs-gen-meta-cascade-dataset:dsparam:pKey")
    assert pkey.Name == "pKey"
    assert pkey.ValueType == "SINGLE_VALUED"
    assert pkey.DefaultValues is not None
    assert pkey.DefaultValues.StaticValues == ["customer_id"]

    assert pvalues.Id == auto_id("qs-gen-meta-cascade-dataset:dsparam:pValues")
    assert pvalues.Name == "pValues"
    assert pvalues.ValueType == "MULTI_VALUED"
    assert pvalues.DefaultValues is not None
    assert pvalues.DefaultValues.StaticValues == [
        "demo-customer_id-1", "demo-customer_id-0",
    ]


def test_dataset_param_ids_are_valid_unique_uuids_across_all_apps() -> None:
    """AK.1 regression guard for the QS dataset-parameter Id bug.

    Every ``DataSetParameter.Id`` QuickSight sees must be a real UUID
    (QS rejects non-UUIDs) AND unique across all datasets an analysis
    can span. The bug was hand-picked GUID-shaped constants reused
    across datasets sharing a param name (``pKey`` on several L2FT
    datasets); when an analysis spanned them the colliding Ids made QS
    reject it on load. build_dataset now derives each Id from
    ``(dataset_id, name)`` so the Ids are real v5 UUIDs and unique."""
    import uuid

    from recon_gen.common.l2 import default_l2_instance
    from recon_gen.apps.executives.datasets import build_all_datasets as _exec
    from recon_gen.apps.investigation.datasets import build_all_datasets as _inv
    from recon_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets as _l1,
    )
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_all_l2_flow_tracing_datasets as _l2ft,
    )

    l2 = default_l2_instance()
    datasets = [*_exec(_CFG), *_inv(_CFG, l2), *_l1(_CFG, l2), *_l2ft(_CFG, l2)]

    seen: dict[str, tuple[str, str]] = {}
    for ds in datasets:
        for p in (ds.DatasetParameters or []):
            variant = (
                p.StringDatasetParameter
                or p.IntegerDatasetParameter
                or p.DecimalDatasetParameter
                or p.DateTimeDatasetParameter
            )
            assert variant is not None
            where = (ds.DataSetId, variant.Name)
            # Real UUID (v5 — deterministic from dataset_id + name).
            assert uuid.UUID(variant.Id).version == 5, (where, variant.Id)
            # Globally unique — the bug was a param Id reused across
            # datasets sharing a param name.
            assert variant.Id not in seen, (
                f"dataset-param Id {variant.Id} collides: "
                f"{where} vs {seen[variant.Id]}"
            )
            seen[variant.Id] = where

    assert seen, "no dataset parameters built — the guard exercised nothing"
