"""Cross-entity validation for a loaded ``L2Instance`` (M.1.3).

The loader (M.1.2) catches malformed YAML + per-entity shape errors. This
module catches everything else the SPEC requires at load time — rules
that need to look across multiple entities to decide.

Public entry point: ``validate(instance)``. Raises ``L2ValidationError``
on the first failure with a domain-flavor banking message identifying
the offending entity + the rule that failed.

BX.14 (2026-06-11) — error messages rewritten in CPA-readable banking
phrasing. Each ``L2ValidationError`` now carries a structured ``code``
(rule id like ``R5`` / ``C8a`` / ``U7``) + ``message`` (plain-language
prose); ``str(exc)`` returns ``"[<code>] <message>"`` so the Studio
error-banner can split the prefix off + wire a per-family side-panel
``[?]`` trigger pointing at long-form context. Use
``validator_glossary_anchor_for(code)`` to map an error code to the
corresponding ``GLOSSARY`` anchor in ``common/html/_side_panel.py``.

**Locked rule (per L.1.18 + M.1.7):** every cross-entity validator that
``validate(instance)`` runs has a dedicated rejection test in
``tests/test_l2_validate.py``. The rule numbering in this docstring
matches the test names (e.g. rule U1 → ``test_u1_duplicate_account_id_rejected``).
Adding a new validator MUST land its rejection test in the same commit
that introduces it; the audit table below extends to cover the new rule.

Rules enforced (numbered for cross-reference with the test file):
  U1. Account.id values are unique within ``accounts``.
  U2. AccountTemplate.role values are unique within ``account_templates``.
  U3. Rail.name values are unique within ``rails``.
  U4. TransferTemplate.name values are unique within ``transfer_templates``.
  U5. LimitSchedule (parent_role, rail, direction) triples are unique
      (M.1a — duplicate combinations are a configuration error).
      Z.B (2026-05-15): renamed from (parent_role, transfer_type) when
      the symmetric transfer_type collapse landed. AB.1 (2026-05-19):
      added ``direction`` to the key — same (parent_role, rail) may
      now appear twice with different directions (one Outbound +
      one Inbound).
  U7. AccountTemplate-generated account_ids MUST NOT collide with any
      declared Account.id (AA.A.6.bug 2026-05-17). The seed plants both
      the singleton AND the template-rendered instance under the same
      id, producing two ``account_name`` values for one account_id;
      downstream the L1 dashboard renders inconsistent dropdown labels
      vs WHERE-clause matches and silently breaks per-account narrowing.
      Author resolution: rename the singleton, drop the redundant
      template, OR set ``instance_id_template`` to a non-colliding
      pattern. See :func:`recon_gen.common.l2.auto_scenario.
      template_instance_ids` — the validator walks the same rendering
      path the seed uses so the collision set is computed identically.

  Removed under Z.B grammar collapse (PLAN.md §Z.B — locked 2026-05-15):
  - U6 (Rail per-leg ``(transfer_type, role)`` discriminators unique) —
    transfer_type is gone; rail-to-transaction binding is now `rail_name`
    directly, which U3 already enforces unique.

  R1. Every Role referenced by a Rail (source_role / destination_role /
      leg_role) resolves to some Account.role OR AccountTemplate.role.
  R2. Every Account.parent_role resolves to some Account.role OR
      AccountTemplate.role.
  R3. Every AccountTemplate.parent_role MUST resolve to a singleton
      Account.role (NOT an AccountTemplate.role) — per the SPEC's
      "Singleton parent only" rule on AccountTemplate.
  R4. Every RailName in a TransferTemplate.leg_rails exists in ``rails``.
  R5. Every Chain.parent and every Chain.children entry resolves to a
      Rail name OR TransferTemplate name.
  R6. Every LimitSchedule.parent_role resolves to some declared Role.
  R7. Every TransferTemplate.leg_rails entry references a NON-aggregating
      Rail (M.1a — aggregating rails sweep on a cadence and don't carry
      the per-instance identity a TransferKey-grouped template needs).
  R8. Every Rail with ``max_unbundled_age`` set MUST appear in some
      AggregatingRail's ``bundles_activity`` (M.1a — otherwise the watch
      can never fire).
  R9. Every dotted-form BundleSelector (``Template.LegRail``) references
      a rail that is actually in that template's ``leg_rails`` (M.1a —
      catches typos + leg-rail cross-references at load).
  R10. Every ``LimitSchedule.rail`` matches some declared ``Rail.name``
      (M.2d.1 — a cap declared against a rail no L2 declares is a
      no-op; catches typos). Z.B (2026-05-15): formerly checked
      transfer_type alignment; under collapse the cap binds directly
      to a rail name.
  R11. Every bare-form (``<name>``, not ``Template.LegRail``) entry in
      an AggregatingRail's ``bundles_activity`` resolves to a declared
      ``Rail.name`` (Z.B 2026-05-15: formerly also matched
      Rail.transfer_type, dropped under the symmetric collapse).
      Companion to R8 (which checks the inverse: any rail with
      ``max_unbundled_age`` set must appear in *some* bundles_activity).
  R12. Every ``TransferKey`` field name MUST appear in
      ``metadata_keys`` of every Rail in the template's ``leg_rails``
      (M.3.13 — a TransferKey field is auto-derived as a
      ``PostedRequirement`` for every leg_rail; if the field isn't
      declared in the rail's ``metadata_keys``, the integrator's ETL
      has no legitimate place to populate it, and the leg can never
      reach Status=Posted).
  R13. Every key in a Rail's ``metadata_value_examples`` MUST appear
      in the same Rail's ``metadata_keys`` (M.4.2b — a typo'd example
      key would otherwise be silently ignored by the seed picker;
      catch it at load).

  C1. Every TransferTemplate contains at most one *non-grouped*
      Variable-direction leg (AB.3 rewrite — Variables that are
      members of a ``leg_rail_xor_groups`` group are exempt from this
      count; the runtime "exactly one fires per Transfer" check moves
      to the ``_xor_group_violation`` matview).
  C1a. Every member of every ``TransferTemplate.leg_rail_xor_groups``
      group is also in that template's ``leg_rails``.
  C1b. Every XOR-group member resolves to a Variable-direction
      SingleLegRail (Debit / Credit / non-SingleLeg rails are excluded).
  C1c. No rail appears in two XOR groups within the same template
      (overlap groups can't resolve to one firing deterministically).
  C1d. Every XOR group has ≥2 members (a 1-member group is degenerate).
  C3. Every Variable-direction SingleLegRail MUST appear in some
      ``TransferTemplate.leg_rails`` (M.3.13 — Variable closure
      semantics require a containing template's ``ExpectedNet`` to
      compute the leg's amount + direction; a Variable rail
      reconciled only by an AggregatingRail has no closure target).
  C5. Every Chain row's ``children`` list is non-empty (Z.A grammar
      collapse — singleton ⇒ required, multi ⇒ XOR; an empty list is
      a degenerate row that encodes no firing rule. Defense-in-depth
      against in-memory L2 instances built outside the loader; loader
      rejects empty lists earlier with a more actionable error.)
  C6. For any given Chain parent, no child appears in two Chain rows
      (Z.A grammar collapse — the new failure mode the collapsed shape
      introduces. E.g. one row says "Foo is required" plus another
      says "Foo is one of [Foo, Bar]" — the two rows contradict so
      reject at load.)
  C8a. ``fan_in=True`` requires every chain child to resolve to a
      TransferTemplate. Rail-as-child fan-in isn't well-defined per
      AB.4 gap doc §2 footnote — a rail's per-Transfer parent is the
      canonical 1:1 shape.
  C8b. ``expected_parent_count`` MUST be None when ``fan_in=False``
      (the field only carries meaning under fan-in; setting it on a
      non-fan-in chain is operator confusion).
  C8c. ``expected_parent_count`` MUST be ≥2 when set under
      ``fan_in=True`` (a 1-parent fan-in chain is degenerate — it's
      just a 1:1 chain).

  Removed under Z.A grammar collapse (PLAN.md §Z.A — locked 2026-05-13):
  - C2 (xor_group members share parent) — every Chain row IS one
    parent, so the cross-parent failure mode is unrepresentable.
  - C4 (xor_group ≥ 2 members) — singleton means "required", not
    "degenerate XOR". The cardinality-1 case is now a meaningful row
    shape, not an error.
  - C4.1 (required + xor_group contradiction) — the two flags are
    gone; the contradiction is unrepresentable.

  S1. A two-leg Rail that is NOT a TransferTemplate leg MUST have
      ``expected_net`` set.
  S2. A two-leg Rail that IS a TransferTemplate leg MUST NOT have
      ``expected_net`` set (the template owns the bundle's ExpectedNet).
  S3. Every NON-aggregating single-leg Rail MUST be reconciled — appears
      in some TransferTemplate.leg_rails OR some aggregating Rail's
      bundles_activity (matched by Rail.name; Z.B 2026-05-15 dropped
      the legacy Rail.transfer_type alternative). Aggregating single-leg
      rails are exempt — they ARE the reconciliation mechanism (per
      SPEC's "single-leg sweep that lands in an external counterparty"
      example).
  S4. Aggregating Rails MUST NOT appear in any Chain.children.
  S5. Aggregating Rails MUST declare both ``cadence`` and
      ``bundles_activity``.
  S6. Non-aggregating Rails MUST NOT declare ``cadence`` or
      ``bundles_activity``.

  V1. Every TransferTemplate.completion matches a v1
      CompletionExpression vocabulary literal.
  V2. Every aggregating Rail's cadence matches a v1 CadenceExpression
      vocabulary literal.

  O1. Every leg of every Rail resolves to an Origin per the SPEC's
      per-leg Origin resolution table (M.1a). 1-leg rails MUST set
      ``origin``; 2-leg rails MUST cover both legs via either rail-level
      ``origin`` alone OR both per-leg overrides OR one override + the
      rail-level fallback.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .primitives import (
    Identifier,
    L2Instance,
    Rail,
    SingleLegRail,
    TwoLegRail,
)


# -- Errors -------------------------------------------------------------------


class L2ValidationError(ValueError):
    """Raised when a loaded ``L2Instance`` fails cross-entity validation.

    BX.14 (2026-06-11) — carries structured ``code`` (rule id like ``R5`` /
    ``C8a`` / ``U7``) + ``message`` (operator-facing CPA-readable banking
    prose). ``str(exc)`` returns ``"[<code>] <message>"`` so the Studio
    error-banner can split the prefix off + wire a per-family side-panel
    ``[?]`` trigger pointing at long-form context.

    Construction forms:
      - ``L2ValidationError(code, message)`` — preferred (BX.14 catalog)
      - ``L2ValidationError(message)`` — legacy bare string (still accepted
        for callers that haven't been migrated; code defaults to ``""``
        and the banner falls back to no side-panel pointer)
    """

    code: str
    message: str

    def __init__(self, code: str, message: str | None = None) -> None:
        # Legacy single-arg form: treat the arg as message, code="".
        if message is None:
            self.code = ""
            self.message = code
            super().__init__(code)
            return
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# -- BX.14 plain-language error-family mapping --------------------------------


# Each error code maps to a glossary anchor so the Studio side-panel
# can deep-link the operator to the long-form SPEC context. Families
# share an anchor when the failure shape + remediation pattern is the
# same (e.g. every R-rule is "this reference doesn't resolve"; every
# S-rule is "this rail is wired wrong").
_VALIDATOR_FAMILY_BY_CODE_PREFIX: dict[str, str] = {
    "U": "validator-uniqueness-rules",
    "R": "validator-reference-rules",
    "C": "validator-cardinality-rules",
    "S": "validator-state-rules",
    "V": "validator-vocabulary-rules",
    "W": "validator-firings-rules",
    "O": "validator-origin-rules",
    "M": "validator-scope-rules",
}


def validator_glossary_anchor_for(code: str) -> str | None:
    """Resolve a validator error code to its side-panel glossary anchor.

    Returns ``None`` for an empty / unrecognized code (legacy errors that
    haven't been migrated to the BX.14 catalog). The Studio editor's
    error banner consults this to decide whether to render the per-error
    ``[?]`` trigger.
    """
    if not code:
        return None
    return _VALIDATOR_FAMILY_BY_CODE_PREFIX.get(code[0])


# -- Vocabulary literals (per SPEC v1) ----------------------------------------


_COMPLETION_PATTERNS = (
    re.compile(r"^business_day_end$"),
    re.compile(r"^business_day_end\+(\d+)d$"),
    re.compile(r"^month_end$"),
    re.compile(r"^metadata\.[A-Za-z_][A-Za-z0-9_]*$"),
)

_WEEKDAY_NAMES = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

_CADENCE_PATTERNS = (
    re.compile(r"^intraday-(\d+)h$"),
    re.compile(r"^daily-eod$"),
    re.compile(r"^daily-bod$"),
    re.compile(r"^weekly-(mon|tue|wed|thu|fri|sat|sun)$"),
    re.compile(r"^monthly-eom$"),
    re.compile(r"^monthly-bom$"),
    re.compile(r"^monthly-(\d+)$"),
)


def _completion_is_valid(expr: str) -> bool:
    return any(p.match(expr) for p in _COMPLETION_PATTERNS)


def _cadence_is_valid(expr: str) -> bool:
    for p in _CADENCE_PATTERNS:
        m = p.match(expr)
        if not m:
            continue
        # Bounds checks: monthly-N is day-of-month 1..31.
        if expr.startswith("monthly-") and expr not in ("monthly-eom", "monthly-bom"):
            day = int(m.group(1))
            if not 1 <= day <= 31:
                return False
        return True
    return False


# -- Public API --------------------------------------------------------------


def validate(instance: L2Instance) -> None:
    """Run every cross-entity validation rule on ``instance``.

    Fail-fast: raises ``L2ValidationError`` on the first rule violation
    with a message naming the offending field + the rule.
    """
    _check_unique_account_ids(instance)
    _check_unique_account_template_roles(instance)
    _check_unique_rail_names(instance)
    _check_unique_transfer_template_names(instance)
    _check_unique_limit_schedule_combinations(instance)
    _check_no_template_id_collides_with_singleton(instance)

    account_roles = {a.role for a in instance.accounts}
    template_roles = {t.role for t in instance.account_templates}
    all_roles = account_roles | template_roles
    rail_names = {r.name for r in instance.rails}
    template_names = {t.name for t in instance.transfer_templates}
    rails_by_name: dict[Identifier, Rail] = {r.name: r for r in instance.rails}

    _check_role_references(instance, all_roles)
    _check_account_parent_role_resolves(instance, all_roles)
    _check_account_template_parent_role_is_singleton(
        instance, account_roles, template_roles,
    )
    _check_template_leg_rails_exist(instance, rail_names)
    _check_template_has_at_least_one_leg_rail(instance)
    _check_chain_endpoints_exist(instance, rail_names, template_names)
    _check_limit_schedule_parent_role_resolves(instance, all_roles)
    _check_template_leg_rails_are_non_aggregating(instance, rails_by_name)
    _check_max_unbundled_age_only_on_bundled_rails(instance)
    _check_dotted_bundle_selectors_resolve(instance)
    _check_limit_schedule_rail_resolves(instance, rail_names)
    _check_bare_bundles_activity_selectors_resolve(instance)
    _check_transfer_key_in_leg_rail_metadata_keys(instance, rails_by_name)
    _check_metadata_value_example_keys_resolve(instance)

    _check_variable_leg_count_per_template(instance)
    _check_leg_rail_xor_group_shape(instance)
    _check_variable_single_leg_in_some_template(instance, rails_by_name)
    _check_chain_parent_has_non_empty_children(instance)
    _check_chain_no_duplicate_child_per_parent(instance)
    _check_fan_in_shape(instance, template_names)

    _check_two_leg_expected_net_consistency(instance)
    _check_single_leg_reconciliation(instance)
    _check_chain_aggregating_not_child(instance)
    _check_aggregating_rail_required_fields(instance)
    _check_amount_typical_range_shape(instance)
    _check_firings_typical_per_period_shape(instance)
    _check_non_aggregating_rail_no_cadence_or_bundles(instance)
    _check_business_day_offset_not_on_external(instance)

    _check_completion_vocabulary(instance)
    _check_cadence_vocabulary(instance)

    _check_per_leg_origin_resolution(instance)


# -- Uniqueness (U1-U4) ------------------------------------------------------


def _check_unique_account_ids(inst: L2Instance) -> None:
    """U1."""
    _reject_duplicates(
        (a.id for a in inst.accounts), label="Account.id",
    )


def _check_unique_account_template_roles(inst: L2Instance) -> None:
    """U2."""
    _reject_duplicates(
        (t.role for t in inst.account_templates), label="AccountTemplate.role",
    )


def _check_unique_rail_names(inst: L2Instance) -> None:
    """U3."""
    _reject_duplicates(
        (r.name for r in inst.rails), label="Rail.name",
    )


def _check_unique_transfer_template_names(inst: L2Instance) -> None:
    """U4."""
    _reject_duplicates(
        (t.name for t in inst.transfer_templates),
        label="TransferTemplate.name",
    )


def _check_unique_limit_schedule_combinations(inst: L2Instance) -> None:
    """U5: each (parent_role, rail, direction) triple appears at most once.

    Per SPEC: duplicate combinations are a load-time configuration error
    (the projection into ``StoredBalance.Limits`` would be ambiguous —
    which cap wins?). Z.B (2026-05-15): renamed from
    (parent_role, transfer_type) under the symmetric collapse. AB.1
    (2026-05-19) added ``direction``: the same ``(parent_role, rail)``
    may now carry both an Outbound AND an Inbound cap (per-direction
    flow caps split AML inbound thresholds from per-rail send caps).
    """
    seen: dict[tuple[Identifier, str, str], int] = {}
    for i, ls in enumerate(inst.limit_schedules):
        key = (ls.parent_role, ls.rail, ls.direction)
        if key in seen:
            raise L2ValidationError(
                "U5",
                f"This limit schedule duplicates an earlier one — "
                f"role {ls.parent_role!r} already has a {ls.direction!r} "
                f"cap on rail {ls.rail!r} (declared at "
                f"limit_schedules[{seen[key]}], duplicated at "
                f"limit_schedules[{i}]). Either drop the duplicate or "
                f"change one of (parent_role, rail, direction)."
            )
        seen[key] = i


def _check_no_template_id_collides_with_singleton(inst: L2Instance) -> None:
    """U7: AccountTemplate-generated account_ids MUST NOT collide with
    any declared Account.id.

    The seed plants both the singleton AND the template-rendered
    instance under the same id, producing two ``account_name`` values
    for one ``account_id``. The L1 dashboard's dropdown source
    (``current_daily_balances`` DISTINCT) then advertises both display
    strings; the WHERE clause picks rows by *one* of them, so picking
    the dropdown option silently narrows to half the account's rows.

    Walks :func:`recon_gen.common.l2.auto_scenario.template_instance_ids`
    so the validator's ID set is the same set the seed will plant —
    impossible to drift apart.
    """
    # Import locally to avoid auto_scenario ↔ validate import cycle
    # at module load (auto_scenario imports nothing from validate but
    # the surrounding common.l2 package wires them together).
    from recon_gen.common.l2.auto_scenario import template_instance_ids
    singleton_ids = {str(a.id): i for i, a in enumerate(inst.accounts)}
    for ti, template in enumerate(inst.account_templates):
        for generated in template_instance_ids(template):
            if generated in singleton_ids:
                raise L2ValidationError(
                    "U7",
                    f"The {template.role!r} role's account template "
                    f"(account_templates[{ti}]) materializes "
                    f"account_id {generated!r}, but that id is already "
                    f"declared as a standalone account "
                    f"(accounts[{singleton_ids[generated]}]). The two "
                    f"would land under the same row id with two "
                    f"different display names, which breaks per-"
                    f"account narrowing on the dashboards. Either "
                    f"rename the standalone account, drop the "
                    f"redundant template, or change the template's "
                    f"`instance_id_template` to a non-colliding "
                    f"pattern (e.g. `tmpl-cust-{{n:03d}}`)."
                )


def _reject_duplicates(values: Iterable[Identifier], *, label: str) -> None:
    counts = Counter(values)
    dupes = sorted(v for v, c in counts.items() if c > 1)
    if dupes:
        # Map the internal `label` to (code, human-readable-noun) for
        # the BX.14 plain-language form. Falls back to the legacy noun
        # if the caller passes something unmapped.
        code, noun = _UNIQ_LABEL_TO_PLAIN.get(
            label, ("U0", label),
        )
        raise L2ValidationError(
            code,
            f"These {noun} values are declared more than once: "
            f"{dupes!r}. Each one must be unique within the L2; "
            f"rename or drop the duplicates."
        )


# (code, plain-language noun) for the four U-rules that share the
# generic duplicate-rejection path. Co-located with the helper so
# adding a new uniqueness rule lands here, not in a remote enum.
_UNIQ_LABEL_TO_PLAIN: dict[str, tuple[str, str]] = {
    "Account.id": ("U1", "account id"),
    "AccountTemplate.role": ("U2", "account template role"),
    "Rail.name": ("U3", "rail name"),
    "TransferTemplate.name": ("U4", "transfer template name"),
}


# -- Reference resolution (R1-R6) --------------------------------------------


def _check_role_references(inst: L2Instance, all_roles: set[Identifier]) -> None:
    """R1: Every Role referenced by a Rail's role fields resolves to a declared Role."""
    for r in inst.rails:
        match r:
            case TwoLegRail(name=n, source_role=src, destination_role=dst):
                _check_role_set(src, all_roles, where=f"Rail {n!r}.source_role")
                _check_role_set(dst, all_roles, where=f"Rail {n!r}.destination_role")
            case SingleLegRail(name=n, leg_role=leg):
                _check_role_set(leg, all_roles, where=f"Rail {n!r}.leg_role")


def _check_role_set(
    roles: tuple[Identifier, ...], declared: set[Identifier], *, where: str,
) -> None:
    missing = [r for r in roles if r not in declared]
    if missing:
        raise L2ValidationError(
            "R1",
            f"{where}: role(s) {missing!r} aren't declared anywhere — "
            f"no account and no account template uses that role. Add "
            f"the role to an account / template, or fix the typo on "
            f"this rail."
        )


def _check_account_parent_role_resolves(
    inst: L2Instance, all_roles: set[Identifier],
) -> None:
    """R2: every Account.parent_role resolves to some declared Role."""
    for a in inst.accounts:
        if a.parent_role is not None and a.parent_role not in all_roles:
            raise L2ValidationError(
                "R2",
                f"Account {a.id!r} rolls up to parent_role "
                f"{a.parent_role!r}, but that role isn't declared on "
                f"any account or account template. Either declare the "
                f"parent role, or fix the typo here."
            )


def _check_account_template_parent_role_is_singleton(
    inst: L2Instance,
    account_roles: set[Identifier],
    template_roles: set[Identifier],
) -> None:
    """R3: AccountTemplate.parent_role MUST resolve to a singleton Account.

    Per SPEC: template-under-template nesting is forbidden because the
    per-instance parent assignment becomes ambiguous (which of N
    parent-template instances does a given child-template instance roll
    up to?).
    """
    for t in inst.account_templates:
        if t.parent_role is None:
            continue
        if t.parent_role in template_roles and t.parent_role not in account_roles:
            raise L2ValidationError(
                "R3",
                f"Account template {t.role!r} tries to roll up to "
                f"parent_role {t.parent_role!r}, but that role resolves "
                f"to another account template. A template's parent must "
                f"be a standalone (1:1) account — template-under-"
                f"template nesting is forbidden because each child "
                f"instance would have N possible parents."
            )
        if t.parent_role not in account_roles:
            raise L2ValidationError(
                "R3",
                f"Account template {t.role!r} tries to roll up to "
                f"parent_role {t.parent_role!r}, but no standalone "
                f"account uses that role. Declare a standalone account "
                f"with that role, or fix the typo."
            )


def _check_template_leg_rails_exist(
    inst: L2Instance, rail_names: set[Identifier],
) -> None:
    """R4: every RailName in TransferTemplate.leg_rails exists."""
    for t in inst.transfer_templates:
        missing = [n for n in t.leg_rails if n not in rail_names]
        if missing:
            raise L2ValidationError(
                "R4",
                f"Transfer template {t.name!r} lists leg rail(s) "
                f"{missing!r} that aren't declared in the rails list. "
                f"Either declare the rail, or remove it from this "
                f"template's leg_rails."
            )


def _check_template_has_at_least_one_leg_rail(inst: L2Instance) -> None:
    """R4.1 (X.4.f.10): every TransferTemplate must declare at least one
    leg_rail. A template with zero leg_rails has no rail firings to
    bundle into a transfer event — there's nothing for the L1 layer to
    measure ``expected_net`` or ``completion`` against. The Studio
    editor surfaces this as the inline error when the operator
    de-selects the last rail in the multi_select; the L1 layer would
    silently ignore the template otherwise.
    """
    for t in inst.transfer_templates:
        if len(t.leg_rails) == 0:
            raise L2ValidationError(
                "R4.1",
                f"Transfer template {t.name!r} has an empty leg_rails "
                f"list — a template with no legs has nothing to bundle "
                f"and no expected_net to enforce. Either add at least "
                f"one rail or delete the template entirely."
            )


def _check_chain_endpoints_exist(
    inst: L2Instance,
    rail_names: set[Identifier],
    template_names: set[Identifier],
) -> None:
    """R5: every Chain.parent and every Chain.children entry resolves to a Rail or Template."""
    valid = rail_names | template_names
    for i, c in enumerate(inst.chains):
        if c.parent not in valid:
            raise L2ValidationError(
                "R5",
                f"Chain row chains[{i}] names {c.parent!r} as its "
                f"parent, but no rail or transfer template uses that "
                f"name. Either declare it, or fix the typo on this "
                f"chain."
            )
        for j, child in enumerate(c.children):
            if child.name not in valid:
                raise L2ValidationError(
                    "R5",
                    f"Chain row chains[{i}].children[{j}] is "
                    f"{child.name!r}, but no rail or transfer template "
                    f"uses that name. Either declare it, or fix the "
                    f"typo on this chain."
                )


def _check_limit_schedule_parent_role_resolves(
    inst: L2Instance, all_roles: set[Identifier],
) -> None:
    """R6: every LimitSchedule.parent_role resolves to some declared Role."""
    for i, ls in enumerate(inst.limit_schedules):
        if ls.parent_role not in all_roles:
            raise L2ValidationError(
                "R6",
                f"This limit schedule (limit_schedules[{i}]) caps role "
                f"{ls.parent_role!r}, but no account or account "
                f"template uses that role. The cap would never fire — "
                f"declare the role or fix the typo."
            )


def _check_template_leg_rails_are_non_aggregating(
    inst: L2Instance, rails_by_name: dict[Identifier, Rail],
) -> None:
    """R7: every TransferTemplate.leg_rails entry references a non-Aggregating Rail.

    Per SPEC: aggregating rails sweep on a cadence and don't carry the
    per-instance identity a TransferKey-grouped template needs. Listing
    one in ``leg_rails`` is a configuration mistake — the template's
    ExpectedNet closure can't be evaluated against a sweeping rail.
    """
    for t in inst.transfer_templates:
        for n in t.leg_rails:
            r = rails_by_name.get(n)
            # R4 already guarantees `n` exists; this rule only triggers
            # when the referenced rail IS aggregating.
            if r is not None and r.aggregating:
                raise L2ValidationError(
                    "R7",
                    f"Transfer template {t.name!r} lists rail {n!r} as "
                    f"a leg, but {n!r} is an aggregating rail (it "
                    f"sweeps on a cadence). A sweep can't be a "
                    f"template leg — per-Transfer closure has no "
                    f"individual firing to bind to. Replace this leg "
                    f"with a non-aggregating rail."
                )


def _check_max_unbundled_age_only_on_bundled_rails(inst: L2Instance) -> None:
    """R8: every Rail with ``max_unbundled_age`` set MUST appear in some
    AggregatingRail's ``bundles_activity``.

    Per SPEC: the watch fires when a Posted-and-eligible-for-bundling row
    sits unassigned past the threshold. If nothing bundles this rail, the
    watch can never fire — declaring it is a configuration error.

    Z.B (2026-05-15): bundles_activity matches Rail.name (or template-leg
    name in the dotted form) only — the legacy transfer_type fallback is
    gone with the symmetric collapse.
    """
    bundled: set[Identifier] = set()
    for r in inst.rails:
        if not r.aggregating:
            continue
        for sel in r.bundles_activity:
            sel_str = str(sel)
            # Dotted form (Template.LegRail) — the leg-rail name is the
            # part after the dot; that IS what gets bundled.
            if "." in sel_str:
                _, _, leg = sel_str.partition(".")
                bundled.add(Identifier(leg))
            else:
                # Bare identifier — Rail.name or TransferTemplate.name.
                bundled.add(Identifier(sel_str))
    for r in inst.rails:
        if r.max_unbundled_age is None:
            continue
        if r.name in bundled:
            continue
        raise L2ValidationError(
            "R8",
            f"Rail {r.name!r} sets a max_unbundled_age watch, but no "
            f"aggregating rail bundles its activity — the watch would "
            f"never fire because nothing sweeps these rows. Either "
            f"add {r.name!r} to some aggregator's bundles_activity, "
            f"or drop max_unbundled_age from this rail."
        )


def _check_limit_schedule_rail_resolves(
    inst: L2Instance, rail_names: set[Identifier],
) -> None:
    """R10: every LimitSchedule.rail matches some declared Rail.name.

    Per M.2d.1: a cap declared against a rail that no L2 emits is a
    no-op — the limit-breach matview keys off the rail name, so a
    typo'd cap never fires. Caught at YAML load.

    Z.B (2026-05-15): formerly checked transfer_type alignment; under
    the symmetric collapse the cap binds directly to a rail name.
    """
    for i, ls in enumerate(inst.limit_schedules):
        if ls.rail not in rail_names:
            raise L2ValidationError(
                "R10",
                f"This limit schedule (limit_schedules[{i}]) caps "
                f"rail={ls.rail!r}, but no rail by that name is "
                f"declared. The cap would never fire — declare the "
                f"rail or fix the typo. Available rail names: "
                f"{sorted(rail_names)!r}."
            )


def _check_bare_bundles_activity_selectors_resolve(inst: L2Instance) -> None:
    """R11: every bare-form bundles_activity selector resolves.

    Per M.2d.1: a bare-form selector (``<name>``, not ``Template.LegRail``)
    must match a declared Rail.name. Otherwise the bundler matches
    nothing and the aggregating rail silently never sweeps. R8
    (max_unbundled_age set ⇒ rail must be bundled) and R9 (dotted form
    ⇒ template + leg actually exist) cover the inverse and the dotted
    form respectively; this rule catches typos in the bare form.

    Z.B (2026-05-15): formerly accepted Rail.transfer_type as a fallback
    match; transfer_type is gone with the symmetric collapse.
    """
    rail_names = {r.name for r in inst.rails}
    for r in inst.rails:
        if not r.aggregating:
            continue
        for sel in r.bundles_activity:
            sel_str = str(sel)
            if "." in sel_str:
                # Dotted form — R9's job, not R11's.
                continue
            if sel_str in rail_names:
                continue
            raise L2ValidationError(
                "R11",
                f"Aggregating rail {r.name!r} bundles activity from "
                f"{sel_str!r}, but no rail by that name is declared. "
                f"The bundler would sweep nothing — fix the typo or "
                f"declare the rail. Available rail names: "
                f"{sorted(rail_names)!r}."
            )


def _check_dotted_bundle_selectors_resolve(inst: L2Instance) -> None:
    """R9: every dotted-form BundleSelector references a real template-leg pair.

    Per SPEC: ``Template.LegRail`` is one of the 4 BundleSelector forms;
    it scopes the bundler's eligibility to one specific leg-pattern of
    a multi-leg template. This rule catches typos in either side AND
    cross-references where the leg-rail isn't actually a leg of that
    template (a common mistake when copy-pasting selectors).
    """
    template_leg_rails: dict[Identifier, set[Identifier]] = {
        t.name: set(t.leg_rails) for t in inst.transfer_templates
    }
    for r in inst.rails:
        if not r.aggregating:
            continue
        for sel in r.bundles_activity:
            sel_str = str(sel)
            if "." not in sel_str:
                continue
            template_name, _, leg_name = sel_str.partition(".")
            tn = Identifier(template_name)
            if tn not in template_leg_rails:
                raise L2ValidationError(
                    "R9",
                    f"Aggregating rail {r.name!r} bundles activity "
                    f"from {sel_str!r}, but transfer template "
                    f"{template_name!r} isn't declared. Either declare "
                    f"the template, or fix the typo on this selector."
                )
            ln = Identifier(leg_name)
            if ln not in template_leg_rails[tn]:
                raise L2ValidationError(
                    "R9",
                    f"Aggregating rail {r.name!r} bundles activity "
                    f"from {sel_str!r}, but {leg_name!r} isn't a leg "
                    f"of transfer template {template_name!r}. Either "
                    f"add the rail to that template's leg_rails, or "
                    f"fix the selector to reference the right leg."
                )


def _check_transfer_key_in_leg_rail_metadata_keys(
    inst: L2Instance, rails_by_name: dict[Identifier, Rail],
) -> None:
    """R12: every TransferKey field name MUST appear in metadata_keys of
    every Rail in the template's leg_rails.

    Per SPEC §"PostedRequirements": TransferKey fields are auto-derived
    as PostedRequirements for every leg_rail (``derived.posted_requirements_for``).
    A leg can't be Posted without those fields populated. If the field
    isn't declared in the rail's ``metadata_keys``, the integrator's
    ETL has no legitimate place to populate it — the column simply
    doesn't exist on the rail's posting shape — and the rail can never
    reach Status=Posted. That's a configuration error, caught at load
    instead of at first posting attempt.
    """
    for t in inst.transfer_templates:
        if not t.transfer_key:
            continue
        for n in t.leg_rails:
            r = rails_by_name.get(n)
            # R4 already guarantees the rail exists; defensive skip.
            if r is None:
                continue
            missing = [
                k for k in t.transfer_key if k not in r.metadata_keys
            ]
            if missing:
                raise L2ValidationError(
                    "R12",
                    f"Transfer template {t.name!r} groups legs by "
                    f"transfer_key {list(t.transfer_key)!r}, but leg "
                    f"rail {n!r} doesn't carry the field(s) "
                    f"{missing!r} in its metadata_keys. The ETL has "
                    f"nowhere to put those values on this leg, so it "
                    f"can never reach Status=Posted. Either add the "
                    f"missing key(s) to rail {n!r}'s metadata_keys, "
                    f"or drop them from the template's transfer_key."
                )


def _check_metadata_value_example_keys_resolve(inst: L2Instance) -> None:
    """R13: every key in a Rail's ``metadata_value_examples`` MUST also
    appear in that Rail's ``metadata_keys``.

    Catches typos. The seed picker only consults examples by-key for
    keys it's already iterating from ``metadata_keys``, so a typo'd
    example-list key would silently never be used — the integrator
    would never see a feedback signal that their example data is
    wrong. Caught at load instead.
    """
    for r in inst.rails:
        if not r.metadata_value_examples:
            continue
        declared = set(r.metadata_keys)
        for key, _values in r.metadata_value_examples:
            if key not in declared:
                raise L2ValidationError(
                    "R13",
                    f"Rail {r.name!r} carries example metadata values "
                    f"for key {key!r}, but the rail's metadata_keys "
                    f"({list(r.metadata_keys)!r}) doesn't list that "
                    f"key. The examples would be silently ignored. "
                    f"Either add the key to metadata_keys, or remove "
                    f"the example list."
                )


# -- Cardinality (C1-C4) -----------------------------------------------------


def _check_variable_leg_count_per_template(inst: L2Instance) -> None:
    """C1 (AB.3 rewrite): at most one *non-grouped* Variable-direction
    leg per TransferTemplate.

    Pre-AB.3 C1 was "≤1 Variable per template, period". AB.3 relaxes
    that for XOR-grouped Variables: a template MAY declare any number
    of Variable-direction legs as long as every additional one beyond
    the first non-grouped Variable is a member of some
    ``leg_rail_xor_groups`` group (where the AB.3.3 matview enforces
    exactly-one-firing-per-Transfer at runtime). The structural
    invariants on the groups themselves live in C1a-d below.
    """
    rails_by_name: dict[str, Rail] = {r.name: r for r in inst.rails}
    for t in inst.transfer_templates:
        grouped: set[Identifier] = {
            member for group in t.leg_rail_xor_groups for member in group
        }
        variable_legs = [
            n for n in t.leg_rails
            if isinstance(rails_by_name.get(n), SingleLegRail)
            and isinstance(rails_by_name[n], SingleLegRail)
            and rails_by_name[n].leg_direction == "Variable"  # type: ignore[union-attr]: narrowed by the prior isinstance(..., SingleLegRail) check
        ]
        non_grouped_variables = [n for n in variable_legs if n not in grouped]
        if len(non_grouped_variables) > 1:
            raise L2ValidationError(
                "C1",
                f"Transfer template {t.name!r} contains "
                f"{len(non_grouped_variables)} non-grouped Variable-"
                f"direction legs ({non_grouped_variables!r}). Only "
                f"one may be left ungrouped — otherwise the closure "
                f"that computes each leg's amount + direction can't "
                f"pick which leg to solve for. Either combine the "
                f"extras into a leg_rail_xor_groups entry, or fix one "
                f"to a Debit / Credit direction."
            )


def _check_leg_rail_xor_group_shape(inst: L2Instance) -> None:
    """C1a-d (AB.3): structural rules on TransferTemplate.leg_rail_xor_groups.

    - **C1a**: every member of every group MUST appear in the same
      template's ``leg_rails``.
    - **C1b**: every member MUST resolve to a Variable-direction
      SingleLegRail. (A non-Variable rail in an XOR group is a category
      error — the "exactly one fires per Transfer" matview enforcement
      only makes sense for Variable closure legs.)
    - **C1c**: no rail appears in two XOR groups within the same
      template. (Overlap groups can't be resolved to one firing
      deterministically.)
    - **C1d**: every group MUST have ≥2 members. (A 1-member group is
      degenerate — the rail always fires; the XOR adds no information.
      Same defense-in-depth shape as Z.A's C5 empty-children check.)

    Runtime "exactly one fires per Transfer" check lives in the AB.3.3
    ``_xor_group_violation`` matview, not here.
    """
    rails_by_name: dict[Identifier, Rail] = {r.name: r for r in inst.rails}
    for t in inst.transfer_templates:
        leg_rails_set: set[Identifier] = set(t.leg_rails)
        seen_members: dict[Identifier, int] = {}  # member -> group index
        for gi, group in enumerate(t.leg_rail_xor_groups):
            if len(group) < 2:
                raise L2ValidationError(
                    "C1d",
                    f"Transfer template {t.name!r}'s "
                    f"leg_rail_xor_groups[{gi}] only has "
                    f"{len(group)} member(s) — an exclusive-or group "
                    f"with one option always picks that option, so "
                    f"it adds no information. Either add more "
                    f"alternatives, or remove the group and let the "
                    f"rail stand on its own."
                )
            for member in group:
                if member not in leg_rails_set:
                    raise L2ValidationError(
                        "C1a",
                        f"Transfer template {t.name!r}'s "
                        f"leg_rail_xor_groups[{gi}] includes "
                        f"{member!r}, but that rail isn't in this "
                        f"template's leg_rails list. Either add it "
                        f"to leg_rails, or remove it from the XOR "
                        f"group."
                    )
                rail = rails_by_name.get(member)
                if not (
                    isinstance(rail, SingleLegRail)
                    and rail.leg_direction == "Variable"
                ):
                    raise L2ValidationError(
                        "C1b",
                        f"Transfer template {t.name!r}'s "
                        f"leg_rail_xor_groups[{gi}] includes "
                        f"{member!r}, but that rail isn't a Variable-"
                        f"direction single-leg rail. Only Variable "
                        f"closure legs belong in XOR groups — "
                        f"Debit / Credit / two-leg rails always fire "
                        f"when the template fires."
                    )
                if member in seen_members:
                    prior_gi = seen_members[member]
                    raise L2ValidationError(
                        "C1c",
                        f"Transfer template {t.name!r}: rail "
                        f"{member!r} appears in two XOR groups "
                        f"(groups {prior_gi} and {gi}). A rail can "
                        f"belong to at most one XOR group per "
                        f"template — otherwise the \"exactly one "
                        f"fires\" rule has nothing to settle on."
                    )
                seen_members[member] = gi


def _check_variable_single_leg_in_some_template(
    inst: L2Instance, rails_by_name: dict[Identifier, Rail],
) -> None:
    """C3: every Variable-direction SingleLegRail MUST appear in some
    TransferTemplate.leg_rails.

    Per SPEC §"LegDirection = Variable": "Both the leg's amount AND
    direction are determined at posting time by ... the requirement that
    a containing TransferTemplate's ExpectedNet hold given the other
    legs already posted." A Variable rail not in any template has no
    closure target — the bundler-only reconciliation path (S3's other
    branch) doesn't compute closure amounts, only sweeps eligible rows.
    Catches the failure mode where an integrator declares a Variable
    rail and reconciles it via an aggregating bundler, expecting the
    closure to "just work".
    """
    template_leg_names: set[Identifier] = set()
    for t in inst.transfer_templates:
        template_leg_names.update(t.leg_rails)
    for r in inst.rails:
        if not isinstance(r, SingleLegRail):
            continue
        if r.leg_direction != "Variable":
            continue
        if r.name in template_leg_names:
            continue
        raise L2ValidationError(
            "C3",
            f"Rail {r.name!r} is a Variable-direction single-leg "
            f"rail, but it isn't a leg of any transfer template. "
            f"Variable closure needs a containing template's "
            f"expected_net to solve for the leg's amount + "
            f"direction — without that, the leg has no way to "
            f"settle. Add this rail to some template's leg_rails."
        )


def _check_chain_no_duplicate_child_per_parent(inst: L2Instance) -> None:
    """C6 (Z.A grammar collapse): for any given parent, no child appears
    in two Chain rows.

    Pre-collapse, this failure mode was unrepresentable in code (the
    `required` + `xor_group` combination silently overlapped). Post-
    collapse, the collapsed shape lets the operator accidentally list
    the same child in two rows for the same parent — e.g. one row
    saying "Foo is required" plus another saying "Foo is one of [Foo,
    Bar]". The two rows contradict (Foo is required ⇒ Bar can't fire
    in the XOR; XOR ⇒ Foo doesn't have to fire), so reject at load.
    """
    for parent in {c.parent for c in inst.chains}:
        seen: dict[Identifier, int] = {}
        for c in inst.chains:
            if c.parent != parent:
                continue
            for child in c.children:
                seen[child.name] = seen.get(child.name, 0) + 1
        dupes = [name for name, count in seen.items() if count > 1]
        if dupes:
            raise L2ValidationError(
                "C6",
                f"Chain parent {str(parent)!r} lists "
                f"{sorted(str(d) for d in dupes)!r} in more than one "
                f"chain row. Each child must appear in exactly one "
                f"row per parent — a single-child row says \"this "
                f"child always fires\", a multi-child row says "
                f"\"exactly one of these fires.\" Two rows mentioning "
                f"the same child contradict each other; merge them."
            )


def _check_fan_in_shape(
    inst: L2Instance,
    template_names: set[Identifier],
) -> None:
    """C8 (AB.6 per-child): structural rules on ``ChainChildSpec.fan_in`` +
    ``ChainChildSpec.expected_parent_count``.

    - **C8a**: a child with ``fan_in=True`` MUST resolve to a
      TransferTemplate. Rail-as-child fan-in isn't well-defined —
      a rail's per-Transfer parent is the canonical 1:1 shape; the
      AB.4 gap doc §2 footnote closes this door explicitly.
    - **C8b**: ``expected_parent_count`` MUST be None when
      ``fan_in=False`` (the field only carries meaning under fan-in;
      setting it on a non-fan-in child is operator confusion that
      would mislead the matview wiring).
    - **C8c**: when ``expected_parent_count`` is set under
      ``fan_in=True``, it MUST be ≥2 (a 1-parent fan-in entry is
      degenerate — it's just a 1:1 chain; the AB.4 contract is "≥2
      parent firings share one child Transfer").

    AB.6.1 transitional: loader still parses AB.4-shape chain-level
    fan_in / expected_parent_count and synthesizes per-child copies
    (all children share the chain-level flag). AB.6.2 hard-cuts the
    chain-level keys + introduces heterogeneous per-child parsing,
    at which point this check fires per-child entry meaningfully.

    Runtime "actual parent count matches expected" check lives in
    the AB.4.7 ``_fan_in_disagreement`` matview, not here.
    """
    for c in inst.chains:
        for child in c.children:
            if child.fan_in:
                if child.name not in template_names:
                    raise L2ValidationError(
                        "C8a",
                        f"Chain parent={c.parent!r}: child "
                        f"{child.name!r} has fan_in=True, but only "
                        f"transfer templates can be fan-in "
                        f"children — a rail's per-Transfer parent "
                        f"is always 1:1 (one parent firing per rail "
                        f"leg). Either point this child at a "
                        f"transfer template, or turn fan_in off."
                    )
                if (
                    child.expected_parent_count is not None
                    and child.expected_parent_count < 2
                ):
                    raise L2ValidationError(
                        "C8c",
                        f"Chain parent={c.parent!r}: child "
                        f"{child.name!r} has fan_in=True with "
                        f"expected_parent_count="
                        f"{child.expected_parent_count}. A 1-parent "
                        f"fan-in is just a regular 1:1 chain — set "
                        f"expected_parent_count to 2 or more, or "
                        f"drop fan_in entirely."
                    )
            else:
                if child.expected_parent_count is not None:
                    raise L2ValidationError(
                        "C8b",
                        f"Chain parent={c.parent!r}: child "
                        f"{child.name!r} sets "
                        f"expected_parent_count="
                        f"{child.expected_parent_count} without "
                        f"fan_in=True. That count only matters "
                        f"under fan-in — either turn fan_in on, or "
                        f"remove the count."
                    )


def _check_chain_parent_has_non_empty_children(inst: L2Instance) -> None:
    """C5 (rewritten under Z.A): every chain row's ``children`` list is
    non-empty.

    Pre-collapse, C5 caught the "all-optional chain" mode (no required
    child, no XOR group). Post-collapse there's no all-optional mode —
    every row IS a firing rule (singleton ⇒ required, multi ⇒ XOR), so
    the only remaining failure mode is an empty children list. Loader
    rejects it earlier (Z.A.3), but keep this as a defense-in-depth
    check for in-memory L2 instances built outside the loader (tests,
    fuzz fixtures, editor mutations).
    """
    for c in inst.chains:
        if not c.children:
            raise L2ValidationError(
                "C5",
                f"Chain parent {str(c.parent)!r} has an empty "
                f"children list — a chain row with no children "
                f"encodes no firing rule. Either add at least one "
                f"child (one = required, two-or-more = XOR), or drop "
                f"the row entirely."
            )


# -- State-dependent (S1-S6) -------------------------------------------------


def _check_two_leg_expected_net_consistency(inst: L2Instance) -> None:
    """S1 + S2: standalone two-leg requires expected_net; template-leg forbids it."""
    template_leg_names: set[str] = set()
    for t in inst.transfer_templates:
        template_leg_names.update(t.leg_rails)

    for r in inst.rails:
        if not isinstance(r, TwoLegRail):
            continue
        is_template_leg = r.name in template_leg_names
        if is_template_leg and r.expected_net is not None:
            raise L2ValidationError(
                "S2",
                f"Two-leg rail {r.name!r} is part of a transfer "
                f"template and also declares its own expected_net. "
                f"The template owns the bundle's expected_net — the "
                f"rail can't carry one too. Drop expected_net from "
                f"this rail."
            )
        if not is_template_leg and r.expected_net is None:
            raise L2ValidationError(
                "S1",
                f"Two-leg rail {r.name!r} stands alone (no transfer "
                f"template wraps it), so it needs its own "
                f"expected_net to settle the two legs. Set "
                f"expected_net (typically 0, for a balanced internal "
                f"transfer)."
            )


def _check_single_leg_reconciliation(inst: L2Instance) -> None:
    """S3: every non-aggregating single-leg Rail is reconciled.

    Aggregating single-leg rails ARE the reconciliation mechanism (per
    SPEC's Aggregating Rails section: "single-leg aggregating rails are
    permitted, e.g. a single-leg sweep that lands in an external
    counterparty"). Their drift exits the system into the External
    counterparty by design — they do not themselves need to appear in
    any other rail's bundles_activity. So the S3 reconciliation check
    only applies to non-aggregating single-leg rails.

    This exemption was surfaced by the M.1.8 kitchen-sink fixture (a
    single-leg aggregating rail tripped a literal reading of the SPEC
    rule). SPEC v1's wording amended in M.1.8 to make the exemption
    explicit.
    """
    template_leg_names: set[str] = set()
    for t in inst.transfer_templates:
        template_leg_names.update(t.leg_rails)

    aggregating_bundles: set[str] = set()
    for r in inst.rails:
        if r.aggregating:
            aggregating_bundles.update(r.bundles_activity)

    for r in inst.rails:
        if not isinstance(r, SingleLegRail):
            continue
        if r.aggregating:
            # Self-reconciling per the exemption above.
            continue
        in_template = r.name in template_leg_names
        in_aggregating = r.name in aggregating_bundles
        if not (in_template or in_aggregating):
            raise L2ValidationError(
                "S3",
                f"Single-leg rail {r.name!r} has nothing to reconcile "
                f"against — it isn't part of any transfer template's "
                f"legs, and no aggregating rail sweeps it. The "
                f"imbalance it posts would sit on the books forever. "
                f"Either add it to a template's leg_rails, or list it "
                f"in some aggregator's bundles_activity."
            )


def _check_chain_aggregating_not_child(inst: L2Instance) -> None:
    """S4: aggregating Rails MUST NOT appear in any Chain.children."""
    aggregating_names = {r.name for r in inst.rails if r.aggregating}
    for i, c in enumerate(inst.chains):
        for j, child in enumerate(c.children):
            if child.name in aggregating_names:
                raise L2ValidationError(
                    "S4",
                    f"Chain row chains[{i}].children[{j}] is "
                    f"{child.name!r}, which is an aggregating rail. "
                    f"Aggregating rails sweep on a cadence — they "
                    f"don't have per-Transfer parents — so they "
                    f"can't sit as a chain child. Replace with a "
                    f"non-aggregating rail or a transfer template."
                )


def _check_aggregating_rail_required_fields(inst: L2Instance) -> None:
    """S5: aggregating Rails MUST declare cadence + bundles_activity."""
    for r in inst.rails:
        if not r.aggregating:
            continue
        if r.cadence is None:
            raise L2ValidationError(
                "S5",
                f"Aggregating rail {r.name!r} doesn't declare a "
                f"cadence — without one, we don't know when the "
                f"sweep posts. Set cadence (e.g. `daily-eod`, "
                f"`intraday-2h`, `monthly-eom`)."
            )
        if not r.bundles_activity:
            raise L2ValidationError(
                "S5",
                f"Aggregating rail {r.name!r} doesn't list any "
                f"bundles_activity — the sweep has nothing to roll "
                f"up. Either list the rails it bundles, or change "
                f"aggregating to false."
            )


def _check_amount_typical_range_shape(inst: L2Instance) -> None:
    """V1a-c (AB.5): structural rules on Rail.amount_typical_range.

    - **V1a**: when set, ``min < max`` (a degenerate single-point range
      would mean every firing samples the same amount — pointless soft
      bound; integrator probably meant either a single value [not the
      feature] or an actual range).
    - **V1b**: both ``min`` and ``max`` MUST be > 0. The bound is on
      ``abs(amount)``; signed direction is determined elsewhere
      (leg_direction for fixed rails; closure for Variable rails).
      Negative or zero magnitudes are operator confusion — reject
      loud rather than silently coerce.
    - **V1c**: ``amount_typical_range`` is forbidden on aggregating
      rails (per AB.5.0 lock — aggregator amounts derive from bundled
      children, so the per-firing bound's meaning is fuzzy; deferred
      to a future iteration if integrators want a sanity-check field
      on aggregators too).
    """
    for r in inst.rails:
        if r.amount_typical_range is None:
            continue
        lo, hi = r.amount_typical_range
        if lo >= hi:
            raise L2ValidationError(
                "V1a",
                f"Rail {r.name!r}: amount_typical_range min ({lo}) "
                f"must be strictly less than max ({hi}). A single-"
                f"point range means every firing samples the same "
                f"amount — if that's what you want, this field isn't "
                f"the right tool. Widen the range or drop the field."
            )
        if lo <= 0 or hi <= 0:
            raise L2ValidationError(
                "V1b",
                f"Rail {r.name!r}: amount_typical_range values must "
                f"both be greater than zero (got min={lo}, max={hi}). "
                f"The range bounds the absolute amount per firing; "
                f"signed and zero values don't make sense here. "
                f"Direction is set elsewhere (leg_direction for "
                f"fixed rails, closure for Variable rails)."
            )
        if r.aggregating:
            raise L2ValidationError(
                "V1c",
                f"Rail {r.name!r}: amount_typical_range can't sit on "
                f"an aggregating rail. Aggregator amounts come from "
                f"the bundled children, so a per-firing bound has no "
                f"single meaning. Remove amount_typical_range here."
            )


def _check_firings_typical_per_period_shape(inst: L2Instance) -> None:
    """W1a-c (AF / E8): structural rules on ``firings_typical_per_period``.

    Applies to Rails AND TransferTemplates (both carry the field).

    - **W1a**: ``count_range`` min ≤ max. Equal endpoints are allowed
      here (unlike AB.5's V1a strict-less-than) — a fixed count like
      ``[1, 1]`` ("exactly one InterestAccrual per ledger account per
      month") is a legitimate operator intent, not confusion.
    - **W1b**: both endpoints ≥ 0. Zero is allowed (a rail that
      typically fires zero times in some periods — e.g. a seasonal
      rail). Negative counts are operator confusion — reject loud.
    - **W1c**: forbidden on aggregating Rails — the existing ``cadence``
      field already governs aggregator firing frequency (one firing per
      cadence-period), so a count band would conflict. (Templates are
      never aggregating, so W1c is Rail-only.)
    """
    for r in inst.rails:
        ftp = r.firings_typical_per_period
        if ftp is None:
            continue
        lo, hi = ftp.count_range
        if lo > hi:
            raise L2ValidationError(
                "W1a",
                f"Rail {r.name!r}: firings_typical_per_period "
                f"min ({lo}) is larger than max ({hi}). The lower "
                f"bound must not exceed the upper bound — swap the "
                f"two, or fix the typo."
            )
        if lo < 0 or hi < 0:
            raise L2ValidationError(
                "W1b",
                f"Rail {r.name!r}: firings_typical_per_period must "
                f"have both endpoints zero or higher (got min={lo}, "
                f"max={hi}). A negative firing count doesn't mean "
                f"anything — set both to 0 or above."
            )
        if r.aggregating:
            raise L2ValidationError(
                "W1c",
                f"Rail {r.name!r}: firings_typical_per_period can't "
                f"sit on an aggregating rail. The cadence already "
                f"says when the sweep fires (once per cadence "
                f"period) — a count band would conflict. Remove "
                f"firings_typical_per_period here."
            )
    for t in inst.transfer_templates:
        ftp = t.firings_typical_per_period
        if ftp is None:
            continue
        lo, hi = ftp.count_range
        if lo > hi:
            raise L2ValidationError(
                "W1a",
                f"Transfer template {t.name!r}: "
                f"firings_typical_per_period min ({lo}) is larger "
                f"than max ({hi}). The lower bound must not exceed "
                f"the upper bound — swap the two, or fix the typo."
            )
        if lo < 0 or hi < 0:
            raise L2ValidationError(
                "W1b",
                f"Transfer template {t.name!r}: "
                f"firings_typical_per_period must have both "
                f"endpoints zero or higher (got min={lo}, max={hi}). "
                f"A negative firing count doesn't mean anything."
            )


def _check_non_aggregating_rail_no_cadence_or_bundles(inst: L2Instance) -> None:
    """S6: non-aggregating Rails MUST NOT declare cadence or bundles_activity."""
    for r in inst.rails:
        if r.aggregating:
            continue
        if r.cadence is not None:
            raise L2ValidationError(
                "S6",
                f"Rail {r.name!r} declares a cadence but isn't "
                f"aggregating. Cadence only governs sweep timing for "
                f"aggregating rails — either remove cadence, or set "
                f"aggregating=true."
            )
        if r.bundles_activity:
            raise L2ValidationError(
                "S6",
                f"Rail {r.name!r} declares bundles_activity but "
                f"isn't aggregating. Only aggregators bundle other "
                f"rails — either remove bundles_activity, or set "
                f"aggregating=true."
            )


# -- Vocabulary (V1-V2) ------------------------------------------------------


def _check_completion_vocabulary(inst: L2Instance) -> None:
    """V1: every TransferTemplate.completion matches a v1 vocabulary literal."""
    for t in inst.transfer_templates:
        if not _completion_is_valid(t.completion):
            raise L2ValidationError(
                "V1",
                f"Transfer template {t.name!r}: completion "
                f"{t.completion!r} isn't a v1 CompletionExpression. "
                f"Use one of: business_day_end, business_day_end+Nd, "
                f"month_end, metadata.<key>."
            )


def _check_cadence_vocabulary(inst: L2Instance) -> None:
    """V2: every aggregating Rail's cadence matches a v1 vocabulary literal."""
    for r in inst.rails:
        if not r.aggregating or r.cadence is None:
            continue
        if not _cadence_is_valid(r.cadence):
            raise L2ValidationError(
                "V2",
                f"Rail {r.name!r}: cadence {r.cadence!r} isn't a v1 "
                f"CadenceExpression. Use one of: intraday-Nh, "
                f"daily-eod, daily-bod, weekly-<mon..sun>, "
                f"monthly-eom, monthly-bom, monthly-<1..31>."
            )


# -- Per-leg Origin resolution (O1) ------------------------------------------


def _check_per_leg_origin_resolution(inst: L2Instance) -> None:
    """O1: every leg of every Rail resolves to an Origin per the SPEC's
    per-leg Origin resolution table.

    1-leg rails: ``origin`` MUST be set.
    2-leg rails: every leg MUST resolve under one of:
      - rail-level ``origin`` alone (covers both legs);
      - both ``source_origin`` AND ``destination_origin`` (per-leg);
      - one per-leg override + rail-level ``origin`` as fallback for
        the unspecified leg.

    The loader (M.1a.2) hard-rejects per-leg overrides on single-leg
    rails — that case never reaches this validator.
    """
    for r in inst.rails:
        if isinstance(r, SingleLegRail):
            if r.origin is None:
                raise L2ValidationError(
                    "O1",
                    f"Single-leg rail {r.name!r} doesn't declare an "
                    f"Origin. Set `origin` on the rail — per-leg "
                    f"Origin overrides only apply to two-leg rails."
                )
            continue
        # Two-leg
        source_resolved = r.source_origin is not None or r.origin is not None
        dest_resolved = r.destination_origin is not None or r.origin is not None
        if not source_resolved or not dest_resolved:
            which = "source" if not source_resolved else "destination"
            raise L2ValidationError(
                "O1",
                f"Two-leg rail {r.name!r}: the {which} leg doesn't "
                f"have an Origin. Either set rail-level `origin` "
                f"(covers both legs), set both `source_origin` AND "
                f"`destination_origin`, or set the missing override "
                f"plus rail-level `origin` as the fallback."
            )


def _check_business_day_offset_not_on_external(inst: L2Instance) -> None:
    """M.4.4.14a — Account / AccountTemplate with ``scope="external"``
    MUST NOT carry a ``business_day_offset``. External accounts have no
    EOD balance row, so the offset has no consumer; declaring one is a
    configuration mistake (typically a copy-paste from an internal
    account). M.4.4.14 (top-level ``role_business_day_offsets`` map)
    was removed under Phase CP — offsets moved per-entity, which is
    what makes this scope check tractable (the map's role-keys couldn't
    distinguish internal vs external roles).
    """
    for a in inst.accounts:
        if a.scope == "external" and a.business_day_offset is not None:
            raise L2ValidationError(
                "M1",
                f"Account {a.id!r} is scope='external' but sets "
                f"business_day_offset={a.business_day_offset!r}. "
                f"External accounts have no end-of-day balance row, "
                f"so the offset has nowhere to land. Either remove "
                f"business_day_offset, or change scope to 'internal'."
            )
    for t in inst.account_templates:
        if t.scope == "external" and t.business_day_offset is not None:
            raise L2ValidationError(
                "M1",
                f"Account template {t.role!r} is scope='external' "
                f"but sets business_day_offset="
                f"{t.business_day_offset!r}. External accounts have "
                f"no end-of-day balance row, so the offset has "
                f"nowhere to land. Either remove business_day_offset, "
                f"or change scope to 'internal'."
            )
