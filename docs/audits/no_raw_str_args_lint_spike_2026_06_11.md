# `no-raw-str-args` Lint Spike

Prototype + blast-radius measurement for extending the BC.1 D8 lint
family (`no-raw-temporal-args`) to bare `str` function parameters
across `src/recon_gen/**`. Goal: make the half-open enum typing
(`Origin` / `TransactionStatus` / `AmountDirection`) load-bearing by
forcing call sites to use typed wrappers (NewType / Literal / typed
constant) instead of bare `str`.

Pair-read: [[untyped_enum_audit_2026_06_11]] (audit identifying the
enum-shaped offenders this lint would force closed).

## Methodology

AST-walk `src/recon_gen/**/*.py`. For every `FunctionDef` /
`AsyncFunctionDef`, examine every parameter (positional / kwonly /
posonly) annotation. Flag if the annotation matches:

- `str` (bare `ast.Name`)
- `str | None` (PEP 604 union via `ast.BinOp` with `BitOr`)
- `Optional[str]` (`ast.Subscript` of `Optional`)

Skip:

- `self` / `cls` (defensively — they don't carry annotations anyway).
- Container shapes (`list[str]`, `dict[str, X]`, `Mapping[str, X]`,
  `Sequence[str]`) — the `str` is structural-not-policy in those.
- Unannotated params (the explicit-any / pyright path catches those).
- `tests/**` (out of scope for the lint — the migration discipline is
  a src-code contract).

Implementation lives at `scripts/measure_no_raw_str_args.py`
(standalone measurement) plus a report-only `NoRawStrArgsCheck` +
`test_no_raw_str_args_blast_radius` in
`tests/unit/test_typing_smells.py`. Report-only means the count is
asserted ≤ a generous ceiling (2000) so the test stays green while
the migration is being planned. Once the migration begins, registering
the check in `_build_checks()` (currently commented out, same staging
pattern as `NoRawTemporalArgsCheck`) flips it from "measure" to
"enforce".

## Total hits + module breakdown

**Total hits: 1364**

Across 131 of 189 `src/recon_gen/**/*.py` files (58 files clean). For
reference, the BC.1 D8 sibling `no-raw-temporal-args` migration spans
dozens of callsites; `no-raw-str-args` is ~30× larger.

Top 15 modules by hit count:

|   Hits | Module |
|-------:|--------|
|    335 | `common/html` |
|    268 | `common/l2` |
|    176 | `common/` (top-level) |
|    109 | `common/spine` |
|     87 | `cli` |
|     86 | `common/tree` |
|     66 | `_dev` |
|     58 | `common/browser` |
|     51 | `common/sql` |
|     42 | `cli/audit` |
|     28 | `common/handbook` |
|     16 | `apps/l1_dashboard` |
|     15 | `apps/l2_flow_tracing` |
|     10 | `<root>` (`main.py`, `__main__.py`) |
|      5 | `apps/executives` |

Top 15 worst single files:

|   Hits | File |
|-------:|------|
|     94 | `common/html/_studio_editor_routes.py` |
|     61 | `common/html/_studio_routes.py` |
|     58 | `common/tree/structure.py` |
|     54 | `common/browser/helpers.py` |
|     41 | `_dev/runner.py` |
|     41 | `common/sql/dialect.py` |
|     39 | `common/l2/loader.py` |
|     38 | `common/l2/plant_registry.py` |
|     34 | `common/l2/seed.py` |
|     33 | `common/html/render.py` |
|     33 | `common/l2/schema.py` |
|     27 | `common/html/_studio_training_v3.py` |
|     27 | `common/snapshotter.py` |
|     25 | `common/db.py` |
|     22 | `cli/audit/pdf.py` |

The `common/html/` Studio surface dominates — unsurprisingly, since
Studio is a wide HTMX route layer where every handler takes bare
strings off the request and threads them to executors. That's also
the highest-typo-risk zone (URL param name typos are silent — wrong
data, no error).

## Annotation shape breakdown

| Shape | Count |
|-------|------:|
| `str` | 1113 |
| `str \| None` | 251 |
| `Optional[str]` | 0 |

The codebase is fully on PEP 604 (`Optional[str]` is zero).

## Parameter-name distribution (top 30)

|   Hits | Param name |
|-------:|------------|
|     99 | `prefix` |
|     85 | `name` |
|     43 | `path` |
|     34 | `sql` |
|     32 | `title` |
|     31 | `account_id` |
|     30 | `scenario_id` |
|     29 | `l2_instance_path` |
|     27 | `text` |
|     20 | `kind` |
|     20 | `value` |
|     20 | `field_id` |
|     19 | `top_nav_html` |
|     19 | `base_prefix` |
|     18 | `p` |
|     16 | `visual_title` |
|     16 | `entity_id` |
|     15 | `rail_name` |
|     14 | `output` |
|     13 | `label` |
|     13 | `config` |
|     13 | `key` |
|     12 | `subtitle` |
|     11 | `dashboard_id` |
|     11 | `test_id` |
|     10 | `version` |
|      9 | `body` |
|      9 | `raw` |
|      9 | `visual_identifier` |
|      9 | `role` |

### Natural buckets (the load-bearing signal)

Crosswalking the top names against the audit's enum-shaped targets,
the typed-ID newtypes, and the obvious free-form / SQL / path patterns:

| Bucket | Count | Notes |
|--------|------:|-------|
| other / uncategorized | 714 | The long tail (`value`, `config`, `version`, `body`, `column`, `password`, `app_name`, `error`, …). Mixed — some are typo-risk (`column`, `column_name`, `dataset_id`, `control_id`), most are free-form (`raw`, `error`, `body`). |
| display / free-form | 274 | `title`, `name`, `prefix`, `subtitle`, `label`, `text`, `description`, `message`, `caption`, `tooltip`, etc. Low typo-risk; wrapping is overhead. |
| typed-id candidate | 230 | `account_id`, `scenario_id`, `rail_name`, `entity_id`, `dashboard_id`, `field_id`, `visual_identifier`, `l2_instance_path`, `test_id`, `role` — most of these already have NewTypes in `common/ids.py` OR should. High value to migrate. |
| path / url | 67 | `path`, `output`, `output_dir`, `config_path`, `src_path`, `dest_path`, `fname`. Could use `pathlib.Path` directly instead of `str` (a separate refactor). |
| sql / template | 40 | `sql`, `template`, `query`, `expression`. Low-value to wrap — they're already raw SQL territory. |
| **enum-shaped (audit targets)** | **39** | `kind` (20), `direction` (6), `supersedes` (3), `origin` (3), `status` (3), plus `scope` / `cadence` / etc. **This is the load-bearing 3%.** |

The 39 enum-shaped hits are the entire reason this lint exists.
They're also a tractable migration on their own — sub-2-day shape if
done alone.

## Sample hits

### Worst-offender first-hits (per top-10 module)

```
src/recon_gen/_dev/cleanup.py:47                     sweep_qs_resources_by_tag(account_id: str)
src/recon_gen/apps/executives/app.py:178             _section_box_content(title: str)
src/recon_gen/apps/investigation/datasets.py:236     _money_trail_base_sql(prefix: str)
src/recon_gen/apps/l1_dashboard/app.py:2033          _populate_pushdown_enum_dropdown(title: str)
src/recon_gen/apps/l2_flow_tracing/app.py:495        _populate_pushdown_dropdown(title: str)
src/recon_gen/cli/_app_builders.py:122               _resolve_l2(l2_instance_path: str | None)
src/recon_gen/cli/audit/__init__.py:1407             audit_apply(l2_instance_path: str | None)
src/recon_gen/common/aging.py:28                     aging_bar_visual(visual_id: str)
src/recon_gen/common/browser/helpers.py:93           generate_dashboard_embed_url(aws_account_id: str)
src/recon_gen/common/handbook/diagrams.py:74         render_l2_topology(name: str | None)
```

### Enum-shaped hits (the load-bearing targets — 35 of 39 shown)

These are the hits that match the `untyped_enum_audit_2026_06_11`
findings. Forcing these to typed wrappers makes the audit's "type
system catches up to what the schema already enforces" change land
with teeth — a future caller can't accidentally pass `"posted"`
(lowercase typo) where `"Posted"` is required.

```
src/recon_gen/_dev/cleanup.py:131                       _delete_one(kind: str)
src/recon_gen/apps/investigation/datasets.py:729        _account_network_sql(direction: str)
src/recon_gen/common/db.py:454                          _TypedSqlLiteral.__init__(kind: str)
src/recon_gen/common/etl.py:87                          write_daily_balance(supersedes: str | None)
src/recon_gen/common/etl.py:173                         write_transaction(origin: str)
src/recon_gen/common/etl.py:174                         write_transaction(status: str)
src/recon_gen/common/handbook/diagrams.py:769           _RailEdgeBundle.add(kind: str)
src/recon_gen/common/html/_data_shape.py:420            shape_for_kind(kind: str)
src/recon_gen/common/html/_sql_executor.py:141          _format_default_for_sql(kind: str)
src/recon_gen/common/html/_sql_executor.py:299          _coerce_bind(kind: str | None)
src/recon_gen/common/html/_studio_routes.py:2646        _gap_kind_label(kind: str)
src/recon_gen/common/html/_studio_routes.py:2653        _gap_kind_editor_label(kind: str)
src/recon_gen/common/html/_studio_routes.py:2920        _render_triage_kind_section(kind: str)
src/recon_gen/common/html/_studio_routes.py:3686        _focus_node_id_for_entity(kind: str)
src/recon_gen/common/html/_studio_routes.py:3735        render_mini_diagram_html(kind: str)
```

The `etl.py` hits at lines 87/173/174 are exactly the audit's top-4
remediation targets — they're the production write entry points for
`<prefix>_transactions` and `<prefix>_daily_balances`. Closing those
three is the highest-ROI cut.

## Recommended whitelist strategy

Three options on the table, in increasing cost:

### Option A — Ship the lint now, scoped to enum-shaped only (~39 hits)

Restrict `NoRawStrArgsCheck` to a `_ENUM_SHAPED_NAMES` allowlist:
`{kind, direction, status, origin, scope, supersedes, cadence,
subtype, completion, transfer_type, amount_direction, account_scope,
role_kind, account_kind}`. Migrate exactly those 39 hits as a single
focused phase, in the audit's recommended order (amount_direction →
status → account_scope → supersedes → the rest).

**Cost:** ~12-16h. Mirrors the audit's stated "top 4 closing-enum
offenders" estimate (8-12h) plus the `kind` family (~4h, 20 hits but
all in Studio so they cluster).

**Pro:** Highest signal-to-noise. Locks the audit's recommendations
in place at the type-system layer. No whitelist churn.

**Con:** Doesn't catch typo drift on the typed-ID surface
(`account_id` / `scenario_id` / `rail_name`) which is the bigger
silent-bug surface in production (silent-zero-rows, not silent-
schema-violation).

### Option B — Ship report-only across the full corpus, migrate in waves

Keep the lint at "report ≤ N hits" (current state, ceiling = 2000).
Migrate in phased waves:

1. Wave 1 (enum-shaped, 39 hits) — closes the audit findings.
2. Wave 2 (typed-id candidates, 230 hits) — `account_id`, `scenario_id`,
   etc. Some already have NewTypes; new ones land in `common/ids.py`.
3. Wave 3 (path/url, 67 hits) — convert to `pathlib.Path` where
   feasible; suppress the rest with `# typing-smell: ignore`.
4. Wave 4 (display / free-form, 274 hits) — suppress as
   `# typing-smell: ignore[no-raw-str-args]: display string`.
5. Wave 5 (other / 714) — case-by-case.

Each wave drops the ceiling. Enforce-mode flip when ceiling = 0.

**Cost:** ~50-80h total, spread across 4-6 phases. Comparable to BC.1
+ BD.

**Pro:** Comprehensive — gets the typed-ID surface too, which is the
production bug-magnet.

**Con:** Long migration. The suppression-comment count grows large
(display strings = 274 hits = 274 `typing-smell: ignore` comments).

### Option C — Whitelist by convention, no migration

Hardcode a `_FREE_FORM_PARAM_NAMES` allowlist that exempts every
plausibly-free-form name (`path`, `url`, `text`, `value`, `key`,
`name`, `message`, `prefix`, `suffix`, `title`, `subtitle`, `label`,
`description`, `body`, `raw`, `output`, `sql`, `template`, `fmt`,
`query`, `config`, `version`, `error`). After whitelist, blast
radius drops to ~250 hits (the `account_id` / `kind` / `field_id`
tail).

**Cost:** ~1h of whitelist tuning, then comparable to Option A
migration (~16h).

**Pro:** Fast. Catches enum-shaped AND typed-ID candidates without
forcing every "title: str" to become typed.

**Con:** Whitelist drift over time — a new free-form name lands in
src/ and trips the lint, prompting a one-line whitelist edit instead
of a thoughtful "should this be typed?" decision. Conventions decay.

## Estimated cleanup cost (per remediation pattern)

Concrete cost-per-hit estimates by remediation pattern:

| Pattern | Hits | Cost-per-hit | Total |
|---------|-----:|-------------:|------:|
| Add `Literal` type alias + thread to writers | 39 (enum-shaped) | ~20 min | 13h |
| Add NewType + wrap at boundaries | 230 (typed-id) | ~10 min | 38h |
| Convert to `Path` | 67 (path/url) | ~5 min | 6h |
| Per-line `# typing-smell: ignore[no-raw-str-args]: <why>` | 274 (display) | ~1 min | 5h |
| Case-by-case (other) | 714 | ~5 min avg | 60h |
| **Full migration total** | **1364** | — | **~122h** |
| Option A (enum-only) | 39 | — | **~13h** |
| Option C (post-whitelist) | ~250 | — | **~25h** |

Reference: BC.1 + BD spent ~40h on the temporal migration
(~80 callsites). Full str migration would be ~3-4× larger.

## Operator decision

**Recommendation: ship Option A.** Scope the lint to enum-shaped names
only (the 39 hits). Reasons:

1. **The audit's findings need teeth.** The
   `untyped_enum_audit_2026_06_11` doc identifies 4 top remediations
   (`amount_direction`, `status`, `account_scope`, `supersedes`)
   totaling 8-12h. Option A adds `kind` + a few more for ~13h total —
   the same shape, just locked in by the type system instead of
   review discipline. Without the lint, the audit's "type system
   catches up to schema CHECK" promise depends on humans remembering
   to thread the new Literal through every writer.

2. **Full corpus is too big to land in one phase.** 1364 hits =
   ~122h is bigger than BC + BD combined. Option B is realistic but
   spans 4-6 phases of focused work; that's a roadmap commitment,
   not a phase decision.

3. **The non-enum surface is mixed-value.** Display strings (274
   hits) get zero value from wrapping. Path/URL (67) is a separate
   refactor (PEP 519 `Path`). Typed-ID candidates (230) ARE valuable
   but `common/ids.py` would need to grow significantly
   (`AccountId`, `ScenarioId`, `RailName`, `EntityId`, `FieldId`,
   `LayerName`, `AppName` …) and that's a wider design conversation
   about whether to grow the newtype layer or accept str at the
   boundary.

4. **Option C's whitelist decays.** "Add a new free-form name"
   becomes a routine PR-comment-and-whitelist-edit interaction. The
   lint stops catching new typo-risk surface because it's already
   suppressed by convention.

Concretely, ship Option A as the BC.1 D9 work (next in the D-family
after the temporal D8) and execute the audit's recommended order:

1. `AmountDirection: Literal["Debit", "Credit"]` (~2-3h).
2. `TransactionStatus: Literal["Posted", "Pending", "Failed"]` +
   schema CHECK (~3-4h).
3. `Scope` threaded through spine generators (~1-2h).
4. `SupersedeReason` annotated at writers (~2-3h).
5. `_RoleKind` module-local Literal in `seed.py` (~1h).
6. The 20 `kind: str` Studio hits — case-by-case Literal vs
   keep-bare with suppression (~2-3h, many are diagram-routing
   strings that are genuinely free-form, not bounded enums).

Total: ~12-16h. Lands as one phase, mirrors BC.1 + BD shape.

Then sequence Option B Wave 2 (typed-ID, 230 hits) as a separate
phase 1-2 quarters out, once Studio surface has stabilized post-CB
and the `common/ids.py` newtype layer can be designed coherently
rather than reactively.

## Spike artifacts

- `scripts/measure_no_raw_str_args.py` — standalone measurement, no
  pytest dependency. Run with `.venv/bin/python
  scripts/measure_no_raw_str_args.py`.
- `tests/unit/test_typing_smells.py::NoRawStrArgsCheck` — report-only
  Check subclass, NOT registered in `_build_checks()` yet.
- `tests/unit/test_typing_smells.py::test_no_raw_str_args_blast_radius` —
  ceiling-bounded sanity test (asserts hits ≤ 2000), surfaces the
  current count to pytest output.

To enable enforcement: scope the `files=` list in the registration to
the enum-shaped-only-modules and add an `_ENUM_SHAPED_NAMES` filter
inside the visitor. See "Option A" section above.
