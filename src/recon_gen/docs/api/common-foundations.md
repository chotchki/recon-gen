# Common foundations

Persona-blind helpers + base AWS dataclasses that the tree builds on
top of. Most tree-API authors never touch these directly — the tree's
typed wrappers cover the construction surface — but they're documented
here for reference and for extension authors.

## Models

The dataclass mapping to the AWS QuickSight API JSON shapes
(`to_aws_json()` produces the exact dict shape `create-analysis` /
`create-dashboard` / `create-data-set` / `create-theme` /
`create-data-source` accept).

::: recon_gen.common.models

## Typed IDs

NewType wrappers for the URL-facing and analyst-facing identifiers
that stay explicit even after Phase L's auto-ID work for internal
IDs.

::: recon_gen.common.ids

## Dataset contracts

`DatasetContract` ties a SQL query's projection to a typed list of
expected columns; `build_dataset()` is the shared constructor used
by every per-app `datasets.py`.

::: recon_gen.common.dataset_contract

## Cross-app deep links

URL builder for the `CustomActionURLOperation` — used when a drill
needs to jump to another app's deployed dashboard with parameter
values pre-set in the URL. (Note: per the L.6.7 / K.4.7 finding, the
QuickSight URL parameter sync defect means controls don't update —
data filters but the on-screen widget label stays "All".)

::: recon_gen.common.drill

## Institution identity

BXa.1 (2026-05-30) replaced the prior `DemoPersona` block with
three top-level `L2Instance` fields: `institution_name`,
`institution_acronym`, and `description`. Handbook templates
substitute `vocab.institution.name` / `vocab.institution.acronym`
across ~20 pages; absent values fall back to a regex-extracted
proper-noun run from `description` (or the `"Your Institution"`
placeholder when no description either). See
`common/handbook/vocabulary.py` for the full dispatch.

For curated Investigation-walkthrough narrative (anchor account,
shell-DDA layering chain, anomaly-pair sender), declare an
`investigation_personas:` block on the L2 YAML with typed
`InvestigationPersona(name, account_id, role)` entries.
