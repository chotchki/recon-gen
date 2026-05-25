# For the compliance analyst

*Audience — AML / fraud / SAR analyst at
**{{ vocab.institution.name }}**.*

## What you do today

A model alert lands in your queue. Or a counterparty referral.
Or a SAR you're drafting needs evidence. The shape of every case
is the same: you have a question about a person, a pair, or a
transfer, and you need to pull the rows that answer it — fast
enough to keep the case from going stale, and with enough trail
to defend your conclusion to the regulator later.

Today, that means a query language (or a request to someone who
speaks one), a spreadsheet, and a stack of CSV exports you stitch
together by hand. Each follow-up question — "OK but where did
*that* counterparty get the money?" / "what does this account's
broader network look like?" — is another query, another export,
another stitch. By the time you've assembled the picture, the
case has aged a week.

## What this tool does differently

The **Investigation Dashboard** is question-shaped — not data-
shaped. Each of the four sheets answers a specific class of
question that maps to the investigative posture you start a case
in:

| Question shape | Sheet |
|---|---|
| "Who's getting money from too many senders?" | Recipient Fanout |
| "Which sender → recipient pair just spiked?" | Volume Anomalies |
| "Where did this transfer actually originate?" | Money Trail |
| "What does this account's money network look like?" | Account Network |

Pose the question; pick the sheet; the rows that answer it are
already there, with drill-into-detail on every row. No SQL, no
exports, no stitch.

**The first time you finish a SAR-evidence pull in 10 minutes
that would have taken a half-day of queries — that's the proof
this tool puts the investigative loop in your hands.**

## What we are *not* asking you to learn

- **Not SQL.** The four matviews behind the four sheets do the
  heavy lifting. You filter, drill, and export — you don't query.
- **Not the L1 / L2 / Executives dashboards.** Those are
  operator / integrator / leadership surfaces. They share the
  same base ledger as Investigation, so the rows are mutually
  consistent, but you don't need to know how they work.
- **Not new statistical concepts.** Volume Anomalies surfaces
  z-scores and standard-deviation-bucket bands, but the bucket
  numbers (1 = normal, 5 = extreme outlier) are the actionable
  signal — the underlying math is in the matview, not your job
  to compute.

## How to start

1. Read the
   [Investigation handbook](../handbook/investigation.md). It
   covers the four sheets, the team posture each sheet supports,
   and the demo scenarios you can practice on.
2. Walk the four question-shape walkthroughs in order — they're
   designed as a graduated curriculum:
    - [Who's getting money from too many senders?](../walkthroughs/investigation/who-is-getting-money-from-too-many-senders.md)
    - [Which sender → recipient pair just spiked?](../walkthroughs/investigation/which-pair-just-spiked.md)
    - [Where did this transfer actually originate?](../walkthroughs/investigation/where-did-this-transfer-originate.md)
    - [What does this account's money network look like?](../walkthroughs/investigation/what-does-this-accounts-money-network-look-like.md)
3. Practice on the demo scenarios. The
   [Cast of Characters](../scenario/index.md) page lays out the
   demo's converging-anchor scenario (one recipient hub, several
   sender-side cluster shapes); each walkthrough above resolves
   one face of that scenario.
4. Bookmark the
   [L1 Reconciliation Dashboard](../handbook/l1.md) as your
   back-stop. When a transfer's row looks suspicious in
   Investigation, the L1 surface tells you whether the underlying
   posting violated any invariant — additional evidence for
   regulator-facing case files.

## The investigator's posture

Each sheet supports a different posture:

- **Recipient Fanout** is *find-the-hub* — start with a
  population of recipients, rank by distinct sender count, drag
  the threshold slider until "too many" stops being noise. The
  fanout-cluster shape (many small inbounds → one account) is a
  classic structuring footprint.
- **Volume Anomalies** is *find-the-spike* — the rolling 2-day
  SUM per (sender, recipient) pair plus z-score buckets surfaces
  pairs whose recent activity is meaningfully out-of-line with
  their own baseline. The 5-band bucket is sortable; bands 4-5
  warrant a look.
- **Money Trail** is *trace-the-provenance* — `WITH RECURSIVE`
  walk over `transfer_parent_id` flattens to one row per
  multi-leg edge with chain root + depth. The most useful drill
  shape for SAR-evidence narrative.
- **Account Network** is *visualize-the-shape* — anchor an
  account, see its inbound + outbound Sankeys, left-click any
  node to walk-the-flow (the URL parameter overwrites the anchor
  to the counterparty side). The chart you'd hand a regulator to
  show "here's what this account's relationships actually look
  like."

## The audit report — your regulator handoff

When you need to hand evidence off to an external auditor or
regulator, the **audit reconciliation report** is the artifact
you ship. It's a regulator-ready PDF generated directly from the
same per-instance L1 invariant matviews the L1 Reconciliation
Dashboard reads — exception tables, per-account-day Daily
Statement walks, sign-off block, cryptographic provenance
fingerprint binding the report to its source data.

Use it when:

- An auditor needs a printable artifact outside your QuickSight
  account.
- A SAR file needs an attached reconciliation snapshot for the
  reporting period.
- A regulator asks "show me what your books looked like on
  these dates and prove the numbers".

Generate one for the past 7 days:

```bash
recon-gen audit apply -c config.yaml \
    --l2 path/to/instance.yaml \
    --execute -o report.pdf
```

Override the period for a custom window:

```bash
recon-gen audit apply -c config.yaml \
    --l2 path/to/instance.yaml \
    --period 2026-04-01..2026-04-30 \
    --execute -o april-report.pdf
```

The `--period` flag accepts several shapes: `trailing:N` for "last N
days ending yesterday" (default `trailing:7`), `yesterday`, `today`,
`YYYY-MM-DD..YYYY-MM-DD` for an explicit closed-closed range, or a
single `YYYY-MM-DD` for a one-day report.

Verify a report's provenance against current source data:

```bash
recon-gen audit verify report.pdf -c config.yaml \
    --l2 path/to/instance.yaml
```

The PDF auto-signs cryptographically when `config.yaml` carries
a `signing:` block. The reviewer attestation page also embeds two
empty signature widgets — your PDF reader can fill those in to
add your countersignature.

!!! note "For integrators — automate the daily report"
    The audit report is intended to be **automatically generated
    daily** by the same scheduled job that runs the matview
    refresh after each ETL load. Wire `recon-gen audit apply
    --execute` immediately after `recon-gen data refresh
    --execute` in your cron / Airflow / Prefect pipeline, write
    the PDF to a dated path, and the compliance team always has
    yesterday's reconciliation snapshot ready to hand off without
    a manual generate step.

For the full reference — what each section contains, the
provenance recompute recipe, certificate creation instructions,
how reviewer countersignatures work — see the
[Audit Reconciliation Report handbook](../handbook/audit.md).

## The concepts you'll want grounded

- [Open vs. closed loop](../concepts/accounting/open-vs-closed-loop.md) —
  the system-boundary question shapes which transfers leave the
  institution's visibility entirely (and which become harder to
  trace beyond {{ vocab.institution.acronym }}'s books).
- [Vouchering](../concepts/accounting/vouchering.md) — voucher → ACH
  materialization is a layering vector worth understanding when
  the Money Trail walks across one.
- [Eventual consistency](../concepts/accounting/eventual-consistency.md) —
  why recent money trails may look incomplete; not all in-flight
  transfers have landed yet, and the picture stabilises after a
  settlement window.

## What "good" looks like

After a few weeks of casework with Investigation:

- You're producing SAR evidence in single sittings, not multi-day
  research arcs.
- You're catching converging-anchor patterns (the fanout +
  spike + chain that point at the same account) without manually
  joining three reports.
- Your case files cite specific `transfer_id` chains pulled from
  Money Trail, not paraphrased CSV exports.
- When the regulator asks for the network shape behind a SAR,
  you screenshot the Account Network Sankey and attach.

That's the acceptance bar. The tool works when
{{ vocab.institution.acronym }}'s compliance team trusts the
dashboard with the investigative loop end-to-end.
