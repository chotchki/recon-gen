# How do I brand my handbook prose?

*Customization walkthrough — Developer / Product Owner. Reskinning + extending.*

## The story

You've pointed the rendered mkdocs site at your own L2 instance
(see [How do I publish docs against my L2?](how-do-i-publish-docs-against-my-l2.md))
and the handbook now reads against your accounts, your rails,
your chains. But the prose still says "Your Institution" where
the bundled `sasquatch_pr` fixture would say "Sasquatch National
Bank — SNB". The neutral fallback works, but the result is
colorless.

The fix is three top-level fields on the L2 YAML:
`institution_name`, `institution_acronym`, and `description`. The
handbook templates substitute them at render time via Jinja
`vocab.institution.name` / `vocab.institution.acronym` references
across ~20 pages — no code changes, no docs site fork.

For curated Investigation-walkthrough narrative (anchor account
names, shell-DDA layering chain, anomaly-pair sender display
names), declare an `investigation_personas:` block carrying
typed `{name, account_id, role}` entries. The roles the
walkthrough templates gate on: `convergence_anchor`,
`counterparty_bank`, `operations_account`, `shell_entity`.

## What changed in BXa.1

Pre-BXa.1, the L2 carried a `persona:` block with `institution`
(name+acronym tuple), `stakeholders`, `merchants`, `gl_accounts`,
and `flavor` fields — plus a hardcoded production-code intercept
(`_sasquatch_pr_vocabulary`) that bypassed the operator-supplied
`stakeholders` + `merchants` values for the bundled fixture. The
intercept then populated `HandbookVocabulary` fields that were
**never substituted in any docs page** (`{{ vocab.stakeholders }}`
etc. existed as variables but no template used them).

BXa.1 nuked the doubly-dead surface and promoted the load-bearing
fields to top-level:

| Pre-BXa.1 (deleted) | Post-BXa.1 |
|---|---|
| `persona.institution[0]` | `institution_name` (top-level) |
| `persona.institution[1]` | `institution_acronym` (top-level) |
| `persona.gl_accounts` | gone — never substituted in any docs page |
| `persona.stakeholders` | gone — Sasquatch vocab hardcoded its own, then those weren't substituted either |
| `persona.merchants` | gone — same as stakeholders |
| `persona.flavor` | gone — `flavor[1]`/`[2]` populated optional handbook `region` + `legacy_entity` which were never substituted |
| (hardcoded `investigation_personas` table) | `investigation_personas:` top-level field on L2 |

Net: smaller editor surface, no L3-leaking-into-L2 (`common/handbook/`
no longer carries Sasquatch-specific strings), same operator-visible
rendered output for institutions that fill in their `institution_name`.

## Want to substitute new per-institution strings in your custom prose?

Add a top-level field to `L2Instance` (`primitives.py`), wire the
loader / serializer, extend `HandbookVocabulary` (`vocabulary.py`)
to surface it, then `{{ vocab.your_field }}` in your fork of the
markdown templates. The "drop-in-via-YAML" pre-BXa.1 path is gone;
new substitution variables require a small PR. The audit
`docs/audits/bx_persona_audit.md` explains why — substitution
variables that no template actually used were silently misleading
operators into filling out forms whose values went nowhere.

## Worked example — minimal flavored L2

```yaml
# my_institution.yaml

institution_name: "Acme Federal Bank"
institution_acronym: "AFB"
description: |
  Acme Federal Bank's combined treasury + commercial-loan
  reconciliation view. Generated nightly from the core ETL feed.

investigation_personas:
  - name: "Suspect Shell LLC"
    account_id: "cust-700-0001-suspect-shell-llc"
    role: "convergence_anchor"
  - name: "Layered Vehicle A"
    account_id: "cust-700-0002-layered-a"
    role: "shell_entity"

# ... accounts / rails / templates / chains / limit_schedules below ...
```

That's the full flavor surface. Handbook pages now read "Acme
Federal Bank" / "AFB" in every substitution callsite; Investigation
walkthroughs render the curated shell-chain narrative when
`vocab.demo.investigation.layering_chain` is non-empty.

## Acceptance test

`docs/handbook/index.md` opens with:

> Welcome to **{{ vocab.institution.name }}** (`{{ l2_instance_name }}`)…

Build the docs against your L2 (`QS_DOCS_L2_INSTANCE=path/to/my.yaml
recon-gen docs ...`). The rendered index reads **"Acme Federal
Bank"** instead of **"Your Institution"**. The bank's acronym shows
up in role-specific landing pages (`for-your-role/operator.md`
opens with "*Audience — reconciliation operator at Acme Federal
Bank*").

If `vocab.institution.name` still reads "Your Institution",
double-check that you set `institution_name:` at the top level of
the YAML (not nested under any block — BXa.1 dropped the
nesting).
