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

from recon_gen.cli.data import _CANONICAL_LOCK_ANCHOR
from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame


def test_locked_is_deterministic() -> None:
    a = AsOfFrame.locked(window_days=7)
    b = AsOfFrame.locked(window_days=7)
    assert a == b == AsOfFrame(as_of=LOCKED_ANCHOR, window_days=7)
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


def test_locked_anchor_agrees_with_canonical_lock_anchor() -> None:
    # Drift gate until AQ.3 funnels the two constants. Today
    # `cli/data.py::_CANONICAL_LOCK_ANCHOR` is the source the locked-seed
    # SQL emitter uses; `as_of_frame.LOCKED_ANCHOR` is the new one views +
    # generator will read. Both must name the same day or determinism
    # breaks at the seam. AQ.3 collapses this to a single import.
    assert LOCKED_ANCHOR == _CANONICAL_LOCK_ANCHOR


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
    assert cfg.as_of_frame() == AsOfFrame(as_of=pinned, window_days=0)


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
    # else — span concept stays AR-deferred (D4 in the audit).
    from recon_gen.common.config import TestGeneratorConfig
    cfg = TestGeneratorConfig(end_date=LOCKED_ANCHOR)
    assert cfg.as_of_frame(window_days=7).window_days == 7
    assert cfg.as_of_frame(window_days=7).as_of == LOCKED_ANCHOR
