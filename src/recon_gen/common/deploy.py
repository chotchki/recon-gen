"""Deploy generated QuickSight JSON to AWS — delete-then-create semantics.

Python port of the original ``deploy.sh``. Uses boto3 directly with
a tight poll loop for the async CREATE_ANALYSIS / CREATE_DASHBOARD
workflows. Deletes any existing resource for each ID before creating
a new one so schema drift never causes update-parameter mismatches.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
import click
from botocore.exceptions import ClientError

from recon_gen.common.config import Config

if TYPE_CHECKING:
    from mypy_boto3_quicksight.client import QuickSightClient


POLL_INTERVAL_SECONDS = 5
POLL_MAX_ATTEMPTS = 60  # 5 minutes


@dataclass
class AppFiles:
    """Paths to the analysis/dashboard JSON for a single app."""

    name: str
    analysis_path: Path
    dashboard_path: Path


def _load_app_files(out_dir: Path, app: str) -> AppFiles | None:
    analysis_path = out_dir / f"{app}-analysis.json"
    dashboard_path = out_dir / f"{app}-dashboard.json"
    if not analysis_path.exists() and not dashboard_path.exists():
        return None
    return AppFiles(name=app, analysis_path=analysis_path, dashboard_path=dashboard_path)


def _read_json(path: Path) -> dict[str, Any]:
    # WHY Any: QS JSON payloads are deeply heterogeneous + sometimes carry
    # boto3-flavored nested dicts we re-emit verbatim; full typing would
    # require mirroring the entire QS create-* TypedDict tree.
    return json.loads(path.read_text())


def _wait_for_analysis(
    client: QuickSightClient, account_id: str, analysis_id: str,
) -> bool:
    """Poll describe-analysis until a terminal state. Returns True on success."""
    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        try:
            resp = client.describe_analysis(
                AwsAccountId=account_id, AnalysisId=analysis_id,
            )
        except ClientError as exc:
            click.echo(f"    describe-analysis error: {exc}")
            return False
        status = resp.get("Analysis", {}).get("Status", "UNKNOWN")
        if status in ("CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"):
            click.echo(f"    Status: {status}")
            return True
        if status in ("CREATION_FAILED", "UPDATE_FAILED"):
            click.echo(f"    Status: {status}")
            for err in resp.get("Analysis", {}).get("Errors", []) or []:
                click.echo(f"      {err.get('Message', '')}")
            return False
        if status == "DELETED":
            click.echo("    Status: DELETED (unexpected)")
            return False
        if attempt % 6 == 0:
            click.echo(f"    Still waiting... ({status}, {attempt}/{POLL_MAX_ATTEMPTS})")
        time.sleep(POLL_INTERVAL_SECONDS)
    click.echo(f"    Timed out waiting for analysis {analysis_id}")
    return False


def _wait_for_dashboard(
    client: QuickSightClient, account_id: str, dashboard_id: str,  # typing-smell: ignore[bare-str-id]: dashboard_id comes from callers as raw analyst string
) -> bool:
    """Poll describe-dashboard until a terminal state. Returns True on success."""
    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        try:
            resp = client.describe_dashboard(
                AwsAccountId=account_id, DashboardId=dashboard_id,
            )
        except ClientError as exc:
            click.echo(f"    describe-dashboard error: {exc}")
            return False
        status = resp.get("Dashboard", {}).get("Version", {}).get("Status", "UNKNOWN")
        if status in ("CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"):
            click.echo(f"    Status: {status}")
            return True
        if status in ("CREATION_FAILED", "UPDATE_FAILED"):
            click.echo(f"    Status: {status}")
            for err in resp.get("Dashboard", {}).get("Version", {}).get("Errors", []) or []:
                click.echo(f"      {err.get('Message', '')}")
            return False
        if attempt % 6 == 0:
            click.echo(f"    Still waiting... ({status}, {attempt}/{POLL_MAX_ATTEMPTS})")
        time.sleep(POLL_INTERVAL_SECONDS)
    click.echo(f"    Timed out waiting for dashboard {dashboard_id}")
    return False


def _resource_exists(
    describe_fn: Callable[..., Any], **kwargs: Any,
) -> bool:
    try:
        describe_fn(**kwargs)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return False
        raise


def _delete_dashboards(
    client: QuickSightClient, account_id: str, apps: list[AppFiles],
) -> None:
    for app in apps:
        if not app.dashboard_path.exists():
            continue
        dash_id = _read_json(app.dashboard_path)["DashboardId"]
        click.echo(f"==> Dashboard: {dash_id}")
        if _resource_exists(
            client.describe_dashboard,
            AwsAccountId=account_id, DashboardId=dash_id,
        ):
            click.echo("    Deleting existing dashboard...")
            client.delete_dashboard(AwsAccountId=account_id, DashboardId=dash_id)


def _delete_analyses(
    client: QuickSightClient, account_id: str, apps: list[AppFiles],
) -> None:
    for app in apps:
        if not app.analysis_path.exists():
            continue
        analysis_id = _read_json(app.analysis_path)["AnalysisId"]
        click.echo(f"==> Analysis: {analysis_id}")
        if _resource_exists(
            client.describe_analysis,
            AwsAccountId=account_id, AnalysisId=analysis_id,
        ):
            click.echo("    Deleting existing analysis...")
            client.delete_analysis(
                AwsAccountId=account_id,
                AnalysisId=analysis_id,
                ForceDeleteWithoutRecovery=True,
            )


def _dataset_ids_for_apps(apps: list[AppFiles]) -> set[str]:
    """Derive the DataSetIds each app's analysis references.

    Walks ``Definition.DataSetIdentifierDeclarations`` on every analysis and
    pulls the trailing segment of each ``DataSetArn``
    (``arn:...:dataset/<id>``). Used to scope the dataset delete-then-create
    so that ``deploy <single-app>`` doesn't recreate the *other* app's
    datasets and leave that app's analysis with stale internal references.
    """
    ids: set[str] = set()
    for app in apps:
        if not app.analysis_path.exists():
            continue
        decls = (
            _read_json(app.analysis_path)
            .get("Definition", {})
            .get("DataSetIdentifierDeclarations", [])
        )
        for decl in decls:
            arn = decl.get("DataSetArn", "")
            if "/" in arn:
                ids.add(arn.rsplit("/", 1)[-1])
    return ids


def _delete_datasets(
    client: QuickSightClient,
    account_id: str,
    out_dir: Path,
    allowed_ids: set[str] | None,
) -> None:
    datasets_dir = out_dir / "datasets"
    if not datasets_dir.is_dir():
        return
    for ds_file in sorted(datasets_dir.glob("*.json")):
        ds_id = _read_json(ds_file)["DataSetId"]
        if allowed_ids is not None and ds_id not in allowed_ids:
            continue
        click.echo(f"==> Dataset: {ds_id}")
        if _resource_exists(
            client.describe_data_set,
            AwsAccountId=account_id, DataSetId=ds_id,
        ):
            click.echo("    Deleting existing dataset...")
            client.delete_data_set(AwsAccountId=account_id, DataSetId=ds_id)


def _delete_theme(
    client: QuickSightClient, account_id: str, theme_path: Path,
) -> None:
    if not theme_path.exists():
        return
    theme_id = _read_json(theme_path)["ThemeId"]
    click.echo(f"==> Theme: {theme_id}")
    if _resource_exists(
        client.describe_theme, AwsAccountId=account_id, ThemeId=theme_id,
    ):
        click.echo("    Deleting existing theme...")
        client.delete_theme(AwsAccountId=account_id, ThemeId=theme_id)


def _delete_datasource(
    client: QuickSightClient, account_id: str, datasource_path: Path,
) -> None:
    if not datasource_path.exists():
        return
    ds_id = _read_json(datasource_path)["DataSourceId"]
    click.echo(f"==> DataSource: {ds_id}")
    if _resource_exists(
        client.describe_data_source,
        AwsAccountId=account_id, DataSourceId=ds_id,
    ):
        click.echo("    Deleting existing datasource...")
        client.delete_data_source(AwsAccountId=account_id, DataSourceId=ds_id)


def _create_datasource(
    client: QuickSightClient, datasource_path: Path,
) -> None:
    if not datasource_path.exists():
        return
    payload = _read_json(datasource_path)
    click.echo(f"==> Creating DataSource: {payload['DataSourceId']}")
    client.create_data_source(**payload)


def _create_theme(client: QuickSightClient, theme_path: Path) -> None:
    if not theme_path.exists():
        # N.4.k silent-fallback: when the L2 instance carried no inline
        # ``theme:`` block, ``build_theme`` returned None and the
        # generate step skipped writing ``theme.json``. AWS QuickSight
        # CLASSIC takes over for the dashboards. ``_delete_theme``
        # uses the same guard.
        return
    payload = _read_json(theme_path)
    click.echo(f"==> Creating Theme: {payload['ThemeId']}")
    client.create_theme(**payload)


def _create_datasets(
    client: QuickSightClient,
    out_dir: Path,
    allowed_ids: set[str] | None,
) -> None:
    datasets_dir = out_dir / "datasets"
    if not datasets_dir.is_dir():
        return
    for ds_file in sorted(datasets_dir.glob("*.json")):
        payload = _read_json(ds_file)
        if allowed_ids is not None and payload["DataSetId"] not in allowed_ids:
            continue
        click.echo(f"==> Creating Dataset: {payload['DataSetId']}")
        client.create_data_set(**payload)


def _create_analyses(
    client: QuickSightClient, apps: list[AppFiles],
) -> list[str]:
    created: list[str] = []
    for app in apps:
        if not app.analysis_path.exists():
            continue
        payload = _read_json(app.analysis_path)
        click.echo(f"==> Creating Analysis: {payload['AnalysisId']}")
        # Datasets created in the prior step return success synchronously,
        # but their underlying SQL prep is async. First-time deploys of
        # the L1 dashboard against a fresh data source (no cached prep
        # validation, 16+ datasets, several with window functions /
        # recursive CTEs) can take several minutes to clear
        # PREPARED_SOURCE_NOT_FOUND. Established data sources (deployed
        # before, cached prep) clear in under 30s. Retry up to ~5 min
        # with a 10s pace — long enough for cold-start data source
        # validation, short enough not to mask a real schema bug.
        max_attempts = 30  # ~5 min total
        for attempt in range(1, max_attempts + 1):
            try:
                client.create_analysis(**payload)
                break
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                msg = str(exc)
                if (
                    code == "ResourceNotFoundException"
                    and "PREPARED_SOURCE_NOT_FOUND" in msg
                    and attempt < max_attempts
                ):
                    click.echo(
                        f"    waiting for dataset prep "
                        f"(attempt {attempt}/{max_attempts}, sleeping 10s)…"
                    )
                    time.sleep(10)
                    continue
                raise
        created.append(payload["AnalysisId"])
    return created


def _create_dashboards(
    client: QuickSightClient, apps: list[AppFiles],
) -> list[str]:
    created: list[str] = []
    for app in apps:
        if not app.dashboard_path.exists():
            continue
        payload = _read_json(app.dashboard_path)
        click.echo(f"==> Creating Dashboard: {payload['DashboardId']}")
        client.create_dashboard(**payload)
        created.append(payload["DashboardId"])
    return created


def deploy(cfg: Config, out_dir: Path, app_names: list[str]) -> int:
    """Deploy one or more apps from ``out_dir``. Returns 0 on success.

    ``app_names`` is a list of kebab-case app keys (e.g. ``["payment-recon"]``)
    that maps to ``{app}-analysis.json`` / ``{app}-dashboard.json``.
    Theme / datasets / datasource are shared across apps and deployed
    from whatever is present in ``out_dir``.
    """
    # BF.1.S2: boto3.client overloaded signature picks the right service
    # client at runtime; ``boto3-stubs[quicksight]`` provides the per-service
    # overload but pyright still surfaces the umbrella signature as
    # `partially unknown` until the call is anchored to a typed var. The
    # suppression covers the call expression itself; the annotation pins
    # the var.
    client: QuickSightClient = boto3.client(  # pyright: ignore[reportUnknownMemberType]: boto3.client overloaded union; QuickSightClient annotation anchors the right stub
        "quicksight", region_name=cfg.aws_region,
    )
    account_id = cfg.aws_account_id

    click.echo(f"Deploying QuickSight resources from {out_dir}")
    click.echo(f"  Account: {account_id}")
    click.echo(f"  Region:  {cfg.aws_region}\n")

    apps: list[AppFiles] = []
    for name in app_names:
        files = _load_app_files(out_dir, name)
        if files is None:
            click.echo(f"  (no JSON for {name} in {out_dir}; skipping)")
            continue
        apps.append(files)

    theme_path = out_dir / "theme.json"
    datasource_path = out_dir / "datasource.json"

    # Scope dataset delete-then-create to the apps actually being deployed.
    # `deploy account-recon` previously delete-then-created every dataset
    # file in out_dir/datasets/ (including PR's), leaving the *other* app's
    # analysis with stale internal refs even though the ARNs survived.
    # Allowed-set is derived from each loaded analysis's
    # DataSetIdentifierDeclarations.
    allowed_dataset_ids = _dataset_ids_for_apps(apps)

    # Delete in dependency order
    _delete_dashboards(client, account_id, apps)
    _delete_analyses(client, account_id, apps)
    _delete_datasets(client, account_id, out_dir, allowed_dataset_ids)
    _delete_theme(client, account_id, theme_path)
    _delete_datasource(client, account_id, datasource_path)

    click.echo("\n--- Recreating all resources ---\n")

    _create_datasource(client, datasource_path)
    _create_theme(client, theme_path)
    _create_datasets(client, out_dir, allowed_dataset_ids)
    analysis_ids = _create_analyses(client, apps)
    dashboard_ids = _create_dashboards(client, apps)

    click.echo("\n--- Waiting for async resources ---\n")

    failures = 0
    for aid in analysis_ids:
        click.echo(f"==> Checking Analysis: {aid}")
        if not _wait_for_analysis(client, account_id, aid):
            failures += 1
    for did in dashboard_ids:
        click.echo(f"==> Checking Dashboard: {did}")
        if not _wait_for_dashboard(client, account_id, did):
            failures += 1

    click.echo()
    if failures > 0:
        click.echo(f"Done with {failures} FAILURE(s). Check errors above.")
        return 1
    click.echo(f"Done. All resources deployed to {account_id} in {cfg.aws_region}.")
    return 0
