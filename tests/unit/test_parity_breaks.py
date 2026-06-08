"""CN.1 — pure-registry lint for `common/parity/breaks.py`.

Per CN.0 Lock CN-5 (2026-06-08, pure-registry shape): every
`PARITY_BREAKS` entry's `references` paths must resolve, every entry
must have non-empty `surface` + `qs_limitation` + a valid ISO
`discovered` date, and names must be unique. There is NO site-comment
lint — the registry is the only declaration.

References shape: each string is either
- a file path under repo root (e.g. ``src/recon_gen/common/models.py``)
- a ``path:symbol`` form (e.g. ``src/recon_gen/common/tree/structure.py::Sheet``)

The lint resolves files via Path.exists(); symbol forms are accepted
but only the file portion is checked (symbol-resolution would require
importing every referenced module, which is brittle in a unit test).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from recon_gen.common.parity import (
    PARITY_BREAKS,
    ParitySeverity,
    QSParityBreak,
    render_handbook_section,
    render_markdown,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PARITY_BREAKS_DOC = REPO_ROOT / "docs" / "reference" / "qs-parity-breaks.md"


def test_registry_has_entries() -> None:
    """Sanity check — empty registry would mean we lost the source."""
    assert len(PARITY_BREAKS) >= 10, (
        f"PARITY_BREAKS has {len(PARITY_BREAKS)} entries; expected ≥10. "
        f"Either the registry got truncated or CN.0's initial population "
        f"was reduced — confirm intent."
    )


def test_every_entry_is_frozen_qs_parity_break() -> None:
    """Type check — defensive against accidental tuple-of-dicts."""
    for entry in PARITY_BREAKS:
        assert isinstance(entry, QSParityBreak), (
            f"Non-QSParityBreak entry in registry: {entry!r}"
        )


def test_names_are_unique() -> None:
    """Each entry's name doubles as a stable identifier (doc anchor,
    handbook link target). Collisions break both."""
    names = [e.name for e in PARITY_BREAKS]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, (
        f"Duplicate PARITY_BREAKS names: {sorted(duplicates)}"
    )


def test_names_are_snake_case_slugs() -> None:
    """Names appear in URLs, file anchors, and CLI grep — restrict to
    snake_case so they're portable across all three."""
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    bad = [e.name for e in PARITY_BREAKS if not pattern.match(e.name)]
    assert not bad, (
        f"Non-snake-case parity-break names: {bad}. Use [a-z][a-z0-9_]*."
    )


def test_surface_and_qs_limitation_non_empty() -> None:
    """A parity break with no `surface` description teaches nothing.
    Same for `qs_limitation` — readers need both halves."""
    failures: list[str] = []
    for e in PARITY_BREAKS:
        if not e.surface.strip():
            failures.append(f"{e.name}: empty surface")
        if not e.qs_limitation.strip():
            failures.append(f"{e.name}: empty qs_limitation")
    assert not failures, "\n".join(failures)


def test_discovered_is_iso_date() -> None:
    """`discovered` feeds the auto-generated quirks-log timeline.
    Reject anything that isn't a parseable YYYY-MM-DD."""
    failures: list[str] = []
    for e in PARITY_BREAKS:
        try:
            date.fromisoformat(e.discovered)
        except ValueError:
            failures.append(
                f"{e.name}: discovered={e.discovered!r} is not ISO-8601 (YYYY-MM-DD)"
            )
    assert not failures, "\n".join(failures)


def test_references_resolve_to_existing_files() -> None:
    """Every reference's file portion must exist. Symbol portion (after
    `::`) is informational; only the file is checked here."""
    failures: list[str] = []
    for e in PARITY_BREAKS:
        for ref in e.references:
            file_part = ref.split("::", 1)[0]
            path = REPO_ROOT / file_part
            if not path.exists():
                failures.append(
                    f"{e.name}: references {ref!r} but {path} does not exist"
                )
    assert not failures, "\n".join(failures)


def test_severity_is_valid_enum_value() -> None:
    """Defensive — frozen dataclass already enforces type, but a future
    refactor could weaken this."""
    for e in PARITY_BREAKS:
        assert isinstance(e.severity, ParitySeverity), (
            f"{e.name}: severity={e.severity!r} not a ParitySeverity enum"
        )


def test_hard_divergence_entries_may_have_no_references() -> None:
    """Hard-divergence entries can be conventions or absent-features
    with no single code site. The lint flags empty `references` for
    sanity-check visibility but doesn't fail on it — just confirm at
    least one HARD_DIVERGENCE entry exists so the category isn't
    dormant."""
    hard_count = sum(
        1 for e in PARITY_BREAKS
        if e.severity == ParitySeverity.HARD_DIVERGENCE
    )
    assert hard_count >= 1, (
        "No HARD_DIVERGENCE parity-break entries — Studio L2 editor "
        "and similar surfaces should be registered."
    )


def test_all_three_severities_populated() -> None:
    """Initial CN.0 population aims for ~5 WORKAROUND / ~5 ENHANCEMENT
    / ~4 HARD_DIVERGENCE. None of the categories should be dormant."""
    by_sev: dict[ParitySeverity, int] = {s: 0 for s in ParitySeverity}
    for e in PARITY_BREAKS:
        by_sev[e.severity] += 1
    for sev, count in by_sev.items():
        assert count >= 1, (
            f"No PARITY_BREAKS entries with severity {sev.value}; "
            f"each category should carry at least one entry."
        )


def test_auto_doc_in_sync_with_registry() -> None:
    """The committed `docs/reference/qs-parity-breaks.md` must equal
    the emitter's current output. Drift means someone updated
    PARITY_BREAKS without regenerating the doc — the CN.6 CI gate.

    Regenerate via:
        .venv/bin/python -c "
        from recon_gen.common.parity import render_markdown
        from pathlib import Path
        Path('docs/reference/qs-parity-breaks.md').write_text(render_markdown())
        "
    """
    expected = render_markdown()
    actual = PARITY_BREAKS_DOC.read_text()
    assert actual == expected, (
        "docs/reference/qs-parity-breaks.md is out of sync with "
        "PARITY_BREAKS. Regenerate it (see docstring for the command)."
    )


def test_render_handbook_section_filters_by_name() -> None:
    """The handbook-section helper produces a bullet list keyed by
    name. Passing a name that isn't in the registry quietly drops it
    (forward-compat for renaming) but the function should not raise."""
    names = ["count_distinct_quirk_bl1", "nonexistent_break"]
    section = render_handbook_section(names)
    assert "count_distinct_quirk_bl1" in section
    assert "nonexistent_break" not in section
    # Empty input → empty output (no header)
    assert render_handbook_section([]) == ""
