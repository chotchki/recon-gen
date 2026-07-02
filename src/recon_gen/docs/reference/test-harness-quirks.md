# Test-harness quirks + silent-renderer detection

Two things live here, both renderer-AGNOSTIC — they outlast whichever renderer is current:

1. **Detecting a renderer that fails silently** — the active-canary + multi-layer-detection pattern we built when QuickSight gave us no error signal, distilled to the part that transfers to any render layer (App2 included).
2. **Local browser-test-harness quirks** — gotchas in the Playwright/WebKit harness itself, not in any renderer.

Renderer-SPECIFIC quirks belong in that renderer's own log — the QuickSight ones (silent rendering, drill/param-write, control UX, data-shape, backend/refresh) are in the [QuickSight quirks log](quicksight-quirks.md), kept as the v15.x reference. This page is what survives when a renderer doesn't.

---

## Detecting a renderer that fails silently

The worst class of renderer bug gives you NO signal. QuickSight was the teacher here: datasets described as `CREATION_SUCCESSFUL`, the database returned rows in milliseconds, the API threw nothing — and every visual sat on a spinner forever, or rendered blank under a label that said it was filtered. No banner, no error, no narrowing-to-zero. (The QS-specific symptoms are catalogued in the [QuickSight quirks log](quicksight-quirks.md#1-silent-rendering-failures); this section is the detection machinery, which is not QS-specific.)

The lesson generalizes: ANY layer between your data and the screen can fail without saying so, so you don't wait for it to complain — you build your own signal. Three layers, cheapest first:

- **An active canary in the product itself.** Every dashboard's last sheet is `Info` (the App Info canary — `common/sheets/app_info.py`): a real-query KPI, a per-matview row-count table and a deploy stamp (git SHA + timestamp). It lives in the SHARED tree, so App2 carries it too — this is why the pattern transfers, not just the story. How to read it: if `Info` shows a number, the renderer is healthy and an empty visual is a data/SQL problem; if `Info` is blank too, the render layer ITSELF is broken. One glance splits "my query is wrong" from "the renderer is down" — the split you otherwise burn an hour guessing at.
- **A diagnostic ladder that assumes nothing.** When a visual is empty, walk down: (1) does the DB return rows for the underlying SQL directly (`psycopg` / `oracledb`) — proves the data is there; (2) is the dataset/query healthy on its own terms; (3) does a fresh session render it — rules out cache; (4) if all three pass, the renderer is the broken layer. Each rung is cheap and rules out a whole class, so you stop re-checking the data when the data was never the problem.
- **Driver-level detection with eager capture.** The browser driver scans every load for SQL-exception / error signals rather than trusting a green assertion. On a hit, the failure-capture suite fires EAGERLY — screenshot, DOM, console, network, DB row-counts and a Playwright trace — BEFORE the assertion unwinds, so the artifacts freeze the moment of detection, not the cleaned-up state afterward. (Same capture suite the `./run_tests.sh triage` flow drops you into; see CLAUDE.md.)

The through-line: a renderer that can't tell you it failed is a renderer you instrument from the outside. Canary + ladder + eager capture is the shape, regardless of what's doing the rendering.

---

## Local browser-test-harness quirks

Gotchas in the local `app2_browser` WebKit harness itself — not in any renderer. Documented here because that's where they surface.

### macOS WebKit "headless" leaks IOSurfaces and crashes WindowServer

**Symptom.** On macOS the browser tier launches a *visible* WebKit window (despite `headless=True`) and, after enough screenshots, the whole display server (`WindowServer`) SIGABRTs with `WSIOSurfaceDebugTallyAndAbort` — taking the session down. The `unit` layer then hang-kills on the next run while the system recovers.

**Cause.** Playwright's macOS WebKit is NOT off-WindowServer the way the Linux/CI port is — it renders through Cocoa and routes screenshot/trace capture through WindowServer's Metal window-capture path (`captureWindowList` → `CompositorMetal::CreateCaptureSurface` → `iosurface_create_common`). The trace **screenshot filmstrip** (`context.tracing.start(screenshots=True)` in `common/browser/helpers.py`) runs continuously per session, allocating a GPU-backed IOSurface per frame against WindowServer's per-client tally; under xdist `-n auto` (N concurrent WebKit sessions) the tally crosses threshold and aborts. macOS 26.5.1 tightened the tally enforcement + surfaced the window; the bundled WebKit 26.4 (`webkit-2272`, playwright 1.59) is one OS-minor behind the host. Linux/CI has no WindowServer, so the identical code is green — a genuine host-OS divergence.

**Fix (shipped).** The filmstrip is now OFF by default and armed only under `RECON_GEN_TRACE_ALL=1`. DOM snapshots + sources stay on (they don't touch WindowServer); the on-failure full-page screenshot is bounded (one per failing test). **DO NOT** set `RECON_GEN_TRACE_ALL=1` for a full xdist browser run on macOS — it re-arms the leak across all workers. Use it only for a focused single-test `--trace-all` debug.

**Durable follow-ups (PLAN.md backlog).** (a) Spike playwright 1.59 → 1.61 (WebKit 26.4 → 26.5, matching host macOS 26.5) to confirm whether it restores true off-screen headless. (b) The only byte-for-byte parity with CI is running the browser tier in a Linux WebKit container locally.
