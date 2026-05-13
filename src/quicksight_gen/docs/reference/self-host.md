# Self-hosting the dashboards (App 2)

The four bundled apps render two ways. The default is **QuickSight** — `quicksight-gen json apply --execute` pushes a JSON resource graph (theme, datasource, datasets, analyses, dashboards) to AWS. The second is **App 2**: a small self-hosted HTMX + d3 page server that reads the same L2 instance and the same database, with no AWS account involved. It's the offline-iteration path (edit the L2 YAML / dataset SQL, refresh the page) and the renderer the X.4 editor and X.5 ETL helper build on.

## Running it

```bash
pip install 'quicksight-gen[serve]'
quicksight-gen dashboards -c config.yaml                # one process, all 4 apps
# → http://127.0.0.1:8000/dashboards
```

`config.yaml` points at a database (`demo_database_url`) — PostgreSQL, Oracle, or a SQLite file; App 2 supports all three (the same dialect-aware SQL the QuickSight datasets use). The schema + seed have to already be applied (`quicksight-gen schema apply --execute`, `data apply --execute`, `data refresh --execute`) — App 2 only reads.

It's stateless on purpose: no auth, no sessions, no in-process cache. Every GET re-runs the query; the URL *is* the cache key (filter state round-trips as `?param_X=…` query params), so an edge / browser cache layer Just Works. Embed it behind your own auth front when you put it on a network.

## What gets bundled in the wheel — App 2 runs offline

App 2 needs a few browser-side libraries (HTMX for the swaps, d3 + d3-sankey for the charts, the filter-widget libs, a context-menu lib for row drills). Rather than CDN-load them — which would break `pip install` + `serve` with no internet — the **pre-built minified dist files are committed inside the package** and served from `/static/vendor/…` off the package's own static mount. The wheel ships everything; nothing is fetched at runtime.

The full vendored set lives in `src/quicksight_gen/common/html/assets/vendor/` with provenance pinned in `assets/vendor/vendor.lock` (`{name, version, source_url, sha256, dest}` per dep). Today:

| Library | Version | Role | File |
|---|---|---|---|
| htmx | 2.0.3 | partial-page swaps after a filter change / drill | `js/htmx.min.js` |
| d3 | 7.9.0 | the chart hydrators (KPI / table / bar / line) use `d3.select` / `d3.scale*` | `js/d3.min.js` |
| d3-sankey | 0.12.3 | the Sankey flow visuals | `js/d3-sankey.min.js` |
| Tom Select | 2.3.1 | single- + multi-select dropdowns with chips + typeahead (replaces native `<select>`) | `js/tom-select.complete.min.js` + `css/tom-select.min.css` |
| Flatpickr | 4.6.13 | the universal date-range popover | `js/flatpickr.min.js` + `css/flatpickr.min.css` |
| noUiSlider | 15.7.1 | draggable threshold sliders + min/max range sliders | `js/nouislider.min.js` + `css/nouislider.min.css` |
| ctxmenu | 2.1.0 | the "⋯" / right-click context menu on table rows that carry a `DATA_POINT_MENU` drill | `js/ctxmenu.min.js` (ships no CSS — it injects its own `<style>`; re-skinned via `widgets-theme.css`) |

Alongside those, the wheel also ships the **compiled Tailwind stylesheet** (`assets/output.css`), the **filter-widget theme override sheet** (`assets/widgets-theme.css` — re-colours the Tom Select / Flatpickr / noUiSlider / ctxmenu chrome onto the L2's `--color-*` tokens), and the **inlined application JS** (`assets/js/bootstrap.js`, `assets/js/dev_log.js` — these get embedded into the page shell at render time, not served as separate files). None of these touch a CDN either.

Two CI guards keep it that way: `tests/unit/test_vendor_assets.py` asserts each committed vendor file's SHA256 matches `vendor.lock` *and* that the rendered page shell carries zero external `<script>` / `<link>` URLs; the `release.yml::Smoke test wheel` job installs a non-editable wheel and runs `pytest tests/unit/`, so a missing `package-data` glob → `FileNotFoundError` at collection → the smoke job fails.

## Maintainer chores

These aren't `quicksight-gen` CLI verbs — end users never run them. They're the recipes for keeping the committed artifacts in sync.

### Bump a vendored JS/CSS version

1. Edit `assets/vendor/vendor.lock`: change the dep's `version` + `source_url`; set its `sha256` to `null`.
2. `python scripts/vendor_js_deps.py --update` — re-downloads each dep, writes the file, fills in the `sha256`.
3. If the file path changed, update the matching `render.py` constant (`_HTMX_SRC` / `_D3_SRC` / `_CTXMENU_JS` / …) and the `package-data` glob in `pyproject.toml` if a new directory appeared.
4. Re-run the JS unit tests (`.venv/bin/pytest tests/js/`) — the fixture harness loads the vendored d3 — and `.venv/bin/pytest tests/unit/test_vendor_assets.py`.
5. Commit the changed file(s) + `vendor.lock`.

`python scripts/vendor_js_deps.py` with no args is the verify mode (assert every committed file's sha256 matches the lock) — handy as a pre-commit sanity check, though `test_vendor_assets.py` already does it in CI.

### Add a new browser-side dep

Same as a bump, plus: add a `{name, version, source_url, sha256: null, dest}` record to `vendor.lock`'s `deps` array, run `--update`, then point a new `render.py` `_…_SRC` constant at `/static/vendor/<dest>` and add it to `_VENDOR_JS` / `_VENDOR_CSS`. If it needs theming, add a block to `widgets-theme.css` keyed off the `--color-*` tokens (and add the override-sheet's `<link>` ordering check to `test_html_filter_widgets.py` if it's a new file). Update this table.

### Rebuild the Tailwind stylesheet

`assets/output.css` is the compiled Tailwind v4 sheet — rebuild it whenever `assets/input.css` (the `@theme` tokens / `@source` scan globs) changes, or a new utility class shows up in `render.py` / `bootstrap.js` (Tailwind only emits the classes it scanned for, so an un-rebuilt sheet silently drops styles that the markup references).

```bash
python scripts/build_app2_css.py            # rebuild + write output.css
python scripts/build_app2_css.py --check    # rebuild to a temp file and diff
                                            # against the committed one (a
                                            # best-effort staleness guard)
```

`tailwindcss` is the standalone Rust binary `pytailwindcss` installs (it's in `[dev]`); the script finds it next to the running interpreter, so `.venv/bin/python scripts/build_app2_css.py` works without activating the venv.

### The docs site ships in the wheel too

This handbook is bundled inside the package (`<site-packages>/quicksight_gen/docs/`) and rebuilt with `quicksight-gen docs apply --portable` — `--portable` inlines the Graphviz WASM renderer so the site itself is offline-capable. Same offline-by-default posture as App 2; covered under [Customization → How do I publish docs against my L2?](../walkthroughs/customization/how-do-i-publish-docs-against-my-l2.md).
