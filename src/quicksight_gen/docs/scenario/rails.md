# {{ vocab.institution.name }} — Rails

Each Rail is a single money-movement primitive. **TwoLegRail** posts
one debit + one credit; **SingleLegRail** posts a single leg (must be
reconciled by a Transfer Template or aggregating rail).

Total: **{{ l2.rails|length }}** rails declared on
`{{ l2_instance_name }}.yaml`. The accounts diagram on the
[overview](index.md#topology-accounts-rails) shows every rail as a
labeled edge.

{% for r in l2.rails %}
## {{ r.name }} — `{{ r.rail_name }}`

{{ r.description or "_(no description on the L2 YAML)_" }}

- **Shape:** {% if r.__class__.__name__ == "TwoLegRail" %}Two-leg ({{ r.source_role }} → {{ r.destination_role }}){% else %}Single-leg ({{ r.leg_role }}, direction {{ r.leg_direction }}){% endif %}
{%- if r.posted_requirements %}
- **Posted requirements:** {{ r.posted_requirements|join(", ") }}
{%- endif %}
{%- if r.max_pending_age %}
- **Aging — pending:** legs SHOULD post within `{{ r.max_pending_age }}` (Stuck Pending matview surfaces violations)
{%- endif %}
{%- if r.max_unbundled_age %}
- **Aging — unbundled:** posted legs SHOULD bundle within `{{ r.max_unbundled_age }}` (Stuck Unbundled matview surfaces violations)
{%- endif %}
{%- if r.aggregating %}
- **Aggregating:** YES — bundles `{{ r.bundles_activity|join(", ") }}`
{%- endif %}
{%- if r.metadata_keys %}
- **Metadata keys:** {{ r.metadata_keys|join(", ") }}
{%- endif %}

{% endfor %}
