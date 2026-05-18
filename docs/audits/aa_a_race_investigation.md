# AA.A.race — App2 pick-chain freshness + missing-fetch investigation

**Date**: 2026-05-17 → 2026-05-18
**Branch**: main (commits `6190c23`, `4c7e248`)
**Trigger**: 22 e2e failures on the `up_to=browser --variants=sp_pg_aw`
chain following AA.A.9.a (commit `6632777`), almost all `[app2-*]` —
`test_l1_dropdown_pickers_inverse_excludes_anchor[app2-Drift]` and
similar inverse-then-restore tests timed out at `App2Driver._wait_for_refetch`'s
15s ceiling after the *restore* pick.

## TL;DR

What looked like two distinct bugs turned out to be one bug with a
cache wrinkle:

1. **T2→T4 freshness race** — a *real* race in `App2Driver.wait_loaded`
   where the no-skeleton predicate could return true between an
   in-flight wave's swap and the next queued wave's beforeRequest.
   **Fixed prophylactically** in `6190c23` via per-visual
   `data-bound-params` provenance stamping (server) +
   `data-requested-params` / `data-rendered-params` mirroring (client)
   + freshness poll in `wait_loaded`.

2. **Nth-pick "missing fetch"** — the *dominant* symptom that the PoC
   didn't fix. Initial diagnosis: "the request never leaves the
   browser." Actual diagnosis (`4c7e248`): the request leaves the
   browser fine, but **WebKit serves the response from disk cache**
   (production sets `Cache-Control: public, max-age=60` on
   `/visuals/.../data`), and **Playwright's `page.expect_response()`
   doesn't fire on cache hits**. `_wait_for_refetch` hangs for 15s
   while the JS layer is happily swapping fresh content into the DOM.
   Fix: `html2_server(visual_data_cache_max_age_s=0)` for e2e — no
   cache, no asymmetry. One line.

## Timeline

### 2026-05-17 — initial investigation

- Chain `20260518T014519Z-6632777` (after AA.A.9.a refactor) produced
  22 failures. The first few looked like `[qs-Drift]` "0 rows after
  picker applied" → suspected SQL bug.
- Built `tmp/diag_l1_pickers.py` (now deleted) that invoked the
  production dataset builders via `apply_dataset_param_defaults` —
  same path App2's `_sql_executor` uses — against Aurora. Returned 1
  row for every spec → SQL pipeline is correct.
- Captured `dom.html` from `[app2-Drift]` showed the table with the
  correct 1 row at capture time. Test was reading DOM mid-state.
- **AA.A.6.bug.drift closed as a false alarm** (not a SQL bug; "0
  rows" was a stale-read race).
- Filed `AA.A.race` umbrella with two hypotheses (T2→T4 freshness,
  Nth-pick missing-fetch).

### 2026-05-17 (PoC) — freshness oracle

Built `6190c23`:

- **Server** (`render.py::emit_visual_data_fragment`): the
  `<script class="chart-data" data-bound-params="...">` attribute
  carries the params the server used to fetch this fragment, in a
  byte-stable shape (sort_keys + compact separators). Mirrors what
  `_serializeBoundParams` does client-side.
- **Client** (`bootstrap.js::htmx:beforeRequest`): stamps
  `data-requested-params` on the `.visual-data` div from
  `evt.detail.requestConfig.parameters`, using the same serializer.
- **Client** (`bootstrap.js::htmx:afterSwap`): mirrors
  `data-bound-params` from the section to
  `data-rendered-params` on the `.visual-data` div.
- **Driver** (`App2Driver.wait_loaded`): after the skeleton-clear +
  content-visible checks, polls until
  `el.dataset.requestedParams === el.dataset.renderedParams`.

This closes the T2→T4 race for real. But the next chain run still
showed 22 failures, all looking like "Nth pick missing fetch."

### 2026-05-18 — race.1 tracer + root cause

**race.1 — instrumented JS tracer**: added `console.debug("[trace] …")`
in:

- `wireFilterAutoRefresh`'s `change` listener (logs source name +
  value)
- The same debounce-fire (logs after the 300ms settles)
- The `htmx.trigger refresh on N visuals` step (logs count)
- `wireTomSelect`'s `onChange` (logs name + value)
- `htmx:beforeRequest` (logs visual ID + serialized params)
- `htmx:afterSwap` (logs visual ID + serialized rendered-params)
- `App2Driver.pick_filter`'s `setValue` (logs target / current /
  `noop=true|false`)

Ran `test_l1_dropdown_pickers_inverse_excludes_anchor[app2-Drift]`
under direct pytest against Aurora `database-2`. Captured
`tests/e2e/screenshots/_failures/…_console.txt` (28k of `[trace]`
lines).

**The trace contradicted the hypothesis.** For the restore pick that
the driver claimed got no fetch:

```
[trace] pick_filter.setValue name=param_pL1DriftAccount
   target="Customer Number One (cust-001)"
   current="Customer 11 (cust-011)" noop=false
[trace] TomSelect.onChange name=param_pL1DriftAccount value="…"
[trace] form.change source=param_pL1DriftAccount value=Customer Number One
[trace] form.change source=param_pL1DriftAccount value=Customer Number One
[trace] debounce-fire
[trace] htmx.trigger refresh on 4 visuals
[trace] htmx:beforeRequest visual=… params={"date_from":"2026-05-11",…,"param_pL1DriftAccount":"Customer Number One (cust-001)",…}
[trace] htmx:beforeRequest visual=… params={…}
[trace] htmx:beforeRequest visual=… params={…}
[trace] htmx:beforeRequest visual=… params={…}
[trace] htmx:afterSwap visual=… rendered={…Customer Number One…}
[trace] htmx:afterSwap visual=… rendered={…Customer Number One…}
…
```

Four `beforeRequest`. Four `afterSwap` with the restored params
landed in the DOM. The fetch fired. The swap happened.

Cross-referenced `network.txt`: 20 visual-data responses captured —
4 per pre-restore phase × 5 phases (initial load + 3 anchor picks +
1 invert). **Zero responses for the restore phase.**

That's the contradiction: the JS layer says 4 successful fetch+swap
cycles for the restore; the network layer says 0 responses arrived
for the restore. Either Playwright dropped 4 responses (unlikely —
the capture sink is a simple `page.on("response")` accumulator), or
the responses were never network responses to begin with.

**Hypothesis: WebKit disk cache.** Production sets
`Cache-Control: public, max-age=60` on `/visuals/.../data`. The
restore pick's URL was identical to Phase-4's URL (same date,
same `param_pL1DriftAccount=Customer Number One`, same role) and was
served <60s earlier. WebKit served it from disk cache. Playwright's
`page.on("response")` callback **doesn't fire on cache hits in
WebKit** — confirmed by the absence of "fromCache" responses in
network.txt despite the swap landing. By extension,
`page.expect_response()` (built on the same event mechanism) didn't
fire either.

**Fix**: `tests/e2e/_harness_html2.py::html2_server` passes
`visual_data_cache_max_age_s=0` to `make_app`. E2E never wants stale
cache — every render must reflect a fresh fetch.

Verified by re-running the same test: ran 93s (vs 35s before),
progressed past the inverse → restore phase, ultimately failed on
an unrelated Aurora SSL timeout. The race is closed.

## What stayed in tree

- **Freshness oracle (PoC, `6190c23`)** — still useful. The T2→T4
  race it closes is real even without the cache wrinkle; under
  parallel-initial-load + mid-load filter pick, the skeleton can
  flicker out between waves. The oracle is the right defense for
  that. Kept.
- **JS tracer (race.1, `4c7e248`)** — kept. `console.debug` is the
  quietest log level, filtered by default in browser devtools, zero
  user impact in production. Next race investigation gets the
  diagnostic for free.
- **App2Driver.pick_filter setValue trace** — kept. Same reasoning.
- **`visual_data_cache_max_age_s=0` in the e2e harness** — the actual
  fix. Production's `max-age=60` stays. Only the e2e server gets the
  no-cache treatment.

## What we learned

1. **"No response in network.txt" ≠ "no fetch happened"**. WebKit
   serves identical-URL repeat fetches from disk cache without
   firing Playwright's response events. If a driver gates on
   `expect_response`, it WILL hang on cache hits.

2. **Tracer first, fix second**. The PLAN had three sub-hypotheses
   for the missing-fetch (TomSelect noop / debounce coalescing /
   per-visual trigger drop). All three were wrong. Without the
   tracer I'd have spent days picking among hypotheses. With the
   tracer the actual bug surfaced in one targeted run.

3. **Production caching is e2e-hostile by default**. Any HTTP
   response with `Cache-Control: max-age=N` and a deterministic URL
   will, on repeat fetch within N seconds, hit cache and bypass any
   network-event-based driver hook. Three options for the design:
   (a) e2e disables cache (chosen), (b) e2e adds `?nocache=<uuid>`
   query params to bust per-call, (c) drivers gate on JS layer
   (`htmx:afterSwap` events) instead of network. Option (a) is the
   cheapest and most honest — e2e isn't testing the cache layer.

4. **The two-bugs-but-one-bug-with-a-wrinkle arc is a real pattern**.
   The freshness oracle was conceived against a different (but real)
   race and still ships as defense. If we'd chased only the dominant
   symptom we'd have missed the T2→T4 vulnerability.

## App2 vs QS structural asymmetry (race.3 motivation)

The cache fix has no QS analogue: QS visuals fetch over a WebSocket
protocol (`{"type":"START_VIS","cid":"…"}` out, `{"type":"STOP_VIS","cids":[…]}`
back) with no HTTP `Cache-Control` to honor. The App2 bug literally
can't recur at the WS layer.

**But** `_QsWsActivityTracker`'s current strategy is a 300ms
quiet-window heuristic (`sent_START - sent_STOP == 0` for 300ms). If
QS's own client decides not to fire `START_VIS` for a parameter-write
whose result is already on-screen — exactly the equivalent of "cache
hit" at the WS layer — the heuristic would return immediately while
the DOM is still old, masking a failure shape symmetric to the App2
bug we just fixed.

Whether QS's client actually does this is unverified. The defensive
move is **race.3**: replace the heuristic with snapshot-then-wait
(snapshot in-flight cids before action, fire action, wait for any
new cids issued during the action to STOP). If QS doesn't fire new
cids, the wait returns immediately with the explicit observation
"no new fetches" — caught instead of masked. The work is now next-up
after this doc.

## References

- Commit `6190c23` — freshness oracle PoC (still in tree)
- Commit `4c7e248` — tracer + cache fix (the actual root cause)
- Spike `docs/audits/x_2_r_event_wait_spike.md` — original QS WS
  protocol capture
- `tests/e2e/_drivers/app2.py::_wait_for_refetch` — the driver hook
  that hung on cache hits
- `src/recon_gen/common/html/server.py::make_app` — the
  `visual_data_cache_max_age_s` knob
- `tests/e2e/_harness_html2.py::html2_server` — the e2e site where
  the knob is set to 0
