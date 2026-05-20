#!/usr/bin/env python3
"""Rebuild App 2's Tailwind stylesheet (`output.css`) from `input.css`.

App 2's page shell links `/static/output.css` — the compiled Tailwind v4
sheet. It's committed + shipped in the wheel (offline-ready, like the
vendored JS in `scripts/vendor_js_deps.py`), but it has to be rebuilt
whenever `input.css` (the `@theme` tokens / `@source` globs) changes or a
new utility class shows up in `render.py` / `bootstrap.js`. This script is
that rebuild recipe, formalized — it is NOT a `recon-gen` CLI verb;
end users never run it.

Tooling: `tailwindcss` is the standalone binary `pytailwindcss` puts on
`PATH` (a Rust binary wrapped in Python — no Node). The committed
`output.css` is minified (single line), so the recipe passes `--minify`;
drop it if you want a readable build for diffing.

Usage::

    python scripts/build_app2_css.py            # rebuild + write output.css
    python scripts/build_app2_css.py --check    # rebuild to a temp file and
                                                # diff against the committed
                                                # output.css (CI-ish guard;
                                                # best-effort — Tailwind
                                                # output can shift between
                                                # versions, so a mismatch is
                                                # a warning, not a hard fail)

`input.css` already carries the `@source "../render.py"` etc. globs, so the
scan set is config-in-the-CSS — nothing to pass on the command line beyond
`-i` / `-o`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ASSETS = (
    Path(__file__).resolve().parent.parent
    / "src" / "recon_gen" / "common" / "html" / "assets"
)
_INPUT = _ASSETS / "input.css"
_OUTPUT = _ASSETS / "output.css"

# AH.7 — PIN the Tailwind binary version. ``pytailwindcss`` defaults to
# "latest", so an unpinned build is non-deterministic: a fresh CI runner
# downloads whatever Tailwind shipped today (it grabbed v4.3.0) while a
# dev box reuses an older cached binary (v4.2.4) — the bytes differ and
# the sessionstart drift gate (conftest.py) fails CI. Pinning makes
# ``output.css`` reproducible across local + CI. Bumping Tailwind = bump
# this string + re-run this script + commit the regenerated output.css.
_TAILWIND_VERSION = "v4.3.0"


def _tailwind_bin() -> str:
    # `pytailwindcss` puts `tailwindcss` next to the venv's `python`, so
    # `.venv/bin/python scripts/build_app2_css.py` works without activating
    # the venv (per the `.venv/bin/...`-direct-invocation convention).
    # NB: don't `.resolve()` — venv `python` is usually a symlink to the
    # base interpreter; we want the venv's bin dir, which is `sys.executable`'s
    # *literal* parent.
    sibling = Path(sys.executable).parent / "tailwindcss"
    if sibling.exists():
        return str(sibling)
    on_path = shutil.which("tailwindcss")
    if on_path is not None:
        return on_path
    sys.exit(
        "tailwindcss not found next to this interpreter or on PATH — "
        "install the dev deps (`uv sync --extra dev`, which brings "
        "`pytailwindcss`), then re-run. The binary it installs is a "
        "standalone Rust build, no Node required."
    )


def _build(out_path: Path) -> None:
    # Pin the Tailwind version (pytailwindcss reads TAILWINDCSS_VERSION;
    # default "latest" is the CI-vs-local drift source). Set it for this
    # subprocess so both a hand rebuild and the conftest drift gate's
    # ``--check`` resolve the same binary → byte-identical output.css.
    env = {**os.environ, "TAILWINDCSS_VERSION": _TAILWIND_VERSION}
    subprocess.run(
        [_tailwind_bin(), "--minify", "-i", str(_INPUT), "-o", str(out_path)],
        check=True,
        env=env,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="rebuild to a temp file and diff against the committed "
             "output.css instead of overwriting it (best-effort guard)",
    )
    args = parser.parse_args()

    if not _INPUT.exists():
        sys.exit(f"input not found: {_INPUT}")

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "output.css"
            _build(fresh)
            if not _OUTPUT.exists():
                print(f"committed {_OUTPUT} is MISSING — run without --check")
                return 1
            if fresh.read_bytes() == _OUTPUT.read_bytes():
                print(f"output.css is up to date ({_OUTPUT})")
                return 0
            print(
                f"WARNING: a fresh build differs from the committed "
                f"{_OUTPUT}. If you changed input.css / added utility "
                f"classes, re-run without --check to refresh it. (If you "
                f"only bumped Tailwind, the diff may be cosmetic — "
                f"inspect before committing.)"
            )
            return 1

    _build(_OUTPUT)
    print(f"rebuilt {_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
