# Y.2.gate.h + Y.2.gate.i.0 — Combined credential-discovery + AWS-auth spike

**Status: LOCKED 2026-05-08.** Supersedes the standalone `y_2_gate_h_1_0_qs_user_arn_spike.md` (which is preserved for the join-key research; this doc is the canonical decision for both gates).

**Recommendation summary:**
- **Auth path (i.0):** Long-lived IAM-user access keys (candidate C from `i.0`'s candidate list), referenced from `~/.aws/credentials` via a named profile, with the profile name carried in `cfg.auth.aws_profile`. New IAM user `quicksight-gen-local` mirrors the existing CI role's policy + `quicksight:ListUsers` (the one extra action h.1 needs).
- **QS user ARN derivation (h.1):** Approach A (`sts:GetCallerIdentity` → `quicksight:ListUsers` filter on `PrincipalId`) for local; explicit cfg override for CI (current GH secret stays).
- **Config shape:** new `auth:` block in `run/config.<dialect>.yaml`. No new files.

---

## 1. The problem (both gates, one shape)

A multi-hour Claude-loop session against AWS-touching layers needs to authenticate without operator intervention. Two intertwined sub-problems:

- **i.0** — How does the AWS session exist in the first place? SSO's ~12h cache miss triggers a browser flow Claude can't auto-invoke (`b.14.4` refusal pattern), forcing the user to type `! aws sso login` mid-loop and lose continuity.
- **h.1** — Given an authenticated session, find the right `QS_E2E_USER_ARN` for embed-URL signing without operator hand-export.

Treating these separately produces a half-fix: even if `QS_E2E_USER_ARN` auto-derives, the SSO cache miss still kills the loop. The user surfaced this gap during the h.1.0 spike review — both go together.

## 2. Live data (2026-05-08)

**Account:** `470656905821`. **CI role:** `Github_e2e_testing` (assumed via OIDC). **CI role's inline policy** (`github-e2e-policy`):

- **QuickSightResources block** — full CRUD on Theme / DataSource / DataSet / Analysis / Dashboard / Folder + `Pass*` + `List*`. Resource: `*`.
- **QuickSightTags block** — `TagResource` / `UntagResource` / `ListTagsForResource`. Resource: `*`.
- **QuickSightEmbedAndUsers block** — `GenerateEmbedUrlForRegisteredUser` + `DescribeUser`. **No `ListUsers`.**

**QS users** (already captured in h.1.0 spike): three users. PrincipalId == `federated/iam/<UserId>` is the canonical join key — verified for IAM user, assumed-role, and root.

## 3. Auth path candidates (i.0)

| | Mechanism | Browser flow? | Cache TTL | Multi-hour loops? | Local secret? | New IAM perms? |
|---|---|---|---|---|---|---|
| **A** | AWS SSO + cached tokens (status quo) | At first login + every cache miss | ~12h | **Pain — forces `! aws sso login`** | No | No |
| **B** | aws-vault wrapping SSO | Same as A on miss | Operator-tunable | Pain reduced; not eliminated | Vault store (Keychain) | No |
| **C** | Long-lived IAM access keys | **Never** | Permanent (rotate manually) | **Works** | Yes (~/.aws/credentials, gitignored / Keychain) | Yes — needs new IAM user |
| **D** | OIDC (CI only) | n/a | Per-job | n/a (CI workflow already uses this) | No | Already wired |

**Lock: C for local, D for CI.** C is the only path that fully eliminates the failure mode. The "long-lived secret" trade-off is accepted because:

1. The IAM user's policy is QS-scoped (no IAM mutation, no broad RDS, no STS-AssumeRole) — blast radius matches the CI role's already-accepted blast radius.
2. The keys live in `~/.aws/credentials` (gitignored if under `run/`; `~/.aws/credentials` is outside the repo entirely), not in cfg yaml. Standard AWS pattern.
3. Rotation is the operator's responsibility on a manual cadence — there's no compliance gate on this account today, so rotation is the operator's discretion.
4. Local-only — CI keeps its OIDC role (no static secrets in GH).

## 4. QS user ARN candidates (h.1) — refined

The h.1.0 spike locked **A + cfg override**. Combined with the C auth path, that becomes:

- **Local** (authed as `quicksight-gen-local` IAM user): derivation works because the new IAM user's PrincipalId is registered in QS (operator runs `RegisterUser` once during onboarding) AND has `quicksight:ListUsers`. Override unused.
- **CI** (authed as `Github_e2e_testing` role): role lacks `ListUsers`. Override path: cfg generated per-CI-job sets `auth.quicksight_user_arn` from the existing GH secret. Same memory pattern as today; no derivation cost in CI.

This means the IAM policy on the new local user must add **one** action beyond the CI role's policy: `quicksight:ListUsers`.

## 5. Config shape (cfg-only, no new files)

New `auth:` block in `run/config.<dialect>.yaml`. Same nesting style as the existing `signing:` block. `run/` is gitignored and current cfg already carries DB passwords inline, so the secrecy posture is identical.

```yaml
# Local AWS auth + QS embed signing identity.
# Both fields optional; runner falls back to ambient AWS env / SSO cache when omitted.
auth:
  # Name of a profile in ~/.aws/credentials. When set, the runner injects
  # AWS_PROFILE=<value> into every subprocess it spawns. The profile entry
  # itself carries the access_key_id / secret_access_key — kept out of this
  # file by design (operator runs `aws configure --profile <name>` once during
  # onboarding). Inheriting the standard AWS credential resolution chain
  # means the runner stays out of secrets storage entirely.
  aws_profile: "quicksight-gen-local"

  # Optional override for QS user ARN. When set, runner uses it directly
  # without calling quicksight:ListUsers. Use case: authenticated as one
  # principal but want embed URLs signed for a different QS user (e.g.,
  # local-root-against-test-user; CI's per-job cfg generated with the
  # GH secret value baked in).
  quicksight_user_arn: null
```

**Why not store keys inline in cfg yaml?** Even in a gitignored file, accidental copy-paste leaks the keys. `~/.aws/credentials` is the standard AWS pattern, IDE-aware (VS Code masks profile contents), and survives cfg-yaml regeneration. The cfg-yaml carries only the *profile name*, which is non-secret.

**Why not store keys in macOS Keychain?** Adds a layer of indirection (`aws-vault` or similar wrapper). Out of scope for the first pass. Operator can opt into it later by changing how their `~/.aws/credentials` profile resolves (e.g., `credential_process = aws-vault exec quicksight-gen-local --json --no-session`); the runner doesn't need to know about it.

## 6. New IAM user setup (one-time onboarding)

Operator action (Claude can flag the actions but not run them without explicit per-step confirmation per the IAM-mutation guardrail):

```bash
# 1. Create the IAM user
aws iam create-user --user-name quicksight-gen-local

# 2. Attach the policy — same as Github_e2e_testing's inline policy + ListUsers.
#    Source: docs/audits/y_2_gate_h_i_combined_spike.md §7 below.
aws iam put-user-policy \
  --user-name quicksight-gen-local \
  --policy-name quicksight-gen-local-policy \
  --policy-document file://docs/audits/_iam/quicksight-gen-local-policy.json

# 3. Generate access keys (returns AccessKeyId + SecretAccessKey ONCE)
aws iam create-access-key --user-name quicksight-gen-local

# 4. Wire into ~/.aws/credentials
aws configure --profile quicksight-gen-local
# (paste the keys when prompted; region = us-east-1)

# 5. Register the new IAM user as a QuickSight user (so it appears in
#    quicksight:ListUsers and PrincipalId match works)
aws quicksight register-user \
  --aws-account-id 470656905821 \
  --namespace default \
  --identity-type IAM \
  --iam-arn arn:aws:iam::470656905821:user/quicksight-gen-local \
  --user-role ADMIN \
  --email <operator-email>

# 6. Grant the new QS user permissions on existing dashboards (via
#    principal_arns in cfg or QS UI Folder permissions)

# 7. Set cfg.auth.aws_profile = "quicksight-gen-local" in
#    run/config.{postgres,oracle}.yaml

# 8. Verify
QS_GEN_CONFIG=run/config.postgres.yaml ./run_tests.sh up_to=browser --variants=default
```

## 7. IAM policy for `quicksight-gen-local`

Mirror of `Github_e2e_testing/github-e2e-policy` + one new action (`quicksight:ListUsers`). Lands at `docs/audits/_iam/quicksight-gen-local-policy.json` for operator's `aws iam put-user-policy` call.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "QuickSightResources",
      "Effect": "Allow",
      "Action": [
        "quicksight:CreateTheme", "quicksight:DescribeTheme",
        "quicksight:UpdateTheme", "quicksight:DeleteTheme",
        "quicksight:ListThemes",
        "quicksight:CreateDataSource", "quicksight:DescribeDataSource",
        "quicksight:UpdateDataSource", "quicksight:DeleteDataSource",
        "quicksight:ListDataSources", "quicksight:PassDataSource",
        "quicksight:CreateDataSet", "quicksight:DescribeDataSet",
        "quicksight:UpdateDataSet", "quicksight:DeleteDataSet",
        "quicksight:ListDataSets", "quicksight:PassDataSet",
        "quicksight:CreateAnalysis", "quicksight:DescribeAnalysis",
        "quicksight:UpdateAnalysis", "quicksight:DeleteAnalysis",
        "quicksight:ListAnalyses",
        "quicksight:CreateDashboard", "quicksight:DescribeDashboard",
        "quicksight:UpdateDashboard", "quicksight:DeleteDashboard",
        "quicksight:ListDashboards",
        "quicksight:CreateFolder", "quicksight:DescribeFolder",
        "quicksight:UpdateFolder", "quicksight:DeleteFolder",
        "quicksight:ListFolders"
      ],
      "Resource": "*"
    },
    {
      "Sid": "QuickSightTags",
      "Effect": "Allow",
      "Action": [
        "quicksight:TagResource", "quicksight:UntagResource",
        "quicksight:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "QuickSightEmbedAndUsers",
      "Effect": "Allow",
      "Action": [
        "quicksight:GenerateEmbedUrlForRegisteredUser",
        "quicksight:DescribeUser",
        "quicksight:ListUsers"
      ],
      "Resource": "*"
    }
  ]
}
```

**Diff vs CI role policy:** added `quicksight:ListUsers` to the EmbedAndUsers block. Everything else identical. Total IAM blast radius on `quicksight-gen-local` ≡ CI role + ListUsers.

## 8. Runner implementation outline (h.1 + i.0 build)

`src/quicksight_gen/_dev/runner.py` adds:

```python
def _resolve_aws_profile(cfg: Config) -> str | None:
    """Returns the profile name to inject as AWS_PROFILE, or None for ambient."""
    return cfg.auth.aws_profile if cfg.auth else None


def _derive_qs_user_arn(cfg: Config, region: str) -> str:
    """Returns the QS user ARN.

    Override path (cfg.auth.quicksight_user_arn): wins, no API call.
    Derivation path: STS GetCallerIdentity → QS ListUsers → match
    PrincipalId == "federated/iam/<UserId>".
    """
    if cfg.auth and cfg.auth.quicksight_user_arn:
        return cfg.auth.quicksight_user_arn
    sts = boto_factory.client("sts")
    qs = boto_factory.client("quicksight", region_name=region)
    user_id = sts.get_caller_identity()["UserId"]
    target_principal = f"federated/iam/{user_id}"
    paginator = qs.get_paginator("list_users")
    for page in paginator.paginate(
        AwsAccountId=cfg.aws_account_id, Namespace="default",
    ):
        for u in page["UserList"]:
            if u["PrincipalId"] == target_principal:
                return u["Arn"]
    raise OperatorError(
        f"AWS principal UserId {user_id!r} does not match any QuickSight user "
        f"in account {cfg.aws_account_id} namespace 'default'. Either "
        f"authenticate as a registered QS user, or set "
        f"`auth.quicksight_user_arn:` in cfg yaml."
    )
```

Wired into `setup_variant`:

```python
def setup_variant(variant: str, cfg: Config) -> dict[str, str]:
    env_overrides = {...existing...}
    profile = _resolve_aws_profile(cfg)
    if profile:
        env_overrides["AWS_PROFILE"] = profile
    if "qs_arn" in _LAYER_DEPS.get(layer_being_dispatched, frozenset()):
        env_overrides["QS_E2E_USER_ARN"] = _derive_qs_user_arn(cfg, cfg.aws_region)
    return env_overrides
```

**`Config` changes** (`common/config.py`):

```python
@dataclass(frozen=True)
class AuthConfig:
    aws_profile: str | None = None
    quicksight_user_arn: str | None = None

@dataclass(frozen=True)
class Config:
    ...existing fields...
    auth: AuthConfig | None = None
```

Loader supports the nested key. Allowlist updated. Pyright-strict scope picks it up automatically.

## 9. Constraints met (the ones from h.1.0 spike + i.0 spike)

| Constraint | Met by |
|---|---|
| Multi-hour Claude-loop sessions don't hit auth interactions | C: long-lived IAM keys never expire |
| No long-lived secrets in repo | Keys in `~/.aws/credentials`, never in cfg yaml |
| `aws sts get-caller-identity` works for c.8 probe | Standard AWS path; profile injection respected |
| Runner detects expired-or-missing creds + exits with `EXIT_NEEDS_OPERATOR` | h.5 + c.8 catch this; long-lived keys never expire so this is the rare path |
| Operator can override via cfg | `auth.quicksight_user_arn` exposes the escape hatch |
| Works for human SSO + CI OIDC + long-lived IAM | Local: C. CI: D (current OIDC). All three identity types map to QS users via §3 join key. |
| Reuses `_probe_aws` STS call | Same `boto_factory.client("sts")` |
| Clear `EXIT_NEEDS_OPERATOR` on no QS-user match | `OperatorError` with actionable message |
| CI-friendly | CI keeps current OIDC + GH-secret-based ARN override; no policy changes needed for ci-bot |
| Deterministic across runs | Both paths return same ARN for same caller |

## 10. Out of scope (deferred)

- **macOS Keychain integration / `aws-vault`** — operator can opt in via `credential_process` in `~/.aws/credentials` without runner changes.
- **Key rotation automation** — operator's manual cadence; no compliance gate on this account.
- **Per-layer auth scoping** — same auth used for all AWS-touching layers (deploy/api/browser); no need to differentiate.
- **CI policy widening** — leave `Github_e2e_testing` as-is; CI uses cfg override path. If CI ever needs derivation, add `quicksight:ListUsers` to the role then.
- **`Y.2.gate.l` RDS start/stop perms** — needs `rds:Start*` / `rds:Stop*`; not in scope for h+i; tracked separately under `l`.

## 11. Order of operations for h+i build

1. Add `AuthConfig` dataclass + cfg loader updates (no operator action yet).
2. Add `_derive_qs_user_arn` + `_resolve_aws_profile` to runner; wire into `setup_variant`. Tests use mocked boto3.
3. Pause for operator confirmation on IAM user creation (§6 steps 1–3 + 5).
4. Operator runs `aws configure --profile quicksight-gen-local` (§6 step 4) + sets `auth.aws_profile` in cfg (§6 step 7).
5. Live verify: `./run_tests.sh up_to=browser` works without env-var exports (§6 step 8).
6. Drop the memory entry that hardcodes `QS_E2E_USER_ARN`.
7. Tick `h.1`, `h.2` (cfg-driven DB strings — already done), `h.3` (cfg-driven AWS account/region — already done), `h.4` (tunable defaults), `h.5` (loud failure), `i.0` (this spike), `i.1`–`i.4` (built atop this).

---

**Spike artifact ends. Next step: tick `h.1.0` and `i.0` complete; build per §8 + §11.**
