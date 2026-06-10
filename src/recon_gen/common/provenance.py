"""Audit-grade provenance fingerprint primitives.

Binds a generated artifact (e.g. an audit PDF) to its source data
via SHA256 over base table rows + L2 yaml file bytes + code identity.
Designed to be reusable: ``cli/audit.py`` is the first consumer
(``audit apply`` to compute + embed; ``audit verify`` to recompute +
diff), but any future tool that wants a reproducibility binding for
its output can pull this module in.

Locked design (Phase U.7, updated Phase CW.2):

- Hash the **base tables** + external inputs, NOT matviews.
  Matviews are derived data; their hash drifting from a recompute
  is a *technical* signal (matviews need refresh, schema drift)
  but isn't authoritative for "what was this report bound to".
  CW.1 (2026-06-09) dropped the matview-evidence appendix sidecar
  outright — it was an informational table NOT covered by the
  composite fingerprint + burned ~60% of the audit-apply wall-clock.

- Per-table column set is **discovered at runtime** via
  ``cur.description`` (DB-API 2.0) and sorted alphabetically by
  lowercased name. Hardcoded column lists were a footgun: a new
  column added to a base table would silently be excluded from the
  hash, producing a fingerprint that claimed "binds to all source
  data" while missing whatever the new column carried.

- Composite fingerprint = SHA256 over the per-source values
  concatenated in a fixed order (each on its own labeled line).
  ``short`` form (footer) = first 8 hex chars; ``composite_sha`` =
  full 64.

**Phase CW.2 streamed-fingerprint redesign (2026-06-09).** The
authoritative base-table fingerprint moved from "fetchall + per-cell
Python loop" to "per-row SHA-256 in the engine, stream digests, fold
via Python ``h.update()``". Measured 19.7s → 1.32s on a 3.27M-row
DuckDB dataset. Byte-identical to ``sha256(string_agg(rh ORDER BY
entry))`` by Merkle-Damgård. Per-dialect canonicalization templates
+ the four maintainer-signed locks: see
``docs/audits/cw_0_audit_pdf_perf_locks.md``.

Legacy v1 callsites (PDFs emitted before CW.2) verify against the
frozen ``legacy_hash_table_rows_v1`` Python ladder kept below as a
deprecated-for-new-code routine — ``audit verify`` dispatches on
the embedded ``provenance_format_version``.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon_gen.common.sql.dialect import Dialect, column_name

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2.primitives import L2Instance


def l2_fingerprint_placeholder() -> str:
    """Long-form fingerprint placeholder for the no-DB code path.

    Used on the cover-page provenance block + sign-off page when an
    artifact ran without a DB connection configured (skeleton mode —
    no DB queries, no real fingerprint to compute). When the DB is
    wired the renderers receive a ``ProvenanceFingerprint`` and
    substitute its real ``composite_sha`` instead.
    """
    return "<pending — see Phase U.7>"


def short_fingerprint_placeholder() -> str:
    """Short-form fingerprint placeholder for the per-page footer.

    Distinct compact stand-in (vs the long-form ``<pending>``) so a
    sweep that resolves one when fingerprints land doesn't
    accidentally rewrite the other.
    """
    return "pending"


# CW.3 — provenance_format_version sentinels. Bumping the version
# means the per-field SHA values are computed by a different algorithm
# (the per-dialect SQL streamed fingerprint vs the legacy Python ladder).
# Pre-CW PDFs have no version field in their embedded JSON blob, so
# `from_dict` treats `missing → 1`. CW emissions stamp version 2.
PROVENANCE_FORMAT_VERSION_LEGACY = 1
PROVENANCE_FORMAT_VERSION_CW = 2
_CURRENT_PROVENANCE_FORMAT_VERSION = PROVENANCE_FORMAT_VERSION_CW


@dataclass(frozen=True)
class ProvenanceFingerprint:
    """The four base inputs that fully determine a generated artifact.

    Locked per U.7: hash the base tables (transactions +
    daily_balances) bounded by their high-water-mark ``entry`` ids,
    plus the L2 instance YAML and the code identity. Matviews are
    deliberately excluded — they're derived data; a fingerprint over
    them would conflate "the source data changed" with "we
    recomputed the matview SQL differently", and the auditor needs
    to bind the report to the AUTHORITATIVE source.

    ``composite_sha`` is the SHA256 of the per-source values
    concatenated in a fixed order; ``short`` is the first 8 hex
    chars (footer). The dict-form serializes to JSON for embedding
    in PDF metadata so ``audit verify`` can recompute and compare.

    Phase CW.3 (2026-06-09) added ``provenance_format_version`` (=2
    for CW emissions, =1 for pre-CW PDFs). The version identifies
    which SHA-computation algorithm produced the per-field SHA
    values: v1 = the legacy Python ``canonical_value`` ladder; v2 =
    the streamed per-dialect SQL fingerprint introduced in CW.2.
    ``audit verify`` dispatches on the embedded version so old PDFs
    keep verifying without manual operator intervention.
    """
    transactions_hwm: int
    transactions_sha: str
    balances_hwm: int
    balances_sha: str
    l2_yaml_sha: str
    code_identity: str
    # CW.3 — defaults to the current emission version. Pre-CW PDFs
    # embedded no such field; ``from_dict`` substitutes
    # PROVENANCE_FORMAT_VERSION_LEGACY when the key is missing.
    provenance_format_version: int = _CURRENT_PROVENANCE_FORMAT_VERSION

    @property
    def composite_sha(self) -> str:
        h = hashlib.sha256()
        h.update(f"tx_hwm={self.transactions_hwm}\n".encode())
        h.update(f"tx_sha={self.transactions_sha}\n".encode())
        h.update(f"bal_hwm={self.balances_hwm}\n".encode())
        h.update(f"bal_sha={self.balances_sha}\n".encode())
        h.update(f"l2_sha={self.l2_yaml_sha}\n".encode())
        h.update(f"code={self.code_identity}\n".encode())
        return h.hexdigest()

    @property
    def short(self) -> str:
        return self.composite_sha[:8]

    def to_dict(self) -> dict[str, str | int]:
        return {
            "schema": "qsg-audit-provenance-v1",
            "composite_sha": self.composite_sha,
            "transactions_hwm": self.transactions_hwm,
            "transactions_sha": self.transactions_sha,
            "balances_hwm": self.balances_hwm,
            "balances_sha": self.balances_sha,
            "l2_yaml_sha": self.l2_yaml_sha,
            "code_identity": self.code_identity,
            "provenance_format_version": self.provenance_format_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProvenanceFingerprint":
        if d.get("schema") != "qsg-audit-provenance-v1":
            raise ValueError(
                f"Unrecognized provenance schema: {d.get('schema')!r}"
            )
        # Pre-CW PDFs have no provenance_format_version key — treat
        # them as legacy (v1). CW emissions ship the field explicitly.
        # Distinct from the outer `schema` field (which describes the
        # JSON-blob layout); the layout is unchanged in CW, only the
        # algorithm that produced the per-field SHA values is new.
        format_version = int(
            d.get("provenance_format_version",
                  PROVENANCE_FORMAT_VERSION_LEGACY)
        )
        return cls(
            transactions_hwm=int(d["transactions_hwm"]),
            transactions_sha=str(d["transactions_sha"]),
            balances_hwm=int(d["balances_hwm"]),
            balances_sha=str(d["balances_sha"]),
            l2_yaml_sha=str(d["l2_yaml_sha"]),
            code_identity=str(d["code_identity"]),
            provenance_format_version=format_version,
        )


# -- Streamed base-table fingerprint (CW.2) ---------------------------------


def _row_hash_sql_expr(
    quoted_columns: list[str], dialect: Dialect,
) -> str:
    """Per-dialect SQL expression that hashes one row's canonical bytes.

    Returns a SQL fragment that evaluates to a 64-char lowercase
    hex SHA-256 digest of the row, given the per-column quoted names
    sorted by ``lower(name)``. The canonical form is

        SHA256(concat_ws(chr(31),
            coalesce(CAST("col_a" AS VARCHAR), chr(0)),
            coalesce(CAST("col_b" AS VARCHAR), chr(0)),
            …
        ))

    rendered with the per-dialect hash function. The wrapping
    ``coalesce + CAST`` ensures NULL columns hash distinctly (as
    the ASCII NUL byte) and that every per-dialect ``CAST AS
    VARCHAR`` produces the same string the legacy Python
    ``canonical_value()`` would have written, for the column types
    we actually use (text, numeric, date, timestamp, boolean,
    bigint).

    Per dialect (see ``docs/audits/cw_0_audit_pdf_perf_locks.md``
    Lock 2/3 for the full lockdown):

    - **DuckDB**: native ``sha256(<text>)`` returns hex digest.
    - **PostgreSQL 17+**: ``encode(digest(<text>, 'sha256'), 'hex')``
      via the ``pgcrypto`` extension (operator-locked exception to
      the no-PG-extensions stance, scoped to provenance only —
      MD5 is publicly broken and regulator-rejectable; see Lock 3).
    - **Oracle 19c**: ``RAWTOHEX(STANDARD_HASH(<text>, 'SHA256'))``
      — built-in, no extension needed. Oracle's ``concat_ws`` exists
      but its NULL handling diverges from PG/DuckDB; build the
      concat with ``||`` chains instead. ``LOWER`` wraps the result
      so all three dialects emit lowercase-hex.
    """
    if dialect is Dialect.ORACLE:
        # `||`-chained concat with explicit chr(31) separators; LOWER
        # so the digest matches PG/DuckDB's lowercase-hex convention.
        sep = " || chr(31) || "
        canon_parts = [
            f"coalesce(CAST({c} AS VARCHAR2(4000)), chr(0))"
            for c in quoted_columns
        ]
        canon = sep.join(canon_parts)
        return f"LOWER(RAWTOHEX(STANDARD_HASH({canon}, 'SHA256')))"

    # PG + DuckDB use the same concat_ws shape. coalesce + CAST so
    # NULLs are explicit and concat_ws doesn't skip them.
    canon_args = ", ".join(
        f"coalesce(CAST({c} AS VARCHAR), chr(0))"
        for c in quoted_columns
    )
    canon = f"concat_ws(chr(31), {canon_args})"
    if dialect is Dialect.DUCKDB:
        return f"sha256({canon})"
    # PG: requires pgcrypto for digest()
    return f"encode(digest({canon}, 'sha256'), 'hex')"


def _quote_column(name: str, dialect: Dialect) -> str:
    """Return the column identifier quoted for the dialect.

    PG + DuckDB use double-quoted lowercase identifiers; Oracle uses
    double-quoted UPPERCASE for unquoted-stored identifiers (matching
    the project's ``column_name`` helper). The double-quote-wrap pins
    the identifier so case-folding rules don't interfere.
    """
    return f'"{column_name(name, dialect)}"'


def hash_table_rows(
    # WHY Any: psycopg + oracledb + DuckDB sync cursors share the DB-API 2.0
    # surface but none ships PEP 561 stubs.
    cur: Any,
    *,
    table: str,
    hwm: int,
    dialect: Dialect,
) -> str:
    """SHA256 fingerprint of ``WHERE entry <= hwm`` rows (CW.2 streamed).

    Per-row SHA-256 happens **in the SQL engine** (per-dialect
    canonicalization template + native hash function); only the
    fixed-size hex digests cross into Python. The Python loop folds
    digests through ``hashlib.sha256().update()`` — byte-identical
    to ``sha256(string_agg(rh ORDER BY entry))`` by Merkle-Damgård,
    proven empirically on DuckDB in ``docs/audits/audit_pdf_perf.md``
    and gated by an in-repo equivalence test (CW.6).

    Properties:

    - **Column-shape portable.** Columns discovered from
      ``cur.description`` and sorted by ``lower(name)`` so the same
      logical row hashes the same on every dialect regardless of
      physical column-order or case-folding rules (PG/DuckDB →
      lowercase identifiers, Oracle → UPPERCASE).
    - **Bounded memory.** ``cur.fetchmany(50_000)`` streams the
      digests in fixed-size batches — heap footprint stays flat
      regardless of row count. (Pre-CW.2 ``fetchall()`` over a
      3.27M-row dataset was the original perf trap.)
    - **Deterministic.** ``ORDER BY entry`` (the base table's PK)
      anchors the row order, so the same data with the same column
      shape yields the same hash byte-for-byte.
    - **Full SHA-256 strength.** No multiset/checksum-grade
      shortcut; this is a real SHA-256 over the row set, audit-grade
      collision-resistant. (The additive 0.52s alternative in the
      perf-audit doc was rejected by operator lock — see Lock 2.)

    ``dialect`` is required (no default) so the SQL canonicalization
    template is explicit per-callsite. ``Dialect.SQLITE`` is no longer
    supported (post-CB.8 the SQLite dialect is dropped repo-wide); the
    enum doesn't carry it.

    .. note::
       Pre-CW.2 PDFs were fingerprinted via the legacy Python ladder
       (``legacy_hash_table_rows_v1`` below). ``audit verify`` dispatches
       on the embedded ``provenance_format_version`` — version 1 uses
       the legacy path; version 2 (new emissions) uses this function.
    """
    # Discover columns + sort by lower(name) — portable across dialects.
    # `description` is a 7-tuple sequence per DB-API 2.0 spec.
    description: list[tuple[str, object, object, object, object, object, object]]
    # Need a description; if the cursor hasn't executed, prime it with a
    # 0-row probe so we can read the schema. (DB-API 2.0 doesn't expose
    # column shapes without an executed statement.)
    cur.execute(f"SELECT * FROM {table} WHERE 1=0")
    description = list(cur.description)
    column_names_sorted = sorted(
        (row[0] for row in description),
        key=lambda n: n.lower(),
    )
    quoted_columns = [_quote_column(n, dialect) for n in column_names_sorted]

    row_hash_expr = _row_hash_sql_expr(quoted_columns, dialect)
    # ORDER BY entry — base tables (transactions, daily_balances)
    # have an `entry` PK so this is a deterministic + index-backed sort.
    cur.execute(
        f"SELECT {row_hash_expr} AS rh"
        f" FROM {table}"
        f" WHERE {_quote_column('entry', dialect)} <= {hwm}"
        f" ORDER BY {_quote_column('entry', dialect)}"
    )

    h = hashlib.sha256()
    batch_size = 50_000
    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break
        for row in rows:
            rh = row[0]
            # The per-dialect SHA-256 helpers all emit lowercase hex
            # by construction; downcase defensively in case a future
            # dialect-helper change drifts case (the cumulative hash
            # depends on byte-for-byte input, so a casing drift would
            # silently fork v2 fingerprints between dialects).
            h.update(rh.lower().encode("ascii"))
    return h.hexdigest()


# -- Legacy v1 fingerprint (frozen for `audit verify` of pre-CW PDFs) -------


def canonical_value(v: object) -> bytes:
    """Stable bytes repr for one cell value when hashing rows (v1 legacy).

    Cross-dialect goal: PG and Oracle return the same logical row
    as the same bytes here. ``Decimal`` via ``str()`` keeps trailing
    zeros + sign; ``date``/``datetime`` via ``isoformat()`` is
    timezone-naive (matches our schema convention); ``bool`` is
    coerced to ``"1"``/``"0"`` since Oracle returns ints for
    booleans where PG returns Python bools; ``None`` is empty
    string (distinct from the field separator).

    .. note::
       **Frozen for legacy v1 verify only.** Phase CW.2 (2026-06-09)
       moved canonicalization into per-dialect SQL; new
       fingerprints are computed by ``hash_table_rows`` /
       ``_row_hash_sql_expr`` above. This function is kept ONLY
       because ``audit verify`` against a pre-CW PDF
       (``provenance_format_version == 1``) needs the exact bytes
       the original Python ladder produced. Do not add new
       callsites; do not modify (even an apparently-safe refactor
       could change a hash and break legacy verification).
    """
    if v is None:
        return b""
    if isinstance(v, bool):
        return b"1" if v else b"0"
    if isinstance(v, (int, float, Decimal)):
        return str(v).encode("utf-8")
    if isinstance(v, (date, datetime)):
        return v.isoformat().encode("utf-8")
    if isinstance(v, bytes):
        return v
    return str(v).encode("utf-8")


def legacy_hash_table_rows_v1(
    # WHY Any: psycopg + oracledb sync cursors share the DB-API 2.0
    # surface but neither ships PEP 561 stubs.
    cur: Any,
    *,
    table: str,
    hwm: int,
) -> str:
    """Frozen-for-verify-v1 SHA-256 over canonical row bytes (legacy).

    Implementation kept verbatim from pre-CW.2 ``hash_table_rows``.
    Reproduces the bytes the original ``canonical_value`` ladder +
    ``\\x1f`` field separator + ``\\x1e`` row separator yielded, so
    ``audit verify`` against a pre-CW PDF (provenance_format_version
    == 1) reproduces the same fingerprint. Do not change.

    NOT exported via ``__all__`` and NOT exposed by
    ``cli/audit/__init__.py``'s re-exports — only ``audit verify``'s
    version-dispatch reaches in.
    """
    cur.execute(
        f"SELECT * FROM {table}"
        f" WHERE entry <= {hwm}"
        f" ORDER BY entry"
    )
    description: list[tuple[str, object, object, object, object, object, object]] = list(cur.description)
    sorted_indices = [
        idx for idx, _ in sorted(
            enumerate(description),
            key=lambda i_d: i_d[1][0].lower(),
        )
    ]
    h = hashlib.sha256()
    for row in cur.fetchall():
        h.update(b"\x1f".join(
            canonical_value(row[i]) for i in sorted_indices
        ))
        h.update(b"\x1e")
    return h.hexdigest()


def l2_yaml_sha256(l2_instance_path: str | None) -> str:
    """SHA256 of the L2 YAML file bytes (verbatim, no normalization).

    When the user passed ``--l2 path``, hash that file. When they
    didn't (audit ran against the bundled default), hash the packaged
    ``spec_example.yaml`` bytes via the shared accessor so the
    fingerprint is still deterministic for the no-flag case.
    """
    if l2_instance_path is None:
        from recon_gen.common.l2 import default_l2_bytes
        data = default_l2_bytes()
    else:
        data = Path(l2_instance_path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def recon_gen_code_identity(version: str) -> str:
    """Code identity string baked into the fingerprint.

    Prefer ``v{version}+g{git_short}`` when running from a git
    checkout (carries both the released version AND the precise
    commit). Fall back to just ``v{version}`` when ``git`` isn't
    available (pip-installed package, no .git dir nearby) so the
    fingerprint stays deterministic for distributed installs.
    """
    if shutil.which("git") is None:
        return f"v{version}"
    try:
        # Run from this file's directory so ``git`` finds the
        # right repo even when the user invoked the CLI from
        # somewhere else in the filesystem.
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return f"v{version}"
    if result.returncode != 0:
        return f"v{version}"
    sha = result.stdout.strip()
    return f"v{version}+g{sha}" if sha else f"v{version}"


def compute_provenance(
    cfg: Config,
    instance: L2Instance,  # noqa: ARG001 — preserved for caller symmetry  # pyright: ignore[reportUnusedParameter]: kept for caller symmetry across audit-CLI subcommands
    *,
    l2_instance_path: str | None,
    version: str,
) -> ProvenanceFingerprint | None:
    """Compute the report's full provenance fingerprint.

    Returns ``None`` when ``demo_database_url`` is not configured —
    the artifact then renders with the long-form ``<pending>``
    placeholder (skeleton mode). Reads ``MAX(entry)`` for both base
    tables, hashes the rows up to those high-water marks via the
    Phase CW.2 streamed per-dialect SQL fingerprint, hashes the L2
    YAML file bytes, captures the code identity, and bundles
    everything into a ``ProvenanceFingerprint`` whose ``composite_sha``
    binds the artifact to its inputs.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db, fetch_one_required

    prefix = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COALESCE(MAX(entry), 0) FROM {prefix}_transactions")
        tx_hwm = int(fetch_one_required(cur)[0] or 0)
        cur.execute(f"SELECT COALESCE(MAX(entry), 0) FROM {prefix}_daily_balances")
        bal_hwm = int(fetch_one_required(cur)[0] or 0)
        tx_sha = hash_table_rows(
            cur, table=f"{prefix}_transactions", hwm=tx_hwm,
            dialect=cfg.dialect,
        )
        bal_sha = hash_table_rows(
            cur, table=f"{prefix}_daily_balances", hwm=bal_hwm,
            dialect=cfg.dialect,
        )
    finally:
        conn.close()

    return ProvenanceFingerprint(
        transactions_hwm=tx_hwm,
        transactions_sha=tx_sha,
        balances_hwm=bal_hwm,
        balances_sha=bal_sha,
        l2_yaml_sha=l2_yaml_sha256(l2_instance_path),
        code_identity=recon_gen_code_identity(version),
    )
