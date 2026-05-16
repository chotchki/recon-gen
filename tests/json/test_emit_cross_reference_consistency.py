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
from typing import Any, Callable

import pytest

from quicksight_gen.cli._app_builders import (
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
        "deployment_name": "qsgen-cross-ref",
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
    tmp_path: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
) -> tuple[str, Path]:
    """Run a single per-app generator into its own tmpdir. Parametrize
    via ``indirect`` to specify which generator."""
    generator_name = request.param
    generator = _GENERATORS[generator_name]
    cfg = _write_min_config(tmp_path)  # type: ignore[arg-type]: tmp_path is pathlib.Path; helper takes str-or-Path
    out = tmp_path / "out"  # type: ignore[operator]: tmp_path Path / str is Path (pyright loses inference here)
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
        for k, v in node.items():
            if k == "DataSetArn" and isinstance(v, str):
                out.append(v)
            else:
                out.extend(_walk_dataset_arn_strings(v))
    elif isinstance(node, list):
        for item in node:
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
