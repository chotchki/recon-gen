"""Unit tests for the promoted `AsOfFrame` (AQ.1).

The AP.0 spike (`tests/unit/test_ap0_as_of_frame.py`) proved the design;
this file is the production-side gate for the type now living in
`src/recon_gen/common/as_of_frame.py`. Tests cover the same three claims
the spike pinned, plus a drift gate against the existing canonical lock
anchor in `cli/data.py` that AQ.3 will eventually funnel.

The spike's renderer-specific derivations (QS `RollingDate`, App2
sentinels) are NOT exercised here — those move to the `View` primitive
in AR.
"""

from __future__ import annotations

from datetime import date, timedelta

from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame
from recon_gen.common.intervals import DateInterval


def test_locked_is_deterministic() -> None:
    a = AsOfFrame.locked(window_days=7)
    b = AsOfFrame.locked(window_days=7)
    # BD.1 — bare ctor takes typed `window: DateInterval` (the v1
    # `window_days: int` field is gone; the kwarg lives only on the
    # named constructors for construction-time ergonomics).
    expected_window = DateInterval.trailing_days_ending_today(
        LOCKED_ANCHOR, 8,  # 7-day window means 8 closed-closed days
    )
    assert a == b == AsOfFrame(as_of=LOCKED_ANCHOR, window=expected_window)
    assert a.as_of == LOCKED_ANCHOR  # not wall-clock


def test_live_ends_at_today_via_same_code_path() -> None:
    live = AsOfFrame.live(window_days=30)
    assert live.as_of == date.today()
    # Same derivations the locked frame uses — only the anchor differs.
    locked = AsOfFrame.locked(window_days=30)
    assert (live.as_of - live.window_start) == (
        locked.as_of - locked.window_start
    )


def test_window_start_derives_from_anchor() -> None:
    frame = AsOfFrame.locked(window_days=7)
    # window_days=7 → 7 days BEFORE the anchor (8-day closed-closed window
    # ending at the anchor). window_start is the left edge.
    assert frame.window_start == date(2029, 12, 25)


def test_contains_is_inclusive_both_ends() -> None:
    frame = AsOfFrame.locked(window_days=7)
    assert frame.contains(frame.as_of)
    assert frame.contains(frame.window_start)
    assert not frame.contains(frame.as_of - timedelta(days=8))
    assert not frame.contains(frame.as_of + timedelta(days=1))


def test_frame_is_frozen() -> None:
    # `frozen=True`: instances can't be mutated after construction. Tests
    # the encoding-in-types invariant: an `as_of` you hold is the one the
    # generator + views agreed on; no later code can flip the anchor under
    # us. Pyright would already catch the assignment statically; this is
    # the runtime witness.
    frame = AsOfFrame.locked(window_days=7)
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.as_of = date(2031, 1, 1)  # type: ignore[misc]: pyright correctly flags assignment to a frozen dataclass; the test asserts the runtime FrozenInstanceError fires, which requires actually attempting the mutation


def test_locked_anchor_value_pinned() -> None:
    # BD.6 retired the `cli/data.py::_CANONICAL_LOCK_ANCHOR` alias (the
    # AQ.3 funnel's transition shim) — there's now ONE constant,
    # `as_of_frame.LOCKED_ANCHOR`, with no parallel name. This test
    # locks the value itself (`date(2030, 1, 1)`) so an inadvertent
    # change to the anchor would break loudly — every locked seed +
    # semantic lock + AsOfFrame.locked() emit depends on it.
    assert LOCKED_ANCHOR == date(2030, 1, 1)


# ---------------------------------------------------------------------------
# AQ.2 — TestGeneratorConfig.as_of_frame() is the call-site every reader
# lands on. Three resolution paths, one shape out.
# ---------------------------------------------------------------------------


def test_config_end_date_equal_to_locked_anchor_resolves_to_locked_frame() -> None:
    from recon_gen.common.config import TestGeneratorConfig
    cfg = TestGeneratorConfig(end_date=LOCKED_ANCHOR)
    frame = cfg.as_of_frame()
    assert frame == AsOfFrame.locked()
    assert frame.as_of == LOCKED_ANCHOR


def test_config_explicit_end_date_resolves_to_anchored_frame() -> None:
    # An operator override or trainer-pinned day that isn't the canonical
    # anchor still produces a deterministic frame at that day.
    from recon_gen.common.config import TestGeneratorConfig
    pinned = date(2026, 5, 22)
    cfg = TestGeneratorConfig(end_date=pinned)
    assert cfg.as_of_frame() == AsOfFrame(
        as_of=pinned, window=DateInterval.single_day(pinned),
    )


def test_config_no_end_date_resolves_to_live_frame() -> None:
    # The default (no end_date in cfg yaml) means "production / ad-hoc" —
    # ends-at-now, same code path. This is the binding the AQ.3 funnel
    # collapses the four ad-hoc date.today() fallbacks onto.
    from recon_gen.common.config import TestGeneratorConfig
    cfg = TestGeneratorConfig()
    frame = cfg.as_of_frame()
    assert frame.as_of == date.today()
    assert frame == AsOfFrame.live()


def test_config_window_days_threads_through() -> None:
    # Callers can ask for a window without the resolver touching anything
    # else. BD.1 — `frame.window` is the typed DateInterval; the
    # `window_days=7` ergonomic kwarg builds an 8-day closed-closed window
    # ending at the anchor.
    from recon_gen.common.config import TestGeneratorConfig
    cfg = TestGeneratorConfig(end_date=LOCKED_ANCHOR)
    frame = cfg.as_of_frame(window_days=7)
    assert frame.window.days == 8  # closed-closed: window_days+1
    assert frame.as_of == LOCKED_ANCHOR
    assert frame.window.end == LOCKED_ANCHOR


# ---------------------------------------------------------------------------
# AQ.4 — the determinism gate. The §8 story end-to-end through the frame:
# locked binding ⇒ byte-identical seed, live binding ⇒ ends-at-now, both
# via THE SAME `build_full_seed_sql` emitter. The frame is the only thing
# that floats.
# ---------------------------------------------------------------------------


def _spec_example_path() -> str:
    from pathlib import Path
    return str(Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml")


def test_locked_binding_emits_byte_identical_seed_twice_via_frame() -> None:
    # Resolve the anchor through the frame (`cfg.as_of_frame().as_of`) and
    # emit twice; the locked binding must give byte-identical output. This
    # is the AP.0 spike's `locked()` determinism claim made end-to-end:
    # locked ⇒ same SQL string across runs, regardless of when run.
    from recon_gen.cli._helpers import build_full_seed_sql
    from recon_gen.common.config import TestGeneratorConfig
    from recon_gen.common.l2 import load_instance
    from tests._test_helpers import make_test_config

    cfg = make_test_config(
        db_table_prefix="spec_example",
        test_generator=TestGeneratorConfig(end_date=LOCKED_ANCHOR),
    )
    instance = load_instance(_spec_example_path())

    anchor = cfg.test_generator.as_of_frame().as_of
    assert anchor == LOCKED_ANCHOR  # locked binding resolved through frame
    a: str = build_full_seed_sql(cfg, instance, anchor=anchor)
    b: str = build_full_seed_sql(cfg, instance, anchor=anchor)
    assert a == b, "locked binding must emit byte-identical SQL across runs"
    assert "2030-01-01" in a, "locked anchor day must appear in the output"


def test_live_binding_emits_seed_ending_at_today_via_frame() -> None:
    # The same emitter under the live binding — only the resolved anchor
    # differs (= today). The emission carries that anchor through; today's
    # ISO date string appears in the output. Same code path as locked.
    from recon_gen.cli._helpers import build_full_seed_sql
    from recon_gen.common.config import TestGeneratorConfig
    from recon_gen.common.l2 import load_instance
    from tests._test_helpers import make_test_config

    cfg = make_test_config(
        db_table_prefix="spec_example",
        test_generator=TestGeneratorConfig(),  # no end_date → live
    )
    instance = load_instance(_spec_example_path())

    anchor = cfg.test_generator.as_of_frame().as_of
    assert anchor == date.today()  # live binding resolved through frame
    sql: str = build_full_seed_sql(cfg, instance, anchor=anchor)
    assert anchor.isoformat() in sql, (
        f"live emission must carry today's anchor through; "
        f"expected {anchor.isoformat()} in output"
    )


# ---------------------------------------------------------------------------
# BD.1 — new constructors + `seed` field + `window: DateInterval` field.
# ---------------------------------------------------------------------------


def test_for_audit_window_excludes_today() -> None:
    """The audit-window convention: today is EXCLUDED (the day hasn't
    closed yet). `for_audit(today, lookback_days=N)` builds
    `[today-N, today-1]`. The chronic v11.10.0 e2e regression existed
    precisely because plants landed at today; this constructor + the
    typed window make that bug unrepresentable at the wiring site."""
    today = date(2026, 5, 24)
    frame = AsOfFrame.for_audit(today, lookback_days=7)
    assert frame.as_of == today
    assert frame.window == DateInterval.trailing_days_ending_yesterday(
        today, 7,
    )
    assert not frame.contains(today)  # today excluded from the window
    assert frame.contains(today - timedelta(days=1))  # yesterday in
    assert frame.contains(today - timedelta(days=7))  # left edge in
    assert not frame.contains(today - timedelta(days=8))  # one-past left


def test_for_test_takes_explicit_window_seed() -> None:
    """Test fixtures' explicit-shape constructor. `seed` is required
    (default 0 — deterministic); `as_of` defaults to `window.end`."""
    window = DateInterval.closed(date(2026, 1, 1), date(2026, 1, 7))
    frame = AsOfFrame.for_test(window=window, seed=42)
    assert frame.window == window
    assert frame.seed == 42
    assert frame.as_of == window.end  # default


def test_for_test_explicit_as_of_overrides_window_end() -> None:
    """`as_of` can be pinned independently of `window.end` — covers
    audit-style frames where today != window.end."""
    window = DateInterval.closed(date(2026, 1, 1), date(2026, 1, 7))
    pinned_as_of = date(2026, 1, 10)
    frame = AsOfFrame.for_test(window=window, seed=1, as_of=pinned_as_of)
    assert frame.as_of == pinned_as_of
    assert frame.window.end == date(2026, 1, 7)  # window unchanged


def test_seed_field_default_is_none_on_live_and_locked() -> None:
    """`live()` and `locked()` default `seed=None` (spine's internal
    default applies). Explicit `seed=` pins it."""
    assert AsOfFrame.live().seed is None
    assert AsOfFrame.locked().seed is None
    assert AsOfFrame.live(seed=42).seed == 42
    assert AsOfFrame.locked(seed=99).seed == 99


def test_window_days_kwarg_zero_produces_single_day_window() -> None:
    """`window_days=0` (the default) → single-day window at the anchor.
    Verifies the ergonomic shortcut on `live()` / `locked()` doesn't
    accidentally produce a zero-width or empty interval."""
    frame = AsOfFrame.locked()  # window_days=0 default
    assert frame.window == DateInterval.single_day(LOCKED_ANCHOR)
    assert frame.window.days == 1


def test_bare_constructor_requires_both_as_of_and_window() -> None:
    """The bare `AsOfFrame(as_of=X, window=Y)` ctor takes the typed
    window directly — no `window_days` field escape hatch (BD.0 D6).
    Wiring sites that want the ergonomic int shortcut go through the
    named constructors."""
    import pytest
    with pytest.raises(TypeError):
        AsOfFrame(as_of=LOCKED_ANCHOR)  # type: ignore[call-arg]: deliberately missing window — test asserts the runtime TypeError fires
    # And the v1 `window_days=N` kwarg is gone from the bare ctor:
    with pytest.raises(TypeError):
        AsOfFrame(as_of=LOCKED_ANCHOR, window_days=7)  # type: ignore[call-arg]: window_days was the v1 field; BD.1 dropped it; test pins the removal


def test_window_days_field_is_gone() -> None:
    """BD.1 D6 — the v1 `window_days: int` field was an escape hatch
    that bypassed the typed `DateInterval`. Removed. Any caller that
    needs the int count reads `frame.window.days` (closed-closed count)."""
    frame = AsOfFrame.locked(window_days=7)
    assert not hasattr(frame, "window_days")
