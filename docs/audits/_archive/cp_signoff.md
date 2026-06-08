# CP — Sign-off

**Phase:** CP — Move `business_day_offset` onto Account / AccountTemplate
**Branch:** `cp-business-day-offset-per-entity`
**Status:** Code-complete; CP.11 demo verification recorded (this doc); CP.12 sign-off pending operator review.

## Shipped sub-cells

| Cell | Commit | Summary |
|---|---|---|
| CP.0 | `e76f6948`, `a8121586` | Design audit (`docs/audits/cp_0_business_day_offset_design.md`); range cap to [-23, 23] locked. |
| CP.1 | `3f5ef1b2` | Dataclass field + `__post_init__` range guard + loader hook + 10 tests. |
| CP.2 | `79131708` | `seed.py:_compute_eod_balances` reads `account.business_day_offset` / `template.business_day_offset` directly; `_ResolvedAccount` carries the value through the meta-map. |
| CP.3 + CP.5 | `95ee5c04` | Deleted `L2Instance.role_business_day_offsets` + loader path + serializer emit + editor mutation + M.4.4.14 validator. Added new validator M.4.4.14a (reject offset on `scope=external`). Dropped the `/l2_shape/instance/` YAML textarea (folded CP.5). |
| CP.4 | `69385933` | FieldSpec on Account + AccountTemplate forms (placeholder `"0"`); `_coerce_field` int branch; Tailwind utility hide via `group-has-[select[name=scope]_option[value=external]:checked]:hidden` on field-row (raw CSS rejected per operator: "I like surprises even less; let's use the utility"). |
| CP.6 / CP.7 / CP.8 / CP.9 / CP.10 | `1b4361e3` | Bundled fixtures exhibit per-entity non-uniformity; fuzz emits per `60/30/10` distribution on internal-scope only; generalized fuzz-coverage anti-drift test (`test_fuzzer_populates_every_optional_field`); semantic-lock + serializer emit fixes surfaced + repaired; dogfood gate round-trips `business_day_offset` for `spec_example`. |

## CP.11 — Demo-surface verification (this run)

Probe: in-memory DuckDB, applied `tests/l2/spec_example.yaml` schema + seed via the production code paths; queried `<prefix>_daily_balances.business_day_start` per account.

Observed:

| Account | Declared offset | `business_day_start` hour | Lock evidenced |
|---|---:|---:|---|
| `cust-001` | `+0` | 0 | Lock 1 — per-entity, role-shared with `cust-002` but different offset |
| `cust-002` | `+5` | **5** | Lock 1 |
| `tmpl-cust-011`…`tmpl-cust-030` (CustomerSubledger materialized) | `-3` (on template) | **21** | Lock 4 — template offset fans out to all 20 instances, wraps cleanly to 21:00 prev-day-relative |
| All accounts/templates without declaration | n/a | 0 | Default behavior preserved |

The shift is end-to-end through the seed → SQL → row contract. No QS / App2 deploy needed to confirm — the timing lives in the row's `business_day_start` / `business_day_end` columns the downstream renderers consume.

## Open / deferred

- **Per-fixture offset values are strawman** (operator deferred with "just need a mixture"). Easy to tweak in `tests/l2/spec_example.yaml` / `sasquatch_pr.yaml` / `heavy_density_v1.yaml` if a specific demo scenario wants different values. Re-lock the semantic-lock files when changing.
- **Fuzz distribution** locked at `60% None / 30% 0 / 10% uniform in [-12, 12]`. CP.8's generalized anti-drift gate enforces both halves of coverage; if a percentage tweak causes the gate to fail at N=100 seeds, bump seed count or adjust percentages.
- **AWS QS deploy verification** intentionally skipped per autonomous-run-boundary. The local DuckDB verification above is the runtime evidence; QS-side parity is the existing 4-way agreement test (run as part of `qs_browser` layer).

## Done-when checklist (from PLAN.md)

- [x] `Account.business_day_offset` + `AccountTemplate.business_day_offset` exist as typed dataclass fields
- [x] `__post_init__` enforces `-23 <= offset <= 23`
- [x] Loader parses the new YAML key + rejects non-int values
- [x] Loader rejects the legacy `role_business_day_offsets` key with an explicit migration error
- [x] Validator M.4.4.14a rejects offset on `scope=external` (Account + AccountTemplate)
- [x] `seed.py` reads + USES the per-entity offset (`business_day_start` shifts visibly)
- [x] `L2Instance.role_business_day_offsets` GONE (field + loader + validator + serializer + editor + studio routes + instance form widget)
- [x] FieldSpec on both account forms; scope-conditional hide via Tailwind utility (no raw CSS)
- [x] Bundled fixtures exhibit non-trivial offsets on role-shared singletons + template-fanout
- [x] Fuzz generator emits the field per locked distribution
- [x] Generalized fuzz-coverage anti-drift test catches future field drift
- [x] Locked seeds + semantic-lock files re-locked
- [x] Dogfood gate round-trips `business_day_offset` for `spec_example`
- [x] Demo-surface verification (this doc) shows the timing shift end-to-end
- [ ] **Operator sign-off** ← pending morning review

## Recommended next step

Merge `cp-business-day-offset-per-entity` → `main` after operator reads this doc + skims the strawman fixture picks. The branch passes pyright clean + 392 unit tests + the dogfood round-trip; the only thing missing is the explicit "yes ship it."
