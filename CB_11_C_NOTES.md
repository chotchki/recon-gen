# CB.11.c — collapsed CI surface (notes for swap-in review)

## What's in this drop

- `.github/workflows/ci.yml.new` — single self-hosted `test-everything` job that runs `./run_tests.sh up_to=qs_browser`, plus `docs-portable-install` (unchanged, ubuntu-latest) and `coverage-badge` (merged W.8b coverage job + the old standalone badge job — one source of coverage data now, no reason for the two-step indirection).
- `.github/workflows/release.yml.new` — trimmed to 5 jobs: `build → smoke → publish-testpypi → publish-pypi → github-release`. Drops `tests`, `release-coverage`, `verify-testpypi-install`, `verify-pypi-install`, `e2e-against-testpypi`. Adds a `workflow_run` trigger so the workflow surfaces "CI on main passed" as a release-readiness signal; the actual publish path is still tag-driven via the `if:` clause on `build`.
- `e2e.yml` — to delete (command at the bottom).

## Secrets the new `ci.yml` needs in the GH repo

All carried over from the existing `e2e.yml` / `ci.yml` shape — no new secrets:

| Secret | Used by | Notes |
|---|---|---|
| `AWS_ROLE_ARN` | `aws-actions/configure-aws-credentials@v4` | OIDC trust policy on `qs-gen-ci` IAM role (see `.github/E2E_SETUP.md`). Trust policy must include the self-hosted runner's repo+branch claim. |
| `RECON_E2E_USER_ARN` | qs_browser layer embed URL minting + cfg auth block | The single QS user post-AD (recon-gen-admin). Gets stale on QS subscription recreate — see `project_qs_e2e_user_arn` memory. |

Secrets no longer used by ci.yml (still needed elsewhere if `nightly-cost-sweep.yml` references them):
- `QS_GEN_PG_URL` / `QS_GEN_ORACLE_URL` — was the external Aurora / Oracle URL; the runner now spins up Docker containers per-cell, so the cfg's `demo_database_url` points at `127.0.0.1:<port>`.
- `QS_GEN_AWS_PG_CLUSTER_ID` / `QS_GEN_AWS_ORACLE_INSTANCE_ID` — was for `start-db-cluster` / `stop-db-cluster`. RDS Aurora is fully decommissioned per CB.12; these secrets can be deleted from the GH repo unless something else references them.

## AWS IAM permissions the self-hosted runner needs

The `qs-gen-ci` role assumed via OIDC needs (carried over from e2e.yml):

- `quicksight:*` on resources tagged `Deployment=qs-ci-${run_id}-*` (deploy + describe + delete for `json apply` / `json clean`)
- `quicksight:GenerateEmbedUrlForRegisteredUser` for the qs_browser layer's embed URL minting
- `quicksight:ListUsers` + `sts:GetCallerIdentity` for the runner's `auth:` block auto-derivation (combined spike `y_2_gate_h_i_combined_spike.md`)
- `s3:*` on the data-source S3 bucket (if any data sources go through S3; not used today but keeping the door open)

The OIDC trust policy's `token.actions.githubusercontent.com:sub` condition needs to allow `repo:<org>/quicksight:ref:refs/heads/main` + PR refs + tag refs. Existing policy already does this.

## Coverage flow change

Pre-CB.11.c (two paths):
1. `ci.yml::test` ran bare `pytest --cov=recon_gen` with `COVERAGE_FILE=.coverage.py3.13` → uploaded as `coverage-data-py3.13`.
2. `ci.yml::integration-pg` ran the runner with `--coverage` → wrote `.coverage.<variant>.<layer>` files under `runs/<id>/` → flatten step copied to repo root → uploaded as `coverage-data-pg-runner`.
3. Aggregator job (`coverage`) downloaded `coverage-data-*` (glob), ran `coverage combine`, generated XML + HTML + markdown.
4. Standalone `coverage-badge` job downloaded XML + MD artifacts, generated badge SVG, pushed to `badges` branch.

Post-CB.11.c (one path):
1. `test-everything` runs `./run_tests.sh up_to=qs_browser --coverage` — runner emits `.coverage.<variant>.<layer>` files for every pytest layer (unit / db / app2 / qs_api / qs_browser) under `runs/<id>/<variant>/`.
2. Collect step flattens to repo root, uploaded as `coverage-data-runner`.
3. Merged `coverage-badge` job downloads, `coverage combine`s, generates everything, conditionally pushes badge (only on `push:main`).

Net: bare `pytest --cov` is gone; the runner's per-layer coverage IS the coverage source. The `.coverage.*` glob shape is unchanged so `coverage combine` works without modification.

## Gotchas + decisions worth sanity-checking

1. **AWS cleanup-pg / cleanup-oracle** — these were e2e.yml's belt-and-suspenders jobs that swept `Deployment`-tagged QS resources + stopped the RDS cluster after both PG e2e jobs finished. The new ci.yml has an inline `Sweep deployed QS resources (always)` step at the end of `test-everything` that does the QS-side sweep via `json clean -c run/config.{postgres,oracle}.yaml`. **The RDS stop-cluster work is GONE** — per `project_phases_ab_bm_tech_debt_cleanup` and CB.12, RDS Aurora is decommissioned, so there's no cluster to stop. If anything in `nightly-cost-sweep.yml` still references RDS, it can be culled too. Operator should sanity-check the nightly-cost-sweep workflow.

2. **`workflow_run` trigger on release.yml** — I added this per spec ("triggers via `workflow_run` after ci.yml passes on main + on tag push") but the actual publish path is still tag-driven; `build`'s `if:` clause inerts the workflow_run path. If you'd rather drop `workflow_run` entirely (since tag pushes are explicit operator action), strip the `workflow_run:` block — nothing else references it.

3. **Coverage badge gate** — the merged `coverage-badge` job conditionally pushes the badge only when `github.ref == 'refs/heads/main' && github.event_name == 'push'`. PRs still render the report + Step Summary + artifacts, but don't push the badge. Matches the pre-CB.11.c behavior.

4. **cfg materialization on the self-hosted runner** — `run/` is gitignored, so the new ci.yml writes `run/config.postgres.yaml` + `run/config.oracle.yaml` from secrets at the top of the job. This is the same pattern e2e.yml used (`/tmp/ci-pg.yaml` etc.) just at the canonical discovery path. The runner's `_resolve_runner_cfg_path` will find them via the default candidate list.

5. **The `e2e-duckdb` job is also gone** (the SQLite/DuckDB cell of the old ci.yml matrix). The runner's `up_to=qs_browser` full-matrix invocation already includes `du_lo` cells; they auto-skip AWS-touching layers per `_apply_aw_target_chain_restrictions`. No coverage loss.

6. **`docs-portable-install`** stays on ubuntu-latest (per spec) — pure wheel-extras gate, no need for the self-hosted runner. `needs: test-everything` so it gates on the runner job, same as before.

7. **The `Release commit` skip pattern** is preserved on `test-everything` (current ci.yml:31). Downstream jobs `needs:` it, so skipping there skips the chain. Release tags don't fire ci.yml's main path.

## Delete e2e.yml

```bash
git rm .github/workflows/e2e.yml
```

After swap-in:
```bash
mv .github/workflows/ci.yml.new .github/workflows/ci.yml
mv .github/workflows/release.yml.new .github/workflows/release.yml
git rm .github/workflows/e2e.yml
```
