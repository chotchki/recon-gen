"""Cleanup orphaned QuickSight resources managed by recon-gen.

Lists every resource in the configured account+region that carries the
``ManagedBy: recon-gen`` tag and is NOT present in the current
generate output directory, prints them, and (after a single y/n
confirmation) deletes them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
import click
from botocore.exceptions import ClientError

from recon_gen.common.config import Config

if TYPE_CHECKING:
    from mypy_boto3_quicksight.client import QuickSightClient


MANAGED_TAG_KEY = "ManagedBy"
MANAGED_TAG_VALUE = "recon-gen"
DEPLOYMENT_TAG_KEY = "Deployment"


def _read_managed_tags(
    client: QuickSightClient, resource_arn: str,
) -> dict[str, str] | None:
    """Return the resource's tag map IF it carries ``ManagedBy: recon-gen``.

    Returns None if the resource is not ours (or we can't read its tags).
    Caller uses the returned map to additionally filter on ``Deployment``
    when ``cfg.deployment_name`` is set (Z.C — collapsed from the prior
    ``ResourcePrefix`` + ``L2Instance`` two-tag scope).
    """
    try:
        resp = client.list_tags_for_resource(ResourceArn=resource_arn)
    except ClientError:
        return None
    # BF.1.S2: boto3-stubs declares Tag.Key + Tag.Value as required `str`;
    # the previous `isinstance(str)` guard was defensive code from the
    # pre-stub days.
    tag_map: dict[str, str] = {tag["Key"]: tag["Value"] for tag in resp.get("Tags", [])}
    if tag_map.get(MANAGED_TAG_KEY) != MANAGED_TAG_VALUE:
        return None
    return tag_map


def _expected_ids_from_out(out_dir: Path, cfg: Config) -> dict[str, set[str]]:
    """Collect the IDs of every resource produced by the current generate run.

    The currently-configured ``datasource_arn`` is always treated as active —
    ``generate`` never writes a datasource.json (only ``demo apply`` does), so
    without this the active datasource would be flagged stale on every run.
    """
    expected: dict[str, set[str]] = {
        "dashboard": set(),
        "analysis": set(),
        "dataset": set(),
        "theme": set(),
        "datasource": set(),
    }

    if cfg.datasource_arn:
        expected["datasource"].add(cfg.datasource_arn.rsplit("/", 1)[-1])

    if not out_dir.exists():
        return expected

    for path in out_dir.glob("*-dashboard.json"):
        expected["dashboard"].add(json.loads(path.read_text())["DashboardId"])
    for path in out_dir.glob("*-analysis.json"):
        expected["analysis"].add(json.loads(path.read_text())["AnalysisId"])
    datasets_dir = out_dir / "datasets"
    if datasets_dir.is_dir():
        for path in datasets_dir.glob("*.json"):
            expected["dataset"].add(json.loads(path.read_text())["DataSetId"])
    theme_path = out_dir / "theme.json"
    if theme_path.exists():
        expected["theme"].add(json.loads(theme_path.read_text())["ThemeId"])
    datasource_path = out_dir / "datasource.json"
    if datasource_path.exists():
        expected["datasource"].add(json.loads(datasource_path.read_text())["DataSourceId"])
    return expected


# BF.1.S2: boto3-stubs marks ``<Resource>Id`` + ``Arn`` NotRequired on
# every Summary TypedDict, even though AWS always populates them on
# list responses in practice. Skip summaries where either key is
# missing — surfaces a malformed-response edge case as a no-op
# instead of a KeyError mid-cleanup.


def _iter_dashboards(
    client: QuickSightClient, account_id: str,
) -> Iterator[tuple[str, str]]:
    paginator = client.get_paginator("list_dashboards")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("DashboardSummaryList", []):
            did = item.get("DashboardId")
            arn = item.get("Arn")
            if did is not None and arn is not None:
                yield did, arn


def _iter_analyses(
    client: QuickSightClient, account_id: str,
) -> Iterator[tuple[str, str]]:
    paginator = client.get_paginator("list_analyses")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("AnalysisSummaryList", []):
            if item.get("Status") == "DELETED":
                continue
            aid = item.get("AnalysisId")
            arn = item.get("Arn")
            if aid is not None and arn is not None:
                yield aid, arn


def _iter_datasets(
    client: QuickSightClient, account_id: str,
) -> Iterator[tuple[str, str]]:
    paginator = client.get_paginator("list_data_sets")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("DataSetSummaries", []):
            dsid = item.get("DataSetId")
            arn = item.get("Arn")
            if dsid is not None and arn is not None:
                yield dsid, arn


def _iter_themes(
    client: QuickSightClient, account_id: str,
) -> Iterator[tuple[str, str]]:
    paginator = client.get_paginator("list_themes")
    for page in paginator.paginate(AwsAccountId=account_id, Type="CUSTOM"):
        for item in page.get("ThemeSummaryList", []):
            tid = item.get("ThemeId")
            arn = item.get("Arn")
            if tid is not None and arn is not None:
                yield tid, arn


def _iter_datasources(
    client: QuickSightClient, account_id: str,
) -> Iterator[tuple[str, str]]:
    paginator = client.get_paginator("list_data_sources")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("DataSources", []):
            dsid = item.get("DataSourceId")
            arn = item.get("Arn")
            if dsid is not None and arn is not None:
                yield dsid, arn


def _collect_stale(
    client: QuickSightClient,
    account_id: str,
    expected: dict[str, set[str]],
    *,
    deployment_name: str,
    tagging_enabled: bool = True,
) -> dict[str, list[tuple[str, str]]]:
    """Return stale (id, arn) tuples grouped by resource type.

    Per-deploy scoping, fail-CLOSED (untagged resources stay safe —
    they were deployed by a previous version of the library and the
    operator hasn't opted into the new scope):

    Z.C collapsed the prior two-tag scheme (``ResourcePrefix`` +
    optional ``L2Instance``) into a single ``Deployment`` tag. Only
    resources whose ``Deployment`` tag matches ``deployment_name``
    are eligible for deletion. This is what makes parallel CI runs +
    coexisting local deploys safe — each deploy stamps its own
    ``Deployment`` value (e.g. ``qs-ci-<run_id>-pg`` for CI,
    ``recon-prod`` for a local deploy) and cleanup only ever sweeps
    its own scope. Resources tagged with a different ``Deployment``
    value AND resources with no ``Deployment`` tag at all (pre-Z.C
    deploys) are skipped.

    When ``tagging_enabled=False`` (v8.6.11), the tag check is
    bypassed entirely. Cleanup matches by ID-prefix
    (``rid.startswith(deployment_name)``) instead — significantly
    weaker isolation, but the only option when the IAM principal
    can't ``Tag*Resource``. See the docs reference for the
    loss-of-safety implications.
    """
    stale: dict[str, list[tuple[str, str]]] = {
        "dashboard": [],
        "analysis": [],
        "dataset": [],
        "theme": [],
        "datasource": [],
    }
    iterators: dict[
        str, Callable[[QuickSightClient, str], Iterable[tuple[str, str]]],
    ] = {
        "dashboard": _iter_dashboards,
        "analysis": _iter_analyses,
        "dataset": _iter_datasets,
        "theme": _iter_themes,
        "datasource": _iter_datasources,
    }
    for kind, it in iterators.items():
        for rid, arn in it(client, account_id):
            if rid in expected[kind]:
                continue
            if not tagging_enabled:
                # ID-prefix fallback (v8.6.11). Match anything starting
                # with the deployment_name; trust the operator's
                # deployment-name uniqueness.
                if not rid.startswith(f"{deployment_name}-"):
                    continue
                stale[kind].append((rid, arn))
                continue
            tags = _read_managed_tags(client, arn)
            if tags is None:
                # Not ours.
                continue
            # Per-deploy Deployment-tag match. Fail-CLOSED on missing
            # tag — pre-Z.C deploys without the tag are NOT eligible
            # for sweep.
            if tags.get(DEPLOYMENT_TAG_KEY) != deployment_name:
                continue
            stale[kind].append((rid, arn))
    return stale


def _delete_stale(
    client: QuickSightClient,
    account_id: str,
    stale: dict[str, list[tuple[str, str]]],
) -> int:
    """Delete stale resources in dependency order. Returns failure count."""
    failures = 0

    for rid, _ in stale["dashboard"]:
        click.echo(f"  deleting dashboard {rid}")
        try:
            client.delete_dashboard(AwsAccountId=account_id, DashboardId=rid)
        except ClientError as exc:
            click.echo(f"    error: {exc}")
            failures += 1
    for rid, _ in stale["analysis"]:
        click.echo(f"  deleting analysis {rid}")
        try:
            client.delete_analysis(
                AwsAccountId=account_id,
                AnalysisId=rid,
                ForceDeleteWithoutRecovery=True,
            )
        except ClientError as exc:
            click.echo(f"    error: {exc}")
            failures += 1
    for rid, _ in stale["dataset"]:
        click.echo(f"  deleting dataset {rid}")
        try:
            client.delete_data_set(AwsAccountId=account_id, DataSetId=rid)
        except ClientError as exc:
            click.echo(f"    error: {exc}")
            failures += 1
    for rid, _ in stale["theme"]:
        click.echo(f"  deleting theme {rid}")
        try:
            client.delete_theme(AwsAccountId=account_id, ThemeId=rid)
        except ClientError as exc:
            click.echo(f"    error: {exc}")
            failures += 1
    for rid, _ in stale["datasource"]:
        click.echo(f"  deleting datasource {rid}")
        try:
            client.delete_data_source(AwsAccountId=account_id, DataSourceId=rid)
        except ClientError as exc:
            click.echo(f"    error: {exc}")
            failures += 1
    return failures


def run_cleanup(
    cfg: Config,
    out_dir: Path,
    *,
    dry_run: bool = False,
    skip_confirm: bool = False,
    purge_all: bool = False,
) -> int:
    """Entrypoint for the `cleanup` CLI command.

    By default, ``out_dir`` is the carve-out: anything currently emitted
    there stays, everything else matching the cfg's tag/prefix scope is
    stale.

    With ``purge_all=True`` (v8.6.13 ``--all`` flag), ``out_dir`` is
    ignored entirely — every resource matching the scope is treated as
    stale, including the live deploy. Use to nuke everything we own
    from a QS account (e.g. tearing down a CI run, decommissioning an
    L2 instance).
    """
    # BF.1.S2: boto3.client returns the right per-service stub at runtime
    # but pyright sees the umbrella overload; anchor to QuickSightClient.
    client: QuickSightClient = boto3.client(  # pyright: ignore[reportUnknownMemberType]: boto3.client overloaded union; QuickSightClient annotation anchors the right stub
        "quicksight", region_name=cfg.aws_region,
    )
    account_id = cfg.aws_account_id

    scope_label = f" scoped to Deployment={cfg.deployment_name!r}"
    if not cfg.tagging_enabled:
        scope_label += (
            " (tagging disabled — matching by ID prefix only; weaker"
            " isolation, see docs reference)"
        )
    if purge_all:
        scope_label += (
            " — PURGE-ALL mode: ignoring out/, every matching resource"
            " is eligible for deletion (including the live deploy)"
        )
    click.echo(
        f"Scanning QuickSight resources in {account_id} "
        f"({cfg.aws_region}){scope_label}..."
    )
    expected: dict[str, set[str]] = (
        # Empty carve-out = every matching resource is stale.
        {kind: set[str]() for kind in (
            "dashboard", "analysis", "dataset", "theme", "datasource",
        )}
        if purge_all
        else _expected_ids_from_out(out_dir, cfg)
    )
    stale = _collect_stale(
        client, account_id, expected,
        deployment_name=cfg.deployment_name,
        tagging_enabled=cfg.tagging_enabled,
    )

    total = sum(len(items) for items in stale.values())
    if total == 0:
        click.echo("No stale tagged resources found. Nothing to do.")
        return 0

    click.echo(f"\nFound {total} stale tagged resource(s):")
    for kind in ("dashboard", "analysis", "dataset", "theme", "datasource"):
        for rid, _ in stale[kind]:
            click.echo(f"  [{kind}] {rid}")

    if dry_run:
        click.echo("\n(dry-run) not deleting anything.")
        return 0

    if not skip_confirm:
        if not click.confirm("\nDelete all of these?", default=False):
            click.echo("Aborted.")
            return 0

    click.echo()
    failures = _delete_stale(client, account_id, stale)
    click.echo()
    if failures:
        click.echo(f"Completed with {failures} failure(s).")
        return 1
    click.echo("Cleanup complete.")
    return 0
