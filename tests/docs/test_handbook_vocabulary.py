"""Unit tests for ``common.handbook.vocabulary``.

Covers all three branches: built-in (``sasquatch_pr``), neutral
fallback derived from a real L2 (``spec_example``), and synthetic
minimal instance (no description, empty fields).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.handbook import (
    HandbookVocabulary,
    InvestigationPersonaVocabulary as InvestigationPersonaVocabulary,
    MerchantVocabulary,
    vocabulary_for,
)
from recon_gen.common.handbook.vocabulary import (
    _extract_institution_name,
    _institution_acronym,
    _SASQUATCH_PERSONA_ACRONYM,
)
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance


_FIXTURES = Path(__file__).parent.parent / "l2"


# -- Helpers -----------------------------------------------------------------


def _load(name: str) -> L2Instance:
    return load_instance(_FIXTURES / f"{name}.yaml")


def _minimal_instance(*, description: str | None = None) -> L2Instance:
    """Empty-tuples-everywhere L2Instance for the synthetic-minimal test.

    Z.C — the legacy ``instance`` field is dropped from L2Instance; the
    deployment identifier lives on cfg now, not the L2.
    """
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
        description=description,
    )


# -- Built-in: sasquatch_pr --------------------------------------------------


class TestSasquatchPRVocabulary:
    def test_picks_snb_branch(self):
        vocab = vocabulary_for(_load("sasquatch_pr"))
        assert vocab.institution.name == "Sasquatch National Bank"
        assert vocab.institution.acronym == _SASQUATCH_PERSONA_ACRONYM

    def test_carries_region_and_legacy_entity(self):
        vocab = vocabulary_for(_load("sasquatch_pr"))
        assert vocab.institution.region == "Pacific Northwest"
        assert vocab.institution.legacy_entity == "Farmers Exchange Bank"

    def test_description_pulled_from_l2(self):
        l2 = _load("sasquatch_pr")
        vocab = vocabulary_for(l2)
        # The description is the L2's description, stripped.
        assert vocab.institution.description.startswith(
            "Sasquatch National Bank's combined treasury"
        )
        assert vocab.institution.description == (
            l2.description.strip() if l2.description else ""
        )

    def test_stakeholders_present(self):
        vocab = vocabulary_for(_load("sasquatch_pr"))
        names = {s.name for s in vocab.stakeholders}
        assert "Federal Reserve Bank" in names
        assert "Payment Gateway Processor" in names

    def test_gl_accounts_pulled_from_snb_persona(self):
        vocab = vocabulary_for(_load("sasquatch_pr"))
        codes = {g.code for g in vocab.gl_accounts}
        # Sample a few codes that SNB_PERSONA carries.
        assert {"gl-1010", "gl-1810", "gl-1815"}.issubset(codes)

    def test_merchants_present_with_account_ids(self):
        vocab = vocabulary_for(_load("sasquatch_pr"))
        assert len(vocab.merchants) >= 5
        for m in vocab.merchants:
            assert isinstance(m, MerchantVocabulary)
            assert m.account_id.startswith("cust-900-")
            assert m.sector  # non-empty

    def test_investigation_personas_cover_demo_actors(self):
        vocab = vocabulary_for(_load("sasquatch_pr"))
        names = {p.name for p in vocab.investigation_personas}
        assert "Juniper Ridge LLC" in names
        assert "Cascadia Trust Bank" in names
        # All three shell entities for the layering chain.
        for letter in ("A", "B", "C"):
            assert f"Shell Company {letter}" in names

    def test_investigation_personas_carry_seed_account_ids(self):
        vocab = vocabulary_for(_load("sasquatch_pr"))
        ids = {p.account_id for p in vocab.investigation_personas}
        assert "cust-900-0007-juniper-ridge-llc" in ids
        assert "ext-cascadia-trust-bank" in ids
        assert "cust-700-0010-shell-company-a" in ids


# -- Neutral fallback: spec_example -----------------------------------------


class TestSpecExampleNeutralFallback:
    def test_picks_neutral_branch(self):
        vocab = vocabulary_for(_load("spec_example"))
        # spec_example's description opens with "Generic SPEC-shaped
        # instance…" — no proper-noun run, so we get the placeholder.
        assert vocab.institution.name == "Your Institution"
        assert vocab.institution.acronym == "the institution"

    def test_no_persona_leakage(self):
        vocab = vocabulary_for(_load("spec_example"))
        # Hard contract — the audit's central O.0 finding is "zero
        # Sasquatch / Bigfoot / SNB / FRB strings in spec_example
        # output". The vocabulary is the substitution surface that has
        # to enforce that.
        for forbidden in ("Sasquatch", "Bigfoot", "SNB", "Federal Reserve"):
            assert forbidden not in vocab.institution.name
            assert forbidden not in vocab.institution.description
            for s in vocab.stakeholders:
                assert forbidden not in s.name
            for m in vocab.merchants:
                assert forbidden not in m.name
            for p in vocab.investigation_personas:
                assert forbidden not in p.name

    def test_neutral_branch_has_empty_persona_tuples(self):
        vocab = vocabulary_for(_load("spec_example"))
        assert vocab.stakeholders == ()
        assert vocab.merchants == ()
        assert vocab.investigation_personas == ()
        assert vocab.flavor == ()


# -- Synthetic minimal: empty-everything L2 ---------------------------------


class TestSyntheticMinimalInstance:
    def test_no_description_uses_default_phrase(self):
        vocab = vocabulary_for(_minimal_instance(description=None))
        assert "L2-fed institution" in vocab.institution.description
        assert vocab.institution.name == "Your Institution"

    def test_proper_noun_description_extracts_name(self):
        vocab = vocabulary_for(
            _minimal_instance(
                description=(
                    "Acme Treasury Bank serves a small community of municipal "
                    "treasurers with internal liquidity sweeps."
                )
            )
        )
        assert vocab.institution.name == "Acme Treasury Bank"
        assert vocab.institution.acronym == "ATB"

    def test_minimal_instance_returns_handbook_vocabulary_type(self):
        vocab = vocabulary_for(_minimal_instance())
        assert isinstance(vocab, HandbookVocabulary)


# -- Helper coverage ---------------------------------------------------------


class TestExtractInstitutionName:
    def test_pulls_first_proper_noun_run(self):
        assert (
            _extract_institution_name("First National Trust serves Acme.")
            == "First National Trust"
        )

    def test_handles_apostrophe_after_name(self):
        # "Sasquatch National Bank's combined treasury…" — the apostrophe
        # ends the proper-noun run cleanly.
        assert (
            _extract_institution_name(
                "Sasquatch National Bank's combined treasury and merchant view."
            )
            == "Sasquatch National Bank"
        )

    def test_no_proper_noun_falls_back_to_placeholder(self):
        assert (
            _extract_institution_name("a generic test instance for cleanliness.")
            == "Your Institution"
        )

    def test_capped_at_five_words(self):
        # Don't grab the entire sentence even when it's all capitalized.
        result = _extract_institution_name(
            "First National Bank Of The Pacific Northwest Region"
        )
        # The regex caps at 5 capitalized tokens (1 + {1,4}).
        assert len(result.split()) <= 5


class TestInstitutionAcronym:
    def test_multi_word_makes_initials(self):
        assert _institution_acronym("First National Bank") == "FNB"
        assert (
            _institution_acronym("Sasquatch National Bank")
            == _SASQUATCH_PERSONA_ACRONYM
        )

    def test_single_word_falls_back_to_phrase(self):
        assert _institution_acronym("Acme") == "the institution"

    def test_placeholder_falls_back_to_phrase(self):
        assert _institution_acronym("Your Institution") == "the institution"


# -- L2 instance dispatcher --------------------------------------------------


class TestVocabularyForDispatch:
    def test_returns_handbook_vocabulary(self):
        for name in ("sasquatch_pr", "spec_example"):
            vocab = vocabulary_for(_load(name))
            assert isinstance(vocab, HandbookVocabulary)

    def test_unknown_instance_uses_neutral_branch(self):
        # An L2 with no SNB-persona block routes to the neutral fallback —
        # zero persona leakage by construction. Z.C — the previous
        # gate sniffed `l2_instance.instance == "sasquatch_pr"`; the
        # new gate checks the persona acronym (`SNB`), so a synthetic
        # L2 with no persona block trivially falls through.
        vocab = vocabulary_for(_minimal_instance())
        assert vocab.stakeholders == ()
        assert vocab.merchants == ()
        assert vocab.investigation_personas == ()


# -- Q.5.a — fixture_name + demo scenario derivation --------------------------


class TestFixtureName:
    def test_sasquatch_persona_yaml_carries_fixture_name(self):
        # Z.C — `_bundled_fixture_name` now keys off the persona block
        # rather than the dropped `l2_instance.instance` db-prefix
        # field. The sasquatch_pr fixture's persona acronym is "SNB",
        # which is the only signal the helper exposes for the
        # bundled-vs-integrator distinction.
        vocab = vocabulary_for(_load("sasquatch_pr"))
        assert vocab.fixture_name == "sasquatch_pr"

    def test_spec_example_has_no_fixture_name(self):
        # Z.C — spec_example has no persona block, so the SNB-acronym
        # gate misses and `fixture_name` reads None. Handbook prose that
        # gates on `{% if vocab.fixture_name %}` simply suppresses the
        # "the bundled X fixture" sentence — matches spec_example's
        # actual unflavored shape.
        vocab = vocabulary_for(_load("spec_example"))
        assert vocab.fixture_name is None

    def test_integrator_fixture_is_unnamed(self):
        # An L2 the project doesn't bundle reads as fixture_name=None,
        # so handbook prose like "the bundled X fixture" can fall back
        # to a generic phrasing via {% if vocab.fixture_name %}.
        vocab = vocabulary_for(_minimal_instance())
        assert vocab.fixture_name is None


class TestDemoScenarioVocabulary:
    def test_bundled_fixtures_have_plant_derived_demo_accounts(self):
        for name in ("sasquatch_pr", "spec_example"):
            vocab = vocabulary_for(_load(name))
            # default_scenario_for plants every L1 invariant kind for
            # both bundled fixtures, so the demo namespace is fully
            # populated. An L2 with no plants would have None here.
            assert vocab.demo.drift_account is not None
            assert vocab.demo.overdraft_account is not None
            assert vocab.demo.limit_breach_account is not None

    def test_account_ids_are_plant_anchored(self):
        # Plants for spec_example use generic ids (cust-NNN); plants for
        # sasquatch_pr use the -snb suffix. This guards against the
        # vocab silently sourcing from the wrong fixture.
        spec = vocabulary_for(_load("spec_example"))
        snb = vocabulary_for(_load("sasquatch_pr"))
        assert spec.demo.drift_account is not None
        assert snb.demo.drift_account is not None
        assert "snb" not in spec.demo.drift_account.id
        assert "snb" in snb.demo.drift_account.id

    def test_investigation_scenario_present_when_inv_fanout_plants(self):
        vocab = vocabulary_for(_load("sasquatch_pr"))
        assert vocab.demo.has_investigation_plants is True
        inv = vocab.demo.investigation
        assert inv is not None
        assert inv.fanout_sender_count > 0

    def test_sasquatch_pr_layering_chain_uses_persona_names(self):
        # Built-in vocab supplies investigation_personas with shell_entity
        # role, so the layering_chain on sasquatch_pr surfaces the
        # curated "Shell Company A/B/C" labels (not raw account_ids).
        vocab = vocabulary_for(_load("sasquatch_pr"))
        assert vocab.demo.investigation is not None
        chain = vocab.demo.investigation.layering_chain
        assert len(chain) == 3
        assert all("Shell Company" in acc.name for acc in chain)

    def test_neutral_fallback_layering_chain_is_empty(self):
        # spec_example has no built-in investigation_personas with
        # shell_entity role, so the layering chain stays empty — the
        # walkthrough's worked-example admonition uses {% if chain %}
        # to skip the chain section in that case.
        vocab = vocabulary_for(_load("spec_example"))
        assert vocab.demo.investigation is not None
        assert vocab.demo.investigation.layering_chain == ()
        assert vocab.demo.investigation.anomaly_pair_sender is None

    def test_l2_without_plants_has_empty_demo(self):
        # An L2 with no scenario.plants at all (synthetic minimal
        # instance) renders demo.* = None across the board — walkthrough
        # templates' {% if vocab.demo.X %} guards skip the worked
        # example sections cleanly.
        # Use the existing minimal-instance helper which has no rails /
        # accounts, so default_scenario_for returns no plants.
        from recon_gen.common.l2.auto_scenario import default_scenario_for
        l2 = _minimal_instance()
        plant = default_scenario_for(l2).scenario
        assume_no_plants = (
            not plant.drift_plants
            and not plant.overdraft_plants
            and not plant.limit_breach_plants
            and not plant.inv_fanout_plants
        )
        if not assume_no_plants:
            pytest.skip(
                "Minimal-instance fixture grew plants — test no longer "
                "exercises the empty-demo branch; pick a different L2.",
            )
        vocab = vocabulary_for(l2)
        assert vocab.demo.drift_account is None
        assert vocab.demo.overdraft_account is None
        assert vocab.demo.limit_breach_account is None
        assert vocab.demo.investigation is None
        assert vocab.demo.has_investigation_plants is False
