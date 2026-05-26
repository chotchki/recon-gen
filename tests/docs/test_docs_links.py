"""Sweep the built mkdocs site for dead internal links + missing anchors.

mkdocs --strict catches missing files but does NOT verify fragment ids
(``#some-anchor``). This test fills that gap: it builds the site once,
walks every produced HTML, and checks that every internal href / src
points at a file that exists AND (when the URL has a fragment) the
matching ``id="..."`` is present in the target page.

Skips:
- External URLs (http://, https://, mailto:, javascript:, data:, tel:)
- Root-absolute paths (`/recon-gen/...`) — these resolve via
  ``site_url`` at deploy time but don't exist locally under ``site/``.
- mkdocs-material framework anchors (``#__toc``, ``#__nav_*``).

If the site already has a fresh build at ``site/``, that's reused;
otherwise the test runs ``mkdocs build`` once.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from tests.docs._handbook_build import build_handbook

HREF_RE = re.compile(r'(?:href|src)="([^"]+)"')
ID_RE = re.compile(r'id="([^"]+)"')


@pytest.fixture(scope="module")
def built_site() -> Path:
    """Build the handbook once per xdist worker into an isolated sandbox.

    AH.8 (#175) — the site used to land in a shared ``REPO_ROOT/site`` that
    every docs module rebuilt + rmtree'd, racing under ``-n auto``.
    ``build_handbook`` copies ``docs/`` + redirects ``docs_dir`` under the
    run-artifact dir so each build is self-contained.
    """
    return build_handbook(None)


def _anchors_in(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path in cache:
        return cache[path]
    if not path.is_file():
        cache[path] = set()
        return cache[path]
    cache[path] = set(ID_RE.findall(path.read_text(errors="replace")))
    return cache[path]


def _sweep(site: Path) -> list[tuple[Path, str, str]]:
    """Walk site/**.html, return list of (page, href, reason) for dead links."""
    cache: dict[Path, set[str]] = {}
    site = site.resolve()
    dead: list[tuple[Path, str, str]] = []
    for html in site.rglob("*.html"):
        text = html.read_text(errors="replace")
        for raw_match in HREF_RE.findall(text):
            raw: str = raw_match
            if not raw:
                continue
            # Pure fragment — same-page anchor.
            if raw.startswith("#"):
                frag = raw.lstrip("#")
                if frag.startswith("__") or frag == "":
                    continue
                if frag not in _anchors_in(html, cache):
                    dead.append((html, raw, "missing anchor on same page"))
                continue
            sp = urlsplit(raw)
            if sp.scheme in (
                "http", "https", "mailto", "javascript", "data", "tel",
            ):
                continue
            if sp.path.startswith("/"):
                # Root-absolute (resolves via site_url at deploy).
                continue
            target_path = (html.parent / unquote(sp.path)).resolve()
            if target_path.is_dir() or raw.endswith("/"):
                target_file = target_path / "index.html"
            else:
                target_file = target_path
            if not target_file.exists():
                try:
                    rel = target_file.relative_to(site)
                except ValueError:
                    rel = target_file
                dead.append((html, raw, f"missing file {rel}"))
                continue
            if sp.fragment and not sp.fragment.startswith("__"):
                if sp.fragment not in _anchors_in(target_file, cache):
                    try:
                        rel = target_file.relative_to(site)
                    except ValueError:
                        rel = target_file
                    dead.append((
                        html, raw,
                        f"missing anchor #{sp.fragment} in {rel}",
                    ))
    return dead


# AH.5 — dead anchors resolved: stable ``{#slug}`` anchors added to the
# L1_Invariants + Schema_v6 headings (their auto-slugs embed
# ``{{ l2_instance_name }}`` + a number prefix, so they're
# L2-instance-dependent — cross-refs pin the natural slug via attr_list).
# Gate is live (was xfail under AB.7.1a).
def test_no_dead_links_in_built_site(built_site: Path):
    """Every internal href / src in the built mkdocs site resolves.

    mkdocs --strict only catches missing files; this also catches
    missing fragment anchors (``#section-id``). When this test fails,
    mkdocs build the site (``mkdocs build``) and inspect the offenders
    at the printed paths.
    """
    dead = _sweep(built_site)
    if not dead:
        return
    by_page: dict[Path, list[tuple[str, str]]] = defaultdict(list)
    for page, href, reason in dead:
        by_page[page].append((href, reason))
    lines = [f"{len(dead)} dead internal link(s) in built site:", ""]
    for page in sorted(by_page):
        lines.append(f"  {page.relative_to(built_site)}:")
        for href, reason in by_page[page]:
            lines.append(f"    {href!r} -> {reason}")
    pytest.fail("\n".join(lines))
