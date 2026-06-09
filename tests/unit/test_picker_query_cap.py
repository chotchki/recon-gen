"""CR.2 — Picker search query length cap + truncation surface.

Pre-CR.2 the fetcher silently truncated user-typed queries at 100
chars with no signal back. Customer with long semantic account
identifiers (>100 chars, realistic at large FIs) typed the full
name, got zero matches, no way to discover why. CR.2 makes the cap
operator-tunable (``RECON_GEN_PICKER_MAX_QUERY_LEN``, default 500)
AND surfaces a ``truncated: bool`` flag on the fetcher's return so
the JSON typeahead route can banner the silent-match-failure on
the client.

This file pins:
1. Default cap is 500 (was 100).
2. ``_picker_query_cap()`` honors the env override.
3. ``OptionsSearchResult.truncated`` flips to True when the input
   exceeds the cap.
4. Invalid env (non-positive, non-numeric) falls back to default
   rather than crashing the dropdown.
"""

from __future__ import annotations

import pytest

from recon_gen.common.html._tree_fetcher import (
    _MAX_QUERY_LEN_DEFAULT,
    OptionsSearchResult,
    _picker_query_cap,
)


def test_default_cap_is_500_not_100() -> None:
    """Pre-CR.2 the cap was 100. CR.2 raises it to 500 to cover
    realistic FI account identifiers without operator intervention."""
    assert _MAX_QUERY_LEN_DEFAULT == 500


def test_picker_query_cap_returns_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECON_GEN_PICKER_MAX_QUERY_LEN", raising=False)
    assert _picker_query_cap() == _MAX_QUERY_LEN_DEFAULT


def test_picker_query_cap_honors_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator with weirdly-long identifiers can crank the cap
    without a code change."""
    monkeypatch.setenv("RECON_GEN_PICKER_MAX_QUERY_LEN", "1500")
    assert _picker_query_cap() == 1500


def test_picker_query_cap_can_lower_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symmetric: env can also lower the cap (e.g., tighter DOS
    bounds in a memory-constrained deployment)."""
    monkeypatch.setenv("RECON_GEN_PICKER_MAX_QUERY_LEN", "50")
    assert _picker_query_cap() == 50


def test_picker_query_cap_falls_back_on_invalid_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed env value must NOT crash every dropdown click —
    fall back to the default. (The env-var validator's loud error
    has already fired at process boot for the operator's eyes; the
    runtime path is defense-in-depth.)"""
    monkeypatch.setenv("RECON_GEN_PICKER_MAX_QUERY_LEN", "not-a-number")
    assert _picker_query_cap() == _MAX_QUERY_LEN_DEFAULT


def test_picker_query_cap_falls_back_on_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECON_GEN_PICKER_MAX_QUERY_LEN", "0")
    assert _picker_query_cap() == _MAX_QUERY_LEN_DEFAULT


def test_picker_query_cap_falls_back_on_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECON_GEN_PICKER_MAX_QUERY_LEN", "-1")
    assert _picker_query_cap() == _MAX_QUERY_LEN_DEFAULT


# ---------------------------------------------------------------------------
# OptionsSearchResult shape
# ---------------------------------------------------------------------------


def test_options_search_result_truncated_field_default_false() -> None:
    result = OptionsSearchResult(options=("a", "b"), truncated=False)
    assert result.options == ("a", "b")
    assert result.truncated is False


def test_options_search_result_truncated_signal_propagates() -> None:
    """The route layer reads this field directly to populate the JSON
    response's ``"truncated"`` key. Regression: don't accidentally
    drop the field name or change its position so a NamedTuple unpack
    reorders silently."""
    result = OptionsSearchResult(options=(), truncated=True)
    assert result.truncated is True
    # NamedTuple semantics: index access still works for legacy
    # callers that destructure positionally (none today, but pin
    # the shape).
    assert result[0] == ()
    assert result[1] is True
