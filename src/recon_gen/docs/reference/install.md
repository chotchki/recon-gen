# Install

`recon-gen` is one PyPI package with opt-in extras. Pick the extra that
matches what you actually run — the CLI surface is broad (emit SQL,
serve the self-hosted dashboards, seed a demo DB, render audit PDFs,
build the docs site) and each of those surfaces drags an unrelated
dependency footprint. There are three extras: `[prod]`, `[dev]` and
`[e2e]`.

> The pre-v15 `[quicksight]` extra (AWS QuickSight deploy via boto3) is
> GONE — the self-hosted dashboards (`recon-gen dashboards` /
> `recon-gen studio`) are the supported renderer, no AWS account.

The bare install stays tiny on purpose — Click + PyYAML + the Graphviz
Python wrapper + DuckDB — so anyone who just wants to emit the SQL for
their own pipeline doesn't pull reportlab / mkdocs / DB drivers. DuckDB
rides along in the base because every `recon-gen` invocation imports it
as the default local dialect.

[See it live](https://recon-gen-spec.hotchkiss.io/)

## What each extra unlocks

| Extra | Adds | Unlocks |
|---|---|---|
| *(none)* | `click`, `pyyaml`, `graphviz`, `duckdb` | `recon-gen schema apply` / `data apply` (no `--execute`) — emit SQL to disk for hand-off to your own pipeline. No DB drivers needed |
| `[prod]` | The App2 server stack (`starlette`, `uvicorn`, `httpx`, `python-multipart`); the auth libs (`authlib`, `pyjwt`, `itsdangerous`); `psycopg[binary,pool]` (PostgreSQL) + `oracledb` thin mode (Oracle); `reportlab` + `pypdf` + `pyhanko`; `mkdocs` + `mkdocs-material` + `mkdocs-click` + `mkdocs-macros-plugin` | Every operator verb. The self-hosted App2 server (`recon-gen dashboards` / `recon-gen studio`); `schema/data apply --execute` against PostgreSQL 17+ OR Oracle 19c+; `audit apply --execute` (regulator-ready PDF); `docs apply` (build this handbook) + `docs serve` (live-preview this handbook with reload). `oracledb` thin mode needs no Oracle Instant Client install |
| `[dev]` | The test + build tooling — `pytest`, `pytest-xdist`, `pyright`, `testcontainers`, `playwright`, `build`, `twine` — plus the prod runtime deps it tests against | Full developer environment — runs every test suite + type-check. Note: the docs-build deps (`mkdocs` + plugins) live in `[prod]`, not `[dev]`, so use `uv sync --all-extras` when you also want `docs apply` |
| `[e2e]` | `pytest`, `pytest-xdist`, `playwright` + the App2 server stack | End-to-end browser test suite against the self-hosted dashboards. Also requires a one-time `playwright install webkit` to download the browser binary |

Pre-BS.6 the split was per-feature (`[deploy]` / `[demo]` /
`[demo-oracle]` / `[audit]` / `[docs]` …). Operators always wanted one
of "production run", "dev run" or "e2e run", so the granularity was
confusion without payoff — collapsed to one knob per persona.

## Common shapes

### "I just want the SQL"

```bash
pip install recon-gen
recon-gen schema apply -c config.yaml -o out/
recon-gen data   apply -c config.yaml -o out/
```

Writes the schema DDL + seed SQL to `out/`. Your own pipeline picks
them up. No DB drivers or credentials needed.

### "I want to see the dashboards"

```bash
pip install "recon-gen[prod]"
recon-gen dashboards -c config.yaml
```

Serves the four bundled apps (L1 Dashboard / L2 Flow Tracing /
Investigation / Executives) over the self-hosted HTMX renderer — no
AWS account, offline-first. `recon-gen studio` adds the L2 editor +
data-shaping panel + diagram viewer on top for the offline iteration
loop.

### "I want to seed the demo database"

```bash
pip install "recon-gen[prod]"
```

One install covers both backends — `[prod]` ships `psycopg[binary,pool]`
(PostgreSQL 17+) and `oracledb` thin mode (Oracle 19c+, no Oracle
Instant Client needed). Then:

```bash
recon-gen schema apply -c config.yaml --execute
recon-gen data   apply -c config.yaml --execute
recon-gen data   refresh -c config.yaml --execute
```

### "I want to render the audit PDF"

```bash
pip install "recon-gen[prod]"
recon-gen audit apply -c config.yaml --execute -o report.pdf
```

For digitally-signed PDFs, [add an `audit.signing:` block to
`config.yaml`](../handbook/audit.md) — `[prod]` already covers it (no
separate install step). pyHanko picks up the PEM key + cert at render
time and stamps a CMS signature on the cover page. Every render also
lands empty reviewer-signature widgets on the page, whether or not an
`audit.signing:` block is present.

### "I want to hack on the source"

```bash
git clone https://github.com/chotchki/recon-gen
cd recon-gen
uv sync --all-extras            # everything, locked from uv.lock
.venv/bin/recon-gen --help
.venv/bin/pytest
```

For a leaner dev install, pick only the extras you need:

```bash
uv sync --frozen --extra dev --extra prod
```

`uv sync` always installs the `[dev]` dependency group + any extras you
ask for. `--frozen` fails if `uv.lock` is out of date — drop it locally
if you're iterating on `pyproject.toml`.

Two non-Python tools the test session uses (the `pytest` sessionstart
hook gates on both): `pyright` (a `[dev]` dep — `uv sync` brings it) and
`biome` (the App2 JS linter). Biome isn't pip-installable here — the
`biome-js` PyPI wrapper ships only a `manylinux_2_28_x86_64` wheel (no
macOS / arm64 / sdist) — so install it your platform's way:

```bash
brew install biome          # macOS / Linuxbrew
# or: see https://biomejs.dev/guides/getting-started/ for npm / scoop /
# nix / mise / standalone-binary options
```

If `biome` isn't on `PATH` the JS-lint gate just skips locally (CI
always runs it via the `biomejs/setup-biome` action) — your tests still
pass, you just won't catch a JS-lint regression before pushing.

## Quoting note

The square brackets in `recon-gen[prod]` are shell
metacharacters — quote them or your shell will interpret them as glob
patterns:

```bash
pip install "recon-gen[prod]"     # works in bash + zsh
pip install 'recon-gen[prod]'     # also works
pip install recon-gen\[dev,e2e\]     # also works
```

Without quoting you'll get `zsh: no matches found: recon-gen[prod]`
or pip will install only the bare package, silently dropping the
extras.
