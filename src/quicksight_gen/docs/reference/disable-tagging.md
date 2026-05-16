# Disabling AWS resource tagging

!!! danger "This setting weakens cleanup safety. Read this page in full before opting in."

The deploy pipeline tags every QuickSight resource it creates with
``ManagedBy=quicksight-gen`` plus a ``Deployment={{ l2_instance_name }}``
tag (Z.C — value comes from ``cfg.deployment_name``). ``json clean``
uses those tags to fail-CLOSED-scope deletion to resources we
deployed — anything untagged or wrongly tagged stays safe.

The ``tagging_enabled: false`` config knob disables that path. Set
it ONLY when the deploy IAM principal cannot be granted
``quicksight:TagResource`` / ``quicksight:UntagResource`` (e.g. an
enterprise environment where governance tags are applied by another
system and your deploy role is locked to "no Tag* actions").

## Default behavior — `tagging_enabled: true`

Every ``Create*`` call carries a ``Tags=[…]`` kwarg with two
machine-readable tags plus anything in ``extra_tags``:

| Tag | Value | Purpose |
| --- | --- | --- |
| ``ManagedBy`` | ``quicksight-gen`` | Marks the resource as ours; ``json clean`` ignores anything missing this tag. |
| ``Deployment`` | the cfg's ``deployment_name`` (e.g. ``qsgen-myorg-prod``) | Per-deploy isolation. Cleanup only sweeps resources whose tag matches this deployment. |

Cleanup is fail-CLOSED: a resource without the right ``ManagedBy``
+ ``Deployment`` tag combination is **never** swept, even when its
ID happens to start with our deployment_name. Concurrent CI runs
and local deploys with the same ID prefix coexist safely because
each deploy stamps its own ``Deployment`` tag value.

## Override behavior — `tagging_enabled: false`

```yaml
# config.yaml
tagging_enabled: false             # ⚠ weakens cleanup isolation; see warning below
deployment_name: "qsgen-myorg-prod"   # MUST be unique to your deploy scope
db_table_prefix: "qsgen_myorg_prod"   # required cfg field; not used by cleanup
```

What changes:

1. **``Create*`` calls omit the ``Tags`` kwarg entirely.** The IAM
   principal does not need ``quicksight:TagResource`` /
   ``quicksight:UntagResource``.
2. **``json clean`` matches by ID prefix instead.** A resource
   counts as ours if its ``DashboardId`` / ``AnalysisId`` /
   ``DataSetId`` / ``ThemeId`` / ``DataSourceId`` starts with
   ``<deployment_name>-`` (note the trailing hyphen).
3. **``deployment_name`` is already required (Z.C, no default), so
   the cleaner always has a scope value** — no "missing prefix"
   pre-check is needed beyond the existing cfg-load loud-fail.

## Why this is unwise

The fail-CLOSED tag check is the only protection against
**ID-collision sweeps**. With tagging disabled:

- A QuickSight dashboard a colleague hand-built and named
  ``qsgen-myorg-prod-revenue`` would be eligible for deletion the
  next time you ran ``json clean`` — its ID matches the prefix,
  and the cleaner has no other way to tell it apart from a stale
  generator output.
- A renamed-from-other-system asset that happened to land in the
  prefix's namespace would similarly disappear.
- Concurrent deploys that share the same ``deployment_name``
  cannot coexist safely — they'll see each other's resources as
  stale on every cleanup pass. (With tagging on, they'd be
  separated by a ``Deployment`` tag value match. Without tagging,
  the IDs alone are the identity.)

Mitigations:

- Pick a ``deployment_name`` that is **highly unlikely to collide**
  with anything else in your QS account. Embedding the team /
  service / environment name (``qsgen-treasury-prod``) gives you a
  meaningful, unambiguous namespace.
- **Run ``json clean --dry-run`` first** every time. It prints the
  full list of resources it would delete; visually verify before
  passing ``--execute``.
- Treat the QS account as effectively single-tenant for this
  deployment_name. Don't deploy two ``tagging_enabled: false`` configs with
  overlapping ``deployment_name`` values into the same account.

## When you should not use this

- **You have ``quicksight:TagResource`` permission.** Default to
  the tagged path. The tag-based isolation is strictly better in
  every dimension that doesn't involve IAM constraints.
- **You're running concurrent deploys (CI matrix, multi-team).**
  The ID-prefix path can't disambiguate two deploys with the same
  prefix. Keep tagging on; let each deploy stamp its own
  ``Deployment`` tag.
- **Your QuickSight account hosts assets created by other tools
  or hand-builders.** Any of them with an ID matching your prefix
  will be swept by cleanup.

## Re-enabling after an opt-out

Setting ``tagging_enabled: true`` (or removing the key) on the
next deploy DOES NOT retroactively tag the previously-untagged
resources. They stay untagged in QuickSight. ``json clean`` after
flipping the flag back on would skip them (fail-CLOSED on the
absent ``ManagedBy`` tag).

To migrate from a long-lived ``tagging_enabled: false`` deploy
back to the tagged path, you have to either:

1. ``json clean --execute`` once **before** flipping the flag,
   while the ID-prefix matcher can still find the legacy
   resources. Then re-deploy with tagging on so the new resources
   land tagged.
2. Or live with the legacy untagged resources permanently and
   periodically clean them via the AWS QS console manually.

## Summary

| Aspect | ``tagging_enabled: true`` (default) | ``tagging_enabled: false`` |
| --- | --- | --- |
| IAM permissions needed | ``quicksight:TagResource``, ``quicksight:UntagResource`` | None for tagging |
| Cleanup match basis | Tag values (``ManagedBy``, ``Deployment``) | ID prefix (``deployment_name``) |
| Coexistence with other deploys | Safe (per-deployment tag values) | Unsafe (same prefix → same scope) |
| Coexistence with hand-built assets | Safe (untagged stays untouched) | Unsafe (matching ID prefix gets swept) |
| Recommended for production | ✓ | Only when forced by IAM policy |
