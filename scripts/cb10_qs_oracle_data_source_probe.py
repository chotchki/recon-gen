"""CB.10 spike probe — verify QuickSight can connect to Docker Oracle via hotchkiss.io.

Sibling of `cb10_qs_data_source_probe.py` (the PG version) targeting
Oracle 19c instead. Same shape: wipe leftover spike data sources,
create the Oracle data source pointing at hotchkiss.io:1521, poll for
CREATION_SUCCESSFUL, on success probe a data set against cb10_probe,
always clean up on exit.

The Oracle service name is configurable via `--service` (default
ORCLPDB1 — the doctorkirk/oracle-19c image's pluggable DB; adjust
based on `lsnrctl status` output if different).

Pre-conditions:
- `aws login` (or `aws sso login --profile recon-gen-local`) has fresh
  creds
- Docker Oracle running on the Windows runner, port 1521 forwarded
  via portproxy + Windows Firewall (see CB.10 PG spike for the
  pattern; only the port number changes)
- A `cb10_probe` table exists in the right schema with 3 sample rows

The Docker Oracle command (fire on the Windows WSL before running):

    docker run -d --name cb10-spike-oracle \\
      --restart unless-stopped \\
      -e ORACLE_SID=FREEPDB1 \\
      -e ORACLE_PWD=cb10spike \\
      -e ORACLE_CHARACTERSET=UTF8 \\
      -p 0.0.0.0:1521:1521 \\
      doctorkirk/oracle-19c

Then seed (find the actual service name first via lsnrctl):

    docker exec -i cb10-spike-oracle sqlplus \\
      "system/cb10spike@//localhost:1521/<SERVICE_NAME>" <<'SQL'
    CREATE TABLE cb10_probe (id NUMBER(10), name VARCHAR2(100), posting TIMESTAMP);
    INSERT INTO cb10_probe VALUES (1, 'spike-row-alpha', SYSTIMESTAMP);
    INSERT INTO cb10_probe VALUES (2, 'spike-row-beta',  SYSTIMESTAMP);
    INSERT INTO cb10_probe VALUES (3, 'spike-row-gamma', SYSTIMESTAMP);
    COMMIT;
    SQL
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Any

import boto3
import yaml


_CFG = "run/config.oracle.yaml"


def _qs_user_arn(session: boto3.Session, account_id: str, region: str) -> str:
    """Resolve the single QS user's ARN. See PG sibling for context."""
    qs = session.client("quicksight", region_name=region)
    users = qs.list_users(AwsAccountId=account_id, Namespace="default")
    return users["UserList"][0]["Arn"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--service", default="ORCLPDB1",
        help="Oracle service name (the doctorkirk/oracle-19c image "
             "defaults to ORCLPDB1 as the pluggable DB; ORCLCDB is "
             "the container DB)",
    )
    args = parser.parse_args()

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
            if ds["DataSourceId"].startswith("cb10-spike-oracle-")
        ]
        for ds_id in leftovers:
            print(f"  wiping {ds_id}")
            qs.delete_data_source(AwsAccountId=account_id, DataSourceId=ds_id)
    except Exception as exc:
        print(f"  cleanup pre-step failed (non-fatal): {exc}")

    try:
        listed_ds = qs.list_data_sets(AwsAccountId=account_id)
        leftover_dsets = [
            ds["DataSetId"] for ds in listed_ds.get("DataSetSummaries", [])
            if ds["DataSetId"].startswith("cb10-spike-oracle-")
        ]
        for dset_id in leftover_dsets:
            print(f"  wiping data set {dset_id}")
            qs.delete_data_set(AwsAccountId=account_id, DataSetId=dset_id)
    except Exception as exc:
        print(f"  dataset cleanup pre-step failed (non-fatal): {exc}")

    # Step 2: create the data source.
    suffix = int(datetime.now().timestamp())
    ds_id = f"cb10-spike-oracle-{suffix}"
    print()
    print(f"--- Creating QS Oracle data source {ds_id} ---")
    print(f"  Server=hotchkiss.io Port=1521 Database={args.service}")
    print(f"  SslMode=disabled, Credentials=system/cb10spike")

    create_resp = qs.create_data_source(
        AwsAccountId=account_id,
        DataSourceId=ds_id,
        Name=ds_id,
        Type="ORACLE",
        DataSourceParameters={
            "OracleParameters": {
                "Host": "hotchkiss.io",
                "Port": 1521,
                "Database": args.service,
            },
        },
        Credentials={
            "CredentialPair": {
                "Username": "system",
                "Password": "cb10spike",
            },
        },
        SslProperties={"DisableSsl": True},
        Tags=[{"Key": "ManagedBy", "Value": "cb10-spike-oracle"}],
    )
    print(f"  create_data_source returned: {create_resp.get('CreationStatus')}")

    # Step 3: poll for creation outcome.
    final_status = "UNKNOWN"
    error_info: dict[str, Any] = {}
    desc: dict[str, Any] = {}
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

    # Step 4: surface findings.
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

    # Step 5: data set probe.
    print()
    print(f"--- Probing data set creation against cb10_probe ---")
    dset_id = f"cb10-spike-oracle-dataset-{suffix}"
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
                        "Schema": "SYSTEM",  # Oracle schemas are case-sensitive uppercase
                        "Name": "CB10_PROBE",  # Oracle case-folds unquoted ids to upper
                        "InputColumns": [
                            {"Name": "ID", "Type": "INTEGER"},
                            {"Name": "NAME", "Type": "STRING"},
                            {"Name": "POSTING", "Type": "DATETIME"},
                        ],
                    },
                },
            },
            ImportMode="DIRECT_QUERY",
            Permissions=[
                {
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
            Tags=[{"Key": "ManagedBy", "Value": "cb10-spike-oracle"}],
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
