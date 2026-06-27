# DW.0 — QuickSight-removal decision spike

**Status:** ALL FIVE decisions locked + operator-confirmed 2026-06-27. Gates DW.1+ — this is settled ground for the QS-removal phase, not a re-litigation.

Phase DW excises QuickSight. Before deleting a line, five technical questions had to be answered so the deletion is surgical, not a flail. This is the record. Each section is a verdict plus the *why*, plus what it means for the DW task list — so the implementation phase inherits settled ground, not a re-litigation.

The through-line: QuickSight removal isn't just "delete the boto3 code." It forces a posture decision — does an independent validation tool keep ANY cloud footprint? — and it surfaced a three-way conflation in how "auth" was being discussed that had to be untangled before anything got cut.

---

## DW.0.1 — Authentication: three "OIDC" things, only one of them is app auth

**Verdict: recon-gen's own App2 user-login auth STAYS as-is through DW. Passkeys + a fully-local authenticated mode become a future "Auth" phase, explicitly out of scope here.**

The word "OIDC" was doing triple duty in the QS-removal discussion, and the three things it named have NOTHING to do with each other beyond the acronym:

1. **App2 user-login OIDC** — `common/html/auth.py`: authlib + a `JwtCookieMiddleware` JWT-cookie session, Dex as the test IdP. This is recon-gen's OWN renderer auth — who's allowed to view a self-hosted dashboard. It is pure authlib/pyjwt, imports zero boto3, and the QuickSight-account nuke never touched it. This is what "integrating with an IdP is standard" means, and it already ships.
2. **`cfg.auth.aws` QS-embed STS signing** — `config.py::AuthAwsConfig` + `resolve_qs_user_arn`. This derives the IAM principal that signs QuickSight embed URLs (mint-time STS, called only in the test layer). It is QS-coupled and it DIES with QuickSight.
3. **The CI GitHub→AWS OIDC role** — the GitHub secrets the operator nuked. Pure CI-infra trust so Actions could reach AWS for the QS deploy and the `_aw` RDS variants. This is infrastructure auth, it is DW.0.5 below, and it is not app auth at all.

The operator's instinct — "we still need authentication support for recon-gen, integrating with an IdP is standard" — is already satisfied by (1), and (1) survived the nuke completely. The thing that got nuked was (3), which is unrelated to whether a deployed dashboard can authenticate a viewer.

**So DW does this, surgically:**

- KEEP `cfg.auth.{oidc,session}`, `common/html/auth.py`, the `JwtCookieMiddleware`, and every auth unit + e2e test (`test_oauth_login_flow.py`, the route-gate contract). Add a DW guard asserting they stay green after the QS code comes out.
- DELETE only `cfg.auth.aws` (`AuthAwsConfig` + `resolve_qs_user_arn`) in DW.8/DW.13 — it's the embed-signing helper, nothing else reads it once the QS deploy is gone.

**The gap, deferred to a future phase.** Today App2 is OIDC-against-an-external-IdP OR a no-auth dev mode — there is no LOCAL authenticated path (no local users/password, zero WebAuthn code, grep-confirmed). "Fully-local auth would be wise" + "I LOVE passkeys" names exactly that gap: a self-hosted deploy shouldn't be FORCED to stand up an external IdP just to require a login. The fix — a `cfg.auth.webauthn` (and/or `cfg.auth.local`) block driving a WebAuthn/FIDO2 registration + assertion flow that mints the SAME `recon_gen_session` JWT the OIDC callback already mints, so the session layer is shared and the IdP becomes optional — is real work and gets its own phase. Captured in the PLAN backlog ("Future Auth phase"). NOT a QS-removal blocker; the operator confirmed "okay if IdP/auth stays as-is and we address in a future phase."

---

## DW.0.5 — AWS footprint after QuickSight: fully-local

**Verdict (operator): FULLY-LOCAL. Drop the `_aw` RDS targets, tear out the GitHub→AWS OIDC + creds + the RDS stop/start lifecycle. AWS footprint → ZERO. The home-network port-forward stays closed.**

This was the consequential fork, and it's what's keeping CI red right now: the nuked OIDC role authed CI for BOTH the QS deploy AND the `_aw` variants (which test against real managed PostgreSQL/Oracle on RDS), so killing the role reds every AWS-touching layer, not just the QS leg.

Three paths were on the table — keep `_aw` standing in CI, go fully-local, or a hybrid that kept `_aw` as an operator-run opt-in. The operator chose fully-local, and reframed the "lost coverage" objection in a way that's sharper than the framing the question was asked in:

> Implementing AWS / Azure / Google Cloud is 100% on the end user. It's still easily possible, just not our problem.

That's the right call and the right reason. recon-gen reaches its database through a connection string. Whether that string points at a local Docker Postgres, AWS RDS, Azure Database, or GCP Cloud SQL is the operator's infra decision. The portability contract this project owns is "portable PG 17 / Oracle 19c SQL" — no JSONB, no `->>`, SQL/JSON path syntax — and that contract is verifiable against local engines. Babysitting a specific managed service's quirks (the RDS Oracle SE2 DRCP dead-end is the canonical example we already hit) is the operator's job against their own infra, not a standing coverage burden an offline-first validation tool carries. Keeping a CI→AWS trust path alive to test someone else's managed-DB quirks would directly contradict the thesis QuickSight removal exists to cement.

**So DW.11 widens** from "strip the QS leg" to "strip the entire AWS footprint": the QS deploy, the `_aw` matrix variants, the RDS stop/start lifecycle, the `up local|aws` / `down` / `status` infra scaffolding, the cost-sweep job, and the CI OIDC perm all go together. Nothing AWS remains to boot, stop, or sweep. The two-cfg `qs.yaml` contortion and the inbound `hotchkiss.io` port-forward into the home network — both existed only to let QuickSight's us-east-1 region reach the dev-box Postgres — retire with it. One less hole in the network was an explicit operator win for this phase.

---

## DW.0.2 — Non-drift backstop: is the 3-way gate enough?

**Proposed verdict: yes, 3-way is enough. The PDF-all-invariants extension is defense-in-depth, not a DW blocker → backlog.**

The 4-way agreement gate (`scenario_plants ⊆ direct-DB matview == QuickSight == App2`, plus PDF for drift) degrades to 3-way when QS leaves: `plants ⊆ direct-DB matview == App2`. The question is whether dropping QS as a corroborator weakens the gate.

It doesn't, because QuickSight was never the truth anchor — the direct-DB matview recompute was. That leg queries the invariant matviews straight from the database, on a code path entirely independent of App2's projection. Set-equality between the direct-DB recompute and App2's rendered set is a genuine cross-check of two independent computations, not a circular one. QS was a third witness that happened to agree; losing the third witness when the first two are independent doesn't make the gate circular.

Extending the PDF recompute from drift-only to ALL L1 invariants WOULD add a second fully-independent recomputer (a real defense-in-depth upgrade, since the PDF path recomputes rather than re-reads). It's worth doing — but it's an enhancement, not a precondition for deleting QS. Tracked as backlog.

---

## DW.0.3 — External consumers of `out/*.json`?

**Verdict: no consumers — and only the QuickSight-API JSON emit retires. The SQL emit for schema/data and the audit PDF stay.**

`out/*.json` is the AWS QuickSight API dict shape — it's only meaningful as input to a QuickSight deploy. With QuickSight gone the artifact has no consumer, and this is the operator's own tool, not a published integration surface with downstream pinners (the DV soak was the migration window). The `json apply` emit goes away cleanly with the `json` group. No compatibility shim — the codebase is pre-stable and the standing posture is "drop the escape hatch + migrate callsites in the same phase," not accumulate cruft.

One sharpening from the operator: this retires the `json` group's QuickSight-API-dict emit ONLY. recon-gen's OTHER emit surface — `schema apply` / `data apply` writing portable SQL to `out/` (the destructive-default-to-emit behaviour), and `audit apply` writing the PDF — was never QuickSight-coupled and stays untouched. "SQL is still emittable for schema/data/etc." DW.7 must scope its deletion to the `json` CLI group, NOT the emit-by-default mechanism that schema/data/audit share. A consumer who took `recon-gen schema apply` SQL off disk to feed their own pipeline keeps working; only the QuickSight-shaped JSON disappears.

---

## DW.0.4 — PyPI hygiene: yank or just stop?

**Verdict: don't yank — but `quicksight-gen` is NEVER published again. Permanent, and structurally guarded.**

The operator tightened this past "stop uploading" to a hard, permanent lock: *we must never publish it again.* Two halves.

Don't yank — same reasoning as never rewriting a version tag: yanking breaks anyone currently pinned and rewrites published history. The already-published `quicksight-gen` releases stay frozen in place as part of the v15 escape hatch (DW.15 — `recon-gen==15.x` with the `[quicksight]` extra is the primary pin; the legacy `quicksight-gen` releases remain reachable for anyone who pinned the old name).

Never again — DV.7 already deleted the shim-publish steps from `release.yml` and retired the `quicksight-gen-shim/` directory, so the definition that COULD be republished is already gone. DW locks it shut: DW.14's grep-zero sweep extends to assert `quicksight-gen` appears in zero workflow + pyproject publish surface, so a future edit can't silently resurrect the shim. The name is dead — frozen where it sits, never re-uploaded.

---

## Net effect on the DW task list

- **DW.0.1** → DW.8/DW.13 delete ONLY `cfg.auth.aws`; keep all other auth. New backlog item for the passkeys/local-auth phase.
- **DW.0.5** → DW.11 is unconditional and wider: QS + `_aw` + RDS lifecycle + CI OIDC + cost-sweep job + port-forward all removed; AWS footprint → 0.
- **DW.0.2** → keep the planned 3-way gate; PDF-all-invariants → backlog.
- **DW.0.3** → DW.7 deletes ONLY the `json` group; the schema/data SQL emit + the audit PDF emit stay.
- **DW.0.4** → no yank; DW.14's grep-zero lock extends to forbid `quicksight-gen` anywhere in the publish surface (never-again).
