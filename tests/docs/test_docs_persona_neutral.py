"""Persona-neutral CI gate for the rendered mkdocs site.

Builds the site against both bundled fixtures and asserts:

1. ``spec_example`` build — every page contains zero persona tokens
   *except* a small allowlist of pages that intentionally cite the
   bundled ``sasquatch_pr`` fixture (handbook hubs explaining the
   demo, walkthroughs telling the integrator how to point docs at a
   real fixture). New leaks on any non-allowlisted page fail the
   test; reductions tighten the per-page bound.
2. ``sasquatch_pr`` build — the persona-rich strings DO render. This
   guards against accidentally over-deleting persona content while
   chasing zero leaks.

Persona token regex matches the SNB / Sasquatch personas (institution
acronym, fixture name, hand-curated Investigation personas). CSS
class names like ``snb-card`` are filtered out — they're stylesheet
artifacts, not persona prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.docs._handbook_build import build_handbook

REPO_ROOT = Path(__file__).resolve().parents[2]

# Real persona tokens — institution acronym, hand-curated personas,
# fixture name. Word-boundaries on `snb` because `snb-card` etc. are
# stylesheet class names.
PERSONA_TOKEN_RE = re.compile(
    r"(juniper|cascadia|shell company|shell-company|"
    r"sasquatch|bigfoot|\bsnb\b)",
    re.IGNORECASE,
)

# CSS / asset noise we explicitly drop — they match the regex but
# aren't persona prose.
NOISE_FRAGMENT_RE = re.compile(
    r"(snb-card|snb-section-label|snb-mark|snb-wordmark|snb-grid|snb-list)"
)


# Per-page max-leak counts. Pages NOT in this map must have zero
# persona tokens. New leaks on a non-allowlisted page fail the test;
# additions to a listed page that exceed its bound also fail.
#
# How to update when you legitimately reduce a count: lower the value
# here in the same commit that drops the leaks. The asymmetric
# "tighten only" gate prevents drift up.
ALLOWED_LEAK_COUNTS: dict[str, int] = {
    # Handbook hubs — explain the bundled demo; intentional Tier 2
    # citations of the sasquatch_pr fixture by name.
    "handbook/l1/index.html": 19,
    "handbook/customization/index.html": 14,
    "handbook/etl/index.html": 12,
    "handbook/l2_flow_tracing/index.html": 5,
    # Customization walkthroughs that tell the integrator HOW to
    # point at a bundled fixture or how to publish docs against it.
    "walkthroughs/customization/how-do-i-reskin-the-dashboards/"
    "index.html": 5,
    "walkthroughs/customization/how-do-i-publish-docs-against-my-l2/"
    "index.html": 5,
    # The persona-block walkthrough explicitly cites sasquatch_pr as
    # the worked example for the optional persona: YAML block.
    "walkthroughs/customization/how-do-i-brand-my-handbook-prose/"
    "index.html": 15,
    # AF/AB-era customization walkthroughs — each cites the bundled
    # sasquatch_pr fixture once as "the real-world example" of the
    # feature it teaches (a Files-you'll-touch pointer, same intentional
    # Tier-2 citation pattern as the entries above).
    "walkthroughs/customization/how-do-i-add-an-aml-inbound-cap/"
    "index.html": 1,
    "walkthroughs/customization/how-do-i-add-multi-mode-settlement/"
    "index.html": 1,
    "walkthroughs/customization/how-do-i-chain-two-templates/"
    "index.html": 1,
    "walkthroughs/customization/how-do-i-mix-cardinality-children/"
    "index.html": 1,
    "walkthroughs/customization/how-do-i-model-batched-payouts/"
    "index.html": 1,
    "walkthroughs/customization/how-do-i-set-typical-amount-ranges/"
    "index.html": 1,
    "walkthroughs/customization/how-do-i-set-typical-firing-counts/"
    "index.html": 1,
    # Macro-emitted "Fallback example" admonition naming sasquatch_pr
    # when spec_example carries no Chains entries.
    "concepts/l2/chain/index.html": 1,
}


def _build_site_for(fixture_path: str) -> Path:
    """Build the site against the named L2 fixture into an isolated sandbox.

    AH.8 (#175) — was a shared ``REPO_ROOT/site`` rebuilt per fixture, which
    raced other workers under ``-n auto``; ``build_handbook`` gives each
    build its own ``docs_dir`` copy + output under the run-artifact dir.
    Returns the rendered ``site/`` so callers read from their own build.
    """
    return build_handbook(REPO_ROOT / fixture_path)


def _count_leaks(html: str) -> int:
    """Count true persona tokens in HTML, dropping CSS-class noise."""
    cleaned = NOISE_FRAGMENT_RE.sub("", html)
    return len(PERSONA_TOKEN_RE.findall(cleaned))


def _site_html_files(site: Path) -> list[Path]:
    return sorted(site.rglob("*.html"))


# AH.5 — persona leaks resolved: SPEC.md + quicksight-quirks.md prose
# generalized, the CLI --l2 help example neutralized; the AF/AB-era
# customization walkthroughs' intentional sasquatch_pr citations are
# allowlisted in ALLOWED_LEAK_COUNTS above. Gate is live (was xfail
# under AB.7.1a).
def test_spec_example_build_has_no_unexpected_persona_leaks() -> None:
    """spec_example renders generic prose — zero leaks outside allowlist."""
    site = _build_site_for("tests/l2/spec_example.yaml")

    failures: list[str] = []
    for html_path in _site_html_files(site):
        rel = html_path.relative_to(site).as_posix()
        leaks = _count_leaks(html_path.read_text(errors="replace"))
        allowed = ALLOWED_LEAK_COUNTS.get(rel, 0)
        if leaks > allowed:
            failures.append(
                f"{rel}: {leaks} persona tokens (allowed {allowed}). "
                f"Either remove the leaks or — if intentional — bump "
                f"the page's bound in ALLOWED_LEAK_COUNTS."
            )

    if failures:
        pytest.fail(
            "Persona leaks detected against spec_example fixture:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )


def test_sasquatch_pr_build_renders_persona_flavor() -> None:
    """Guard against over-deletion: the curated SNB strings still render."""
    site = _build_site_for("tests/l2/sasquatch_pr.yaml")

    expected_per_page: dict[str, list[str]] = {
        "handbook/l1/index.html": ["Sasquatch National Bank"],
        "handbook/etl/index.html": ["Sasquatch"],
        "handbook/investigation/index.html": [
            "Juniper Ridge LLC", "Cascadia"
        ],
        "walkthroughs/investigation/who-is-getting-money-from-too-many-"
        "senders/index.html": ["Juniper Ridge LLC"],
        "walkthroughs/investigation/where-did-this-transfer-originate/"
        "index.html": ["Cascadia", "Shell Company"],
    }

    missing: list[str] = []
    for rel, expected in expected_per_page.items():
        page = site / rel
        if not page.is_file():
            missing.append(f"{rel}: file not built")
            continue
        body = page.read_text(errors="replace")
        for token in expected:
            if token not in body:
                missing.append(f"{rel}: missing {token!r}")

    if missing:
        pytest.fail(
            "Sasquatch flavor under-renders against sasquatch_pr "
            "fixture:\n" + "\n".join(f"  - {f}" for f in missing)
        )
