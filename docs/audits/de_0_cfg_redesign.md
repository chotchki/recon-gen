# DE.0 — cfg.yaml structural redesign (inventory + prototype + locks)

**Date:** 2026-06-13
**Phase:** DE.0
**Status:** Field inventory + locked target shape + `extends:` loader prototype recipe. Ready for DE.1.

## Locks (operator-confirmed in PLAN.md 2026-06-12)

- **`extends:` inheritance is the headline feature.** Operators stop maintaining N near-identical cfg.yamls per environment. A base + tiny per-env overlays that stamp only the deltas.
- **Hard break to v14.0.0.** No compat shim. `Config.load` raises on legacy keys with a migration-hint message.
- **Auto-derive aggressively.** Cfg file is the deltas-from-derived, not the full input.
- **Concern grouping.** `aws: / db: / audit: / app2: / auth: / test:`. Concern won over posture once inheritance entered.
- **`auth:` reshapes to nest the two distinct concerns** — `auth.aws.*` (AWS auth — was top-level `auth:` block pre-DE) + `auth.oidc.*` + `auth.session.*` (Phase DD adds these).
- **`demo_database_url` → `db.url`.**
- **Test-only fields default to optional + collapsed.** Operators on prod-deploy postures never see them.
- **DC + DD coordinate** — DC.1's TLS block lands as `app2.tls.*`; DD.1's OIDC + session blocks land into `auth:`.

- Comment: There is an implicit choice today that based on the precence of the database_url vs qs_datasource_arn whether the qs_datasource gets created or not. I think we should make this explicit (whether the datasource is created at that name or just used). We need this toggle because we can't test the entire datasource connection path due to AWS costs.
  - **Addressed below in "Datasource lifecycle: explicit `aws.datasource.mode`".** The implicit presence-of-key dispatch becomes an explicit enum (`create` / `adopt` / `skip`). The `skip` value covers the no-AWS-cost test path. Locked at DE.0 exit.

## Pre-DE field inventory

`grep "field" src/recon_gen/common/config.py` + cfg.yaml inspection. 22 top-level fields + 3 nested blocks.

| Field | Type | Required | New location | Derivation rule |
|---|---|---|---|---|
| `aws_account_id` | str | yes (deploy) | `aws.account_id` | none — explicit |
| `aws_region` | str | yes (deploy) | `aws.region` | none — explicit |
| `deployment_name` | str | yes (deploy) | `aws.deployment_name` | none — explicit |
| `db_table_prefix` | str | derived | `db.table_prefix` | `aws.deployment_name.replace('-', '_')` |
| `principal_arns` | list[str] | yes (deploy) | `aws.principal_arns` | none — explicit |
| `datasource_arn` | str | optional | `aws.datasource_arn` | derived from `aws.account_id` + `aws.region` + `aws.deployment_name`; explicit override for QS data source adoption |
| `extra_tags` | dict | optional | `aws.extra_tags` | none — explicit |
| `tagging_enabled` | bool | optional | `aws.tagging_enabled` | default true; rare opt-out |
| `qs_disable_pg_ssl` | bool | optional | `aws.qs_disable_pg_ssl` | default false; needed for test forwards |
| `aws_pg_cluster_id` | str | optional | `aws.pg_cluster_id` | for `runner up aws` cluster control |
| `aws_oracle_instance_id` | str | optional | `aws.oracle_instance_id` | same |
| `dialect` | enum | yes | `db.dialect` | none — explicit |
| `demo_database_url` | str | yes (run) | `db.url` | renamed — drop the v1-demo prefix |
| `app2_db_pool_size` | int | optional | `app2.db_pool_size` | default 10 |
| `auth.aws_profile` | str | optional | `auth.aws.profile` | none — explicit |
| `auth.quicksight_user_arn` | str | derived | `auth.aws.quicksight_user_arn` | `list_users(Namespace="default")` → ADMIN role lookup; explicit override only when multiple admins exist |
| `signing.key_path` | path | yes (audit) | `audit.signing.key_path` | none — explicit |
| `signing.cert_path` | path | yes (audit) | `audit.signing.cert_path` | none — explicit |
| `signing.passphrase_env` | str | optional | `audit.signing.passphrase_env` | none — explicit |
| `signing.signer_name` | str | optional | `audit.signing.signer_name` | default "recon-gen audit" |
| `default_l2_instance` | path | optional | `db.default_l2_instance` | absent ⇒ require `--l2` on CLI |
| `etl_hook` | path | optional | `app2.etl_hook` | optional shell hook |
| `banner_text` | str | optional | `app2.banner_text` | studio chrome only |
| `test_generator.*` | misc | optional | `test.generator.*` | fuzz-L2 generation knobs |
| `studio_enabled` | bool | derived | drop | absence of `app2:` block ⇒ studio off |

DC adds: `app2.tls.cert_path` + `app2.tls.key_path` (DC locks "App2-only").
DD adds: `auth.oidc.*` + `auth.session.jwt_secret_env`.

## Target shape (post-DE.1)

```yaml
# Optional — points at a parent cfg this one extends. Path is relative
# to this cfg's directory. Deep-merge by default; lists replace.
# Chain depth is unbounded; loader detects cycles.
extends: ./base.yaml

aws:
  account_id: '470656905821'
  region: us-east-1
  deployment_name: qsgen-postgres
  # principal_arns inherited from base
  qs_disable_pg_ssl: true       # local Docker-PG case

db:
  dialect: postgres
  url: postgresql://test:test@hotchkiss.io:5433/test
  default_l2_instance: tests/l2/sasquatch_pr.yaml

auth:
  aws:
    profile: recon-gen-local
  # oidc: + session: blocks land here when DD ships

app2:
  db_pool_size: 30
  # tls: block lands here when DC ships

audit:
  signing:
    key_path: tests/audit/fixtures/test-signing-key.pem
    cert_path: tests/audit/fixtures/test-signing-cert.pem
```

Compare to today's 17-line flat shape: 13 lines including the redundant `db_table_prefix` (derived), `quicksight_user_arn` (derived).

## `extends:` loader prototype

```python
def load_config(path: Path, *, _seen: set[Path] | None = None) -> Config:
    _seen = _seen or set()
    abs_path = path.resolve()
    if abs_path in _seen:
        raise CycleError(f"extends: cycle detected at {abs_path}")
    _seen.add(abs_path)

    raw = yaml.safe_load(path.read_text())
    extends = raw.pop("extends", None)
    if extends is not None:
        parent_path = (path.parent / extends).resolve()
        parent_raw = load_raw(parent_path, _seen=_seen)
        raw = deep_merge(parent_raw, raw)  # child wins
    return Config.from_dict(raw)
```

**Merge semantics:**
- Dicts: deep-merge per key. Child's value wins on leaf collision.
- Lists: child replaces parent (no append). Operators wanting append-semantics write `principal_arns: ['{{ inherited }}', new_arn]` — explicit > implicit.
- Scalars: child wins.

**Derive-on-load:**
- After `deep_merge`, walk the result dict + apply derivations.
- `aws.deployment_name` set + `db.table_prefix` absent ⇒ derive prefix from deployment_name (`-` → `_`).
- `auth.aws.profile` set + `auth.aws.quicksight_user_arn` absent ⇒ derive via `boto3.list_users` ADMIN-role lookup (cached per-process).
- Missing required field ⇒ raise `MissingField("<field path> — set in this cfg or in an `extends:` parent")`.

## Migration (v13.x → v14.0.0 hard break)

Per `[[feedback_no_compat_shims]]`. Loader raises on legacy shape:

```python
if "demo_database_url" in raw:
    raise LegacyFieldError(
        "demo_database_url is now db.url in v14.0.0. "
        "See RELEASE_NOTES.md v14.0.0 for the migration table."
    )
```

Migration table for v14.0.0 release notes covers every legacy → new field path. The 4-6 `run/config.*.yaml` files in the repo migrate by hand as part of DE.1.

## Datasource lifecycle: explicit `aws.datasource.mode`

Today the runtime makes an implicit decision: presence of `datasource_arn` ⇒ adopt that ARN; absence ⇒ create-or-update from cfg + `demo_database_url`. Two problems with the implicit dispatch:

1. **No way to say "use the connection metadata but skip the datasource API call entirely."** Tests that exercise the QS-deploy path pay AWS API costs (and risk test-env drift) on every run even when they don't actually need the datasource — they just need the cfg shape to validate.
2. **The dispatch is opaque.** Reading the cfg, you can't tell whether `datasource_arn` absence means "create me one" or "the operator forgot to fill it in."

Lock at DE.0 exit:

```yaml
aws:
  datasource:
    mode: create   # | adopt | skip
    arn: arn:aws:...   # required iff mode=adopt; ignored otherwise
    # name + connection metadata stay implicit from cfg.deployment_name +
    # db.url; only the lifecycle differs by mode.
```

- **`create`** (default for prod-deploy postures): generator creates the QS datasource if absent, updates if present. Today's no-arn behavior.
- **`adopt`**: generator reads the explicit `arn` from cfg + uses it as-is. Today's with-arn behavior. Useful when the operator pre-provisioned the datasource (e.g. a shared one across deployments).
- **`skip`**: generator does NOT call the QS datasource API. Tests that only exercise dataset / analysis / dashboard generation (the bulk of the test surface) set this to avoid the API cost. The dataset emission still needs a placeholder ARN to bind to; the runner stamps a fake ARN in `runs/<id>/cfg/qs.yaml` for the skip case.

Migration mapping for v14.0.0:

| Pre-DE | Post-DE |
|---|---|
| no `datasource_arn` (implicit create) | `aws.datasource.mode: create` |
| `datasource_arn: arn:...` (implicit adopt) | `aws.datasource.mode: adopt` + `aws.datasource.arn: arn:...` |
| (no equivalent) | `aws.datasource.mode: skip` (new option) |

The runner's existing `_write_qs_cfg_for_thin` path (which materializes `runs/<id>/cfg/qs.yaml` per `[[project_cb10_qs_to_docker_pg_constraints]]`) is the natural place to stamp `mode: skip` for tests that don't need the datasource API exercise.

## Open question for DE.0 spike exit

- **`extends:` value shape:** string (`extends: ./base.yaml`) or list (`extends: [./base.yaml, ./tier-prod.yaml]`)? List supports composition (base + tier overlays); string is simpler. **Recommend list-only** so the loader always iterates; single-string operators write `[./base.yaml]`. Costs one bracket; gains compositional simplicity. Operator confirm at DE.0 exit.
- **List-merge policy:** child-replaces (current proposal) vs per-field policy? Recommend child-replaces — predictable; operators write explicit additions when they want append. Re-evaluate if a real cfg ends up needing additive lists for `principal_arns`.

## DE.1+ unblock

- This audit + prototype-recipe doc ✓
- Operator confirms `extends:` list-shape + list-replace policy
- DE.1 implements the loader + `Config.from_dict` builder + per-field test pinning the shape. DE.2 sweeps every `cfg.<old_field>` callsite. DE.3 doc sweep. DE.4 coordinates with DC.1 + DD.1 to land their blocks under the locked hierarchy. DE.5 phase exit + v14.0.0 release.
