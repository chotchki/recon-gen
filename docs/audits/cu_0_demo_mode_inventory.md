# CU.0 — `demo_mode` inventory + cutover map

**Filed 2026-06-09.** Inventory of every `demo_mode` callsite + sandbox/launchd asset that needs to change in Phase CU, with a per-site disposition. Locks the surface so CU.3 (the strip) is mechanical, not design.

**Principle (re-stated from CU header):** the demo install should fall out of the standard product configuration — cfg.yaml shape + sandbox-exec profile + launchd wrapper, NOT an in-code flag. After CU.3:

- L2 mutation routes (POST/PUT/DELETE on `/l2_shape/*`) — **always mounted.** Filesystem perms (the sandbox-exec profile) is the security layer.
- `POST /deploy` — **always mounted.** Demo cfg has dummy AWS creds → fails at the AWS-push step, which is acceptable.
- `PUT /data/knobs/etl_hook` — **gated on `cfg.app2.etl_hook is not None`** (already the natural semantics; demo cfg omits etl_hook). NOT gated on a demo flag.
- Create / Edit / Delete affordances — **always visible.** Demo visitors SHOULD edit (auto-reset on restart is the contract).
- `.studio-state.yaml` placement — **env-driven (`STUDIO_STATE_DIR`)** unconditionally. CU.2's wrapper script exports it; default falls back to `<cfg.parent>` when unset.

## Callsite categories

Counts by file (`grep -rn "demo_mode\|--demo-mode" src/ tests/` after the `.pyc` strip):

| File | Hits | Disposition |
| --- | ---: | --- |
| `tests/unit/test_studio_demo_mode.py` | 58 | **DELETE entire file.** Its only purpose is to assert routes are stripped in demo_mode. After CU.3 those routes always mount; the file's contract no longer exists. |
| `src/recon_gen/common/html/_studio_routes.py` | 39 | Strip 32 occurrences; 7 are doc-comments to remove or rewrite. |
| `src/recon_gen/common/html/_studio_editor_routes.py` | 19 | Strip all 19; the affordance-hide branches go away (Create/Edit/Delete stay visible). |
| `src/recon_gen/cli/studio.py` | 5 | Drop the click option + tmpdir branch (the env-driven path in CU.2 replaces it). |
| `tests/unit/test_cg{11,12,16,18}_*.py`, `test_btb_cold_read_v3_fixes.py`, `test_studio_etl_{run,triage}.py` | 22 | Strip the `demo_mode=False` kwarg from each `_render_read_card(..., demo_mode=False, ...)` call. These tests don't actually exercise demo_mode — they pass the literal False to satisfy the current signature. |
| `src/recon_gen/common/html/_studio_training_v2.py` | 1 | Single stale prose mention in a button-title attr — "shows only your plant, not the demo-mode noise." Rephrase to drop "demo-mode" (the noise is structural, not demo-specific). |

**Total in-tree callsites to touch: 142.** Of those, 22 are test kwarg strips (purely mechanical), 58 are deletion of one file (`test_studio_demo_mode.py`), and 62 are the real code surface across `_studio_routes.py` / `_studio_editor_routes.py` / `cli/studio.py` / `_studio_training_v2.py`.

## Disposition map — `_studio_routes.py`

Source-of-truth: file at `96a63867`.

| Line | Current shape | After CU.3 |
| ---: | --- | --- |
| 306–328 | `def _demo_mode_banner(demo_mode: bool) -> str:` returning hardcoded read-only banner | **Replace with `_banner(cfg)`** — reads `cfg.app2.banner_text` (new cfg field, see §New cfg knobs). Hardcoded "Read-only demo" text was wrong (edits will work post-CU.3); demo cfg sets `banner_text: "Edits reset on restart"`. |
| 332, 410, 417 | `_render_home_page(..., demo_mode=False, ...)` — `add_link = "" if demo_mode else (...)` | Drop `demo_mode` param. Always render the `+ Add` link. |
| 498, 509, 517 | Singleton Edit affordance suppressed in demo_mode | Drop the branch; always render the Edit link. |
| 546 | `demo_banner = _demo_mode_banner(demo_mode)` in home render | Replace with `_banner(cfg)` (read from cfg). |
| 820, 837, 845 | `_render_diagram_page(..., demo_mode=False, ...)` + banner splice | Drop `demo_mode` param; banner via cfg. |
| 938, 962 | `_render_data_page(..., demo_mode=False, ...)` + banner splice | Drop `demo_mode` param; banner via cfg. |
| 1556, 1581 | `_render_docs_page(..., demo_mode=False, ...)` + banner | Drop; banner via cfg. |
| 2311, 2323 | `_render_data_modal_*` + banner | Drop; banner via cfg. |
| 2701 | `_render_*(..., demo_mode: bool = False, ...)` | Drop param. |
| 3170 | `{_demo_mode_banner(demo_mode and not embed)}` | `{_banner(cfg, embed=embed)}` (banner suppressed in embed mode preserved via embed kwarg). |
| 4038, 4111, 4112 | `deploy_controls = "" if demo_mode else (...)` | **Always render Deploy button.** Per CU header lock: /deploy stays mounted, fails noisily on dummy AWS creds. |
| 4216, 4275, 4287, 4454, 4518, 4536, 4554, 4607 | `make_studio_routes(..., demo_mode=False, ...)` threading | Drop param from `make_studio_routes` signature + all call sites. |
| 5262, 5271 | `make_editor_routes(cache, demo_mode=demo_mode, ...)` | Drop kwarg. |
| 5571–5575 | `if not demo_mode: routes.append(Route("/data/knobs/etl_hook", ...))` | **Replace with `if cfg.app2.etl_hook is not None:`** — already the right semantics: a PUT to invoke etl_hook is meaningless without a configured hook. Demo cfg omits etl_hook → route auto-skips. |
| 5720–5721 | `if not demo_mode: routes.append(Route("/deploy", ...))` | **Always mount.** Demo cfg's dummy AWS creds let it fail at AWS-step, surface that to the visitor without breaking the route table. |

## Disposition map — `_studio_editor_routes.py`

| Line | Current shape | After CU.3 |
| ---: | --- | --- |
| 2678, 2684–2685, 2810, 2831 | `_render_read_card(..., demo_mode=False, ...)` — Edit/Delete actions suppressed in demo_mode | Drop param. Always emit Edit/Delete actions. |
| 2886, 2900, 2908 | `_render_read_card_partial(..., demo_mode=False, ...)` — same shape | Drop param. |
| 5096, 5135 | `_render_kind_index_partial(..., demo_mode=False, ...)` — affects what list-page renders | Drop param. |
| 5409, 5536, 5556 | `_render_kind_page(..., demo_mode=False, ...)` | Drop param. |
| 6276, 6284, 6292, 6295, 6302, 6319 | `make_editor_routes(..., demo_mode=False, ...)` — strips `/new` GET + create POST + edit GET + update PUT + delete DELETE | Drop param. **Always mount all five.** Sandbox-exec is the security layer (writes to `l2.yaml` succeed only when the path is writable, which on demo is only the tmpdir overlay). |

## Disposition map — `cli/studio.py`

| Line | Current shape | After CU.3 |
| ---: | --- | --- |
| 93–110 | `@click.option("--demo-mode/--no-demo-mode", default=False, ...)` | **DELETE.** No replacement flag. |
| 119 | `demo_mode: bool,` parameter | **DELETE.** |
| 162–175 | `if demo_mode: ... tg_cache = TestGeneratorCache(..., state_path=_demo_state_dir / ".studio-state.yaml")` | **REPLACE.** Read `STUDIO_STATE_DIR` env var unconditionally. When set, place `.studio-state.yaml` there (CU.2 wrapper exports it). When unset, fall back to `<cfg.parent>/.studio-state.yaml` (today's non-demo behavior). |
| 187 | `make_studio_routes(..., demo_mode=demo_mode, ...)` call | Drop kwarg. |

## Disposition map — `_studio_training_v2.py`

| Line | Current shape | After CU.3 |
| ---: | --- | --- |
| 278 | Button-title attr text `"shows only your plant, not the demo-mode noise."` | Rephrase to `"shows only your plant, not the seed noise."` The phrasing was wrong even pre-CU — it's the seed data that's noisy, not "demo-mode" specifically. |

## Test cleanup

22 test-file kwarg strips, all mechanical:

- `tests/unit/test_cg11_account_display_name.py` lines 79, 103, 121, 141, 157 — drop `demo_mode=False,`
- `tests/unit/test_cg12_chain_title_parent_only.py` lines 98, 122, 139 — drop `demo_mode=False,`
- `tests/unit/test_cg16_chain_id_comma_safe.py` lines 134, 165 — drop `demo_mode=False,`
- `tests/unit/test_cg18_limit_schedule_title.py` lines 99, 116, 132, 150 — drop `demo_mode=False,`
- `tests/unit/test_btb_cold_read_v3_fixes.py` lines 191, 221, 245 — drop `demo_mode=False, top_nav_html="",` (top_nav_html stays a parameter — separate concern)
- `tests/unit/test_studio_etl_run.py` line 598 — drop `demo_mode=False,`
- `tests/unit/test_studio_etl_triage.py` lines 177, 224 — drop `demo_mode=False,`
- `tests/unit/test_studio_demo_mode.py` — **delete file** (58 hits, 335 lines)

After cleanup, `grep -rn "demo_mode\|--demo-mode" src/ tests/` should return zero.

## New cfg knobs (count: 1)

Only one new cfg field, justified as general "this server has a banner" rather than demo-specific:

```python
@dataclass(frozen=True, slots=True)
class Config:
    # ... existing fields ...

    # CU.3 — optional top-of-page banner text. When set, every Studio /
    # Dashboards page renders a sticky banner above the content with
    # this text + a Learn-more link. Demo installs set
    # `banner_text: "Edits reset on next restart"`; production-cfg
    # leaves it None (no banner). Replaces the hardcoded
    # `_demo_mode_banner` block (which was wrong post-CU anyway —
    # edits work, they just don't persist).
    banner_text: str | None = None
```

The "Learn more" link target stays hardcoded to `https://chotchki.github.io/recon-gen/` — same as today's `_demo_mode_banner`. (If the user wants that configurable too, add `banner_href: str | None = None` — but YAGNI for now.)

## CU.2 — Wrapper script + ulimit cap (no recon-gen changes besides `STUDIO_STATE_DIR` env-driven `.studio-state.yaml`)

`deploy/launchd/launch-{spec,sasquatch}.sh` (the latter exists today, the former needs creation — `recon-gen dashboards` for spec is currently launched directly by the plist without a wrapper, see `io.hotchkiss.recon-demo.spec.plist`):

```sh
#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1

INSTANCE_DIR=/Users/recon-demo/sasquatch_pr
CANONICAL_L2="$INSTANCE_DIR/l2.yaml"

# CU.2 — per-launch tmpdir for the L2 overlay + .studio-state.yaml.
# KeepAlive respawn (post-SIGTERM from refresh-demos.sh, or any crash)
# re-runs this script → fresh tmpdir → canonical L2 re-copied. Visitor
# edits accumulated in the prior tmpdir are intentionally discarded.
STUDIO_STATE_DIR="$(mktemp -d -t recon-demo-studio-state)"
export STUDIO_STATE_DIR

cp "$CANONICAL_L2" "$STUDIO_STATE_DIR/l2.yaml"

# CU.2 — per-file disk cap. 50MB is way more than the L2 yaml + state
# file ever need (canonical sasquatch_pr l2.yaml is ~35KB); a hostile
# visitor scripting POSTs into accounts/rails/templates would have to
# add roughly a million entries to hit this cap. Sandbox-exec writable
# allowlist is STUDIO_STATE_DIR only, so per-file cap = effective
# total cap for visitor-controlled disk usage.
ulimit -f 51200

exec /usr/bin/sandbox-exec \
    -D HOME=/Users/recon-demo \
    -D INSTANCE_DIR="$INSTANCE_DIR" \
    -D PORT=8402 \
    -D PYTHON=/Users/recon-demo/venv/bin/python3.13 \
    -D STUDIO_STATE_DIR="$STUDIO_STATE_DIR" \
    -f /Users/recon-demo/sandbox/recon-demo-sasquatch.sb \
    -- /Users/recon-demo/venv/bin/recon-gen studio \
        -c /Users/recon-demo/sasquatch_pr/config.yaml \
        --l2 "$STUDIO_STATE_DIR/l2.yaml" \
        --port 8402 \
        --host 127.0.0.1 \
        --no-docs
```

Spec needs the same shape (with `recon-gen dashboards` instead of `recon-gen studio` — read-only doesn't need the overlay copy, but for symmetry and so spec's `/data` panel works the same way, run studio there too with `cfg.studio_enabled: false` if we want to lock down the editor surface for spec specifically).

## CU.4 — Mac mini cutover (operator-side)

Per-instance config.yaml changes:

```yaml
# /Users/recon-demo/sasquatch_pr/config.yaml — after CU.4
aws:
  account_id: "111122223333"   # dummy; /deploy will fail at AWS step, intentional
  region: "us-east-1"
  deployment_name: "recon-demo-sasquatch_pr"
db:
  dialect: duckdb
  url: duckdb:///Users/recon-demo/sasquatch_pr/current.duckdb
  table_prefix: "demo_sasquatch_pr"
app2:
  banner_text: "Edits reset on next restart"   # CU.3 — new field
# NOTE: no `app2.etl_hook:` — `cfg.app2.etl_hook is None` → PUT route auto-skips
# NOTE: no `aws.principal_arns:` — /deploy will fail at AWS step regardless
```

Sandbox profile is unchanged from CU.1 (already DuckDB + STUDIO_STATE_DIR writable). Wrapper script lands per CU.2. launchd plist `ProgramArguments` swap to point at the wrapper instead of bare `recon-gen` (the sasquatch plist already does this; spec doesn't yet).

## Verify checklist (CU.4 exit)

1. Manual `workflow_dispatch` on demo-publish → refresh succeeds → `current.duckdb` rebuilt.
2. Visit https://recon-gen-sasquatch.hotchkiss.io → home page renders → banner visible reading "Edits reset on next restart".
3. Click `+ Add` on Accounts → form renders → POST succeeds → account appears.
4. Restart sasquatch launchd job → home page no longer shows the added account.
5. Click Deploy → request fires → noisy 503 surfacing AWS-step failure with dummy creds.
6. Verify `PUT /data/knobs/etl_hook` returns 405 (route not mounted; `cfg.app2.etl_hook` is None).
7. Attempt to overshoot the `ulimit -f` cap: POST a 60MB blob's worth of accounts → server errors at file-write boundary, browser surfaces a 500. KeepAlive respawn picks up cleanly.

## Open questions / nothing held

None. Locks from the user (2026-06-09):

- L2 yaml edits enabled (sandbox is security layer)
- /deploy enabled (dummy AWS → noisy fail acceptable)
- etl_hook unconfigured (cfg-driven, not flag-driven)
- Trainer knobs unchanged (already always-enabled)
- Banner kept (operators want it for legalese / disclaimers); driven by `cfg.app2.banner_text`
- Wrapper does the overlay copy (no `l2_overlay_dir` cfg knob; just `--l2` pointing at tmpdir)
- `ulimit -f 51200` cap (~50MB per file)
- Existing restart cadence (nightly + manual `workflow_dispatch`)
- Mac mini cutover is operator-side (no repo commit; CU.4 deliverable)
- **spec stays `recon-gen dashboards`-only.** Demonstrates the dashboards-only deployment shape (no editor surface, no mutation routes, no wrapper). Sasquatch demonstrates the full Studio + editable-overlay shape. CU.1's sandbox-profile DuckDB swap is the only CU touchpoint for spec; CU.2's wrapper + overlay applies to sasquatch only. spec's plist keeps direct `recon-gen dashboards` invocation.
