# BE.4 Phase B — tests/unit/ review doc

**Status**: Phase B `tests/unit/` slice complete. 45 of 52 hits migrated;
7 flagged below for principal review. The 7 are all CATEGORY 6 judgment
calls where the inline literal *coincidentally* matches a src constant
but the test's intent is not coupled to that constant.

## Slice summary

- Initial hits: **52**
- Migrated: **45**
- Flagged for review: **7** (all CATEGORY 6 illustrative-literal pattern)

## Proposed Phase C action for each flagged hit

Default disposition: add a per-line
`# typing-smell: ignore[no-inline-production-constants]: <why>` comment so
the lint stays loud for genuine duplication and quiet for these illustrative
fixtures. (Alternative: leave as-is and rely on the file-level allowlist
mechanism the BE.2 check exposes — depends on what shape Phase C lands on
for the lint's escape hatch.)

---

### 1. `tests/unit/test_column_human_name.py:81`

```python
def test_default_for_calc_field_shaped_name(self) -> None:
    # Calc field-shaped names (no underscores) round-trip cleanly.
    c = ColumnSpec(name="drift", type="DECIMAL")
    assert c.human_name == "Drift"
```

- Inline literal: `"Drift"`
- Matches src constant: `_DRIFT_NAME` at `src/recon_gen/apps/l1_dashboard/app.py:261`
- Reasoning: The test is exercising `ColumnSpec._smart_title("drift")` →
  `"Drift"`. The literal is the title-case output of a generic column
  name; coincidentally matches `_DRIFT_NAME` because that constant is
  also the title-case form of the same word. Importing `_DRIFT_NAME`
  here would be misleading — the test would survive a `_DRIFT_NAME`
  rename to `"Sub-Ledger Drift"` because the column-name math doesn't
  flow through the dashboard sheet name.
- Proposed Phase C: ignore with reason `column-title-case output, not the L1 sheet name`.

### 2. `tests/unit/test_l2_loader_theme.py:98` + `:104`

```python
_FULL_THEME_BLOCK = dedent("""\
    theme:
      ...
      primary_bg: "#FFFFFF"
      ...
""")

def test_theme_full_block_round_trips(tmp_path: Path) -> None:
    ...
    assert t.primary_bg == "#FFFFFF"
    ...
    assert t.success_fg == "#FFFFFF"
```

- Inline literal: `"#FFFFFF"` (x2)
- Matches src constant: `_WHITE` at `src/recon_gen/common/theme.py:65`
- Reasoning: The test's fixture YAML hand-authors `primary_bg: "#FFFFFF"`
  + `success_fg: "#FFFFFF"`. The test asserts the loader preserves
  those values verbatim. The match to `_WHITE` is coincidental — pure
  white is just a common hex value. If src `_WHITE` were renamed to
  `"#F0F0F0"`, this fixture should still load `"#FFFFFF"` as written;
  importing `_WHITE` would make the test fail for the wrong reason.
- Proposed Phase C: ignore with reason
  `theme yaml roundtrip fixture, not coupled to _WHITE`.

### 3. `tests/unit/test_rich_text.py:35`

```python
def test_color(self) -> None:
    assert rt.inline("hi", color="#2E5090") == '<inline color="#2E5090">hi</inline>'
```

- Inline literal: `"#2E5090"`
- Matches src constant: `_DARK_BLUE` at `src/recon_gen/common/theme.py:54`
- Reasoning: Test of `rt.inline()`'s color-attribute passthrough. Any
  valid `#RRGGBB` string would prove the behavior; the test author
  picked `#2E5090` as an illustrative hex. Not coupled to theme.
- Proposed Phase C: ignore with reason
  `illustrative hex for inline() passthrough, not theme constant`.

### 4. `tests/unit/test_rich_text.py:297`

```python
def test_bold(self) -> None:
    assert rt.bold("Drift") == "<b>Drift</b>"
```

- Inline literal: `"Drift"`
- Matches src constant: `_DRIFT_NAME` at `src/recon_gen/apps/l1_dashboard/app.py:261`
- Reasoning: Test of `rt.bold()` wrapping arbitrary text in `<b>`.
  `"Drift"` is illustrative input; could be any non-empty string. Not
  coupled to the L1 dashboard sheet name.
- Proposed Phase C: ignore with reason
  `illustrative input for rt.bold(), not the L1 sheet name`.

### 5. `tests/unit/test_runner_skeleton.py:538`

```python
cmd_env = runner._layer_command(
    "deploy", tmp_path, variant_env=variant_env,
)
...
assert "recon-gen" in cmd[0]
```

- Inline literal: `"recon-gen"`
- Matches src constant: `MANAGED_TAG_VALUE` at `src/recon_gen/common/cleanup.py:23`
- Reasoning: The assertion verifies the spawned command's binary is the
  `recon-gen` CLI. The string `"recon-gen"` is the published package /
  console-script name (per `pyproject.toml::[project] name`).
  `MANAGED_TAG_VALUE` happens to be the same string because the
  cleanup tag value uses the package name — but importing it here
  would suggest the runner test is checking tag values, which it
  isn't. (Latent question for Phase C: should there be a single
  exported `PACKAGE_NAME` / `CLI_BINARY` constant that both
  `cleanup.py` and the runner reference? That would tighten the
  coupling — but it's a src-side promotion, deferred per Phase B
  constraints.)
- Proposed Phase C: ignore with reason
  `CLI binary name; package-name not tag-value coupling`.
  OR: promote `PACKAGE_NAME` to `src/recon_gen/__init__.py`, have
  `MANAGED_TAG_VALUE = PACKAGE_NAME`, then migrate this test to
  import the new constant.

### 6. `tests/unit/test_studio_editor_routes.py:1655`

```python
resp = c.post(
    "/l2_shape/instance/edit",
    data={
        ...
        "accent": "#1f4e79",
    },
    ...
)
...
# The form re-renders with the operator's partial input + the
# validator error inline. The accent color survives so the
# operator doesn't have to retype.
assert "#1f4e79" in resp.text
```

- Inline literal: `"#1f4e79"`
- Matches src constant: `_BUNDLE_EDGE_COLOR` at `src/recon_gen/common/l2/topology.py:187`
- Reasoning: Test posts a hex color as form input + asserts it
  round-trips into the re-rendered form. The literal is operator
  input, not the bundle-edge-color constant. Match is coincidental.
- Proposed Phase C: ignore with reason
  `operator form-input hex, not the bundle-edge-color theme constant`.

---

## Notes for Phase C

- All 7 flagged cases share the same shape: an illustrative hex
  / domain string that happens to collide with a src constant whose
  semantic meaning differs.
- No src promotions needed unless the principal wants the
  `PACKAGE_NAME` shared constant (item 5 above).
- The lint stays valuable on the 45 migrated hits; these 7 are noise
  in the BE.2 check's signal/noise denominator.
