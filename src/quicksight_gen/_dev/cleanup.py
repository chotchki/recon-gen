"""QuickSight resource sweep helpers (Y.2.gate.f.9).

Lifted from ``tests/e2e/_harness_cleanup.py`` (originally M.4.1.a) so
the runner's ``cmd_sweep`` can import them without the
``sys.path``-into-``tests/e2e/`` dance the harness layer required. The
harness layer drops with f.9; these helpers stay because production
e2e tests (``test_l1_*``, ``test_inv_*``, ``test_exec_*``,
``test_l2ft_*``) still tag their per-test resources with
``Harness:e2e`` (the tag name is historical; the new name "harness"
just means "ephemeral test resource" now).

Two surfaces:

1. ``sweep_qs_resources_by_tag(client, account_id, tag_key, tag_value)``
   — list every QuickSight resource (dashboard / analysis / dataset /
   theme / datasource), filter by an `(extra_tag_key, extra_tag_value)`
   pair the test fixture injects via ``cfg.extra_tags``, and delete in
   dependency order. Returns a count of deletions for triage.

2. ``_collect_resources_matching_tag`` — same walk without the delete,
   for dry-run mode.

Dropped from the original module: ``drop_prefixed_schema`` (DB-side
cleanup that only the legacy harness used; teardown of per-test
schemas now happens via the test's own DROP statements or the
container's auto-teardown).
"""

from __future__ import annotations

from typing import Any


# QS resource types swept in dependency order: dashboards reference
# analyses, analyses reference datasets, datasets reference datasources +
# themes. Datasource swept LAST (after datasets) since datasets reference
# it; theme is independent. M.4.1 option 2 — the per-test fixture creates
# its OWN datasource (vs the earlier shared-production-datasource pattern),
# so the sweep deletes it.
_QS_DELETION_ORDER = (
    "dashboard", "analysis", "dataset", "datasource", "theme",
)


def sweep_qs_resources_by_tag(
    client: Any,  # typing-smell: ignore[explicit-any]: boto3 quicksight client has no PEP 561 stubs; usage is generic enough that a Protocol would be all-Any anyway
    account_id: str,
    *,
    tag_key: str,
    tag_value: str,
) -> dict[str, int]:
    """Delete every QS resource carrying ``tag_key == tag_value``.

    Walks dashboards / analyses / datasets / datasources / themes; for
    each, calls ``list_tags_for_resource`` on its ARN; if the tag
    matches, deletes.

    Returns a dict ``{resource_type: deletion_count}`` for triage.

    Robust against partial failures: a delete that errors out is
    logged to stderr but does not abort the sweep — the next test
    needs the rest of the sweep to land or its deploy collides on
    leftover IDs.
    """
    matched = _collect_resources_matching_tag(
        client, account_id, tag_key=tag_key, tag_value=tag_value,
    )
    counts: dict[str, int] = {}
    for kind in _QS_DELETION_ORDER:
        items = matched.get(kind, [])
        deleted = 0
        for resource_id, _arn in items:
            try:
                _delete_one(client, account_id, kind, resource_id)
                deleted += 1
            except Exception as exc:  # noqa: BLE001 — best-effort sweep
                # Per-test cleanup must continue past one bad delete so
                # the rest of the sweep still lands. Bubble the message
                # to stderr.
                import sys
                print(
                    f"[qs-sweep] {kind} {resource_id!r} delete failed: "
                    f"{exc}",
                    file=sys.stderr,
                )
        counts[kind] = deleted
    return counts


def _collect_resources_matching_tag(
    client: Any,  # typing-smell: ignore[explicit-any]: boto3 quicksight client has no PEP 561 stubs
    account_id: str,
    *,
    tag_key: str,
    tag_value: str,
) -> dict[str, list[tuple[str, str]]]:
    """Return ``{kind: [(id, arn), ...]}`` for resources carrying the tag."""
    matched: dict[str, list[tuple[str, str]]] = {
        kind: [] for kind in _QS_DELETION_ORDER
    }
    iterators = {
        "dashboard": _iter_dashboards,
        "analysis": _iter_analyses,
        "dataset": _iter_datasets,
        "datasource": _iter_datasources,
        "theme": _iter_themes,
    }
    for kind, it in iterators.items():
        for resource_id, arn in it(client, account_id):
            if not _tag_matches(client, arn, tag_key, tag_value):
                continue
            matched[kind].append((resource_id, arn))
    return matched


def _tag_matches(
    client: Any, arn: str, tag_key: str, tag_value: str,  # typing-smell: ignore[explicit-any]: boto3 quicksight client
) -> bool:
    """True if the resource's tags include the (key, value) pair."""
    try:
        resp = client.list_tags_for_resource(ResourceArn=arn)
    except Exception:  # noqa: BLE001 — read failure means "not ours"
        return False
    for tag in resp.get("Tags", []):
        if tag.get("Key") == tag_key and tag.get("Value") == tag_value:
            return True
    return False


def _delete_one(
    client: Any, account_id: str, kind: str, rid: str,  # typing-smell: ignore[explicit-any]: boto3 quicksight client
) -> None:
    if kind == "dashboard":
        client.delete_dashboard(AwsAccountId=account_id, DashboardId=rid)
    elif kind == "analysis":
        # Force-delete bypasses the 30-day recovery window so the next
        # test's deploy doesn't collide on the resurrectable ID.
        client.delete_analysis(
            AwsAccountId=account_id,
            AnalysisId=rid,
            ForceDeleteWithoutRecovery=True,
        )
    elif kind == "dataset":
        client.delete_data_set(AwsAccountId=account_id, DataSetId=rid)
    elif kind == "datasource":
        client.delete_data_source(AwsAccountId=account_id, DataSourceId=rid)
    elif kind == "theme":
        client.delete_theme(AwsAccountId=account_id, ThemeId=rid)
    else:
        raise ValueError(f"unknown QS resource kind: {kind!r}")


def _iter_dashboards(client: Any, account_id: str):  # typing-smell: ignore[explicit-any]: boto3 quicksight client
    paginator = client.get_paginator("list_dashboards")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("DashboardSummaryList", []):
            yield item["DashboardId"], item["Arn"]


def _iter_analyses(client: Any, account_id: str):  # typing-smell: ignore[explicit-any]: boto3 quicksight client
    paginator = client.get_paginator("list_analyses")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("AnalysisSummaryList", []):
            # Skip soft-deleted analyses (DELETED status) — they're
            # already on the way out and a second delete returns a
            # 4xx that confuses triage.
            if item.get("Status") == "DELETED":
                continue
            yield item["AnalysisId"], item["Arn"]


def _iter_datasets(client: Any, account_id: str):  # typing-smell: ignore[explicit-any]: boto3 quicksight client
    paginator = client.get_paginator("list_data_sets")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("DataSetSummaries", []):
            yield item["DataSetId"], item["Arn"]


def _iter_datasources(client: Any, account_id: str):  # typing-smell: ignore[explicit-any]: boto3 quicksight client
    paginator = client.get_paginator("list_data_sources")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("DataSources", []):
            yield item["DataSourceId"], item["Arn"]


def _iter_themes(client: Any, account_id: str):  # typing-smell: ignore[explicit-any]: boto3 quicksight client
    paginator = client.get_paginator("list_themes")
    for page in paginator.paginate(AwsAccountId=account_id):
        for item in page.get("ThemeSummaryList", []):
            yield item["ThemeId"], item["Arn"]
