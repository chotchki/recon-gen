"""Handbook substitution vocabulary, built per-render from an L2 instance.

The Phase O unified mkdocs site renders against an L2 institution
YAML. ``vocabulary_for(l2_instance)`` picks the right
``HandbookVocabulary`` for that instance — either a built-in vocabulary
(currently only ``sasquatch_pr`` ships one) or a neutral fallback
derived from the L2's own structural data.

The neutral fallback exists so an integrator pointing at their own
L2 instance gets a sensible handbook out of the box: the institution
name comes from the L2's description; account labels come from the
account roster; stakeholders come from the external accounts; no
Sasquatch flavor leaks. As integrators want richer per-institution
flavor (named compliance scenarios, regional voice, legacy entities),
they can submit a built-in vocabulary the same way ``sasquatch_pr``
ships one — or a future ``personas:`` YAML block (audit §5) can carry
the data inline on the L2 itself.

Q.5.a additions: ``vocab.fixture_name`` carries the active fixture
name for "the bundled X fixture" sentences in the handbook; ``vocab.demo``
carries plant-derived demo accounts + Investigation scenario data so
walkthrough templates can render worked examples bound to the active
L2's planted scenarios. When the active L2 has no plants of a given
kind, the corresponding ``vocab.demo.*`` field is ``None`` and the
template's ``{% if vocab.demo.X %}`` guard hides the worked example.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from quicksight_gen.common.l2.primitives import L2Instance
from quicksight_gen.common.persona import DemoPersona, GLAccount


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

    region: str | None = None
    """Optional geographic flavor — ``"Pacific Northwest"``."""

    legacy_entity: str | None = None
    """Optional absorbed-institution name — ``"Farmers Exchange Bank"``."""


@dataclass(frozen=True)
class StakeholderVocabulary:
    """A counterparty / external entity referenced in the handbook prose."""

    name: str
    """Full name — ``"Federal Reserve Bank"``."""

    short_name: str
    """How prose abbreviates it — ``"the Fed"``."""

    role: str
    """One-line role — ``"settlement authority"``."""


@dataclass(frozen=True)
class MerchantVocabulary:
    """A merchant / commercial customer referenced by name in scenarios."""

    name: str
    """Display name — ``"Big Meadow Dairy"``."""

    account_id: str
    """Joins to the seed — ``"cust-900-0001-big-meadow-dairy"``."""

    sector: str
    """One-word industry hint — ``"agricultural"`` / ``"coffee retail"``."""


@dataclass(frozen=True)
class InvestigationPersonaVocabulary:
    """Compliance / AML scenario actor — Investigation app uses these."""

    name: str
    """Display name — ``"Juniper Ridge LLC"``."""

    account_id: str
    """Joins to the seed — ``"cust-900-0007-juniper-ridge-llc"``."""

    role: str
    """Scenario role — ``"convergence_anchor"`` / ``"shell_entity"``."""


# -- Demo scenario vocabulary (Q.5.a) ----------------------------------------


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
    """Substitution vocabulary handed to mkdocs-macros at render time."""

    institution: InstitutionVocabulary
    stakeholders: tuple[StakeholderVocabulary, ...]
    gl_accounts: tuple[GLAccount, ...]
    merchants: tuple[MerchantVocabulary, ...]
    flavor: tuple[str, ...]
    investigation_personas: tuple[InvestigationPersonaVocabulary, ...]
    fixture_name: str | None = None
    """Bundled fixture name (e.g. ``"sasquatch_pr"``) when the active L2 IS a bundled fixture; ``None`` for integrator-supplied YAMLs. Used by handbook prose that references "the bundled <X> fixture"."""

    demo: DemoScenarioVocabulary = field(default_factory=DemoScenarioVocabulary)
    """Plant-derived demo accounts + Investigation scenario for walkthrough worked examples."""


# -- Public entry point ------------------------------------------------------


_SASQUATCH_PERSONA_ACRONYM = "SNB"


def vocabulary_for(l2_instance: L2Instance) -> HandbookVocabulary:
    """Return the handbook vocabulary appropriate for ``l2_instance``.

    Built-in vocabularies take precedence (currently the Sasquatch
    flavor — gated on the L2's persona acronym so any L2 yaml that
    declares the SNB persona block routes here, not on db-prefix
    sniffing). Anything else falls back to a neutral vocabulary
    derived from the L2 instance's own fields — institution name
    from the description, GL accounts + merchants from the account
    roster, no flavor leaks.
    """
    if _has_sasquatch_persona(l2_instance):
        return _sasquatch_pr_vocabulary(l2_instance)
    return _neutral_vocabulary_for(l2_instance)


def _has_sasquatch_persona(l2_instance: L2Instance) -> bool:
    """True iff the L2 yaml's persona block identifies as the SNB flavor.

    Z.C — replaces the prior db-prefix sniff (``l2_instance.instance ==
    "sasquatch_pr"``) with a persona-aware check. The L2 yaml's
    ``persona.institution`` tuple is ``(name, acronym, ...)``; we
    discriminate on the acronym slot because that's the stable
    operator-chosen string a curated vocabulary keys off.
    """
    persona = l2_instance.persona
    if persona is None or len(persona.institution) < 2:
        return False
    return persona.institution[1] == _SASQUATCH_PERSONA_ACRONYM


def _bundled_fixture_name(l2_instance: L2Instance) -> str | None:
    """Return the bundled-fixture name when the L2 looks like a bundled
    fixture; else None.

    Z.C — the prior implementation read ``l2_instance.instance`` (the
    db-table prefix) and matched against a hardcoded fixture-name set.
    With ``instance`` gone, the only remaining persona-derived signal
    for "is this the bundled Sasquatch fixture" is the persona acronym
    itself. ``spec_example`` (the other bundled fixture) carries no
    persona block, so it returns None — handbook prose that gates on
    ``vocab.fixture_name`` simply suppresses its "the bundled X
    fixture" sentence for spec_example, matching its actual unflavored
    shape.
    """
    if _has_sasquatch_persona(l2_instance):
        return "sasquatch_pr"
    return None


def _build_demo_scenario(
    l2_instance: L2Instance,
    *,
    investigation_personas: tuple[InvestigationPersonaVocabulary, ...] = (),
) -> DemoScenarioVocabulary:
    """Derive ``DemoScenarioVocabulary`` from the L2's planted scenarios.

    Reads ``default_scenario_for(l2_instance)`` and pulls the FIRST
    plant of each kind (drift/overdraft/limit_breach/inv_fanout). Each
    plant's ``account_id`` is resolved to a ``DemoAccount`` via the L2's
    own account roster (description → name); falls back to the raw id
    when no roster entry matches.

    ``investigation_personas`` lets a built-in vocabulary supply
    curated display names (e.g. ``Juniper Ridge LLC``) instead of the
    raw ``cust-900-0007-juniper-ridge-llc`` id; the lookup is keyed on
    account_id.
    """
    # Lazy import — auto_scenario imports a lot, and we don't want
    # vocabulary_for() to drag the seed pipeline in for callers that
    # only need the institution name.
    from quicksight_gen.common.l2.auto_scenario import default_scenario_for

    persona_lookup = {p.account_id: p.name for p in investigation_personas}

    def _account_name(account_id: str) -> str:
        """Resolve a runtime account id to a human-readable name."""
        if account_id in persona_lookup:
            return persona_lookup[account_id]
        for acc in l2_instance.accounts:
            if str(acc.id) == account_id:
                return acc.description.split(".")[0].strip() if acc.description else account_id
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
        # Curated personas (when present) override the raw plant
        # data — the bundled fixture's seed plants a smaller raw
        # fanout than the demo CLI eventually deploys, but the
        # hand-curated narrative (Juniper at 12 senders + Cascadia
        # spike + Shell A-B-C layering chain) is what the
        # walkthroughs are written around. For integrator L2s with
        # no curated personas, fall back to the first plant.
        curated_anchor = next(
            (
                _to_demo_account(p.account_id)
                for p in investigation_personas
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
            for p in investigation_personas
            if p.role == "shell_entity"
        )
        anomaly_sender = next(
            (
                _to_demo_account(p.account_id)
                for p in investigation_personas
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


# -- Built-in vocabularies ---------------------------------------------------


def _sasquatch_pr_vocabulary(l2_instance: L2Instance) -> HandbookVocabulary:
    """The Sasquatch National Bank handbook flavor.

    Reads ``l2_instance.persona`` for the strings the YAML now carries
    (institution name, GL accounts, merchant names, flavor terms) and
    layers Investigation personas on top — the latter aren't in the
    YAML's ``persona:`` block because they're handbook-render-only
    metadata (display names + scenario roles for the compliance demo
    walkthroughs), not anything the L2 model itself uses.
    """
    persona = l2_instance.persona or DemoPersona()
    description = (
        l2_instance.description.strip()
        if l2_instance.description
        else "Sasquatch National Bank — combined treasury + merchant-acquiring."
    )
    investigation_personas = (
        InvestigationPersonaVocabulary(
            name="Juniper Ridge LLC",
            account_id="cust-900-0007-juniper-ridge-llc",
            role="convergence_anchor",
        ),
        InvestigationPersonaVocabulary(
            name="Cascadia Trust Bank",
            account_id="ext-cascadia-trust-bank",
            role="counterparty_bank",
        ),
        InvestigationPersonaVocabulary(
            name="Cascadia Trust Bank — Operations",
            account_id="ext-cascadia-trust-bank-sub-ops",
            role="operations_account",
        ),
        InvestigationPersonaVocabulary(
            name="Shell Company A",
            account_id="cust-700-0010-shell-company-a",
            role="shell_entity",
        ),
        InvestigationPersonaVocabulary(
            name="Shell Company B",
            account_id="cust-700-0011-shell-company-b",
            role="shell_entity",
        ),
        InvestigationPersonaVocabulary(
            name="Shell Company C",
            account_id="cust-700-0012-shell-company-c",
            role="shell_entity",
        ),
    )
    return HandbookVocabulary(
        institution=InstitutionVocabulary(
            name=persona.institution[0],
            acronym=persona.institution[1],
            description=description,
            region=persona.flavor[1] if len(persona.flavor) > 1 else None,
            legacy_entity=(
                persona.flavor[2] if len(persona.flavor) > 2 else None
            ),
        ),
        stakeholders=(
            StakeholderVocabulary(
                name="Federal Reserve Bank",
                short_name="the Fed",
                role="settlement authority for ACH, wire, and daily sweep flows",
            ),
            StakeholderVocabulary(
                name="Payment Gateway Processor",
                short_name="the processor",
                role="card-network acquirer and merchant settlement counterparty",
            ),
        ),
        gl_accounts=persona.gl_accounts,
        merchants=(
            MerchantVocabulary(
                name="Big Meadow Dairy",
                account_id="cust-900-0001-big-meadow-dairy",
                sector="agricultural",
            ),
            MerchantVocabulary(
                name="Bigfoot Brews",
                account_id="cust-900-0002-bigfoot-brews",
                sector="coffee retail",
            ),
            MerchantVocabulary(
                name="Cascade Timber Mill",
                account_id="cust-900-0003-cascade-timber-mill",
                sector="industrial",
            ),
            MerchantVocabulary(
                name="Pinecrest Vineyards LLC",
                account_id="cust-900-0004-pinecrest-vineyards",
                sector="agricultural",
            ),
            MerchantVocabulary(
                name="Harvest Moon Bakery",
                account_id="cust-900-0005-harvest-moon-bakery",
                sector="food retail",
            ),
        ),
        flavor=persona.flavor,
        investigation_personas=investigation_personas,
        fixture_name=_bundled_fixture_name(l2_instance),
        demo=_build_demo_scenario(
            l2_instance, investigation_personas=investigation_personas,
        ),
    )


# -- Neutral fallback --------------------------------------------------------


_INSTITUTION_NAME_RE = re.compile(
    # First strict-title-case run (≥2 words) in the description's first
    # sentence — e.g. "Sasquatch National Bank's combined treasury…" →
    # "Sasquatch National Bank". Strict title case (uppercase initial +
    # lowercase remainder) so all-caps tokens don't false-match: in
    # ``spec_example`` the description opens with "Generic SPEC-shaped
    # instance…", and we want that to fall through to the placeholder
    # rather than emit "Generic SPEC". Real bank names with all-caps
    # tokens ("BMO Harris", "PNC Financial") need a built-in vocabulary
    # or a future ``personas:`` YAML block — the regex isn't trying to
    # cover the long tail.
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})\b"
)


def _neutral_vocabulary_for(l2_instance: L2Instance) -> HandbookVocabulary:
    """Derive a neutral vocabulary from the L2 instance's own data.

    No persona flavor — pulls institution name from the description's
    first proper-noun run (or "Your Institution" if none), uses
    ``Identifier``-shaped placeholders for stakeholders, merchants, and
    Investigation personas, and lets ``flavor`` stay empty.
    """
    description = (
        l2_instance.description.strip()
        if l2_instance.description
        else "An L2-fed institution — handbook generated from the L2 YAML."
    )
    inst_name = _extract_institution_name(description)
    inst_acronym = _institution_acronym(inst_name)
    return HandbookVocabulary(
        institution=InstitutionVocabulary(
            name=inst_name,
            acronym=inst_acronym,
            description=description,
            region=None,
            legacy_entity=None,
        ),
        stakeholders=(),
        gl_accounts=(),
        merchants=(),
        flavor=(),
        investigation_personas=(),
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
