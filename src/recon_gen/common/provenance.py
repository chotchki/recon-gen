"""Audit-grade provenance fingerprint primitives.

Binds a generated artifact (e.g. an audit PDF) to its source data
via SHA256 over base table rows + L2 yaml file bytes + code identity.
Designed to be reusable: ``cli/audit.py`` is the first consumer
(``audit apply`` to compute + embed; ``audit verify`` to recompute +
diff), but any future tool that wants a reproducibility binding for
its output can pull this module in.

Locked design (Phase U.7):

- Hash the **base tables** + external inputs, NOT matviews.
  Matviews are derived data; their hash drifting from a recompute
  is a *technical* signal (matviews need refresh, schema drift)
  but isn't authoritative for "what was this report bound to".

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
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


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
    """
    transactions_hwm: int
    transactions_sha: str
    balances_hwm: int
    balances_sha: str
    l2_yaml_sha: str
    code_identity: str

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

    def to_dict(self) -> dict:
        return {
            "schema": "qsg-audit-provenance-v1",
            "composite_sha": self.composite_sha,
            "transactions_hwm": self.transactions_hwm,
            "transactions_sha": self.transactions_sha,
            "balances_hwm": self.balances_hwm,
            "balances_sha": self.balances_sha,
            "l2_yaml_sha": self.l2_yaml_sha,
            "code_identity": self.code_identity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProvenanceFingerprint":
        if d.get("schema") != "qsg-audit-provenance-v1":
            raise ValueError(
                f"Unrecognized provenance schema: {d.get('schema')!r}"
            )
        return cls(
            transactions_hwm=int(d["transactions_hwm"]),
            transactions_sha=str(d["transactions_sha"]),
            balances_hwm=int(d["balances_hwm"]),
            balances_sha=str(d["balances_sha"]),
            l2_yaml_sha=str(d["l2_yaml_sha"]),
            code_identity=str(d["code_identity"]),
        )


def canonical_value(v) -> bytes:  # type: ignore[no-untyped-def]: v is any DB cell value (Decimal, datetime, str, bytes, None)
    """Stable bytes repr for one cell value when hashing rows.

    Cross-dialect goal: PG and Oracle return the same logical row
    as the same bytes here. ``Decimal`` via ``str()`` keeps trailing
    zeros + sign; ``date``/``datetime`` via ``isoformat()`` is
    timezone-naive (matches our schema convention); ``bool`` is
    coerced to ``"1"``/``"0"`` since Oracle returns ints for
    booleans where PG returns Python bools; ``None`` is empty
    string (distinct from the field separator).
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


def hash_table_rows(
    cur,  # type: ignore[no-untyped-def]: psycopg/oracledb sync cursor — drivers lack PEP 561 stubs
    *,
    table: str,
    hwm: int,
) -> str:
    """SHA256 over canonical row bytes for ``WHERE entry <= hwm``.

    Column set is **discovered at runtime** from ``cur.description``
    (DB-API 2.0 standard, works for both psycopg2 and oracledb)
    and sorted alphabetically by lowercased name. This avoids the
    footgun where a hardcoded column list would silently exclude
    new columns added to the base table from the fingerprint —
    producing a hash that claims "this binds to all source data"
    while missing whatever the new column carries.

    Lowercasing before sorting makes the order portable across
    Postgres (returns lowercase identifiers) and Oracle (returns
    UPPERCASE for unquoted identifiers).

    Streams results so memory stays flat regardless of row count.
    Field separator: ``\\x1f`` (unit separator). Row separator:
    ``\\x1e`` (record separator). Both are control codes that
    can't appear in our schema's data types, so we don't need to
    escape them.
    """
    cur.execute(
        f"SELECT * FROM {table}"
        f" WHERE entry <= {hwm}"
        f" ORDER BY entry"
    )
    sorted_indices = [
        idx for idx, _ in sorted(
            enumerate(cur.description),
            key=lambda i_d: i_d[1][0].lower(),
        )
    ]
    h = hashlib.sha256()
    for row in cur:
        h.update(b"\x1f".join(
            canonical_value(row[i]) for i in sorted_indices
        ))
        h.update(b"\x1e")
    return h.hexdigest()


def hash_matview_rows(
    cur,  # type: ignore[no-untyped-def]: psycopg/oracledb sync cursor — drivers lack PEP 561 stubs
    *,
    matview: str,
) -> tuple[int, str]:
    """SHA256 + row count over every row in a matview.

    Distinct from ``hash_table_rows`` (which is bounded by an
    ``entry`` high-water-mark, since base tables are append-only and
    we want a stable snapshot point). Matviews don't have ``entry``;
    they're recomputable from base tables and we want the SHA256 to
    represent "what the matview contained at audit time". So this
    helper just hashes ALL rows in a deterministic order.

    Determinism: discover columns alphabetically by lowercased name
    (same convention as ``hash_table_rows`` — works portably on PG
    + Oracle), pull all rows into memory as canonicalized tuples,
    sort by tuple-lex (works for any matview without needing to
    know its natural key), then stream into SHA256. Matviews are
    bounded (~tens to hundreds of rows in practice) so memory is
    fine. Returns ``(row_count, sha256_hex)`` so the appendix can
    show both side by side.
    """
    cur.execute(f"SELECT * FROM {matview}")
    sorted_indices = [
        idx for idx, _ in sorted(
            enumerate(cur.description),
            key=lambda i_d: i_d[1][0].lower(),
        )
    ]
    canonical_rows = [
        tuple(canonical_value(row[i]) for i in sorted_indices)
        for row in cur
    ]
    canonical_rows.sort()
    h = hashlib.sha256()
    for cells in canonical_rows:
        h.update(b"\x1f".join(cells))
        h.update(b"\x1e")
    return len(canonical_rows), h.hexdigest()


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
    cfg, instance,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
    *,
    l2_instance_path: str | None,
    version: str,
) -> ProvenanceFingerprint | None:
    """Compute the report's full provenance fingerprint.

    Returns ``None`` when ``demo_database_url`` is not configured —
    the artifact then renders with the long-form ``<pending>``
    placeholder (skeleton mode). Reads ``MAX(entry)`` for both base
    tables, hashes the rows up to those high-water marks, hashes
    the L2 YAML file bytes, captures the code identity, and bundles
    everything into a ``ProvenanceFingerprint`` whose ``composite_sha``
    binds the artifact to its inputs.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COALESCE(MAX(entry), 0) FROM {prefix}_transactions")
        tx_hwm = int(cur.fetchone()[0] or 0)
        cur.execute(f"SELECT COALESCE(MAX(entry), 0) FROM {prefix}_daily_balances")
        bal_hwm = int(cur.fetchone()[0] or 0)
        tx_sha = hash_table_rows(
            cur, table=f"{prefix}_transactions", hwm=tx_hwm,
        )
        bal_sha = hash_table_rows(
            cur, table=f"{prefix}_daily_balances", hwm=bal_hwm,
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
