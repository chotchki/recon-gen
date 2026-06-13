# DE.5 — collapse status (paused for operator decision)

**Date:** 2026-06-13
**Phase:** DE.5 (paused at "ready to swap" boundary)
**Status:** Augmented `config_v14.py` to full parity with legacy; example yaml rewritten to v14; remaining yaml + Python migrations sized + waiting on operator direction.

## What landed

| Commit | What |
|---|---|
| `c542e0c1` (DE.5 sub-A) | Augmented `config_v14` toward parity: AwsConfig gains `prefixed`/`tags`/`dataset_arn`/`theme_arn` methods; AuditSigningConfig gains lazy env-loaded `passphrase()`; TestGeneratorConfig expanded to full 15-field surface + `as_of_frame`; App2Config gains `db_pool_size`; loader gains `_apply_env_overrides` (RECON_GEN_* env vars on nested dict) + auto-derives `datasource.arn` when `mode=create`. |
| (uncommitted) | `config.example.yaml` rewritten to v14 nested shape with `extends:`/auth/audit/app2/test block examples + comments documenting the v13→v14 migration. |

## What's blocked

**40+ files reference v13 flat-shape concepts (`aws_account_id`, `deployment_name`, etc.) at one of three layers:**

1. **`make_test_config(**overrides)` callsites** (~30 files) — `tests/_test_helpers.py:62` accepts flat kwargs and constructs `Config(aws_account_id=..., aws_region=..., ...)`. Under the v14 collapse, `Config` no longer has those fields. The helper signature breaks; every caller using `make_test_config(aws_account_id=X)` breaks.
2. **Inline yaml strings** (~17 files) — tests write `cfg.yaml` to tmp paths with flat-shape yaml strings (e.g. `tests/unit/test_cfg_de4_dc_dd_blocks.py::_MIN_YAML`). v14 loader REJECTS legacy keys via `_check_legacy_keys`. Each fixture needs hand-rewriting to nested shape.
3. **Direct `cfg.<flat>` reads in src/** (3 files surfaced in earlier scan: `_dev/runner.py`, `common/probe.py`, `common/browser/helpers.py`) — already swept in DE.2; cross-checking they're all on nested accessors.

Mechanical sweep would TRY but each layer has structural rewrites (not pure text-replace), so attempts to mass-rewrite via regex have garbled docstrings + yaml comments (see this run: cleanup.py docstring was clobbered by the auto-migrator, restored from git).

## Honest scope estimate

| Sub-step | Effort | Risk |
|---|---|---|
| B — Migrate `make_test_config` signature (accept nested kwargs OR auto-translate) + ~30 callers | 2 hr | Medium — every test fixture might subtly break |
| C — Hand-migrate ~17 inline-yaml fixtures to nested shape | 1 hr | Low |
| D — Swap `config_v14.py` → `config.py` + delete legacy + sweep 4 `from config_v14 import` callsites | 30 min | Medium — pyright catches misses, runner catches behavior |
| E — `./run_tests.sh up_to=qs_browser` validation | 30 min wall + ~2-3 iterations of fix-up | High — first run almost certainly red, each iter ~30 min |
| F — RELEASE_NOTES.md + PLAN.md → ARCHIVE | 30 min | Low |
| G — Version bump + tag v14.0.0 + push | 10 min | **OPERATOR-GATED** |

**Total estimated wall time:** 4-6 hours autonomous, with substantial unknown-unknowns in the make_test_config sweep + runner fix-ups.

## Three paths for operator decision

### 1. Hard v14.0.0 — push through all of DE.5 now

I attempt the full collapse autonomously, run `up_to=qs_browser` until green, pause at version-bump for explicit go-ahead. Estimated 4-6 hours of session time. Risk: I land partial breakage that surfaces in CI; or I exhaust effort budget halfway through.

### 2. Soft v14.0.0 — cut now, defer hard break

Cut v14.0.0 at current state — in-process accessors are nested via DE.2 proxies, `config_v14.py` is the canonical v14 loader, but legacy `config.py` still accepts flat yaml. This **violates the DE.0 "hard break, no compat shim" lock** but ships a coherent v14 release immediately. v14.1.0 or v15.0.0 later does the actual collapse + yaml shape break.

Cons: contradicts `[[feedback_no_compat_shims]]`; an operator reading "v14.0.0" expects the hard cut. Pros: ships v14 surface (TLS / OIDC / nested accessors / `extends:`) without weeks of migration risk.

### 3. Defer v14.0.0 entirely — v13.16.0 ship of TLS/OIDC

Ship a minor release v13.16.0 with DE.2-DE.4's value (nested accessors via proxies, TLS, OIDC, env_keys partition widening) but no v14 cut. Mark DE.5 as parked. Resume DE.5 when there's a dedicated session for the collapse.

Cons: DE.0's lock-in cadence breaks; phase D loops open longer. Pros: lowest risk, ships value, doesn't tie a major release to migration work I can't complete autonomously.

## Operator's call needed

- [ ] Path 1 (push through), 2 (soft cut), or 3 (defer)?

If path 1: I resume autonomously, you watch for the version-bump pause.
If path 2: I commit current state + write a v14.0.0 RELEASE_NOTES entry that acknowledges the soft-cut posture + push to a branch for your review.
If path 3: I sweep my open DE.5 work to a parked-phase doc + ship v13.16.0 (asking before the cut).

Captured: 2026-06-13 (DE.5 sub-A pushed; sub-B paused at the make_test_config breaking-change boundary).
