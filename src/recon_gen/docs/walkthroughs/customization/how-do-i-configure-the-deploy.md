# How do I configure the deploy for my AWS account?

> **QuickSight support is being REMOVED.** This covers the QuickSight deploy, now optional (the `[quicksight]` extra) and going away in an upcoming release (Phase DW). MIGRATE NOW to the self-hosted dashboards (`recon-gen dashboards` / `recon-gen studio`) — the supported path, no AWS account.

*Customization walkthrough — Developer / Product Owner. Setup.*

## The story

The data side fits
([How do I map my production database?](how-do-i-map-my-database.md)),
so now point the generator at your AWS account. The deploy side is one
nested YAML file plus a QuickSight datasource the generator either
creates or adopts — the same shape across development, staging and
production, distinguished only by which file the CLI reads.

This walkthrough covers what each `config.yaml` block controls, which
fields are required vs optional, the env-var override pattern for
CI / multi-environment deploys and the datasource lifecycle (create vs
adopt vs skip).

## The question

"What do I put in `config.yaml` for my AWS account, and what's the
minimum to get a first deploy through?"

## Where to look

Three reference points:

- **`config.example.yaml`** — the canonical template, v14 nested shape.
  Every block documented inline. Copy it to your working directory and
  edit.
- **`src/recon_gen/common/config.py`** — the `Config` dataclass plus
  its nested sub-cfgs (`AwsConfig` / `DbConfig` / `AuthConfig` and the
  rest). Source of truth for field names, defaults and env-var
  mappings.
- **`run/config.yaml`** (your own) — convention for keeping local
  production config out of git. The repo's `.gitignore` excludes
  `run/`; mount your real account ID, ARN and principal there. Pass
  `-c run/config.yaml` on every CLI invocation, or `cd run/` to make it
  the default.

## What you'll see in the demo

The example config from `config.example.yaml`:

```yaml
aws:
  account_id: "111122223333"
  region: "us-east-1"
  deployment_name: "recon-prod"
  datasource:
    mode: create
    # arn: "arn:aws:quicksight:us-east-1:111122223333:datasource/example-datasource"  # required iff mode: adopt
  principal_arns:
    - "arn:aws:quicksight:us-east-1:111122223333:user/default/example-user"

db:
  dialect: "postgres"          # postgres | oracle | duckdb (required)
  table_prefix: "recon_prod"   # optional — defaults to deployment_name with - → _
  # url: "postgresql://user:password@host:5432/dbname"
  # url: "user/password@host:1521/SERVICE"  # Oracle Easy Connect form

# Theme is declared inline on the L2 institution YAML, not here. When the
# L2 instance carries no ``theme:`` block, AWS QuickSight CLASSIC takes over
# at deploy — the look is an institution attribute, so swapping the L2
# swaps the brand.
```

Four fields are required to load: `aws.account_id`, `aws.region`,
`aws.deployment_name` and `db.dialect`. Everything else has a default or
an escape hatch — `db.table_prefix` derives from `deployment_name`,
`aws.datasource` defaults to `mode: create` (which synthesizes the ARN),
and `aws.principal_arns` defaults to empty. `db.url` is required only for
the execute paths (`data apply --execute`, `audit apply --execute`,
Studio + Dashboards). That's the entire deploy contract.

## What it means

Each field, what it controls and what breaks if you set it wrong:

### Required for any deploy

- **`aws.account_id`** — the 12-digit AWS account ID where resources are
  created. The generator embeds this in every ARN and tag. Wrong value:
  deploy targets the wrong account (or fails with a permissions error,
  depending on your IAM setup).
- **`aws.region`** — the AWS region where QuickSight resources live.
  This is the region of your *dashboard* deployment, NOT the QuickSight
  identity region (which is always `us-east-1`). Wrong value: deploy
  creates resources in the wrong region; the dashboard URL points
  somewhere your users can't reach.
- **`aws.deployment_name`** — prefix prepended to every QS resource ID,
  required (no default — loud-fail at load). `cfg.aws.prefixed("foo")`
  → `<deployment_name>-foo`. Also stamped as the `Deployment` tag value.
  Useful for multi-tenant deploys (one account hosting dashboards for
  multiple business units — `recon-team-a` / `recon-team-b` namespaces
  stay visually separable in the QuickSight console). The cleanup
  command gates on the `ManagedBy` + `Deployment` tag pair, not the ID
  prefix, so changing `deployment_name` is safe — it doesn't orphan old
  resources, just shifts where new ones land.
- **`db.dialect`** (required) — which database family backs the deploy.
  Accepts `postgres`, `oracle` or `duckdb`. Drives every dialect-aware
  emit decision (DDL types, matview options, recursive-CTE alias shape,
  JSON literal form, datasource Type field on the QuickSight resource).
  It has to match the datasource and the schema already on disk, so set
  it in the YAML rather than leaning on the env override for routine
  work. `duckdb` is the local-iteration / Studio default; `postgres` and
  `oracle` are the deploy-target dialects.

### The datasource — create, adopt or skip

`aws.datasource.mode` picks how the generator handles the QuickSight
datasource. Three modes:

- **`create`** (default) — the generator creates the QS datasource if
  it's absent and updates it if present. The `arn` auto-derives from
  account + region + deployment_name
  (`arn:<partition>:quicksight:<region>:<account_id>:datasource/<deployment_name>-demo-datasource`),
  so you leave `arn` unset. This is the demo / first-deploy path — point
  `db.url` at your database and the generator stands up a matching
  datasource as part of the run.
- **`adopt`** — use the explicit `arn` as-is and don't touch the
  datasource API. The generator does NOT create or update it; you
  pre-provision the datasource via the QuickSight console (or your IaC)
  with the credentials and VPC config that don't belong in this tool,
  then paste the ARN here. This is the production path. `arn` is required
  when `mode: adopt`.
- **`skip`** — don't call the QS datasource API at all (the test-mode
  escape hatch).

### Recommended for production-grade deploys

- **`aws.principal_arns`** — IAM principals granted permissions on every
  generated resource (theme, analyses, datasets and dashboards). It's a
  YAML list, one ARN per entry (a bare string is rejected — wrap a
  single ARN in a one-item list). Optional at load, but without at least
  one principal the generated resources carry no explicit permissions —
  the resource owner (the IAM user / role running the deploy) gets
  implicit access via CreateAnalysis, but no other principal can see the
  dashboards. Production: list the QuickSight user / group ARNs that
  should have edit + view access. Group ARNs are valid and treated
  identically to user ARNs; for team-wide access prefer one group ARN
  over many user ARNs (easier to maintain when team members rotate).

### Common knobs

- **`db.table_prefix`** (optional) — prefix prepended to every emitted
  DB table / matview / dataset name. Defaults to `deployment_name` with
  `-` → `_`, so set it only when you want a different value (integrators
  with an established table-prefix convention). Must be a valid SQL
  identifier: snake_case, ≤30 chars (PostgreSQL caps identifiers at 63,
  Oracle 19c+ at 128, but the codebase's longest table suffix eats into
  that budget, hence the ≤30 cap). Loud-fails at load when it doesn't
  match.
- **`aws.extra_tags`** — mapping of extra AWS tags applied to every
  resource alongside the always-on `ManagedBy: recon-gen` +
  `Deployment: <deployment_name>` tags. Use for cost allocation
  (`CostCenter: treasury`), ownership (`Owner: gl-recon`) or environment
  (`Environment: prod`). The deploy refreshes tags on every run.

> **Note — lateness is data-driven, no config knob.** There's no
> `late_default_days` setting (it was removed). Each transaction row
> carries an optional `expected_complete_at` timestamp and the generated
> SQL surfaces an `is_late` column that flips when
> `CURRENT_TIMESTAMP > COALESCE(expected_complete_at, posting +
> INTERVAL '1 day')`. See the ETL handbook section on
> `expected_complete_at` for the population contract.

### Demo connection URL

- **`db.url`** — connection string for the demo flow (`schema apply` /
  `data apply` / `data refresh`) to write seed data, and for Studio +
  Dashboards + the higher test layers to read it. Required for the
  execute paths; a plain `json apply` (no `--execute`) doesn't need it.
  Two URL shapes are accepted:
  - **Postgres**: `postgresql://user:pass@host:5432/dbname`
  - **Oracle (Easy Connect)**: `user/pass@host:1521/SERVICE` (no scheme
    prefix; the same form the `oracledb` thin driver accepts). The
    SQLAlchemy form
    `oracle+oracledb://user:pass@host:1521/?service_name=ORCL` also
    works.
  - **DuckDB**: `duckdb:///path/to/demo.duckdb` (use four slashes for an
    absolute path — `duckdb:////Users/...` — three slashes is relative
    to CWD).

  The dialect routes the URL, so `db.dialect` has to match the URL shape.

> **Oracle on RDS — TLS quirk.** RDS Oracle disables TLS by default (you
> attach an option group to turn it on). The generated QuickSight
> datasource sets `SslProperties.DisableSsl=True` on the Oracle path so
> the QS-side TLS probe doesn't drop the connection in ~2ms. Postgres on
> RDS forces TLS, so we leave `DisableSsl=False` there (flip it per-deploy
> via `aws.qs_disable_pg_ssl: true` when your PG datasource needs SSL
> off). If you turn TLS on for your RDS Oracle instance, edit
> `common/datasource.py::build_datasource` to flip the Oracle SSL default
> — there's no config knob for the Oracle side yet.

> **Oracle service name vs SID.** The QuickSight datasource emits
> `OracleParameters.UseServiceName=True` (RDS Oracle expects service
> names, not SIDs, against `FREEPDB1` / your custom service). For SID
> semantics, edit `common/models.py::OracleParameters` to set
> `UseServiceName=False`.

> **`oracledb` thin mode.** The `prod` extra installs `oracledb>=3.4.0`,
> which runs in *thin* mode by default — no Oracle Instant Client install
> needed. The `data apply` CLI uses thin mode directly; you don't need an
> `LD_LIBRARY_PATH`-style setup on the integrator host.

## Drilling in

A few patterns to know once the basic config works:

### Env-var overrides (CI / multi-environment)

Most fields have a `RECON_GEN_*` env var that overrides the YAML. The
override (`_apply_env_overrides_nested` in `common/config.py`) mutates
the nested cfg dict in place before the typed blocks build, so the env
value travels the same code path as the YAML value. The runner uses this
to inject per-cell DB URL / account / region / dialect without rewriting
cfg yaml per cell.

| Nested YAML field      | Env var                          |
|------------------------|----------------------------------|
| `aws.account_id`       | `RECON_GEN_AWS_ACCOUNT_ID`       |
| `aws.region`           | `RECON_GEN_AWS_REGION`           |
| `aws.datasource.arn`   | `RECON_GEN_DATASOURCE_ARN` (sets `mode: adopt`) |
| `aws.deployment_name`  | `RECON_GEN_DEPLOYMENT_NAME`      |
| `aws.principal_arns`   | `RECON_GEN_PRINCIPAL_ARNS` (CSV) |
| `db.table_prefix`      | `RECON_GEN_DB_TABLE_PREFIX`      |
| `db.url`               | `RECON_GEN_DEMO_DATABASE_URL`    |
| `db.dialect`           | `RECON_GEN_DIALECT`              |

CI pattern: commit `config.example.yaml` (copied to your staging
template) and override `RECON_GEN_AWS_ACCOUNT_ID` /
`RECON_GEN_DATASOURCE_ARN` per environment in the CI runner. No
per-environment YAML files to maintain.

### Production datasource ARN vs demo connection URL

The datasource mode is what splits the two:

- **Production** — `aws.datasource.mode: adopt` with an explicit `arn`
  pointing at a QuickSight datasource you've already created (typically a
  Postgres, Oracle, Athena or Redshift datasource via the QuickSight
  console or Terraform). The deploy never touches the datasource; it only
  references the ARN.
- **Demo** — `aws.datasource.mode: create` (the default) plus a `db.url`
  for the dialect you set on `db.dialect`. The demo flow
  (`recon-gen schema apply --execute && recon-gen data apply --execute &&
  recon-gen data refresh --execute`) runs your schema + seed against that
  URL, and the deploy creates a QuickSight datasource pointing at the same
  database (Type `POSTGRESQL` or `ORACLE`, dispatched off `db.dialect`)
  with the ARN auto-derived from account + region + deployment_name.

See it live: https://recon-gen-spec.hotchkiss.io/

### Why no `--profile` flag

The generator's CLI uses boto3's default credential resolution (env vars
→ `~/.aws/credentials` → instance profile). To target a specific profile
on a direct CLI invocation, set `AWS_PROFILE` in the environment before
invoking — that keeps the generator's config focused on what's
*generated* rather than how the caller authenticates. The one place a
profile lives in cfg is `auth.aws.profile`, which the test runner reads
to inject `AWS_PROFILE` into every subprocess it spawns (so the layered
test chain derives the QuickSight user ARN without an env-var dance); the
plain `json apply` CLI doesn't consult it.

## Next step

Once your `config.yaml` is in place:

1. **Generate to validate the config.** `recon-gen json apply
   -c config.yaml -o out/` writes the JSON without touching AWS. Inspect
   `out/` — confirm the prefix, theme and analysis name look right.
2. **Run a dry-run cleanup.** `recon-gen json clean -c config.yaml` lists
   what *would* be deleted under the `ManagedBy:recon-gen` +
   `Deployment:<deployment_name>` tag pair. On a fresh account this is
   empty; if you see unexpected resources, investigate before running a
   real deploy.
3. **Walk
   [How do I run my first deploy?](how-do-i-run-my-first-deploy.md)** —
   the actual `json apply --execute` invocation, what to watch for during
   the delete-then-create cycle and how to confirm the dashboard renders.

## Related walkthroughs

- [How do I run my first deploy?](how-do-i-run-my-first-deploy.md) —
  the **next step**: actually invoking `json apply --execute` with the
  config you've just written.
- [How do I reskin the dashboards for my brand?](how-do-i-reskin-the-dashboards.md) —
  the inline ``theme:`` block on the L2 institution YAML; how to declare
  your brand colors per institution.
- [How do I map my production database to the two base tables?](how-do-i-map-my-database.md) —
  the upstream prerequisite. Deploy assumes your data is already landing
  in the two base tables (or the warehouse views your custom dataset SQL
  points at).
</content>
</invoke>
