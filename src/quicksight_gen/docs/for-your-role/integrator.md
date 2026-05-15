# For the integrator

*Audience — the engineer who wrote the L2 institution YAML for
**{{ vocab.institution.name }}** and needs to prove it matches
runtime reality.*

## What you do today

You're the bridge between the institution's chart of accounts +
operational rails and the L2 model that every dashboard reads off.
You wrote the YAML once; now the question every audit, every
quarterly review, every "we added a new rail / metadata key /
limit schedule" change asks is: **does the YAML still describe
what's actually happening?**

Without a tool, that's a manual reconciliation: spread the YAML
on one screen, the actual posting volumes on another, and check
each declared Rail / Chain / TransferTemplate / LimitSchedule
against runtime activity. Slow, error-prone, and something you
fall behind on as soon as the model grows past a dozen entries.

## What this tool does differently

The **L2 Flow Tracing** dashboard is the YAML, projected onto the
runtime data. Every Rail you declared shows up with its firing
count, status mix, and last-fire timestamp. Every Chain shows the
parent / child completion rate. Every TransferTemplate has a
Sankey of its multi-leg flow. Every LimitSchedule shows how close
to its cap it ran in the date window.

Where the projection finds nothing — a declared Rail with zero
firings, a Chain with no children, a metadata key never set on
any leg — those surface on the **L2 Hygiene Exceptions** sheet
as concrete row-shaped findings, not free-text questions.

**The first time the dashboard shows you a Dead Rail you'd never
have spotted by reading the YAML alone — that's the proof L2 Flow
Tracing closes the declaration / runtime gap your manual
process can't.**

## What we are *not* asking you to learn

- **Not new model semantics.** The L2 SPEC doesn't change because
  of the dashboard. You wrote the YAML against the SPEC; the
  dashboard reads the same SPEC.
- **Not new tooling for everyday model edits.** You still edit
  the YAML in your editor, validate with `quicksight-gen` CLI,
  commit + redeploy. The dashboard is read-only.
- **Not every other dashboard.** L1 (operator surface),
  Investigation (compliance), and Executives (scorecard) are not
  your problem unless your L2 changes break them.

## How to start

If you're new to the L2 model, ground the primitives first; the
handbook + dashboards assume you know what a Rail / Chain /
Transfer Template / Limit Schedule *is*. Five short reads:

- [Account](../concepts/l2/account.md) and
  [Account template](../concepts/l2/account-template.md) — the
  who.
- [Rail](../concepts/l2/rail.md) — the how (per-leg posting
  contract per transfer type).
- [Transfer template](../concepts/l2/transfer-template.md) — the
  multi-leg shape (debit / credit / variable closure).
- [Chain](../concepts/l2/chain.md) — the parent / child firing
  relationship across transfers.
- [Limit schedule](../concepts/l2/limit-schedule.md) — declared
  caps per (parent_role, rail_name).

Then:

1. **Authoring a fresh L2:** start from
   [`spec_example.yaml`](../reference/fixtures/spec_example.yaml)
   (the persona-neutral skeleton — same file the test suite uses)
   plus the
   [Customization handbook](../handbook/customization.md) for the
   editing patterns. The
   [Demo Institution Tour](../scenario/index.md) shows the
   bundled `{{ l2_instance_name }}` fixture projected into a real
   institution shape; copy from it for your own model.
2. Read the
   [L2 Flow Tracing handbook](../handbook/l2_flow_tracing.md). It
   walks the 5 sheets and spells out how each one projects YAML
   declarations against runtime data.
3. Open the dashboard against your current L2 instance + freshest
   data. Tour the **Rails** sheet first; spot any rail with zero
   firings — those are your dead declarations or your dead
   runtime entries, depending on which side you trust.
4. Walk **Chains** + **Transfer Templates**. Required parents
   without children; templates whose legs don't sum to
   `expected_net`. Each is a class of integration bug.
5. End on **L2 Hygiene Exceptions** — the UNION view across 6
   hygiene checks. Treat any non-zero count here as a backlog
   item.
6. Bookmark the
   [Customization handbook](../handbook/customization.md). It's
   the reference for editing the L2 + redeploying; you'll come
   back to it every time the model changes.

## When the L2 changes

The integrator workflow loop:

1. Edit `<your-l2>.yaml`.
2. Regenerate the dashboard JSON: `quicksight-gen json apply -c run/config.yaml -o run/out --l2 <yaml>`. The loader runs the validator as it reads the YAML — any cross-entity errors surface here with a logical path before any JSON gets written.
3. Deploy to AWS: `quicksight-gen json apply -c run/config.yaml -o run/out --l2 <yaml> --execute`.
4. Re-open L2 Flow Tracing — the new declarations should show up
   with their firing counts (which may be zero on day one if
   nothing has fired yet).

The
[publishing-workflow walkthrough](../walkthroughs/customization/how-do-i-publish-docs-against-my-l2.md)
covers the docs-side of the same loop — re-rendering the handbook
against your edited L2. To get your institution's name +
stakeholders + GL labels into the handbook prose, add an optional
`persona:` block to your L2 (see
[How do I brand my handbook prose?](../walkthroughs/customization/how-do-i-brand-my-handbook-prose.md)).

## The concepts you'll want grounded

- [Open vs. closed loop](../concepts/accounting/open-vs-closed-loop.md) —
  the boundary distinction shapes which rails reconcile against
  external counterparties (and why those rails behave differently
  at L2 Flow Tracing).
- [Vouchering](../concepts/accounting/vouchering.md) — the
  TransferTemplate-with-grouping pattern, which is the most
  common multi-leg shape in the L2 model.
- [Eventual consistency](../concepts/accounting/eventual-consistency.md) —
  why declared rails take time to fire; "no firings yet" on a
  rail you only declared yesterday is normal, not a hygiene
  exception.

## What "good" looks like

After a few weeks of using L2 Flow Tracing:

- You're catching dead declarations within days of model changes,
  not at quarterly audit time.
- L2 Hygiene Exceptions stays at zero or near-zero — open
  findings have owners and ETAs.
- When the operator team asks "is the rail for X working?" you
  answer from the dashboard in 10 seconds.
- New L2 entries (rails, chains, transfer templates) come with
  matching activity confirmations within the first few firings.

That's the acceptance bar. The tool works when {{ vocab.institution.acronym }}'s
L2 model and runtime data stay in lockstep without manual
reconciliation cycles.
