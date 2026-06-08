# Y.2.gate.h.1.0 — QS user ARN derivation strategy (SPIKE)

**Status: LOCKED 2026-05-08.**
**Recommendation: A (ListUsers + STS UserId match) with cfg override (`cfg.quicksight_user_arn`).**

---

## 1. Problem

`QS_E2E_USER_ARN` is required by every browser e2e test (the embed URL is signed
for that QuickSight user). Today the operator hand-exports the value, captured
in a memory entry. We want the runner to auto-derive it deterministically across
three identity types:

- **Human SSO session** — local dev, `aws sso login` produces an assumed-role
  identity in the operator's account.
- **CI OIDC role** — GitHub Actions assumes `Github_e2e_testing/ci-bot` via
  `sts:AssumeRoleWithWebIdentity`.
- **Long-lived IAM user** — the operator runs as a dedicated IAM user (e.g.,
  `quicksight-test-user`) via static access keys.

## 2. Live data (2026-05-08, account `470656905821`)

`aws quicksight list-users` returned three users:

| QS UserName | QS Arn (suffix) | PrincipalId | Maps to STS identity |
|---|---|---|---|
| `quicksight-test-user` | `user/default/quicksight-test-user` | `federated/iam/AIDAW3FKWOJOWNRRPCKGF` | IAM user (UserId starts `AIDA…`) |
| `Github_e2e_testing/ci-bot` | `user/default/Github_e2e_testing/ci-bot` | `federated/iam/AROAW3FKWOJOSHKR2ESG5:ci-bot` | Assumed-role session (UserId = `<AROA>:<session>`) |
| `470656905821` | `user/default/470656905821` | `federated/iam/470656905821` | Root (UserId = account ID) |

**Key pattern:** every QS user's `PrincipalId` is exactly `federated/iam/<UserId>`
where `<UserId>` is what `sts:GetCallerIdentity` returns. Verified across all
three identity types in this account. This is the canonical join key.

## 3. Candidates compared

| Candidate | Mechanism | API calls | Matching rule | Edge-case risk |
|---|---|---|---|---|
| **A** | `sts:GetCallerIdentity` → `quicksight:ListUsers` (paginate) → filter `PrincipalId == "federated/iam/<UserId>"` | O(N/100) — paginated | UserId-vs-PrincipalId-tail. Identity-type-agnostic. | None observed — STS UserId + QS PrincipalId are both canonical fields. |
| **B** | Cfg field `quicksight_user_arn:` | 0 | Operator hand-types the ARN. | No automation; the failure mode the memory entry exists for. |
| **C** | `quicksight:DescribeUser` with derived username | O(1) | Username derived from STS Arn shape (`:user/<name>`, `:root`, `:assumed-role/<role>/<session>`). | Username-derivation edge cases (cross-account roles, SAML federation, Identity Center). |
| **D** | Lazy `quicksight:RegisterUser` if no match | O(1) on hit, O(2) on miss | Self-heals when QS user missing. | Needs `quicksight:RegisterUser` IAM perm; hides the "auth as wrong principal" bug behind a silent provision. |
| **E** | A + cache to `~/.cache/quicksight-gen/qs-user-arn-<account>-<region>` | 0 on cache hit | Same as A. | Staleness foot-gun: QS user replaced → cache returns deleted ARN. |

## 4. Decision matrix

| Constraint | A | B | C | D | E |
|---|---|---|---|---|---|
| Works for human SSO | ✓ | ✓ (manual) | ✓ | ✓ | ✓ |
| Works for CI OIDC | ✓ | ✓ (manual via env) | ✓ | ✓ | ✓ |
| Works for long-lived IAM | ✓ | ✓ (manual) | ✓ | ✓ | ✓ |
| Reuses `_probe_aws` STS call (c.8) | ✓ | — | ✓ | ✓ | ✓ |
| `EXIT_NEEDS_OPERATOR` on no match | ✓ | n/a | ✓ | hides via auto-provision | ✓ |
| Deterministic across runs | ✓ | ✓ | ✓ | ✓ (after first run) | ⚠ stale cache |
| Zero new IAM perms | ✓ | ✓ | ✓ | ✗ | ✓ |
| No operator-typed string | ✓ | ✗ | ✓ | ✓ | ✓ |
| No identity-type-specific derivation logic | ✓ | n/a | ✗ | ✗ | ✓ |
| O(1) at QS-account scale | ⚠ paginated | ✓ | ✓ | ✓ | ✓ on cache hit |

## 5. Recommendation: A + cfg override

**Primary path:** `_derive_qs_user_arn(cfg)` calls `sts:GetCallerIdentity` →
`quicksight:ListUsers` (paginating with `MaxResults=100`) → returns the user
whose `PrincipalId` equals `f"federated/iam/{caller_user_id}"`.

**Override:** if `cfg.quicksight_user_arn` is set, return it without any API
calls. Operator escape hatch for cases where the authenticated principal differs
from the desired QS embed user (e.g., local dev authed as root but wants the
`quicksight-test-user` ARN for embed signing).

**Failure mode:** no match found AND no cfg override → raise `OperatorError`
with actionable message: *"AWS principal `<Arn>` (UserId `<UserId>`) does not
match any registered QuickSight user in account `<account>` namespace
`<namespace>`. Either authenticate as a registered QS user, or set
`quicksight_user_arn:` in `run/config.<dialect>.yaml`."*

**No cache yet.** Derivation cost in our account: <1s (single ListUsers page,
3 users). Per-runner-invocation cost amortizes across all variants/layers.
Re-evaluate if profiling shows this is a hot path or QS account grows >500
users.

## 6. Why not C?

C (DescribeUser + derived username) is O(1) regardless of QS account size, so
strictly better at scale. But the username-derivation logic is the kind of thing
that quietly breaks on edge cases — cross-account assumed roles, SAML federated
identities, AWS Identity Center users — and the failure mode is "wrong username,
404, fall through to error" rather than "found the wrong user". A's matching
rule is bullet-proof because both UserId and PrincipalId are canonical fields
populated by AWS itself, with the join key (`federated/iam/<UserId>`) verified
across all three identity types in our actual account.

If we hit a QS account where ListUsers pagination matters (>1000 users), revisit
and add C as a fallback after A's first page returns no match.

## 7. Why not E (cache)?

The cache adds complexity (file path, invalidation, version stamp) for ~1s saved
per runner invocation. The runner currently spawns one Python process per
top-level invocation; within that process the derivation is one-shot anyway.
Defer until profiling shows this matters.

## 8. Why not D (lazy RegisterUser)?

Auto-provisioning hides a class of bugs: "I'm authed as the wrong principal" or
"my OIDC role isn't in the QS account yet" should fail loudly, not silently
provision a new QS user whose presence may surprise the operator. Useful for
greenfield CI bootstrap but not for the steady-state path.

## 9. Implementation notes for h.1

- `_derive_qs_user_arn(cfg) -> str` lives in `src/quicksight_gen/_dev/runner.py`
  (same module as `_probe_aws`).
- Reuses existing `boto3` client from `common/aws.py` factory (no direct
  `boto3.client` per `b.15.lint.boto3-direct`).
- Hooks into `setup_variant` for any layer in `_LAYER_DEPS[layer]` that includes
  `qs_arn`. Sets `QS_E2E_USER_ARN` in `env_overrides` before dispatching the
  pytest subprocess.
- Matching uses string equality, not regex — both fields are canonical.
- Pagination loop: `while NextToken: …` (boto3 `get_paginator('list_users')`
  also works).
- Add `quicksight_user_arn` to `Config` (validated as optional `str`); document
  in `docs/reference/configuration.md`.
- Drop the memory entry pointing at the hardcoded ARN once h.1 lands.

## 10. CI side (relationship to gate.k)

CI's GitHub Actions OIDC role assumes `Github_e2e_testing/ci-bot`. STS UserId
in that session = `AROAW3FKWOJOSHKR2ESG5:ci-bot`, which matches QS user #2's
`PrincipalId`. Derivation works without any CI-specific config — the
`QS_E2E_USER_ARN` secret currently set in the GH workflow can be removed once
h.1 lands.

---

**Spike artifact ends. Next step: tick `Y.2.gate.h.1.0` complete; design + build
`Y.2.gate.h.1` per §5 + §9 above.**
