# Audit Reconciliation Report

*Regulator-ready PDF reconciliation report generated directly from
the per-instance L1 invariant matviews. Currently rendered against
**{{ vocab.institution.name }}** ({{ l2_instance_name }}).*

The **`audit` artifact group** generates a printable PDF that
covers the same L1 SHOULD-constraints the
[L1 Reconciliation Dashboard](l1.md) surfaces — drift, overdraft,
limit breach, stuck pending, stuck unbundled, supersession — but
shaped for a regulator or external auditor sitting outside the
operator's QuickSight account. The PDF is the artifact you hand off;
the dashboard is the artifact you operate against.

The report is generated **directly from the database** via
`reportlab` — no QuickSight in the loop. The same matviews the L1
dashboard reads supply the row counts, magnitudes, and per-day
walks, so the numbers in the PDF and the numbers on the dashboard
are computed from the same sources at the same point in time.

## What the report contains

A single PDF with bookmarks + a dot-leader table of contents. Layer
order, top to bottom:

- **Cover page** — institution name (from the L2 persona block),
  reporting period, generation timestamp, L2 instance fingerprint
  short hash, and a `Source-data provenance` panel naming the four
  inputs the report binds to.
- **Table of contents** — every section heading + sub-heading with
  resolved page numbers.
- **Executive summary** — period totals: transaction count,
  transfer count, dollar volume gross + net, exception counts per
  L1 invariant. Stuck-state and supersession counts are flagged
  with `*` to mark them as current-state (not period-filtered).
- **Per-invariant violation tables** — one section per L1
  invariant. Parent (L2 `Account` singleton) violations get
  per-row detail; child (template-materialized) violations roll up
  per `parent_role` so the report stays bounded. Sections:
    - Drift
    - Overdraft
    - Limit Breach
    - Stuck Pending
    - Stuck Unbundled
    - Supersession Audit (aggregate counts + in-window detail)
- **Per-account-day Daily Statement walks** — one page per
  `(account_id, business_day)` pair. Five KPIs (Opening / Debits /
  Credits / Closing stored / Drift) sourced from the
  `{{ l2_instance_name }}_daily_statement_summary` matview, plus the day's
  transactions from `{{ l2_instance_name }}_current_transactions`. Walks render
  for every drifted account-day plus every internal-scope L2
  `Account` singleton (parent accounts) on every day in the
  period — clean walks are themselves auditor-relevant evidence
  of correctness. External counterparty singletons are out of
  scope and don't get walks.
- **Sign-Off** — two stacked attestation blocks. The **System
  attestation** is a themed-panel table (institution, period,
  generated-by version, generated-at timestamp, L2 instance label,
  long-form provenance fingerprint). The **Reviewer attestation**
  is the human surface — a fillable Notes / Exceptions box plus
  two empty PDF signature widgets the reviewer's PDF reader can
  fill in.
- **Provenance Appendix** — three sub-sections covering matview
  evidence sidecars, the one-shot `audit verify` command, and a
  manual-recompute Python recipe. Embedded as a PDF attachment is
  a byte-exact copy of the L2 YAML the report bound to and the
  `verify-provenance.py` recipe script.

Every page carries a footer with the `recon-gen` version,
generation timestamp, page number, and short-form provenance hash.

For the L1 invariant definitions themselves — what each view
contains, when a row qualifies as a violation, refresh contract
— see the [L1 Invariants reference](../L1_Invariants.md).

## Generating the report

```bash
# Emit the report's Markdown source to stdout for quick inspection
recon-gen audit apply -c config.yaml --l2 path/to/instance.yaml

# Same emit, then write the PDF
recon-gen audit apply -c config.yaml --l2 path/to/instance.yaml \
    --execute -o report.pdf
```

The `--l2 PATH` flag is the same shape as the other artifact
groups; without it the report binds to the bundled
`spec_example.yaml`. The `-c PATH` config carries the database
URL the report queries against (`demo_database_url`) plus the
optional `signing:` block (see below). When `demo_database_url`
is unset, the renderer falls back to a skeleton-mode placeholder
in every numeric cell so the layout is still previewable.

**Period default: a 7-day window ending yesterday.** `[today − 7,
today − 1]` inclusive. Override via the typed `--period` flag:

```bash
recon-gen audit apply -c config.yaml --l2 instance.yaml \
    --period 2026-04-01..2026-04-30 \
    --execute -o april-report.pdf
```

The `--period` flag accepts several shapes: `trailing:N` ("last N
days ending yesterday"; default is `trailing:7`), `yesterday`,
`today`, `YYYY-MM-DD..YYYY-MM-DD` for an explicit closed-closed
range, or a single `YYYY-MM-DD` for a one-day report. Both
endpoints in the range form are inclusive. `--execute` is the
universal opt-in across the CLI — without it, `audit apply` only
emits the Markdown rendering (handy for content review without
committing to a real PDF write).

`audit clean -o report.pdf` removes the file. Like the other
groups, it dry-runs by default; pass `--execute` to actually
unlink.

## Provenance fingerprint

Every PDF carries a cryptographic fingerprint over **four inputs**
that, taken together, fully determine the report's contents:

1. `{{ l2_instance_name }}_transactions` — every row up to the
   high-water-mark `MAX(entry)` at audit time.
2. `{{ l2_instance_name }}_daily_balances` — every row up to its own
   `MAX(entry)`.
3. The L2 instance YAML — file bytes, verbatim.
4. The `recon-gen` code identity — `v{version}+g{git_short}`
   when running from a git checkout, just `v{version}` for pip
   installs.

Matviews are deliberately excluded. They're derived data, and
binding the fingerprint to derived state would conflate "the
source data changed" with "we recomputed the matview SQL
differently". The four inputs above are authoritative — anything
else you can recompute from them.

### Canonical-bytes rules

The base-table hashes are reproducible without touching the
generator's source by following these rules:

- **Column discovery is runtime, alphabetical-by-lowercased-name.**
  Pulled from `cur.description` (DB-API 2.0); lowercasing handles
  PostgreSQL (lowercase identifiers) vs Oracle (uppercase) so the
  column order is portable. Hardcoded column lists were rejected
  as a footgun — a new column added later would silently be
  excluded from the hash while the fingerprint still claimed
  full-row coverage.
- **Per-cell canonicalization** (`common/provenance.py::canonical_value`):
    - `Decimal` and numerics: `str(v)`
    - `date` / `datetime`: `isoformat()`
    - `bool`: `b"1"` / `b"0"`
    - `None`: empty bytes
- **Field separator: `\x1f`** (unit separator). **Row separator:
  `\x1e`** (record separator). Both control codes can't appear in
  the schema's data, so escaping isn't needed.
- **Composite hash**: SHA256 over the per-source values joined
  with labeled lines:
  ```
  tx_hwm={n}
  tx_sha={hex}
  bal_hwm={n}
  bal_sha={hex}
  l2_sha={hex}
  code={identity}
  ```

The composite hash surfaces in four places in the PDF:

- **Cover page** `Source-data provenance` panel (long form)
- **Per-page footer** (short form — first 8 hex chars)
- **Sign-off page** system-attestation row (long form)
- **Provenance Appendix** per-source breakdown table (long form)

The full design is documented inline in
`src/recon_gen/common/provenance.py`.

## Verifying the fingerprint

```bash
recon-gen audit verify report.pdf -c config.yaml \
    --l2 path/to/instance.yaml
```

Extracts the embedded `ProvenanceFingerprint` JSON from the PDF's
`/Subject` metadata, recomputes each input from current sources,
and compares.

A subtlety worth knowing: `verify` **recomputes against the
embedded high-water-mark, not the current `MAX(entry)`**. New
rows added since report generation don't trigger a false diff —
the fingerprint only fires when bytes the report actually bound
to have changed.

Exit codes:

- `0` — all four inputs match. Report verifies.
- `1` — at least one input diverged. Per-source diff is printed
  to stderr with embedded vs current values for each diverged
  source.

`audit verify` first sanity-checks that the embedded high-water
marks haven't been truncated below the current `MAX(entry)` —
if the table was rebuilt or rolled back below the report's bind
point, the rows are gone and the report can't be re-verified.

### Manual recompute

The Provenance Appendix carries a ~30-line Python recipe that
reproduces the composite hash without `recon-gen` installed.
That same script is embedded as a PDF attachment named
`verify-provenance.py` (open the PDF in any reader's attachments
panel to download it). The recipe takes the embedded
high-water-marks + code identity as constants and emits the
composite SHA the appendix advertises.

The L2 YAML the report bound to is also attached byte-exact, so
a verifier can independently run `sha256sum <attached-yaml>` and
confirm it matches the embedded `l2_yaml_sha`.

## Auto-signing the PDF

When `config.yaml` carries a `signing:` block, `audit apply
--execute` runs the rendered PDF through pyHanko and applies a
CMS digital signature over the entire byte range. The signature
field is named `QSGSystemSignature` and is the cryptographically
bound machine attestation referenced from the sign-off block.

```yaml
# config.yaml
signing:
  key_path: "path/to/signing-key.pem"
  cert_path: "path/to/signing-cert.pem"
  passphrase_env: "QSG_SIGNING_PASSPHRASE"   # optional
  signer_name: "Audit Pipeline (production)" # optional; defaults to cert CN
```

Field reference (`SigningConfig` in `common/config.py`):

- **`key_path`** — PEM-encoded RSA private key file.
- **`cert_path`** — PEM-encoded X.509 certificate matching the
  key.
- **`passphrase_env`** — optional. Names the environment variable
  holding the key's passphrase if the key is encrypted. Operator
  infrastructure (vaults, secrets managers) stays out of the
  YAML; the YAML only says where to look.
- **`signer_name`** — optional. Free-form display name shown in
  the signature widget. Defaults to the certificate's CN when
  unset.

When the `signing:` block is absent, the PDF ships unsigned — the
provenance fingerprint still binds the report to its source data,
just without a cryptographic countersignature.

!!! note
    Signing is **incremental**. Subsequent reviewers stack
    additional signatures on top via Adobe Acrobat / pyHanko / any
    compliant tool — the document is deliberately silent on how
    many signatures are required. There's no "1 of N" advertised in
    the rendered PDF.

## Certificate creation

For testing, generate a self-signed PEM RSA key + cert pair with
OpenSSL:

```bash
# Generate a 2048-bit RSA private key (PEM, unencrypted)
openssl genpkey -algorithm RSA -out signing-key.pem \
    -pkeyopt rsa_keygen_bits:2048

# Generate a self-signed X.509 cert valid for 100 years
openssl req -new -x509 -key signing-key.pem -out signing-cert.pem \
    -days 36500 \
    -subj "/CN=recon-gen audit test signing/O=recon-gen test fixtures"
```

The bundled test fixture in `tests/audit/fixtures/test-signing-{key,cert}.pem`
was created with exactly this recipe (`CN=recon-gen audit
test signing`, `O=recon-gen test fixtures`). Copy it as a
starting point if you want a working `signing:` block to validate
the pipeline end-to-end before swapping in a real cert.

To encrypt the private key with a passphrase:

```bash
openssl genpkey -algorithm RSA -out signing-key.pem \
    -pkeyopt rsa_keygen_bits:2048 \
    -aes256
```

Then set `passphrase_env: QSG_SIGNING_PASSPHRASE` in
`config.yaml` and export the passphrase before running
`audit apply --execute`:

```bash
export QSG_SIGNING_PASSPHRASE="..."
recon-gen audit apply -c config.yaml --l2 instance.yaml \
    --execute -o report.pdf
```

!!! warning "Self-signed certificates and Adobe trust chains"
    Adobe Reader will display **"At least one signature has
    problems"** when verifying a PDF signed with a self-signed
    certificate. That isn't a generator bug — Adobe distrusts any
    cert not on the Adobe Approved Trust List (AATL). The
    signature is cryptographically valid; Adobe just won't extend
    its trust chain to a cert it doesn't recognize.

    For production, use a certificate issued by a CA on the AATL
    (DigiCert, GlobalSign, Entrust, IdenTrust, etc.). The PDF
    signing path is identical — only the cert source differs. The
    AATL list is published at
    [helpx.adobe.com/acrobat/kb/approved-trust-list1.html](https://helpx.adobe.com/acrobat/kb/approved-trust-list1.html).

## Reviewer countersignatures

The sign-off page carries two empty PDF signature widgets below
the Notes / Exceptions box. The notes box is a fillable AcroForm
text field; the signature widgets are unsigned by default.

A reviewer (auditor, second approver, regulator) opens the PDF in
their PDF reader, fills the Notes box if they have comments, and
clicks one of the empty signature widgets to apply their own
signature. pyHanko's incremental signing leaves the form fields
unlocked by default, so the system seal applied first does NOT
prevent the reviewer from filling the box or signing the widget.

When the reviewer signs, their PDF reader writes a new revision
on top of the system-signed PDF. Both signatures verify
independently:

- The **system signature** (`QSGSystemSignature`) covers the
  rendered report bytes.
- The **reviewer signature(s)** cover the report bytes plus the
  system signature plus any notes filled in by that reviewer.

This is the same multi-signature workflow Adobe and pyHanko both
support — the generator just provides the empty widgets so the
reviewer doesn't have to add them in their PDF reader.

## See also

- [L1 Invariants](../L1_Invariants.md) — formal definitions of
  the SHOULD-constraints whose violations the report tabulates.
- [L1 Reconciliation Dashboard](l1.md) — the operator-facing
  dashboard the audit report is the printable counterpart to.
- [Schema v6 — Data Feed Contract](../Schema_v6.md) — the base
  table contract whose rows the provenance fingerprint binds to.
- [Double-entry posting](../concepts/accounting/double-entry.md) —
  the bookkeeping invariant most of the L1 SHOULD-constraints
  rest on.
