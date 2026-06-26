# How do I brand my handbook prose?

*Customization walkthrough — Developer / Product Owner. Reskinning + extending.*

## The story

You've pointed the rendered mkdocs site at your own L2 instance
(see [How do I publish docs against my L2?](how-do-i-publish-docs-against-my-l2.md))
and the handbook now reads against your accounts, your rails, your
chains. The prose still says "Your Institution" everywhere, though —
where the bundled `sasquatch_pr` fixture would say "Sasquatch National
Bank — SNB". The neutral fallback renders fine, it just reads colorless.

The fix is three top-level fields on the L2 YAML — `institution_name`,
`institution_acronym` and `description`. The handbook templates
substitute them at render time via Jinja `vocab.institution.name` /
`vocab.institution.acronym` references across ~20 pages. No code
changes, no docs-site fork.

For curated Investigation-walkthrough narrative (anchor account names,
shell-DDA layering chain, anomaly-pair sender display names), declare
an `investigation_personas:` block carrying typed
`{name, account_id, role}` entries. The roles the walkthrough templates
gate on: `convergence_anchor`, `counterparty_bank`, `operations_account`
and `shell_entity`.

## Migrating from the old `persona:` block

The old L2 carried a `persona:` block — `institution` (name+acronym
tuple) plus `stakeholders`, `merchants`, `gl_accounts` and `flavor`
fields — and a hardcoded production-code intercept
(`_sasquatch_pr_vocabulary`) that bypassed the operator-supplied
`stakeholders` + `merchants` values for the bundled fixture. The
intercept then populated `HandbookVocabulary` fields that NO docs page
ever substituted (`{{ vocab.stakeholders }}` etc. existed as variables,
no template touched them).

We dropped that doubly-dead surface and promoted the fields that
actually get substituted to top-level:

| Old `persona:` block (removed) | Now |
|---|---|
| `persona.institution[0]` | `institution_name` (top-level) |
| `persona.institution[1]` | `institution_acronym` (top-level) |
| `persona.gl_accounts` | gone — never substituted in any docs page |
| `persona.stakeholders` | gone — Sasquatch vocab hardcoded its own, then those weren't substituted either |
| `persona.merchants` | gone — same as stakeholders |
| `persona.flavor` | gone — `flavor[1]`/`[2]` populated optional handbook `region` + `legacy_entity` which were never substituted |
| (hardcoded `investigation_personas` table) | `investigation_personas:` top-level field on L2 |

Net: a smaller editor surface, no L3 leaking into L2 (`common/handbook/`
no longer carries Sasquatch-specific strings) and the same
operator-visible rendered output for institutions that fill in their
`institution_name`.

## Want to substitute new per-institution strings in your custom prose?

The drop-in-via-YAML path the old `persona:` block offered is gone — a
new substitution variable now takes a small PR. Four steps:

1. Add a top-level field to `L2Instance` (`primitives.py`).
2. Wire the loader / serializer.
3. Extend `HandbookVocabulary` (`vocabulary.py`) to surface it.
4. Reference `{{ vocab.your_field }}` in your fork of the markdown
   templates.

The PR gate's reasoning lives in the archived audit
(`docs/audits/_archive/bx_persona_audit.md`):
substitution variables that no template actually used were silently
misleading operators into filling out forms whose values went nowhere.

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

That's the full flavor surface. Handbook pages now read "Acme Federal
Bank" / "AFB" at every substitution callsite; Investigation
walkthroughs render the curated shell-chain narrative when
`vocab.demo.investigation.layering_chain` is non-empty.

## Acceptance test

The docs landing page (`index.md`) renders its intro against your L2:

> …rendered against **{{ vocab.institution.name }}** (`{{ l2_instance_name }}`)…

Build against your L2 (`recon-gen docs apply --l2 path/to/my.yaml`). The
rendered intro reads "Acme Federal Bank" instead of "Your Institution".
The acronym shows up on role-specific landing pages —
`for-your-role/operator.md` opens with "*Audience — reconciliation
operator at Acme Federal Bank*".

If `vocab.institution.name` still reads "Your Institution",
double-check that you set `institution_name:` at the TOP level of the
YAML — not nested under any block (the old `persona:` nesting is gone).
