# X.2.r — QS event-wait spike

## Problem

`QsEmbedDriver._settle_after_param_change()` (X.2.q.3) waited for QS's
post-param-write data layer to settle via two heuristics:

1. `page.wait_for_timeout(1200)` — give the re-query a head start.
2. Poll the tables' first-cell text every 700 ms until it's steady
   across two reads.

Total budget: ~2.6 s minimum per parameter write, more if the re-query
is slow. Three more `page.wait_for_timeout` calls scattered through
`common/browser/helpers.py` (`scroll_visual_into_view` 800 ms,
`right_click_first_row_of_visual` 800 ms post-click, `count_table_total_rows`
2 s + 1.5 s + 0.5 s, `expand_all_tables_on_sheet` 800 ms + 500 ms) plus
a `time.sleep(0.25)` in `wait_for_dropdown_options_present`. With Y.2's
SQL pushdown making queries fast, the polling overhead is now larger
than the useful wait.

## What we knew going in

- `page.wait_for_load_state('networkidle')` doesn't fire on QS (a
  long-lived WebSocket keeps activity alive — quirks log §3.4).
- App2 has a clean signal: `page.expect_response(/visuals\/.*\/data/)`
  blocks on the next dataset HTTP response. App2Driver uses it in
  `_wait_for_refetch`.
- QS's data layer wire shape was undocumented. Two candidates: either
  HTTP XHRs (then `expect_response` works there too) or WebSocket
  frames (then `WebSocket.on("framesent" / "framereceived")` is the
  primitive).

## Capture

`scripts/spike_x2r_qs_event_capture.py` opens the deployed
`qs-gen-postgres-sasquatch_pr-l1-dashboard`, attaches `page.on("response")`
+ `page.on("websocket")` listeners (with `framesent` / `framereceived` /
`close` per WS), and prints a timeline across three windows: initial
load, sheet switch (Today's Exceptions), and a `pick_filter` on the
Check Type dropdown. Frames are dumped with their first ~200 chars of
payload + a wall-clock Δms relative to the action start.

## What we found

**One WebSocket connection** opens at +3 s during the initial load:

```
wss://us-east-1.quicksight.aws.amazon.com/embed/<id>/websocket/?mbtc=...
```

All subsequent dataset queries flow over that single WS as JSON text
frames. The wire shape:

```json
// Client → server (start a visual's query):
{"type":"START_VIS","cid":"<uuid>","request":{
    "dashboardId":"qs-gen-postgres-sasquatch_pr-l1-dashboard",
    "cfg":{
        "calculatedFields":[],
        "group":{"fields":[...],"metrics":[...]},
        ...
    }
}}

// Client → server (visual finished — tear down server-side state):
{"type":"STOP_VIS","cids":["<uuid>", "<uuid>", ...]}
```

After `pick_filter('Check Type', ['drift'])`:

```
t=+516ms  WS-SEND  START_VIS cid=39145314...  (KPI re-query)
t=+547ms  WS-SEND  START_VIS cid=e51693de...  (BarChart re-query)
t=+547ms  WS-SEND  START_VIS cid=66cf1e5d...  (Table re-query)
t=+880ms  WS-SEND  STOP_VIS  cids=[66cf1e5d]
t=+912ms  WS-SEND  STOP_VIS  cids=[e51693de]
t=+967ms  WS-SEND  STOP_VIS  cids=[39145314]
```

A second burst fires at t≈2 s (pick's debounced settle re-triggers a
follow-up round of fetches — same shape, fresh cids, all STOP'd within
~400 ms).

**`STOP_VIS` is the settle signal.** The client only sends STOP_VIS
after it's processed the data and torn down its rendering pipeline for
that visual. Tracking the set difference `sent_START - sent_STOP` gives
a "pending re-queries" counter. After a param write, wait for that
counter to drain back to zero (after at least one fresh START fires).

Caveats from the capture:

- **No `framereceived` events surfaced** during the spike runs (every
  WS-RECV count is zero in both timelines). Either WebKit's Playwright
  surface doesn't expose inbound WS frames, or the server pushes data
  through a different channel we didn't capture. Doesn't matter —
  client-sent `STOP_VIS` is sufficient (the client wouldn't tear down
  the visual without having processed the response).
- **The first request URL after sheet-load triggers visible HTTP** to
  `/api/dashboards/.../sheets/.../sheet-controls` etc. — these are the
  *structural* fetches (filter list, sheet config). The dataset queries
  themselves are WebSocket-only.
- **The pick triggers two bursts**, not one. The first burst at +516 ms
  is the immediate response to the param change; the second at +2 s is
  a debounced follow-up (likely from QS's own internal state-machine).
  The settle must wait until BOTH bursts have STOP_VIS'd, otherwise the
  read happens in the gap between bursts. "Pending == 0 AND at least
  300 ms since the last START" handles this.

## Decision

Replace `QsEmbedDriver._settle_after_param_change()`'s sleep-and-poll
with a `_QsWsActivityTracker` keyed off WebSocket frames:

- Class hooks `page.on("websocket")` + `ws.on("framesent")` at driver
  construction, parses each text frame as JSON, tracks `START_VIS` /
  `STOP_VIS` activity into a `pending: set[str]` of cids.
- `_settle_after_param_change()`:
  - Snapshot `total_start_count` baseline.
  - Run the action (caller's pick / set_date_range / drill).
  - Spin until `total_start_count > baseline` (proves the re-fetch was
    triggered) AND `len(pending) == 0` AND no new START in the last
    300 ms.
  - Cap at 15 s; swallow on timeout (caller re-reads — same best-effort
    contract as before).

The "no new START in 300 ms" guards against the two-burst case: we
won't return between bursts because the pending set briefly empties
mid-gap, but a fresh START fires before the 300 ms timer elapses.

**Fallback** (if the tracker turns out to flake): the existing
content-stability poll in `_settle_after_param_change` stays available
behind a feature flag for one cycle, then drops. (Not landing the flag
unless the tracker actually flakes — speculative complexity is its own
smell.)

## What this also gives us

The same tracker enables `wait_loaded(visual_title)` to resolve more
precisely on the *initial* page load too — instead of polling for the
title label, the driver can wait for the cid that started for that
visual to land its STOP_VIS. (Out of X.2.r scope, but tee'd up.)

## Sweep targets

After landing the tracker, audit the other `wait_for_timeout` /
`time.sleep` calls in `common/browser/helpers.py` and replace each with
the closest event primitive:

- `scroll_visual_into_view`'s 800 ms post-scroll → `wait_for_function`
  on the visual element being scrollable into view.
- `right_click_first_row_of_visual`'s 800 ms post-click → `wait_for_selector`
  on the context menu mounting.
- `count_table_total_rows`'s 2 s + 1.5 s + 0.5 s → `wait_for_selector`
  on `simplePagedDisplayNav_dropdown_pageSize` mounting + the menu item
  being visible.
- `expand_all_tables_on_sheet`'s 800 ms + 500 ms — same shape.
- `wait_for_dropdown_options_present`'s `time.sleep(0.25)` — replace
  with `expect_response` on the cascade fetch URL when applicable, else
  `wait_for_function` on the option list populating.
