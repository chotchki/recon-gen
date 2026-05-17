# Phase W — One-time E2E setup runbook

Everything `.github/workflows/e2e.yml` depends on. All four pieces
below are user-in-console work — once done, the workflow runs
hands-off.

## 1. GitHub secrets (W.0.b + W.0.d)

```bash
gh secret set QS_GEN_PG_URL \
  --body 'postgresql://postgres:<pg_password>@<aurora-host>:5432/postgres'

gh secret set QS_GEN_ORACLE_URL \
  --body '<oracle_user>/<oracle_password>@<oracle-host>:1521/ORCL'

gh secret set QS_E2E_USER_ARN \
  --body 'arn:aws:quicksight:us-east-1:<account>:user/default/ci-bot'

gh secret set AWS_ROLE_ARN \
  --body 'arn:aws:iam::<account>:role/qs-gen-ci'
```

Verify with `gh secret list`. All four are masked in workflow logs
(any `echo` of the value renders as `***`).

## 2. Aurora + Oracle ingress (W.0.b)

Widen each cluster's security group to allow inbound from
`0.0.0.0/0` on its port:

- Aurora PG cluster — port 5432
- Oracle RDS instance — port 1521

The URL password IS the access control. Risk profile: scan/connect
noise + DDoS surface, not data theft (PG + Oracle both rate-limit
auth attempts, and the credentials are 20+ chars random).

Record the SG IDs you modify here for easy rollback later:

- Aurora SG: `sg-________________`
- Oracle SG: `sg-________________`

## 3. AWS OIDC IdP + IAM role (W.0.c)

### Step 1: Register GitHub as an OIDC provider in your AWS account

One-time, IAM console → Identity providers → Add provider →
OpenID Connect:

- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

(Thumbprints auto-managed since 2023; AWS handles cert rotation.)

### Step 2: Create the `qs-gen-ci` role

IAM console → Roles → Create role → Web identity:

- Identity provider: `token.actions.githubusercontent.com` (the
  one you just registered)
- Audience: `sts.amazonaws.com`
- GitHub organization: `chotchki`
- GitHub repository: `Quicksight-Generator`
- GitHub branch: `main`

Then on the next screen, REPLACE the auto-generated trust policy
with the one below — the auto-generated version uses `StringEquals`
on `sub` which is too strict (it locks to one specific ref form);
the `StringLike` form below allows main-branch pushes,
workflow_dispatch from main, AND release-tag pushes (`refs/tags/v*`)
that the release pipeline's e2e-against-testpypi job needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:chotchki/Quicksight-Generator:ref:refs/heads/main",
            "repo:chotchki/Quicksight-Generator:ref:refs/tags/v*"
          ]
        }
      }
    }
  ]
}
```

Replace `ACCOUNT_ID` with your AWS account number. Do NOT add a
`*` wildcard to the `sub` claim or open the tag form to non-`v*`
prefixes — that would let any branch in the repo assume the role,
defeating the trigger-model lockdown.

### Step 3: Attach the permissions policy

Create a new inline policy on the role with this JSON. Scope is
exactly what `recon-gen json apply --execute` + `json clean
--execute` + `audit apply --execute` + `audit verify` need, plus
the embed-URL generation for browser tests. Anything beyond this
is a leak.

**The two `Pass*` actions are easy to miss.** AWS QuickSight
requires `quicksight:PassDataSource` when you call `CreateDataSet`
that references an existing datasource (the Pass action authorizes
"hand this resource to the new dataset"). Same shape for
`PassDataSet` when `CreateAnalysis` references existing datasets.
Without these the deploy fails at `CreateDataSet` time with a
confusingly-worded `AccessDeniedException` ("not authorized to
perform: quicksight:PassDataSet on resource: …datasource/…" — the
action name in the error is misleading; the missing permission is
typically `PassDataSource` for the resource type shown).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "QuickSightResources",
      "Effect": "Allow",
      "Action": [
        "quicksight:CreateTheme",
        "quicksight:DescribeTheme",
        "quicksight:UpdateTheme",
        "quicksight:DeleteTheme",
        "quicksight:ListThemes",
        "quicksight:CreateDataSource",
        "quicksight:DescribeDataSource",
        "quicksight:UpdateDataSource",
        "quicksight:DeleteDataSource",
        "quicksight:ListDataSources",
        "quicksight:PassDataSource",
        "quicksight:CreateDataSet",
        "quicksight:DescribeDataSet",
        "quicksight:UpdateDataSet",
        "quicksight:DeleteDataSet",
        "quicksight:ListDataSets",
        "quicksight:PassDataSet",
        "quicksight:CreateAnalysis",
        "quicksight:DescribeAnalysis",
        "quicksight:UpdateAnalysis",
        "quicksight:DeleteAnalysis",
        "quicksight:ListAnalyses",
        "quicksight:CreateDashboard",
        "quicksight:DescribeDashboard",
        "quicksight:UpdateDashboard",
        "quicksight:DeleteDashboard",
        "quicksight:ListDashboards",
        "quicksight:CreateFolder",
        "quicksight:DescribeFolder",
        "quicksight:UpdateFolder",
        "quicksight:DeleteFolder",
        "quicksight:ListFolders"
      ],
      "Resource": "*"
    },
    {
      "Sid": "QuickSightTags",
      "Effect": "Allow",
      "Action": [
        "quicksight:TagResource",
        "quicksight:UntagResource",
        "quicksight:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "QuickSightEmbedAndUsers",
      "Effect": "Allow",
      "Action": [
        "quicksight:GenerateEmbedUrlForRegisteredUser",
        "quicksight:DescribeUser"
      ],
      "Resource": "*"
    }
  ]
}
```

The `Resource: "*"` is QS's standard pattern — QuickSight resource
ARNs aren't well-supported as IAM resource constraints. The trust
policy + the per-run `deployment_name: qs-ci-${{ github.run_id }}`
(Z.C — `resource_prefix` was renamed) provide isolation, not the IAM
resource scope.

After both policies attach, copy the role ARN
(`arn:aws:iam::ACCOUNT:role/qs-gen-ci`) into the `AWS_ROLE_ARN`
GitHub secret per Section 1.

## 4. `ci-bot` QuickSight user (W.0.d)

The browser tests render dashboards via QS embed URLs. Each embed
URL is "as" some QuickSight user — it inherits that user's
permissions. Best practice: a dedicated bot user with no
human-attached identity, separate from your real user.

### Register the user

```bash
aws quicksight register-user \
  --aws-account-id <account> \
  --namespace default \
  --identity-type IAM \
  --iam-arn arn:aws:iam::<account>:role/qs-gen-ci \
  --session-name ci-bot \
  --email ci-bot@example.com \
  --user-role READER
```

(IAM user-type means the user identity comes from the assumed
role's session — no separate password.)

The resulting user ARN looks like
`arn:aws:quicksight:us-east-1:<account>:user/default/qs-gen-ci/ci-bot`.
Copy that into the `QS_E2E_USER_ARN` secret per Section 1.

### Grant the bot dashboard read access

The deploy step's `principal_arns` config also needs to include
the bot user, OR you grant per-dashboard read on the
`UpdateDashboardPermissions` API. The simplest path: add the bot
ARN to your `config.yaml`'s `principal_arns` list so every
deployed dashboard has the bot pre-permissioned.

(Alternative: omit `principal_arns` from the CI-generated config
and grant the bot the QS account-wide `Reader` role with
permissions to all dashboards in the namespace. Looser scope, less
to maintain.)

## 5. Verify

Trigger the workflow manually:

```bash
gh workflow run E2E
gh run watch
```

The `auth-smoke` job should print a `GetCallerIdentity` response
showing the assumed-role ARN. If it errors with
`Not authorized to perform sts:AssumeRoleWithWebIdentity`, the
trust policy didn't take — re-check section 3 step 2 (especially
the `sub` claim form).
