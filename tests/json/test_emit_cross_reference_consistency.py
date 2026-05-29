"""Per-generator cross-resource consistency check (X.1.f).

Each per-app generator in ``cli/_app_builders.py`` writes a complete
JSON bundle (``theme.json`` + ``datasets/*.json`` +
``<app>-analysis.json`` + ``<app>-dashboard.json``) into the output
directory. The deploy step assumes that bundle is internally
consistent — every ``ThemeArn`` in the dashboard / analysis JSON
points to the id ``theme.json`` actually declares; every
``DataSetArn`` resolves to a sibling ``datasets/<id>.json``.

X.1.f shipped a fix for a bug class where ``_generate_l1_dashboard``
and ``_generate_l2_flow_tracing`` called ``build_theme(cfg, ...)``
before the deployment-prefix was woven in. Result: ``theme.json``
got the un-prefixed id, but the dashboard's ``ThemeArn`` (set by
the App tree, which DOES stamp internally) included the prefix —
dangling binding → ``GetThemeForDashboard`` 404 at runtime. (Z.C
collapsed the prior two-segment ``resource_prefix`` +
``l2_instance_prefix`` shape into a single ``deployment_name``;
the consistency invariant the test guards is unchanged.)

This module runs each generator in its own tmpdir with a sasquatch_pr
L2 (which declares a ``theme:`` block) and asserts the bundle each
one emits is internally consistent. Per-generator parametrization
matches the actual fault surface — the X.1.f bug was generator-
specific (Investigation + Executives stamped correctly; L1 + L2FT
didn't), so a single shared-emit fixture wouldn't have caught it
because earlier generators' theme.json got overwritten by later
ones at the same output path.
"""

from __future__ import annotations

import json as _json
import re
from pathlib import Path
from typing import Any, Callable, cast

import pytest

from recon_gen.cli._app_builders import (
    _generate_executives,
    _generate_investigation,
    _generate_l1_dashboard,
    _generate_l2_flow_tracing,
)


_GENERATORS: dict[str, Callable[..., None]] = {
    "investigation": _generate_investigation,
    "executives": _generate_executives,
    "l1_dashboard": _generate_l1_dashboard,
    "l2_flow_tracing": _generate_l2_flow_tracing,
}


_REPO_ROOT = Path(__file__).parent.parent.parent
_SASQUATCH_L2 = _REPO_ROOT / "tests" / "l2" / "sasquatch_pr.yaml"


def _write_min_config(tmp_path: Path) -> Path:
    """Minimal config.yaml the CLI loader accepts."""
    body = {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        # Z.C — both required cfg fields. db_table_prefix matches
        # sasquatch_pr because that's the L2 yaml the bundle reads.
        "deployment_name": "recon-cross-ref",
        "db_table_prefix": "sasquatch_pr",
        "datasource_arn": (
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
    }
    p = tmp_path / "config.yaml"
    p.write_text(_json.dumps(body), encoding="utf-8")
    return p


@pytest.fixture
def emitted_bundle(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> tuple[str, Path]:
    """Run a single per-app generator into its own tmpdir. Parametrize
    via ``indirect`` to specify which generator."""
    generator_name = str(request.param)
    generator = _GENERATORS[generator_name]
    cfg = _write_min_config(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    generator(
        str(cfg), str(out), l2_instance_path=str(_SASQUATCH_L2),
    )
    return generator_name, out


def _all_dashboard_and_analysis_jsons(out: Path) -> list[Path]:
    return sorted(
        p for p in out.glob("*.json")
        if p.name.endswith("-dashboard.json") or p.name.endswith("-analysis.json")
    )


def _theme_arn_id(theme_arn: str) -> str:
    """``arn:aws:quicksight:R:A:theme/<id>`` → ``<id>``."""
    m = re.match(r"^arn:aws:quicksight:[^:]+:[^:]+:theme/(.+)$", theme_arn)
    assert m, f"ThemeArn {theme_arn!r} doesn't match the expected shape"
    return m.group(1)


def _dataset_arn_id(dataset_arn: str) -> str:
    """``arn:aws:quicksight:R:A:dataset/<id>`` → ``<id>``."""
    m = re.match(r"^arn:aws:quicksight:[^:]+:[^:]+:dataset/(.+)$", dataset_arn)
    assert m, f"DataSetArn {dataset_arn!r} doesn't match the expected shape"
    return m.group(1)


def _walk_dataset_arn_strings(node: Any) -> list[str]:
    """Pull every ``DataSetArn`` string out of a nested dict/list."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in cast("dict[str, Any]", node).items():
            if k == "DataSetArn" and isinstance(v, str):
                out.append(v)
            else:
                out.extend(_walk_dataset_arn_strings(v))
    elif isinstance(node, list):
        for item in cast("list[Any]", node):
            out.extend(_walk_dataset_arn_strings(item))
    return out


@pytest.mark.parametrize(
    "emitted_bundle", list(_GENERATORS.keys()), indirect=True,
)
def test_theme_arn_matches_emitted_theme_id(
    emitted_bundle: tuple[str, Path],
) -> None:
    """X.1.f regression guard. The dashboard / analysis JSON's
    ``ThemeArn`` MUST reference the id ``theme.json`` declares —
    otherwise the deployed dashboard has a dangling binding and QS's
    ``GetThemeForDashboard`` API call 404s on every embed session.

    Per-generator parametrization matches the X.1.f fault surface:
    the bug lived inside specific generators (L1 + L2FT) that built
    the theme without stamping the L2 prefix. Investigation +
    Executives were correct. A bundle-level test that ran all four
    sequentially would have masked the L1 / L2FT bugs because the
    later writers overwrote the earlier theme.json at the same path.
    """
    generator_name, out = emitted_bundle
    theme_path = out / "theme.json"
    assert theme_path.exists(), (
        f"{generator_name}: sasquatch_pr declares a theme: block but "
        f"the generator didn't emit theme.json"
    )
    expected_id = _json.loads(theme_path.read_text())["ThemeId"]

    failures: list[str] = []
    dash_paths = _all_dashboard_and_analysis_jsons(out)
    assert dash_paths, (
        f"{generator_name}: no *-dashboard.json / *-analysis.json files "
        f"emitted under {out}"
    )
    for dash_path in dash_paths:
        doc = _json.loads(dash_path.read_text())
        arn = doc.get("ThemeArn")
        if arn is None:
            failures.append(
                f"  {dash_path.name}: ThemeArn is None but theme.json "
                f"emits {expected_id!r} — dashboard would deploy "
                f"without a theme binding even though one's available."
            )
            continue
        actual_id = _theme_arn_id(arn)
        if actual_id != expected_id:
            failures.append(
                f"  {dash_path.name}: ThemeArn cites {actual_id!r}, "
                f"theme.json emits {expected_id!r}. Dangling binding "
                f"→ QS GetThemeForDashboard 404 at runtime."
            )
    assert not failures, (
        f"{generator_name}: ThemeArn / ThemeId mismatch in emitted "
        f"bundle:\n" + "\n".join(failures)
    )


@pytest.mark.parametrize(
    "emitted_bundle", list(_GENERATORS.keys()), indirect=True,
)
def test_dataset_arns_resolve_to_emitted_dataset_files(
    emitted_bundle: tuple[str, Path],
) -> None:
    """Every ``DataSetArn`` referenced in any dashboard / analysis
    JSON MUST correspond to an emitted ``datasets/<id>.json``. Catches
    the "per-app builder forgot to register a dataset" bug class —
    the deployed dashboard would describe-create cleanly but every
    visual bound to the missing dataset would render the spinner
    forever with no error banner."""
    generator_name, out = emitted_bundle
    datasets_dir = out / "datasets"
    emitted_ids: set[str] = (
        {p.stem for p in datasets_dir.glob("*.json")}
        if datasets_dir.exists() else set()
    )

    failures: list[str] = []
    for dash_path in _all_dashboard_and_analysis_jsons(out):
        doc = _json.loads(dash_path.read_text())
        for arn in _walk_dataset_arn_strings(doc):
            ds_id = _dataset_arn_id(arn)
            if ds_id not in emitted_ids:
                failures.append(
                    f"  {dash_path.name}: DataSetArn cites {ds_id!r} "
                    f"but no datasets/{ds_id}.json was emitted."
                )
    assert not failures, (
        f"{generator_name}: dashboard / analysis cites datasets the "
        f"bundle didn't emit:\n" + "\n".join(failures)
    )


def _dataset_parameter_names(ds_doc: dict[str, Any]) -> list[str]:
    """Extract declared ``DatasetParameters`` names from a dataset
    JSON. Each entry is wrapped in a kind-discriminator dict —
    ``{StringDatasetParameter|IntegerDatasetParameter|…: {Name: ...}}``.
    """
    out: list[str] = []
    for entry in cast(
        "list[dict[str, Any]]", ds_doc.get("DatasetParameters", []) or [],
    ):
        for decl_any in entry.values():
            if not isinstance(decl_any, dict):
                continue
            decl = cast("dict[str, Any]", decl_any)
            name = decl.get("Name")
            if isinstance(name, str):
                out.append(name)
    return out


def _analysis_param_bridges(
    analysis_doc: dict[str, Any],
) -> set[tuple[str, str]]:
    """Collect every ``(DataSetIdentifier, DataSetParameterName)`` pair
    that any analysis-side ``ParameterDeclaration`` bridges via
    ``MappedDataSetParameters``. These are the (short_ds_id, ds_param)
    pairs that QS knows how to substitute at fetch time."""
    bridged: set[tuple[str, str]] = set()
    defn = cast("dict[str, Any]", analysis_doc.get("Definition", {}))
    for decl_wrapper in cast(
        "list[dict[str, Any]]",
        defn.get("ParameterDeclarations", []) or [],
    ):
        for decl_any in decl_wrapper.values():
            if not isinstance(decl_any, dict):
                continue
            decl = cast("dict[str, Any]", decl_any)
            mappings = cast(
                "list[dict[str, Any]]",
                decl.get("MappedDataSetParameters") or [],
            )
            for m in mappings:
                ds_id = m.get("DataSetIdentifier")
                param = m.get("DataSetParameterName")
                if isinstance(ds_id, str) and isinstance(param, str):
                    bridged.add((ds_id, param))
    return bridged


def _analysis_short_id_for_arn(
    analysis_doc: dict[str, Any],
) -> dict[str, str]:
    """Build ``DataSetArn → short Identifier`` lookup from the analysis's
    ``DataSetIdentifierDeclarations``. The short id is what
    ``MappedDataSetParameters.DataSetIdentifier`` references; the ARN
    points at the emitted dataset file's id."""
    out: dict[str, str] = {}
    defn = analysis_doc.get("Definition", {})
    for d in cast(
        "list[dict[str, Any]]",
        defn.get("DataSetIdentifierDeclarations", []) or [],
    ):
        ident = d.get("Identifier")
        arn = d.get("DataSetArn")
        if isinstance(ident, str) and isinstance(arn, str):
            out[arn] = ident
    return out


@pytest.mark.parametrize(
    "emitted_bundle", list(_GENERATORS.keys()), indirect=True,
)
def test_dataset_parameters_are_bridged_from_analysis(
    emitted_bundle: tuple[str, Path],
) -> None:
    """Every ``DatasetParameter`` a dataset declares MUST be mapped
    from at least one analysis-side ``ParameterDeclaration`` via
    ``MappedDataSetParameters`` — otherwise QS errors with "dataset
    has unmapped parameter" on analysis load, and any cascade /
    SQL-pushdown path downstream of it fails with a misleading
    "calculated field has invalid syntax".

    Surfaced by BR.x: ``l1-accounts-ds`` declared ``pL1DsRole`` (the
    role-cascade param) but the analysis only bridged ``pL1DsRole`` →
    ``l1-ds-accounts-ds`` (the Daily-Statement companion). The wider
    ``l1-accounts-ds`` had ``<<$pL1DsRole>>`` in its SQL with no
    analysis-param feeding it — QS surfaced this as a load-time
    notification, and the cascade-driven Account dropdown refetch on
    Daily Statement hit "calculated field has invalid syntax".
    """
    generator_name, out = emitted_bundle
    datasets_dir = out / "datasets"
    if not datasets_dir.exists():
        pytest.skip(f"{generator_name}: no datasets directory")

    analysis_paths = sorted(out.glob("*-analysis.json"))
    assert analysis_paths, (
        f"{generator_name}: no *-analysis.json emitted under {out}"
    )

    failures: list[str] = []
    for analysis_path in analysis_paths:
        analysis_doc = cast(
            "dict[str, Any]", _json.loads(analysis_path.read_text()),
        )
        bridged = _analysis_param_bridges(analysis_doc)
        arn_to_short = _analysis_short_id_for_arn(analysis_doc)

        for ds_path in sorted(datasets_dir.glob("*.json")):
            ds_doc = cast(
                "dict[str, Any]", _json.loads(ds_path.read_text()),
            )
            ds_id = ds_doc.get("DataSetId")
            if not isinstance(ds_id, str):
                continue
            declared = _dataset_parameter_names(ds_doc)
            if not declared:
                continue
            # Match the dataset to its analysis-side short identifier.
            # ARN shape: arn:aws:quicksight:R:A:dataset/<DataSetId>.
            matching_arn = next(
                (
                    arn for arn in arn_to_short
                    if arn.endswith(f"dataset/{ds_id}")
                ),
                None,
            )
            if matching_arn is None:
                # Dataset emitted but not referenced by this analysis
                # — skip; it belongs to a different app's bundle.
                continue
            short_id = arn_to_short[matching_arn]
            for param_name in declared:
                if (short_id, param_name) not in bridged:
                    failures.append(
                        f"  {analysis_path.name}: dataset "
                        f"{ds_id!r} (analysis short id {short_id!r}) "
                        f"declares ``DatasetParameter`` "
                        f"{param_name!r} but no ``ParameterDeclaration`` "
                        f"bridges it via ``MappedDataSetParameters``. "
                        f"QS will error with \"dataset has unmapped "
                        f"parameter\" on analysis load and any cascade "
                        f"path downstream will surface as \"calculated "
                        f"field has invalid syntax\"."
                    )
    assert not failures, (
        f"{generator_name}: dataset parameters declared but not "
        f"bridged from the analysis:\n" + "\n".join(failures)
    )
