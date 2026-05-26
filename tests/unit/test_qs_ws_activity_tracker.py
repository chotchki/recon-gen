"""AA.A.race.3 — unit tests for ``_QsWsActivityTracker`` snapshot API.

The tracker hooks Playwright's ``page.on("websocket")`` +
``ws.on("framesent")`` callbacks at driver construction. These tests
use a thin mock that captures the wiring so we can drive synthetic
START_VIS / STOP_VIS frames without spinning a browser.

Covers:

- Snapshot at construction (no frames seen → empty pending, zero starts)
- Snapshot after frames captures the current state immutably
- ``new_starts_since`` counts ONLY post-snapshot starts
- ``new_pending_since`` returns cids in flight NOW that weren't at
  snapshot time (handles the case where prior-snapshot cids drain)
- Malformed payloads degrade silently (don't crash)
- The cache-equivalent case: snapshot, no new frames, snapshot's view
  of "new" is empty
"""
from __future__ import annotations

import json
from typing import Callable

from tests.e2e._drivers.qs import WsSnapshot, _QsWsActivityTracker


class _MockWebSocket:
    """Stand-in for Playwright's ``WebSocket`` — captures the framesent
    listener so a test can synthesize frames."""

    def __init__(self) -> None:
        self.framesent_cb: "Callable[[str], None] | None" = None

    def on(self, event: str, cb: "Callable[[str], None]") -> None:
        if event == "framesent":
            self.framesent_cb = cb

    def send(self, payload: str) -> None:
        """Test helper — invokes the framesent callback as if Playwright
        observed the page sending ``payload``."""
        if self.framesent_cb is not None:
            self.framesent_cb(payload)


class _MockPage:
    """Stand-in for Playwright's ``Page`` — captures the websocket
    listener so a test can attach a mock WS."""

    def __init__(self) -> None:
        self.websocket_cb: "Callable[[_MockWebSocket], None] | None" = None

    def on(self, event: str, cb: "Callable[[_MockWebSocket], None]") -> None:
        if event == "websocket":
            self.websocket_cb = cb

    def attach_ws(self) -> _MockWebSocket:
        """Test helper — fires the websocket-opened callback with a
        fresh mock WS; returns the WS for frame synthesis."""
        ws = _MockWebSocket()
        if self.websocket_cb is not None:
            self.websocket_cb(ws)
        return ws


def _start(cid: str) -> str:
    return json.dumps({"type": "START_VIS", "cid": cid, "request": {}})


def _stop(*cids: str) -> str:
    return json.dumps({"type": "STOP_VIS", "cids": list(cids)})


def test_snapshot_at_construction_is_empty() -> None:
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    snap = tracker.snapshot()
    assert snap == WsSnapshot(total_starts=0, pending=frozenset())


def test_snapshot_captures_current_state_after_frames() -> None:
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    ws.send(_start("c1"))
    ws.send(_start("c2"))
    snap = tracker.snapshot()
    assert snap.total_starts == 2
    assert snap.pending == frozenset({"c1", "c2"})


def test_snapshot_is_frozen_against_later_mutation() -> None:
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    ws.send(_start("c1"))
    snap = tracker.snapshot()
    # Tracker keeps mutating — the snapshot does not.
    ws.send(_start("c2"))
    ws.send(_stop("c1"))
    assert snap.total_starts == 1
    assert snap.pending == frozenset({"c1"})
    # Live state has advanced.
    assert tracker.total_starts == 2
    assert tracker.pending_count == 1  # only c2 still pending


def test_new_starts_since_counts_only_post_snapshot() -> None:
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    ws.send(_start("prior-1"))
    ws.send(_start("prior-2"))
    snap = tracker.snapshot()
    # Simulate the action firing new starts.
    ws.send(_start("new-1"))
    ws.send(_start("new-2"))
    ws.send(_start("new-3"))
    assert tracker.new_starts_since(snap) == 3


def test_new_starts_since_zero_when_nothing_fires() -> None:
    """The cache-equivalent case: snapshot, no new frames, settle loop
    sees zero new starts and takes the fast-path return."""
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    ws.send(_start("c1"))
    snap = tracker.snapshot()
    # No new frames fire — the action was a no-op (cache-served).
    assert tracker.new_starts_since(snap) == 0
    assert tracker.new_pending_since(snap) == frozenset()


def test_new_pending_since_excludes_pre_snapshot_cids() -> None:
    """A cid in flight at snapshot time is not "new pending" even if
    it's still in flight now. Only cids that started after the snapshot
    count."""
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    ws.send(_start("prior"))  # still in flight at snapshot
    snap = tracker.snapshot()
    ws.send(_start("new"))
    # prior is in tracker.pending but should NOT be in new_pending.
    assert tracker.new_pending_since(snap) == frozenset({"new"})


def test_new_pending_since_drains_as_stop_arrives() -> None:
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    snap = tracker.snapshot()
    ws.send(_start("a"))
    ws.send(_start("b"))
    ws.send(_start("c"))
    assert tracker.new_pending_since(snap) == frozenset({"a", "b", "c"})
    ws.send(_stop("a", "b"))
    assert tracker.new_pending_since(snap) == frozenset({"c"})
    ws.send(_stop("c"))
    assert tracker.new_pending_since(snap) == frozenset()


def test_pre_snapshot_drain_does_not_corrupt_view() -> None:
    """If a cid that was pending at snapshot time STOPS during the wait,
    it just leaves both the snapshot's pending set and the live pending
    set — no effect on ``new_pending_since``."""
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    ws.send(_start("old"))
    snap = tracker.snapshot()
    # Old cid drains; new cid fires + drains.
    ws.send(_stop("old"))
    ws.send(_start("new"))
    assert tracker.new_pending_since(snap) == frozenset({"new"})
    ws.send(_stop("new"))
    assert tracker.new_pending_since(snap) == frozenset()


def test_malformed_payloads_degrade_silently() -> None:
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    # Each of these should be silently ignored.
    ws.send("not json at all")
    ws.send(b"binary bytes")  # type: ignore[arg-type]: deliberately wrong type to verify malformed payloads degrade silently
    ws.send(json.dumps({"no_type_key": True}))
    ws.send(json.dumps({"type": "UNKNOWN_KIND"}))
    ws.send(json.dumps({"type": "START_VIS"}))  # no cid
    ws.send(json.dumps({"type": "START_VIS", "cid": 42}))  # non-str cid
    ws.send(json.dumps({"type": "STOP_VIS", "cids": "not-a-list"}))
    ws.send(json.dumps({"type": "STOP_VIS", "cids": [42, None]}))
    # Tracker stayed clean.
    assert tracker.total_starts == 0
    assert tracker.pending_count == 0


def test_snapshot_equality_for_use_in_assertions() -> None:
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    snap1 = tracker.snapshot()
    snap2 = tracker.snapshot()
    assert snap1 == snap2  # frozen, structural equality


# AA.A.qs-triage.1 — frame_sink wiring. The sink captures every framesent
# payload (parseable or not) for the per-test ``ws_frames.txt`` artifact.
# Confirms the tracker's parsed counters and the artifact sink stay
# decoupled — a malformed frame still lands in the sink even though it's
# ignored for ``total_starts`` / ``pending`` accounting.


def test_frame_sink_appends_every_text_payload() -> None:
    sink: list[str] = []
    page = _MockPage()
    tracker = _QsWsActivityTracker(page, frame_sink=sink)
    ws = page.attach_ws()
    ws.send(_start("c1"))
    ws.send(_stop("c1"))
    assert len(sink) == 2
    assert sink[0].startswith("[framesent] ")
    assert "START_VIS" in sink[0]
    assert "STOP_VIS" in sink[1]
    # Parsed state unchanged by sink wiring.
    assert tracker.total_starts == 1
    assert tracker.pending_count == 0


def test_frame_sink_captures_malformed_payloads_too() -> None:
    """Sink is the ARTIFACT log — it captures everything for forensic
    replay even if the parser ignores it. The post-pick QS frame might
    have an unexpected shape we want to inspect."""
    sink: list[str] = []
    page = _MockPage()
    tracker = _QsWsActivityTracker(page, frame_sink=sink)
    ws = page.attach_ws()
    ws.send("not json at all")
    ws.send(json.dumps({"type": "UNKNOWN_KIND", "details": 42}))
    ws.send(_start("c1"))
    # Sink captures all 3; tracker counts only the START_VIS.
    assert len(sink) == 3
    assert sink[0] == "[framesent] not json at all"
    assert "UNKNOWN_KIND" in sink[1]
    assert "START_VIS" in sink[2]
    assert tracker.total_starts == 1


def test_frame_sink_marks_binary_frames_without_dumping_bytes() -> None:
    """Binary frames (QS heartbeats etc.) get a length marker rather
    than raw bytes — keeps ws_frames.txt grep-friendly text."""
    sink: list[str] = []
    page = _MockPage()
    tracker = _QsWsActivityTracker(page, frame_sink=sink)
    ws = page.attach_ws()
    ws.send(b"\x00\x01\x02\x03")  # type: ignore[arg-type]: deliberately wrong type to verify binary path
    assert len(sink) == 1
    assert sink[0] == "[framesent-binary len=4]"
    # Parser ignores binary; counters stay zero.
    assert tracker.total_starts == 0
    assert tracker.pending_count == 0


def test_no_sink_means_no_capture_overhead() -> None:
    """Default ``frame_sink=None`` is a zero-cost path — the tracker
    doesn't allocate or buffer anything, matching the pre-qs-triage.1
    behavior for callers that don't need the artifact."""
    page = _MockPage()
    tracker = _QsWsActivityTracker(page)
    ws = page.attach_ws()
    ws.send(_start("c1"))
    ws.send(_stop("c1"))
    # No accessor for the sink; the only verification we can do is
    # that the tracker still works correctly without one.
    assert tracker.total_starts == 1
    assert tracker.pending_count == 0
