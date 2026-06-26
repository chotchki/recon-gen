# Max unbundled age

> **What this field controls.** The aging threshold at which a leg
> firing on this rail surfaces as "stuck unbundled" — old enough
> that the expected aggregating-rail sweep should already have
> claimed it.

## What you're looking at

A text input taking an ISO 8601 duration literal (`PT24H`, `PT4H`,
`P1D`, `P3D`, etc.). Empty means "no aging watch" — the L1 Unbundled
Aging matview skips this rail.

## How L1 uses this

The `<prefix>_stuck_unbundled` matview joins this rail's firings
against the matching aggregating rail's firings via the
`bundles_activity` membership. A firing is "unbundled" when no
aggregating firing in the cadence window has claimed it; "stuck"
when the current time minus the unbundled firing's `posting`
exceeds `max_unbundled_age`.

Stuck rows surface on the L1 Unbundled Aging sheet and roll up
into the L1 Exceptions sheet's `stuck_unbundled` check-type branch.

## Picking a duration

The threshold should be longer than the bundling rail's cadence —
otherwise every firing surfaces as "stuck" the moment the cadence
window closes but the sweep hasn't run yet. A daily sweep typically
gets `PT36H` or `P2D` so a delayed sweep doesn't immediately
trip the alarm. An intraday sweep typically gets `PT4H` or `PT8H`.

For rails that aren't bundled into anything (the rail doesn't
appear in any aggregating rail's `bundles_activity`), leave this
empty — the matview has no aggregating sibling to look for, so
the aging concept is meaningless.

## ISO 8601 reference

- `PT24H` — 24 hours
- `PT4H` — 4 hours
- `P1D` — 1 day (calendar-aligned)
- `P3D` — 3 days
- `PT90M` — 90 minutes

The `T` separator is required when the duration contains a
sub-day unit. `P24H` (no T) is rejected.

## Related handbook pages

- [Bundles activity](bundles-activity.md) — the field on the
  aggregating rail that names which rails it sweeps. The two
  fields must agree for the Unbundled Aging matview to produce
  meaningful rows.

[Vocabulary](../_glossary.md)
