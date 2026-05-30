# Install

`recon-gen` is a single PyPI package with **opt-in extras** — pick
the extras matching what you actually run, since the CLI surface is
broad (emit JSON, deploy to AWS, seed a demo DB, render audit PDFs,
build the docs site) and the dependency footprint of each surface is
unrelated.

The bare install is intentionally tiny (Click + PyYAML + the Graphviz
Python wrapper) so consumers who just want to emit JSON for their own
deploy pipeline don't pull boto3 / reportlab / mkdocs / DB drivers.

## What each extra unlocks

| Extra | Adds | Unlocks |
|---|---|---|
| *(none)* | `click`, `pyyaml`, `graphviz` | `recon-gen json apply` — emits JSON to disk for hand-off to your own pipeline |
| `[deploy]` | `boto3`, `botocore[crt]` | `json apply --execute` (push to AWS QuickSight); `json clean --execute` (sweep `ManagedBy:recon-gen` resources). `botocore[crt]` is needed for AWS SSO (`aws sso login`) auth |
| `[demo]` | `psycopg2-binary` | `schema apply --execute`, `data apply --execute`, `data refresh --execute` against PostgreSQL 17+ |
| `[demo-oracle]` | `oracledb` (thin mode) | Same `--execute` verbs against Oracle 19c+. No Oracle Instant Client install needed |
| `[audit]` | `reportlab`, `pypdf`, `pyhanko` | `audit apply --execute -o report.pdf` (regulator-ready PDF). `pyhanko` covers both auto-signing (when `config.yaml` carries a `signing:` block) and the empty reviewer-signature widgets that land on every render |
| `[docs]` | `mkdocs`, `mkdocs-material`, `mkdocstrings`, `mkdocs-click`, `mkdocs-macros-plugin`, `graphviz` | `docs apply` / `docs serve` to build or live-preview this handbook |
| `[dev]` | All of the above plus `pytest`, `pytest-cov`, `pyright`, `boto3-stubs`, `build`, `twine` | Full developer environment — runs every test suite + type-check |
| `[e2e]` | `pytest`, `pytest-xdist`, `boto3`, `botocore[crt]`, `playwright` | End-to-end test suite (browser + API) against deployed dashboards. Also requires a one-time `playwright install webkit` to download the browser binary |

## Common shapes

### "I just want the JSON"

```bash
pip install recon-gen
recon-gen json apply -c config.yaml -o out/
```

Writes `out/*.json` for the four bundled apps. Your own pipeline picks
them up. No AWS credentials or DB drivers needed.

### "I want to deploy to AWS"

```bash
pip install "recon-gen[deploy]"
recon-gen json apply -c config.yaml -o out/ --execute
```

`--execute` does a delete-then-create against AWS QuickSight using
the credentials your environment already has (env vars, `~/.aws/`,
SSO session, instance profile).

### "I want to seed the demo database"

```bash
pip install "recon-gen[deploy,demo]"           # PostgreSQL 17+
pip install "recon-gen[deploy,demo,demo-oracle]"  # add Oracle 19c+
```

Then:

```bash
recon-gen schema apply -c config.yaml --execute
recon-gen data   apply -c config.yaml --execute
recon-gen data   refresh -c config.yaml --execute
recon-gen json   apply -c config.yaml -o out/ --execute
```

### "I want to render the audit PDF"

```bash
pip install "recon-gen[deploy,demo,audit]"
recon-gen audit apply -c config.yaml --execute -o report.pdf
```

For digitally-signed PDFs, [add a `signing:` block to
`config.yaml`](../handbook/audit.md) — the same `[audit]` extra
covers it (no separate install step). pyHanko picks up the PEM key +
cert at render time and stamps a CMS signature on the cover page.

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

(`uv sync` always installs the `[dev]` group + any extras you ask
for. `--frozen` fails if `uv.lock` is out of date — drop it locally if
you're iterating on `pyproject.toml`.)

Two non-Python tools the test session uses (the `pytest` sessionstart
hook gates on both): `pyright` (a `[dev]` dep — `uv sync` brings it) and
`biome` (the App 2 JS linter). Biome isn't pip-installable here — the
`biome-js` PyPI wrapper ships only a linux-x86_64 wheel — so install it
your platform's way:

```bash
brew install biome          # macOS / Linuxbrew
# or: see https://biomejs.dev/guides/getting-started/ for npm / scoop /
# nix / mise / standalone-binary options
```

If `biome` isn't on `PATH` the JS-lint gate just skips locally (CI
always runs it via the `biomejs/setup-biome` action) — your tests still
pass, you just won't catch a JS-lint regression before pushing.

## Quoting note

The square brackets in `recon-gen[demo,audit]` are shell
metacharacters — quote them or your shell will interpret them as glob
patterns:

```bash
pip install "recon-gen[demo,audit]"     # works in bash + zsh
pip install 'recon-gen[demo,audit]'     # also works
pip install recon-gen\[demo,audit\]     # also works
```

Without quoting you'll get `zsh: no matches found: recon-gen[demo,audit]`
or pip will install only the bare package, silently dropping the
extras.
