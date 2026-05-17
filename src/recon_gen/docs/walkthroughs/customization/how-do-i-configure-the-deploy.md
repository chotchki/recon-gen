# How do I configure the deploy for my AWS account?

*Customization walkthrough — Developer / Product Owner. Setup.*

## The story

You've decided the data side fits
([How do I map my production database?](how-do-i-map-my-database.md))
and you're ready to point the generator at your AWS account. The
deploy side is a single YAML file plus an existing QuickSight
datasource ARN — the same shape used in development, staging, and
production, distinguished by which file the CLI reads.

This walkthrough covers what each `config.yaml` field controls,
which fields are required vs optional, the env-var override
pattern for CI / multi-environment deploys, and the demo vs
production datasource distinction.

## The question

"What do I put in `config.yaml` for my AWS account, and what's
the minimum to get a first deploy through?"

## Where to look

Three reference points:

- **`examples/config.yaml`** — the canonical template. Every
  field documented inline. Copy it to your working directory
  and edit.
- **`src/recon_gen/common/config.py`** — the `Config`
  dataclass. Source of truth for field names, defaults, and
  env-var mappings.
- **`run/config.yaml`** (your own) — convention for keeping
  local production config out of git. The repo's `.gitignore`
  excludes `run/`; mount your real account ID, ARN, and
  principal there. Pass `-c run/config.yaml` on every CLI
  invocation, or `cd run/` to make it the default.

## What you'll see in the demo

The example config from `examples/config.yaml`:

```yaml
aws_account_id: "111122223333"
aws_region: "us-east-1"

datasource_arn: "arn:aws:quicksight:us-east-1:111122223333:datasource/example-datasource"

deployment_name: "recon-prod"
db_table_prefix: "recon_prod"

# Theme is declared inline on the L2 institution YAML, not here
# (N.4.j). When the L2 instance carries no ``theme:`` block, AWS
# QuickSight CLASSIC takes over at deploy.

principal_arns:
  - "arn:aws:quicksight:us-east-1:111122223333:user/default/example-user"

# dialect: "postgres"  # or "oracle" — defaults to "postgres"
# demo_database_url: "postgresql://user:password@host:5432/dbname"
# demo_database_url: "user/password@host:1521/SERVICE"  # Oracle Easy Connect form
```

Six required fields (account, region, datasource ARN,
`deployment_name`, `db_table_prefix`, at least one principal) and two
optional demo fields (`dialect`, `demo_database_url`). That's the
entire deploy contract.

## What it means

Each field, what it controls, and what breaks if you set it wrong:

### Required for any deploy

- **`aws_account_id`** — the 12-digit AWS account ID where
  resources are created. The generator embeds this in every
  ARN and tag. Wrong value: deploy targets the wrong account
  (or fails with a permissions error, depending on your IAM
  setup).
- **`aws_region`** — the AWS region where QuickSight
  resources live. **Important:** this is the region of your
  *dashboard* deployment, not the QuickSight identity region
  (which is always `us-east-1`). Wrong value: deploy creates
  resources in the wrong region; the dashboard URL points
  somewhere your users can't access.
- **`datasource_arn`** — the ARN of an existing QuickSight
  datasource pointing at your warehouse. The generator does
  *not* create datasources for you — they require credentials
  and VPC config that don't belong in this tool. Pre-provision
  the datasource via the QuickSight console (or your IaC), then
  paste the ARN here.

### Required for production-grade deploys

- **`principal_arns`** — IAM principals granted permissions on
  every generated resource (theme, analyses, datasets,
  dashboards). Accept a single string or a list. Without at
  least one principal, the generated resources have no
  explicit permissions — the resource owner (the IAM user /
  role running the deploy) gets implicit access via
  CreateAnalysis but no other principal can see the dashboards.
  Production: list the QuickSight user / group ARNs that
  should have edit + view access.

### Common knobs

- **`deployment_name`** (required, no default — Z.C) — prefix prepended to
  every QS resource ID. Useful for multi-tenant deploys (one
  account hosting dashboards for multiple business units —
  `recon-team-a` / `recon-team-b` namespaces keep them visually
  separable in the QuickSight console). The cleanup command uses the
  `ManagedBy` + `Deployment` tag pair (not the ID prefix), so
  changing `deployment_name` is safe — it doesn't orphan old
  resources, just shifts where new ones land.
- **`db_table_prefix`** (required, no default — Z.C) — prefix prepended
  to every emitted DB table / matview / dataset name. Pick a value
  that's a valid SQL identifier (lowercase, alphanumeric + underscore,
  ≤30 chars). Typically tracks `deployment_name` (e.g.
  `deployment_name: recon-myorg-prod` + `db_table_prefix:
  recon_myorg_prod`).
- **`extra_tags`** — dict of extra AWS tags to apply to every
  resource alongside the always-on `ManagedBy:recon-gen`
  tag. Use for cost allocation (`CostCenter: treasury`),
  ownership (`Owner: gl-recon`), or environment
  (`Environment: prod`). The deploy refreshes tags on every
  run.

> **Note (v3.8.0):** the prior `late_default_days` knob is gone.
> Lateness is now data-driven — each transaction row carries an
> optional `expected_complete_at` timestamp, and the generated
> SQL surfaces an `is_late` column that flips when
> `CURRENT_TIMESTAMP > COALESCE(expected_complete_at,
> posted_at + INTERVAL '1 day')`. See the ETL handbook section
> on `expected_complete_at` for the population contract.

### Demo-only

- **`dialect`** (default `postgres`) — which database family the demo
  feeds. Accepts `postgres` or `oracle`. Drives every dialect-aware
  emit decision (DDL types, matview options, recursive-CTE alias
  shape, JSON literal form, datasource Type field on the QuickSight
  resource). Set it in the YAML, not via env var, since it has to
  match the schema that's already on disk for tests.
- **`demo_database_url`** — connection string for the demo flow
  (`schema apply` / `data apply` / `data refresh`) to
  write seed data. Two URL shapes are accepted:
  - **Postgres**: `postgresql://user:pass@host:5432/dbname`
  - **Oracle (Easy Connect)**: `user/pass@host:1521/SERVICE` (no
    scheme prefix; use the same form the `oracledb` thin driver
    accepts). The SQLAlchemy form
    `oracle+oracledb://user:pass@host:1521/?service_name=ORCL` also
    works.

  When set and `datasource_arn` is omitted, the generator derives the
  ARN automatically
  (`{aws_region}:{aws_account_id}:datasource/{deployment_name}-demo-datasource`).
  In production, leave this unset and provide the explicit
  `datasource_arn`.

> **Oracle on RDS — TLS quirk.** RDS Oracle disables TLS by default
> (you have to attach an option group to turn it on). The generated
> QuickSight datasource sets `SslProperties.DisableSsl=True` on the
> Oracle path so the QS-side TLS probe doesn't drop the connection in
> ~2ms. Postgres on RDS forces TLS, so we leave `DisableSsl=False`
> there. If you turn TLS on for your RDS Oracle instance, edit
> `common/datasource.py::build_datasource` to flip the Oracle SSL
> default — there's no config knob yet.

> **Oracle service name vs SID.** The QuickSight datasource emits
> `OracleParameters.UseServiceName=True` (RDS Oracle expects service
> names, not SIDs, against `FREEPDB1` / your custom service). If you
> need SID semantics, edit `common/models.py::OracleParameters` to
> set `UseServiceName=False`.

> **`oracledb` thin mode.** The `[demo-oracle]` extra installs
> `oracledb>=2.0` which runs in *thin* mode by default — no Oracle
> Instant Client install needed. The `data apply` CLI uses thin mode
> directly; you don't need an `LD_LIBRARY_PATH`-style setup on the
> integrator host.

## Drilling in

A few patterns to know once the basic config works:

### Env-var overrides (CI / multi-environment)

Every field has a `QS_GEN_*` env var that overrides the YAML.
The mapping (from `config.py:90-98`):

| YAML field          | Env var                          |
|---------------------|----------------------------------|
| `aws_account_id`    | `QS_GEN_AWS_ACCOUNT_ID`          |
| `aws_region`        | `QS_GEN_AWS_REGION`              |
| `datasource_arn`    | `QS_GEN_DATASOURCE_ARN`          |
| `deployment_name`   | `QS_GEN_DEPLOYMENT_NAME`         |
| `db_table_prefix`   | `QS_GEN_DB_TABLE_PREFIX`         |
| `principal_arns`    | `QS_GEN_PRINCIPAL_ARNS` (CSV)    |
| `demo_database_url` | `QS_GEN_DEMO_DATABASE_URL`       |
| `dialect`           | (YAML only — see Demo-only)      |

CI pattern: commit `examples/config.yaml` as the staging
template, override `QS_GEN_AWS_ACCOUNT_ID` /
`QS_GEN_DATASOURCE_ARN` per environment in the CI runner. No
per-environment YAML files to maintain.

### Production datasource ARN vs demo connection string

The two are mutually exclusive in practice:

- **Production**: `datasource_arn` points at a QuickSight
  datasource you've already created (typically a Postgres,
  Oracle, Athena, or Redshift datasource via the QuickSight
  console or Terraform). The deploy never touches the
  datasource; it only references the ARN.
- **Demo**: `demo_database_url` is a connection string for the
  dialect you set on `dialect:`. The demo flow
  (`recon-gen schema apply --execute && recon-gen
  data apply --execute && recon-gen data refresh
  --execute`) runs your schema + seed against this URL, then
  writes a `datasource.json` describing a QuickSight datasource
  pointing at the same database (Type=`POSTGRESQL` or `ORACLE`,
  dispatched off `dialect`). The deploy creates that datasource
  as part of the run.

If you set both, the explicit `datasource_arn` wins. If you
set neither, `Config.__post_init__` raises with a clear
"datasource_arn is required unless demo_database_url is set"
error.

### Principals — single string vs list

Accept both shapes:

```yaml
# Single string
principal_arns: "arn:aws:quicksight:us-east-1:111122223333:user/default/alice"

# List
principal_arns:
  - "arn:aws:quicksight:us-east-1:111122223333:user/default/alice"
  - "arn:aws:quicksight:us-east-1:111122223333:group/default/treasury"

# Legacy single key (still works)
principal_arn: "arn:aws:quicksight:us-east-1:111122223333:user/default/alice"
```

Group ARNs are valid; the deploy treats them identically to
user ARNs. For team-wide access, prefer one group ARN over
many user ARNs — easier to maintain when team members rotate.

### Why no `--profile` flag

The generator uses boto3's default credential resolution
(env vars → `~/.aws/credentials` → instance profile). To
target a specific profile, set `AWS_PROFILE` in the
environment before invoking. This keeps the generator's
config focused on what's *generated* rather than how the
caller authenticates.

## Next step

Once your `config.yaml` is in place:

1. **Generate to validate the config.** `recon-gen
   json apply -c config.yaml -o out/` writes the JSON
   without touching AWS. Inspect `out/` — confirm the
   prefix, theme, and analysis name look right.
2. **Run a dry-run cleanup.** `recon-gen json clean
   -c config.yaml` lists what *would* be deleted
   under the `ManagedBy:recon-gen` tag. On a fresh
   account this is empty; if you see unexpected resources,
   investigate before running a real deploy.
3. **Walk
   [How do I run my first deploy?](how-do-i-run-my-first-deploy.md)** —
   the actual `json apply --execute` invocation, what to watch
   for during the delete-then-create cycle, and how to confirm
   the dashboard renders.

## Related walkthroughs

- [How do I run my first deploy?](how-do-i-run-my-first-deploy.md) —
  the **next step**: actually invoking `json apply --execute`
  with the config you've just written.
- [How do I reskin the dashboards for my brand?](how-do-i-reskin-the-dashboards.md) —
  the inline ``theme:`` block on the L2 institution YAML; how to
  declare your brand colors per institution.
- [How do I map my production database to the two base tables?](how-do-i-map-my-database.md) —
  the upstream prerequisite. Deploy assumes your data is
  already landing in the two base tables (or the warehouse
  views your custom dataset SQL points at).
