# Leg rail XOR groups

> **What this field controls.** Mutually-exclusive subsets of a
> transfer template's *Variable*-direction leg rails — exactly one
> member of each group fires per template firing.

## What you're looking at

A list of group rows, one per existing XOR group, with a trailing
empty row for adding a new group. Each row is a checkbox set whose
universe is the template's own `leg_rails` field. Tick the rails
that belong in the group; untick every box to drop the group on save.

## Why this exists

Some real-world transfer templates have legs that are required by
the template's net-conservation contract but flexible at the rail
level — the institution-side leg of a wire might be debited from
one of several pools depending on customer routing. The XOR-group
construct lets the L2 author declare that flexibility while keeping
the L1 conservation invariant intact: each template firing picks
exactly one rail per group, and the validator checks the picked
rail is in-group at every firing.

The per-firing pick is deterministic — derived from the firing's
metadata, not random — so the L1 layer can replay a firing and get
the same group-member every time.

## Constraints (validator C1a-d)

- Each group needs at least 2 members. A one-rail XOR is just a
  required rail.
- Every member must already be in the template's `leg_rails`.
- Every member must be a `SingleLegRail` with
  `direction="variable"`. Fixed-direction rails are unconditional;
  the XOR construct doesn't apply.
- No rail may appear in two groups. The form disables already-claimed
  rails in the other group rows to surface this before submit.

## Staged-edit pattern

This field is `edit_only=True` — it doesn't render on the *create
template* form. The reason is chicken-and-egg: the option universe
(`self.leg_rails`) doesn't exist until the template has been saved
once. Author the template + its `leg_rails` first, save, then reopen
the edit form to add the XOR groups.

## Related handbook pages

- [Transfer key](transfer-key.md) — the metadata field whose value
  determines which group member fires per template instance.
- [Chain children](chain-children.md) — how this template participates
  in a chain alternation (which is the *chain-level* XOR, separate
  from the *leg-rail-level* XOR this field declares).

[Vocabulary](../_glossary.md)
