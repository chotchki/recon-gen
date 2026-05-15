# {{ vocab.institution.name }} — Institution Tour

*Generated from the L2 institution YAML (`{{ l2_instance_name }}.yaml`).
Re-run the docs build (or point `QS_DOCS_L2_INSTANCE` at a different
YAML) to regenerate this section against another institution.*

{{ l2.description or "_(no institution description provided in the L2 YAML)_" }}

---

## At a glance

| What | Count |
|---|---|
| Singleton accounts ({{ vocab.institution.acronym }}'s GL + external counterparties) | **{{ l2.accounts|length }}** |
| Account templates (per-customer / per-merchant shapes) | **{{ l2.account_templates|length }}** |
| Rails (money-movement primitives) | **{{ l2.rails|length }}** |
| Transfer Templates (multi-rail bundles) | **{{ l2.transfer_templates|length }}** |
| Chains (parent → child firing rules) | **{{ l2.chains|length }}** |
| Limit Schedules (per-account / per-rail caps) | **{{ l2.limit_schedules|length }}** |

The diagrams below show how these pieces connect. Per-entity descriptions
live on the dedicated subpages — that prose IS the source of truth for
how {{ vocab.institution.acronym }} treats each entity.

---

## Topology — accounts + rails

Every Rail draws an edge between its source-role account and its
destination-role account. Single-leg rails draw a self-loop on the leg-
role account. Internal {{ vocab.institution.acronym }} accounts are
blue; external counterparties are orange.

{{ diagram("l2_topology", kind="accounts") }}

---

## Topology — account templates + rails

Same shape as the accounts view above, but the nodes are
``AccountTemplate`` roles rather than singleton Accounts. Each template
is one ``role × N`` node (the dashed border marks "many instances at
runtime"); rail edges connect templates whose roles the rail's
``source_role`` / ``destination_role`` / ``leg_role`` references.
Singleton-only rails (no template touched) drop out — this is the
template-shape skeleton, not the full topology.

{{ diagram("l2_topology", kind="account_templates") }}

---

## Topology — chains (parent → child firings)

Chains declare that when one Rail or Transfer Template fires, another
SHOULD fire too. Solid edges are required (validator catches a missing
firing); dashed edges are optional. XOR groups capture "any one of
these MUST fire — pick the right child by metadata".

{{ diagram("l2_topology", kind="chains") }}

---

## Topology — account hierarchy (rollup)

How the singleton accounts and templates roll up. Each edge points
from a child to its parent — the singleton ``Account`` whose ``role``
matches the child's ``parent_role``. Solid-bordered nodes are 1-of-1
singletons; dashed-bordered ``× N`` nodes are templates that
materialize many instances at runtime (e.g. one ``CustomerDDA`` per
customer, all rolling up to the ``DDAControl`` GL).

{{ diagram("l2_topology", kind="hierarchy") }}

---

## Per-entity reference

Pick a primitive to walk its full inventory + descriptions:

- [Accounts](accounts.md) — singletons + templates with descriptions.
- [Rails](rails.md) — every money-movement primitive with shape, aging
  caps, posted requirements, metadata keys.
- [Transfer templates](transfer-templates.md) — multi-rail bundles.
- [Chains](chains.md) — required (singleton-children) + XOR (multi-children) firings.
- [Limit schedules](limit-schedules.md) — daily flow caps.

---

## How the dashboards read this

- **L1 Reconciliation Dashboard** — surfaces the L1 invariant
  violations against the data this L2 declares: drift, overdraft,
  limit breach (using the Limit Schedules above), stuck pending /
  unbundled (using the Rails' aging caps), supersession audit.
- **L2 Flow Tracing** — walks the Rails / Chains / Transfer Templates
  diagrams above against runtime activity, surfacing
  declared-but-never-fired rails, chain orphans, and unmatched
  transfer types.
- **Investigation** — questions over the leaf-account / external-
  counterparty graph above (recipient fanout, volume anomalies, money
  trail, account network).
- **Executives** — coverage / volume / money-moved scorecard rolled up
  across {{ vocab.institution.acronym }}'s account roster.

For a per-app sheet-by-sheet walkthrough, see the
[Walkthroughs](../walkthroughs/index.md) section.
