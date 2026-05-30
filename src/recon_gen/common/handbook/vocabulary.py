"""Handbook substitution vocabulary, built per-render from an L2 instance.

BXa.1 (2026-05-30) collapsed the prior Sasquatch-specific intercept
into a single ``vocabulary_for_l2`` builder. The institution name +
acronym + curated investigation personas now live on the L2 YAML as
top-level ``institution_name`` / ``institution_acronym`` /
``investigation_personas`` fields; the deleted persona block's other
slots (stakeholders, merchants, gl_accounts, flavor) were doubly
dead — bypassed by the intercept AND not substituted in any docs
page — so they went with the intercept.

The neutral fallback (regex-extracted institution name + "Your
Institution" placeholder) survives for L2 YAMLs that omit
``institution_name``. Empty `investigation_personas` is the same
"no curated walkthrough content" signal the old empty-persona path
carried — the handbook's ``{% if vocab.demo.investigation.layering_chain %}``
gates already hide the walkthrough sections when no curated
narrative is available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from recon_gen.common.l2.primitives import (
    InvestigationPersona,
    L2Instance,
)


# -- Sub-shapes -------------------------------------------------------------


@dataclass(frozen=True)
class InstitutionVocabulary:
    """How the handbook refers to the institution."""

    name: str
    """Full name — ``"Sasquatch National Bank"`` / ``"Your Institution"``."""

    acronym: str
    """Short name — ``"SNB"`` / ``"the bank"``."""

    description: str
    """One-paragraph intro for handbook landing pages."""


@dataclass(frozen=True)
class DemoAccount:
    """An account demonstrated by a planted scenario.

    ``id`` is the runtime account id (joins to the seed). ``name`` is a
    human-readable label for prose — falls back to the id when no
    description is available on the L2 account.
    """

    id: str
    name: str


@dataclass(frozen=True)
class InvestigationScenarioVocabulary:
    """The Investigation app's planted scenario, normalized for prose.

    Only populated when the active L2 has at least one
    ``inv_fanout_plants`` entry; otherwise the parent
    ``DemoScenarioVocabulary.investigation`` is ``None`` and the
    Investigation worked-example admonitions don't render.
    """

    anchor: DemoAccount
    """The recipient account the fanout converges on."""

    fanout_sender_count: int
    """Number of distinct senders in the recipient fanout."""

    layering_chain: tuple[DemoAccount, ...] = ()
    """Optional shell-DDA layering chain (Sasquatch's Shell A-B-C); empty for L2s without a hand-curated layering scenario."""

    anomaly_pair_sender: DemoAccount | None = None
    """Optional sender of the volume-anomaly spike (Sasquatch's Cascadia Trust Bank — Operations); None for L2s without one."""


@dataclass(frozen=True)
class DemoScenarioVocabulary:
    """Plant-derived demo content for walkthrough worked examples.

    Each field is ``None`` when the active L2 has no matching plants.
    Walkthrough templates use ``{% if vocab.demo.X %}...{% endif %}``
    to hide their worked-example sections when the field is None.
    """

    drift_account: DemoAccount | None = None
    overdraft_account: DemoAccount | None = None
    limit_breach_account: DemoAccount | None = None
    investigation: InvestigationScenarioVocabulary | None = None

    @property
    def has_investigation_plants(self) -> bool:
        return self.investigation is not None


# -- Top-level ---------------------------------------------------------------


@dataclass(frozen=True)
class HandbookVocabulary:
    """Substitution vocabulary handed to mkdocs-macros at render time.

    BXa.1 (2026-05-30) trimmed ``stakeholders`` / ``merchants`` /
    ``gl_accounts`` / ``flavor`` — never substituted in any rendered
    page; the hardcoded production-code values that backed them
    (``StakeholderVocabulary`` + ``MerchantVocabulary`` tables) went
    with them. If a future custom-prose template needs per-institution
    counterparty / merchant narrative, add a new top-level
    ``L2Instance`` field + extend this dataclass; don't resurrect
    the hardcoded vocab path.
    """

    institution: InstitutionVocabulary
    investigation_personas: tuple[InvestigationPersona, ...]
    fixture_name: str | None = None
    """Bundled fixture name (e.g. ``"sasquatch_pr"``) when the active L2 IS a bundled fixture; ``None`` for integrator-supplied YAMLs. Used by handbook prose that references "the bundled <X> fixture"."""

    demo: DemoScenarioVocabulary = field(default_factory=DemoScenarioVocabulary)
    """Plant-derived demo accounts + Investigation scenario for walkthrough worked examples."""


# -- Public entry point ------------------------------------------------------


# BXa.1: kept as the one remaining hardcoded discriminator in
# production code. The Sasquatch fixture's L2 yaml declares
# ``institution_acronym: SNB``; this maps it to the bundled fixture
# name docs/L1_Invariants.md gates on. If a customer forks the
# fixture + changes the acronym, fixture_name becomes None and the
# docs render the neutral prose branch.
_BUNDLED_FIXTURE_ACRONYM_MAP: dict[str, str] = {
    "SNB": "sasquatch_pr",
}


def vocabulary_for(l2_instance: L2Instance) -> HandbookVocabulary:
    """Return the handbook vocabulary for ``l2_instance``.

    Single dispatch path post-BXa.1. Institution name + acronym +
    investigation-persona curated narrative all come from the L2 YAML
    directly; no special-case branches for bundled fixtures vs operator
    L2s.
    """
    return vocabulary_for_l2(l2_instance)


def _bundled_fixture_name(l2_instance: L2Instance) -> str | None:
    """Return the bundled-fixture name when the L2's acronym maps to one;
    else None.

    The handbook's ``L1_Invariants.md`` gates on
    ``{% if vocab.fixture_name == "sasquatch_pr" %}`` to render
    Sasquatch-specific concrete examples. The map is the one
    remaining production-code literal tying the docs surface to a
    fixture name; a future "drop bundled-fixture-specific docs"
    pass eliminates it entirely.
    """
    if l2_instance.institution_acronym is None:
        return None
    return _BUNDLED_FIXTURE_ACRONYM_MAP.get(l2_instance.institution_acronym)


def _build_demo_scenario(
    l2_instance: L2Instance,
) -> DemoScenarioVocabulary:
    """Derive ``DemoScenarioVocabulary`` from the L2's planted scenarios.

    Reads ``default_scenario_for(l2_instance)`` and pulls the FIRST
    plant of each kind (drift/overdraft/limit_breach/inv_fanout). Each
    plant's ``account_id`` is resolved to a ``DemoAccount`` via the
    L2's own ``investigation_personas`` (when set) for curated display
    names, falling back to the account roster's description, then to
    the raw id.
    """
    # Lazy import — auto_scenario imports a lot, and we don't want
    # vocabulary_for() to drag the seed pipeline in for callers that
    # only need the institution name.
    from recon_gen.common.l2.auto_scenario import default_scenario_for

    persona_lookup = {
        p.account_id: p.name for p in l2_instance.investigation_personas
    }

    def _account_name(account_id: str) -> str:
        """Resolve a runtime account id to a human-readable name."""
        if account_id in persona_lookup:
            return persona_lookup[account_id]
        for acc in l2_instance.accounts:
            if str(acc.id) == account_id:
                return (
                    acc.description.split(".")[0].strip()
                    if acc.description
                    else account_id
                )
        return account_id

    def _to_demo_account(account_id: str) -> DemoAccount:
        return DemoAccount(id=account_id, name=_account_name(account_id))

    report = default_scenario_for(l2_instance)
    plant = report.scenario

    drift = (
        _to_demo_account(str(plant.drift_plants[0].account_id))
        if plant.drift_plants else None
    )
    overdraft = (
        _to_demo_account(str(plant.overdraft_plants[0].account_id))
        if plant.overdraft_plants else None
    )
    limit_breach = (
        _to_demo_account(str(plant.limit_breach_plants[0].account_id))
        if plant.limit_breach_plants else None
    )

    investigation: InvestigationScenarioVocabulary | None = None
    if plant.inv_fanout_plants:
        # Curated personas (when present in the L2) override the raw
        # plant data — the bundled fixture's seed plants a smaller raw
        # fanout than the demo CLI eventually deploys, but the
        # hand-curated narrative (Juniper at 12 senders + Cascadia
        # spike + Shell A-B-C layering chain) is what the walkthroughs
        # are written around. For integrator L2s with no curated
        # personas, fall back to the first plant.
        curated_anchor = next(
            (
                _to_demo_account(p.account_id)
                for p in l2_instance.investigation_personas
                if p.role == "convergence_anchor"
            ),
            None,
        )
        first = plant.inv_fanout_plants[0]
        if curated_anchor is not None:
            anchor = curated_anchor
            fanout_sender_count = 12
        else:
            anchor = _to_demo_account(str(first.recipient_account_id))
            fanout_sender_count = len(first.sender_account_ids)
        chain = tuple(
            _to_demo_account(p.account_id)
            for p in l2_instance.investigation_personas
            if p.role == "shell_entity"
        )
        anomaly_sender = next(
            (
                _to_demo_account(p.account_id)
                for p in l2_instance.investigation_personas
                if p.role == "operations_account"
            ),
            None,
        )
        investigation = InvestigationScenarioVocabulary(
            anchor=anchor,
            fanout_sender_count=fanout_sender_count,
            layering_chain=chain,
            anomaly_pair_sender=anomaly_sender,
        )

    return DemoScenarioVocabulary(
        drift_account=drift,
        overdraft_account=overdraft,
        limit_breach_account=limit_breach,
        investigation=investigation,
    )


# -- Single builder (post-BXa.1) --------------------------------------------


_INSTITUTION_NAME_RE = re.compile(
    # First strict-title-case run (≥2 words) in the description's first
    # sentence — e.g. "Sasquatch National Bank's combined treasury…" →
    # "Sasquatch National Bank". Strict title case (uppercase initial +
    # lowercase remainder) so all-caps tokens don't false-match: in
    # ``spec_example`` the description opens with "Generic SPEC-shaped
    # instance…", and we want that to fall through to the placeholder
    # rather than emit "Generic SPEC". Real bank names with all-caps
    # tokens ("BMO Harris", "PNC Financial") need to set
    # ``institution_name`` on the L2 explicitly — the regex isn't
    # trying to cover the long tail.
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})\b"
)


def vocabulary_for_l2(l2_instance: L2Instance) -> HandbookVocabulary:
    """Build the handbook vocabulary from the L2 instance directly.

    Precedence for the institution name:
    1. ``l2_instance.institution_name`` if set (the BXa.1 promoted field
       that operator L2s declare directly).
    2. Regex-extracted from the L2's description (the neutral fallback
       for L2s that omit ``institution_name``).
    3. ``"Your Institution"`` placeholder (when no description either).

    Same precedence for acronym (explicit field → derived-from-name →
    ``"the institution"`` placeholder).
    """
    description = (
        l2_instance.description.strip()
        if l2_instance.description
        else "An L2-fed institution — handbook generated from the L2 YAML."
    )
    if l2_instance.institution_name is not None:
        name = l2_instance.institution_name
    else:
        name = _extract_institution_name(description)
    if l2_instance.institution_acronym is not None:
        acronym = l2_instance.institution_acronym
    else:
        acronym = _institution_acronym(name)
    return HandbookVocabulary(
        institution=InstitutionVocabulary(
            name=name,
            acronym=acronym,
            description=description,
        ),
        investigation_personas=l2_instance.investigation_personas,
        fixture_name=_bundled_fixture_name(l2_instance),
        demo=_build_demo_scenario(l2_instance),
    )


def _extract_institution_name(description: str) -> str:
    """Pull a proper-noun-shaped institution name out of a description.

    Returns ``"Your Institution"`` when no candidate is found, so the
    handbook reads sensibly for L2 instances whose descriptions are
    test-shaped or otherwise lack a proper-noun run.
    """
    first_sentence = description.split(".", 1)[0]
    match = _INSTITUTION_NAME_RE.search(first_sentence)
    if match is None:
        return "Your Institution"
    return match.group(1)


def _institution_acronym(name: str) -> str:
    """Make a 2-4 letter acronym from a multi-word institution name.

    Single-word names (or the ``"Your Institution"`` fallback) collapse
    to ``"the institution"`` for readable inline prose.
    """
    words = [w for w in name.split() if w[0].isupper()]
    if len(words) < 2 or name == "Your Institution":
        return "the institution"
    return "".join(w[0] for w in words)
