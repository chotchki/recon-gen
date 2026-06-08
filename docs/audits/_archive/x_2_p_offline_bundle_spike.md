# X.2.p.0 — Offline asset bundle: shape spike

**Status:** decisions below; implementation = X.2.p.1.
**Date:** 2026-05-12.
**Why a spike** (per `feedback_spike_before_locking_implementation`):
the PLAN pre-named **esbuild** as "the natural pick given the
no-npm/no-bundler constraint". Investigating the actual asset surface
shows that pick doesn't fit — **no bundler is needed at all** for the
shape this codebase is in. The other open questions (vendor source vs
dist, lockfile mechanism, where the vendored files live, what the
"build hook" is) are decided here.

---

## 1. What's actually CDN-loaded today

App 2's page shell (`common/html/render.py::_PAGE_SHELL`) pulls these
from CDNs at runtime:

| Asset | Version | CDN | Role |
|---|---|---|---|
| `htmx.min.js` | 2.0.3 | unpkg | swap engine |
| `d3.min.js` | 7.9.0 | jsdelivr | chart rendering |
| `d3-sankey.min.js` | 0.12.3 | jsdelivr | Sankey layout |
| `tom-select.complete.min.js` + `.css` | 2.3.1 | jsdelivr | multi-select chips |
| `flatpickr.min.js` + `.css` | 4.6.13 | jsdelivr | date-range popover |
| `nouislider.min.js` + `.css` | 15.7.1 | jsdelivr | min/max slider |

That's **6 JS files + 3 CSS files**. All are pre-built, self-contained
UMD/IIFE dist bundles (no `import` statements, no shared deps).

**Already offline-ready** (no change needed):

- `assets/output.css` — Tailwind-compiled, **committed to git**, shipped
  via `package-data: common/html/assets/*.css`, served `/static/output.css`.
  Built by `.venv/bin/tailwindcss -i assets/input.css -o assets/output.css`
  (the `pytailwindcss` standalone binary) — run by hand; the *output* is
  committed so a `pip install` doesn't need the Tailwind CLI.
- `assets/widgets-theme.css` — hand-written, committed, served `/static/widgets-theme.css`.
- `assets/js/bootstrap.js` + `dev_log.js` — hand-written, committed,
  *inlined* into the page shell at module-import time.

**Out of scope:** `@hpcc-js/wasm-graphviz` — that's the *docs site's*
diagram renderer (`docs/stylesheets/qs-graphviz-wasm.js`), not App 2, and
the `--portable` docs build already vendors it (`cli/docs.py::_bake_portable_wasm`).

So the entire X.2.p job is: ship those 6 JS + 3 CSS third-party dist
files inside the wheel, and point the page shell at `/static/vendor/...`
instead of the CDN.

---

## 2. Decision: vendor pre-built dist — no bundler, no esbuild

**Drop esbuild from the plan.** The "esbuild" pick assumed we'd bundle
*something*. We won't:

- These 9 files are **pre-built, already-minified, opaque** dist bundles.
  esbuild on them would: bundle (nothing to resolve — no imports),
  minify (already minified), tree-shake (impossible — opaque UMD). Zero
  value.
- The only thing esbuild buys is *building from source* — and **the
  no-npm constraint makes "vendor source" the painful path, not the
  easy one.** `d3` 7.9.0 is a meta-package re-exporting ~15 sub-packages
  (`d3-array`, `d3-scale`, `d3-shape`, `d3-selection`, …); to bundle it
  ourselves without npm we'd vendor ~15 source trees (or vendor the npm
  tarball and extract — fragile, and esbuild still needs the dep graph
  resolvable on disk). Vendoring the pre-built `d3.min.js` is the
  no-npm-friendly path, and once you have pre-built dist you don't need a
  bundler.
- The Tailwind step *is* a real build (purges unused utilities → a
  smaller `output.css`) and stays as-is. The JS deps are different —
  they're libs we *ship*, not *build*.

### The trade-off (user asked) — small, favors vendor-dist here

| | vendor pre-built **dist** (decided) | vendor **source** + esbuild-bundle |
|---|---|---|
| Wheel/repo size | +~650 KB committed minified JS + ~50 KB CSS | a custom d3 bundle (only the sub-modules App 2 uses) ≈ ~250 KB → saves ~350–400 KB |
| Build tooling | **none** — commit the files + a refresh script + a SHA256-lock test | esbuild standalone binary (download-on-first-run, like `pytailwindcss`) + a real build step that must run before the wheel ships |
| What gets vendored | 9 files (the exact bytes jsdelivr/unpkg serve — battle-tested in dev) | ~15+ d3 source trees + htmx/tom-select/flatpickr/nouislider source — fragile to vendor without npm |
| Failure mode | none new — same files, served locally | our own bundle config; a wrong d3 sub-module or missing shim breaks a chart in a hard-to-debug way |
| Refresh on version bump | re-run the vendor script (re-download + verify SHA256 + commit) | re-vendor source trees + re-run esbuild + verify the bundle still works |

The size saving (~350 KB) is noise next to what the wheel already ships
(`docs/**` is several MB of markdown + SVGs), users `pip install` once,
and the build/correctness cost of "vendor source" is real given no npm.
**Vendor dist.** (If the wheel size ever becomes a real concern, the
fallback is splitting App 2 into its own optional `serve` install — which
it mostly already is — not switching to a source bundle.)

---

## 3. Decided shape

### 3a. Where the vendored files live

`src/quicksight_gen/common/html/assets/vendor/{js,css}/` — alongside
`output.css` / `widgets-theme.css` / `js/`. Shipped via a new
`package-data` glob `common/html/assets/vendor/*/*`. Served at
`/static/vendor/...` via the **existing** `assets/` static mount
(`Mount("/static", StaticFiles(directory=assets_dir))` — no new route).

### 3b. Provenance + refresh — `vendor.lock` + a script

`assets/vendor/vendor.lock` (JSON) — one record per dep:
`{name, version, source_url, sha256, dest}`. This is the provenance
stamp **and** the refresh input. Same model as
`tests/data/_locked_seeds/*.sql` + the `data lock` command: the artifact
(the vendored file) is committed; the lock records what it should be; a
script regenerates; a test asserts they match.

`scripts/vendor_js_deps.py` — reads `vendor.lock`, re-downloads each
`source_url`, verifies `sha256`, writes to `dest`. Run by hand when
bumping a version (edit the version + url + sha256 in the lock, run the
script). **No CLI subcommand** — this is a maintainer chore, not an
end-user op (mirrors `dump_top_queries.py`, not a `quicksight-gen …`
verb). It needs the network; everything downstream of it doesn't.

`tests/unit/test_vendor_assets.py` — for each `vendor.lock` record,
assert the committed file at `dest` exists and its SHA256 matches the
lock (so a stale or hand-edited commit fails loudly — exactly like
`test_locked_seeds.py`). Plus: assert every `<script>`/`<link>` `src`/
`href` the page shell emits is a `/static/...` local path — **no
`https://` / protocol-relative external URLs** anywhere in the rendered
HTML. That second assertion *is* the offline guarantee, as a fast unit
test.

### 3c. The "build hook" — there isn't one

There's no bundling, so there's no build step to hook into
`serve app2 apply`. The vendored `.min.js` / `.min.css` are committed →
they're just *there* in the wheel → `pip install quicksight-gen[serve]
&& quicksight-gen serve app2 apply` works with the network unplugged.
The page shell switches its `_HTMX_SRC` etc. constants from CDN URLs to
`/static/vendor/js/htmx.min.js` etc. — a one-line change per constant in
`render.py`, plus the new `package-data` glob.

(Adjacent, **not in X.2.p**: the Tailwind `output.css` build is
currently a hand-typed `.venv/bin/tailwindcss -i … -o …` with no
recorded recipe. It works — `output.css` is committed — but a
`scripts/build_app2_css.py` (or folding it into the same vendor script)
would formalize "how to rebuild the CSS". Flagging for a separate small
item, not blocking X.2.p.)

### 3d. Offline-runtime CI cell (X.2.p.2)

Two layers:

1. Fast unit test (above): page shell HTML has zero external
   `<script>`/`<link>` URLs.
2. CI step in `ci.yml::docs-portable-install` (or a sibling job): the
   existing "fresh non-editable venv" job already installs the wheel —
   add `pip install …[serve]`, then assert every `vendor/**` file the
   page shell references exists under the installed package's
   `common/html/assets/vendor/`. (A true "run the server with the
   network blocked and load a page" check is heavier — `iptables`/network
   namespace in CI — and the two checks above already prove the same
   thing: nothing the page needs is remote. Skip the network-block dance
   unless a regression slips past both.)

---

## 4. Updated X.2.p sub-task list (supersedes the PLAN's placeholders)

- **X.2.p.0** — this doc. ✓
- **X.2.p.1** — vendor the 9 dist files into `assets/vendor/{js,css}/`;
  write `assets/vendor/vendor.lock` + `scripts/vendor_js_deps.py`; add
  the `package-data` glob; switch `render.py`'s `_HTMX_SRC` /
  `_D3_SRC` / `_D3_SANKEY_SRC` / `_TOM_SELECT_*` / `_FLATPICKR_*` /
  `_NOUISLIDER_*` to `/static/vendor/...`; add `test_vendor_assets.py`
  (SHA256-lock + no-external-URL).
- **X.2.p.2** — CI: extend `docs-portable-install` to assert the
  `vendor/**` files land in the installed wheel.
- **X.2.p.3** — X.6.j self-host guide section: "what's vendored, how to
  bump a version (`scripts/vendor_js_deps.py`), how to add a new JS dep"
  — and fold the 9 deps into the X.2.p vendor list (this closes the
  parked X.2.l.4.e too).

**No esbuild. No new `serve app2 build` subcommand. No JS source
trees.** The deviation from the PLAN's "esbuild is the natural pick" is
deliberate and documented above.
