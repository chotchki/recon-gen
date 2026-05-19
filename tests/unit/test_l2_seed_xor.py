"""AB.3.4: per-firing XOR variant suppression in baseline emission.

The helper ``_xor_suppressed_members`` is the picker the baseline path
consults when iterating a TransferTemplate's leg_rails — for each XOR
group, it returns the *other* members so the caller skips them. Pinned
properties:

- empty groups → empty suppression (every pre-AB.3 template byte-equivalent)
- one group of N → exactly N-1 suppressed (one survives)
- deterministic per (template_name, group_index, firing_id) — independent
  of any seeded RNG state, so ``scope:`` changes that shift the rng don't
  shift XOR picks
- firing_id varies → picks distribute across members (no fixed-bias)
"""

from __future__ import annotations

from decimal import Decimal

from recon_gen.common.l2 import Identifier, TransferTemplate
from recon_gen.common.l2.seed import _xor_suppressed_members


def _tt(
    name: str,
    leg_rails: tuple[str, ...],
    xor_groups: tuple[tuple[str, ...], ...] = (),
) -> TransferTemplate:
    """Build a TransferTemplate fixture with XOR groups for picker tests.

    The picker reads only ``name`` and ``leg_rail_xor_groups``; the
    other fields are placeholders never consulted by
    ``_xor_suppressed_members``.
    """
    return TransferTemplate(
        name=Identifier(name),
        expected_net=Decimal("0"),
        transfer_key=(),
        completion="business_day_end",
        leg_rails=tuple(Identifier(n) for n in leg_rails),
        leg_rail_xor_groups=tuple(
            tuple(Identifier(m) for m in g) for g in xor_groups
        ),
    )


def test_empty_xor_groups_returns_empty_set() -> None:
    tt = _tt("PassThrough", leg_rails=("A", "B"), xor_groups=())
    assert _xor_suppressed_members(tt, firing_id="any-tr-id") == set()


def test_one_group_of_three_suppresses_exactly_two() -> None:
    tt = _tt(
        "MerchantSettlementCycle",
        leg_rails=("Auto", "Standard", "Slow"),
        xor_groups=(("Auto", "Standard", "Slow"),),
    )
    sup = _xor_suppressed_members(tt, firing_id="tr-abc-001")
    assert len(sup) == 2  # exactly one survives per group
    assert sup.issubset({"Auto", "Standard", "Slow"})


def test_two_groups_each_suppress_their_own_non_picked_members() -> None:
    """Multiple XOR groups in one template: each is independent."""
    tt = _tt(
        "MultiGroupTemplate",
        leg_rails=("X1", "X2", "Y1", "Y2", "Y3"),
        xor_groups=(("X1", "X2"), ("Y1", "Y2", "Y3")),
    )
    sup = _xor_suppressed_members(tt, firing_id="tr-multi-1")
    # Group X (2 members) → 1 suppressed; Group Y (3 members) → 2 suppressed.
    assert len(sup) == 3
    assert sup.issubset({"X1", "X2", "Y1", "Y2", "Y3"})
    # Each group is resolved independently — at least one member of each
    # group must survive (i.e. not be in the suppressed set).
    assert {"X1", "X2"} - sup, "group X picked nobody"
    assert {"Y1", "Y2", "Y3"} - sup, "group Y picked nobody"


def test_picker_deterministic_per_firing_id() -> None:
    """Same (template, firing_id) always picks the same member."""
    tt = _tt(
        "MerchantSettlementCycle",
        leg_rails=("Auto", "Standard", "Slow"),
        xor_groups=(("Auto", "Standard", "Slow"),),
    )
    a = _xor_suppressed_members(tt, firing_id="tr-fixed-001")
    b = _xor_suppressed_members(tt, firing_id="tr-fixed-001")
    assert a == b


def test_picker_distributes_across_members_over_many_firings() -> None:
    """Different firing_ids → different picks. Across 300 synthetic
    firing_ids every member of a 3-member group should be picked at
    least once — confirms the crc32 hash isn't constant-biased.
    """
    tt = _tt(
        "MerchantSettlementCycle",
        leg_rails=("Auto", "Standard", "Slow"),
        xor_groups=(("Auto", "Standard", "Slow"),),
    )
    members = {"Auto", "Standard", "Slow"}
    picked_per_firing: set[str] = set()
    for n in range(300):
        sup = _xor_suppressed_members(tt, firing_id=f"tr-base-chain-tmpl-{n:06d}")
        survivor = members - sup
        assert len(survivor) == 1
        picked_per_firing |= survivor
    assert picked_per_firing == members, (
        f"Expected all 3 variants to be picked across 300 firings; "
        f"got {picked_per_firing}"
    )


def test_picker_independent_of_rng_state() -> None:
    """The picker is content-derived (crc32 over a stable string); it
    does NOT consume the seeded RNG. This means scope: changes that
    shift the rng don't ripple into XOR picks — per AB.3.0 lock.

    Documented contract: the picker takes no rng argument; this test
    pins that the signature stays content-only.
    """
    import inspect
    sig = inspect.signature(_xor_suppressed_members)
    param_names = set(sig.parameters)
    assert "rng" not in param_names
    assert param_names == {"template", "firing_id"}


def test_two_member_group_picks_one_each_way() -> None:
    """A 2-member group either suppresses {X1} or {X2}, never both /
    neither. With 300 firing_ids both outcomes appear.
    """
    tt = _tt(
        "BinaryChoice",
        leg_rails=("X1", "X2"),
        xor_groups=(("X1", "X2"),),
    )
    outcomes: set[frozenset[str]] = set()
    for n in range(300):
        sup = _xor_suppressed_members(tt, firing_id=f"tr-binary-{n:06d}")
        assert len(sup) == 1
        assert sup.issubset({"X1", "X2"})
        outcomes.add(frozenset(sup))
    assert outcomes == {frozenset({"X1"}), frozenset({"X2"})}


def test_picker_consumes_template_name_in_key() -> None:
    """Two templates with the SAME group composition but DIFFERENT
    names get independent pick streams — the template_name is mixed
    into the crc32 key so two templates can't accidentally co-pick.

    Not asserting they ALWAYS disagree (a hash collision is possible)
    but across 300 firings the streams diverge at least once.
    """
    tt_a = _tt("TemplateA", leg_rails=("V1", "V2", "V3"),
               xor_groups=(("V1", "V2", "V3"),))
    tt_b = _tt("TemplateB", leg_rails=("V1", "V2", "V3"),
               xor_groups=(("V1", "V2", "V3"),))
    any_diverge = False
    for n in range(300):
        fid = f"tr-cross-{n:06d}"
        if _xor_suppressed_members(tt_a, firing_id=fid) != _xor_suppressed_members(tt_b, firing_id=fid):
            any_diverge = True
            break
    assert any_diverge, (
        "Two templates with the same group composition but different names "
        "should produce different pick streams (template_name is in the key)"
    )


