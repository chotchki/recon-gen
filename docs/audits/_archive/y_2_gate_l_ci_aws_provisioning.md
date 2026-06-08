# Y.2.gate.l.0 — CI AWS infra provisioning runbook

**Status:** runbook for one-time manual provisioning. Locks the
"same AWS account, manually provisioned" approach decided
2026-05-09 (chris.hotchkiss).

## Why a separate CI infra?

Today (pre-gate.l), `secrets.QS_GEN_PG_URL` + `secrets.QS_GEN_ORACLE_URL`
in `.github/workflows/e2e.yml` point at the **same** Aurora cluster +
Oracle instance the operator uses for local dev (`database-2` /
`database-3` in account 470656905821). Two consequences:

1. **Local + CI race the schema.** A local `quicksight-gen json apply`
   writes datasets while CI's e2e is mid-run → CI's seed mismatches
   what was deployed. Symptom: spurious e2e failures the operator
   can't reproduce locally because the next local run rewrites the
   schema state.
2. **`up aws` / `down aws` race the lifecycle.** Once gate.l.1 wires
   `start-db-cluster` + `if: always()` `stop-db-cluster` into CI,
   a local session that just brought the cluster up (mid-Claude loop)
   gets its DB stopped out from under it because CI finished and ran
   teardown.

Splitting the infra (one cluster + instance for local, one for CI)
isolates both axes. Cost surface stays minimal because both pairs are
stopped between use (storage cost only ~$0.10/GB-month).

## What you'll provision

| Purpose | Resource | Cluster/Instance ID | Engine | Approximate cost (running) |
| --- | --- | --- | --- | --- |
| CI-only PG | Aurora cluster | `qsgen-ci-aurora` | aurora-postgresql 17.x | ~$0.30/hr (db.r5.large) |
| CI-only Oracle | RDS instance | `qsgen-ci-oracle` | oracle-se2 19c | ~$0.10/hr (db.t3.small SE2) |

Both stopped between CI runs ⇒ ~$0.05/hr storage-only baseline each
(see CI cost dashboard for actuals).

Local dev infra stays as-is:

- `database-2` (Aurora PG)
- `database-3` (Oracle RDS instance)

## Step-by-step

### 1. Provision Aurora PG cluster

AWS Console → RDS → Create database → **Standard create**:

- Engine type: **Aurora (PostgreSQL Compatible)**
- Engine version: 17.x (whatever's current; tests are dialect-portable)
- Templates: **Dev/Test** (NOT Production — saves cost)
- DB cluster identifier: `qsgen-ci-aurora`
- Master username: `postgres` (matches local cfg shape)
- Master password: generate + save to AWS Secrets Manager
- Instance class: **db.r5.large** (smallest Aurora supports; v8.x bumped from db.t3.medium)
- Storage: default (~10 GB Aurora-managed)
- VPC: default VPC
- Public access: **Yes** (CI runners need network reachability;
  alternative is VPC peering which is gate.l.0.alt — out of scope)
- VPC security group: **Create new** with one inbound rule:
  port 5432 from `0.0.0.0/0` (yes, internet-facing — same shape as
  local cluster; gate the access via password not IP)
- Database authentication: Password authentication
- Initial database name: `postgres`

Click Create. Wait ~5-10 min for `available`.

After creation:

```bash
aws rds describe-db-clusters --db-cluster-identifier qsgen-ci-aurora \
  --query 'DBClusters[0].Endpoint' --output text
```

→ note the endpoint. Build the `QS_GEN_PG_URL` for CI secrets:

```
postgresql://postgres:<password-from-secrets-manager>@<endpoint>:5432/postgres
```

### 2. Provision Oracle RDS instance

AWS Console → RDS → Create database → **Standard create**:

- Engine type: **Oracle**
- Edition: **Oracle Standard Edition Two**
- Engine version: 19.x
- Templates: **Dev/Test**
- DB instance identifier: `qsgen-ci-oracle`
- Master username: `admin` (matches local cfg shape)
- Master password: generate + save
- Instance class: **db.t3.small**
- Storage: 20 GB gp2
- VPC + security group: same shape as the Aurora step (port 1521 inbound from 0.0.0.0/0)
- Database authentication: Password authentication
- Initial database name: `ORCL`

Click Create. Wait ~10 min for `available`.

After creation:

```bash
aws rds describe-db-instances --db-instance-identifier qsgen-ci-oracle \
  --query 'DBInstances[0].Endpoint.Address' --output text
```

→ note the endpoint. Build the `QS_GEN_ORACLE_URL`:

```
admin/<password>@<endpoint>:1521/ORCL
```

### 3. IAM policy: grant the OIDC role start/stop permissions

The CI workflows assume `arn:aws:iam::470656905821:role/Github_e2e_testing`
via OIDC. That role needs RDS lifecycle permissions on the new
resources. Edit the role's policy (or attach a new one):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "RDSLifecycle",
      "Effect": "Allow",
      "Action": [
        "rds:StartDBCluster",
        "rds:StopDBCluster",
        "rds:StartDBInstance",
        "rds:StopDBInstance",
        "rds:DescribeDBClusters",
        "rds:DescribeDBInstances"
      ],
      "Resource": [
        "arn:aws:rds:us-east-1:470656905821:cluster:qsgen-ci-aurora",
        "arn:aws:rds:us-east-1:470656905821:db:qsgen-ci-oracle"
      ]
    }
  ]
}
```

Resource-scoped to the two CI identifiers — the local dev clusters
stay outside the role's blast radius.

### 4. Update GitHub Actions secrets

In repo Settings → Secrets and variables → Actions, update / add:

| Secret name | New value |
| --- | --- |
| `QS_GEN_PG_URL` | `postgresql://postgres:<pw>@<aurora-endpoint>:5432/postgres` |
| `QS_GEN_ORACLE_URL` | `admin/<pw>@<oracle-endpoint>:1521/ORCL` |
| `QS_GEN_AWS_PG_CLUSTER_ID` | `qsgen-ci-aurora` |
| `QS_GEN_AWS_ORACLE_INSTANCE_ID` | `qsgen-ci-oracle` |

The first two existed pre-gate.l pointing at `database-2`/`database-3`;
they're being repointed at the CI-dedicated infra.

### 5. (Optional) Add cfg fields to local dev configs

For local `./run_tests.sh up aws` / `down aws` / `status` to act on
the local dev infra, add these fields to `run/config.postgres.yaml`
+ `run/config.oracle.yaml`:

```yaml
# Y.2.gate.l — RDS identifiers for ./run_tests.sh up/down/status
aws_pg_cluster_id: "database-2"
aws_oracle_instance_id: "database-3"
```

**Don't commit these to the repo** if `run/config.*.yaml` is local-only
(check `.gitignore`). If you do commit cfg, scrub the cluster IDs
the same way you scrub passwords — the IDs are not secret but they're
the operator's local-dev shape, which is none of CI's business.

### 6. Verify

Local — should report status without acting on CI infra:

```bash
./run_tests.sh status --cost
```

Expected output:

```
runner: status — local containers
  (none — no persistent local containers)

runner: status — AWS RDS resources
  cluster database-2: stopped  (~$0.05/hr)
  instance database-3: stopped  (~$0.02/hr)
  rough total: ~$0.07/hr (estimates only)
```

CI — first push after the secret update should:

- Run `aws rds start-db-cluster --db-cluster-identifier qsgen-ci-aurora`
  (gate.l.1) before the e2e test step.
- Run `aws rds stop-db-cluster ...` in the `if: always()` post-test
  step regardless of test outcome.

Confirm in the workflow log that both calls fired against
`qsgen-ci-aurora` (NOT `database-2`).

## Rollback

If the CI infra causes problems, revert by repointing the GH secrets
back at `database-2` / `database-3`. CI workflows then operate on the
local dev infra (the pre-gate.l shape). The new CI clusters can stay
provisioned (storage cost is small) or be deleted via console.

## Future: VPC peering (gate.l.0.alt — not in scope)

Public-internet RDS endpoints are the simplest shape. If security
review pushes back on `0.0.0.0/0` ingress, the alternative is
spinning up a VPC peering connection between the CI runner's VPC
(GitHub-hosted runners use the actions/runner-images pool, no fixed
VPC) and a private subnet hosting the RDS resources. That requires
either self-hosted CI runners (operator-managed VPC) or AWS-hosted
GHA runners (paid feature). Not blocking gate.l close-out.
