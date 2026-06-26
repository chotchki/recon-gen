# How do I publish docs against my L2?

The unified docs site renders against any L2 institution YAML. Point
it at yours and the SAME source produces a different rendered handbook
(vocab, diagrams, intro prose) with zero markdown edits.

## Render against your L2

```bash
# Build the site to ./site/ — bound to your L2.
recon-gen docs apply --l2 run/my-l2.yaml -o site

# Or live-reload preview at http://localhost:8000:
recon-gen docs serve --l2 run/my-l2.yaml
```

The rendered `site/` directory is a static site you can publish to
GitHub Pages, S3 or any static host that runs HTTP.

## Ship a portable site (open via `file://`)

By default the site uses pretty URLs (`/scenario/`) which need an
HTTP server to map the slug to its `index.html`. For a wiki / USB
stick / zip-attachment build that opens directly in a browser:

```bash
recon-gen docs apply --portable --l2 run/my-l2.yaml -o portable-site
```

`--portable` does two things:

- Flips `use_directory_urls: false` so every page emits as
  `<slug>/index.html` and links resolve via `file://`.
- Post-processes the rendered `stylesheets/qs-graphviz-wasm.js` to
  inline the WASM diagram bundle directly into the script (browsers
  block ES module imports under `file://` for security; inlining
  bypasses that). Diagrams render without a web server.

The CLI echoes the absolute path to the entry-point `index.html` you
can hand to a stakeholder. Zip the directory and ship — recipients
double-click and read.

## Advanced: hand-build with your own mkdocs config

If you want to integrate with an existing mkdocs build pipeline (a
custom theme, an extra plugin set, your own CI), extract the source:

```bash
recon-gen docs export -o /tmp/my-docs --l2 run/my-l2.yaml
QS_DOCS_L2_INSTANCE=/Users/me/run/my-l2.yaml \
    mkdocs build -f /tmp/my-docs/mkdocs.yml
```

## What changes per L2 instance

Three substitution surfaces:

- **Vocab strings** ({% raw %}`{{ vocab.institution.name }}`,
  `{{ vocab.institution.acronym }}`{% endraw %}, etc.) substitute
  throughout the prose. The built-in `sasquatch_pr` vocabulary
  carries Sasquatch flavor; any other instance falls back to a
  neutral "Your Institution" voice derived from the L2 description's
  first proper-noun-shaped run.
- **L2-driven diagrams** ({% raw %}`{{ diagram("l2_topology", kind="accounts") }}`,
  `{{ diagram("dataflow", app="l1_dashboard") }}`{% endraw %})
  regenerate from your L2's structural data — accounts + rails +
  chains laid out visually for your specific institution.
- **The page set** stays the same; the CONTENT of each page reflects
  your L2.

## What stays the same

- **Conceptual diagrams** (`docs/_diagrams/conceptual/*.dot`) are
  hand-authored teaching aids. They don't change per L2.
- **Reference material** describing the L1 invariants, the SPEC and
  the Schema v6 contract are persona-blind and identical across
  renders.
- **Walkthroughs** describing per-sheet operator flows are written
  against the dashboard structure (which is the same regardless of
  L2). Body examples may still mention Sasquatch-flavored
  identifiers (gl-1010, Bigfoot Brews) where the example is
  illustrative; those are tagged for future cleanup or replacement
  with vocab pulls.

## Adding your institution's flavor

If your institution wants richer flavor than the neutral fallback,
the primary path is the optional `persona:` block on your L2
YAML — institution name + acronym, upstream stakeholders, GL
account labels, merchant names, free-form flavor literals. The
handbook templates substitute via `vocab` Jinja references at render time.

See [How do I brand my handbook prose?](how-do-i-brand-my-handbook-prose.md)
for the full block shape, the field-by-field map to handbook
surfaces and the neutral fall-through behavior when fields are
omitted.

The Investigation walkthroughs' worked-example admonitions (the
`??? example "Worked example: <fixture>"` blocks naming specific
accounts) are still wired by *role* in
`common/handbook/vocabulary.py::_sasquatch_pr_vocabulary` rather
than from the YAML — those need a small Python addition for now.
A future extension will likely lift them into the persona block.
