# AI.1 — Studio L2 Editor Surface Coverage Audit

**Phase AI goal:** prove the Studio L2 editor can rebuild *any* L2 yaml via
browser-driven editing — load a reference L2, recreate every entity through
the editor, save, and assert the round-trip matches structurally (AI.4) and in
dashboard output (AI.5). This audit is the discovery step (AI.1): it inventories
the editor's current entity-editing surface against what the test corpus
(`spec_example` + `sasquatch_pr` + fuzz seeds) actually exercises, so AI.2 has a
concrete punch list. Per the AI.0 Lock-1 "zero noise floor" contract, AI.2 must
build every missing widget — there is no "scope to current surface, defer the
rest" escape hatch.

## Editor architecture (where the code lives)

The editor is an HTMX/Starlette surface mounted by `recon-gen studio`.

| Concern | File:line |
|---|---|
| CLI entry (`studio` command) | `src/recon_gen/cli/studio.py` |
| Server wiring + cache bind to `--l2` path | `src/recon_gen/cli/_html_serve.py:234` (`L2InstanceCache.from_path`) |
| Route splicing | `src/recon_gen/common/html/_studio_routes.py:1675` → `make_editor_routes` (1807) |
| Editor forms + HTMX cards + route handlers | `src/recon_gen/common/html/_studio_editor_routes.py` (FieldSpec lists, renderers, `make_editor_routes` factory @ 2811) |
| Mutation transforms (pure) | `src/recon_gen/common/l2/editor.py` — `mutate_l2`:81, `create_l2_entity`:166, `delete_l2_entity`:550, `rename_identifier`:116 |
| Cache + save-on-mutate (atomic write) | `src/recon_gen/common/l2/cache.py:118` (`save` → `serialize_l2` + `save_yaml_atomic`) |
| Serializer (round-trip to yaml) | `src/recon_gen/common/l2/serializer.py:45` |
| Ground-truth primitives | `src/recon_gen/common/l2/primitives.py` |

**Routes** (all under `/l2_shape/{kind}/`, `make_editor_routes` @ `_studio_editor_routes.py:2834`):
`GET /` (list), `POST /` (create), `GET /new` (new form; rail = 2-step subtype picker),
`GET /{id}` (read card), `GET /{id}/edit`, `PUT /{id}` (save), `DELETE /{id}`.
In `--demo-mode` only the two GET routes mount (create/edit/delete stripped).

The per-kind `FieldSpec` lists (`_ACCOUNT_FIELDS`, `_RAIL_FIELDS`, `_CHAIN_FIELDS`,
`_TRANSFER_TEMPLATE_FIELDS`, `_LIMIT_SCHEDULE_FIELDS`, dispatched by
`_FIELD_SPECS_BY_KIND`) drive both render and form-coercion.

## Per-entity-kind coverage

| Entity kind | Add | Edit | Delete | Missing |
|---|---|---|---|---|
| Account | ✅ | ✅ | ✅ | none (all 7 fields, incl. `parent_role`) |
| AccountTemplate | ✅ | ✅ | ✅ | none (incl. `instance_id_template` / `instance_name_template`) |
| Rail (TwoLeg + SingleLeg, subtype-gated) | ✅ | ✅ | ✅ | **create-path drops `cadence`, `amount_typical_range`, `firings_typical_per_period`** (edit-path fine) |
| TransferTemplate | ✅ | ✅ | ✅ | **`transfer_key` — no field anywhere** (hard gap); `leg_rail_xor_groups` is edit-only |
| Chain | ✅ | ✅ | ✅ | **per-child `fan_in` / `expected_parent_count` lost on CREATE** (one chain-level flag applied to all children; mixed-cardinality only via edit) |
| LimitSchedule | ✅ | ✅ | ✅ | none (incl. AB.1 `direction: Inbound`) |
| Theme / Persona (singletons) | n/a | ✅ (raw-YAML textarea) | clear-by-empty | none functionally |

Two **top-level L2 fields have no editor surface at all**:
`L2Instance.description` (`primitives.py:527`) and `role_business_day_offsets`
(`primitives.py:538`). Both are serialized, neither is editable.

## Corpus usage — what the dogfood must rebuild

Union of features exercised by `tests/l2/spec_example.yaml`,
`tests/l2/sasquatch_pr.yaml`, and `tests/l2/fuzz.py::random_l2_yaml`:

- All 6 list kinds + both singletons (theme: spec+sasquatch; persona: sasquatch only).
- Rail: union RoleExpression (multi-role leg — sasquatch), `Variable` leg_direction,
  per-leg origins, both aging windows, `posted_requirements`, `metadata_keys`,
  `metadata_value_examples` (sasquatch), `amount_typical_range`,
  `firings_typical_per_period` (both compact + `{period, range}` forms), aggregating + cadence + bundles_activity.
- TransferTemplate: **non-empty `transfer_key` on every corpus template**,
  `leg_rail_xor_groups` (sasquatch has a 2-group template), template-level
  `firings_typical_per_period` (spec_example).
- Chain: required (single child), multi-XOR, **`fan_in` + `expected_parent_count`**
  (spec epc=2, sasquatch epc=5), **mixed-cardinality** (sasquatch
  MerchantSettlementCycle: 1:1 ACH/Wire/Check + fan_in batch), template-as-parent / template-as-child.
- LimitSchedule: Outbound + `direction: Inbound`.
- **Top-level**: `description` (spec/sasquatch/most fuzz) and
  `role_business_day_offsets` (≈ every fuzz seed).

## Save mechanism

**No dedicated export route** — persistence is **save-on-mutate**: every successful
`PUT`/`POST`/`DELETE` calls `cache.save()` (`cache.py:118`), which reserializes the
full `L2Instance` via `serialize_l2` and atomically writes it back to the `--l2`
path. So the dogfood asserts against the saved `--l2` file (or `cache.get()`).
The serializer round-trips **every** field (incl. `transfer_key`,
`role_business_day_offsets`, top-level `description`), so once the editor can
*set* a field, persistence is sound. (`.studio-state.yaml` is trainer-knob state
only — never touches L2 entities.) AI.2 may add an explicit `POST /l2/export` per
the AI.0 lock or rely on save-on-mutate; save-on-mutate is sufficient for the test.

## Gap punch list (AI.2 scope)

### Hard blockers — corpus value cannot be produced by the editor at all
1. **(TransferTemplate, `transfer_key`)** — no FieldSpec; `create_l2_entity`
   hardcodes `transfer_key=()` (`editor.py:387`); `mutate_l2` never receives it.
   **Every** corpus template declares a non-empty `transfer_key`. New
   field needed. **Highest priority — blocks rebuilding any template.**
2. **(L2Instance, top-level `description`)** — no editor surface. Needs a
   singleton-style page or a top-level form field.
3. **(L2Instance, `role_business_day_offsets`)** — no editor surface; emitted by
   ≈ every fuzz seed. Needs a role→int(hours) map editor. Without it no fuzz seed round-trips.

### Create-path field drops — authorable via edit, silently lost on create
(create-then-edit is a workaround, but these are real bugs to fix)
4. **(Rail, `cadence`)** — in `_RAIL_FIELDS` but not passed by `create_l2_entity` (`editor.py:272-335`).
5. **(Rail, `amount_typical_range`)** — same.
6. **(Rail, `firings_typical_per_period`)** — same.
7. **(Chain, per-child `fan_in` / `expected_parent_count` on CREATE)** — create reads a
   non-existent chain-level flag and applies it to all children; the per-child
   `chain_children` widget is edit-only. Fix: have the create branch consume the
   per-child `ChainChildSpec` tuple from `_coerce_form` (the same path edit uses).

### No gaps (confirmed exhaustively covered)
Account, AccountTemplate, Rail edit-path (all fields incl. union RoleExpression,
Variable, aging, posted_requirements, metadata_value_examples, bundles_activity),
TransferTemplate `leg_rail_xor_groups` (edit-only — author leg_rails first), LimitSchedule
(incl. Inbound + composite triple key), Theme/Persona raw-YAML singletons.

## Implications for AI.2

- **Build, don't skip** (Lock 1 + `feedback_build_verbs_not_skip`): the 3 hard-gap
  UIs + 4 create-path fixes are AI.2.x sub-tasks. None may be deferred.
- **StudioEditorDriver verbs** key off this inventory:
  `create_account`, `create_account_template`, `create_rail` (subtype-aware, all
  fields incl. the 3 create-path-dropped ones), `create_transfer_template` (with
  `transfer_key` + `leg_rail_xor_groups`), `create_chain` (per-child `ChainChildSpec`
  incl. mixed-cardinality), `create_limit_schedule`, `set_description`,
  `set_role_business_day_offsets`, `save_l2_to_path`, plus a bulk
  `create_l2(reference)` that walks entities in dependency order.
- **Dependency order** for `create_l2`: AccountTemplates → Accounts → Rails →
  TransferTemplates → Chains → LimitSchedules → top-level fields.
- The serializer is not a gap — every field round-trips once it's settable.
