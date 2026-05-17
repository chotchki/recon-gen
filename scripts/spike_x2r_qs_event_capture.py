"""X.2.r spike — capture network + WebSocket traffic during one filter
pick on a deployed QS dashboard.

Goal: identify the smallest stable Playwright primitive that fires once
per dataset re-query, so QsEmbedDriver._settle_after_param_change can
drop its 1.2s+700ms-poll heuristic in favor of `page.expect_response`
(or `WebSocket.expect_event`).

Usage:
    AWS_PROFILE=recon-gen-local \\
    RECON_E2E_USER_ARN="arn:aws:quicksight:us-east-1:470656905821:user/default/recon-gen-local" \\
    RECON_GEN_TEST_L2_INSTANCE=tests/l2/sasquatch_pr.yaml \\
    RECON_GEN_CONFIG=run/config.postgres.yaml \\
    .venv/bin/python scripts/spike_x2r_qs_event_capture.py

Output: prints a timeline of every HTTP response + WS frame across
three windows — initial load, sheet switch (Today's Exceptions), and a
pick_filter on the Check Type dropdown. Each event is tagged with t=Δms
since the action started, so we can spot which signal lands first +
which lands consistently.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from recon_gen.common.config import load_config
from tests.e2e._drivers import QsEmbedDriver


_DASHBOARD_ID = "qs-gen-postgres-sasquatch_pr-l1-dashboard"


@contextmanager
def _capture_traffic(page: Any) -> Iterator[list[tuple[float, str, str]]]:
    """Attach response + websocket listeners. Yields a shared list of
    ``(t_ms, kind, summary)`` tuples; ``t_ms`` is the wall-clock at
    capture time (monotonic — caller subtracts the action-start ts to
    get a Δ). Detaches on exit."""
    events: list[tuple[float, str, str]] = []

    def _ts() -> float:
        return time.monotonic() * 1000.0

    def on_response(resp: Any) -> None:
        try:
            url = resp.url
            status = resp.status
            method = resp.request.method
            # Trim the URL for readability — keep the path + first
            # query param. Full URL printed below for the interesting
            # ones via post-hoc inspection.
            short = url.split("?", 1)[0]
            if "?" in url:
                first_q = url.split("?", 1)[1].split("&", 1)[0]
                short += f"?{first_q}…"
            events.append((_ts(), "HTTP", f"{method} {status} {short}"))
        except Exception as exc:  # noqa: BLE001
            events.append((_ts(), "HTTP-ERR", str(exc)))

    def on_websocket(ws: Any) -> None:
        events.append((_ts(), "WS-OPEN", str(ws.url)[:120]))

        def on_send(payload: Any) -> None:
            # Playwright passes the payload (str | bytes) directly,
            # NOT a frame object. Spike v1 assumed .payload — wrong.
            try:
                if isinstance(payload, bytes):
                    summary = f"[binary {len(payload)}B] {payload[:120]!r}"
                else:
                    summary = f"[text {len(payload)}c] {payload[:200]!r}"
                events.append((_ts(), "WS-SEND", summary))
            except Exception as exc:  # noqa: BLE001
                events.append((_ts(), "WS-SEND-ERR", str(exc)))

        def on_recv(payload: Any) -> None:
            try:
                if isinstance(payload, bytes):
                    summary = f"[binary {len(payload)}B] {payload[:120]!r}"
                else:
                    summary = f"[text {len(payload)}c] {payload[:200]!r}"
                events.append((_ts(), "WS-RECV", summary))
            except Exception as exc:  # noqa: BLE001
                events.append((_ts(), "WS-RECV-ERR", str(exc)))

        def on_close(*_: Any) -> None:
            events.append((_ts(), "WS-CLOSE", str(ws.url)[:120]))

        ws.on("framesent", on_send)
        ws.on("framereceived", on_recv)
        ws.on("close", on_close)

    page.on("response", on_response)
    page.on("websocket", on_websocket)
    try:
        yield events
    finally:
        # Playwright doesn't expose .off; the listeners are torn down
        # with the page on driver close.
        pass


def _print_window(
    label: str, events: list[tuple[float, str, str]],
    start_ts: float, settle_after_ms: float = 8000.0,
) -> None:
    """Print every event between (start_ts) and (start_ts + settle_after_ms),
    with Δms relative to start_ts."""
    print()
    print(f"=== {label} (t=0 at action start, capturing {settle_after_ms:.0f}ms) ===")
    n = 0
    for ts, kind, summary in events:
        delta = ts - start_ts
        if delta < -50.0:
            continue  # before the action — usually filter dust
        if delta > settle_after_ms:
            continue
        print(f"  t={delta:+7.0f}ms  {kind:10}  {summary}")
        n += 1
    print(f"  ({n} events in window)")


def main() -> None:
    cfg = load_config("run/config.postgres.yaml")
    print(f"Opening {_DASHBOARD_ID} in account {cfg.aws_account_id}, region {cfg.aws_region}")

    with QsEmbedDriver.embed(
        aws_account_id=cfg.aws_account_id,
        aws_region=cfg.aws_region,
        headless=True,
    ) as driver:
        page = driver._page  # noqa: SLF001  -- spike-only access
        with _capture_traffic(page) as events:
            # Window 1 — initial dashboard load.
            print("\nW1: Loading dashboard…")
            t0 = time.monotonic() * 1000.0
            driver.open(_DASHBOARD_ID)
            # Give the load some room to drain the post-load chatter.
            page.wait_for_timeout(2000)
            _print_window("W1: open(dashboard)", events, t0, settle_after_ms=10_000)

            # Window 2 — switch to a sheet with the dropdown we care about.
            print("\nW2: Switching to 'Today's Exceptions' sheet…")
            t0 = time.monotonic() * 1000.0
            driver.goto_sheet("Today's Exceptions")
            page.wait_for_timeout(2000)
            _print_window("W2: goto_sheet('Today's Exceptions')", events, t0, settle_after_ms=10_000)

            # Window 3 — the pick_filter we want to wait on.
            # Use filter_options to know what's valid first.
            print("\nW3: Picking a Check Type value…")
            options = driver.filter_options("Check Type")
            if not options:
                print("  (no options — bailing on W3)")
                return
            chosen = options[0]
            print(f"  Picking {chosen!r} from {len(options)} options")
            t0 = time.monotonic() * 1000.0
            # IMPORTANT: bypass _settle_after_param_change so we see the
            # raw signal — call the underlying helper directly.
            from recon_gen.common.browser.helpers import set_multi_select_values
            set_multi_select_values(
                page, "Check Type", [chosen], driver._page_timeout,  # noqa: SLF001
            )
            # Give the re-fetch room to land + tail off.
            page.wait_for_timeout(8000)
            _print_window(
                "W3: pick_filter('Check Type', [first option])",
                events, t0, settle_after_ms=10_000,
            )


if __name__ == "__main__":
    main()
