"""Unit coverage for ``QsEmbedDriver.typeahead_filter`` — bucket D of
``docs/audits/qs_browser_skip_triage_2026_06_11.md``.

Pre-this-change the non-empty-query branch raised
``NotImplementedError`` — ``skips_if_unsupported`` converted it to a
pytest skip on the QS half of any ``[qs, app2]``-parametrized test
that drove the typed-narrowing flow (~4 skips per the triage).

Post-this-change the verb delegates to the new
``narrow_dropdown_options_by_query`` helper. These tests pin both
contract halves:

1. Empty query short-circuits via ``filter_options`` (unchanged from
   pre-fix behavior).
2. Non-empty query routes through the helper — no
   ``NotImplementedError`` reaches the caller.

The browser-driving guts (popover open + autocomplete typing) live
inside the helper and are covered by the qs_browser tier
(``test_cq_picker_search_and_find.py``); these unit tests pin the
dispatch only.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e._drivers.qs import QsEmbedDriver


def _fake_filter_options_seed(label: str) -> list[str]:  # noqa: ARG001 — sentinel callable; signature mirrors the real verb
    return ["seed"]


def _fake_helper_narrowed_a(
    page: Any, label: str, query: str, timeout_ms: int,  # noqa: ARG001 — sentinel callable; mirrors helper signature
) -> list[str]:
    return ["narrowed-a"]


def _fake_helper_cab(
    page: Any, label: str, query: str, timeout_ms: int,  # noqa: ARG001 — sentinel callable; mirrors helper signature
) -> list[str]:
    return ["c", "a", "b"]


def _fake_helper_empty_list(
    page: Any, label: str, query: str, timeout_ms: int,  # noqa: ARG001 — sentinel callable; mirrors helper signature
) -> list[str]:
    return []


def _bare_driver(visual_timeout_ms: int = 15_000) -> QsEmbedDriver:
    """Construct a ``QsEmbedDriver`` without firing ``__init__``.

    Mirrors ``test_cy_metadata_popup_app2.py``'s pattern: the
    dispatch under test reads ``self._page`` and ``self._visual_timeout``
    only — no live browser or QS embed needed.
    """
    driver = QsEmbedDriver.__new__(QsEmbedDriver)
    # ``self._page`` / ``self._visual_timeout`` are the only attrs the
    # typeahead_filter dispatch reads. Stamp sentinels so we can
    # assert the helper receives them verbatim.
    object.__setattr__(driver, "_page", object())
    object.__setattr__(driver, "_visual_timeout", visual_timeout_ms)
    return driver


class TestTypeaheadFilterEmptyQuery:
    """Empty query routes to ``filter_options`` — the historical seed-
    page path. Same contract as pre-bucket-D fix."""

    def test_empty_string_calls_filter_options(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        driver = _bare_driver()
        captured: list[str] = []

        def fake_filter_options(label: str) -> list[str]:
            captured.append(label)
            return ["opt-a", "opt-b"]

        monkeypatch.setattr(driver, "filter_options", fake_filter_options)
        out = driver.typeahead_filter("Rail", "")
        assert out == ["opt-a", "opt-b"]
        assert captured == ["Rail"], (
            "empty query should short-circuit to filter_options"
        )

    def test_empty_string_does_not_call_helper(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty query must not reach ``narrow_dropdown_options_by_query``
        — the seed-page read is the entire contract for empty."""
        driver = _bare_driver()
        helper_calls: list[tuple[Any, str, str, int]] = []

        def fake_helper(
            page: Any, label: str, query: str, timeout_ms: int,
        ) -> list[str]:
            helper_calls.append((page, label, query, timeout_ms))
            return []

        monkeypatch.setattr(driver, "filter_options", _fake_filter_options_seed)
        monkeypatch.setattr(
            "tests.e2e._drivers.qs.narrow_dropdown_options_by_query",
            fake_helper,
        )
        driver.typeahead_filter("Rail", "")
        assert helper_calls == [], (
            "empty query must not reach the popover-search helper"
        )


class TestTypeaheadFilterNonEmptyQuery:
    """Non-empty query routes to the new helper. Pre-fix this raised
    ``NotImplementedError`` — bucket D's ~4 skips trace to that
    raise."""

    def test_non_empty_does_not_raise_not_implemented(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        driver = _bare_driver()
        monkeypatch.setattr(
            "tests.e2e._drivers.qs.narrow_dropdown_options_by_query",
            _fake_helper_narrowed_a,
        )
        # No NotImplementedError. Regression signal: bucket D's
        # skip-driver re-emerges if this test starts failing with
        # NotImplementedError again.
        assert driver.typeahead_filter("Account", "cust-0001") == [
            "narrowed-a",
        ]

    def test_non_empty_delegates_to_helper_with_args(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        driver = _bare_driver(visual_timeout_ms=9_999)
        helper_calls: list[tuple[Any, str, str, int]] = []

        def fake_helper(
            page: Any, label: str, query: str, timeout_ms: int,
        ) -> list[str]:
            helper_calls.append((page, label, query, timeout_ms))
            return ["opt-1", "opt-2"]

        monkeypatch.setattr(
            "tests.e2e._drivers.qs.narrow_dropdown_options_by_query",
            fake_helper,
        )
        out = driver.typeahead_filter("Account", "cust-0001")
        assert out == ["opt-1", "opt-2"]
        assert len(helper_calls) == 1
        (got_page, got_label, got_query, got_timeout) = helper_calls[0]
        # All four args reach the helper verbatim (page identity + the
        # caller's label/query + the driver's visual timeout).
        assert got_page is driver._page  # pyright: ignore[reportPrivateUsage]: unit test asserts on the dispatch's helper-arg passthrough
        assert got_label == "Account"
        assert got_query == "cust-0001"
        assert got_timeout == 9_999

    def test_non_empty_returns_helper_output_verbatim(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The dispatch is a pure pass-through — no filtering / sorting /
        re-shaping. (Helper handles the sentinel-strip; driver just
        forwards.)"""
        driver = _bare_driver()
        monkeypatch.setattr(
            "tests.e2e._drivers.qs.narrow_dropdown_options_by_query",
            _fake_helper_cab,
        )
        # Order preserved (no implicit sort).
        assert driver.typeahead_filter("X", "q") == ["c", "a", "b"]

    def test_non_empty_with_empty_helper_result(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A typed query that matches zero rows surfaces as ``[]`` —
        the caller asserts on that empty set if that's the test's
        contract (e.g. "this picker has no matches for 'zzz'")."""
        driver = _bare_driver()
        monkeypatch.setattr(
            "tests.e2e._drivers.qs.narrow_dropdown_options_by_query",
            _fake_helper_empty_list,
        )
        assert driver.typeahead_filter("X", "zzz") == []
