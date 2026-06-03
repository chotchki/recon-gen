"""CB.10 spike probe — verify QuickSight can connect to Docker PG via hotchkiss.io.

Drives the QS side end-to-end:
1. Wipe any leftover cb10-spike-* resources from prior probe runs.
2. Create a PostgreSQL data source pointing at hotchkiss.io:5432 with
   SSL disabled (the simplest path; cert chains are a follow-up).
3. Poll describe_data_source for up to 60s until status is
   CREATION_SUCCESSFUL or CREATION_FAILED.
4. On success: create a data set against the `cb10_probe` table that
   the operator's docker run command seeded; verify column inference
   via describe_data_set; clean up.
5. On failure: print the ErrorInfo (the QS-side connection error —
   what the spike is here to learn).

Always cleans up (try/finally) so a crashed run doesn't leak QS
resources counting against the per-account data source quota.

Outputs a findings table to stdout. The operator transcribes to
docs/audits/cb_10_qs_docker_pg_spike.md after the run.

Pre-conditions:
- `aws login` (or `aws sso login --profile recon-gen-local`) has
  fresh creds.
- Docker PG running on the Windows runner with the seed data — see
  the README at the top of the script for the exact `docker run`.

The Docker PG command (fire on the Windows WSL before running this):

    docker run -d --name cb10-spike-pg \\
      --restart unless-stopped \\
      -e POSTGRES_PASSWORD=cb10spike \\
      -e POSTGRES_DB=quicksight \\
      -p 0.0.0.0:5432:5432 \\
      postgres:17-alpine && \\
    sleep 3 && \\
    docker exec -i cb10-spike-pg psql -U postgres -d quicksight <<'SQL'
    CREATE TABLE cb10_probe (id INT, name TEXT, posting TIMESTAMP);
    INSERT INTO cb10_probe VALUES
      (1, 'spike-row-alpha', NOW()),
      (2, 'spike-row-beta',  NOW()),
      (3, 'spike-row-gamma', NOW());
    SQL
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import Any

import boto3
import yaml


_CFG = "run/config.postgres.yaml"


def _qs_user_arn(session: boto3.Session, account_id: str, region: str) -> str:
    """Resolve the single QS user's ARN in this account.

    Per [[project_qs_e2e_user_arn]] there's one QS user
    (`recon-gen-admin`) in the default namespace.
    """
    qs = session.client("quicksight", region_name=region)
    users = qs.list_users(AwsAccountId=account_id, Namespace="default")
    return users["UserList"][0]["Arn"]


def main() -> int:
    with open(_CFG) as f:
        cfg = yaml.safe_load(f)
    account_id = cfg["aws_account_id"]
    region = cfg["aws_region"]
    profile = cfg.get("auth", {}).get("aws_profile")

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    qs = session.client("quicksight", region_name=region)

    # Step 1: wipe leftover cb10-spike-* resources from prior runs.
    print(f"--- Cleaning up prior cb10-spike-* resources ---")
    try:
        listed = qs.list_data_sources(AwsAccountId=account_id)
        leftovers = [
            ds["DataSourceId"] for ds in listed.get("DataSources", [])
            if ds["DataSourceId"].startswith("cb10-spike-")
        ]
        for ds_id in leftovers:
            print(f"  wiping {ds_id}")
            qs.delete_data_source(AwsAccountId=account_id, DataSourceId=ds_id)
    except Exception as exc:
        print(f"  cleanup pre-step failed (non-fatal): {exc}")

    # Also wipe leftover datasets, in case a prior run created one
    # then failed before deleting it.
    try:
        listed_ds = qs.list_data_sets(AwsAccountId=account_id)
        leftover_dsets = [
            ds["DataSetId"] for ds in listed_ds.get("DataSetSummaries", [])
            if ds["DataSetId"].startswith("cb10-spike-")
        ]
        for dset_id in leftover_dsets:
            print(f"  wiping data set {dset_id}")
            qs.delete_data_set(AwsAccountId=account_id, DataSetId=dset_id)
    except Exception as exc:
        print(f"  dataset cleanup pre-step failed (non-fatal): {exc}")

    # Step 2: create the data source.
    suffix = int(datetime.now().timestamp())
    ds_id = f"cb10-spike-{suffix}"
    print()
    print(f"--- Creating QS data source {ds_id} ---")
    print(f"  Server=hotchkiss.io Port=5432 Database=quicksight")
    print(f"  SslMode=disabled, Credentials=postgres/cb10spike")

    create_resp = qs.create_data_source(
        AwsAccountId=account_id,
        DataSourceId=ds_id,
        Name=ds_id,
        Type="POSTGRESQL",
        DataSourceParameters={
            "PostgreSqlParameters": {
                "Host": "hotchkiss.io",
                "Port": 5432,
                "Database": "quicksight",
            },
        },
        Credentials={
            "CredentialPair": {
                "Username": "postgres",
                "Password": "cb10spike",
            },
        },
        SslProperties={"DisableSsl": True},
        Tags=[{"Key": "ManagedBy", "Value": "cb10-spike"}],
    )
    print(f"  create_data_source returned: {create_resp.get('CreationStatus')}")

    # Step 3: poll for creation outcome.
    final_status = "UNKNOWN"
    error_info: dict[str, Any] = {}
    for i in range(60):
        try:
            desc = qs.describe_data_source(
                AwsAccountId=account_id, DataSourceId=ds_id,
            )
            ds_obj = desc["DataSource"]
            status = ds_obj.get("Status")
            error_info = ds_obj.get("ErrorInfo", {})
            if status in ("CREATION_SUCCESSFUL", "CREATION_FAILED"):
                final_status = status
                print(f"  status after {i + 1}s: {status}")
                break
            if (i + 1) % 5 == 0:
                print(f"  ...still {status} after {i + 1}s")
        except Exception as exc:
            print(f"  describe_data_source raised at {i + 1}s: {exc}")
        time.sleep(1)
    else:
        print(f"  polling timed out after 60s; last status: {final_status}")

    # Step 4: on failure, surface the ErrorInfo (the headline finding).
    print()
    print(f"--- Findings ---")
    print(f"  final status:     {final_status}")
    if error_info:
        print(f"  error type:       {error_info.get('Type')}")
        print(f"  error message:    {error_info.get('Message')}")
    if final_status != "CREATION_SUCCESSFUL":
        print()
        print(f"  Cleaning up failed data source {ds_id}...")
        try:
            qs.delete_data_source(AwsAccountId=account_id, DataSourceId=ds_id)
        except Exception as exc:
            print(f"  delete failed (might already be gone): {exc}")
        return 1

    # Step 5: success path — try a data set against cb10_probe.
    print()
    print(f"--- Probing data set creation against cb10_probe ---")
    dset_id = f"cb10-spike-dataset-{suffix}"
    try:
        ds_arn = desc["DataSource"]["Arn"]
        qs.create_data_set(
            AwsAccountId=account_id,
            DataSetId=dset_id,
            Name=dset_id,
            PhysicalTableMap={
                "cb10-probe-table": {
                    "RelationalTable": {
                        "DataSourceArn": ds_arn,
                        "Name": "cb10_probe",
                        "InputColumns": [
                            {"Name": "id", "Type": "INTEGER"},
                            {"Name": "name", "Type": "STRING"},
                            {"Name": "posting", "Type": "DATETIME"},
                        ],
                    },
                },
            },
            ImportMode="DIRECT_QUERY",
            Permissions=[
                {
                    # CB.10 spike — QS principal (not IAM). Per
                    # [[project_qs_e2e_user_arn]] there's one QS user
                    # in this account post-AD recreation. Cache it via
                    # list_users → ARN.
                    "Principal": _qs_user_arn(session, account_id, region),
                    "Actions": [
                        "quicksight:DescribeDataSet",
                        "quicksight:DescribeDataSetPermissions",
                        "quicksight:PassDataSet",
                        "quicksight:DescribeIngestion",
                        "quicksight:ListIngestions",
                        "quicksight:UpdateDataSet",
                        "quicksight:DeleteDataSet",
                        "quicksight:CreateIngestion",
                        "quicksight:CancelIngestion",
                        "quicksight:UpdateDataSetPermissions",
                    ],
                },
            ],
            Tags=[{"Key": "ManagedBy", "Value": "cb10-spike"}],
        )
        dset_desc = qs.describe_data_set(
            AwsAccountId=account_id, DataSetId=dset_id,
        )
        cols = dset_desc["DataSet"].get("OutputColumns", [])
        print(f"  data set created; QS inferred {len(cols)} columns:")
        for c in cols:
            print(f"    - {c.get('Name')}: {c.get('Type')}")
    except Exception as exc:
        print(f"  data set probe failed: {exc}")
    finally:
        print()
        print(f"--- Cleaning up ---")
        try:
            qs.delete_data_set(AwsAccountId=account_id, DataSetId=dset_id)
            print(f"  deleted data set {dset_id}")
        except Exception:
            pass
        try:
            qs.delete_data_source(AwsAccountId=account_id, DataSourceId=ds_id)
            print(f"  deleted data source {ds_id}")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
