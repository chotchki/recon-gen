"""Cross-reference docs ``recon-gen ...`` invocations against the
live Click tree (X.1.h.B v0).

Walks ``src/recon_gen/docs/**/*.md``, extracts every
``recon-gen <command> <flags>`` line out of fenced bash blocks,
and asserts each cited subcommand chain + each flag exists in the
shipped CLI surface. Catches the "doc cites a removed verb / renamed
flag" hallucination class (the X.1.h.A motivator was a step further:
the verb existed but its output was wrong; this guard catches the
verb-doesn't-exist case before docs ship).

What this is NOT:

- Not an execution test — flag *values* aren't validated, just flag
  *names*. Running every documented command would need fixtures and
  AWS / DB context.
- Not a Python / SQL block checker — those are separate items
  (X.1.h.B.2 / X.1.h.B.3).
- Not a column-name cross-reference — that's X.1.h.B.4.

Add new docs and the checker auto-discovers them — no per-doc wiring.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
import pytest

from recon_gen.cli import main as cli_root


_DOCS_DIR = (
    Path(__file__).parent.parent.parent
    / "src" / "recon_gen" / "docs"
)


# Click flags every command implicitly accepts — Click adds these at
# group-level even though they don't appear on the leaf's params list.
_GLOBAL_FLAGS: frozenset[str] = frozenset({
    "--help",
    "-h",
    "--version",
})


def _walk_cli_tree(
    cmd: click.BaseCommand, path: list[str],
) -> dict[str, frozenset[str]]:
    """Map ``"group sub leaf"`` → frozenset of allowed flag tokens
    (``-x`` and ``--xxx`` forms both included)."""
    out: dict[str, frozenset[str]] = {}
    if isinstance(cmd, click.Group):
        for name, sub in cmd.commands.items():
            out.update(_walk_cli_tree(sub, path + [name]))
        return out
    flags: set[str] = set()
    for p in cmd.params:
        if isinstance(p, click.Option):
            flags.update(p.opts)
            flags.update(p.secondary_opts)
    out[" ".join(path)] = frozenset(flags) | _GLOBAL_FLAGS
    return out


_CLI_TREE: dict[str, frozenset[str]] = _walk_cli_tree(cli_root, [])


def _extract_bash_blocks(md_text: str) -> list[str]:
    """Return the body text of every ```bash``` fenced block."""
    return re.findall(r"```bash\n(.*?)```", md_text, re.DOTALL)


def _join_line_continuations(block: str) -> list[str]:
    """Collapse trailing-backslash bash line continuations into single
    logical lines."""
    out: list[str] = []
    pending: list[str] = []
    for raw in block.splitlines():
        if raw.rstrip().endswith("\\"):
            pending.append(raw.rstrip()[:-1])
            continue
        if pending:
            pending.append(raw)
            out.append(" ".join(s.strip() for s in pending))
            pending = []
        else:
            out.append(raw)
    if pending:
        out.append(" ".join(s.strip() for s in pending))
    return out


def _split_at_subshell_or_pipe(line: str) -> str:
    """Strip everything from the first ``|``, ``&&``, ``||``, ``;``, or
    inline ``#`` comment so we only check the head invocation. We don't
    try to parse subshells (``$(...)``) — if a doc nests
    ``recon-gen`` inside a subshell, the test will currently miss
    it. That's acceptable for the v0 cut.
    """
    for sep in (" | ", " && ", " || ", " ; ", " # "):
        i = line.find(sep)
        if i >= 0:
            line = line[:i]
    return line


def _is_qsg_command_position(tokens: list[str], idx: int) -> bool:
    """The token at ``idx`` is the ``recon-gen`` *command*
    (rather than e.g. an argument to ``pip install``).

    Accepted shapes:

    - first executable token in the line, or
    - preceded only by env-var assignments (``KEY=val``) and/or path
      segments that resolve to a ``recon-gen`` binary.

    Rejects ``pip install recon-gen`` and similar package-name
    references where ``recon-gen`` appears as an argument to a
    different command.
    """
    for prior in tokens[:idx]:
        if "=" in prior and not prior.startswith("-"):
            # Env-var assignment like ``RECON_GEN_E2E=1`` — skip.
            continue
        return False
    return True


def _resolve_subcommand_chain(
    rest_tokens: list[str],
) -> tuple[list[str], int] | None:
    """Walk ``rest_tokens`` and return the longest prefix that matches
    a real subcommand chain in the live Click tree, plus the index in
    ``rest_tokens`` where parsing should resume (skipping any
    positional args that follow the subcommand).

    Returns ``None`` if no prefix matches — caller treats that as an
    unknown chain and reports failure. ``audit verify report.pdf``
    parses as chain=``["audit", "verify"]``, resume_at=2 (so
    ``report.pdf`` is treated as a positional arg, not a flag or a
    nested subcommand).
    """
    chain: list[str] = []
    matched_chain: list[str] | None = None
    matched_consumed = 0
    for i, t in enumerate(rest_tokens):
        if t.startswith("-"):
            break
        chain.append(t)
        if " ".join(chain) in _CLI_TREE:
            matched_chain = list(chain)
            matched_consumed = i + 1
    if matched_chain is None:
        return None
    return matched_chain, matched_consumed


def _iter_qsg_invocations(
    md_text: str,
) -> list[tuple[list[str] | None, list[str], list[str]]]:
    """Yield ``(resolved_chain, attempted_chain, flag_tokens)`` for
    every ``recon-gen ...`` invocation in the markdown body's
    bash blocks.

    - ``resolved_chain`` is the longest non-flag-token prefix that
      resolves to a real Click subcommand chain (e.g.
      ``["audit", "verify"]`` from ``audit verify report.pdf``).
      ``None`` means no prefix resolved — the doc cites an unknown
      subcommand. An empty list means there were no non-flag tokens
      after ``recon-gen`` (bare ``recon-gen --version``).
    - ``attempted_chain`` is the raw run of non-flag tokens for error
      reporting, even when nothing resolved.
    - ``flag_tokens`` is every ``-x`` / ``--xxx`` token (no values).
    """
    out: list[tuple[list[str] | None, list[str], list[str]]] = []
    for block in _extract_bash_blocks(md_text):
        for line in _join_line_continuations(block):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = _split_at_subshell_or_pipe(line)
            try:
                tokens = shlex.split(line, comments=True, posix=True)
            except ValueError:
                continue
            idx = None
            for i, t in enumerate(tokens):
                if t == "recon-gen" or t.endswith("/recon-gen"):
                    if _is_qsg_command_position(tokens, i):
                        idx = i
                        break
            if idx is None:
                continue
            rest = tokens[idx + 1:]
            attempted: list[str] = []
            for t in rest:
                if t.startswith("-"):
                    break
                attempted.append(t)
            resolved_pair = _resolve_subcommand_chain(rest)
            if resolved_pair is None:
                resolved_chain: list[str] | None = (
                    [] if not attempted else None
                )
                resume_at = len(attempted)
            else:
                resolved_chain, resume_at = resolved_pair
            flags: list[str] = []
            i = resume_at
            while i < len(rest):
                t = rest[i]
                if t.startswith("-"):
                    flags.append(t.split("=", 1)[0])
                i += 1
            out.append((resolved_chain, attempted, flags))
    return out


def _markdown_files() -> list[Path]:
    return sorted(_DOCS_DIR.rglob("*.md"))


@pytest.mark.parametrize("md_path", _markdown_files(), ids=lambda p: p.name)
def test_docs_cli_invocations_resolve(md_path: Path) -> None:
    """Every ``recon-gen ...`` invocation in the doc's bash blocks
    cites a real subcommand chain + real flags from the live Click
    tree."""
    invocations = _iter_qsg_invocations(md_path.read_text())
    if not invocations:
        pytest.skip(f"No recon-gen invocations in {md_path.name}")

    failures: list[str] = []
    for resolved_chain, attempted, flags in invocations:
        attempted_str = " ".join(attempted)
        if resolved_chain is None:
            failures.append(
                f"  unknown subcommand chain: 'recon-gen "
                f"{attempted_str}' (closest matches: "
                f"{[c for c in _CLI_TREE if attempted and attempted[0] in c][:3]})"
            )
            continue
        if not resolved_chain:
            # Bare ``recon-gen [--version|--help]`` — only
            # global flags are valid; no subcommand to look up.
            allowed = _GLOBAL_FLAGS
            chain_str = ""
        else:
            chain_str = " ".join(resolved_chain)
            allowed = _CLI_TREE[chain_str]
        for flag in flags:
            if flag not in allowed:
                failures.append(
                    f"  unknown flag for 'recon-gen {chain_str}': "
                    f"{flag!r} (allowed: {sorted(allowed - _GLOBAL_FLAGS)})"
                )
    assert not failures, (
        f"\n{md_path.relative_to(_DOCS_DIR)} cites CLI surface that "
        f"doesn't exist in the shipped tree:\n" + "\n".join(failures)
    )
