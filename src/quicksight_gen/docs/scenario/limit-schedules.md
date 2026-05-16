# {{ vocab.institution.name }} — Limit schedules

Each Limit Schedule sets a daily outbound-flow cap for a
``(parent_role, rail_name)`` pair. The L1 ``limit_breach`` matview
lists every account/day where outbound activity exceeded the cap.

Total: **{{ l2.limit_schedules|length }}** limit schedules declared
on `{{ l2_instance_name }}.yaml`.

{% if l2.limit_schedules %}
| Parent role | Rail | Cap | Description |
|---|---|---|---|
{% for ls in l2.limit_schedules -%}
| {{ ls.parent_role }} | {{ ls.rail }} | `{{ ls.cap }}` | {{ (ls.description or "—")|replace("\n", " ") }} |
{% endfor %}
{% else %}
*This L2 instance declares no limit schedules — outbound flow on every
``(parent_role, rail)`` pair is uncapped.*
{% endif %}
