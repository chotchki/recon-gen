"""BX.14 — domain-flavor validator error messages + [?] side-panel triggers.

The operator-locked rule: every L2ValidationError message uses CPA-readable
banking phrasing in active voice, names the offending entity by its
human-readable identifier (not internal index), and the Studio editor's
rejected-save banner renders a side-panel ``[?]`` trigger pointing at a
per-rule-family glossary entry.

This file pins:

1. **Structured error shape** — every BX.14 catalog rule constructs
   ``L2ValidationError(code, message)``, ``str(exc)`` is
   ``"[<code>] <message>"``, the legacy single-arg form still works.
2. **Domain-flavor wording** — sample rules from each family (U/R/C/S/V/W/O/M)
   carry plain-language phrases (no "MUST", no "SPEC", no all-caps imperatives,
   no engineering jargon like "PostedRequirements" without context).
3. **Glossary anchor resolution** — every code prefix maps to a known
   ``GLOSSARY`` entry; unknown codes return None.
4. **Studio editor banner** — when a rejected save produces a coded
   error, the rendered banner includes the [?] trigger
   ``hx-get="/studio/side-panel/glossary/<family-anchor>"``; without a
   code, no trigger is rendered (legacy fallback).
5. **GLOSSARY presence** — every family anchor exists with non-empty
   prose; format is operator-readable markdown bullet/paragraph.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from recon_gen.common.html._side_panel import GLOSSARY
from recon_gen.common.html._studio_editor_routes import (
    _render_global_error_banner,
)
from recon_gen.common.l2 import (
    Account,
    Identifier,
    L2Instance,
    L2ValidationError,
    LimitSchedule,
    Name,
    RailName,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
    validate,
)
from recon_gen.common.l2.validate import validator_glossary_anchor_for


# -- 1. Structured error shape -----------------------------------------------


def test_l2_validation_error_carries_code_and_message() -> None:
    """``L2ValidationError(code, message)`` is the BX.14 ctor."""
    e = L2ValidationError("R5", "Chain row chains[0] names X as its parent")
    assert e.code == "R5"
    assert e.message == "Chain row chains[0] names X as its parent"
    assert str(e) == "[R5] Chain row chains[0] names X as its parent"


def test_l2_validation_error_legacy_single_arg_still_works() -> None:
    """Pre-BX.14 callers that pass a bare message keep working — code
    defaults to empty + the banner falls back to no [?] trigger."""
    e = L2ValidationError("plain old message")
    assert e.code == ""
    assert e.message == "plain old message"
    assert str(e) == "plain old message"


# -- 2. Domain-flavor wording per family --------------------------------------


def _baseline() -> L2Instance:
    """Minimal valid instance — every family-rule test mutates one field."""
    return L2Instance(
        accounts=(
            Account(
                id=Identifier("gl-1010-cash-due-frb"),
                scope="internal",
                name=Name("Cash Due FRB"),
                role=Identifier("CashDueFRB"),
            ),
            Account(
                id=Identifier("ext-counter"),
                scope="external",
                role=Identifier("ExtCounterparty"),
            ),
        ),
        account_templates=(),
        rails=(
            TwoLegRail(
                name=Identifier("WireOutbound"),
                origin="ExternalForcePosted",
                metadata_keys=(Identifier("imad"),),
                source_role=(Identifier("CashDueFRB"),),
                destination_role=(Identifier("ExtCounterparty"),),
                expected_net=Decimal("0"),
            ),
        ),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def _expect_domain_wording(msg: str, *, banned_phrases: tuple[str, ...]) -> None:
    """Every BX.14 message dodges these engineering-jargon phrases."""
    for phrase in banned_phrases:
        assert phrase not in msg, (
            f"BX.14 wording smell — message still contains "
            f"engineering jargon {phrase!r}: {msg!r}"
        )


def test_u1_message_uses_plain_language() -> None:
    """U1: duplicate account id error names the entity in plain English."""
    inst = _baseline()
    dup = dataclasses.replace(inst.accounts[1], id=inst.accounts[0].id)
    bad = dataclasses.replace(inst, accounts=(inst.accounts[0], dup))
    with pytest.raises(L2ValidationError) as info:
        validate(bad)
    assert info.value.code == "U1"
    msg = info.value.message
    assert "account id" in msg
    assert "more than once" in msg
    _expect_domain_wording(
        msg, banned_phrases=("MUST", "duplicate Account.id", "SPEC"),
    )


def test_r1_message_names_rail_and_role() -> None:
    """R1: undeclared role reference names the rail + role."""
    inst = _baseline()
    bad_rail = dataclasses.replace(
        inst.rails[0],
        source_role=(Identifier("PhantomRole"),),
    )
    bad = dataclasses.replace(inst, rails=(bad_rail,))
    with pytest.raises(L2ValidationError) as info:
        validate(bad)
    assert info.value.code == "R1"
    msg = info.value.message
    assert "'PhantomRole'" in msg or "PhantomRole" in msg
    assert "WireOutbound" in msg
    _expect_domain_wording(msg, banned_phrases=("MUST", "Account or AccountTemplate"))


def test_s3_message_says_nothing_to_reconcile() -> None:
    """S3: unreconciled single-leg rail uses the plain-language phrase."""
    orphan = SingleLegRail(
        name=Identifier("OrphanDDADebit"),
        origin="InternalInitiated",
        metadata_keys=(),
        leg_role=(Identifier("CashDueFRB"),),
        leg_direction="Debit",
    )
    inst = _baseline()
    bad = dataclasses.replace(inst, rails=(*inst.rails, orphan))
    with pytest.raises(L2ValidationError) as info:
        validate(bad)
    assert info.value.code == "S3"
    msg = info.value.message
    assert "OrphanDDADebit" in msg
    assert "nothing to reconcile against" in msg
    # Banking metaphor — "sit on the books forever" — survives the
    # legacy "drift would persist" engineering phrasing.
    assert "books" in msg
    _expect_domain_wording(msg, banned_phrases=("MUST", "TransferTemplate.leg_rails"))


def test_r6_message_uses_active_voice() -> None:
    """R6: limit-schedule on undeclared role uses active-voice phrasing."""
    inst = _baseline()
    bad = dataclasses.replace(
        inst,
        limit_schedules=(
            LimitSchedule(
                parent_role=Identifier("PhantomRole"),
                rail=RailName("WireOutbound"),
                cap=Decimal("100"),
            ),
        ),
    )
    with pytest.raises(L2ValidationError) as info:
        validate(bad)
    assert info.value.code == "R6"
    msg = info.value.message
    assert "limit_schedules[0]" in msg
    # Active voice — "this limit schedule caps role X", not
    # "validation failed for limit_schedules[0].parent_role".
    assert "caps role" in msg
    _expect_domain_wording(
        msg, banned_phrases=("MUST", "Validation failed", "not declared on any"),
    )


def test_v1_message_lists_allowed_vocabulary_without_spec_jargon() -> None:
    """V1: completion-vocabulary error lists the allowed literals
    without referencing SPEC sections."""
    bad_tmpl = TransferTemplate(
        name=Identifier("MerchantSettlement"),
        expected_net=Decimal("0"),
        transfer_key=(),
        leg_rails=(Identifier("WireOutbound"),),
        completion="every-other-tuesday",  # invalid vocabulary
    )
    inst = _baseline()
    # Drop the rail's expected_net so it's a valid template leg under S2
    # (template owns the bundle's expected_net).
    bare_rail = dataclasses.replace(inst.rails[0], expected_net=None)
    bad = dataclasses.replace(
        inst, rails=(bare_rail,), transfer_templates=(bad_tmpl,),
    )
    with pytest.raises(L2ValidationError) as info:
        validate(bad)
    assert info.value.code == "V1"
    msg = info.value.message
    assert "MerchantSettlement" in msg
    assert "isn't a v1 CompletionExpression" in msg
    assert "business_day_end" in msg
    _expect_domain_wording(msg, banned_phrases=("SPEC V1", "literal."))


# -- 3. Glossary anchor resolution --------------------------------------------


@pytest.mark.parametrize(
    "code,expected_anchor",
    [
        ("U1", "validator-uniqueness-rules"),
        ("U7", "validator-uniqueness-rules"),
        ("R5", "validator-reference-rules"),
        ("R12", "validator-reference-rules"),
        ("C8a", "validator-cardinality-rules"),
        ("C1d", "validator-cardinality-rules"),
        ("S3", "validator-state-rules"),
        ("V1", "validator-vocabulary-rules"),
        ("V1c", "validator-vocabulary-rules"),
        ("W1a", "validator-firings-rules"),
        ("O1", "validator-origin-rules"),
        ("M1", "validator-scope-rules"),
    ],
)
def test_validator_glossary_anchor_resolution(
    code: str, expected_anchor: str,
) -> None:
    """Every BX.14 code prefix maps to a known glossary anchor."""
    assert validator_glossary_anchor_for(code) == expected_anchor


def test_validator_glossary_anchor_unknown_returns_none() -> None:
    """Empty code (legacy bare-string error) + unrecognized prefix both
    return None so the banner falls back to no [?] trigger."""
    assert validator_glossary_anchor_for("") is None
    assert validator_glossary_anchor_for("Z9") is None
    assert validator_glossary_anchor_for("X1") is None


# -- 4. Banner rendering ------------------------------------------------------


def test_banner_renders_code_chip_and_help_trigger() -> None:
    """A coded error produces a code-chip + [?] trigger pointing at the
    family glossary anchor."""
    html = _render_global_error_banner(
        "[R5] Chain row chains[0] names 'PhantomChild' as its parent",
    )
    assert 'role="alert"' in html
    assert "R5" in html
    assert "Chain row chains[0]" in html
    # The [?] trigger is rendered as a side-panel-trigger button with
    # the right hx-get URL — anchor maps to validator-reference-rules.
    assert "data-side-panel-trigger" in html
    assert (
        'hx-get="/studio/side-panel/glossary/validator-reference-rules"'
        in html
    )
    assert "[?]" in html


def test_banner_without_code_renders_bare_message() -> None:
    """A legacy bare-string error (no [CODE] prefix) renders without
    the help trigger — operator can still read the message; the
    fallback shape is the pre-BX.14 banner."""
    html = _render_global_error_banner("Field coercion failed: bad input")
    assert 'role="alert"' in html
    assert "Field coercion failed: bad input" in html
    assert "data-side-panel-trigger" not in html


def test_banner_empty_error_renders_nothing() -> None:
    """No error -> no banner. Same as the pre-BX.14 contract."""
    assert _render_global_error_banner(None) == ""
    assert _render_global_error_banner("") == ""


def test_banner_unknown_code_family_renders_chip_without_trigger() -> None:
    """A coded error whose code prefix isn't a known validator family
    (e.g., a typo'd "Z9" or a future rule not yet wired into the
    glossary map) renders the code chip but no [?] trigger. Operator
    sees the code for triage; we don't 404-link them."""
    html = _render_global_error_banner("[Z9] some made-up error")
    assert 'role="alert"' in html
    assert "Z9" in html
    assert "some made-up error" in html
    assert "data-side-panel-trigger" not in html


# -- 5. GLOSSARY entries exist + are operator-readable -----------------------


@pytest.mark.parametrize(
    "anchor",
    [
        "validator-uniqueness-rules",
        "validator-reference-rules",
        "validator-cardinality-rules",
        "validator-state-rules",
        "validator-vocabulary-rules",
        "validator-firings-rules",
        "validator-origin-rules",
        "validator-scope-rules",
    ],
)
def test_validator_glossary_anchor_resolves_in_glossary(anchor: str) -> None:
    """Every family anchor that ``validator_glossary_anchor_for`` can
    return is present in the GLOSSARY dict with non-empty markdown
    prose. Prevents the per-error [?] trigger from 404-ing."""
    assert anchor in GLOSSARY, (
        f"validator family anchor {anchor!r} missing from GLOSSARY — "
        f"the [?] trigger would 404"
    )
    body = GLOSSARY[anchor]
    assert isinstance(body, str)
    assert len(body) > 200, (
        f"validator family entry {anchor!r} is too short to be useful "
        f"(< 200 chars): {body[:60]!r}…"
    )
    # Bold-prefix opener — same shape as other GLOSSARY entries.
    assert body.startswith("**"), (
        f"validator family entry {anchor!r} should start with a "
        f"**bold** name like other glossary entries: {body[:40]!r}…"
    )


def test_validator_glossary_entries_mention_remediation() -> None:
    """Each family entry includes some "fix by …" guidance so the
    operator hits remediation, not just the failure category."""
    for anchor in (
        "validator-uniqueness-rules",
        "validator-reference-rules",
        "validator-cardinality-rules",
        "validator-state-rules",
        "validator-vocabulary-rules",
        "validator-firings-rules",
        "validator-origin-rules",
        "validator-scope-rules",
    ):
        body = GLOSSARY[anchor].lower()
        assert any(
            phrase in body for phrase in ("fix", "remediation", "either")
        ), (
            f"validator family entry {anchor!r} doesn't surface "
            f"remediation guidance — every BX.14 entry should tell the "
            f"operator how to fix the failure"
        )
