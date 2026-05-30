"""Unit tests for ``common.handbook.vocabulary`` (BXa.1 trimmed shape).

Covers the single dispatch path post-BXa.1: institution name +
acronym come from the L2 YAML's top-level fields (regex-extracted
from description when absent); investigation_personas pass through
verbatim; fixture_name maps `institution_acronym == "SNB"` →
`"sasquatch_pr"` for the docs-side bundled-fixture discriminator.

Dropped from v1 (BXa.1 nuked the underlying surface):
- ``stakeholders`` / ``merchants`` / ``flavor`` / ``gl_accounts``
  assertions — those HandbookVocabulary fields no longer exist.
- ``StakeholderVocabulary`` / ``MerchantVocabulary`` type checks
  — types deleted.
- ``_SASQUATCH_PERSONA_ACRONYM`` reference — discriminator moved to
  the ``_BUNDLED_FIXTURE_ACRONYM_MAP`` lookup in vocabulary.py.
"""

from __future__ import annotations

from pathlib import Path

from recon_gen.common.handbook import HandbookVocabulary, vocabulary_for
from recon_gen.common.handbook.vocabulary import (
    _extract_institution_name,
    _institution_acronym,
)
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import InvestigationPersona, L2Instance


_FIXTURES = Path(__file__).parent.parent / "l2"


def _load(name: str) -> L2Instance:
    return load_instance(_FIXTURES / f"{name}.yaml")


def _minimal_instance(*, description: str | None = None) -> L2Instance:
    return L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
        description=description,
    )


# -- Single dispatch path ----------------------------------------------------


def test_vocabulary_for_sasquatch_pr_reads_top_level_institution_fields() -> None:
    """BXa.1: institution_name + institution_acronym promoted out of
    persona.institution; vocabulary_for reads them directly."""
    vocab = vocabulary_for(_load("sasquatch_pr"))
    assert isinstance(vocab, HandbookVocabulary)
    # spec_example's neutral regex extracts "Sasquatch National Bank"
    # — the fixture also carries institution_name top-level, so the
    # explicit value wins.
    assert "Sasquatch" in vocab.institution.name
    assert vocab.institution.acronym == "SNB"


def test_vocabulary_for_spec_example_uses_regex_extraction() -> None:
    """spec_example carries no institution_name → regex extracts from
    description; spec_example's description opens with 'Generic
    SPEC-shaped' which doesn't match strict title-case → falls back
    to 'Your Institution' placeholder."""
    vocab = vocabulary_for(_load("spec_example"))  # typing-smell: ignore[no-inline-production-constants]: fixture filename, intentional literal
    assert vocab.institution.name == "Your Institution"
    assert vocab.institution.acronym == "the institution"


def test_vocabulary_for_minimal_instance_with_no_description() -> None:
    """Empty L2 → no description → 'Your Institution' placeholder +
    'the institution' acronym + empty investigation_personas."""
    vocab = vocabulary_for(_minimal_instance())
    assert vocab.institution.name == "Your Institution"
    assert vocab.institution.acronym == "the institution"
    assert vocab.investigation_personas == ()
    # demo scenario has no plants → all None
    assert vocab.demo.drift_account is None
    assert vocab.demo.investigation is None


def test_vocabulary_for_minimal_instance_with_proper_noun_description() -> None:
    """Description with a strict-title-case run → regex matches."""
    vocab = vocabulary_for(_minimal_instance(
        description="Acme Federal Bank's reconciliation ledger.",
    ))
    assert vocab.institution.name == "Acme Federal Bank"
    assert vocab.institution.acronym == "AFB"


# -- investigation_personas pass-through -------------------------------------


def test_investigation_personas_pass_through_from_l2() -> None:
    """BXa.1: the curated narrative actors come from L2 top-level,
    not from a hardcoded production-code table."""
    inst = L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
        investigation_personas=(
            InvestigationPersona(
                name="Test Anchor LLC",
                account_id="cust-test-001",
                role="convergence_anchor",
            ),
        ),
    )
    vocab = vocabulary_for(inst)
    assert len(vocab.investigation_personas) == 1
    assert vocab.investigation_personas[0].name == "Test Anchor LLC"


def test_sasquatch_pr_carries_six_curated_personas() -> None:
    """The Sasquatch fixture migrated its 6 curated personas from the
    hardcoded `_sasquatch_pr_vocabulary` table to the YAML top-level."""
    inst = _load("sasquatch_pr")
    assert len(inst.investigation_personas) == 6
    names = {p.name for p in inst.investigation_personas}
    assert "Juniper Ridge LLC" in names
    assert "Shell Company A" in names


# -- fixture_name discriminator (one remaining hardcoded literal) ------------


def test_fixture_name_maps_snb_acronym_to_sasquatch_pr() -> None:
    """The handbook's ``L1_Invariants.md`` gates on
    ``{% if vocab.fixture_name == "sasquatch_pr" %}`` to render the
    bundled-fixture concrete examples."""
    vocab = vocabulary_for(_load("sasquatch_pr"))
    assert vocab.fixture_name == "sasquatch_pr"


def test_fixture_name_none_for_non_bundled_acronym() -> None:
    """Any L2 not declaring institution_acronym=="SNB" → no fixture
    discriminator → docs render the neutral prose branch."""
    inst = L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
        institution_acronym="ACME",
    )
    vocab = vocabulary_for(inst)
    assert vocab.fixture_name is None


# -- Regex / acronym helper unit tests --------------------------------------


def test_extract_institution_name_finds_proper_noun_run() -> None:
    assert _extract_institution_name(
        "Sasquatch National Bank's treasury ledger.",
    ) == "Sasquatch National Bank"


def test_extract_institution_name_falls_back_when_no_proper_noun() -> None:
    assert _extract_institution_name(
        "Generic SPEC-shaped fixture for testing.",
    ) == "Your Institution"


def test_institution_acronym_first_letters_of_multi_word() -> None:
    assert _institution_acronym("Acme Federal Bank") == "AFB"


def test_institution_acronym_collapses_for_placeholder_or_single_word() -> None:
    assert _institution_acronym("Your Institution") == "the institution"
    assert _institution_acronym("Bank") == "the institution"
