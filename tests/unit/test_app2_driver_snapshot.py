"""BV.3.3 snapshot — unit coverage for ``App2Driver.snapshot_take`` /
``snapshot_restore`` / ``snapshot_drop`` verbs.

The verbs are thin httpx POSTs against the Studio test server's
``/training/snapshot/{take,restore,drop}`` routes — so the unit harness
intercepts httpx.Client via a ``MockTransport`` and asserts:

1. The verb hits the right URL path (``/training/snapshot/<verb>``).
2. The ``name`` query parameter carries the supplied value.
3. A non-204 response surfaces as a raised exception (no silent
   continuation against an indeterminate v-overlay state).
4. Repeated take / restore / drop calls are idempotent at the driver
   layer — the driver doesn't carry state between calls, so each is
   independent.

These verbs preserve the "everything through driver" invariant (X.2.q)
for the BV.3.3 per-plant snapshot pattern. Integration coverage (real
Snapshotter, real DB round-trips) lives in tests/e2e/db/ under the
phase 2 per-dialect impls.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests._test_helpers import make_test_config
from tests.e2e._drivers.app2 import App2Driver


# -- shared scaffolding ----------------------------------------------------


_BASE_URL = "http://snapshot-test.invalid:9000"


def _make_driver() -> App2Driver:
    """Construct an ``App2Driver`` with a stub page + base URL.

    The snapshot verbs don't touch ``self._page`` or ``self._sheet_id_by_name``
    — they only read ``self._base`` and fire an httpx POST — so a minimal
    ``object()`` page placeholder + empty sheet map suffice. Keeps the
    test surface narrow on the verb under test (no Playwright fixtures /
    no real server)."""
    return App2Driver(
        base_url=_BASE_URL,
        page=object(),
        cfg=make_test_config(),
        sheet_id_by_name={},
    )


class _RouteRecorder:
    """Captures every httpx request the driver fires for later assertion.

    A bare list of ``(method, url, params)`` tuples — easier to read at
    the assert site than digging through ``httpx.Request`` attributes.
    Also carries a mutable ``status_code`` slot so individual tests can
    flip the canned response (default 204) to assert error-surfacing
    behavior."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, list[str]]]] = []
        # Mutable single-element list (not a bare int) so the
        # ``MockTransport`` handler closure can read the latest value
        # at request time without the test needing to rebuild the
        # fixture between status-code changes.
        self.status_code: list[int] = [204]

    def record(self, request: httpx.Request) -> None:
        # ``request.url.params`` is a ``QueryParams`` (multi-dict);
        # normalize to ``{key: [values]}`` so duplicate-key contracts
        # round-trip clearly in test assertions.
        params: dict[str, list[str]] = {}
        for key in request.url.params.keys():
            params[key] = request.url.params.get_list(key)
        self.calls.append((request.method, str(request.url.path), params))


@pytest.fixture
def httpx_recorder(monkeypatch: pytest.MonkeyPatch) -> Iterator[_RouteRecorder]:
    """Intercept every ``httpx.Client()`` the driver constructs and
    route all requests through a ``MockTransport`` that records the
    call + returns the default 204 response.

    Tests that need a non-2xx response flip ``recorder.status_code[0]``
    (see ``TestSnapshotVerbErrors``)."""
    recorder = _RouteRecorder()

    def _handler(request: httpx.Request) -> httpx.Response:
        recorder.record(request)
        return httpx.Response(recorder.status_code[0])

    real_client = httpx.Client

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _patched_client)
    yield recorder


# -- URL + query-param shape ----------------------------------------------


class TestSnapshotVerbUrls:
    """Each verb posts to the right path with the right ``name`` query
    parameter — the wire contract the Studio snapshot route depends on."""

    def test_take_posts_to_take_route_with_name(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        driver = _make_driver()
        driver.snapshot_take("plant1")
        assert httpx_recorder.calls == [
            ("POST", "/training/snapshot/take", {"name": ["plant1"]}),
        ]

    def test_restore_posts_to_restore_route_with_name(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        driver = _make_driver()
        driver.snapshot_restore("plant1")
        assert httpx_recorder.calls == [
            ("POST", "/training/snapshot/restore", {"name": ["plant1"]}),
        ]

    def test_drop_posts_to_drop_route_with_name(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        driver = _make_driver()
        driver.snapshot_drop("plant1")
        assert httpx_recorder.calls == [
            ("POST", "/training/snapshot/drop", {"name": ["plant1"]}),
        ]

    def test_name_is_url_safe_quoted(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        # httpx auto-quotes special characters in query params — names
        # with spaces / colons (legitimately allowed by the Snapshotter
        # Protocol) must reach the server intact, not as raw URL chars
        # that break parsing.
        driver = _make_driver()
        driver.snapshot_take("plant 1:overlay")
        # ``params`` from httpx is decoded, so we assert against the
        # decoded value — the encoding is httpx's job + an integration
        # test would catch a wire-level break.
        assert httpx_recorder.calls == [
            ("POST", "/training/snapshot/take",
             {"name": ["plant 1:overlay"]}),
        ]


# -- error surfacing ------------------------------------------------------


class TestSnapshotVerbErrors:
    """Non-204 responses must raise — surfacing the snapshotter's error
    message rather than continuing against an indeterminate state."""

    def test_500_raises_http_status_error_on_take(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        driver = _make_driver()
        httpx_recorder.status_code[0] = 500
        with pytest.raises(httpx.HTTPStatusError):
            driver.snapshot_take("plant1")

    def test_500_raises_http_status_error_on_restore(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        driver = _make_driver()
        httpx_recorder.status_code[0] = 500
        with pytest.raises(httpx.HTTPStatusError):
            driver.snapshot_restore("plant1")

    def test_500_raises_http_status_error_on_drop(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        driver = _make_driver()
        httpx_recorder.status_code[0] = 500
        with pytest.raises(httpx.HTTPStatusError):
            driver.snapshot_drop("plant1")

    def test_404_raises_http_status_error(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        # 404 is the Snapshotter's "name doesn't exist" signal for
        # restore / drop — must NOT silently no-op.
        driver = _make_driver()
        httpx_recorder.status_code[0] = 404
        with pytest.raises(httpx.HTTPStatusError):
            driver.snapshot_restore("nonexistent")

    def test_200_does_not_raise(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        # 200 (instead of the canonical 204) still passes ``raise_for_status``
        # — the contract is "non-2xx raises", not "must be exactly 204".
        # The Snapshotter Protocol returns ``None`` on success regardless
        # of which 2xx the server picks.
        driver = _make_driver()
        httpx_recorder.status_code[0] = 200
        driver.snapshot_take("plant1")  # must not raise


# -- idempotency ----------------------------------------------------------


class TestSnapshotVerbIdempotency:
    """The driver layer holds no state between calls — each verb is an
    independent POST. Repeated take / restore / drop calls fire repeated
    requests (the server's idempotency contract is what enforces the
    semantic guarantee; the driver just passes the call through)."""

    def test_repeat_take_fires_independent_requests(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        driver = _make_driver()
        driver.snapshot_take("plant1")
        driver.snapshot_take("plant1")
        assert len(httpx_recorder.calls) == 2
        assert httpx_recorder.calls[0] == httpx_recorder.calls[1]

    def test_repeat_restore_fires_independent_requests(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        driver = _make_driver()
        driver.snapshot_restore("plant1")
        driver.snapshot_restore("plant1")
        driver.snapshot_restore("plant1")
        assert len(httpx_recorder.calls) == 3

    def test_take_restore_drop_sequence(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        # The expected BV.3.3 usage shape — take once, restore between
        # plants, drop at teardown. Asserts the verbs compose cleanly
        # without inter-call coupling at the driver layer.
        driver = _make_driver()
        driver.snapshot_take("snap1")
        driver.snapshot_restore("snap1")
        driver.snapshot_restore("snap1")
        driver.snapshot_drop("snap1")
        assert [call[1] for call in httpx_recorder.calls] == [
            "/training/snapshot/take",
            "/training/snapshot/restore",
            "/training/snapshot/restore",
            "/training/snapshot/drop",
        ]

    def test_distinct_names_round_trip_independently(
        self, httpx_recorder: _RouteRecorder,
    ) -> None:
        # Distinct names target distinct snapshots — the driver passes
        # each through verbatim without any per-driver name registry.
        driver = _make_driver()
        driver.snapshot_take("snap1")
        driver.snapshot_take("snap2")
        names = [call[2]["name"][0] for call in httpx_recorder.calls]
        assert names == ["snap1", "snap2"]
