"""Per-app JSON-emit helpers for the four bundled QuickSight apps.

Lifted from the v7.x ``cli_legacy.py`` so the new ``cli/json.py`` can
build dashboards without reaching back into the legacy module.
Every helper takes a config path + output dir + optional L2 YAML
path; emits theme.json / datasets/*.json / <app>-analysis.json /
<app>-dashboard.json into ``out_dir``.

These are private to the ``cli/`` package — external callers should
go through ``quicksight-gen json apply`` (which wraps them).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from quicksight_gen.common.config import load_config
from quicksight_gen.common.theme import build_theme


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    click.echo(f"  wrote {path}")


def _prune_stale_files(directory: Path, *, keep: set[str]) -> None:
    """Delete any ``*.json`` in ``directory`` whose filename isn't in ``keep``.

    Prevents orphan files from a prior emit — datasets that were dropped
    or renamed — from being re-deployed on the next apply run.
    """
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        if path.name not in keep:
            path.unlink()
            click.echo(f"  pruned stale {path}")


def _all_dataset_filenames(
    cfg, *, keep_current: list, l2_instance=None,
) -> set[str]:
    """Expected dataset filenames for all four apps combined.

    ``keep_current`` is the list of DataSet models the current apply
    pass will write — always included. The other apps' filenames are
    included so a single-app rebuild doesn't prune its sibling's
    output.

    ``l2_instance`` selects which L2 institution YAML drives the
    sibling enumeration. When None, falls back to the bundled default
    (``spec_example``). Pass the same L2 instance the caller is
    generating against — otherwise sibling enumeration produces names
    with the wrong prefix and the prune step deletes the sibling's
    actual files.
    """
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.executives.datasets import (
        build_all_datasets as _exec,
    )
    from quicksight_gen.apps.investigation.datasets import (
        build_all_datasets as _inv,
    )
    from quicksight_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets as _l1,
    )
    from quicksight_gen.apps.l2_flow_tracing.datasets import (
        build_all_l2_flow_tracing_datasets as _l2ft,
    )

    active_l2 = l2_instance if l2_instance is not None else default_l2_instance()
    cfg_with_prefix = (
        cfg if cfg.l2_instance_prefix is not None
        else cfg.with_l2_instance_prefix(str(active_l2.instance))
    )

    names: set[str] = {f"{ds.DataSetId}.json" for ds in keep_current}
    names.update(f"{ds.DataSetId}.json" for ds in _inv(cfg_with_prefix, active_l2))
    names.update(f"{ds.DataSetId}.json" for ds in _exec(cfg_with_prefix))
    names.update(
        f"{ds.DataSetId}.json"
        for ds in _l1(cfg_with_prefix, active_l2)
    )
    names.update(
        f"{ds.DataSetId}.json"
        for ds in _l2ft(cfg_with_prefix, active_l2)
    )
    return names


def _resolve_l2(l2_instance_path: str | None):  # type: ignore[no-untyped-def]
    """Load + return the L2 instance, defaulting to the bundled spec_example."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.common.l2 import load_instance

    if l2_instance_path is not None:
        return load_instance(Path(l2_instance_path))
    return default_l2_instance()


def _generate_investigation(
    config_path: str, output_dir: str,
    *,
    l2_instance_path: str | None = None,
) -> None:
    from quicksight_gen.apps.investigation.app import (
        build_investigation_app,
    )
    from quicksight_gen.apps.investigation.datasets import build_all_datasets
    from quicksight_gen.common.theme import resolve_l2_theme

    cfg = load_config(config_path)
    out = Path(output_dir)
    l2_instance = _resolve_l2(l2_instance_path)
    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2_instance.instance))

    click.echo(
        f"Investigation: account={cfg.aws_account_id}, "
        f"region={cfg.aws_region}, l2_instance={l2_instance.instance}"
    )

    theme = build_theme(cfg, resolve_l2_theme(l2_instance))
    if theme is not None:
        _write_json(out / "theme.json", theme.to_aws_json())

    datasets = build_all_datasets(cfg, l2_instance)
    _prune_stale_files(
        out / "datasets",
        keep=_all_dataset_filenames(
            cfg, keep_current=datasets, l2_instance=l2_instance,
        ),
    )
    for ds in datasets:
        _write_json(out / "datasets" / f"{ds.DataSetId}.json", ds.to_aws_json())

    app = build_investigation_app(cfg, l2_instance=l2_instance)
    _write_json(
        out / "investigation-analysis.json",
        app.emit_analysis().to_aws_json(),
    )
    _write_json(
        out / "investigation-dashboard.json",
        app.emit_dashboard().to_aws_json(),
    )

    click.echo(f"\nGenerated {1 + len(datasets) + 2} files in {out}/")


def _generate_executives(
    config_path: str, output_dir: str,
    *,
    l2_instance_path: str | None = None,
) -> None:
    from quicksight_gen.apps.executives.app import (
        build_executives_app,
    )
    from quicksight_gen.apps.executives.datasets import build_all_datasets
    from quicksight_gen.common.theme import resolve_l2_theme

    cfg = load_config(config_path)
    out = Path(output_dir)
    l2_instance = _resolve_l2(l2_instance_path)
    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2_instance.instance))

    click.echo(
        f"Executives: account={cfg.aws_account_id}, "
        f"region={cfg.aws_region}, l2_instance={l2_instance.instance}"
    )

    theme = build_theme(cfg, resolve_l2_theme(l2_instance))
    if theme is not None:
        _write_json(out / "theme.json", theme.to_aws_json())

    datasets = build_all_datasets(cfg)
    _prune_stale_files(
        out / "datasets",
        keep=_all_dataset_filenames(
            cfg, keep_current=datasets, l2_instance=l2_instance,
        ),
    )
    for ds in datasets:
        _write_json(out / "datasets" / f"{ds.DataSetId}.json", ds.to_aws_json())

    app = build_executives_app(cfg, l2_instance=l2_instance)
    _write_json(
        out / "executives-analysis.json",
        app.emit_analysis().to_aws_json(),
    )
    _write_json(
        out / "executives-dashboard.json",
        app.emit_dashboard().to_aws_json(),
    )

    click.echo(f"\nGenerated {1 + len(datasets) + 2} files in {out}/")


def _generate_l1_dashboard(
    config_path: str, output_dir: str,
    *,
    l2_instance_path: str | None = None,
) -> None:
    from quicksight_gen.apps.l1_dashboard.app import (
        build_l1_dashboard_app,
    )
    from quicksight_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets,
    )
    from quicksight_gen.common.theme import resolve_l2_theme

    cfg = load_config(config_path)
    out = Path(output_dir)
    l2_instance = _resolve_l2(l2_instance_path)
    # X.1.f — stamp cfg with the L2 prefix BEFORE building the theme.
    # Without this, ``build_theme`` calls ``cfg.prefixed("theme")``
    # without the L2 segment and emits ``theme.json`` with id
    # ``<resource_prefix>-theme`` while the dashboard's ThemeArn (built
    # downstream by ``build_l1_dashboard_app`` which DOES stamp the
    # prefix) references ``<resource_prefix>-<l2>-theme``. Result: the
    # deployed dashboard has a dangling ThemeArn → QS's
    # ``GetThemeForDashboard`` API call 404s on every embed session.
    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2_instance.instance))

    click.echo(
        f"L1 Dashboard: account={cfg.aws_account_id}, "
        f"region={cfg.aws_region}, l2_instance={l2_instance.instance}"
    )

    theme = build_theme(cfg, resolve_l2_theme(l2_instance))
    if theme is not None:
        _write_json(out / "theme.json", theme.to_aws_json())

    datasets = build_all_l1_dashboard_datasets(cfg, l2_instance)
    _prune_stale_files(
        out / "datasets",
        keep=_all_dataset_filenames(
            cfg, keep_current=datasets, l2_instance=l2_instance,
        ),
    )
    for ds in datasets:
        _write_json(out / "datasets" / f"{ds.DataSetId}.json", ds.to_aws_json())

    app = build_l1_dashboard_app(cfg, l2_instance=l2_instance)
    _write_json(
        out / "l1-dashboard-analysis.json",
        app.emit_analysis().to_aws_json(),
    )
    _write_json(
        out / "l1-dashboard-dashboard.json",
        app.emit_dashboard().to_aws_json(),
    )

    click.echo(f"\nGenerated {1 + len(datasets) + 2} files in {out}/")


def _generate_l2_flow_tracing(
    config_path: str, output_dir: str,
    *,
    l2_instance_path: str | None = None,
) -> None:
    from quicksight_gen.apps.l2_flow_tracing.app import (
        build_l2_flow_tracing_app,
    )
    from quicksight_gen.apps.l2_flow_tracing.datasets import (
        build_all_l2_flow_tracing_datasets,
    )
    from quicksight_gen.common.theme import resolve_l2_theme

    cfg = load_config(config_path)
    out = Path(output_dir)
    l2_instance = _resolve_l2(l2_instance_path)
    # X.1.f — see L1 Dashboard generator for the full rationale.
    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2_instance.instance))

    click.echo(
        f"L2 Flow Tracing: account={cfg.aws_account_id}, "
        f"region={cfg.aws_region}, l2_instance={l2_instance.instance}"
    )

    theme = build_theme(cfg, resolve_l2_theme(l2_instance))
    if theme is not None:
        _write_json(out / "theme.json", theme.to_aws_json())

    datasets = build_all_l2_flow_tracing_datasets(cfg, l2_instance)
    _prune_stale_files(
        out / "datasets",
        keep=_all_dataset_filenames(
            cfg, keep_current=datasets, l2_instance=l2_instance,
        ),
    )
    for ds in datasets:
        _write_json(out / "datasets" / f"{ds.DataSetId}.json", ds.to_aws_json())

    app = build_l2_flow_tracing_app(cfg, l2_instance=l2_instance)
    _write_json(
        out / "l2-flow-tracing-analysis.json",
        app.emit_analysis().to_aws_json(),
    )
    _write_json(
        out / "l2-flow-tracing-dashboard.json",
        app.emit_dashboard().to_aws_json(),
    )

    click.echo(f"\nGenerated {1 + len(datasets) + 2} files in {out}/")


def _dashboard_id_for_app(app_name: str, output_dir: str) -> str:
    """Look up the deployed DashboardId from the generated dashboard JSON."""
    path = Path(output_dir) / f"{app_name}-dashboard.json"
    if not path.exists():
        raise click.ClickException(
            f"Cannot find {path}. Run `json apply` first, or pass "
            "--dashboard-id directly."
        )
    payload = json.loads(path.read_text())
    return payload["DashboardId"]
