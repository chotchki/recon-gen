# Walkthroughs

Step-by-step task recipes — "I have X and want Y", or "I'm looking at
this row, what do I do?" Each page is self-contained and assumes the
reader already knows the relevant concept (see
[Concepts](../concepts/index.md)) and where things live (see
[Reference](../reference/index.md)).

Landed here cold? The [role pages](../for-your-role/index.md) curate
which walkthroughs each role needs, and in what order — start there.

See it live: https://recon-gen-spec.hotchkiss.io/

## L1 sheets

The day-in-the-life flows for an operator working the L1 dashboard.

- [Getting Started](l1/getting-started.md)
- [Drift](l1/drift.md) · [Drift Timelines](l1/drift-timelines.md)
- [Overdraft](l1/overdraft.md) · [Limit Breach](l1/limit-breach.md)
- [Pending Aging](l1/pending-aging.md) · [Unbundled Aging](l1/unbundled-aging.md)
- [Supersession Audit](l1/supersession-audit.md)
- [L1 Exceptions](l1/exceptions.md)
- [Daily Statement](l1/daily-statement.md) · [Transactions](l1/transactions.md)

## Investigation

The four question-shaped flows for compliance / AML triage.

- [Who's Getting Money from Too Many Senders?](investigation/who-is-getting-money-from-too-many-senders.md)
- [Which Sender → Recipient Pair Just Spiked?](investigation/which-pair-just-spiked.md)
- [Where Did This Transfer Actually Originate?](investigation/where-did-this-transfer-originate.md)
- [What Does This Account's Money Network Look Like?](investigation/what-does-this-accounts-money-network-look-like.md)

## ETL

Recipes for the engineer wiring the two base tables.

- [How do I populate transactions?](etl/how-do-i-populate-transactions.md)
- [How do I populate daily_balances?](etl/how-do-i-populate-daily-balances.md)
- [How do I validate a single account-day?](etl/how-do-i-validate-a-single-account-day.md)
- [How do I prove my ETL is working?](etl/how-do-i-prove-my-etl-is-working.md)
- [How do I tag a force-posted transfer?](etl/how-do-i-tag-a-force-posted-transfer.md)
- [How do I add a metadata key?](etl/how-do-i-add-a-metadata-key.md)
- [What to do when demo passes but prod fails?](etl/what-do-i-do-when-demo-passes-but-prod-fails.md)

## Customization

Recipes for the developer dropping the dashboards onto their own
stack and shaping the L2 to their own institution.

- [How do I map my database to the two base tables?](customization/how-do-i-map-my-database.md)
- [How do I swap the SQL behind a dataset?](customization/how-do-i-swap-dataset-sql.md)
- [How do I reskin the dashboards for my brand?](customization/how-do-i-reskin-the-dashboards.md)
- [How do I add an app-specific metadata key?](customization/how-do-i-add-a-metadata-key.md)
- [How do I extend canonical values?](customization/how-do-i-extend-canonical-values.md)
- [How do I add an AML inbound-flow cap?](customization/how-do-i-add-an-aml-inbound-cap.md)
- [How do I chain two templates?](customization/how-do-i-chain-two-templates.md)
- [How do I add multi-mode settlement?](customization/how-do-i-add-multi-mode-settlement.md)
- [How do I model batched payouts?](customization/how-do-i-model-batched-payouts.md)
- [How do I mix cardinality children?](customization/how-do-i-mix-cardinality-children.md)
- [How do I set typical amount ranges on a rail?](customization/how-do-i-set-typical-amount-ranges.md)
- [How do I set typical firing counts on a rail?](customization/how-do-i-set-typical-firing-counts.md)
- [How do I author a new app on the tree?](customization/how-do-i-author-a-new-app-on-the-tree.md)
- [How do I test my customization?](customization/how-do-i-test-my-customization.md)
- [How do I publish docs against my L2?](customization/how-do-i-publish-docs-against-my-l2.md)
